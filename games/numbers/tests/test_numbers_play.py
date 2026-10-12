"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` must be a pure function of the puzzle and the moves before it,
because the session layer replays the whole ledger on every submission and
raises if a regrade disagrees. Beyond that, the thing worth testing hardest in
this game is what is *not* said: the property is the answer in words, so no
note, no render and no share may name it before the board has closed.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.plugin import SessionTelemetry
from puzzlegen_numbers.descriptor import SHARE_TOKENS
from puzzlegen_numbers.play import (
    DIFFICULTY_BONUS,
    MAX_MISTAKES,
    PENALTY_PER_HINT,
    PENALTY_PER_MISTAKE,
    POINTS_FIRST_TRY,
    POINTS_FOR_ANSWER,
    answer_of,
    create_share_artifact,
    empty_state,
    get_hint,
    grade_move,
    render,
    score,
)

from boards import make_puzzle


@pytest.fixture
def puzzle():
    return make_puzzle()


def wrong_tile(puzzle) -> str:
    return next(
        tile["id"]
        for tile in puzzle.payload["tiles"]
        if tile["id"] != answer_of(puzzle)
    )


def telemetry(**kwargs) -> SessionTelemetry:
    defaults = dict(
        completed=True, attempts=1, elapsed_ms=10_000, mistakes=0, hints_used=0
    )
    return SessionTelemetry(**{**defaults, **kwargs})


class TestGrading:
    def test_the_odd_one_out_is_correct_and_finishes_the_puzzle(self, puzzle):
        judgement = grade_move(
            puzzle, {"tile": answer_of(puzzle)}, empty_state(puzzle)
        )
        assert judgement.correct and judgement.complete and judgement.solved

    def test_the_correct_note_explains_the_board(self, puzzle):
        """Once the board is won, saying what the others shared is the whole
        of the explanation a player is owed."""
        judgement = grade_move(
            puzzle, {"tile": answer_of(puzzle)}, empty_state(puzzle)
        )
        assert puzzle.solution["property"] in judgement.note

    def test_a_wrong_tile_costs_a_mistake_and_does_not_finish(self, puzzle):
        judgement = grade_move(
            puzzle, {"tile": wrong_tile(puzzle)}, empty_state(puzzle)
        )
        assert not judgement.correct and not judgement.complete
        assert judgement.state["mistakes"] == 1

    def test_the_board_closes_after_the_last_mistake(self, puzzle):
        state = empty_state(puzzle)
        for _ in range(MAX_MISTAKES):
            state = grade_move(puzzle, {"tile": wrong_tile(puzzle)}, state).state
        assert state["complete"] and not state["solved"]

    def test_the_answer_is_revealed_only_once_the_board_closes(self, puzzle):
        state = empty_state(puzzle)
        for turn in range(MAX_MISTAKES):
            state = grade_move(puzzle, {"tile": wrong_tile(puzzle)}, state).state
            if turn < MAX_MISTAKES - 1:
                assert state["revealed"] == []
        assert state["revealed"] == [answer_of(puzzle)]

    def test_a_move_after_the_end_solves_nothing(self, puzzle):
        closed = {**empty_state(puzzle), "complete": True}
        judgement = grade_move(puzzle, {"tile": answer_of(puzzle)}, closed)
        assert judgement.complete and not judgement.solved

    def test_grading_is_a_pure_function_of_the_puzzle_and_the_state(self, puzzle):
        state = empty_state(puzzle)
        payload = {"tile": wrong_tile(puzzle)}
        assert grade_move(puzzle, payload, state) == grade_move(puzzle, payload, state)

    def test_no_miss_note_names_the_property(self, puzzle):
        """Naming it would be the answer in words: the player could then
        find the one number that lacks it."""
        state = empty_state(puzzle)
        for _ in range(MAX_MISTAKES - 1):
            judgement = grade_move(puzzle, {"tile": wrong_tile(puzzle)}, state)
            state = judgement.state
            assert puzzle.solution["property"] not in judgement.note

    def test_a_miss_note_names_a_near_reading_when_there_is_one(self):
        """The most likely reason a player chose a wrong tile, and safe
        because a property with two outsiders is not the answer."""
        puzzle = make_puzzle([12, 34, 51, 53, 76], answer=51)
        judgement = grade_move(puzzle, {"tile": "entity:53"}, empty_state(puzzle))
        assert "even number" in judgement.note

    def test_the_last_note_gives_the_number_rather_than_the_tile_id(self, puzzle):
        state = empty_state(puzzle)
        for _ in range(MAX_MISTAKES):
            judgement = grade_move(puzzle, {"tile": wrong_tile(puzzle)}, state)
            state = judgement.state
        values = {t["id"]: t["value"] for t in puzzle.payload["tiles"]}
        assert str(values[answer_of(puzzle)]) in judgement.note

    @pytest.mark.parametrize("payload", [{}, {"tile": ""}, {"tile": 12}])
    def test_a_malformed_submission_raises_rather_than_costing_a_mistake(
        self, puzzle, payload
    ):
        with pytest.raises(ValueError):
            grade_move(puzzle, payload, empty_state(puzzle))

    def test_a_submission_naming_a_tile_off_the_board_raises(self, puzzle):
        with pytest.raises(ValueError, match="not on the board"):
            grade_move(puzzle, {"tile": "entity:1000"}, empty_state(puzzle))


