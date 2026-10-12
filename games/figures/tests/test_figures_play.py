"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` carries the hard constraint: it must be a pure function of the
puzzle and the moves before it, because the session layer replays the whole
ledger on every submission and raises if a regrade disagrees. Most of what
follows is that purity and the one decision inside it -- that the order a
player clicks three tiles in is not part of the answer.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.plugin import SessionTelemetry
from puzzlegen_figures.descriptor import DESCRIPTOR, SHARE_TOKENS, TRIO
from puzzlegen_figures.play import (
    DIFFICULTY_BONUS,
    MAX_MISTAKES,
    PENALTY_PER_HINT,
    PENALTY_PER_MISTAKE,
    POINTS_FIRST_TRY,
    POINTS_FOR_TRIO,
    answer_of,
    create_share_artifact,
    empty_state,
    get_hint,
    grade_move,
    render,
    score,
)
from puzzlegen_figures.trio import ATTRIBUTES

from boards import make_puzzle


@pytest.fixture
def puzzle():
    return make_puzzle()


def other_tiles(puzzle, count=TRIO):
    trio = set(puzzle.solution["trio"])
    return [tile["id"] for tile in puzzle.payload["tiles"] if tile["id"] not in trio][
        :count
    ]


def telemetry(**kwargs) -> SessionTelemetry:
    defaults = dict(
        completed=True, attempts=1, elapsed_ms=10_000, mistakes=0, hints_used=0
    )
    return SessionTelemetry(**{**defaults, **kwargs})


class TestGrading:
    def test_the_trio_is_correct_and_finishes_the_puzzle(self, puzzle):
        judgement = grade_move(
            puzzle, {"tiles": puzzle.solution["trio"]}, empty_state(puzzle)
        )
        assert judgement.correct and judgement.complete and judgement.solved

    def test_the_order_the_tiles_are_named_in_does_not_matter(self, puzzle):
        """A trio is three tiles. Comparing sequences would mark the same
        three right or wrong by which one the player noticed first."""
        reversed_trio = list(reversed(puzzle.solution["trio"]))
        judgement = grade_move(
            puzzle, {"tiles": reversed_trio}, empty_state(puzzle)
        )
        assert judgement.solved

    def test_a_wrong_trio_costs_a_mistake_and_does_not_finish(self, puzzle):
        judgement = grade_move(
            puzzle, {"tiles": other_tiles(puzzle)}, empty_state(puzzle)
        )
        assert not judgement.correct
        assert not judgement.complete
        assert judgement.state["mistakes"] == 1

    def test_the_board_closes_after_the_last_mistake(self, puzzle):
        state = empty_state(puzzle)
        wrong = other_tiles(puzzle)
        for _ in range(MAX_MISTAKES):
            state = grade_move(puzzle, {"tiles": wrong}, state).state
        assert state["complete"] and not state["solved"]

    def test_the_answer_is_revealed_only_once_the_board_closes(self, puzzle):
        state = empty_state(puzzle)
        wrong = other_tiles(puzzle)
        for turn in range(MAX_MISTAKES):
            judgement = grade_move(puzzle, {"tiles": wrong}, state)
            state = judgement.state
            if turn < MAX_MISTAKES - 1:
                assert state["revealed"] == []
        assert state["revealed"] == [list(answer_of(puzzle))]

    def test_a_move_after_the_end_solves_nothing(self, puzzle):
        closed = {**empty_state(puzzle), "complete": True}
        judgement = grade_move(puzzle, {"tiles": puzzle.solution["trio"]}, closed)
        assert judgement.complete and not judgement.solved and not judgement.correct

    def test_grading_is_a_pure_function_of_the_puzzle_and_the_state(self, puzzle):
        """The session layer replays the ledger on every submission and
        raises if a regrade disagrees, so this is a hard requirement."""
        state = empty_state(puzzle)
        payload = {"tiles": other_tiles(puzzle)}
        first = grade_move(puzzle, payload, state)
        second = grade_move(puzzle, payload, state)
        assert first == second

    def test_the_note_names_the_attribute_a_wrong_trio_fails_on(self, puzzle):
        judgement = grade_move(
            puzzle, {"tiles": other_tiles(puzzle)}, empty_state(puzzle)
        )
        assert any(attribute in judgement.note for attribute in ATTRIBUTES)

    def test_no_note_names_a_tile_of_the_answer(self, puzzle):
        """A note must say something true about the submission without
        narrowing the answer."""
        state = empty_state(puzzle)
        for _ in range(MAX_MISTAKES - 1):
            judgement = grade_move(
                puzzle, {"tiles": other_tiles(puzzle)}, state
            )
            state = judgement.state
            for tile_id in puzzle.solution["trio"]:
                assert tile_id not in judgement.note

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"tiles": "not-a-list"},
            {"tiles": []},
            {"tiles": ["entity:a", "entity:b"]},
        ],
    )
    def test_a_malformed_submission_raises_rather_than_costing_a_mistake(
        self, puzzle, payload
    ):
        """Charging the player a mistake for a client's bug is the wrong
        failure, so a malformed move is an error rather than a wrong answer."""
        with pytest.raises(ValueError):
            grade_move(puzzle, payload, empty_state(puzzle))

    def test_a_submission_naming_a_tile_off_the_board_raises(self, puzzle):
        trio = puzzle.solution["trio"]
        with pytest.raises(ValueError, match="not on the board"):
            grade_move(
                puzzle, {"tiles": [trio[0], trio[1], "entity:nope"]},
                empty_state(puzzle),
            )

    def test_a_submission_naming_one_tile_twice_raises(self, puzzle):
        trio = puzzle.solution["trio"]
        with pytest.raises(ValueError, match="distinct tiles"):
            grade_move(
                puzzle, {"tiles": [trio[0], trio[0], trio[1]]}, empty_state(puzzle)
            )


