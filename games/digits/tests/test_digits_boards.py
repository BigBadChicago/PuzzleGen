"""The test fixtures themselves, held to the properties other tests assume."""

from __future__ import annotations

import pytest

from puzzlegen.providers.generated import is_prime
from puzzlegen_digits.lock import space_for, survivors, verdict
from puzzlegen_digits.verify import surviving_codes, verify

from boards import CODE, DIGITS, GUESSES, context, digits, entity, facts, make_puzzle


class TestTheFixtureBoard:
    def test_the_clues_entail_exactly_one_code(self):
        """The property the fixture exists for. The obvious clue set -- one row
        per third of the digits -- leaves twenty-four codes alive, which is why
        these were searched for."""
        assert surviving_codes(make_puzzle()) == (tuple(CODE),)

    def test_it_verifies(self):
        result = verify(make_puzzle())
        assert result.solvable and result.solution_count == 1
        assert result.detail == ""

    def test_the_obvious_clue_set_would_not_have_worked(self):
        """Recorded as a test so the comment in boards.py is checkable."""
        naive = make_puzzle(guesses=((0, 1, 2), (3, 4, 5), (6, 7, 8)))
        assert len(surviving_codes(naive)) > 1

    def test_every_clue_is_true_of_the_code(self):
        puzzle = make_puzzle()
        for row in puzzle.payload["clues"]:
            got = verdict(tuple(row["guess"]), tuple(CODE))
            assert got == (row["exact"], row["misplaced"])

    def test_no_clue_is_the_code(self):
        for guess in GUESSES:
            assert tuple(guess) != tuple(CODE)

    def test_a_four_digit_fixture_also_works(self):
        """Not searched, so it is allowed to leave several codes alive; what
        is asserted is that the builder handles the other length."""
        puzzle = make_puzzle(
            code=(1, 4, 7, 2), guesses=((4, 3, 7, 0), (4, 6, 1, 2), (6, 0, 3, 1))
        )
        assert puzzle.payload["code_length"] == 4
        assert verify(puzzle).solvable


class TestTheBuilders:
    def test_every_digit_carries_a_glyph_a_label_and_its_facts(self):
        view = entity(7)
        assert view.properties["glyph"] == "7"
        assert view.properties["spoken"] == "seven"
        assert view.properties["is_prime"] is True
        assert view.properties["parity"] == "odd"

    def test_the_facts_are_the_providers_arithmetic(self):
        """A fixture with its own arithmetic would make a property clue true
        in a test and false in production."""
        built = facts()
        for value in DIGITS:
            assert built[value].prime == is_prime(value)
            assert built[value].even == (value % 2 == 0)

    def test_ten_digits_come_back_ascending(self):
        values = [view.properties["value"] for view in digits()]
        assert values == list(range(10))

    def test_the_stated_facts_are_only_the_kinds_asked_for(self):
        assert make_puzzle().payload["fact_count"] == 0
        assert make_puzzle(properties=("primes",)).payload["fact_count"] == 1

    def test_a_doctored_verdict_is_how_a_broken_board_is_built(self):
        puzzle = make_puzzle(exact_override={0: (3, 0)})
        assert puzzle.payload["clues"][0]["exact"] == 3

    def test_the_solution_carries_the_digit_facts(self):
        """The verifier reads them from here rather than recomputing, so a
        fixture that omitted them would make every verification fail."""
        puzzle = make_puzzle()
        stated = puzzle.solution["digit_facts"]
        assert len(stated) == 10
        assert stated["7"]["prime"] is True

    def test_a_context_carries_the_content_under_the_name_the_game_reads(self):
        from puzzlegen_digits import content as content_module

        assert content_module.CANDIDATES in context().content
