"""Review: an append-only ledger, and the status derived from it.

Nothing here edits a status the way a form edits a field. A curator's decision
is a record; the record's status is a fold over every decision ever made about
it. That is what lets "which items had I approved on the 3rd" give the same
answer a year later, after the threshold has changed and the subject has been
re-proposed twice.

This module is the only place in the package permitted to write
``ReviewStatus.APPROVED``, and it never writes ``ReviewStatus.ACTIVE``.
``SnapshotBuilder.activate()`` owns that, and an invariant test enforces the
split, because two independent gates that can both be reached from one code
path are one gate.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, model_validator

from ..core import ids
from ..core.errors import ContentError
from ..core.types import ProvenanceClass, ReviewStatus
from ..core.versions import CONTENT_SCHEMA_VERSION
from ..graph.models import Provenance
from ..graph.repositories import DocumentRepository, GraphRepositories

#: Credited accepts required before a record derives to APPROVED. A module
#: constant rather than a literal so the rule is readable in one place; the
#: service takes an override so a test need not write ten decisions to reach
#: the case it is actually testing.
ACCEPT_THRESHOLD = 10

#: Statuses a review decision may be recorded against. Everything outside this
#: set is either already live or already withdrawn, and retracting live
#: content is the freshness path's job, not review's.
DECIDABLE_STATUS = frozenset(
    {
        ReviewStatus.CANDIDATE,
        ReviewStatus.AUTO_VALIDATED,
        ReviewStatus.PENDING_REVIEW,
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
    }
)

_SUBJECT_KINDS = frozenset({ids.ENTITY, ids.FACT, ids.RELATIONSHIP, ids.CATEGORY})


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(dt.timezone.utc)


class Clock(Protocol):
    def now(self) -> dt.datetime: ...


class ReviewDecisionKind(StrEnum):
    """What a curator said about one subject on one occasion.

    Two values, deliberately. A "maybe" would have to be stored and then
    ignored by the fold, which is a queue filter dressed up as a decision.
    """

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class ReviewDecision(BaseModel):
    """One curator, one subject, one occasion. Never updated once written."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: str
    subject_ref: str
    reviewer: str
    decision: ReviewDecisionKind
    #: Which proposer run surfaced the subject. Two accepts from one batch are
    #: one reading of one piece of evidence, however many times it was clicked.
    proposal_batch: str
    at: dt.datetime
    note: str | None = None
    schema_version: int = CONTENT_SCHEMA_VERSION

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.REVIEW)
        if ids.kind_of(self.subject_ref) not in _SUBJECT_KINDS:
            raise ValueError(
                "review subject must be an entity, fact, relationship or category"
            )
        if not self.reviewer:
            raise ValueError("a review decision must name its reviewer")
        if not self.proposal_batch:
            raise ValueError("a review decision must name its proposal batch")
        object.__setattr__(self, "at", _utc(self.at))
        return self

    @classmethod
    def build(
        cls,
        *,
        subject_ref: str,
        reviewer: str,
        decision: ReviewDecisionKind,
        proposal_batch: str,
        at: dt.datetime,
        note: str | None = None,
    ) -> "ReviewDecision":
        stamp = f"{_utc(at).isoformat()}|{proposal_batch}|{reviewer}"
        return cls(
            id=ids.for_review(subject_ref, stamp),
            subject_ref=subject_ref,
            reviewer=reviewer,
            decision=decision,
            proposal_batch=proposal_batch,
            at=at,
            note=note,
        )

    @property
    def day(self) -> dt.date:
        return self.at.date()

    def sort_key(self) -> tuple[dt.datetime, str, str, str]:
        # Ties broken by fields the record already carries, so the fold is
        # independent of the order rows come back from any store.
        return (self.at, self.proposal_batch, self.reviewer, self.id)


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """The status a ledger derives to, with the evidence that produced it."""

    subject_ref: str
    status: ReviewStatus
    threshold: int
    credited_accepts: int = 0
    credited_ids: tuple[str, ...] = ()
    credited_batches: tuple[str, ...] = ()
    last_credited_day: dt.date | None = None
    uncredited_accepts: int = 0
    approved_by: str | None = None
    approved_at: dt.datetime | None = None
    rejected_by: str | None = None
    rejected_at: dt.datetime | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.threshold - self.credited_accepts)


