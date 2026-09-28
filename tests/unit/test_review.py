"""Review ledger, derived status, and the activation gate that admits it.

Every storage-dependent case runs against both backends through the shared
``repos`` fixture, because the derivation reads rows back out of an index and
an index that behaved differently per backend would give two answers to "is
this approved".
"""

from __future__ import annotations

import datetime as dt
import random

import pytest
from conftest import (
    LATER,
    NOW,
    make_category,
    make_entity,
    sourced_provenance,
)

from puzzlegen.core import ids
from puzzlegen.core.errors import ConflictError, ContentError
from puzzlegen.core.types import (
    FreshnessClass,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
)
from puzzlegen.content.review import (
    ACCEPT_THRESHOLD,
    ReviewDecision,
    ReviewDecisionKind,
    ReviewRepository,
    ReviewService,
    credited_accepts,
    derive_status,
)
from puzzlegen.content.snapshots import ActivationPolicy, SnapshotBuilder
from puzzlegen.graph.models import Provenance, Relationship, Source

BATCH = "batch-2026-09-01"
CURATOR = "curator@example"
SUBJECT = ids.for_relationship(
    ids.for_entity("salmon", "en"), "is_a", ids.for_category("colour", "overlay")
)


class FixedClock:
    def __init__(self, instant: dt.datetime = NOW) -> None:
        self.instant = instant

    def now(self) -> dt.datetime:
        return self.instant


def judged_provenance(source: Source, confidence: float = 0.85) -> Provenance:
    """What a proposer writes: JUDGED, with a placeholder reviewer.

    ``Provenance`` refuses to construct JUDGED without a reviewer, so the
    proposer must name something. The placeholder is the reason the service
    restamps the field when the threshold is crossed.
    """
    return Provenance(
        source_id=source.id,
        provenance_class=ProvenanceClass.JUDGED,
        source_ref="propose_overlay/salmon",
        retrieval_date=NOW,
        confidence=confidence,
        reviewer="tools/propose_overlay.py",
    )


def accept(
    subject_ref: str = SUBJECT,
    *,
    batch: str = BATCH,
    day: int = 1,
    reviewer: str = CURATOR,
) -> ReviewDecision:
    return ReviewDecision.build(
        subject_ref=subject_ref,
        reviewer=reviewer,
        decision=ReviewDecisionKind.ACCEPT,
        proposal_batch=batch,
        at=NOW + dt.timedelta(days=day),
    )


def reject(
    subject_ref: str = SUBJECT,
    *,
    batch: str = BATCH,
    day: int = 1,
    reviewer: str = CURATOR,
) -> ReviewDecision:
    return ReviewDecision.build(
        subject_ref=subject_ref,
        reviewer=reviewer,
        decision=ReviewDecisionKind.REJECT,
        proposal_batch=batch,
        at=NOW + dt.timedelta(days=day),
    )


def ten_credited(subject_ref: str = SUBJECT) -> list[ReviewDecision]:
    return [
        accept(subject_ref, batch=f"batch-{n:02d}", day=n)
        for n in range(1, ACCEPT_THRESHOLD + 1)
    ]


@pytest.fixture
def overlay_source() -> Source:
    return Source(
        id=ids.for_source("overlay-proposer", "2026.09"),
        name="overlay-proposer",
        kind=SourceKind.COMPUTED,
        version="2026.09",
        retrieved_at=NOW,
    )


@pytest.fixture
def reviews(repos) -> ReviewRepository:
    return repos.attach(ReviewRepository)


@pytest.fixture
def seeded(repos, overlay_source, curated_source):
    """One pending JUDGED overlay membership over two active endpoints."""
    repos.sources.put(overlay_source)
    repos.sources.put(curated_source)
    salmon = make_entity("salmon", curated_source)
    colour = make_category("colour", curated_source)
    repos.entities.put(salmon)
    repos.categories.put(colour)
    membership = Relationship.build(
        subject_id=salmon.id,
        predicate="is_a",
        object_id=colour.id,
        created_at=NOW,
        status=ReviewStatus.PENDING_REVIEW,
        freshness_class=FreshnessClass.STATIC,
        confidence=0.85,
        provenance=(judged_provenance(overlay_source),),
    )
    repos.relationships.put(membership)
    return membership