class TestHints:
    def test_the_first_hint_restates_the_rule(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 0)
        assert str(puzzle.payload["board_size"] - 1) in hint.text

    def test_the_second_hint_names_the_kind_without_the_property(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 1)
        assert puzzle.solution["property"] not in hint.text
        assert hint.text

    def test_the_third_hint_names_the_property_and_is_the_last(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 2)
        assert puzzle.solution["property"] in hint.text
        assert hint.exhausted

    def test_no_hint_names_the_answer(self, puzzle):
        """Even the last one leaves the player applying the property to every
        tile to find the one it misses."""
        values = {t["id"]: t["value"] for t in puzzle.payload["tiles"]}
        answer = str(values[answer_of(puzzle)])
        for used in range(6):
            hint = get_hint(puzzle, empty_state(puzzle), used)
            assert answer not in hint.text
            assert answer_of(puzzle) not in hint.reveals

    def test_a_finished_puzzle_offers_nothing(self, puzzle):
        hint = get_hint(puzzle, {**empty_state(puzzle), "complete": True}, 0)
        assert hint.exhausted and hint.cost == 0

    @pytest.mark.parametrize(
        "name,kind",
        [
            ("even number", "divisibility"),
            ("multiple of three", "divisibility"),
            ("prime number", "factors"),
            ("Fibonacci number", "sequence"),
        ],
    )
    def test_the_kind_groups_properties_coarsely(self, name, kind):
        """Coarse on purpose: the hint narrows the search without naming the
        property."""
        puzzle = make_puzzle(property_name=name)
        assert kind in get_hint(puzzle, empty_state(puzzle), 1).text

    def test_an_unrated_property_still_gets_a_second_hint(self):
        puzzle = make_puzzle(property_name="numbers nobody has classified")
        assert get_hint(puzzle, empty_state(puzzle), 1).text


