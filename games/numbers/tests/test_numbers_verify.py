"""Counting the tiles that could be the odd one out.

The distinction this verifier exists to get right is between a board with two
reasons and a board with two answers. The first is one answer argued twice and
is easier; the second is two players giving two tiles and one of them being
marked wrong. Most of what follows is that distinction, at the level of the
count the engine judges.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.uniqueness import consistency_problem
from puzzlegen_numbers.descriptor import DESCRIPTOR, SEARCH_BOUND
from puzzlegen_numbers.verify import (
    NEAR_READINGS,
    OBSCURITY,
    READINGS_FOUND,
    REASONS,
    SPREAD,
    VOCABULARY_SIZE,
    board_problem,
    near_miss_for,
    readings,
    verify,
    why_not,
)

from boards import make_puzzle


class TestAGoodBoard:
    def test_one_reading_is_found_and_reported_as_one(self):
        result = verify(make_puzzle())
        assert result.solvable
        assert result.solution_count == 1
        assert result.completeness is VerificationCompleteness.COMPLETE
        assert result.detail == ""

    def test_the_states_examined_are_tiles_against_properties(self):
        puzzle = make_puzzle()
        result = verify(puzzle)
        vocabulary = len(puzzle.solution["vocabulary"])
        assert result.states_examined == vocabulary * puzzle.payload["board_size"]

    def test_the_scan_stays_inside_the_declared_bound(self):
        assert verify(make_puzzle()).states_examined <= DESCRIPTOR.search_bound

    def test_the_reported_solution_is_the_puzzles_own_object(self):
        """The engine hashes the puzzle's solution and looks for that hash
        among the verifier's, so the intended answer has to come back in
        exactly the intended shape."""
        puzzle = make_puzzle()
        assert verify(puzzle).solutions == (dict(puzzle.solution),)

    def test_the_metrics_the_difficulty_model_reads_are_present(self):
        result = verify(make_puzzle())
        for metric in (
            READINGS_FOUND, REASONS, VOCABULARY_SIZE, NEAR_READINGS, OBSCURITY, SPREAD
        ):
            assert metric in result.metrics

    def test_the_result_is_internally_consistent(self):
        assert consistency_problem(verify(make_puzzle())) is None

    def test_the_vocabulary_is_rebuilt_from_the_board(self):
        """The recorded vocabulary is what generation scanned; this is what
        the board offers. A board whose tiles changed between the two would
        otherwise be checked against the wrong list."""
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "vocabulary": []},
            fact_refs=puzzle.fact_refs,
        )
        assert verify(tampered).metrics[VOCABULARY_SIZE] > 0


class TestReasonsAreNotAnswers:
    def test_a_board_whose_answer_has_two_reasons_still_has_one_solution(self):
        """15, 30, 45 and 60 are multiples of three and of five; 16 is
        neither. A verifier counting property-and-tile pairs would report two
        solutions and have this board refused for being too generous.
        """
        puzzle = make_puzzle([15, 16, 30, 45, 60])
        result = verify(puzzle)
        assert result.solution_count == 1
        assert len(puzzle.solution["reasons"]) >= 2
        assert result.metrics[REASONS] >= 2

    def test_a_board_with_two_readable_tiles_reports_two(self):
        """The ambiguity that matters, from the searched example in
        test_numbers_properties: 10 is the only one not odd and 11 is the
        only one not triangular."""
        result = verify(make_puzzle([10, 11, 15, 21, 45], answer=10))
        assert result.solution_count == 2
        assert "could be the odd one out" in result.detail


class TestABrokenBoard:
    def test_a_board_where_nothing_stands_out_is_unsolvable(self):
        result = verify(make_puzzle([12, 14, 16, 18], answer=12))
        assert not result.solvable
        assert result.solution_count == 0
        assert "no tile" in result.detail

    def test_a_board_whose_stated_answer_is_not_the_reading(self):
        """Verifies, is unique, and marks the player wrong: the most
        dangerous way for a board to be broken."""
        puzzle = make_puzzle([12, 34, 51, 76, 98], answer=12)
        result = verify(puzzle)
        assert result.solution_count == 1
        assert "not among the readings" in result.detail

    def test_a_board_out_of_order_is_refused(self):
        puzzle = make_puzzle()
        reversed_tiles = list(reversed(puzzle.payload["tiles"]))
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "tiles": reversed_tiles},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert "ascending order" in board_problem(tampered)

    def test_a_board_whose_count_disagrees_with_its_tiles_is_refused(self):
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "board_size": 99},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert "shows" in board_problem(tampered)

    def test_a_board_too_small_to_have_a_majority_is_refused(self):
        puzzle = make_puzzle([12, 13])
        assert "majority" in board_problem(puzzle)

    def test_an_answer_not_on_the_board_is_refused(self):
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "answer": "entity:1000"},
            fact_refs=puzzle.fact_refs,
        )
        assert "not a tile on the board" in board_problem(tampered)

    def test_a_board_with_a_problem_verifies_as_unsolvable_and_complete(self):
        """COMPLETE rather than incomplete: nothing was left unexamined, the
        board was unjudgeable."""
        puzzle = make_puzzle([12, 13])
        result = verify(puzzle)
        assert not result.solvable
        assert result.completeness is VerificationCompleteness.COMPLETE

    def test_a_good_board_has_no_problem(self):
        assert board_problem(make_puzzle()) is None


class TestTheBound:
    def test_a_board_too_large_to_scan_reports_an_unfinished_search(self):
        """Not a truncated enumeration: it is not started. The honest report
        is refused publication by the uniqueness gate, which is correct."""
        result = verify(make_puzzle(), reading_budget=3)
        assert result.completeness is VerificationCompleteness.SOUND_INCOMPLETE
        assert result.states_examined == 0
        assert not result.solvable
        assert "beyond the 3" in result.detail

    def test_the_declared_bound_covers_the_largest_board(self):
        from puzzlegen_numbers.descriptor import BOARD_SIZES

        # Nine rateable properties plus the root and the digit count is a
        # generous ceiling on the vocabulary a board can offer.
        assert max(BOARD_SIZES) * 12 < SEARCH_BOUND


class TestTheDiagnostics:
    def test_why_not_names_what_a_wrong_tile_shares_with_nearly_all(self):
        """A board with one reading guarantees no property only this tile
        lacks, so the honest thing to say is the reverse."""
        puzzle = make_puzzle()
        wrong = next(
            tile for tile in puzzle.payload["tiles"]
            if tile["id"] != puzzle.solution["answer"]
        )
        shared = why_not(puzzle, wrong["id"])
        assert puzzle.solution["property"] in shared

    def test_why_not_says_nothing_about_a_tile_off_the_board(self):
        assert why_not(make_puzzle(), "entity:1000") == ()

    def test_a_near_miss_is_a_property_with_two_outsiders(self):
        """Safe to name precisely because a property with two outsiders is
        not a reading, so it cannot be the board's answer."""
        from puzzlegen_numbers.properties import near_readings_of, vocabulary_of

        puzzle = make_puzzle([12, 34, 51, 53, 76], answer=51)
        memberships = {
            k: tuple(v) for k, v in puzzle.solution["memberships"].items()
        }
        near = near_readings_of(memberships, vocabulary_of(memberships))
        assert "even number" in near
        assert near_miss_for(puzzle, "entity:53") == "even number"

    def test_a_near_miss_is_empty_when_the_board_has_none(self):
        puzzle = make_puzzle()
        assert puzzle.solution["near"] == []
        tile = puzzle.payload["tiles"][0]["id"]
        assert near_miss_for(puzzle, tile) == ""

    def test_a_near_miss_says_nothing_about_a_tile_off_the_board(self):
        assert near_miss_for(make_puzzle(), "entity:1000") == ""

    def test_readings_reports_what_the_verifier_counts(self):
        puzzle = make_puzzle()
        assert list(readings(puzzle)) == [puzzle.solution["answer"]]