class TestHints:
    def test_the_first_hint_names_an_attribute_rather_than_a_tile(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 0)
        assert any(attribute in hint.text for attribute in ATTRIBUTES)
        assert hint.reveals == ()

    def test_the_second_hint_names_one_tile(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 1)
        assert len(hint.reveals) == 1
        assert hint.reveals[0] in puzzle.solution["trio"]

    def test_the_third_hint_names_two_and_is_the_last(self, puzzle):
        hint = get_hint(puzzle, empty_state(puzzle), 2)
        assert len(hint.reveals) == 2
        assert set(hint.reveals) <= set(puzzle.solution["trio"])
        assert hint.exhausted

    def test_no_hint_names_all_three(self, puzzle):
        """A fourth hint would be the answer, so there is no fourth."""
        for used in range(6):
            hint = get_hint(puzzle, empty_state(puzzle), used)
            assert len(hint.reveals) < TRIO

    def test_a_finished_puzzle_offers_nothing(self, puzzle):
        hint = get_hint(puzzle, {**empty_state(puzzle), "complete": True}, 0)
        assert hint.exhausted and hint.cost == 0

    def test_a_trio_varying_on_nothing_still_gets_a_first_hint(self):
        """Unreachable on a real board and cheap to be right about."""
        puzzle = make_puzzle(varying=())
        hint = get_hint(puzzle, empty_state(puzzle), 0)
        assert any(attribute in hint.text for attribute in ATTRIBUTES)


