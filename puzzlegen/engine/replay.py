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
from .records import PuzzleManifest
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

        The plugin and the difficulty score come from the registry and the
        manifest rather than from the caller, so a replay cannot be handed a
        different game or a different difficulty than the one played.
        """
        self.require_honoured(session)
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


__all__ = ["ReplayService", "RulesVersionError"]
