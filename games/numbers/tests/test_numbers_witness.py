"""The witness: the elimination kept, and the ladder it orders.

The counts here are over a five-tile board, so every figure is checkable by
counting: five tiles before anything is said, and one fewer for each tile
ruled out.
"""

from __future__ import annotations

from puzzlegen.engine.certify import AssuranceLevel
from puzzlegen.engine.coupling import Fact
from puzzlegen.engine.witness import entails
from puzzlegen_numbers.play import answer_of, empty_state
from puzzlegen_numbers.witness import (
    ASSURANCE,
    IS_ODD_ONE_OUT,
    check,
    known_facts,
    next_step,
    reading_space,
    tile_elements,
    tile_space,
    upcoming,
    witness_for,
)

import pytest

from boards import make_puzzle


@pytest.fixture
def puzzle():
    return make_puzzle()


def other_tiles(puzzle):
    return [
        tile["id"]
        for tile in puzzle.payload["tiles"]
        if tile["id"] != answer_of(puzzle)
    ]


class TestTheSpaces:
    def test_the_cost_space_is_every_tile_the_board_shows(self, puzzle):
        assert len(tile_space(puzzle).answers) == 5

    def test_the_reading_space_is_the_elimination_s_result(self, puzzle):
        """The board's contract is one tile, so one tile is a reading."""
        assert reading_space(puzzle).answers == (answer_of(puzzle),)

    def test_the_two_spaces_are_not_the_same_space(self, puzzle):
        """The distinction the whole module rests on.

        Costs counted over the readings would be zero for every true
        statement, because the memberships already entail the answer. Counted
        over the tiles they order the ladder.
        """
        assert len(tile_space(puzzle).answers) > len(reading_space(puzzle).answers)

    def test_the_predicate_is_this_game_s_own(self):
        """Namespaced, so no other game's space reads it by accident."""
        assert IS_ODD_ONE_OUT.startswith("odd_number_out/")


class TestTheArithmetic:
    def test_an_exclusion_removes_one_tile_of_five(self, puzzle):
        steps = {step.statement: step for step in witness_for(puzzle).steps}
        exclusions = [s for s in steps.values() if "is not the one" in s.statement]
        assert exclusions
        assert all(step.space_after == 4 for step in exclusions)
        assert all(step.information == pytest.approx(0.2) for step in exclusions)

    def test_ruling_out_all_but_two_leaves_two(self, puzzle):
        pair = [
            step
            for step in witness_for(puzzle).steps
            if step.statement.startswith("It is either")
        ]
        assert len(pair) == 1
        assert pair[0].space_after == 2

    def test_the_witness_runs_cheapest_first(self, puzzle):
        costs = [step.space_after for step in witness_for(puzzle).steps]
        assert costs == sorted(costs, reverse=True)


class TestWhatTheWitnessRefuses:
    def test_no_step_names_the_answer(self, puzzle):
        """A witness holding the answer would make the no-reveal rule a
        negotiation with the hint service rather than a property here."""
        assert not any(step.is_reveal for step in witness_for(puzzle).steps)
        assert all(
            answer_of(puzzle) not in step.reveals
            for step in witness_for(puzzle).steps
        )

    def test_every_step_is_entailed_by_the_board(self, puzzle):
        admitted = reading_space(puzzle)
        assert all(
            entails(admitted, step.establishes)
            for step in witness_for(puzzle).steps
        )

    def test_the_whole_check_passes_on_a_sound_board(self, puzzle):
        assert check(puzzle) == ()

    def test_the_witness_holds_up_against_its_own_space(self, puzzle):
        assert witness_for(puzzle).holds_up(tile_space(puzzle)) == ()


class TestTheAssurance:
    def test_it_is_answer_valid_and_not_borrowed(self, puzzle):
        """One level below digit_lock's, because this game's vocabulary is
        bounded and its own verifier promises SOUND_INCOMPLETE."""
        assert ASSURANCE is AssuranceLevel.ANSWER_VALID
        assert witness_for(puzzle).assurance is AssuranceLevel.ANSWER_VALID

    def test_a_caller_demanding_proved_uniqueness_is_refused(self, puzzle):
        from puzzlegen.engine.witness import WitnessError, next_deduction

        with pytest.raises(WitnessError):
            next_deduction(
                witness_for(puzzle),
                floor=AssuranceLevel.UNIQUENESS_PROVED,
            )


