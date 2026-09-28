"""The session lifecycle.

One session per player per manifest per kind, keyed on an id minted from those
three parts, so a client that retries ``start`` after a dropped reply resumes
its session instead of opening a second history.

Moves arrive with a caller-supplied sequence number. A repeat of a sequence
already held is idempotent when its payload matches and a conflict when it
does not, and a gap is refused. That is what makes a retry safe on a bad
connection while leaving a replayed or forged move detectable.

Grading happens here, server side, against the stored puzzle record, which
holds the solution. The client is given a manifest's public view and a
presentation model, and at no point before completion is it given the answer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..core import ids
from ..core.errors import ConfigurationError, ConflictError, NotFoundError
from .accessibility import validate_game
from .identity import IdentityService
from .plugin import Hint, MoveJudgement, PresentationModel, Puzzle
from .records import PuzzleStatus
from .registry import GameRegistry, RegisteredGame
from .scoring import ScoringService, puzzle_from_record, replay_state
from .session_storage import SessionRepositories
from .sessions import (
    Clock,
    DayWindow,
    MoveKind,
    MoveOutcome,
    MoveRecord,
    ScoreRecord,
    SessionKind,
    SessionRecord,
    SessionState,
    StreakRecord,
)
from .storage import EngineRepositories

#: Methods added in plugin protocol 1.1. A game registered against 1.0 still
#: generates puzzles; it cannot be played, and saying so by name beats an
#: AttributeError halfway through somebody's first move.
PLAY_METHODS = ("grade_move", "get_hint")


@dataclass(frozen=True, slots=True)
class MoveResult:
    """What one accepted move produced."""

    session: SessionRecord
    judgement: MoveJudgement
    #: Present only when this move ended a live session.
    score: ScoreRecord | None = None
    streak: StreakRecord | None = None
    #: True when the move was already on the ledger and was replayed.
    idempotent: bool = False

    @property
    def finished(self) -> bool:
        return self.session.is_terminal


@dataclass(frozen=True, slots=True)
class HintResult:
    session: SessionRecord
    hint: Hint
    idempotent: bool = False


class SessionService:
    """Starts, advances and ends sessions. Holds no scoring rules of its own."""

    def __init__(
        self,
        sessions: SessionRepositories,
        engine: EngineRepositories,
        registry: GameRegistry,
        *,
        clock: Clock,
        day_window: DayWindow,
        scoring: ScoringService,
        identity: IdentityService,
    ) -> None:
        self._repos = sessions
        self._engine = engine
        self._registry = registry
        self._clock = clock
        self._days = day_window
        self._scoring = scoring
        self._identity = identity

    # -- starting ------------------------------------------------------------

    def start(
        self,
        player_id: str,
        manifest_id: str,
        *,
        allow_practice: bool = True,
    ) -> SessionRecord:
        """Open or resume a session against one published manifest.

        The kind is decided here rather than requested by the caller: a
        manifest for today is a live session and a manifest for any other day
        is practice. A client that could choose would eventually choose wrong,
        and "live" is the flag that admits a score into a ranking.
        """
        player = self._repos.players.require(player_id)
        manifest = self._engine.manifests.get(manifest_id)
        if manifest is None:
            raise NotFoundError("manifests", manifest_id)
        self._playable_game(manifest.game_id)
        puzzle_record = self._engine.puzzles.require(manifest.puzzle_id)
        if puzzle_record.status is PuzzleStatus.INVALIDATED:
            raise ConflictError(
                f"puzzle {puzzle_record.id} was invalidated after publication "
                "and cannot be started"
            )

        now = self._clock.now()
        today = self._days.day_key_for(now)
        kind = SessionKind.LIVE if manifest.day_key == today else SessionKind.PRACTICE
        if kind is SessionKind.PRACTICE and not allow_practice:
            raise ConflictError(
                f"manifest {manifest_id} is for {manifest.day_key}, not {today}"
            )

        session_id = ids.for_session(player.id, manifest.id, kind.value)
        existing = self._repos.sessions.get(session_id)
        if existing is not None:
            return self.resume(existing.id)

        if kind is SessionKind.LIVE:
            expires_at = self._days.expires_at(manifest.day_key)
            if expires_at <= now:
                raise ConflictError(
                    f"the {manifest.day_key} window closed at {expires_at}"
                )
        else:
            # A practice session's clock runs from when it starts, since the
            # day it belongs to is long gone and a past expiry would make the
            # session dead on arrival.
            expires_at = now + (
                self._days.expires_at(today) - self._days.opens_at(today)
            )

        session = SessionRecord(
            id=session_id,
            player_id=player.id,
            manifest_id=manifest.id,
            puzzle_id=manifest.puzzle_id,
            game_id=manifest.game_id,
            day_key=manifest.day_key,
            kind=kind,
            started_at=now,
            last_activity_at=now,
            expires_at=expires_at,
            locale=player.locale,
            accessibility=player.accessibility,
        )
        with self._repos.transaction():
            self._repos.sessions.insert(session)
            self._identity.touch(player.id)
        return session

    def start_today(
        self, player_id: str, game_id: str, *, day_key: str | None = None
    ) -> SessionRecord:
        """Convenience: find the published manifest for a day and start it."""
        day = day_key or self._days.day_key_for(self._clock.now())
        manifests = self._engine.manifests.for_day(day, game_id)
        if not manifests:
            raise NotFoundError("manifests", f"{day}|{game_id}")
        return self.start(player_id, manifests[0].id)

    def resume(self, session_id: str) -> SessionRecord:
        """Read a session, applying expiry if its window has closed.

        Expiry is applied on read rather than by a background sweep, because
        the engine does not own a scheduler and a session nobody looks at
        again never needed the write.
        """
        session = self._repos.sessions.require(session_id)
        return self._expire_if_due(session)

    def _expire_if_due(self, session: SessionRecord) -> SessionRecord:
        if session.is_terminal or self._clock.now() < session.expires_at:
            return session
        game = self._playable_game(session.game_id)
        puzzle = puzzle_from_record(self._engine.puzzles.require(session.puzzle_id))
        expired = self._terminate(
            session, SessionState.EXPIRED, at=session.expires_at, presave=False
        )
        # Expiry settles like any other ending: a zero score is still a fact
        # about the day, and the streak reset is what the player will be shown.
        self._settle(expired, game, puzzle)
        return expired

    # -- playing -------------------------------------------------------------

    def submit(
        self,
        session_id: str,
        sequence: int,
        payload: Mapping[str, Any],
        *,
        client_at: dt.datetime | None = None,
    ) -> MoveResult:
        """Grade and record one move."""
        session = self.resume(session_id)
        game = self._playable_game(session.game_id)
        puzzle = puzzle_from_record(self._engine.puzzles.require(session.puzzle_id))

        replayed = self._replay_if_seen(
            session, game, puzzle, sequence, MoveKind.SUBMIT, payload
        )
        if replayed is not None:
            return replayed
        self._guard_open(session, sequence)

        state, _ = replay_state(session, game.plugin, puzzle)
        judgement = self._grade(game, puzzle, payload, state, session)

        now = self._clock.now()
        move = MoveRecord(
            sequence=sequence,
            kind=MoveKind.SUBMIT,
            outcome=(
                MoveOutcome.CORRECT if judgement.correct else MoveOutcome.INCORRECT
            ),
            server_at=now,
            payload=dict(payload),
            client_at=client_at,
            note=judgement.note,
        )
        session = session.with_move(move)

        if not judgement.complete:
            self._repos.sessions.put(session)
            return MoveResult(session=session, judgement=judgement)

        final_state = (
            SessionState.COMPLETED if judgement.solved else SessionState.ABANDONED
        )
        session = self._terminate(session, final_state, at=now, presave=False)
        score, streak = self._settle(session, game, puzzle)
        return MoveResult(
            session=session, judgement=judgement, score=score, streak=streak
        )

    def request_hint(
        self,
        session_id: str,
        sequence: int,
        *,
        client_at: dt.datetime | None = None,
    ) -> HintResult:
        """Record a hint request and return the game's next hint.

        The hint occupies a sequence number like any other move, so the ledger
        stays a complete account of what happened and the hint count cannot be
        under-reported by a client that simply declines to mention it.
        """
        session = self.resume(session_id)
        game = self._playable_game(session.game_id)
        puzzle = puzzle_from_record(self._engine.puzzles.require(session.puzzle_id))

        existing = session.move_at(sequence)
        if existing is not None:
            if existing.kind is not MoveKind.HINT:
                raise ConflictError(
                    f"sequence {sequence} of session {session.id} is a "
                    f"{existing.kind} move, not a hint request"
                )
            hint = self._hint(game, puzzle, session, before=sequence)
            return HintResult(session=session, hint=hint, idempotent=True)

        self._guard_open(session, sequence)
        hint = self._hint(game, puzzle, session, before=sequence)
        move = MoveRecord(
            sequence=sequence,
            kind=MoveKind.HINT,
            outcome=MoveOutcome.NEUTRAL,
            server_at=self._clock.now(),
            payload={"reveals": list(hint.reveals), "cost": hint.cost},
            client_at=client_at,
            note=hint.text,
        )
        session = session.with_move(move)
        self._repos.sessions.put(session)
        return HintResult(session=session, hint=hint)

    def give_up(self, session_id: str, sequence: int) -> MoveResult:
        """End a session at the player's request, recording that they asked.

        A separate move kind rather than a plain abandon, because "stopped
        playing" and "asked to see the answer" are different facts and only
        one of them is a decision the player made.
        """
        session = self.resume(session_id)
        game = self._playable_game(session.game_id)
        puzzle = puzzle_from_record(self._engine.puzzles.require(session.puzzle_id))
        existing = session.move_at(sequence)
        if existing is not None and existing.kind is MoveKind.GIVE_UP:
            return MoveResult(
                session=session,
                judgement=MoveJudgement(correct=False, complete=True),
                score=self._repos.scores.for_session(session.id),
                idempotent=True,
            )
        self._guard_open(session, sequence)

        now = self._clock.now()
        session = session.with_move(
            MoveRecord(
                sequence=sequence,
                kind=MoveKind.GIVE_UP,
                outcome=MoveOutcome.NEUTRAL,
                server_at=now,
            )
        )
        session = self._terminate(
            session, SessionState.ABANDONED, at=now, presave=False
        )
        score, streak = self._settle(session, game, puzzle)
        return MoveResult(
            session=session,
            judgement=MoveJudgement(correct=False, complete=True),
            score=score,
            streak=streak,
        )

    def abandon(self, session_id: str) -> SessionRecord:
        """Close a session the player walked away from."""
        session = self.resume(session_id)
        if session.is_terminal:
            return session
        game = self._playable_game(session.game_id)
        puzzle = puzzle_from_record(self._engine.puzzles.require(session.puzzle_id))
        session = self._terminate(
            session, SessionState.ABANDONED, at=self._clock.now(), presave=False
        )
        self._settle(session, game, puzzle)
        return session

    def expire_open_sessions(self, *, as_of: dt.datetime | None = None) -> int:
        """Operator sweep. Correctness never depends on it having run."""
        now = as_of or self._clock.now()
        today = self._days.day_key_for(now)
        expired = 0
        for session in self._repos.sessions.open_before(today):
            if now >= session.expires_at:
                self._expire_if_due(session)
                expired += 1
        return expired

    # -- reading -------------------------------------------------------------

    def presentation(self, session_id: str) -> PresentationModel:
        """What the shell should draw, built by the game from public state.

        Built from replayed state rather than from the puzzle's solution, so
        the model handed to a client contains only what that client's own
        moves have already established.
        """
        session = self.resume(session_id)
        game = self._playable_game(session.game_id)
        puzzle = puzzle_from_record(self._engine.puzzles.require(session.puzzle_id))
        state, _ = replay_state(session, game.plugin, puzzle)
        return game.plugin.render(puzzle, state, session.locale)

    def public_manifest(self, session_id: str) -> dict[str, Any]:
        session = self._repos.sessions.require(session_id)
        manifest = self._engine.manifests.require(session.manifest_id)
        return manifest.public_view()

    def sessions_on(self, player_id: str, day_key: str) -> list[SessionRecord]:
        return self._repos.sessions.on_day(player_id, day_key)

    # -- internals -----------------------------------------------------------

    def _playable_game(self, game_id: str) -> RegisteredGame:
        """Resolve a game and refuse the ones that cannot carry a session.

        Two gates, both stated rather than assumed. A game must implement the
        1.1 play methods, and its accessibility declaration must be complete:
        the declaration has existed since phase 4 and was enforced nowhere, so
        a game could promise nothing and still ship.
        """
        game = self._registry.get(game_id)
        missing = [m for m in PLAY_METHODS if not hasattr(game.plugin, m)]
        if missing:
            raise ConfigurationError(
                f"game {game_id} implements plugin protocol "
                f"{game.descriptor.protocol_version} and is missing "
                f"{', '.join(missing)}; sessions need protocol 1.1"
            )
        complete, reason = game.descriptor.accessibility.is_complete()
        if not complete:
            raise ConfigurationError(
                f"game {game_id} cannot be played: {reason}"
            )
        # Stricter than the game's own self-description: the keyboard model
        # must be one the shell installs, the state symbols must exist, and
        # every announcement template must resolve.
        validate_game(game.descriptor)
        return game

    def _guard_open(self, session: SessionRecord, sequence: int) -> None:
        if session.is_terminal:
            raise ConflictError(
                f"session {session.id} ended as {session.state} and takes no "
                "further moves"
            )
        if sequence != session.next_sequence:
            raise ConflictError(
                f"session {session.id} expects sequence "
                f"{session.next_sequence}, got {sequence}; a gap would leave "
                "the ledger unable to reproduce the session"
            )

    def _replay_if_seen(
        self,
        session: SessionRecord,
        game: RegisteredGame,
        puzzle: Puzzle,
        sequence: int,
        kind: MoveKind,
        payload: Mapping[str, Any],
    ) -> MoveResult | None:
        """Handle a resubmitted sequence: identical is idempotent, changed is
        a conflict. Anything else returns ``None`` and takes the normal path."""
        existing = session.move_at(sequence)
        if existing is None:
            return None
        if existing.kind is not kind:
            raise ConflictError(
                f"sequence {sequence} of session {session.id} is already a "
                f"{existing.kind} move"
            )
        candidate = MoveRecord(
            sequence=sequence,
            kind=kind,
            outcome=existing.outcome,
            server_at=existing.server_at,
            payload=dict(payload),
        )
        if candidate.payload_hash() != existing.payload_hash():
            raise ConflictError(
                f"sequence {sequence} of session {session.id} was already "
                "played with different content; a ledger entry is never "
                "rewritten"
            )
        state, _ = replay_state(
            session.model_copy(update={"moves": session.moves[: sequence - 1]}),
            game.plugin,
            puzzle,
        )
        judgement = game.plugin.grade_move(puzzle, dict(payload), state)
        return MoveResult(
            session=session,
            judgement=judgement,
            score=self._repos.scores.for_session(session.id),
            idempotent=True,
        )

    def _grade(
        self,
        game: RegisteredGame,
        puzzle: Puzzle,
        payload: Mapping[str, Any],
        state: Mapping[str, Any],
        session: SessionRecord,
    ) -> MoveJudgement:
        """Call the game's grader, converting a crash into a refused move.

        A plugin exception fails this move rather than the session, mirroring
        the generation pipeline's rule that one game's bug never takes down
        the rest of the day.
        """
        try:
            judgement = game.plugin.grade_move(puzzle, dict(payload), state)
        except Exception as exc:  # noqa: BLE001 - plugin boundary
            raise ConflictError(
                f"game {session.game_id} failed to grade a move in session "
                f"{session.id}: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(judgement, MoveJudgement):
            raise ConflictError(
                f"game {session.game_id} returned "
                f"{type(judgement).__name__} from grade_move(); the boundary "
                "type is MoveJudgement"
            )
        return judgement

    def _hint(
        self,
        game: RegisteredGame,
        puzzle: Puzzle,
        session: SessionRecord,
        *,
        before: int,
    ) -> Hint:
        state, _ = replay_state(
            session.model_copy(update={"moves": session.moves[: before - 1]}),
            game.plugin,
            puzzle,
        )
        used = sum(
            1 for m in session.moves[: before - 1] if m.kind is MoveKind.HINT
        )
        try:
            hint = game.plugin.get_hint(puzzle, state, used)
        except Exception as exc:  # noqa: BLE001 - plugin boundary
            raise ConflictError(
                f"game {session.game_id} failed to produce a hint: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(hint, Hint):
            raise ConflictError(
                f"game {session.game_id} returned {type(hint).__name__} from "
                "get_hint(); the boundary type is Hint"
            )
        return hint

    def _terminate(
        self,
        session: SessionRecord,
        state: SessionState,
        *,
        at: dt.datetime,
        presave: bool = True,
    ) -> SessionRecord:
        ended = session.terminated(state, at)
        if presave:
            self._repos.sessions.put(ended)
        return ended

    def _settle(
        self, session: SessionRecord, game: RegisteredGame, puzzle: Puzzle
    ) -> tuple[ScoreRecord | None, StreakRecord | None]:
        """Write the terminal session, its score and its streak together.

        One transaction, because a session recorded as completed with no score
        beside it is a state no read path knows how to interpret.
        """
        manifest = self._engine.manifests.require(session.manifest_id)
        with self._repos.transaction():
            self._repos.sessions.put(session)
            score = self._scoring.record_score(session, game.plugin, puzzle, manifest)
            streak = self._scoring.advance_streak(session)
        return score, streak
