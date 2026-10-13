"""Withdrawing a fact, and saying so verifiably.

``DependencyRepository.blast_radius`` already answers "which published days
rested on this record". What was missing was the action and the evidence: no
code path ever set :attr:`PuzzleStatus.INVALIDATED`, and a player told their
board had been withdrawn had no way to check that the withdrawal applied to
the board they actually played.

A retraction here does three things, and the third is the one that needed the
dependency commitment to exist.

1. Marks every affected puzzle ``INVALIDATED``. The record stays readable; a
   withdrawn day is never deleted or quietly regenerated.
2. Releases the salt of every affected day. A retraction a player cannot
   verify is an announcement, and verification needs the salt. This is why a
   retraction is not reversible in its effect on secrecy: releasing a salt
   publishes that day's dependency set, which is acceptable only because the
   day in question is being withdrawn anyway.
3. Emits a document carrying, per day, the root the board published, the
   released salt, and a membership proof for the retracted ref. A player holds
   the root from the public view of the manifest they played; the proof plus
   the salt carries the named ref to that root, and nothing else does.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..graph.repositories import DependencyRepository
from .commitment import ProofStep, membership_proof
from .records import PuzzleStatus
from .storage import EngineRepositories


@dataclass(frozen=True, slots=True)
class RetractedDay:
    """One published day a retraction applies to, with its evidence."""

    manifest_id: str
    puzzle_id: str
    game_id: str
    day_key: str
    dependency_root: str
    salt: str
    proof: tuple[ProofStep, ...]

    def as_document(self) -> dict[str, object]:
        """The publishable form. Proof steps flattened for a wire format."""
        return {
            "manifest_id": self.manifest_id,
            "game_id": self.game_id,
            "day_key": self.day_key,
            "dependency_root": self.dependency_root,
            "salt": self.salt,
            "proof": [
                {"side": step.side, "hash": step.hash} for step in self.proof
            ],
        }


@dataclass(frozen=True, slots=True)
class Retraction:
    """Everything one withdrawal did, and the evidence for it."""

    ref_id: str
    reason: str
    retracted_at: dt.datetime
    days: tuple[RetractedDay, ...] = ()
    #: Days the retraction touched but could not evidence, with the reason.
    #: A day whose salt or manifest is missing is reported rather than
    #: dropped: a retraction that silently covered fewer days than it claimed
    #: would be worse than one that admits a gap.
    unevidenced: tuple[tuple[str, str], ...] = ()
    blast_radius: dict[str, object] = field(default_factory=dict)

    @property
    def manifest_count(self) -> int:
        return len(self.days)

    def as_document(self) -> dict[str, object]:
        return {
            "record": "retraction",
            "ref_id": self.ref_id,
            "reason": self.reason,
            "retracted_at": self.retracted_at.isoformat(),
            "days": [day.as_document() for day in self.days],
            "unevidenced": [
                {"manifest_id": mid, "reason": why}
                for mid, why in self.unevidenced
            ],
            "blast_radius": dict(self.blast_radius),
        }


class RetractionService:
    """Withdraws a graph record from every published day that used it."""

    def __init__(
        self,
        engine: EngineRepositories,
        dependencies: DependencyRepository,
    ) -> None:
        self._engine = engine
        self._dependencies = dependencies

    def preview(self, ref_id: str) -> dict[str, object]:
        """What a retraction would touch, without touching it."""
        return self._dependencies.blast_radius(ref_id)

    def retract(
        self, ref_id: str, *, reason: str, at: dt.datetime
    ) -> Retraction:
        """Withdraw ``ref_id`` and return the verifiable retraction document.

        The whole withdrawal is one transaction. A partially applied
        retraction would leave some days marked and others not, with one
        document claiming both.
        """
        if not reason:
            raise ValueError(
                "a retraction must record why; an unexplained invalidation is "
                "a day players cannot be told anything about"
            )
        edges = self._dependencies.dependents_of(ref_id)
        manifest_ids = sorted({edge.manifest_id for edge in edges})
        days: list[RetractedDay] = []
        unevidenced: list[tuple[str, str]] = []

        with self._engine.transaction():
            for manifest_id in manifest_ids:
                manifest = self._engine.manifests.get(manifest_id)
                if manifest is None:
                    unevidenced.append((manifest_id, "manifest not stored"))
                    continue
                self._engine.puzzles.set_status(
                    manifest.puzzle_id, PuzzleStatus.INVALIDATED
                )
                salt = self._engine.dependency_salts.for_manifest(manifest_id)
                if salt is None:
                    unevidenced.append(
                        (manifest_id, "no commitment salt was stored")
                    )
                    continue
                released = self._engine.dependency_salts.release(manifest_id, at)
                days.append(
                    RetractedDay(
                        manifest_id=manifest.id,
                        puzzle_id=manifest.puzzle_id,
                        game_id=manifest.game_id,
                        day_key=manifest.day_key,
                        dependency_root=manifest.dependency_root,
                        salt=released.salt,
                        proof=membership_proof(
                            manifest.fact_refs, released.salt, ref_id
                        ),
                    )
                )

        return Retraction(
            ref_id=ref_id,
            reason=reason,
            retracted_at=at,
            days=tuple(days),
            unevidenced=tuple(unevidenced),
            blast_radius=self._dependencies.blast_radius(ref_id),
        )

    def verify(self, retraction: Retraction) -> tuple[str, ...]:
        """Day keys in ``retraction`` whose evidence does not check out.

        The same arithmetic a player would do, run against the service's own
        output. An empty result is what makes the document worth publishing.
        """
        from .commitment import verify_membership

        return tuple(
            day.day_key
            for day in retraction.days
            if not verify_membership(
                day.dependency_root, day.salt, retraction.ref_id, day.proof
            )
        )


def retract_many(
    service: RetractionService,
    ref_ids: Sequence[str],
    *,
    reason: str,
    at: dt.datetime,
) -> tuple[Retraction, ...]:
    """Withdraw several records, one document each.

    One document per ref rather than one merged document, because a player
    verifying a retraction checks one ref against one root, and a merged
    document would make them guess which proof belongs to which withdrawal.
    """
    return tuple(
        service.retract(ref_id, reason=reason, at=at) for ref_id in ref_ids
    )


__all__ = [
    "RetractedDay",
    "Retraction",
    "RetractionService",
    "retract_many",
]