class TestWhatThePlayerHolds:
    def test_a_wrong_guess_is_an_exclusion(self, puzzle):
        wrong = other_tiles(puzzle)[0]
        held = known_facts(puzzle, {"guesses": [wrong]})
        assert held == (Fact(predicate=IS_ODD_ONE_OUT, ref=wrong, value=False),)

    def test_a_right_guess_establishes_nothing_to_skip(self, puzzle):
        """The board is over; there is no hint to withhold."""
        assert known_facts(puzzle, {"guesses": [answer_of(puzzle)]}) == ()

    def test_a_guess_the_board_does_not_hold_is_ignored(self, puzzle):
        assert known_facts(puzzle, {"guesses": ["entity:9999"]}) == ()

    def test_no_guesses_is_no_knowledge(self, puzzle):
        assert known_facts(puzzle, empty_state(puzzle)) == ()

    def test_repeated_guesses_establish_one_fact(self, puzzle):
        wrong = other_tiles(puzzle)[0]
        assert len(known_facts(puzzle, {"guesses": [wrong, wrong]})) == 1


class TestTheLadder:
    def test_the_first_step_is_an_exclusion(self, puzzle):
        step = next_step(puzzle, empty_state(puzzle), 0)
        assert "is not the one" in step.statement

    def test_each_request_moves_one_rung(self, puzzle):
        first = next_step(puzzle, empty_state(puzzle), 0)
        second = next_step(puzzle, empty_state(puzzle), 1)
        assert first.statement != second.statement

    def test_a_statement_the_player_bought_is_skipped(self, puzzle):
        wrong = other_tiles(puzzle)[0]
        step = next_step(puzzle, {"guesses": [wrong]}, 0)
        assert wrong not in step.reveals

    def test_the_ladder_runs_out_rather_than_revealing(self, puzzle):
        assert next_step(puzzle, empty_state(puzzle), 3) is None

    def test_the_walk_re_costs_what_the_absolute_order_ties(self, puzzle):
        """Four exclusions tie at four tiles absolutely and separate here."""
        absolute = [
            step.space_after
            for step in witness_for(puzzle).steps
            if "is not the one" in step.statement
        ]
        assert absolute == [4, 4, 4, 4]
        walked = upcoming(puzzle, empty_state(puzzle), 0, count=3)
        assert [step.space_after for step in walked] == [4, 3, 2]


class TestTheJustification:
    def test_a_step_rests_on_the_whole_board(self, puzzle):
        elements = tile_elements(puzzle)
        assert len(elements) == 5
        assert all(
            step.justification == elements for step in witness_for(puzzle).steps
        )

    def test_a_step_speaks_about_the_tiles_it_names(self, puzzle):
        for step in witness_for(puzzle).steps:
            assert step.reveals == tuple(fact.ref for fact in step.establishes)


class TestABoardWhoseContractDoesNotHold:
    def test_a_board_with_two_readings_offers_fewer_steps(self):
        """The entailment check drops what a player could not have derived.

        Two, three, five, seven and nine has two readings: the two lacks odd
        and the nine lacks prime. So "the two is not the one" is no longer
        forced by the board, and the witness is shorter than a single-reading
        board's by exactly the statements about those tiles.
        """
        sound = make_puzzle()
        ambiguous = make_puzzle(values=(2, 3, 5, 7, 9))
        assert len(reading_space(ambiguous).answers) == 2
        assert len(witness_for(ambiguous).steps) < len(witness_for(sound).steps)

    def test_a_board_with_no_reading_offers_nothing(self):
        """Not a full ladder of false statements.

        A board no property distinguishes admits no answer, and every
        statement is vacuously consistent with an empty set. The engine's
        entailment guard refuses that, so the witness comes back exhausted
        rather than confident.
        """
        broken = make_puzzle(values=(2, 4, 6, 7, 9))
        assert reading_space(broken).answers == ()
        assert witness_for(broken).exhausted