class TestScoring:
    def test_finding_it_on_the_first_try_pays_both_awards(self, puzzle):
        points = score(puzzle, telemetry(difficulty_score=0.0)).points
        assert points == POINTS_FOR_ANSWER + POINTS_FIRST_TRY

    def test_difficulty_adds_up_to_its_bonus(self, puzzle):
        points = score(puzzle, telemetry(difficulty_score=1.0)).points
        assert points == POINTS_FOR_ANSWER + POINTS_FIRST_TRY + DIFFICULTY_BONUS

    def test_mistakes_and_hints_are_charged(self, puzzle):
        result = score(
            puzzle,
            telemetry(attempts=2, mistakes=1, hints_used=1, difficulty_score=0.0),
        )
        assert result.points == max(
            0, POINTS_FOR_ANSWER - PENALTY_PER_MISTAKE - PENALTY_PER_HINT
        )

    def test_an_unfinished_board_scores_nothing_positive(self, puzzle):
        assert score(
            puzzle, telemetry(completed=False, attempts=3, mistakes=3)
        ).points == 0

    def test_the_breakdown_explains_the_total(self, puzzle):
        result = score(puzzle, telemetry(mistakes=1, difficulty_score=0.5))
        assert result.points == max(0, sum(result.breakdown.values()))


class TestRendering:
    def test_every_tile_is_an_element_with_its_glyph_and_value(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert len(model.elements) == len(puzzle.payload["tiles"])
        for element in model.elements:
            assert element["glyph"] and element["label"]
            assert isinstance(element["value"], int)

    def test_the_board_renders_as_a_row_in_ascending_order(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert model.layout == "row"
        values = [element["value"] for element in model.elements]
        assert values == sorted(values)

    def test_the_render_names_neither_the_answer_nor_the_property(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        text = repr(model)
        assert puzzle.solution["property"] not in text
        assert "answer" not in model.labels

    def test_both_appear_only_once_the_board_has_closed(self, puzzle):
        state = empty_state(puzzle)
        for _ in range(MAX_MISTAKES):
            state = grade_move(puzzle, {"tile": wrong_tile(puzzle)}, state).state
        model = render(puzzle, state, "en")
        assert model.labels["property"] == puzzle.solution["property"]
        assert model.labels["answer"]

    def test_chosen_tiles_are_marked(self, puzzle):
        judgement = grade_move(
            puzzle, {"tile": wrong_tile(puzzle)}, empty_state(puzzle)
        )
        model = render(puzzle, judgement.state, "en")
        chosen = {e["id"] for e in model.elements if e["state"] == "chosen"}
        assert chosen == {wrong_tile(puzzle)}

    def test_the_state_reports_the_tries_left(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert model.state["tries_left"] == MAX_MISTAKES


class TestSharing:
    def test_one_row_per_submission_in_order(self, puzzle):
        events = (
            {"kind": "SUBMIT", "outcome": "INCORRECT"},
            {"kind": "HINT"},
            {"kind": "SUBMIT", "outcome": "CORRECT"},
        )
        artifact = create_share_artifact(
            puzzle,
            telemetry(attempts=2, mistakes=1, hints_used=1, events=events),
            "2026-10-01",
        )
        assert artifact.tokens == (("miss",), ("hint",), ("solved",))

    def test_every_token_is_in_the_declared_vocabulary(self, puzzle):
        declared = {token.name for token in SHARE_TOKENS}
        events = ({"kind": "SUBMIT", "outcome": "CORRECT"},)
        artifact = create_share_artifact(
            puzzle, telemetry(events=events), "2026-10-01"
        )
        for row in artifact.tokens:
            assert set(row) <= declared

    def test_the_share_says_nothing_about_the_numbers_or_the_property(self, puzzle):
        """Naming the property would give the answer to a board the reader
        has not played."""
        artifact = create_share_artifact(
            puzzle, telemetry(attempts=2, mistakes=1), "2026-10-01"
        )
        text = repr(artifact)
        assert puzzle.solution["property"] not in text
        assert answer_of(puzzle) not in text

    def test_an_unsolved_board_says_so(self, puzzle):
        artifact = create_share_artifact(
            puzzle, telemetry(completed=False, attempts=3, mistakes=3), "2026-10-01"
        )
        assert artifact.outcome == "unsolved"

    def test_a_board_with_no_events_still_renders_its_attempts(self, puzzle):
        artifact = create_share_artifact(puzzle, telemetry(attempts=2), "2026-10-01")
        assert len(artifact.tokens) == 2
