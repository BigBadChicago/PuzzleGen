"""The deduction, which everything else in the game rests on.

Two kinds of clue and one verdict function. The verdict is tested against its
definition rather than against a table, because the two counts being disjoint
is the thing a solver relies on and the thing an implementation gets wrong.
The property clues are tested for being read off the graph's facts rather than
computed, which is the property that keeps a clue and the content from
disagreeing.
"""

from __future__ import annotations

from itertools import permutations

import pytest

from puzzlegen_digits.lock import (
    BASE,
    PROPERTY_KINDS,
    CodeError,
    DigitFacts,
    GuessClue,
    PropertyClue,
    check_code,
    facts_from,
    property_clues_for,
    space_for,
    survivors,
    verdict,
)

from boards import DIGITS, facts


class TestTheVerdict:
    @pytest.mark.parametrize(
        "guess,secret,expected",
        [
            ((1, 2, 3), (1, 2, 3), (3, 0)),
            ((1, 2, 3), (3, 2, 1), (1, 2)),
            ((1, 2, 3), (4, 5, 6), (0, 0)),
            ((1, 2, 3), (1, 5, 6), (1, 0)),
            ((1, 2, 3), (2, 3, 1), (0, 3)),
        ],
    )
    def test_the_counts(self, guess, secret, expected):
        assert verdict(guess, secret) == expected

    def test_the_two_counts_are_disjoint(self):
        """A digit in the right place is not counted again as misplaced.

        The thing a solver relies on, over every pair of three-digit codes:
        the total never exceeds the code's length, which it would if a
        positional match were double counted.
        """
        space = space_for(3, range(5))
        for guess in space:
            for secret in space:
                exact, misplaced = verdict(guess, secret)
                assert exact + misplaced <= 3
                assert exact + misplaced == len(set(guess) & set(secret))

    def test_a_guess_matching_everything_is_the_code(self):
        clue = GuessClue(guess=(1, 2, 3), exact=3, misplaced=0)
        assert clue.solves()

    def test_a_guess_with_a_misplaced_digit_is_not(self):
        assert not GuessClue(guess=(1, 2, 3), exact=2, misplaced=1).solves()

    def test_a_clue_admits_exactly_the_codes_its_counts_describe(self):
        clue = GuessClue(guess=(1, 2, 3), exact=1, misplaced=1)
        for code in space_for(3, range(6)):
            assert clue.admits(code) is (verdict((1, 2, 3), code) == (1, 1))


class TestCodes:
    def test_a_legal_code_comes_back_as_a_tuple(self):
        assert check_code([1, 2, 3], length=3, digits=DIGITS) == (1, 2, 3)

    @pytest.mark.parametrize(
        "code,message",
        [
            ([1, 2], "is 3 digits"),
            ([1, 1, 2], "no repeated digit"),
            ([1, 2, 99], "not digits this board uses"),
        ],
    )
    def test_an_illegal_code_is_refused_with_its_own_message(self, code, message):
        """Three different bugs: a client sending the wrong length, a client
        sending a repeat, and a client sending a digit off the board."""
        with pytest.raises(CodeError, match=message):
            check_code(code, length=3, digits=DIGITS)

    def test_the_space_is_every_code_of_the_length(self):
        assert len(space_for(3, range(10))) == 720
        assert len(space_for(4, range(10))) == 5040

    def test_the_space_is_in_a_fixed_order(self):
        assert space_for(3, [2, 0, 1]) == list(permutations([0, 1, 2], 3))

    def test_the_base_is_ten(self):
        assert BASE == 10


class TestTheFactsComeFromTheGraph:
    def test_a_codes_facts_are_looked_up_rather_than_computed(self):
        """A build whose digit taxonomy disagreed with arithmetic would be
        wrong consistently, which is what matters for a puzzle being fair: the
        clue, the elimination and the enumeration would all say the same
        thing.
        """
        lying = {
            value: DigitFacts(value=value, prime=True, even=False)
            for value in range(10)
        }
        total, primes, evens = facts_from(lying, (1, 4, 6))
        assert primes == 3
        assert evens == 0
        assert total == 11

    def test_a_digit_the_content_does_not_hold_is_refused(self):
        with pytest.raises(CodeError, match="no digit 9"):
            facts_from(facts(range(9)), (1, 9))

    def test_the_real_facts_agree_with_arithmetic(self):
        total, primes, evens = facts_from(facts(), (1, 4, 7))
        assert (total, primes, evens) == (12, 1, 1)


class TestPropertyClues:
    def test_they_are_read_off_the_code_and_so_are_true_of_it(self):
        clues = property_clues_for((1, 4, 7), facts(), PROPERTY_KINDS)
        for clue in clues:
            assert clue.admits((1, 4, 7), facts())

    def test_one_is_emitted_per_kind_asked_for(self):
        assert len(property_clues_for((1, 4, 7), facts(), ("primes",))) == 1
        assert len(property_clues_for((1, 4, 7), facts(), PROPERTY_KINDS)) == 3

    def test_an_unknown_kind_is_not_emitted(self):
        assert property_clues_for((1, 4, 7), facts(), ("colour",)) == ()

    def test_an_unknown_kind_on_a_stored_board_admits_everything(self):
        """A snapshot carrying a new kind should serve a board with one fewer
        usable clue rather than a board nothing can solve."""
        clue = PropertyClue(kind="colour", value=1)
        assert clue.admits((1, 4, 7), facts())
        assert clue.admits((2, 5, 8), facts())

    @pytest.mark.parametrize(
        "kind,value,sentence",
        [
            ("sum", 12, "The digits add up to 12"),
            ("primes", 0, "None of the digits is prime"),
            ("primes", 1, "Exactly one digit is prime"),
            ("evens", 3, "Exactly 3 of the digits are even"),
        ],
    )
    def test_the_wording_is_part_of_the_clue(self, kind, value, sentence):
        """A client inventing its own phrasing would be inventing the
        puzzle's statement, and two clients wording "exactly two" and "two or
        more" differently would be two different games."""
        assert PropertyClue(kind=kind, value=value).sentence() == sentence

    def test_a_sum_clue_admits_only_codes_adding_to_it(self):
        clue = PropertyClue(kind="sum", value=12)
        known = facts()
        for code in space_for(3, range(10))[:200]:
            assert clue.admits(code, known) is (sum(code) == 12)


class TestTheElimination:
    def test_no_clue_leaves_the_whole_space(self):
        space = space_for(3, range(10))
        assert len(survivors(space, (), (), facts())) == len(space)

    def test_a_clue_removes_what_it_contradicts(self):
        space = space_for(3, range(10))
        clue = GuessClue(guess=(1, 2, 3), exact=3, misplaced=0)
        assert survivors(space, (clue,), (), facts()) == [(1, 2, 3)]

    def test_clues_compose(self):
        space = space_for(3, range(10))
        first = GuessClue(guess=(1, 2, 3), exact=1, misplaced=0)
        second = GuessClue(guess=(4, 5, 6), exact=0, misplaced=0)
        kept = survivors(space, (first, second), (), facts())
        for code in kept:
            assert first.admits(code) and second.admits(code)

    def test_a_property_clue_narrows_the_space(self):
        space = space_for(3, range(10))
        clue = PropertyClue(kind="primes", value=3)
        kept = survivors(space, (), (clue,), facts())
        assert kept
        assert len(kept) < len(space)
        for code in kept:
            assert all(digit in (2, 3, 5, 7) for digit in code)
