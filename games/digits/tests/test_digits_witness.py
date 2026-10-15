"""The witness: the elimination kept, and the ladder it orders.

The counts here are over 720 codes -- ten digits taken three at a time --
and each is checkable: the codes containing a given digit are three tenths of
them, the codes with a given digit in a given place are one tenth, and the
arrangements of three named digits are six.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.certify import AssuranceLevel
from puzzlegen.engine.coupling import Fact
from puzzlegen.engine.witness import entails, next_deduction
from puzzlegen_digits.witness import (
    AT,
    USES,
    check,
    clue_elements,
    code_space,
    known_facts,
    next_step,
    survivor_space,
    witness_for,
)

from boards import CODE, make_puzzle


@pytest.fixture
def puzzle():
    return make_puzzle()


class TestTheSpaces:
    def test_the_cost_space_is_every_code_the_board_could_name(self, puzzle):
        """Ten digits taken three at a time, before a clue is read."""
        assert len(code_space(puzzle).answers) == 720

    def test_the_survivor_space_is_the_elimination_s_result(self, puzzle):
        """The board's contract is one code, so one survives its clues."""
        assert survivor_space(puzzle).answers == (CODE,)

    def test_the_two_spaces_are_not_the_same_space(self, puzzle):
        """The distinction the whole module rests on.

        Costs counted over the survivors would be zero for every true
        statement, because the clues already entail the answer. Counted over
        the unconstrained space they order the ladder.
        """
        assert len(code_space(puzzle).answers) > len(
            survivor_space(puzzle).answers
        )

    def test_a_board_with_weaker_clues_keeps_more_survivors(self):
        one_clue = make_puzzle(guesses=(CODE[:1] + (0, 2),))
        assert len(survivor_space(one_clue).answers) > 1


class TestTheCosts:
    def test_an_exclusion_leaves_seven_tenths_of_the_space(self, puzzle):
        """504 of 720: the codes drawn from the other nine digits."""
        step = next(
            s
            for s in witness_for(puzzle).steps
            if s.statement == "The digit 0 is not in the code"
        )
        assert step.space_before == 720
        assert step.space_after == 504
        assert step.information == pytest.approx(0.3)

    def test_an_inclusion_leaves_three_tenths_of_the_space(self, puzzle):
        """216 of 720: three positions for the digit, 72 codes each."""
        step = next(
            s
            for s in witness_for(puzzle).steps
            if s.statement == f"The digit {CODE[0]} is in the code"
        )
        assert step.space_after == 216
        assert step.information == pytest.approx(0.7)

    def test_a_position_leaves_a_tenth_of_the_space(self, puzzle):
        step = next(
            s for s in witness_for(puzzle).steps if s.statement.startswith("Position 1")
        )
        assert step.space_after == 72

    def test_the_unordered_digits_leave_their_own_arrangements(self, puzzle):
        """Six, at three digits, which is why it is the last rung and not a
        reveal: the player is left arranging them."""
        step = witness_for(puzzle).steps[-1]
        assert "in some order" in step.statement
        assert step.space_after == 6
        assert not step.is_reveal

    def test_an_exclusion_is_cheaper_than_an_inclusion(self, puzzle):
        """The measured fact that reorders the authored ladder."""
        steps = {s.statement: s for s in witness_for(puzzle).steps}
        assert (
            steps["The digit 0 is not in the code"].information
            < steps[f"The digit {CODE[0]} is in the code"].information
        )


class TestTheWitness:
    def test_the_steps_run_cheapest_first(self, puzzle):
        costs = [step.information for step in witness_for(puzzle).steps]
        assert costs == sorted(costs)

    def test_the_witness_declares_its_assurance(self, puzzle):
        """Declared rather than implied, and this game may claim the top one
        only because it enumerates its whole space."""
        assert witness_for(puzzle).assurance is AssuranceLevel.UNIQUENESS_PROVED

    def test_the_witness_holds_up_against_its_own_space(self, puzzle):
        assert witness_for(puzzle).holds_up(code_space(puzzle)) == ()

    def test_every_step_is_entailed_by_the_board_s_clues(self, puzzle):
        """What makes a step a deduction rather than a leak of the answer."""
        admitted = survivor_space(puzzle)
        assert all(
            entails(admitted, step.establishes)
            for step in witness_for(puzzle).steps
        )

    def test_the_whole_check_passes_on_a_sound_board(self, puzzle):
        assert check(puzzle) == ()

    def test_a_step_cites_the_rows_it_rests_on(self, puzzle):
        elements = clue_elements(puzzle)
        assert elements
        assert all(step.justification == elements for step in witness_for(puzzle).steps)

    def test_no_step_states_the_whole_code(self, puzzle):
        assert not any(step.is_reveal for step in witness_for(puzzle).steps)

    def test_two_builds_of_one_board_agree(self, puzzle):
        """A hint is re-derived on replay, so the ladder must be stable."""
        first = [s.statement for s in witness_for(puzzle).steps]
        second = [s.statement for s in witness_for(puzzle).steps]
        assert first == second

    def test_a_board_with_two_survivors_offers_only_the_shared_claims(self):
        """The entailment check doing its work.

        A board whose clues admit several codes has statements true of the
        intended answer and false of the others, and those are exactly the
        ones a player could not have derived. They are dropped.
        """
        weak = make_puzzle(guesses=(CODE[:1] + (0, 2),))
        admitted = survivor_space(weak)
        assert len(admitted.answers) > 1
        witness = witness_for(weak)
        assert all(
            entails(admitted, step.establishes) for step in witness.steps
        )
        assert len(witness.steps) < len(witness_for(make_puzzle()).steps)