def derive_status(
    decisions: Iterable[ReviewDecision],
    *,
    threshold: int = ACCEPT_THRESHOLD,
) -> ReviewOutcome:
    """Fold a ledger into a status.

    Pure, and sorted internally, so the answer does not depend on write order
    or on which backend returned the rows.

    Three rules, all from the design record. An accept is credited only when it
    is both a new proposal batch and a strictly later UTC day than the last
    credited accept: a single curator cannot make ten accepts mean ten people,
    so what has to be shown instead is that the item survived re-proposal
    against regenerated evidence and was read on separate occasions. One reject
    is terminal without a threshold, because a hasty no costs a re-proposal
    while a hasty yes reaches a published puzzle. And a reject clears the
    credited count rather than ending the subject: re-proposal after rejection
    starts the count again, from a batch the rejecting curator has not already
    seen and a day after they saw it.
    """
    if threshold < 1:
        raise ValueError("threshold must be at least one accept")

    ordered = sorted(decisions, key=lambda d: d.sort_key())
    subject_refs = {d.subject_ref for d in ordered}
    if len(subject_refs) > 1:
        raise ValueError(f"decisions span several subjects: {sorted(subject_refs)}")
    subject_ref = next(iter(subject_refs), "")

    credited: list[ReviewDecision] = []
    batches: set[str] = set()
    last_day: dt.date | None = None
    uncredited = 0
    rejected_by: str | None = None
    rejected_at: dt.datetime | None = None
    rejected_batch: str | None = None
    rejected_day: dt.date | None = None

    for decision in ordered:
        if decision.decision is ReviewDecisionKind.REJECT:
            credited.clear()
            batches.clear()
            last_day = None
            uncredited = 0
            rejected_by = decision.reviewer
            rejected_at = decision.at
            rejected_batch = decision.proposal_batch
            rejected_day = decision.day
            continue

        if rejected_batch is not None and not credited:
            # Reopening after a rejection needs evidence the rejecting curator
            # has not already seen, on a later day than they saw it. Without
            # both, a reject and an accept in the same sitting would cancel.
            reopened = (
                decision.proposal_batch != rejected_batch
                and rejected_day is not None
                and decision.day > rejected_day
            )
            if not reopened:
                uncredited += 1
                continue

        if decision.proposal_batch in batches:
            uncredited += 1
            continue
        if last_day is not None and decision.day <= last_day:
            uncredited += 1
            continue

        credited.append(decision)
        batches.add(decision.proposal_batch)
        last_day = decision.day

    if rejected_by is not None and not credited:
        status = ReviewStatus.REJECTED
    elif len(credited) >= threshold:
        status = ReviewStatus.APPROVED
    else:
        status = ReviewStatus.PENDING_REVIEW

    crossing = credited[threshold - 1] if len(credited) >= threshold else None
    return ReviewOutcome(
        subject_ref=subject_ref,
        status=status,
        threshold=threshold,
        credited_accepts=len(credited),
        credited_ids=tuple(d.id for d in credited),
        credited_batches=tuple(d.proposal_batch for d in credited),
        last_credited_day=last_day,
        uncredited_accepts=uncredited,
        approved_by=crossing.reviewer if crossing else None,
        approved_at=crossing.at if crossing else None,
        rejected_by=rejected_by if status is ReviewStatus.REJECTED else None,
        rejected_at=rejected_at if status is ReviewStatus.REJECTED else None,
    )


