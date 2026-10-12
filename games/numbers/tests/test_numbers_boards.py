"""The test fixtures themselves, held to the properties other tests assume.

A builder that quietly stopped producing the board it claims to would make
every test using it pass for the wrong reason. The figure game's padding
figures were hand-picked once and were wrong, so this game's fixture board is
checked here rather than trusted, and its arithmetic is imported from the
provider rather than restated.
"""

from __future__ import annotations

import pytest

from puzzlegen.providers.generated import is_prime, is_square, is_triangular
from puzzlegen_numbers.descriptor import WINDOW
from puzzlegen_numbers.properties import readings_of, vocabulary_of
from puzzlegen_numbers.verify import readings, verify

from boards import EVEN_BOARD, TESTS, entity, make_puzzle, properties_of, window


class TestTheArithmeticIsTheProvidersOwn:
    def test_the_property_tests_are_imported_rather_than_restated(self):
        """A fixture with its own arithmetic would be a second place for it
        to be wrong, and a membership list claiming 51 was prime would make a
        uniqueness test pass against arithmetic nobody does."""
        assert TESTS["prime number"] is is_prime
        assert TESTS["square number"] is is_square
        assert TESTS["triangular number"] is is_triangular

    @pytest.mark.parametrize(
        "value,expected",
        [
            (51, ("odd number", "multiple of three", "two-digit number")),
            (36, ("even number", "square number", "triangular number")),
            (89, ("odd number", "prime number", "Fibonacci number")),
        ],
    )
    def test_a_numbers_properties_are_the_ones_it_has(self, value, expected):
        found = properties_of(value)
        for name in expected:
            assert name in found


class TestTheFixtureBoard:
    def test_it_holds_exactly_one_reading(self):
        """Four even numbers chosen carelessly are also all multiples of
        something, and a board with a second reading would make other tests
        pass for the wrong reason."""
        puzzle = make_puzzle()
        assert len(readings(puzzle)) == 1

    def test_the_reading_is_the_one_the_fixture_claims(self):
        puzzle = make_puzzle()
        assert puzzle.solution["answer"] == "entity:51"
        assert puzzle.solution["property"] == "even number"

    def test_it_verifies(self):
        result = verify(make_puzzle())
        assert result.solvable and result.solution_count == 1

    def test_it_is_in_the_window(self):
        low, high = WINDOW
        assert all(low <= value <= high for value in EVEN_BOARD)

    def test_it_is_in_ascending_order_once_built(self):
        puzzle = make_puzzle([98, 12, 51, 34, 76])
        values = [tile["value"] for tile in puzzle.payload["tiles"]]
        assert values == sorted(values)


class TestTheBuilders:
    def test_the_window_is_the_descriptors_own(self):
        low, high = WINDOW
        built = window(low, high)
        assert len(built) == high - low + 1
        assert built[0].properties["value"] == low

    def test_the_window_comes_back_ascending(self):
        values = [view.properties["value"] for view in window()]
        assert values == sorted(values)

    def test_an_entity_carries_a_glyph_a_label_and_a_value(self):
        view = entity(42)
        assert view.properties["glyph"] == "42"
        assert view.properties["spoken"] == "forty-two"
        assert view.properties["value"] == 42

    def test_an_entity_carries_the_root_as_well_as_its_properties(self):
        """Without it nothing on a board is universal, and every test about
        universal properties would pass by having nothing to find."""
        assert "number" in entity(42).types

    def test_make_puzzle_can_be_told_a_different_answer(self):
        """Needed by the tests that break a board on purpose."""
        puzzle = make_puzzle(answer=12, property_name="odd number")
        assert puzzle.solution["answer"] == "entity:12"
        assert puzzle.solution["property"] == "odd number"

    def test_a_context_carries_the_content_under_the_name_the_game_reads(self):
        from puzzlegen_numbers import content as content_module

        from boards import context

        built = context(window())
        assert content_module.CANDIDATES in built.content
