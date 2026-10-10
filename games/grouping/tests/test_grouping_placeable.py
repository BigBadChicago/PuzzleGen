"""Hidden groups are recomposed from the members the board can hold.

The overlay query offers particular subsets of each overlay category, chosen
with no knowledge of which tiles the visible pool ended up with. The pool's
covering requirement guarantees that some ``group_size`` members of a covered
category are on the board; it cannot guarantee the offered subset is that one.
Measured on build A2 before this existed: 195 of 200 offered subsets were
unplaceable against a pool that covered nine overlay categories.

Two sizes of claim are tested here. That recomposition finds the placeable
subset, and that it refuses a subset the board could hold but no board could
use, because a hidden group has to give a tile to each of the four visible
groups and one drawn from three of them is findable by counting.
"""

from __future__ import annotations

from puzzlegen.content.query import EntityView, GroupView
from puzzlegen_grouping.descriptor import VISIBLE_GROUPS
from puzzlegen_grouping.generate import member_ids, placeable_hidden_groups

SIZE = 5


def entity(name: str) -> EntityView:
    return EntityView(entity_id=f"entity:{name}", name=name)


def group(category: str, names: list[str], *, secondary: str | None = None) -> GroupView:
    return GroupView(
        members=tuple(entity(name) for name in names),
        shared_category=category,
        shared_category_id=f"category:{category}",
        secondary_category=secondary,
        secondary_category_id=f"category:{secondary}" if secondary else None,
    )


def pool() -> list[GroupView]:
    """Four visible groups of five tiles, named by their group."""
    return [
        group(f"g{index}", [f"g{index}_{n}" for n in range(SIZE)])
        for index in range(VISIBLE_GROUPS)
    ]


def names_of(view: GroupView) -> set[str]:
    return {member.name for member in view.members}


class TestRecomposition:
    def test_the_placeable_subset_is_assembled(self):
        """The offered subset is unplaceable; a placeable one exists.

        The category's members include five tiles the pool holds, and every
        subset the query offered mixes them with tiles on no group at all.
        """
        placeable = [f"g{index}_0" for index in range(VISIBLE_GROUPS)] + ["g0_1"]
        # Split across two unplaceable subsets, which is the real case: no
        # single subset the query offered works, and the members of one that
        # would are spread across several of them.
        offered = [
            (group("overlay", ["absent1", "absent2", *placeable[:3]]), False),
            (group("overlay", ["absent3", "absent4", *placeable[3:]]), False),
        ]
        result = placeable_hidden_groups(offered, pool(), SIZE)

        recomposed = [
            view for view, _enriched in result if names_of(view) == set(placeable)
        ]
        assert len(recomposed) == 1
        assert recomposed[0].shared_category_id == "category:overlay"

    def test_the_offered_subsets_are_kept(self):
        """Recomposition adds candidates; it never removes one.

        A subset the query chose for a reason the game cannot see is still
        material, and all of them are only candidates.
        """
        offered = [(group("overlay", ["absent1", "absent2"]), False)]
        result = placeable_hidden_groups(offered, pool(), SIZE)
        assert [names_of(view) for view, _ in result] == [{"absent1", "absent2"}]

    def test_a_concentrated_subset_is_refused(self):
        """Five placeable members, all in one visible group.

        The board could hold them and no board could use them: every visible
        group has to give up a tile. Offering it would spend search budget
        that is divided among the candidates, so one unusable candidate makes
        every usable one poorer.
        """
        offered = [
            (group("overlay", [f"g0_{n}" for n in range(SIZE)] + ["absent"]), False)
        ]
        result = placeable_hidden_groups(offered, pool(), SIZE)
        assert len(result) == 1
        assert names_of(result[0][0]) == {f"g0_{n}" for n in range(SIZE)} | {"absent"}

    def test_the_enriched_view_is_the_source_when_it_covers_the_choice(self):
        """Its secondary axis is the only thing distinguishing the views.

        Every chosen member comes from the enriched subset, so the axis is
        true of all of them and the board can be hinted as two-axis.
        """
        spread = [f"g{index}_0" for index in range(VISIBLE_GROUPS)]
        offered = [
            (group("overlay", ["absent1", *spread], secondary="kitchen"), True),
            (group("overlay", ["absent2", *spread[2:]]), False),
        ]
        result = placeable_hidden_groups(offered, pool(), VISIBLE_GROUPS)

        recomposed = [
            (view, enriched)
            for view, enriched in result
            if names_of(view) == set(spread)
        ]
        assert len(recomposed) == 1
        view, enriched = recomposed[0]
        assert enriched is True
        assert view.secondary_category == "kitchen"

    def test_an_axis_the_chosen_members_do_not_share_is_dropped(self):
        """Half the choice comes from the plain subset.

        The enriched view says its members are also kitchen things; it says
        nothing about ``g2_0`` and ``g3_0``, which the pool contributed from
        the other subset. Carrying the axis anyway would show a player "these
        also have to do with kitchens" over two tiles that do not, which is
        worse than offering no second hint. The recomposed group is still
        offered; only the claim about it is withdrawn.
        """
        spread = [f"g{index}_0" for index in range(VISIBLE_GROUPS)]
        offered = [
            (group("overlay", ["absent1", *spread[:2]], secondary="kitchen"), True),
            (group("overlay", ["absent2", *spread[2:]]), False),
        ]
        result = placeable_hidden_groups(offered, pool(), VISIBLE_GROUPS)

        recomposed = [
            (view, enriched)
            for view, enriched in result
            if names_of(view) == set(spread)
        ]
        assert len(recomposed) == 1
        view, enriched = recomposed[0]
        assert enriched is False
        assert view.secondary_category is None

    def test_duplicates_are_collapsed(self):
        """Recomposing a category whose offered subset was already placeable."""
        spread = [f"g{index}_0" for index in range(VISIBLE_GROUPS)]
        offered = [(group("overlay", spread), False)]
        result = placeable_hidden_groups(offered, pool(), VISIBLE_GROUPS)
        assert len(result) == 1
        assert member_ids(result[0][0]) == member_ids(offered[0][0])

    def test_members_the_pool_cannot_place_are_dropped_not_substituted(self):
        """A category with too few placeable members yields nothing extra."""
        offered = [(group("overlay", ["g0_0", "absent1", "absent2"]), False)]
        result = placeable_hidden_groups(offered, pool(), SIZE)
        assert len(result) == 1