@pytest.fixture
def service(repos, reviews) -> ReviewService:
    return ReviewService(repos, reviews, clock=FixedClock())


class TestTheDecisionRecord:
    def test_an_id_is_derived_from_subject_time_batch_and_reviewer(self):
        one = accept()
        again = accept()
        assert one.id == again.id
        assert one.id != accept(batch="other").id
        assert one.id != accept(reviewer="someone-else").id
        assert one.id != accept(day=2).id

    def test_a_naive_timestamp_is_refused(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            ReviewDecision.build(
                subject_ref=SUBJECT,
                reviewer=CURATOR,
                decision=ReviewDecisionKind.ACCEPT,
                proposal_batch=BATCH,
                at=dt.datetime(2026, 9, 1, 12, 0, 0),
            )

    def test_a_decision_is_frozen(self):
        decision = accept()
        with pytest.raises(Exception):
            decision.reviewer = "someone-else"

    def test_a_subject_must_be_a_graph_record(self):
        with pytest.raises(ValueError, match="review subject"):
            ReviewDecision.build(
                subject_ref=ids.for_puzzle("2026-09-27", "grouping", "hash"),
                reviewer=CURATOR,
                decision=ReviewDecisionKind.ACCEPT,
                proposal_batch=BATCH,
                at=NOW,
            )

    def test_reviewer_and_batch_cannot_be_blank(self):
        for field in ("reviewer", "proposal_batch"):
            kwargs = {
                "subject_ref": SUBJECT,
                "reviewer": CURATOR,
                "decision": ReviewDecisionKind.ACCEPT,
                "proposal_batch": BATCH,
                "at": NOW,
            }
            kwargs[field] = "   "
            with pytest.raises(ValueError):
                ReviewDecision.build(**kwargs)


class TestCreditRules:
    def test_an_empty_ledger_is_pending(self):
        outcome = derive_status([])
        assert outcome.status is ReviewStatus.PENDING_REVIEW
        assert outcome.credited_accepts == 0
        assert outcome.remaining == ACCEPT_THRESHOLD

    def test_repeated_clicks_in_one_sitting_credit_once(self):
        outcome = derive_status(
            [accept(), accept(reviewer="curator-two"), accept(reviewer="curator-three")]
        )
        assert outcome.credited_accepts == 1
        assert outcome.uncredited_accepts == 2

    def test_a_new_batch_on_the_same_day_does_not_credit(self):
        outcome = derive_status([accept(day=1), accept(batch="second", day=1)])
        assert outcome.credited_accepts == 1

    def test_a_later_day_in_the_same_batch_does_not_credit(self):
        outcome = derive_status([accept(day=1), accept(day=2)])
        assert outcome.credited_accepts == 1

    def test_a_new_batch_on_a_later_day_credits(self):
        outcome = derive_status([accept(day=1), accept(batch="second", day=2)])
        assert outcome.credited_accepts == 2
        assert outcome.credited_batches == (BATCH, "second")

    def test_ten_credited_accepts_derive_to_approved(self):
        outcome = derive_status(ten_credited())
        assert outcome.status is ReviewStatus.APPROVED
        assert outcome.credited_accepts == ACCEPT_THRESHOLD
        assert outcome.remaining == 0

    def test_nine_credited_accepts_are_still_pending(self):
        outcome = derive_status(ten_credited()[:-1])
        assert outcome.status is ReviewStatus.PENDING_REVIEW
        assert outcome.remaining == 1

    def test_the_crossing_accept_is_named(self):
        outcome = derive_status(ten_credited())
        assert outcome.approved_by == CURATOR
        assert outcome.approved_at == NOW + dt.timedelta(days=ACCEPT_THRESHOLD)

    def test_the_fold_is_independent_of_order(self):
        decisions = ten_credited()
        shuffled = list(decisions)
        random.Random(7).shuffle(shuffled)
        assert derive_status(shuffled) == derive_status(decisions)

    def test_a_lower_threshold_approves_sooner(self):
        outcome = derive_status(ten_credited()[:3], threshold=3)
        assert outcome.status is ReviewStatus.APPROVED
        assert outcome.threshold == 3

    def test_a_threshold_below_one_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            derive_status([], threshold=0)

    def test_decisions_spanning_two_subjects_are_refused(self):
        other = ids.for_relationship(
            ids.for_entity("kiwi", "en"), "is_a", ids.for_category("colour", "overlay")
        )
        with pytest.raises(ValueError, match="span several subjects"):
            derive_status([accept(), accept(other, batch="second", day=2)])

    def test_credited_accepts_helper_agrees_with_the_fold(self):
        assert credited_accepts(ten_credited()) == ACCEPT_THRESHOLD


class TestRejection:
    def test_one_reject_is_enough(self):
        outcome = derive_status([reject()])
        assert outcome.status is ReviewStatus.REJECTED
        assert outcome.rejected_by == CURATOR

    def test_a_reject_discards_accumulated_accepts(self):
        outcome = derive_status([*ten_credited()[:5], reject(batch="late", day=6)])
        assert outcome.status is ReviewStatus.REJECTED
        assert outcome.credited_accepts == 0

    def test_an_accept_in_the_rejecting_batch_does_not_reopen(self):
        outcome = derive_status(
            [reject(batch="b1", day=1), accept(batch="b1", day=2)]
        )
        assert outcome.status is ReviewStatus.REJECTED

    def test_an_accept_on_the_rejecting_day_does_not_reopen(self):
        outcome = derive_status(
            [reject(batch="b1", day=1), accept(batch="b2", day=1)]
        )
        assert outcome.status is ReviewStatus.REJECTED

    def test_a_later_batch_on_a_later_day_reopens_at_one_credit(self):
        outcome = derive_status(
            [reject(batch="b1", day=1), accept(batch="b2", day=2)]
        )
        assert outcome.status is ReviewStatus.PENDING_REVIEW
        assert outcome.credited_accepts == 1
        assert outcome.rejected_by is None

    def test_a_reopened_subject_can_reach_approval(self):
        decisions = [reject(batch="b0", day=1)]
        decisions += [
            accept(batch=f"re-{n:02d}", day=n + 1)
            for n in range(1, ACCEPT_THRESHOLD + 1)
        ]
        outcome = derive_status(decisions)
        assert outcome.status is ReviewStatus.APPROVED

    def test_a_second_reject_after_reopening_rejects_again(self):
        outcome = derive_status(
            [
                reject(batch="b1", day=1),
                accept(batch="b2", day=2),
                reject(batch="b3", day=3),
            ]
        )
        assert outcome.status is ReviewStatus.REJECTED
        assert outcome.rejected_at == NOW + dt.timedelta(days=3)

    def test_the_rejection_stays_in_the_ledger_after_reopening(self, reviews):
        for decision in (reject(batch="b1", day=1), accept(batch="b2", day=2)):
            reviews.append(decision)
        kinds = [d.decision for d in reviews.for_subject(SUBJECT)]
        assert kinds == [ReviewDecisionKind.REJECT, ReviewDecisionKind.ACCEPT]


class TestTheLedgerIsAppendOnly:
    def test_a_decision_round_trips(self, reviews):
        decision = accept()
        reviews.append(decision)
        assert reviews.get(decision.id) == decision

    def test_an_exactly_duplicated_decision_is_a_conflict(self, reviews):
        reviews.append(accept())
        with pytest.raises(ConflictError):
            reviews.append(accept())

    def test_decisions_are_indexed_for_the_queries_the_service_makes(self, reviews):
        reviews.append(accept())
        reviews.append(accept(batch="second", day=2, reviewer="curator-two"))
        assert len(reviews.for_subject(SUBJECT)) == 2
        assert len(reviews.for_batch("second")) == 1
        assert len(reviews.by_reviewer("curator-two")) == 1
        assert reviews.subjects() == [SUBJECT]

    def test_for_subject_returns_chronological_order(self, reviews):
        for decision in reversed(ten_credited()):
            reviews.append(decision)
        returned = [d.at for d in reviews.for_subject(SUBJECT)]
        assert returned == sorted(returned)


class TestTheQueue:
    def test_unresolved_subjects_are_listed_nearest_the_threshold_first(
        self, reviews
    ):
        near = SUBJECT
        far = ids.for_relationship(
            ids.for_entity("kiwi", "en"), "is_a", ids.for_category("colour", "overlay")
        )
        for decision in ten_credited(near)[:8]:
            reviews.append(decision)
        for decision in ten_credited(far)[:2]:
            reviews.append(decision)
        assert reviews.open_items() == [near, far]

    def test_resolved_subjects_leave_the_queue(self, reviews):
        for decision in ten_credited():
            reviews.append(decision)
        assert reviews.open_items() == []

    def test_a_rejected_subject_leaves_the_queue(self, reviews):
        reviews.append(reject())
        assert reviews.open_items() == []

    def test_the_limit_is_applied_after_ordering(self, reviews):
        near = SUBJECT
        far = ids.for_relationship(
            ids.for_entity("kiwi", "en"), "is_a", ids.for_category("colour", "overlay")
        )
        for decision in ten_credited(near)[:8]:
            reviews.append(decision)
        for decision in ten_credited(far)[:2]:
            reviews.append(decision)
        assert reviews.open_items(1) == [near]


class TestTheService:
    def test_an_accept_is_recorded_and_leaves_the_subject_pending(
        self, service, reviews, repos, seeded
    ):
        outcome = service.accept(seeded.id, reviewer=CURATOR, proposal_batch="b1", at=NOW)
        assert outcome.credited_accepts == 1
        assert len(reviews.for_subject(seeded.id)) == 1
        assert repos.relationships.require(seeded.id).status is (
            ReviewStatus.PENDING_REVIEW
        )

    def test_the_tenth_credited_accept_writes_approved(self, service, repos, seeded):
        outcome = None
        for n in range(1, ACCEPT_THRESHOLD + 1):
            outcome = service.accept(
                seeded.id,
                reviewer=CURATOR,
                proposal_batch=f"batch-{n:02d}",
                at=NOW + dt.timedelta(days=n),
            )
        assert outcome.status is ReviewStatus.APPROVED
        assert repos.relationships.require(seeded.id).status is ReviewStatus.APPROVED

    def test_approval_stamps_the_crossing_reviewer_onto_judged_provenance(
        self, service, repos, seeded
    ):
        for n in range(1, ACCEPT_THRESHOLD + 1):
            reviewer = "final-curator" if n == ACCEPT_THRESHOLD else CURATOR
            service.accept(
                seeded.id,
                reviewer=reviewer,
                proposal_batch=f"batch-{n:02d}",
                at=NOW + dt.timedelta(days=n),
            )
        stored = repos.relationships.require(seeded.id)
        judged = [
            p for p in stored.provenance if p.provenance_class is ProvenanceClass.JUDGED
        ]
        assert judged
        assert all(p.reviewer == "final-curator" for p in judged)
        assert all(p.verification_date is not None for p in judged)

    def test_non_judged_provenance_is_left_alone(
        self, service, repos, overlay_source, curated_source, seeded
    ):
        mixed = seeded.model_copy(
            update={
                "provenance": (
                    judged_provenance(overlay_source),
                    sourced_provenance(curated_source),
                )
            }
        )
        repos.relationships.put(mixed)
        for n in range(1, ACCEPT_THRESHOLD + 1):
            service.accept(
                mixed.id,
                reviewer=CURATOR,
                proposal_batch=f"batch-{n:02d}",
                at=NOW + dt.timedelta(days=n),
            )
        stored = repos.relationships.require(mixed.id)
        sourced = [
            p for p in stored.provenance if p.provenance_class is ProvenanceClass.SOURCED
        ]
        assert all(p.reviewer is None for p in sourced)

    def test_a_reject_writes_rejected(self, service, repos, seeded):
        outcome = service.reject(
            seeded.id, reviewer=CURATOR, proposal_batch="b1", at=NOW
        )
        assert outcome.status is ReviewStatus.REJECTED
        assert repos.relationships.require(seeded.id).status is ReviewStatus.REJECTED

    def test_a_rejected_subject_can_be_reopened_through_the_service(
        self, service, repos, seeded
    ):
        service.reject(seeded.id, reviewer=CURATOR, proposal_batch="b1", at=NOW)
        outcome = service.accept(
            seeded.id,
            reviewer=CURATOR,
            proposal_batch="b2",
            at=NOW + dt.timedelta(days=1),
        )
        assert outcome.status is ReviewStatus.PENDING_REVIEW
        assert repos.relationships.require(seeded.id).status is (
            ReviewStatus.PENDING_REVIEW
        )

    def test_review_never_writes_active(self, service, repos, seeded):
        for n in range(1, ACCEPT_THRESHOLD + 4):
            service.accept(
                seeded.id,
                reviewer=CURATOR,
                proposal_batch=f"batch-{n:02d}",
                at=NOW + dt.timedelta(days=n),
            )
        assert repos.relationships.require(seeded.id).status is not ReviewStatus.ACTIVE

    def test_an_active_subject_is_not_open_to_review(self, service, repos, seeded):
        active = seeded.model_copy(
            update={
                "status": ReviewStatus.ACTIVE,
                "provenance": (
                    seeded.provenance[0].model_copy(update={"reviewer": CURATOR}),
                ),
            }
        )
        repos.relationships.put(active)
        with pytest.raises(ContentError, match="not open to review"):
            service.accept(active.id, reviewer=CURATOR, proposal_batch="b1", at=NOW)

    def test_an_unknown_subject_is_refused_before_anything_is_written(
        self, service, reviews, seeded
    ):
        missing = ids.for_relationship(
            ids.for_entity("nothing", "en"), "is_a", ids.for_category("colour", "overlay")
        )
        with pytest.raises(ContentError, match="no such review subject"):
            service.accept(missing, reviewer=CURATOR, proposal_batch="b1", at=NOW)
        assert reviews.for_subject(missing) == []

    def test_a_non_graph_subject_is_refused(self, service):
        with pytest.raises(ContentError):
            service.accept(
                ids.for_snapshot("snap"), reviewer=CURATOR, proposal_batch="b1", at=NOW
            )

    def test_the_clock_supplies_the_time_when_none_is_given(
        self, repos, reviews, seeded
    ):
        clock = FixedClock(NOW + dt.timedelta(days=3))
        service = ReviewService(repos, reviews, clock=clock)
        service.accept(seeded.id, reviewer=CURATOR, proposal_batch="b1")
        assert reviews.for_subject(seeded.id)[0].at == clock.instant

    def test_without_a_clock_a_decision_must_carry_its_own_time(
        self, repos, reviews, seeded
    ):
        service = ReviewService(repos, reviews)
        with pytest.raises(ContentError, match="no clock"):
            service.accept(seeded.id, reviewer=CURATOR, proposal_batch="b1")

    def test_a_lowered_threshold_is_honoured_end_to_end(self, repos, reviews, seeded):
        service = ReviewService(repos, reviews, threshold=2, clock=FixedClock())
        service.accept(seeded.id, reviewer=CURATOR, proposal_batch="b1", at=NOW)
        outcome = service.accept(
            seeded.id,
            reviewer=CURATOR,
            proposal_batch="b2",
            at=NOW + dt.timedelta(days=1),
        )
        assert outcome.status is ReviewStatus.APPROVED
        assert service.threshold == 2

    def test_the_queue_shows_an_item_nobody_has_touched(self, service, seeded):
        """A freshly proposed record has no ledger at all, and is exactly what
        a curator needs to see."""
        assert service.queue() == [seeded.id]
        assert service.ledger(seeded.id) == []

    def test_the_queue_orders_by_what_is_nearest_the_threshold(
        self, service, repos, seeded, curated_source
    ):
        other = make_entity(
            "quokka", curated_source, status=ReviewStatus.PENDING_REVIEW
        )
        repos.entities.put(other)
        service.accept(seeded.id, reviewer=CURATOR, proposal_batch="b1", at=NOW)
        assert service.queue() == [seeded.id, other.id]
        assert service.queue(1) == [seeded.id]

    def test_an_approved_item_leaves_the_queue(self, service, repos, seeded):
        for n in range(1, ACCEPT_THRESHOLD + 1):
            service.accept(
                seeded.id,
                reviewer=CURATOR,
                proposal_batch=f"batch-{n:02d}",
                at=NOW + dt.timedelta(days=n),
            )
        assert service.queue() == []

    def test_a_rejected_item_leaves_the_queue(self, service, seeded):
        service.reject(seeded.id, reviewer=CURATOR, proposal_batch="b1", at=NOW)
        assert service.queue() == []

    def test_the_queue_reflects_what_the_service_recorded(self, service, seeded):
        service.accept(seeded.id, reviewer=CURATOR, proposal_batch="b1", at=NOW)
        assert service.queue() == [seeded.id]
        assert service.outcome_for(seeded.id).credited_accepts == 1
        assert len(service.ledger(seeded.id)) == 1

    def test_entities_and_categories_are_reviewable_too(
        self, service, repos, curated_source
    ):
        pending = make_entity(
            "quokka", curated_source, status=ReviewStatus.PENDING_REVIEW
        )
        repos.entities.put(pending)
        outcome = service.reject(
            pending.id, reviewer=CURATOR, proposal_batch="b1", at=NOW
        )
        assert outcome.status is ReviewStatus.REJECTED
        assert repos.entities.require(pending.id).status is ReviewStatus.REJECTED


class TestActivationAdmitsApproved:
    def approve(self, service, subject_ref: str) -> None:
        for n in range(1, ACCEPT_THRESHOLD + 1):
            service.accept(
                subject_ref,
                reviewer=CURATOR,
                proposal_batch=f"batch-{n:02d}",
                at=NOW + dt.timedelta(days=n),
            )

    def test_an_approved_judged_record_activates(self, service, repos, seeded):
        self.approve(service, seeded.id)
        SnapshotBuilder(repos, now=LATER).activate(ActivationPolicy())
        assert repos.relationships.require(seeded.id).status is ReviewStatus.ACTIVE

    def test_an_unapproved_judged_record_is_still_withheld(self, repos, seeded):
        activated = SnapshotBuilder(repos, now=LATER).activate(ActivationPolicy())
        assert activated["relationships"] == 0
        assert repos.relationships.require(seeded.id).status is (
            ReviewStatus.PENDING_REVIEW
        )

    def test_admit_approved_can_be_switched_off(self, service, repos, seeded):
        self.approve(service, seeded.id)
        SnapshotBuilder(repos, now=LATER).activate(
            ActivationPolicy(admit_approved=False)
        )
        assert repos.relationships.require(seeded.id).status is ReviewStatus.APPROVED

    def test_approval_does_not_bypass_the_confidence_floor(
        self, service, repos, seeded
    ):
        self.approve(service, seeded.id)
        SnapshotBuilder(repos, now=LATER).activate(
            ActivationPolicy(minimum_confidence=0.99)
        )
        assert repos.relationships.require(seeded.id).status is ReviewStatus.APPROVED

    def test_approval_does_not_bypass_a_manual_review_source(
        self, service, repos, seeded, overlay_source
    ):
        self.approve(service, seeded.id)
        SnapshotBuilder(repos, now=LATER).activate(
            ActivationPolicy(manual_review_sources=frozenset({overlay_source.id}))
        )
        assert repos.relationships.require(seeded.id).status is ReviewStatus.APPROVED

    def test_the_two_gates_are_independent(self, service, repos, seeded):
        """Approval alone never makes content usable."""
        self.approve(service, seeded.id)
        assert repos.relationships.require(seeded.id).status is ReviewStatus.APPROVED
        assert repos.relationships.active() == []


class TestAttachedRepositories:
    def test_attach_binds_a_higher_layer_repository_to_the_same_store(
        self, repos, reviews
    ):
        reviews.append(accept())
        assert repos.attach(ReviewRepository).get(accept().id) is not None

    def test_the_store_is_not_exposed_by_attaching(self, repos):
        assert not hasattr(repos, "store")
