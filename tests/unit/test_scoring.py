"""Scoring and streaks.

The claims under test are that telemetry is derived rather than supplied, that
a score is a pure function of the puzzle and the ordered moves, and that the
streak rule has no quiet exceptions. Each one fails silently if it is wrong: a
supplied signal looks like a measured one, a score that cannot be rebuilt
looks correct until somebody rebuilds it, and a streak that forgives a gap
looks generous until it is tightened.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core import ids
from puzzlegen.core.errors import ConflictError, DeterminismError
from puzzlegen.engine.plugin import Puzzle, Score, SessionTelemetry
from puzzlegen.engine.scoring import (
    OPTIMAL_ATTEMPTS_KEY,
    ScoringService,
    build_telemetry,
    measure_efficiency,
    move_events,
    puzzle_from_record,
)
from puzzlegen.engine.sessions import (
    MoveKind,
    MoveOutcome,
    MoveRecord,
    SessionKind,
    SessionRecord,
    SessionState,
    StreakRecord,
)

from conftest import DAY, build_published_day

NOW = dt.datetime(2026, 9, 27, 12, 0, 0, tzinfo=dt.timezone.utc)


def a_puzzle(**presentation) -> Puzzle:
    return Puzzle(
        game_id="oddoneout",
        payload={
            "options": ["opt_a", "opt_b", "opt_c", "opt_d"],
            "labels": {
                "opt_a": "tiger",
                "opt_b": "lion",
                "opt_c": "puma",
                "opt_d": "eagle",
            },
            "prompt": "Three share a category",
        },
        solution={"answer": "opt_d"},
        fact_refs=("fact:one",),
        presentation=presentation,
    )


def a_session(*, moves=(), state=SessionState.COMPLETED, kind=SessionKind.LIVE, day=DAY):
    player_id = ids.for_player("tok")
    manifest_id = ids.for_manifest(ids.for_puzzle(day, "oddoneout", "h"))
    session = SessionRecord(
        id=ids.for_session(player_id, manifest_id, kind.value),
        player_id=player_id,
        manifest_id=manifest_id,
        puzzle_id=ids.for_puzzle(day, "oddoneout", "h"),
        game_id="oddoneout",
        day_key=day,
        kind=kind,
        started_at=NOW,
        last_activity_at=NOW,
        expires_at=NOW + dt.timedelta(hours=12),
    )
    for move in moves:
        session = session.with_move(move)
    if state is not SessionState.IN_PROGRESS:
        session = session.terminated(state, session.last_activity_at)
    return session


def submit(sequence, *, correct, seconds=0, payload=None):
    return MoveRecord(
        sequence=sequence,
        kind=MoveKind.SUBMIT,
        outcome=MoveOutcome.CORRECT if correct else MoveOutcome.INCORRECT,
        server_at=NOW + dt.timedelta(seconds=seconds),
        payload=payload or {"choice": f"opt_{sequence}"},
    )


def hint(sequence, *, seconds=0):
    return MoveRecord(
        sequence=sequence,
        kind=MoveKind.HINT,
        outcome=MoveOutcome.NEUTRAL,
        server_at=NOW + dt.timedelta(seconds=seconds),
    )


class TestTelemetry:
    def test_every_counter_comes_from_the_ledger(self):
        session = a_session(
            moves=(submit(1, correct=False), hint(2, seconds=10), submit(3, correct=True, seconds=40))
        )
        telemetry = build_telemetry(session, a_puzzle(), difficulty_score=0.4)
        assert telemetry.completed
        assert telemetry.attempts == 2
        assert telemetry.mistakes == 1
        assert telemetry.hints_used == 1
        assert telemetry.elapsed_ms == 40_000

    def test_move_events_carry_no_payload(self):
        session = a_session(
            moves=(submit(1, correct=True, payload={"choice": "opt_d"}),)
        )
        events = move_events(session)
        assert events == (
            {"sequence": 1, "kind": "SUBMIT", "outcome": "CORRECT", "offset_ms": 0},
        )

    def test_events_are_ordered_and_offset_from_the_start(self):
        session = a_session(
            moves=(submit(1, correct=False), submit(2, correct=True, seconds=12))
        )
        offsets = [event["offset_ms"] for event in move_events(session)]
        assert offsets == [0, 12_000]

    def test_efficiency_is_neutral_without_a_declared_optimum(self):
        session = a_session(moves=(submit(1, correct=False), submit(2, correct=True)))
        assert measure_efficiency(session, a_puzzle()) == 1.0

    def test_efficiency_measures_against_the_declared_optimum(self):
        session = a_session(moves=(submit(1, correct=False), submit(2, correct=True)))
        puzzle = a_puzzle(**{OPTIMAL_ATTEMPTS_KEY: 1})
        assert measure_efficiency(session, puzzle) == 0.5

    def test_beating_the_optimum_is_capped_at_one(self):
        session = a_session(moves=(submit(1, correct=True),))
        puzzle = a_puzzle(**{OPTIMAL_ATTEMPTS_KEY: 3})
        assert measure_efficiency(session, puzzle) == 1.0

    def test_a_nonsense_optimum_is_ignored(self):
        session = a_session(moves=(submit(1, correct=True),))
        assert measure_efficiency(session, a_puzzle(optimal_attempts=0)) == 1.0
        assert measure_efficiency(session, a_puzzle(optimal_attempts="two")) == 1.0

    def test_puzzle_from_record_carries_the_solution(self, engine_repos):
        manifest = build_published_day(engine_repos)
        record = engine_repos.puzzles.require(manifest.puzzle_id)
        puzzle = puzzle_from_record(record)
        assert puzzle.solution == {"answer": "opt_d"}
        assert puzzle.fact_refs == record.fact_refs


class TestEvaluate:
    def test_an_open_session_cannot_be_scored(self, scoring, game):
        session = a_session(state=SessionState.IN_PROGRESS)
        with pytest.raises(ValueError, match="still in progress"):
            scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)

    def test_points_come_from_the_game(self, scoring, game):
        session = a_session(moves=(submit(1, correct=True),))
        record = scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)
        assert record.points == 100

    def test_mistakes_and_hints_reduce_the_score(self, scoring, game):
        session = a_session(
            moves=(submit(1, correct=False), hint(2), submit(3, correct=True))
        )
        record = scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)
        assert record.points == 70

    def test_tiebreakers_are_time_then_mistakes_then_hints_then_attempts(
        self, scoring, game
    ):
        session = a_session(
            moves=(submit(1, correct=False), hint(2), submit(3, correct=True, seconds=5))
        )
        record = scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)
        assert record.tiebreakers == (5_000, 1, 1, 2)

    def test_the_ledger_hash_is_recorded_with_the_score(self, scoring, game):
        session = a_session(moves=(submit(1, correct=True),))
        record = scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)
        assert record.ledger_hash == session.ledger_hash()

    def test_scoring_the_same_session_twice_agrees(self, scoring, game):
        session = a_session(moves=(submit(1, correct=True),))
        first = scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)
        again = scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)
        assert first == again

    def test_a_game_returning_the_wrong_type_is_caught(self, scoring, game):
        class BadScorer:
            def score(self, puzzle, telemetry):
                return 42

        session = a_session(moves=(submit(1, correct=True),))
        with pytest.raises(DeterminismError, match="boundary type is Score"):
            scoring.evaluate(session, BadScorer(), a_puzzle(), difficulty_score=0.4)

    def test_a_negative_score_is_refused_at_the_boundary(self):
        with pytest.raises(ValueError, match="negative"):
            Score(points=-1)


class TestRanking:
    def test_ranking_key_orders_points_down_and_tiebreakers_up(self, scoring, game):
        fast = a_session(moves=(submit(1, correct=True),))
        slow = a_session(moves=(submit(1, correct=True, seconds=90),))
        fast_score = scoring.evaluate(fast, game, a_puzzle(), difficulty_score=0.4)
        slow_score = scoring.evaluate(slow, game, a_puzzle(), difficulty_score=0.4)
        assert fast_score.ranking_key() < slow_score.ranking_key()

    def test_the_day_ranking_is_sorted(self, session_repos, scoring, game):
        for index, seconds in enumerate((60, 5, 30)):
            player = ids.for_player(f"p{index}")
            manifest = ids.for_manifest(ids.for_puzzle(DAY, "oddoneout", "h"))
            session = SessionRecord(
                id=ids.for_session(player, manifest, "LIVE"),
                player_id=player,
                manifest_id=manifest,
                puzzle_id=ids.for_puzzle(DAY, "oddoneout", "h"),
                game_id="oddoneout",
                day_key=DAY,
                started_at=NOW,
                last_activity_at=NOW,
                expires_at=NOW + dt.timedelta(hours=12),
            ).with_move(submit(1, correct=True, seconds=seconds))
            session = session.terminated(SessionState.COMPLETED, session.last_activity_at)
            session_repos.sessions.put(session)
            session_repos.scores.put(
                scoring.evaluate(session, game, a_puzzle(), difficulty_score=0.4)
            )
        ranking = scoring.day_ranking(DAY, "oddoneout")
        assert [score.elapsed_ms for score in ranking] == [5_000, 30_000, 60_000]
        assert scoring.rank_of(ranking[1]) == (2, 3)


class TestStoredScores:
    def test_a_practice_session_is_not_scored(
        self, scoring, session_repos, engine_repos, game
    ):
        manifest = build_published_day(engine_repos)
        session = a_session(
            moves=(submit(1, correct=True),), kind=SessionKind.PRACTICE
        )
        assert scoring.record_score(session, game, a_puzzle(), manifest) is None
        assert session_repos.scores.count() == 0

    def test_a_score_is_written_once(
        self, scoring, session_repos, engine_repos, game
    ):
        manifest = build_published_day(engine_repos)
        session = a_session(moves=(submit(1, correct=True),))
        record = scoring.record_score(session, game, a_puzzle(), manifest)
        scoring.record_score(session, game, a_puzzle(), manifest)  # idempotent
        assert session_repos.scores.count() == 1
        with pytest.raises(ConflictError, match="already final"):
            session_repos.scores.put(record.model_copy(update={"points": 999}))

    def test_recompute_reproduces_a_stored_score(
        self, scoring, engine_repos, game
    ):
        manifest = build_published_day(engine_repos)
        session = a_session(moves=(submit(1, correct=True),))
        stored = scoring.record_score(session, game, a_puzzle(), manifest)
        assert scoring.recompute(session, game, a_puzzle(), manifest) == stored

    def test_recompute_catches_a_changed_ledger(
        self, scoring, engine_repos, game
    ):
        manifest = build_published_day(engine_repos)
        session = a_session(moves=(submit(1, correct=True),))
        scoring.record_score(session, game, a_puzzle(), manifest)
        tampered = a_session(
            moves=(submit(1, correct=True, payload={"choice": "elsewhere"}),)
        )
        with pytest.raises(DeterminismError, match="ledger changed"):
            scoring.recompute(tampered, game, a_puzzle(), manifest)

    def test_recompute_catches_a_changed_scoring_rule(
        self, scoring, engine_repos, game
    ):
        manifest = build_published_day(engine_repos)
        session = a_session(moves=(submit(1, correct=True),))
        scoring.record_score(session, game, a_puzzle(), manifest)

        class Rescored:
            def score(self, puzzle, telemetry):
                return Score(points=5)

        with pytest.raises(DeterminismError, match="does not reproduce"):
            scoring.recompute(session, Rescored(), a_puzzle(), manifest)

    def test_recompute_without_a_stored_score_returns_the_rebuild(
        self, scoring, engine_repos, game
    ):
        manifest = build_published_day(engine_repos)
        session = a_session(moves=(submit(1, correct=True),))
        assert scoring.recompute(session, game, a_puzzle(), manifest).points == 100


class TestStreaks:
    def test_an_unseen_streak_starts_at_zero(self, scoring):
        streak = scoring.streak_for(ids.for_player("tok"), "oddoneout")
        assert streak.current == 0
        assert streak.last_day_key is None

    def test_a_completed_day_starts_a_streak(self, scoring):
        streak = scoring.advance_streak(a_session(moves=(submit(1, correct=True),)))
        assert streak.current == 1
        assert streak.longest == 1

    def test_consecutive_days_extend_it(self, scoring, session_repos):
        for day in ("2026-09-25", "2026-09-26", "2026-09-27"):
            session = a_session(moves=(submit(1, correct=True),), day=day)
            session_repos.sessions.put(session)
            streak = scoring.advance_streak(session)
        assert streak.current == 3
        assert streak.longest == 3

    def test_a_missed_day_resets_it(self, scoring, session_repos):
        for day in ("2026-09-20", "2026-09-21", "2026-09-27"):
            session = a_session(moves=(submit(1, correct=True),), day=day)
            session_repos.sessions.put(session)
            streak = scoring.advance_streak(session)
        assert streak.current == 1
        assert streak.longest == 2

    def test_replaying_the_same_day_changes_nothing(self, scoring):
        session = a_session(moves=(submit(1, correct=True),))
        scoring.advance_streak(session)
        assert scoring.advance_streak(session).current == 1

    def test_an_abandoned_day_resets_it(self, scoring, session_repos):
        first = a_session(moves=(submit(1, correct=True),), day="2026-09-26")
        session_repos.sessions.put(first)
        scoring.advance_streak(first)
        lost = a_session(
            moves=(submit(1, correct=False),), state=SessionState.ABANDONED
        )
        assert scoring.advance_streak(lost).current == 0

    def test_an_expired_day_resets_it(self, scoring, session_repos):
        first = a_session(moves=(submit(1, correct=True),), day="2026-09-26")
        session_repos.sessions.put(first)
        scoring.advance_streak(first)
        expired = a_session(state=SessionState.EXPIRED)
        assert scoring.advance_streak(expired).current == 0

    def test_a_practice_session_advances_nothing(self, scoring):
        session = a_session(
            moves=(submit(1, correct=True),), kind=SessionKind.PRACTICE
        )
        assert scoring.advance_streak(session) is None

    def test_an_open_session_advances_nothing(self, scoring):
        session = a_session(state=SessionState.IN_PROGRESS)
        assert scoring.advance_streak(session) is None

    def test_a_back_filled_day_rebuilds_from_history(self, scoring, session_repos):
        recent = a_session(moves=(submit(1, correct=True),), day="2026-09-27")
        session_repos.sessions.put(recent)
        scoring.advance_streak(recent)
        older = a_session(moves=(submit(1, correct=True),), day="2026-09-26")
        session_repos.sessions.put(older)
        streak = scoring.advance_streak(older)
        assert streak.current == 2
        assert streak.last_day_key == "2026-09-27"

    def test_rebuild_ignores_practice_and_unfinished_days(
        self, scoring, session_repos
    ):
        session_repos.sessions.put(
            a_session(moves=(submit(1, correct=True),), day="2026-09-26")
        )
        session_repos.sessions.put(
            a_session(
                moves=(submit(1, correct=True),),
                day="2026-09-27",
                kind=SessionKind.PRACTICE,
            )
        )
        streak = scoring.rebuild_streak(ids.for_player("tok"), "oddoneout")
        assert streak.current == 1
        assert streak.last_day_key == "2026-09-26"

    def test_liveness_is_answered_before_any_reset_is_written(self, scoring):
        streak = scoring.advance_streak(a_session(moves=(submit(1, correct=True),)))
        assert scoring.streak_is_live(streak, "2026-09-28")
        assert not scoring.streak_is_live(streak, "2026-09-29")

    def test_a_zero_streak_is_never_live(self, scoring):
        streak = scoring.streak_for(ids.for_player("tok"), "oddoneout")
        assert not scoring.streak_is_live(streak, DAY)

    def test_a_non_iso_day_key_is_refused(self, scoring, session_repos):
        session = a_session(moves=(submit(1, correct=True),), day="2026-09-26")
        session_repos.sessions.put(session)
        scoring.advance_streak(session)
        with pytest.raises(ValueError, match="ISO day keys"):
            scoring.advance_streak(
                a_session(moves=(submit(1, correct=True),), day="week-39")
            )

    def test_longest_is_never_below_current(self):
        with pytest.raises(ValueError, match="longest"):
            StreakRecord(
                id=ids.for_streak(ids.for_player("tok"), "g"),
                player_id=ids.for_player("tok"),
                game_id="g",
                current=5,
                longest=2,
                last_day_key=DAY,
                updated_at=NOW,
            )


class TestEndToEndScoring:
    def test_a_played_session_scores_and_streaks_together(
        self, sessions, scoring, player, published
    ):
        session = sessions.start(player.id, published.id)
        result = sessions.submit(session.id, 1, {"choice": "opt_d"})
        assert result.score.points == 100
        assert result.score.ledger_hash == result.session.ledger_hash()
        assert scoring.player_history(player.id)[0].id == result.score.id
        assert scoring.streak_for(player.id, "oddoneout").current == 1

    def test_telemetry_reaching_the_game_is_the_derived_one(
        self, sessions, registry, engine_repos, player, game
    ):
        seen: list[SessionTelemetry] = []

        class Recording:
            def describe(self):
                descriptor = game.describe()
                return descriptor.__class__(
                    game_id="recording",
                    display_name="Recording",
                    game_version="1.0.0",
                    accessibility=descriptor.accessibility,
                    share_tokens=descriptor.share_tokens,
                )

            def score(self, puzzle, telemetry):
                seen.append(telemetry)
                return Score(points=1)

            grade_move = game.grade_move
            get_hint = game.get_hint

        registry.register(Recording())
        manifest = build_published_day(engine_repos, game_id="recording")
        session = sessions.start(player.id, manifest.id)
        sessions.submit(session.id, 1, {"choice": "opt_a", "elapsed_ms": 1})
        sessions.submit(session.id, 2, {"choice": "opt_d"})
        telemetry = seen[0]
        assert telemetry.attempts == 2
        assert telemetry.mistakes == 1
        assert all("payload" not in event for event in telemetry.events)
