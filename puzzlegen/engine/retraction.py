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

The fourth thing a withdrawal owes is to the player rather than to the record,
and it is :class:`RestorationService`. A player whose run ended on a board
built from a fact the platform has since admitted was wrong lost that run to
our error, and marking the day ``INVALIDATED`` does nothing about it. So a
withdrawn day is *excused*: it bridges a gap in a streak rather than breaking
it, and a withdrawn day the player completed still advances one. Excusing can
therefore only lengthen a run and never shorten it, which is the property that
makes the restoration safe to run unattended -- the alternative, a correction
that takes a day back from somebody because we got the content wrong, is worse
than no correction.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..graph.repositories import DependencyRepository
from .commitment import ProofStep, membership_proof
from .records import PuzzleStatus
from .scoring import ScoringService
from .session_storage import SessionRepositories
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


@dataclass(frozen=True, slots=True)
class RestoredStreak:
    """One player's streak, before and after a withdrawn day was excused.

    Both figures rather than the gain alone, because the useful question
    after a restoration is what a player is now told their run is, and a
    caller holding only the difference has to read the record again to say.
    """

    player_id: str
    game_id: str
    before: int
    after: int
    longest_before: int
    longest_after: int

    @property
    def gained(self) -> int:
        return self.after - self.before

    @property
    def changed(self) -> bool:
        return self.after != self.before or self.longest_after != self.longest_before

    def describe(self) -> str:
        return (
            f"{self.player_id} {self.game_id}: {self.before} -> {self.after} "
            f"(longest {self.longest_before} -> {self.longest_after})"
        )


class RestorationService:
    """Gives back what a withdrawn day cost the people who played it.

    Separate from :class:`RetractionService` because the two have different
    authorities and different failure modes. A retraction is one transaction
    over published records and must be all or nothing. A restoration walks
    players, is idempotent per player, and must be re-runnable: a player who
    comes back and plays after the retraction has a streak that needs
    recomputing again, and a second run over the same retraction has to reach
    the same answer rather than compound the first.
    """

    def __init__(
        self,
        engine: EngineRepositories,
        sessions: SessionRepositories,
        scoring: ScoringService,
    ) -> None:
        self._engine = engine
        self._sessions = sessions
        self._scoring = scoring

    def excused_days(self, game_id: str) -> frozenset[str]:
        """Day keys of this game that a retraction has withdrawn.

        Read from the puzzles' own status rather than from a retraction
        document. A player's streak has to account for *every* day withdrawn
        so far, not only the ones this retraction named, and the status index
        is the one place that holds all of them.
        """
        return frozenset(
            puzzle.day_key for puzzle in self._engine.puzzles.invalidated(game_id)
        )

    def affected(self, retraction: "Retraction") -> dict[str, set[str]]:
        """Player ids by game id, for the days this retraction withdrew.

        Only players with a live session on one of those boards. A player who
        never saw the day was not cost anything by it, and rebuilding every
        streak in the store to find that out would make the cost of a
        retraction the size of the player base.
        """
        players: dict[str, set[str]] = {}
        for day in retraction.days:
            for player_id in self._sessions.sessions.players_on_day(
                day.day_key, day.game_id
            ):
                players.setdefault(day.game_id, set()).add(player_id)
        return players

    def restore(self, retraction: "Retraction") -> tuple[RestoredStreak, ...]:
        """Rebuild the streak of everyone who played a withdrawn board.

        Returns only the players whose figures moved. A player whose run was
        not in fact broken by the bad day has nothing restored, and reporting
        them as restored would overstate what the retraction did -- which, on
        a feature whose whole claim is that the correction is verifiable, is
        the one thing it must not do.
        """
        restored: list[RestoredStreak] = []
        for game_id, player_ids in self.affected(retraction).items():
            excused = self.excused_days(game_id)
            for player_id in sorted(player_ids):
                before = self._scoring.streak_for(player_id, game_id)
                after = self._scoring.rebuild_streak(
                    player_id, game_id, excused=excused
                )
                outcome = RestoredStreak(
                    player_id=player_id,
                    game_id=game_id,
                    before=before.current,
                    after=after.current,
                    longest_before=before.longest,
                    longest_after=after.longest,
                )
                if outcome.changed:
                    restored.append(outcome)
        return tuple(restored)


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
    "RestorationService",
    "RestoredStreak",
    "RetractedDay",
    "Retraction",
    "RetractionService",
    "retract_many",
]