class ReviewRepository(DocumentRepository[ReviewDecision]):
    """The decision ledger. Appends only; there is no update method.

    Bound to the store through ``GraphRepositories.attach`` rather than being
    a graph repository, because the ledger is content-layer machinery and the
    graph layer must not learn about it.
    """

    collection = "reviews"
    model = ReviewDecision
    indexed_fields = ("subject_ref", "reviewer", "decision", "proposal_batch")

    def _index_values(self, record: ReviewDecision) -> dict[str, list[str]]:
        return {
            "subject_ref": [record.subject_ref],
            "reviewer": [record.reviewer],
            "decision": [str(record.decision)],
            "proposal_batch": [record.proposal_batch],
        }

    def append(self, decision: ReviewDecision) -> None:
        """Write a decision, refusing an exact duplicate.

        ``insert`` rather than ``put``: the same reviewer, batch and instant
        twice is a replayed write, and silently overwriting it would make the
        ledger's count depend on how many times a command was run.
        """
        self.insert(decision)

    def for_subject(self, subject_ref: str) -> list[ReviewDecision]:
        return sorted(self.find(subject_ref=subject_ref), key=lambda d: d.sort_key())

    def for_batch(self, proposal_batch: str) -> list[ReviewDecision]:
        return sorted(
            self.find(proposal_batch=proposal_batch), key=lambda d: d.sort_key()
        )

    def by_reviewer(self, reviewer: str) -> list[ReviewDecision]:
        return sorted(self.find(reviewer=reviewer), key=lambda d: d.sort_key())

    def subjects(self) -> list[str]:
        return sorted({d.subject_ref for d in self.iter_all()})

    def outcomes(
        self, *, threshold: int = ACCEPT_THRESHOLD
    ) -> dict[str, ReviewOutcome]:
        grouped: dict[str, list[ReviewDecision]] = {}
        for decision in self.iter_all():
            grouped.setdefault(decision.subject_ref, []).append(decision)
        return {
            ref: derive_status(rows, threshold=threshold)
            for ref, rows in sorted(grouped.items())
        }

    def open_items(
        self,
        limit: int | None = None,
        *,
        threshold: int = ACCEPT_THRESHOLD,
    ) -> list[str]:
        """Subjects whose ledger has not resolved, nearest to the threshold
        first, so a curator's queue is ordered by what a further accept would
        actually finish."""
        pending = [
            (outcome.remaining, ref)
            for ref, outcome in self.outcomes(threshold=threshold).items()
            if outcome.status is ReviewStatus.PENDING_REVIEW
        ]
        pending.sort()
        refs = [ref for _, ref in pending]
        return refs[:limit] if limit is not None else refs


