"""The property vocabulary, and the rule the whole game is an application of.

Two things are tested. The rule itself -- a property true of every tile but
one -- against boards whose arithmetic is computed rather than typed. And the
two decisions this module makes that the graph cannot: which properties are
familiar, and which cannot be a board's answer at all.
"""

from __future__ import annotations

import pytest

from puzzlegen_numbers.properties import (
    MAX_TIER,
    MIN_TIER,
    TIERS,
    UNKNOWN_TIER,
    holds_for_all_but,
    near_readings_of,
    obscurity_of,
    readings_of,
    tier_of,
    universal_in,
    vocabulary_of,
)

from boards import entity, properties_of


def memberships(*values: int) -> dict[str, tuple[str, ...]]:
    """A board's memberships, exactly as the content service supplies them.

    Through ``entity`` rather than through ``properties_of``, so the taxonomy
    root is present. A helper that left it out would make every test about
    universal properties pass by having nothing universal to find.
    """
    return {view.entity_id: tuple(view.types) for view in map(entity, values)}


class TestTheTiers:
    def test_every_property_the_content_emits_is_rated(self):
        """A property the table has not heard of rates as most obscure, which
        is safe but silent. This is the check that keeps it from happening for
        the properties that actually exist."""
        from puzzlegen.providers.generated import NUMBER_CATEGORIES

        emitted = {category.name for category in NUMBER_CATEGORIES}
        # "two-digit number" and the digit-count categories are never a
        # board's answer, because a window of one magnitude makes them
        # universal, so they need no tier.
        rateable = {name for name in emitted if "digit" not in name}
        assert rateable <= set(TIERS)

    def test_an_unknown_property_is_rated_most_obscure(self):
        """A build that grew a category nobody has rated would otherwise have
        it treated as familiar as "even" on an easy day."""
        assert tier_of("numbers whose digits differ by four") == UNKNOWN_TIER
        assert UNKNOWN_TIER == MAX_TIER

    def test_the_tiers_run_from_the_plainest_to_a_sequence(self):
        assert tier_of("even number") == MIN_TIER
        assert tier_of("prime number") == MIN_TIER + 1
        assert tier_of("Fibonacci number") == MAX_TIER

    def test_obscurity_spans_the_whole_unit_interval(self):
        """A feature whose floor was a third would compress the scale."""
        assert obscurity_of("even number") == 0.0
        assert obscurity_of("Fibonacci number") == 1.0
        assert 0.0 < obscurity_of("prime number") < 1.0

    def test_every_tier_has_more_than_one_property(self):
        """A tier with one property would make every board of that band the
        same puzzle."""
        counts: dict[int, int] = {}
        for name in TIERS:
            counts[tier_of(name)] = counts.get(tier_of(name), 0) + 1
        assert all(count >= 2 for count in counts.values())


class TestTheVocabulary:
    def test_it_is_every_property_the_tiles_carry(self):
        found = vocabulary_of(memberships(12, 13))
        assert "prime number" in found
        assert "even number" in found

    def test_a_property_every_tile_carries_is_dropped(self):
        """It has no outsider, so it is a reading of no board. A generator
        that did not know would count it as a reading and have the board
        refused as ambiguous."""
        board = memberships(12, 34, 56)
        assert "even number" not in vocabulary_of(board)
        assert "even number" in universal_in(board)

    def test_the_root_is_always_universal(self):
        board = memberships(11, 12, 13)
        assert "number" in universal_in(board)
        assert "number" not in vocabulary_of(board)

    def test_the_digit_count_is_universal_inside_one_window(self):
        """The reason the window is one magnitude: otherwise digit count is
        the easiest reading on every board, every day."""
        board = memberships(10, 47, 99)
        assert "two-digit number" in universal_in(board)
        assert "two-digit number" not in vocabulary_of(board)

    def test_it_is_sorted_so_two_runs_scan_in_the_same_order(self):
        board = memberships(10, 13, 21, 36)
        assert list(vocabulary_of(board)) == sorted(vocabulary_of(board))

    def test_an_empty_board_has_no_vocabulary(self):
        assert vocabulary_of({}) == ()
        assert universal_in({}) == ()


class TestTheRule:
    def test_a_property_true_of_all_but_one_holds_for_that_one(self):
        board = memberships(12, 34, 51, 76)
        assert holds_for_all_but("even number", "entity:51", board)

    def test_it_is_false_when_the_outsider_also_has_it(self):
        board = memberships(12, 34, 76)
        assert not holds_for_all_but("even number", "entity:12", board)

    def test_it_is_false_when_another_tile_lacks_it_too(self):
        board = memberships(12, 34, 51, 53)
        assert not holds_for_all_but("even number", "entity:51", board)

    def test_a_tile_not_on_the_board_is_never_an_outsider(self):
        board = memberships(12, 34)
        assert not holds_for_all_but("even number", "entity:99", board)


class TestTheReadings:
    def test_a_board_with_one_odd_number_out_has_one_reading(self):
        board = memberships(12, 34, 51, 76, 98)
        found = readings_of(board, vocabulary_of(board))
        assert list(found) == ["entity:51"]

    def test_two_properties_picking_the_same_tile_are_one_answer(self):
        """One answer with two arguments for it. A verifier counting pairs
        would report two solutions and have the board refused for being too
        generous, when in fact it is easier.
        """
        # 15, 30, 45, 60 are all multiples of three and five; 16 is neither.
        board = memberships(15, 16, 30, 45, 60)
        found = readings_of(board, vocabulary_of(board))
        assert list(found) == ["entity:16"]
        assert len(found["entity:16"]) >= 2

    def test_two_tiles_readable_are_two_answers(self):
        """The ambiguity that actually matters: two players give two
        different tiles and the game marks one of them wrong.

        This board is the smallest such one found by searching the five-tile
        subsets of 10 to 59, which is how it was chosen rather than guessed:
        11, 15, 21 and 45 are odd and 10 is not, while 10, 15, 21 and 45 are
        triangular and 11 is not. Both readings are honest and a player
        giving either tile has an argument.
        """
        board = memberships(10, 11, 15, 21, 45)
        found = readings_of(board, vocabulary_of(board))
        assert set(found) == {"entity:10", "entity:11"}
        assert found["entity:10"] == ("odd number",)
        assert found["entity:11"] == ("triangular number",)

    def test_a_board_where_nothing_stands_out_has_no_reading(self):
        board = memberships(12, 14, 16, 18)
        assert readings_of(board, vocabulary_of(board)) == {}

    def test_the_readings_are_keyed_by_tile_and_sorted(self):
        board = memberships(12, 34, 51, 76, 98)
        found = readings_of(board, vocabulary_of(board))
        assert all(list(v) == sorted(v) for v in found.values())


class TestTheNearReadings:
    def test_a_property_true_of_all_but_two_is_near_and_not_a_reading(self):
        board = memberships(12, 34, 51, 53, 76)
        near = near_readings_of(board, vocabulary_of(board))
        assert "even number" in near
        assert "entity:51" not in readings_of(board, vocabulary_of(board))

    def test_a_reading_is_not_also_near(self):
        board = memberships(12, 34, 51, 76, 98)
        vocabulary = vocabulary_of(board)
        readings = set(readings_of(board, vocabulary))
        near = set(near_readings_of(board, vocabulary))
        for name in near:
            assert not any(
                holds_for_all_but(name, tile, board) for tile in readings
            )
