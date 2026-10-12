"""Proving the clues entail exactly one code.

The contract is ``UNIQUE_DEDUCTION``, which is stronger than one answer: the
clues must entail it, so a solver who reasons correctly is left holding the
code rather than choosing between two. The enumeration counts survivors, so a
board with two reports two and is refused, which is the right outcome and not
an approximation of it.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.uniqueness import consistency_problem
from puzzlegen_digits.descriptor import DESCRIPTOR, MAX_CLUES, SEARCH_BOUND
from puzzlegen_digits.lock import PROPERTY_KINDS
from puzzlegen_digits.verify import (
    AFTER_FACTS,
    BEFORE_LAST,
    CLUE_COUNT,
    CODES_EXAMINED,
    FACT_COUNT,
    SURVIVORS,
    board_problem,
    digit_facts,
    surviving_codes,
    verify,
)

from boards import CODE, DIGITS, GUESSES, make_puzzle


class TestAGoodBoard:
    def test_one_code_survives_and_is_reported_as_one(self):
        result = verify(make_puzzle())
        assert result.solvable
        assert result.solution_count == 1
        assert result.completeness is VerificationCompleteness.COMPLETE
        assert result.detail == ""

    def test_the_whole_space_is_examined(self):
        """The basis for the COMPLETE claim, asserted against the count rather
        than against itself."""
        assert verify(make_puzzle()).states_examined == 720
        four = make_puzzle(
            code=(1, 4, 7, 2), guesses=((4, 3, 7, 0), (4, 6, 1, 2), (6, 0, 3, 1))
        )
        assert verify(four).states_examined == 5040

    def test_the_space_stays_inside_the_declared_bound(self):
        assert verify(make_puzzle()).states_examined <= DESCRIPTOR.search_bound

    def test_the_reported_solution_carries_the_code(self):
        puzzle = make_puzzle()
        result = verify(puzzle)
        assert result.solutions[0]["code"] == list(CODE)

    def test_the_metrics_the_difficulty_model_reads_are_present(self):
        result = verify(make_puzzle(properties=("primes",)))
        for metric in (
            CODES_EXAMINED, SURVIVORS, CLUE_COUNT, FACT_COUNT, BEFORE_LAST, AFTER_FACTS
        ):
            assert metric in result.metrics

    def test_the_result_is_internally_consistent(self):
        assert consistency_problem(verify(make_puzzle())) is None

    def test_a_board_with_only_stated_facts_can_still_be_judged(self):
        """Not a board the generator makes, and the verifier should not refuse
        it: facts alone rarely entail a code, so the honest report is the
        count they leave."""
        result = verify(make_puzzle(guesses=(), properties=PROPERTY_KINDS))
        assert result.solution_count >= 1


class TestTheFactsComeFromTheBoard:
    def test_the_verifier_reads_the_facts_the_clue_was_made_from(self):
        """It holds no content port, so if it computed primality itself a
        build whose digit taxonomy differed would produce a board whose clue
        said one thing and whose verifier said another."""
        puzzle = make_puzzle()
        assert digit_facts(puzzle)[7].prime is True

    def test_a_board_carrying_no_facts_is_refused_rather_than_guessed_at(self):
        puzzle = make_puzzle(properties=("primes",))
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "digit_facts": {}},
            fact_refs=puzzle.fact_refs,
        )
        assert "no facts for digits" in board_problem(tampered)

    def test_doctored_facts_change_what_a_stated_fact_admits(self):
        """The point of carrying them: the verifier agrees with the content,
        whatever the content says."""
        puzzle = make_puzzle(properties=("primes",))
        lying = {
            str(digit): {"value": digit, "prime": True, "even": False}
            for digit in DIGITS
        }
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "digit_facts": lying},
            fact_refs=puzzle.fact_refs,
        )
        assert digit_facts(tampered)[4].prime is True
        assert verify(tampered).solution_count != verify(puzzle).solution_count


class TestABoardThatDoesNotEntailItsAnswer:
    def test_a_board_with_two_survivors_reports_two(self):
        """One intended answer and no argument for it: a player who reasons to
        the end has two codes and a coin. The board ``UNIQUE_DEDUCTION``
        promises not to ship.
        """
        result = verify(make_puzzle(guesses=GUESSES[:1]))
        assert result.solution_count > 1
        assert "does not entail" in result.detail

    def test_a_clue_that_contradicts_the_code_is_refused(self):
        """Caught before any enumeration: a board whose clues are not true of
        its own answer has no answer, whatever the enumeration would find."""
        puzzle = make_puzzle(exact_override={0: (2, 0)})
        assert "contradicts its own answer" in board_problem(puzzle)

    def test_a_doctored_count_is_caught_as_a_contradiction_not_a_leak(self):
        """The order of the two checks, stated. A guess that is not the code
        cannot report every digit exact *truthfully*, so the truth check sees
        it first and says the more useful thing."""
        puzzle = make_puzzle(exact_override={0: (3, 0)})
        assert "contradicts its own answer" in board_problem(puzzle)

    def test_a_clue_that_is_the_code_is_refused_as_the_answer_shown(self):
        """The leak check, reached by a clue that is both true and the code --
        which is the only way to reach it, since any other guess reporting
        every digit exact is a contradiction first."""
        puzzle = make_puzzle(guesses=(CODE, *GUESSES))
        assert "the answer shown" in board_problem(puzzle)


class TestTheBoardProblems:
    def test_a_good_board_has_none(self):
        assert board_problem(make_puzzle()) is None

    def test_a_board_listing_a_digit_twice_is_refused(self):
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "digits": [1, 1, 2, 3]},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert "a digit twice" in board_problem(tampered)

    def test_a_board_with_too_few_digits_for_its_length_is_refused(self):
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "digits": [1, 2]},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert "cannot be built from" in board_problem(tampered)

    def test_an_illegal_stated_answer_is_refused(self):
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "code": [1, 1, 1]},
            fact_refs=puzzle.fact_refs,
        )
        assert "not a legal code" in board_problem(tampered)

    def test_a_board_with_too_many_clues_is_refused(self):
        puzzle = make_puzzle()
        rows = puzzle.payload["clues"] * 4
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "clues": rows},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert f"over the {MAX_CLUES}" in board_problem(tampered)

    def test_a_board_with_no_clues_is_refused(self):
        puzzle = make_puzzle(guesses=())
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload={**puzzle.payload, "clues": [], "facts": []},
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        assert "nothing entails anything" in board_problem(tampered)

    def test_a_board_with_a_problem_verifies_as_unsolvable_and_complete(self):
        """COMPLETE rather than incomplete: nothing was left unexamined, the
        board was unjudgeable."""
        result = verify(make_puzzle(exact_override={0: (3, 0)}))
        assert not result.solvable
        assert result.completeness is VerificationCompleteness.COMPLETE


class TestTheBound:
    def test_a_space_too_large_to_enumerate_reports_an_unfinished_search(self):
        result = verify(make_puzzle(), code_budget=10)
        assert result.completeness is VerificationCompleteness.SOUND_INCOMPLETE
        assert result.states_examined == 0
        assert not result.solvable
        assert "beyond the 10" in result.detail

    def test_the_bound_covers_the_largest_board(self):
        from puzzlegen_digits.descriptor import CODE_LENGTHS

        from math import perm

        assert perm(10, max(CODE_LENGTHS)) < SEARCH_BOUND


class TestTheDiagnostics:
    def test_surviving_codes_reports_what_the_verifier_counts(self):
        puzzle = make_puzzle()
        assert surviving_codes(puzzle) == (tuple(CODE),)
        assert len(surviving_codes(puzzle)) == verify(puzzle).solution_count