class ReviewService:
    """Records decisions and writes the status they derive to.

    The one writer of ``APPROVED``. It deliberately cannot write ``ACTIVE``:
    passing review makes a record eligible for the activation gate, not usable
    by a puzzle, and ``USABLE_STATUS`` remains ``{ACTIVE}`` alone.
    """

    def __init__(
        self,
        repos: GraphRepositories,
        reviews: ReviewRepository,
        *,
        threshold: int = ACCEPT_THRESHOLD,
        clock: Clock | None = None,
    ) -> None:
        self._repos = repos
        self._reviews = reviews
        self._threshold = threshold
        self._clock = clock

    @property
    def threshold(self) -> int:
        return self._threshold

    # -- writing ----------------------------------------------------------

    def record(
        self,
        subject_ref: str,
        *,
        reviewer: str,
        decision: ReviewDecisionKind,
        proposal_batch: str,
        at: dt.datetime | None = None,
        note: str | None = None,
    ) -> ReviewOutcome:
        """Append one decision and apply the status it derives to."""
        moment = _utc(at) if at is not None else self._now()
        repo = self._repo_for(subject_ref)
        record = repo.get(subject_ref)
        if record is None:
            raise ContentError(f"no such review subject: {subject_ref}")
        if record.status not in DECIDABLE_STATUS:
            raise ContentError(
                f"{subject_ref} is {record.status} and is not open to review"
            )

        entry = ReviewDecision.build(
            subject_ref=subject_ref,
            reviewer=reviewer,
            decision=decision,
            proposal_batch=proposal_batch,
            at=moment,
            note=note,
        )
        with self._repos.transaction():
            self._reviews.append(entry)
            outcome = derive_status(
                self._reviews.for_subject(subject_ref), threshold=self._threshold
            )
            self._apply(outcome, moment)
        return outcome

    def accept(
        self,
        subject_ref: str,
        *,
        reviewer: str,
        proposal_batch: str,
        at: dt.datetime | None = None,
        note: str | None = None,
    ) -> ReviewOutcome:
        return self.record(
            subject_ref,
            reviewer=reviewer,
            decision=ReviewDecisionKind.ACCEPT,
            proposal_batch=proposal_batch,
            at=at,
            note=note,
        )

    def reject(
        self,
        subject_ref: str,
        *,
        reviewer: str,
        proposal_batch: str,
        at: dt.datetime | None = None,
        note: str | None = None,
    ) -> ReviewOutcome:
        return self.record(
            subject_ref,
            reviewer=reviewer,
            decision=ReviewDecisionKind.REJECT,
            proposal_batch=proposal_batch,
            at=at,
            note=note,
        )

    # -- reading ----------------------------------------------------------

    def ledger(self, subject_ref: str) -> list[ReviewDecision]:
        return self._reviews.for_subject(subject_ref)

    def outcome_for(self, subject_ref: str) -> ReviewOutcome:
        return derive_status(
            self._reviews.for_subject(subject_ref), threshold=self._threshold
        )

    def queue(self, limit: int | None = None) -> list[str]:
        """Everything waiting on a curator, nearest the threshold first.

        Read from the graph rather than from the ledger. A freshly proposed
        record has no decisions at all, so a ledger-only queue would show a
        curator only the items they had already started on, which is the exact
        set they do not need reminding about.
        """
        pending: list[tuple[int, str]] = []
        for repo in (
            self._repos.entities,
            self._repos.facts,
            self._repos.relationships,
            self._repos.categories,
        ):
            for record in repo.by_status(ReviewStatus.PENDING_REVIEW):
                outcome = self.outcome_for(record.id)
                pending.append((outcome.remaining, record.id))
        pending.sort()
        refs = [ref for _, ref in pending]
        return refs[:limit] if limit is not None else refs

    # -- internals --------------------------------------------------------

    def _now(self) -> dt.datetime:
        if self._clock is None:
            raise ContentError(
                "no clock was injected, so a decision must carry its own time"
            )
        return _utc(self._clock.now())

    def _repo_for(self, subject_ref: str):
        kind = ids.kind_of(subject_ref)
        try:
            return {
                ids.ENTITY: self._repos.entities,
                ids.FACT: self._repos.facts,
                ids.RELATIONSHIP: self._repos.relationships,
                ids.CATEGORY: self._repos.categories,
            }[kind]
        except KeyError:
            raise ContentError(f"{subject_ref} is not a reviewable record") from None

    def _apply(self, outcome: ReviewOutcome, moment: dt.datetime) -> None:
        if outcome.status is ReviewStatus.ACTIVE:
            # Unreachable from derive_status, and a loud failure if that ever
            # changes: activation is the other gate and stays the other gate.
            raise ContentError("review may not activate a record")

        repo = self._repo_for(outcome.subject_ref)
        record = repo.require(outcome.subject_ref)
        if record.status is outcome.status:
            return

        updates: dict[str, Any] = {"status": outcome.status, "updated_at": moment}
        if outcome.status is ReviewStatus.APPROVED:
            updates["provenance"] = _stamped(
                record.provenance,
                reviewer=outcome.approved_by or "",
                at=outcome.approved_at or moment,
            )
        repo.put(record.model_copy(update=updates))


def _stamped(
    provenance: Sequence[Provenance],
    *,
    reviewer: str,
    at: dt.datetime,
) -> tuple[Provenance, ...]:
    """Name the crossing accept's reviewer on every JUDGED provenance entry.

    A proposer has to write some reviewer to construct JUDGED provenance at
    all, so without this the name on an approved record would be the tool's
    placeholder. Replacing it with the reviewer whose accept crossed the
    threshold is what makes the model's "an ACTIVE JUDGED record names a
    reviewer" rule mean a person rather than a constant.
    """
    stamped: list[Provenance] = []
    for entry in provenance:
        if entry.provenance_class is ProvenanceClass.JUDGED:
            stamped.append(
                entry.model_copy(update={"reviewer": reviewer, "verification_date": at})
            )
        else:
            stamped.append(entry)
    return tuple(stamped)


def credited_accepts(
    decisions: Iterable[ReviewDecision],
    *,
    threshold: int = ACCEPT_THRESHOLD,
) -> int:
    """Convenience for tooling that wants the count without the status."""
    return derive_status(decisions, threshold=threshold).credited_accepts


__all__ = [
    "ACCEPT_THRESHOLD",
    "DECIDABLE_STATUS",
    "Clock",
    "ReviewDecision",
    "ReviewDecisionKind",
    "ReviewOutcome",
    "ReviewRepository",
    "ReviewService",
    "credited_accepts",
    "derive_status",
]
