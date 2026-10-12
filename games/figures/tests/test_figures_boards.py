"""The test fixtures themselves, held to the properties other tests assume.

A builder that quietly stopped producing the board it claims to would make
every test using it pass for the wrong reason. The padding figures in
``boards.py`` were hand-picked once and were wrong -- one of them closed a
second trio with a member of the seed trio -- so the property they exist for
is asserted here rather than trusted.
"""

from __future__ import annotations

import pytest

from puzzlegen_figures.trio import ATTRIBUTES, check_space, is_trio, trios_in

from boards import (
    GLYPHS,
    INERT_READINGS,
    SPACE,
    TRIO_READINGS,
    WIDER_READINGS,
    context,
    entity,
    make_puzzle,
    whole_space,
)


def readings_of(puzzle):
    return [
        {attribute: tile[attribute] for attribute in ATTRIBUTES}
        for tile in puzzle.payload["tiles"]
    ]


class TestTheSeedTrio:
    def test_it_is_a_trio(self):
        assert is_trio(list(TRIO_READINGS))

    def test_it_varies_on_shape_alone(self):
        assert len({r["shape"] for r in TRIO_READINGS}) == 3
        for attribute in ("fill", "count", "colour"):
            assert len({r[attribute] for r in TRIO_READINGS}) == 1


class TestThePadding:
    def test_the_nine_tile_board_holds_exactly_one_trio(self):
        puzzle = make_puzzle()
        assert len(puzzle.payload["tiles"]) == 9
        assert trios_in(readings_of(puzzle)) == ((0, 1, 2),)

    def test_the_twelve_tile_board_holds_exactly_one_trio(self):
        puzzle = make_puzzle([*TRIO_READINGS, *INERT_READINGS, *WIDER_READINGS])
        assert len(puzzle.payload["tiles"]) == 12
        assert trios_in(readings_of(puzzle)) == ((0, 1, 2),)

    def test_the_padding_holds_no_trio_on_its_own(self):
        assert trios_in(list(INERT_READINGS)) == ()

    def test_every_padding_figure_is_distinct_from_the_seed(self):
        seed = {tuple(r.values()) for r in TRIO_READINGS}
        assert not seed & {tuple(r.values()) for r in INERT_READINGS}


class TestTheBuilders:
    def test_the_whole_space_is_the_providers_default_size(self):
        assert len(whole_space()) == 81

    def test_the_whole_space_passes_the_games_own_space_check(self):
        readings = [
            {attribute: view.properties[attribute] for attribute in ATTRIBUTES}
            for view in whole_space()
        ]
        assert check_space(readings)

    def test_every_shape_and_fill_pair_has_a_glyph(self):
        for shape in SPACE["shape"]:
            for fill in SPACE["fill"]:
                assert GLYPHS[(shape, fill)]

    def test_the_glyphs_are_short_and_distinct(self):
        """A tile is one glyph wide, and two tiles drawn the same are a board
        the player cannot operate."""
        assert len(set(GLYPHS.values())) == len(GLYPHS)
        assert all(len(glyph) == 1 for glyph in GLYPHS.values())

    def test_an_entity_carries_every_attribute_as_a_fact_and_a_membership(self):
        view = entity(TRIO_READINGS[0])
        for attribute in ATTRIBUTES:
            assert attribute in view.properties
        assert len(view.types) == len(view.type_ids) == len(ATTRIBUTES)

    def test_make_puzzle_derives_the_varying_attributes_from_the_trio(self):
        puzzle = make_puzzle()
        assert puzzle.solution["varying"] == ["shape"]

    def test_make_puzzle_can_place_the_trio_anywhere(self):
        puzzle = make_puzzle([*INERT_READINGS, *TRIO_READINGS], trio=(6, 7, 8))
        tiles = [tile["id"] for tile in puzzle.payload["tiles"]]
        assert puzzle.solution["trio"] == sorted(tiles[6:9])

    def test_a_context_carries_the_content_under_the_name_the_game_reads(self):
        from puzzlegen_figures import content as content_module

        built = context(whole_space())
        assert content_module.CANDIDATES in built.content
