"""Sessions: the record's own invariants, and the service that advances them.

Two things are worth stating about what is tested here. The ledger rules
(sequence, idempotency, terminality) are tested through the service rather
than only on the record, because a rule enforced in one of the two places and
not the other is a rule a caller can walk around. And every storage-dependent
test runs on both backends through the shared ``store`` fixture, so the
session collections cannot drift the way the graph collections cannot.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core import ids
from puzzlegen.core.errors import ConfigurationError, ConflictError, NotFoundError
from puzzlegen.engine.plugin import (
    AccessibilityDeclaration,
    GameDescriptor,
    MoveJudgement,
    StateSymbol,
)
from puzzlegen.engine.sessions import (
    MAX_MOVES,
    AccessibilityProfile,
    MoveKind,
    MoveOutcome,
    MoveRecord,
    OffsetDayWindow,
    SessionKind,
    SessionRecord,
    SessionState,
    UtcDayWindow,
)

from conftest import DAY, build_published_day

NOW = dt.datetime(2026, 9, 27, 12, 0, 0, tzinfo=dt.timezone.utc)


def make_session(**overrides) -> SessionRecord:
    player_id = overrides.pop("player_id", ids.for_player("tok"))
    manifest_id = overrides.pop("manifest_id", ids.for_manifest(ids.for_puzzle(DAY, "g", "h")))
    kind = overrides.pop("kind", SessionKind.LIVE)
    fields = {
        "id": ids.for_session(player_id, manifest_id, kind.value),
        "player_id": player_id,
        "manifest_id": manifest_id,
        "puzzle_id": ids.for_puzzle(DAY, "g", "h"),
        "game_id": "g",
        "day_key": DAY,
        "kind": kind,
        "started_at": NOW,
        "last_activity_at": NOW,
        "expires_at": NOW + dt.timedelta(hours=12),
    }
    fields.update(overrides)
    return SessionRecord(**fields)


def submit_move(sequence: int, *, correct: bool, at: dt.datetime = NOW) -> MoveRecord:
    return MoveRecord(
        sequence=sequence,
        kind=MoveKind.SUBMIT,
        outcome=MoveOutcome.CORRECT if correct else MoveOutcome.INCORRECT,
        server_at=at,
        payload={"choice": f"opt_{sequence}"},
    )


class TestDayWindows:
    def test_utc_day_runs_midnight_to_midnight(self, day_window):
        assert day_window.day_key_for(NOW) == DAY
        assert day_window.opens_at(DAY).hour == 0
        assert day_window.expires_at(DAY) == day_window.opens_at("2026-09-28")

    def test_grace_extends_expiry_only(self):
        window = UtcDayWindow(grace=dt.timedelta(hours=2))
        assert window.day_key_for(NOW) == DAY
        assert window.expires_at(DAY).hour == 2

    def test_negative_grace_is_refused(self):
        with pytest.raises(ValueError):
            UtcDayWindow(grace=dt.timedelta(hours=-1))

    def test_offset_window_moves_the_boundary(self):
        window = OffsetDayWindow(offset=dt.timedelta(hours=-6))
        late = dt.datetime(2026, 9, 27, 3, 0, tzinfo=dt.timezone.utc)
        assert window.day_key_for(late) == "2026-09-26"
        assert window.opens_at(DAY).hour == 6

    def test_offset_must_be_under_a_day(self):
        with pytest.raises(ValueError):
            OffsetDayWindow(offset=dt.timedelta(hours=30))


class TestAccessibilityProfile:
    def test_text_scale_is_bounded(self):
        with pytest.raises(ValueError):
            AccessibilityProfile(text_scale=9.0)

    def test_colour_vision_or_contrast_means_symbols_are_needed(self):
        from puzzlegen.engine.sessions import ColourVision

        assert AccessibilityProfile(high_contrast=True).needs_symbolic_state
        assert AccessibilityProfile(
            colour_vision=ColourVision.PROTAN
        ).needs_symbolic_state
        assert not AccessibilityProfile().needs_symbolic_state


class TestSessionRecord:
    def test_id_must_be_minted_from_player_manifest_and_kind(self):
        with pytest.raises(ValueError, match="minted from player"):
            make_session(id=ids.for_session("player:x", "manifest:y", "LIVE"))

    def test_practice_and_live_are_different_sessions(self):
        live = make_session()
        practice = make_session(kind=SessionKind.PRACTICE)
        assert live.id != practice.id

    def test_a_terminal_session_needs_an_end_time(self):
        with pytest.raises(ValueError, match="terminal session"):
            make_session(state=SessionState.COMPLETED)

    def test_an_open_session_must_not_have_one(self):
        with pytest.raises(ValueError, match="terminal session"):
            make_session(ended_at=NOW)

    def test_ledger_sequences_must_be_contiguous(self):
        with pytest.raises(ValueError, match="breaks the ledger"):
            make_session(moves=(submit_move(1, correct=False), submit_move(3, correct=True)))

    def test_move_timestamps_must_not_go_backwards(self):
        with pytest.raises(ValueError, match="backwards"):
            make_session(
                moves=(
                    submit_move(1, correct=False, at=NOW + dt.timedelta(minutes=5)),
                    submit_move(2, correct=True, at=NOW),
                )
            )

    def test_only_a_submit_can_be_right_or_wrong(self):
        with pytest.raises(ValueError, match="only a SUBMIT"):
            MoveRecord(
                sequence=1,
                kind=MoveKind.HINT,
                outcome=MoveOutcome.CORRECT,
                server_at=NOW,
            )

    def test_counters_come_from_the_ledger(self):
        session = make_session(
            moves=(
                submit_move(1, correct=False),
                MoveRecord(
                    sequence=2,
                    kind=MoveKind.HINT,
                    outcome=MoveOutcome.NEUTRAL,
                    server_at=NOW,
                ),
                submit_move(3, correct=True),
            )
        )
        assert session.attempts == 2
        assert session.mistakes == 1
        assert session.hints_used == 1
        assert session.next_sequence == 4

    def test_elapsed_is_measured_to_last_activity_not_to_now(self):
        session = make_session().with_move(
            submit_move(1, correct=True, at=NOW + dt.timedelta(seconds=30))
        )
        assert session.elapsed_ms == 30_000

    def test_the_activity_clock_may_not_precede_the_last_move(self):
        with pytest.raises(ValueError, match="precedes the last move"):
            make_session(
                moves=(
                    submit_move(1, correct=True, at=NOW + dt.timedelta(seconds=30)),
                )
            )

    def test_client_timestamps_are_stored_but_never_counted(self):
        move = MoveRecord(
            sequence=1,
            kind=MoveKind.SUBMIT,
            outcome=MoveOutcome.CORRECT,
            server_at=NOW,
            client_at=NOW - dt.timedelta(hours=3),
        )
        session = make_session(moves=(move,))
        assert session.moves[0].client_at is not None
        assert session.elapsed_ms == 0

    def test_with_move_refuses_a_gap(self):
        with pytest.raises(ValueError, match="expected sequence 1"):
            make_session().with_move(submit_move(2, correct=True))

    def test_a_terminal_session_takes_no_moves(self):
        session = make_session().terminated(SessionState.COMPLETED, NOW)
        with pytest.raises(ValueError, match="takes no moves"):
            session.with_move(submit_move(1, correct=True))

    def test_a_session_ends_once(self):
        session = make_session().terminated(SessionState.ABANDONED, NOW)
        with pytest.raises(ValueError, match="already ended"):
            session.terminated(SessionState.COMPLETED, NOW)

    def test_in_progress_is_not_a_terminal_state(self):
        with pytest.raises(ValueError, match="not a terminal state"):
            make_session().terminated(SessionState.IN_PROGRESS, NOW)

    def test_the_ledger_is_capped(self):
        moves = tuple(
            submit_move(index, correct=False) for index in range(1, MAX_MOVES + 2)
        )
        with pytest.raises(ValueError, match="at most"):
            make_session(moves=moves)

    def test_an_oversized_payload_is_refused(self):
        with pytest.raises(ValueError, match="characters"):
            MoveRecord(
                sequence=1,
                kind=MoveKind.SUBMIT,
                outcome=MoveOutcome.NEUTRAL if False else MoveOutcome.CORRECT,
                server_at=NOW,
                payload={"blob": "x" * 5000},
            )

    def test_ledger_hash_ignores_timing_but_not_content(self):
        one = make_session(moves=(submit_move(1, correct=True),))
        later = make_session(
            moves=(submit_move(1, correct=True, at=NOW + dt.timedelta(minutes=9)),),
            last_activity_at=NOW + dt.timedelta(minutes=9),
        )
        other = make_session(
            moves=(
                MoveRecord(
                    sequence=1,
                    kind=MoveKind.SUBMIT,
                    outcome=MoveOutcome.CORRECT,
                    server_at=NOW,
                    payload={"choice": "different"},
                ),
            )
        )
        assert one.ledger_hash() == later.ledger_hash()
        assert one.ledger_hash() != other.ledger_hash()

    def test_only_live_sessions_are_ranked(self):
        assert make_session().ranked
        assert not make_session(kind=SessionKind.PRACTICE).ranked


class TestSessionStorage:
    def test_a_terminal_session_is_immutable(self, session_repos):
        session = make_session().terminated(SessionState.COMPLETED, NOW)
        session_repos.sessions.put(session)
        session_repos.sessions.put(session)  # identical rewrite is fine
        with pytest.raises(ConflictError, match="immutable"):
            session_repos.sessions.put(
                session.model_copy(update={"day_key": "2026-09-28"})
            )

    def test_completed_days_excludes_practice_and_unfinished(self, session_repos):
        player = ids.for_player("p")
        first = make_session(player_id=player).terminated(SessionState.COMPLETED, NOW)
        practice = make_session(
            player_id=player, kind=SessionKind.PRACTICE
        ).terminated(SessionState.COMPLETED, NOW)
        open_one = make_session(
            player_id=player,
            manifest_id=ids.for_manifest(ids.for_puzzle("2026-09-28", "g", "h")),
        )
        session_repos.sessions.put_many([first, practice, open_one])
        assert session_repos.sessions.completed_days(player, "g") == [DAY]

    def test_lookup_by_day_and_by_manifest(self, session_repos):
        session = make_session()
        session_repos.sessions.put(session)
        assert session_repos.sessions.on_day(session.player_id, DAY) == [session]
        assert session_repos.sessions.for_manifest(
            session.player_id, session.manifest_id
        ) == [session]


class TestStartingSessions:
    def test_start_opens_a_live_session_for_today(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        assert session.kind is SessionKind.LIVE
        assert session.state is SessionState.IN_PROGRESS
        assert session.expires_at > session.started_at

    def test_the_players_profile_is_copied_onto_the_session(
        self, sessions, identity, published
    ):
        minted = identity.create_player(
            accessibility=AccessibilityProfile(reduced_motion=True), locale="fr"
        )
        session = sessions.start(minted.player.id, published.id)
        assert session.accessibility.reduced_motion
        assert session.locale == "fr"

    def test_changing_the_profile_later_does_not_change_the_session(
        self, sessions, identity, published
    ):
        minted = identity.create_player()
        session = sessions.start(minted.player.id, published.id)
        identity.update_accessibility(
            minted.player.id, AccessibilityProfile(high_contrast=True)
        )
        assert not sessions.resume(session.id).accessibility.high_contrast

    def test_starting_twice_returns_the_same_session(
        self, sessions, player, published
    ):
        first = sessions.start(player.id, published.id)
        again = sessions.start(player.id, published.id)
        assert first.id == again.id

    def test_a_past_day_is_a_practice_session(
        self, sessions, engine_repos, player, clock
    ):
        manifest = build_published_day(engine_repos, day_key="2026-09-20")
        session = sessions.start(player.id, manifest.id)
        assert session.kind is SessionKind.PRACTICE
        assert session.day_key == "2026-09-20"

    def test_practice_can_be_refused(self, sessions, engine_repos, player):
        manifest = build_published_day(engine_repos, day_key="2026-09-20")
        with pytest.raises(ConflictError, match="not 2026-09-27"):
            sessions.start(player.id, manifest.id, allow_practice=False)

    def test_a_practice_window_runs_from_now(
        self, sessions, engine_repos, player, clock
    ):
        manifest = build_published_day(engine_repos, day_key="2026-09-20")
        session = sessions.start(player.id, manifest.id)
        assert session.expires_at > clock.now()

    def test_an_invalidated_puzzle_cannot_be_started(
        self, sessions, engine_repos, player, published
    ):
        from puzzlegen.engine.records import PuzzleStatus

        engine_repos.puzzles.set_status(published.puzzle_id, PuzzleStatus.INVALIDATED)
        with pytest.raises(ConflictError, match="invalidated"):
            sessions.start(player.id, published.id)

    def test_an_unknown_manifest_raises(self, sessions, player):
        with pytest.raises(NotFoundError):
            sessions.start(player.id, ids.for_manifest(ids.for_puzzle(DAY, "g", "h")))

    def test_an_unknown_player_raises(self, sessions, published):
        with pytest.raises(NotFoundError):
            sessions.start(ids.for_player("ghost"), published.id)

    def test_start_today_finds_the_published_manifest(
        self, sessions, player, published
    ):
        assert sessions.start_today(player.id, "oddoneout").manifest_id == published.id

    def test_a_game_without_the_play_methods_is_refused(
        self, sessions, registry, engine_repos, player
    ):
        class GenerateOnly:
            def describe(self):
                return GameDescriptor(
                    game_id="legacy",
                    display_name="Legacy",
                    game_version="1.0.0",
                    protocol_version="1.0.0",
                    accessibility=AccessibilityDeclaration(
                        element_kinds=("tile",),
                        state_symbols=(
                            StateSymbol(name="on", symbol="O", label="on"),
                        ),
                    ),
                )

        registry.register(GenerateOnly())
        manifest = build_published_day(engine_repos, game_id="legacy")
        with pytest.raises(ConfigurationError, match="protocol 1.1"):
            sessions.start(player.id, manifest.id)

    def test_a_game_with_an_incomplete_declaration_is_refused(
        self, sessions, registry, engine_repos, player, game
    ):
        class Undeclared:
            def describe(self):
                return GameDescriptor(
                    game_id="undeclared",
                    display_name="Undeclared",
                    game_version="1.0.0",
                    accessibility=AccessibilityDeclaration(),
                )

            grade_move = game.grade_move
            get_hint = game.get_hint

        registry.register(Undeclared())
        manifest = build_published_day(engine_repos, game_id="undeclared")
        with pytest.raises(ConfigurationError, match="cannot be played"):
            sessions.start(player.id, manifest.id)


class TestPlayingSessions:
    def test_a_wrong_move_is_recorded_and_play_continues(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        result = sessions.submit(session.id, 1, {"choice": "opt_a"})
        assert not result.judgement.correct
        assert not result.finished
        assert result.session.mistakes == 1

    def test_a_correct_move_completes_and_scores(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        result = sessions.submit(session.id, 1, {"choice": "opt_d"})
        assert result.finished
        assert result.session.state is SessionState.COMPLETED
        assert result.score is not None
        assert result.streak.current == 1

    def test_grading_never_trusts_the_client(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        result = sessions.submit(
            session.id, 1, {"choice": "opt_a", "correct": True, "points": 9999}
        )
        assert not result.judgement.correct
        assert result.score is None

    def test_an_identical_resubmission_is_idempotent(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        sessions.submit(session.id, 1, {"choice": "opt_a"})
        again = sessions.submit(session.id, 1, {"choice": "opt_a"})
        assert again.idempotent
        assert again.session.attempts == 1

    def test_a_changed_resubmission_is_a_conflict(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        sessions.submit(session.id, 1, {"choice": "opt_a"})
        with pytest.raises(ConflictError, match="different content"):
            sessions.submit(session.id, 1, {"choice": "opt_b"})

    def test_a_gap_in_the_sequence_is_refused(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        with pytest.raises(ConflictError, match="expects sequence 1"):
            sessions.submit(session.id, 7, {"choice": "opt_a"})

    def test_a_finished_session_takes_no_more_moves(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        sessions.submit(session.id, 1, {"choice": "opt_d"})
        with pytest.raises(ConflictError, match="takes no further moves"):
            sessions.submit(session.id, 2, {"choice": "opt_a"})

    def test_a_hint_occupies_a_sequence_number(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        result = sessions.request_hint(session.id, 1)
        assert result.session.hints_used == 1
        assert result.session.next_sequence == 2
        assert result.hint.reveals

    def test_a_repeated_hint_request_is_idempotent(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        sessions.request_hint(session.id, 1)
        again = sessions.request_hint(session.id, 1)
        assert again.idempotent
        assert again.session.hints_used == 1

    def test_a_hint_cannot_overwrite_a_submit(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        sessions.submit(session.id, 1, {"choice": "opt_a"})
        with pytest.raises(ConflictError, match="not a hint request"):
            sessions.request_hint(session.id, 1)

    def test_giving_up_ends_the_session_without_completing_it(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        result = sessions.give_up(session.id, 1)
        assert result.session.state is SessionState.ABANDONED
        assert result.session.gave_up
        assert result.score.points == 0
        assert result.streak.current == 0

    def test_abandoning_records_no_give_up_move(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        abandoned = sessions.abandon(session.id)
        assert abandoned.state is SessionState.ABANDONED
        assert not abandoned.gave_up

    def test_abandoning_twice_is_harmless(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        sessions.abandon(session.id)
        assert sessions.abandon(session.id).state is SessionState.ABANDONED

    def test_running_out_of_options_ends_the_session(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        sessions.submit(session.id, 1, {"choice": "opt_a"})
        sessions.submit(session.id, 2, {"choice": "opt_b"})
        result = sessions.submit(session.id, 3, {"choice": "opt_c"})
        # Every wrong option is now spent, so the game reports the puzzle over
        # rather than leaving one forced guess.
        assert result.finished
        assert result.session.state is SessionState.ABANDONED
        assert result.score.points == 0

    def test_an_off_board_choice_does_not_end_the_session(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        result = sessions.submit(session.id, 1, {"choice": "nonsense"})
        assert not result.finished
        assert "not on the board" in result.judgement.note

    def test_a_crashing_grader_fails_the_move_not_the_process(
        self, sessions, registry, engine_repos, player, game
    ):
        class Exploding:
            def describe(self):
                return game.describe().__class__(
                    game_id="exploding",
                    display_name="Exploding",
                    game_version="1.0.0",
                    accessibility=game.describe().accessibility,
                    share_tokens=game.describe().share_tokens,
                )

            def grade_move(self, puzzle, payload, state):
                raise RuntimeError("boom")

            get_hint = game.get_hint

        registry.register(Exploding())
        manifest = build_published_day(engine_repos, game_id="exploding")
        session = sessions.start(player.id, manifest.id)
        with pytest.raises(ConflictError, match="failed to grade"):
            sessions.submit(session.id, 1, {"choice": "opt_a"})

    def test_a_grader_returning_the_wrong_type_is_a_conflict(
        self, sessions, registry, engine_repos, player, game
    ):
        class WrongType:
            def describe(self):
                descriptor = game.describe()
                return descriptor.__class__(
                    game_id="wrongtype",
                    display_name="Wrong Type",
                    game_version="1.0.0",
                    accessibility=descriptor.accessibility,
                    share_tokens=descriptor.share_tokens,
                )

            def grade_move(self, puzzle, payload, state):
                return {"correct": True}

            get_hint = game.get_hint

        registry.register(WrongType())
        manifest = build_published_day(engine_repos, game_id="wrongtype")
        session = sessions.start(player.id, manifest.id)
        with pytest.raises(ConflictError, match="boundary type is MoveJudgement"):
            sessions.submit(session.id, 1, {"choice": "opt_a"})


class TestExpiry:
    def test_a_session_expires_when_its_window_closes(
        self, sessions, player, published, clock
    ):
        session = sessions.start(player.id, published.id)
        clock.advance(days=1, hours=1)
        expired = sessions.resume(session.id)
        assert expired.state is SessionState.EXPIRED
        assert expired.ended_at == session.expires_at

    def test_expiry_scores_zero_and_resets_the_streak(
        self, sessions, scoring, player, published, clock
    ):
        session = sessions.start(player.id, published.id)
        sessions.submit(session.id, 1, {"choice": "opt_a"})
        clock.advance(days=1, hours=1)
        expired = sessions.resume(session.id)
        score = scoring.player_history(player.id)[0]
        assert score.session_id == expired.id
        assert score.points == 0
        assert scoring.streak_for(player.id, "oddoneout").current == 0

    def test_expiry_is_not_applied_early(self, sessions, player, published, clock):
        session = sessions.start(player.id, published.id)
        clock.advance(hours=6)
        assert sessions.resume(session.id).state is SessionState.IN_PROGRESS

    def test_a_past_day_is_refused_when_practice_is_not_allowed(
        self, sessions, player, published, clock
    ):
        clock.advance(days=1, hours=2)
        with pytest.raises(ConflictError, match="not 2026-09-28"):
            sessions.start(player.id, published.id, allow_practice=False)

    def test_a_window_that_closed_early_is_caught(
        self,
        session_repos,
        engine_repos,
        registry,
        clock,
        scoring,
        identity,
        player,
        published,
    ):
        """A day window whose expiry precedes its own day is misconfigured.

        Unreachable with the stock windows, which is the point: the guard is
        what turns a bad policy into a refusal at start rather than a session
        that is born expired and confuses every read after it.
        """
        from puzzlegen.engine.session_service import SessionService

        class ClosedWindow(UtcDayWindow):
            def expires_at(self, day_key):
                return self.opens_at(day_key)

        service = SessionService(
            session_repos,
            engine_repos,
            registry,
            clock=clock,
            day_window=ClosedWindow(),
            scoring=scoring,
            identity=identity,
        )
        with pytest.raises(ConflictError, match="window closed"):
            service.start(player.id, published.id)

    def test_the_sweep_reports_what_it_closed(
        self, sessions, player, published, clock
    ):
        sessions.start(player.id, published.id)
        clock.advance(days=1, hours=1)
        assert sessions.expire_open_sessions() == 1
        assert sessions.expire_open_sessions() == 0


class TestReading:
    def test_presentation_shows_replayed_state_only(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        sessions.submit(session.id, 1, {"choice": "opt_a"})
        model = sessions.presentation(session.id)
        assert model.state["tried"] == ["opt_a"]
        assert "answer" not in model.state

    def test_the_public_manifest_carries_no_dependencies(
        self, sessions, player, published
    ):
        session = sessions.start(player.id, published.id)
        view = sessions.public_manifest(session.id)
        assert "fact_refs" not in view
        assert "puzzle_content_hash" not in view

    def test_sessions_on_a_day_are_listed(self, sessions, player, published):
        session = sessions.start(player.id, published.id)
        assert [s.id for s in sessions.sessions_on(player.id, DAY)] == [session.id]


class TestReplayPurity:
    def test_a_grader_that_changes_its_mind_is_caught(
        self, sessions, registry, engine_repos, player, game
    ):
        class Flaky:
            def __init__(self):
                self.calls = 0

            def describe(self):
                descriptor = game.describe()
                return descriptor.__class__(
                    game_id="flaky",
                    display_name="Flaky",
                    game_version="1.0.0",
                    accessibility=descriptor.accessibility,
                    share_tokens=descriptor.share_tokens,
                )

            def grade_move(self, puzzle, payload, state):
                self.calls += 1
                return MoveJudgement(correct=self.calls > 2, state=dict(state))

            get_hint = game.get_hint

        registry.register(Flaky())
        manifest = build_published_day(engine_repos, game_id="flaky")
        session = sessions.start(player.id, manifest.id)
        sessions.submit(session.id, 1, {"choice": "opt_a"})
        sessions.submit(session.id, 2, {"choice": "opt_b"})
        from puzzlegen.core.errors import DeterminismError

        with pytest.raises(DeterminismError, match="regraded move"):
            sessions.submit(session.id, 3, {"choice": "opt_c"})
