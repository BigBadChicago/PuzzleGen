"""The hidden category may not also be on the board as a visible group.

A regression guard for one specific broken board, not a test of the generation
rules in general: coverage-B produced a board whose hidden ask was "find the
word that is also a colour" while one of the four visible groups was
``chromatic color`` and two more were ``pink`` and ``red``. Every individual
group was sound, the four visible ones were disjoint and carried none of each
other's categories, and the board was still unplayable.

Fixtures are borrowed from the generation tests rather than rebuilt, so the
two files cannot drift into disagreeing about what a board is.
"""

from __future__ import annotations

from puzzlegen.games.grouping.descriptor import VISIBLE_GROUPS
from puzzlegen.games.grouping.generate import (
    CATEGORY_LEAK,
    has_no_category_leak,
    plans_for,
)

from .test_grouping_generate import (
    SIZE,
    entity,
    group,
    hidden_group,
    visible_group,
)

HIDDEN = "category:colour"


def ordinary_board() -> tuple[list, object]:
    """Four visible groups and a hidden group borrowing one tile from each.

    Built so the borrowed tiles carry the hidden category in the visible
    groups as well. Real content hands the same entity view to both queries,
    with its full inherited ancestry on it either way; the shared fixtures
    build the two sides separately, which would quietly hide a tile's overlay
    membership from the rule under test.
    """
    borrowed = [(i, 0) for i in range(VISIBLE_GROUPS)]
    borrowed += [(i % VISIBLE_GROUPS, 1) for i in range(SIZE - VISIBLE_GROUPS)]
    lent = {(index, tile) for index, tile in borrowed}
    visible = []
    for index in range(VISIBLE_GROUPS):
        own = f"category:g{index}"
        members = [
            entity(
                f"g{index}_{n}",
                own,
                *((HIDDEN,) if (index, n) in lent else ()),
            )
            for n in range(SIZE)
        ]
        visible.append(group(own, members))
    return visible, hidden_group(borrowed, category=HIDDEN)


def group_entirely_under(category: str, index: int) -> object:
    """A visible group every one of whose members carries ``category``.

    This is ``chromatic color`` standing next to a hidden "is a colour": the
    group's own category is a child of the hidden one, so inherited membership
    puts the hidden category on all five members.
    """
    own = f"category:g{index}"
    return group(
        own,
        [entity(f"g{index}_{n}", own, category) for n in range(SIZE)],
    )


class TestTheRule:
    def test_an_ordinary_board_passes(self):
        visible, hidden = ordinary_board()
        assert has_no_category_leak(hidden, visible)

    def test_borrowed_tiles_alone_are_not_a_leak(self):
        """Every board has tiles carrying the hidden category; that is the game.

        Stated as its own test because the obvious wrong implementation of this
        rule, refusing any visible group that holds a hidden member, refuses
        every board there is.
        """
        visible, hidden = ordinary_board()
        assert any(
            hidden.shared_category_id in member.type_ids
            for group_ in visible
            for member in group_.members
        )
        assert has_no_category_leak(hidden, visible)

    def test_one_stray_tile_carrying_the_hidden_category_is_refused(self):
        """The board that shipped, reduced to its smallest form.

        ``rose`` is in the ``pink`` group and is also a colour word. One tile
        is enough: the hidden ask then has six answers and wants five, and the
        player who names the sixth is not wrong.
        """
        visible, hidden = ordinary_board()
        stray = entity("g1_4", "category:g1", HIDDEN)
        visible[1] = group("category:g1", [*visible[1].members[:-1], stray])
        assert not has_no_category_leak(hidden, visible)

    def test_a_visible_group_below_the_hidden_category_is_refused(self):
        visible, hidden = ordinary_board()
        visible[2] = group_entirely_under(HIDDEN, 2)
        assert not has_no_category_leak(hidden, visible)

    def test_a_visible_group_that_is_the_hidden_category_is_refused(self):
        visible, hidden = ordinary_board()
        visible[1] = group(
            HIDDEN,
            [entity(f"c_{n}", HIDDEN) for n in range(SIZE)],
        )
        assert not has_no_category_leak(hidden, visible)

    def test_a_visible_group_above_the_hidden_category_is_refused(self):
        """The hidden group sits under a visible group's category.

        The mirror of the case that shipped. Reachable only through inherited
        membership on the hidden members, since a visible group whose category
        every hidden member carries would also have to hold tiles in the other
        three groups to be a board, which cross-membership refuses first.
        """
        visible, hidden = ordinary_board()
        above = visible[0].shared_category_id
        hidden = group(
            HIDDEN,
            [
                entity(member.name, *member.type_ids, above)
                for member in hidden.members
            ],
        )
        assert not has_no_category_leak(hidden, visible)

    def test_a_hidden_group_with_no_category_is_not_judged(self):
        """A similarity-formed group has no category to be above or below.

        The rule abstains rather than refusing: a group with no shared
        category id cannot be in a hierarchy relation with anything, and
        refusing it would close off hidden groups formed by similarity.
        """
        visible, hidden = ordinary_board()
        uncategorised = group("", hidden.members)
        assert uncategorised.shared_category_id == ""
        assert has_no_category_leak(uncategorised, visible)


class TestTheSearchRefusesIt:
    def test_a_leaking_combination_is_counted_and_dropped(self):
        visible, hidden = ordinary_board()
        visible[2] = group_entirely_under(HIDDEN, 2)
        rejected: dict[str, int] = {}
        plans = list(
            plans_for(
                hidden,
                visible,
                SIZE,
                enriched=False,
                budget=64,
                rejected=rejected,
            )
        )
        assert plans == []
        assert rejected.get(CATEGORY_LEAK, 0) >= 1

    def test_a_clean_board_still_produces_a_plan(self):
        visible, hidden = ordinary_board()
        rejected: dict[str, int] = {}
        plans = list(
            plans_for(
                hidden,
                visible,
                SIZE,
                enriched=False,
                budget=64,
                rejected=rejected,
            )
        )
        assert plans
        assert CATEGORY_LEAK not in rejected
