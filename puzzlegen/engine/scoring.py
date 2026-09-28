"""Telemetry, scores and streaks.

Three properties are what this module exists to hold.

Telemetry is derived, never submitted. Every number a game's scoring function
reads is computed here from the move ledger and from timestamps the engine
wrote, so a game cannot be handed a favourable signal by a modified client.

Scoring is a pure function of the puzzle and the ordered moves. The stored
``ScoreRecord`` is a cache of that function, and ``recompute`` rebuilds it
from the same inputs. A number that cannot be rebuilt cannot be audited, and a
rebuild that disagrees is a bug worth raising rather than a difference worth
absorbing.

Ranking is within one game. Points plus an explicit tiebreaker tuple order one
day of one game and nothing wider, for the same reason difficulty thresholds
are per game: a single scale across games would be comparable and wrong.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from ..core import ids
from ..core.errors import DeterminismError
from ..core.versions import ENGINE_VERSION
from .plugin import GamePlugin, Puzzle, Score, SessionTelemetry
from .records import PuzzleManifest, PuzzleRecord
from .session_storage import SessionRepositories
from .sessions import (
    Clock,
    MoveKind,
    MoveOutcome,
    ScoreRecord,
    SessionKind,
    SessionRecord,
    SessionState,
    StreakRecord,
)

#: Reserved key a game may set in ``Puzzle.presentation`` to declare the fewest
#: submissions in which its puzzle can be solved. The engine cannot know this
#: for an arbitrary game, so efficiency is only measured when it is declared.
OPTIMAL_ATTEMPTS_KEY = "optimal_attempts"


def puzzle_from_record(record: PuzzleRecord) -> Puzzle:
    """Rebuild the plugin-facing puzzle from its stored record.

    The engine holds the stored record and the game expects the boundary type;
    converting here rather than at each call site keeps the solution's one
    crossing point visible in a single function.
    """
    return Puzzle(
        game_id=record.game_id,
        payload=dict(record.payload),
        solution=dict(record.solution),
        fact_refs=record.fact_refs,
        presentation=dict(record.presentation),
    )


def move_events(session: SessionRecord) -> tuple[Mapping[str, Any], ...]:
    """The ledger, reduced to what a scoring or share function may see.

    Payloads are deliberately excluded. A share artifact built from raw move
    payloads would reproduce the player's guesses, and a guess names parts of
    the answer; the shape of the attempt is all a grid ever needs.
    """
    return tuple(
        {
            "sequence": move.sequence,
            "kind": move.kind.value,
            "outcome": move.outcome.value,
            "offset_ms": int(
                (move.server_at - session.started_at).total_seconds() * 1000
            ),
        }
        for move in session.moves
    )


def measure_efficiency(session: SessionRecord, puzzle: Puzzle) -> float:
    """Submissions against the declared optimum, on [0, 1].

    A game that declares no optimum gets 1.0 rather than a guess: the neutral
    value leaves a score resting on the signals that were actually measured,
    where an invented optimum would quietly penalise every player of that game.
    """
    declared = puzzle.presentation.get(OPTIMAL_ATTEMPTS_KEY)
    if not isinstance(declared, int) or declared < 1:
        return 1.0
    attempts = session.attempts
    if attempts < 1:
        return 0.0 if session.state is SessionState.COMPLETED else 1.0
    return min(1.0, declared / max(attempts, declared))


def build_telemetry(
    session: SessionRecord,
    puzzle: Puzzle,
    *,
    difficulty_score: float,
) -> SessionTelemetry:
    """Everything a scoring function is allowed to read, all of it derived."""
    return SessionTelemetry(
        completed=session.state is SessionState.COMPLETED,
        attempts=session.attempts,
        elapsed_ms=session.elapsed_ms,
        mistakes=session.mistakes,
        hints_used=session.hints_used,
        efficiency=measure_efficiency(session, puzzle),
        difficulty_score=difficulty_score,
        events=move_events(session),
    )


def _previous_day(day_key: str) -> str:
    """The day before, on ISO day keys.

    Streaks are the one place day keys are treated as dates rather than opaque
    strings, so a day window producing anything other than ISO dates has to
    say so here rather than silently breaking a streak rule.
    """
    try:
        day = dt.date.fromisoformat(day_key)
    except ValueError as exc:
        raise ValueError(
            f"streaks need ISO day keys; {day_key!r} is not one"
        ) from exc
    return (day - dt.timedelta(days=1)).isoformat()


class ScoringService:
    """Builds, stores and verifies scores and streaks."""

    def __init__(
        self,
        repositories: SessionRepositories,
        *,
        clock: Clock,
        engine_version: str = ENGINE_VERSION,
    ) -> None:
        self._repos = repositories
        self._clock = clock
        self._engine_version = engine_version

    # -- scoring -------------------------------------------------------------

    def evaluate(
        self,
        session: SessionRecord,
        plugin: GamePlugin,
        puzzle: Puzzle,
        *,
        difficulty_score: float,
        scored_at: dt.datetime | None = None,
    ) -> ScoreRecord:
        """Compute a score without storing it. Pure, given its four inputs."""
        if not session.is_terminal:
            raise ValueError(
                f"session {session.id} is still in progress; a score is only "
                "meaningful once play has stopped"
            )
        telemetry = build_telemetry(
            session, puzzle, difficulty_score=difficulty_score
        )
        score: Score = plugin.score(puzzle, telemetry)
        if not isinstance(score, Score):
            raise DeterminismError(
                f"game {session.game_id} returned {type(score).__name__} "
                "from score(); the boundary type is Score"
            )
        return ScoreRecord(
            id=ids.for_score(session.id),
            session_id=session.id,
            player_id=session.player_id,
            game_id=session.game_id,
            day_key=session.day_key,
            points=score.points,
            breakdown=dict(score.breakdown),
            detail=score.detail,
            # Fewer milliseconds, then fewer mistakes, then fewer hints, then
            # fewer submissions. Ordered by how directly each reflects play.
            tiebreakers=(
                session.elapsed_ms,
                session.mistakes,
                session.hints_used,
                session.attempts,
            ),
            completed=telemetry.completed,
            attempts=telemetry.attempts,
            mistakes=telemetry.mistakes,
            hints_used=telemetry.hints_used,
            elapsed_ms=telemetry.elapsed_ms,
            efficiency=telemetry.efficiency,
            difficulty_score=telemetry.difficulty_score,
            ledger_hash=session.ledger_hash(),
            engine_version=self._engine_version,
            scored_at=scored_at or session.ended_at or self._clock.now(),
        )

    def record_score(
        self,
        session: SessionRecord,
        plugin: GamePlugin,
        puzzle: Puzzle,
        manifest: PuzzleManifest,
    ) -> ScoreRecord | None:
        """Score a finished session and store the result.

        Practice sessions return ``None`` and store nothing. They are recorded
        in full as sessions, so a player can see they played, and they enter
        no ranking, no streak and no share, which is the whole of what
        "excluded from every ranking" has to mean.
        """
        if session.kind is not SessionKind.LIVE:
            return None
        record = self.evaluate(
            session,
            plugin,
            puzzle,
            difficulty_score=manifest.difficulty.score,
        )
        self._repos.scores.put(record)
        return record

    def recompute(
        self,
        session: SessionRecord,
        plugin: GamePlugin,
        puzzle: Puzzle,
        manifest: PuzzleManifest,
    ) -> ScoreRecord:
        """Rebuild a stored score from its inputs and prove it still holds.

        A disagreement is raised rather than written. The stored number is
        what a player already saw; if the same ledger now produces a different
        score, something changed underneath a published result and that is
        worth stopping for.
        """
        stored = self._repos.scores.for_session(session.id)
        rebuilt = self.evaluate(
            session,
            plugin,
            puzzle,
            difficulty_score=manifest.difficulty.score,
            scored_at=stored.scored_at if stored else None,
        )
        if stored is None:
            return rebuilt
        if stored.ledger_hash != rebuilt.ledger_hash:
            raise DeterminismError(
                f"session {session.id} ledger changed under score "
                f"{stored.id}: stored {stored.ledger_hash[:12]}, "
                f"recomputed {rebuilt.ledger_hash[:12]}"
            )
        if stored.points != rebuilt.points or stored.tiebreakers != rebuilt.tiebreakers:
            raise DeterminismError(
                f"score {stored.id} does not reproduce: stored "
                f"{stored.points} points, recomputed {rebuilt.points} "
                f"(stored engine {stored.engine_version}, now "
                f"{self._engine_version})"
            )
        return stored

    # -- streaks -------------------------------------------------------------

    def streak_for(self, player_id: str, game_id: str) -> StreakRecord:
        """The current streak, or a zeroed one that has never been stored."""
        existing = self._repos.streaks.get(ids.for_streak(player_id, game_id))
        if existing is not None:
            return existing
        return StreakRecord(
            id=ids.for_streak(player_id, game_id),
            player_id=player_id,
            game_id=game_id,
            updated_at=self._clock.now(),
        )

    def advance_streak(self, session: SessionRecord) -> StreakRecord | None:
        """Advance, hold or reset a streak for one finished session.

        Only a completed live session advances anything. A session that
        expired or was abandoned resets the streak, because the rule players
        are told is "play every day" and a rule with quiet exceptions cannot
        be tightened later without rewriting history.
        """
        if session.kind is not SessionKind.LIVE or not session.is_terminal:
            return None
        streak = self.streak_for(session.player_id, session.game_id)
        now = self._clock.now()

        if session.state is not SessionState.COMPLETED:
            updated = streak.model_copy(
                update={"current": 0, "updated_at": now}
            )
            self._repos.streaks.put(updated)
            return updated

        if streak.last_day_key == session.day_key:
            return streak  # Already counted; re-entry changes nothing.

        if streak.last_day_key is not None and session.day_key < streak.last_day_key:
            # A back-filled day older than the streak's head. Recomputing the
            # whole run from stored history is the only correct answer here,
            # and it is cheap because the days are already indexed.
            return self.rebuild_streak(session.player_id, session.game_id)

        if streak.last_day_key == _previous_day(session.day_key):
            current = streak.current + 1
        else:
            current = 1

        updated = streak.model_copy(
            update={
                "current": current,
                "longest": max(current, streak.longest),
                "last_day_key": session.day_key,
                "updated_at": now,
            }
        )
        self._repos.streaks.put(updated)
        return updated

    def rebuild_streak(self, player_id: str, game_id: str) -> StreakRecord:
        """Recompute a streak from every completed day on record.

        The authority is the session history, not the streak record; the
        record is a cache like a score is, and this is how it is rebuilt when
        history arrives out of order.
        """
        days = self._repos.sessions.completed_days(player_id, game_id)
        current = 0
        longest = 0
        previous: str | None = None
        for day in days:
            if previous is not None and day == previous:
                continue
            if previous is not None and _previous_day(day) == previous:
                current += 1
            else:
                current = 1
            longest = max(longest, current)
            previous = day
        updated = StreakRecord(
            id=ids.for_streak(player_id, game_id),
            player_id=player_id,
            game_id=game_id,
            current=current,
            longest=longest,
            last_day_key=previous,
            updated_at=self._clock.now(),
        )
        self._repos.streaks.put(updated)
        return updated

    def streak_is_live(self, streak: StreakRecord, today: str) -> bool:
        """Whether a stored streak is still alive as of ``today``.

        A streak whose last day is two days back is already broken, but
        nothing has written that yet because the player has not come back.
        Callers asking what to display need the answer before the reset.
        """
        if streak.current == 0 or streak.last_day_key is None:
            return False
        return streak.last_day_key in (today, _previous_day(today))

    # -- reading -------------------------------------------------------------

    def day_ranking(self, day_key: str, game_id: str) -> list[ScoreRecord]:
        return self._repos.scores.for_day(day_key, game_id)

    def player_history(
        self, player_id: str, game_id: str | None = None
    ) -> list[ScoreRecord]:
        return self._repos.scores.for_player(player_id, game_id)

    def rank_of(self, score: ScoreRecord) -> tuple[int, int]:
        return self._repos.scores.rank_of(score)


def replay_state(
    session: SessionRecord, plugin: GamePlugin, puzzle: Puzzle
) -> tuple[Mapping[str, Any], bool]:
    """Rebuild game state by replaying the ledger, returning it and whether
    the replay reported the puzzle complete.

    Replay rather than cache. A stored state and a stored ledger can disagree,
    and when they do there is no way to tell which one is right; a derived
    state is always exactly what the moves say it is. The cost is O(moves) per
    submission, bounded by the ledger cap.
    """
    state: Mapping[str, Any] = {}
    complete = False
    for move in session.moves:
        if move.kind is not MoveKind.SUBMIT:
            continue
        judgement = plugin.grade_move(puzzle, dict(move.payload), state)
        state = dict(judgement.state)
        complete = complete or judgement.complete
        if (judgement.correct and move.outcome is MoveOutcome.INCORRECT) or (
            not judgement.correct and move.outcome is MoveOutcome.CORRECT
        ):
            raise DeterminismError(
                f"game {session.game_id} regraded move {move.sequence} of "
                f"session {session.id} differently; grading must be a pure "
                "function of the puzzle and the moves before it"
            )
    return state, complete
