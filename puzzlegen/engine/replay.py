"""Grading a recorded transcript under the rules it was recorded under.

A session carries a :class:`~puzzlegen.engine.sessions.RulesVersion` stamped
when play started. This module is the only entry point that grades one, and it
refuses rather than best-efforts: a transcript whose rules this release cannot
honour is reported by name, not graded under today's semantics.

Refusing is the whole design. A replay engine that silently regrades produces
a number that looks right, carries no mark of the substitution, and will be
compared against numbers produced under the other rules. Every later use --
replay sharing, a difficulty refit, a historical archive -- then compares two
changes at once without knowing it.
"""

from __future__ import annotations

from ..core.errors import PuzzleGenError
from ..core.versions import (
    ENGINE_VERSION,
    SCORING_VERSION,
    SESSION_SCHEMA_VERSION,
)
from .plugin import Puzzle
from .records import PuzzleManifest, content_hash_of
from .registry import GameRegistry
from .scoring import ScoringService
from .sessions import RulesVersion, ScoreRecord, SessionRecord


class RulesVersionError(PuzzleGenError):
    """A transcript's recorded rules are not the rules this release grades by.

    Its own error class rather than a :class:`ValueError`, because a caller
    walking an archive has to be able to tell "this transcript belongs to an
    older ruleset" apart from "this transcript is malformed". The first is an
    expected outcome of replaying history and the second is a bug.
    """


class ReplayBindingError(PuzzleGenError):
    """The board or manifest offered is not the one this session played.

    Separate from :class:`RulesVersionError` because the two are different
    failures with different remedies: moved rules mean this release cannot
    grade the transcript at all, while a mismatched board means the caller
    fetched the wrong one and a correct replay is still available.
    """


class ReplayService:
    """Grades stored transcripts, and only under their own rules."""

    def __init__(
        self,
        scoring: ScoringService,
        registry: GameRegistry,
        *,
        engine_version: str = ENGINE_VERSION,
        scoring_version: str = SCORING_VERSION,
        session_schema_version: int = SESSION_SCHEMA_VERSION,
    ) -> None:
        self._scoring = scoring
        self._registry = registry
        self._engine_version = engine_version
        self._scoring_version = scoring_version
        self._session_schema_version = session_schema_version

    def current_rules(self, game_id: str) -> RulesVersion:
        """The rules this release would stamp on a session of ``game_id``."""
        descriptor = self._registry.get(game_id).descriptor
        return RulesVersion(
            engine=self._engine_version,
            game=descriptor.game_version,
            scoring=self._scoring_version,
            session_schema=self._session_schema_version,
        )

    def can_honour(self, session: SessionRecord) -> bool:
        """Whether this release grades by exactly the recorded rules.

        A game that is not registered at all cannot honour anything, and says
        so as ``False`` rather than by raising: an archive sweep asking which
        transcripts are replayable should not stop at the first withdrawn
        game.
        """
        try:
            current = self.current_rules(session.game_id)
        except Exception:
            return False
        return session.rules.is_honoured_by(current)

    def grade(
        self,
        session: SessionRecord,
        puzzle: Puzzle,
        manifest: PuzzleManifest,
    ) -> ScoreRecord:
        """Grade a transcript, or refuse because its rules have moved.

        The plugin comes from the registry rather than the caller, and the
        board and its manifest are checked against the session before
        anything is evaluated. Pinning the rules and then grading against
        whatever board was passed in would leave the easier substitution
        open: the difficulty score comes off the manifest and the telemetry
        comes off the puzzle, so another day's manifest buys a different
        score under a rules check that still passes.
        """
        self.require_honoured(session)
        self.require_bound(session, puzzle, manifest)
        plugin = self._registry.get(session.game_id).plugin
        return self._scoring.evaluate(
            session,
            plugin,
            puzzle,
            difficulty_score=manifest.difficulty.score,
            # The original scoring time, so a replay of an unchanged
            # transcript reproduces the stored record exactly rather than
            # differing in a timestamp nobody changed.
            scored_at=session.ended_at,
        )

    def require_bound(
        self,
        session: SessionRecord,
        puzzle: Puzzle,
        manifest: PuzzleManifest,
    ) -> None:
        """Raise unless this board and manifest are the ones this session played.

        Four checks rather than one, because they fail for different reasons
        and a caller needs to know which. The manifest's id is the binding a
        session actually stores; the game and day guard a manifest whose id
        was somehow minted from the right puzzle but describes another day;
        and the content hash is the only thing that ties the board in hand to
        the one published, since a puzzle carries no id of its own here.
        """
        if manifest.id != session.manifest_id:
            raise ReplayBindingError(
                f"session {session.id} was played on manifest "
                f"{session.manifest_id}, not {manifest.id}"
            )
        if manifest.puzzle_id != session.puzzle_id:
            raise ReplayBindingError(
                f"manifest {manifest.id} publishes puzzle "
                f"{manifest.puzzle_id}, but session {session.id} played "
                f"{session.puzzle_id}"
            )
        if (manifest.game_id, manifest.day_key) != (
            session.game_id,
            session.day_key,
        ):
            raise ReplayBindingError(
                f"manifest {manifest.id} is {manifest.game_id} on "
                f"{manifest.day_key}, but session {session.id} played "
                f"{session.game_id} on {session.day_key}"
            )
        content_hash = content_hash_of(
            puzzle.game_id,
            puzzle.payload,
            puzzle.solution,
            puzzle.fact_refs,
            tuple(part.as_document() for part in puzzle.parts),
        )
        if content_hash != manifest.puzzle_content_hash:
            raise ReplayBindingError(
                f"the board passed for session {session.id} hashes to "
                f"{content_hash}, but manifest {manifest.id} published "
                f"{manifest.puzzle_content_hash}; this is not the board that "
                "was played"
            )

    def require_honoured(self, session: SessionRecord) -> None:
        """Raise unless the recorded rules are this release's rules."""
        try:
            current = self.current_rules(session.game_id)
        except Exception as exc:
            raise RulesVersionError(
                f"session {session.id} was played on game "
                f"{session.game_id!r}, which this release cannot load, so its "
                "transcript cannot be graded under the rules it recorded"
            ) from exc
        if session.rules.is_honoured_by(current):
            return
        differences = ", ".join(
            f"{name}: recorded {getattr(session.rules, name)!r}, "
            f"now {getattr(current, name)!r}"
            for name in ("engine", "game", "scoring", "session_schema")
            if getattr(session.rules, name) != getattr(current, name)
        )
        raise RulesVersionError(
            f"session {session.id} recorded rules this release does not "
            f"grade by ({differences}); grading it under today's rules would "
            "produce a number that cannot be compared with the one the "
            "player saw"
        )


__all__ = ["ReplayBindingError", "ReplayService", "RulesVersionError"]
