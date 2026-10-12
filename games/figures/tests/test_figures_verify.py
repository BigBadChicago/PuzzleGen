"""Proving the board has exactly one trio, and refusing to claim it when not.

The verifier is the one component in this game that can promise the strong
thing, so what is tested is that the promise is earned: every triple examined,
the count reported being the count, and a board with two trios or none
reported as what it is rather than as what the generator intended.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.uniqueness import consistency_problem
from puzzlegen_figures.descriptor import DESCRIPTOR, SEARCH_BOUND, TRIO
from puzzlegen_figures.verify import (
    DECOY_SHARE,
    NEAR_MISSES,
    TRIOS_FOUND,
    TRIPLES_EXAMINED,
    VARYING_ATTRIBUTES,
    board_problem,
    closest_misses,
    verify,
    why_not,
)

from boards import INERT_READINGS, TRIO_READINGS, figure, make_puzzle


def triples(board_size: int) -> int:
    return board_size * (board_size - 1) * (board_size - 2) // 6


class TestAGoodBoard:
    def test_one_trio_is_found_and_reported_as_one(self):
        result = verify(make_puzzle())
        assert result.solvable
        assert result.solution_count == 1
        assert result.completeness is VerificationCompleteness.COMPLETE
        assert result.detail == ""

    def test_every_triple_is_examined(self):
        """The count is the whole basis for the COMPLETE claim, so it is
        asserted against the closed form rather than against itself."""
        puzzle = make_puzzle()
        result = verify(puzzle)
        assert result.states_examined == triples(len(puzzle.payload["tiles"]))

    def test_the_states_examined_stay_inside_the_declared_bound(self):
        """A board whose enumeration overran the bound would be refused
        publication, so the bound has to cover the largest board."""
        assert triples(12) < SEARCH_BOUND
        assert verify(make_puzzle()).states_examined <= DESCRIPTOR.search_bound

    def test_the_reported_solution_is_the_puzzles_own_object(self):
        """The engine hashes the puzzle's solution and looks for that hash
        among the verifier's, so the intended answer has to come back in
        exactly the intended shape."""
        puzzle = make_puzzle()
        result = verify(puzzle)
        assert result.solutions == (dict(puzzle.solution),)

    def test_the_metrics_the_difficulty_model_reads_are_present(self):
        result = verify(make_puzzle())
        for metric in (
            TRIPLES_EXAMINED,
            TRIOS_FOUND,
            NEAR_MISSES,
            DECOY_SHARE,
            VARYING_ATTRIBUTES,
        ):
            assert metric in result.metrics

    def test_the_result_is_internally_consistent(self):
        """The engine rejects a self-contradictory verification as a protocol
        error, so the game's own result has to pass that check."""
        assert consistency_problem(verify(make_puzzle())) is None


class TestABoardWithTheWrongNumberOfTrios:
    def test_a_board_with_two_trios_reports_two(self):
        """Counted rather than intended. A verifier reporting its own intent
        would publish a board that marks a correct player wrong.

        The second trio here is the one the test fixtures were originally
        wrong about: three circles whose fill, count and colour are each all
        different. It looks like nothing and satisfies the rule exactly.
        """
        readings = [
            *TRIO_READINGS,
            figure("circle", "hollow", "three", "green"),
            figure("circle", "striped", "two", "blue"),
            *INERT_READINGS[:4],
        ]
        result = verify(make_puzzle(readings))
        assert result.solution_count == 2
        assert "not unique" in result.detail

    def test_a_board_with_no_trio_is_unsolvable(self):
        """A board whose own stated answer is not a trio.

        Assembly refuses this, so reaching the verifier means a bug; what the
        verifier owes is the honest report rather than a repair.
        """
        readings = [
            figure("triangle", "solid", "one", "red"),
            figure("triangle", "solid", "one", "green"),
            *INERT_READINGS[:4],
        ]
        result = verify(make_puzzle(readings, trio=(0, 1, 2), varying=()))
        assert not result.solvable
        assert result.solution_count == 0

    def test_a_board_whose_stated_answer_is_not_the_trio_it_holds(self):
        """Verifies, is unique, and marks the player wrong: the most
        dangerous way for a board to be broken."""
        readings = [*TRIO_READINGS, *INERT_READINGS]
        puzzle = make_puzzle(readings, trio=(0, 1, 2))
        tiles = [tile["id"] for tile in puzzle.payload["tiles"]]
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "trio": sorted(tiles[3:6])},
            fact_refs=puzzle.fact_refs,
        )
        result = verify(tampered)
        assert result.solution_count == 1
        assert "not among the trios" in result.detail


class TestTheBoardProblems:
    def test_a_good_board_has_none(self):
        assert board_problem(make_puzzle()) is None

    def test_a_board_shorter_than_a_trio_is_refused(self):
        puzzle = make_puzzle(list(TRIO_READINGS)[:2], trio=(0, 1, 1))
        assert "cannot hold a trio" in board_problem(puzzle)

    def test_a_board_whose_count_disagrees_with_its_tiles_is_refused(self):
        puzzle = make_puzzle(board_size=99)
        assert "shows" in board_problem(puzzle)

    def test_an_answer_naming_a_tile_not_on_the_board_is_refused(self):
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={
                **puzzle.solution,
                "trio": ["entity:nope", *puzzle.solution["trio"][1:]],
            },
            fact_refs=puzzle.fact_refs,
        )
        assert "not on the board" in board_problem(tampered)

    def test_a_board_with_a_problem_verifies_as_unsolvable_and_complete(self):
        """COMPLETE rather than incomplete: nothing was left unexamined, the
        board was unjudgeable."""
        result = verify(make_puzzle(board_size=99))
        assert not result.solvable
        assert result.completeness is VerificationCompleteness.COMPLETE


class TestTheBound:
    def test_a_board_too_large_to_enumerate_reports_an_unfinished_search(self):
        """Not a truncated enumeration: it is not started.

        The honest report is refused publication by the uniqueness gate, which
        is the correct outcome for a board this game was not built for.
        """
        result = verify(make_puzzle(), triple_budget=10)
        assert result.completeness is VerificationCompleteness.SOUND_INCOMPLETE
        assert result.states_examined == 0
        assert not result.solvable
        assert "beyond the 10" in result.detail


class TestTheDiagnostics:
    def test_why_not_names_the_attribute_a_wrong_triple_fails_on(self):
        puzzle = make_puzzle()
        tiles = [tile["id"] for tile in puzzle.payload["tiles"]]
        trio = puzzle.solution["trio"]
        others = [tile for tile in tiles if tile not in trio]
        failing = why_not(puzzle, (trio[0], trio[1], others[0]))
        assert failing and set(failing) <= set(puzzle.payload["attributes"])

    def test_why_not_says_nothing_about_the_right_triple(self):
        puzzle = make_puzzle()
        assert why_not(puzzle, tuple(puzzle.solution["trio"])) == ()

    def test_why_not_refuses_a_submission_of_the_wrong_size(self):
        puzzle = make_puzzle()
        assert why_not(puzzle, tuple(puzzle.solution["trio"][:2])) == tuple(
            puzzle.payload["attributes"]
        )

    def test_closest_misses_are_near_trios(self):
        puzzle = make_puzzle()
        for miss in closest_misses(puzzle):
            assert len(miss) == TRIO
            assert len(why_not(puzzle, miss)) == 1