class TestScoring:
    def test_finding_it_on_the_first_try_pays_both_awards(self, puzzle):
        points = score(puzzle, telemetry(difficulty_score=0.0)).points
        assert points == POINTS_FOR_TRIO + POINTS_FIRST_TRY

    def test_difficulty_adds_up_to_its_bonus(self, puzzle):
        points = score(puzzle, telemetry(difficulty_score=1.0)).points
        assert points == POINTS_FOR_TRIO + POINTS_FIRST_TRY + DIFFICULTY_BONUS

    def test_mistakes_and_hints_are_charged(self, puzzle):
        result = score(
            puzzle,
            telemetry(attempts=3, mistakes=2, hints_used=1, difficulty_score=0.0),
        )
        assert result.points == max(
            0, POINTS_FOR_TRIO - 2 * PENALTY_PER_MISTAKE - PENALTY_PER_HINT
        )

    def test_an_unfinished_board_scores_nothing_positive(self, puzzle):
        result = score(puzzle, telemetry(completed=False, attempts=5, mistakes=5))
        assert result.points == 0

    def test_the_score_never_goes_negative(self, puzzle):
        result = score(
            puzzle, telemetry(completed=False, attempts=9, mistakes=9, hints_used=3)
        )
        assert result.points == 0

    def test_the_breakdown_explains_the_total(self, puzzle):
        result = score(puzzle, telemetry(mistakes=1, difficulty_score=0.5))
        assert result.points == max(0, sum(result.breakdown.values()))


class TestRendering:
    def test_every_tile_is_an_element_with_its_glyph_and_label(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert len(model.elements) == len(puzzle.payload["tiles"])
        for element in model.elements:
            assert element["glyph"] and element["label"]

    def test_every_tile_carries_its_colour_as_a_word(self, puzzle):
        """The board's claim to being operable without colour vision."""
        model = render(puzzle, empty_state(puzzle), "en")
        assert all(element["colour"] for element in model.elements)

    def test_every_attribute_reaches_the_client_by_name(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        for element in model.elements:
            assert all(attribute in element for attribute in ATTRIBUTES)

    def test_the_render_never_names_the_trio_before_the_end(self, puzzle):
        model = render(puzzle, empty_state(puzzle), "en")
        assert "trio" not in model.labels

    def test_the_trio_appears_only_through_the_state(self, puzzle):
        state = empty_state(puzzle)
        wrong = other_tiles(puzzle)
        for _ in range(MAX_MISTAKES):
            state = grade_move(puzzle, {"tiles": wrong}, state).state
        model = render(puzzle, state, "en")
        assert "trio" in model.labels

    def test_chosen_tiles_are_marked(self, puzzle):
        judgement = grade_move(
            puzzle, {"tiles": other_tiles(puzzle)}, empty_state(puzzle)
        )
        model = render(puzzle, judgement.state, "en")
        chosen = {e["id"] for e in model.elements if e["state"] == "chosen"}
        assert chosen == set(other_tiles(puzzle))

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
            puzzle, telemetry(attempts=2, mistakes=1, hints_used=1, events=events),
            "2026-10-01",
        )
        assert artifact.tokens == (("miss",), ("hint",), ("solved",))

    def test_every_token_is_in_the_declared_vocabulary(self, puzzle):
        """A token outside it is a protocol error, which is what lets the
        engine hold a codepoint allowlist for share text."""
        declared = {token.name for token in SHARE_TOKENS}
        events = (
            {"kind": "SUBMIT", "outcome": "INCORRECT"},
            {"kind": "HINT"},
            {"kind": "SUBMIT", "outcome": "CORRECT"},
        )
        artifact = create_share_artifact(
            puzzle, telemetry(events=events, attempts=2), "2026-10-01"
        )
        for row in artifact.tokens:
            assert set(row) <= declared

    def test_the_share_says_nothing_about_which_tiles_were_chosen(self, puzzle):
        """Built from a ledger with payloads stripped, so it cannot."""
        artifact = create_share_artifact(
            puzzle, telemetry(attempts=2, mistakes=1), "2026-10-01"
        )
        text = repr(artifact)
        for tile_id in puzzle.solution["trio"]:
            assert tile_id not in text

    def test_an_unsolved_board_says_so(self, puzzle):
        artifact = create_share_artifact(
            puzzle, telemetry(completed=False, attempts=5, mistakes=5), "2026-10-01"
        )
        assert artifact.outcome == "unsolved"

    def test_a_board_with_no_events_still_renders_its_attempts(self, puzzle):
        artifact = create_share_artifact(puzzle, telemetry(attempts=3), "2026-10-01")
        assert len(artifact.tokens) == 3