class TestWhatThePlayerAlreadyHolds:
    def test_a_guess_told_nothing_is_an_exclusion_per_digit(self, puzzle):
        missing = [digit for digit in range(10) if digit not in CODE][:3]
        held = known_facts(
            puzzle,
            {"attempts": [{"guess": missing, "exact": 0, "misplaced": 0}]},
        )
        assert set(held) == {
            Fact(predicate=USES, ref=str(digit), value=False) for digit in missing
        }

    def test_a_guess_with_every_digit_right_is_an_inclusion_per_digit(self, puzzle):
        shuffled = [CODE[1], CODE[2], CODE[0]]
        held = known_facts(
            puzzle,
            {"attempts": [{"guess": shuffled, "exact": 0, "misplaced": 3}]},
        )
        assert set(held) == {
            Fact(predicate=USES, ref=str(digit), value=True) for digit in CODE
        }

    def test_a_partial_verdict_establishes_nothing_without_search(self, puzzle):
        """Only what is readable off the row is counted as held.

        A guess told one digit is right somewhere narrows the space, and
        working out *which* is a search. Counting it as knowledge would skip
        a hint the player does not actually have.
        """
        held = known_facts(
            puzzle,
            {"attempts": [{"guess": [CODE[0], 0, 2], "exact": 1, "misplaced": 0}]},
        )
        assert held == ()

    def test_no_attempts_is_no_knowledge(self, puzzle):
        assert known_facts(puzzle, {}) == ()

    def test_the_facts_are_distinct_across_repeated_attempts(self, puzzle):
        missing = [digit for digit in range(10) if digit not in CODE][:3]
        row = {"guess": missing, "exact": 0, "misplaced": 0}
        held = known_facts(puzzle, {"attempts": [row, dict(row)]})
        assert len(held) == len(set(held))


class TestTheLadderAgainstState:
    def test_the_first_step_is_the_cheapest_statement(self, puzzle):
        step = next_step(puzzle, {}, 0)
        assert step is not None
        assert "is not in the code" in step.statement

    def test_a_statement_the_player_bought_is_skipped(self, puzzle):
        first = next_step(puzzle, {}, 0)
        digit = int(first.establishes[0].ref)
        state = {"attempts": [{"guess": [digit, 0, 2], "exact": 0, "misplaced": 0}]}
        assert next_step(puzzle, state, 0).statement != first.statement

    def test_each_request_moves_one_rung(self, puzzle):
        statements = [next_step(puzzle, {}, used).statement for used in range(5)]
        assert len(set(statements)) == 5

    def test_the_ladder_runs_out_rather_than_revealing(self, puzzle):
        assert next_step(puzzle, {}, 40) is None

    def test_a_caller_demanding_proved_uniqueness_is_served(self, puzzle):
        """The declared level is the real one, so a strict floor passes."""
        step = next_deduction(
            witness_for(puzzle), floor=AssuranceLevel.UNIQUENESS_PROVED
        )
        assert step is not None


class TestTheFourDigitBoard:
    def board(self):
        """Four clues, because fewer leave this code unentailed.

        Two clues leave twelve survivors, and on such a board the unordered
        statement is dropped by the entailment check rather than offered --
        which is the behaviour the three-digit suite asserts and worth seeing
        once from the other side.
        """
        return make_puzzle(
            code=(1, 4, 7, 2),
            guesses=((4, 3, 7, 1), (4, 6, 1, 2), (0, 5, 8, 9), (2, 7, 4, 1)),
        )

    def test_the_space_is_five_thousand_and_forty(self):
        assert len(code_space(self.board()).answers) == 5040

    def test_the_unordered_digits_leave_twenty_four(self):
        """Real work at four digits, which is the authored hint's own claim."""
        last = witness_for(self.board()).steps[-1]
        assert "in some order" in last.statement
        assert last.space_after == 24

    def test_a_board_whose_clues_leave_twelve_codes_offers_less(self):
        loose = make_puzzle(code=(1, 4, 7, 2), guesses=((4, 3, 7, 1), (4, 6, 1, 2)))
        assert len(survivor_space(loose).answers) == 12
        assert len(witness_for(loose).steps) < len(witness_for(self.board()).steps)

    def test_the_predicates_are_this_game_s_own(self):
        """Namespaced, because a predicate is part of a game's rules."""
        assert AT.startswith("digit_lock/") and USES.startswith("digit_lock/")
