"""Playing the board: grading, hints, scoring, rendering, sharing.

Grading carries the one hard constraint in this file. The session layer
replays the whole ledger on every submission and raises ``DeterminismError``
if a regrade disagrees with a recorded outcome, so a grade must be a pure
function of the puzzle and the moves before it. The determinism tests here are
what stand between that and a session that fails on its second move.

The hints get a test each for the same reason the hint ladder has three rungs:
the third one hands the player every letter the board withholds, which leaves
them the anagram and nothing else, and a fourth would be the answer.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.plugin import SessionTelemetry
from puzzlegen_letters.descriptor import DESCRIPTOR, SHARE_TOKENS
from puzzlegen_letters.play import (
    MAX_MISTAKES,
    PENALTY_PER_HINT,
    PENALTY_PER_MISTAKE,
    POINTS_FIRST_GUESS,
    POINTS_FOR_ANSWER,
    answer_letters,
    blank_letters,
    create_share_artifact,
    empty_state,
    get_hint,
    grade_move,
    render,
    score,
)

from boards import fish_board

BOARD = fish_board(forced=["f"], manufactured=["i"])


def telemetry(**overrides) -> SessionTelemetry:
    base = dict(
        completed=True,
        attempts=1,
        elapsed_ms=30_000,
        mistakes=0,
        hints_used=0,
        difficulty_score=0.5,
    )
    base.update(overrides)
    return SessionTelemetry(**base)


def guess(word: str, state=None):
    return grade_move(BOARD, {"answer": word}, state or empty_state(BOARD))


class TestGrading:
    def test_the_answer_solves_the_board(self):
        judgement = guess("fish")
        assert judgement.correct and judgement.solved and judgement.complete
        assert "fish" in judgement.note

    def test_the_answer_in_any_case_is_still_the_answer(self):
        """The comparison is between letter forms, not strings: a board built
        under one set of letter rules and graded under another would mark a
        correct answer wrong."""
        assert guess("FISH").solved
        assert guess(" Fish ").solved

    def test_a_wrong_word_is_a_mistake_and_the_board_stays_open(self):
        judgement = guess("food")
        assert not judgement.correct
        assert not judgement.complete
        assert judgement.state["mistakes"] == 1

    def test_a_wrong_length_is_told_how_many_letters_there_are(self):
        """Which the board already shows, so saying it costs nothing."""
        assert "4 letters" in guess("salmon").note

    def test_a_word_that_ignores_the_marks_is_told_so(self):
        assert "every letter the board marks" in guess("oboe").note

    def test_the_answer_is_not_revealed_while_guesses_remain(self):
        judgement = guess("food")
        assert judgement.state["revealed"] == []
        assert "fish" not in judgement.note

    def test_running_out_of_guesses_closes_the_board_and_reveals_it(self):
        state = empty_state(BOARD)
        for _ in range(MAX_MISTAKES):
            state = guess("food", state).state
        assert state["complete"] and not state["solved"]
        assert state["revealed"] == ["fish"]

    def test_a_move_after_the_end_solves_nothing(self):
        state = guess("fish").state
        judgement = grade_move(BOARD, {"answer": "fish"}, state)
        assert judgement.complete and not judgement.solved and not judgement.correct

    def test_a_submission_with_no_answer_is_a_protocol_error(self):
        with pytest.raises(ValueError):
            grade_move(BOARD, {}, empty_state(BOARD))


class TestGradingIsDeterministic:
    def test_the_same_move_on_the_same_state_grades_the_same(self):
        state = empty_state(BOARD)
        first = grade_move(BOARD, {"answer": "food"}, state)
        second = grade_move(BOARD, {"answer": "food"}, state)
        assert first == second

    def test_replaying_a_ledger_reaches_the_same_state_twice(self):
        def replay():
            state = empty_state(BOARD)
            for word in ("food", "fist", "fish"):
                state = grade_move(BOARD, {"answer": word}, state).state
            return state

        assert replay() == replay()

    def test_it_reads_nothing_outside_its_arguments(self, monkeypatch):
        monkeypatch.setenv("PUZZLEGEN_SALT", "elsewhere")
        first = guess("food")
        monkeypatch.delenv("PUZZLEGEN_SALT")
        assert guess("food") == first


class TestHints:
    def test_the_first_hint_names_a_letter_the_board_withholds(self):
        hint = get_hint(BOARD, empty_state(BOARD), 0)
        assert any(letter in hint.text for letter in blank_letters(BOARD))
        assert not hint.exhausted

    def test_the_second_hint_names_the_letter_it_starts_with(self):
        hint = get_hint(BOARD, empty_state(BOARD), 1)
        assert answer_letters(BOARD)[0] in hint.text

    def test_the_third_hint_hands_over_every_withheld_letter_and_stops(self):
        """Which leaves the player the anagram and nothing else, so it is a
        real last hint rather than a reveal."""
        hint = get_hint(BOARD, empty_state(BOARD), 2)
        assert all(letter in hint.text for letter in blank_letters(BOARD))
        assert hint.exhausted

    def test_no_hint_is_the_answer(self):
        for used in range(4):
            hint = get_hint(BOARD, empty_state(BOARD), used)
            assert "fish" not in hint.text.lower().replace(", ", "").replace("'", "")

    def test_every_hint_costs_something(self):
        for used in range(3):
            assert get_hint(BOARD, empty_state(BOARD), used).cost > 0

    def test_a_finished_board_has_nothing_left_to_hint_at(self):
        state = guess("fish").state
        assert get_hint(BOARD, state, 0).exhausted


class TestScoring:
    def test_answering_scores(self):
        assert score(BOARD, telemetry()).points >= POINTS_FOR_ANSWER

    def test_the_first_guess_is_worth_more_than_the_third(self):
        clean = score(BOARD, telemetry())
        messy = score(BOARD, telemetry(attempts=3, mistakes=2))
        assert clean.points > messy.points
        assert clean.breakdown["first_guess"] == POINTS_FIRST_GUESS

    def test_mistakes_and_hints_are_subtracted(self):
        result = score(BOARD, telemetry(attempts=3, mistakes=2, hints_used=1))
        assert result.breakdown["mistakes"] == -2 * PENALTY_PER_MISTAKE
        assert result.breakdown["hints"] == -PENALTY_PER_HINT

    def test_a_score_never_goes_below_zero(self):
        assert score(BOARD, telemetry(completed=False, mistakes=5, hints_used=3)).points == 0

    def test_an_unanswered_board_earns_nothing_for_the_answer(self):
        result = score(BOARD, telemetry(completed=False, attempts=5, mistakes=5))
        assert result.breakdown["answer"] == 0
        assert result.breakdown["difficulty"] == 0

    def test_difficulty_is_read_from_the_engine_s_own_telemetry(self):
        """A game cannot invent a flattering signal."""
        low = score(BOARD, telemetry(difficulty_score=0.0)).points
        high = score(BOARD, telemetry(difficulty_score=1.0)).points
        assert high > low


class TestRendering:
    def test_every_tile_is_an_element_with_its_mark(self):
        model = render(BOARD, empty_state(BOARD), "en")
        tiles = [e for e in model.elements if e["kind"] == "tile"]
        assert len(tiles) == BOARD.payload["board_size"]
        marked = [e for e in tiles if e["state"] == "marked"]
        assert len(marked) == BOARD.payload["marked_tiles"]

    def test_the_blanks_are_slots_the_client_can_draw(self):
        model = render(BOARD, empty_state(BOARD), "en")
        slots = [e for e in model.elements if e["kind"] == "slot"]
        assert len(slots) == BOARD.payload["blanks"]

    def test_the_element_kinds_are_the_ones_declared(self):
        model = render(BOARD, empty_state(BOARD), "en")
        kinds = {e["kind"] for e in model.elements}
        assert kinds <= set(DESCRIPTOR.accessibility.element_kinds)

    def test_it_never_names_the_answer_before_the_board_closes(self):
        model = render(BOARD, empty_state(BOARD), "en")
        assert "answer" not in model.labels
        assert "fish" not in str(model.labels)

    def test_it_names_the_answer_once_the_board_has_closed(self):
        state = guess("fish").state
        model = render(BOARD, state, "en")
        assert model.labels["answer"] == "fish"

    def test_the_state_says_how_many_guesses_are_left(self):
        state = guess("food").state
        model = render(BOARD, state, "en")
        assert model.state["guesses_left"] == MAX_MISTAKES - 1


class TestSharing:
    def events(self, *kinds):
        return tuple(
            {"kind": kind, "outcome": outcome} for kind, outcome in kinds
        )

    def test_one_row_per_submission_in_the_order_they_were_made(self):
        artifact = create_share_artifact(
            BOARD,
            telemetry(
                attempts=3,
                mistakes=2,
                events=self.events(
                    ("SUBMIT", "INCORRECT"), ("SUBMIT", "INCORRECT"), ("SUBMIT", "CORRECT")
                ),
            ),
            "2026-10-01",
        )
        assert artifact.tokens == (("miss",), ("miss",), ("solved",))

    def test_a_hint_is_its_own_row(self):
        artifact = create_share_artifact(
            BOARD,
            telemetry(attempts=1, hints_used=1, events=self.events(("HINT", ""), ("SUBMIT", "CORRECT"))),
            "2026-10-01",
        )
        assert artifact.tokens == (("hint",), ("solved",))

    def test_every_token_is_in_the_declared_vocabulary(self):
        """A token outside it is a protocol error, not a content rejection."""
        declared = {token.name for token in SHARE_TOKENS}
        artifact = create_share_artifact(
            BOARD,
            telemetry(attempts=2, mistakes=1, events=self.events(("SUBMIT", "INCORRECT"), ("SUBMIT", "CORRECT"))),
            "2026-10-01",
        )
        assert {name for row in artifact.tokens for name in row} <= declared

    def test_it_says_nothing_about_the_answer(self):
        artifact = create_share_artifact(
            BOARD,
            telemetry(attempts=2, mistakes=1, events=self.events(("SUBMIT", "INCORRECT"), ("SUBMIT", "CORRECT"))),
            "2026-10-01",
        )
        assert "fish" not in str(artifact.headline) + str(artifact.detail)

    def test_an_unsolved_board_says_so(self):
        artifact = create_share_artifact(
            BOARD, telemetry(completed=False, attempts=5, mistakes=5), "2026-10-01"
        )
        assert artifact.outcome == "unsolved"
        assert len(artifact.tokens) == 5
