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


def wide_pool(count: int) -> list[GroupView]:
    """``count`` disjoint visible groups, which is more than a board has."""
    return [
        group(f"g{index}", [f"g{index}_{n}" for n in range(SIZE)])
        for index in range(count)
    ]


class TestTheSubsetIsBorrowable:
    """A hidden group gives one tile to each of four visible groups.

    So for any size above four, some of its members have to share a visible
    group. Recomposition used to maximise spread, which is the opposite
    requirement: a subset sitting in five groups cannot be borrowed from four,
    and the combination search could only spend its budget discovering that.
    Measured on build A2 at size 5, six of the fourteen overlay categories had
    members four disjoint groups could hold and two of the six ever reached a
    board.
    """

    def test_a_subset_wider_than_the_board_is_narrowed_to_four_groups(self):
        """One member in each of six groups, and a second in one of them.

        The widest spread is six groups, which no board can borrow from. The
        subset has to come from four, so the surplus member is what makes the
        count: four groups giving one tile each and one giving two.
        """
        offered = [
            (group("overlay", [f"g{index}_0" for index in range(6)] + ["g0_1"]), False)
        ]
        result = placeable_hidden_groups(offered, wide_pool(6), SIZE)

        recomposed = [view for view, _ in result if names_of(view) != {
            *(f"g{index}_0" for index in range(6)), "g0_1"
        }]
        assert len(recomposed) == 1
        homes = {name.split("_")[0] for name in names_of(recomposed[0])}
        assert len(homes) == VISIBLE_GROUPS
        assert len(recomposed[0].members) == SIZE

    def test_a_quad_failing_cross_membership_is_passed_over(self):
        """``g0`` carries ``g1``'s category, so those two cannot share a board.

        The first four groups hold the category's members and are the quad the
        ranking reaches first. A fifth group holds one too, and the subset has
        to be built on that one instead. A quad this check skips costs one more
        iteration here; a subset built on it costs the category its whole share
        of the candidate budget, spent rediscovering the refusal.
        """
        leaky = GroupView(
            members=(
                EntityView(
                    entity_id="entity:g0_0",
                    name="g0_0",
                    type_ids=("category:g0", "category:g1"),
                ),
                entity("g0_1"),
            ),
            shared_category="g0",
            shared_category_id="category:g0",
        )
        pool_views = [leaky, *wide_pool(5)[1:]]
        offered = [(group("overlay", ["g0_0", "g1_0", "g2_0", "g3_0", "g4_0"]), False)]
        result = placeable_hidden_groups(offered, pool_views, VISIBLE_GROUPS)

        recomposed = [
            names_of(view)
            for view, _ in result
            if names_of(view) != {"g0_0", "g1_0", "g2_0", "g3_0", "g4_0"}
        ]
        assert len(recomposed) == 1
        # Either of the two is sound; what matters is that the quad holding
        # both g0 and g1 was not the one chosen.
        assert not {"g0_0", "g1_0"} <= recomposed[0]
        assert "g4_0" in recomposed[0]

    def test_a_category_only_an_unusable_quad_can_hold_recomposes_nothing(self):
        """The same violation with no fifth group to fall back on.

        Four groups hold the members, two of them cannot share a board, and
        there is nothing else: the honest answer is that this category has no
        board today, given during recomposition rather than discovered by the
        search.
        """
        leaky = GroupView(
            members=(
                EntityView(
                    entity_id="entity:g0_0",
                    name="g0_0",
                    type_ids=("category:g0", "category:g1"),
                ),
                entity("g0_1"),
            ),
            shared_category="g0",
            shared_category_id="category:g0",
        )
        pool_views = [leaky, *wide_pool(VISIBLE_GROUPS)[1:]]
        offered = [(group("overlay", ["g0_0", "g1_0", "g2_0", "g3_0"]), False)]
        result = placeable_hidden_groups(offered, pool_views, VISIBLE_GROUPS)
        assert [names_of(view) for view, _ in result] == [
            {"g0_0", "g1_0", "g2_0", "g3_0"}
        ]

    def test_a_pool_with_no_four_disjoint_groups_recomposes_nothing(self):
        """Every group shares a tile with every other.

        There is no board here at all, and saying so during recomposition is
        cheaper than offering a candidate the search has to refuse.
        """
        shared = [
            group(f"g{index}", ["common", f"g{index}_1", f"g{index}_2"])
            for index in range(6)
        ]
        offered = [(group("overlay", ["common", "g1_1", "g2_1", "g3_1"]), False)]
        result = placeable_hidden_groups(offered, shared, VISIBLE_GROUPS)
        assert len(result) == 1
        assert names_of(result[0][0]) == {"common", "g1_1", "g2_1", "g3_1"}
    def test_a_quad_that_leaks_the_hidden_category_is_passed_over(self):
        """A tile outside the hidden group carries the hidden category.

        ``g1_1`` is a colour word sitting in a visible group, so a board built
        on ``g1`` has a "which of these are colours" ask with more answers than
        the hidden group has tiles. The same rule the search applies, applied
        while there is still another quad to choose.
        """
        colourful = GroupView(
            members=(
                entity("g1_0"),
                EntityView(
                    entity_id="entity:g1_1",
                    name="g1_1",
                    type_ids=("category:g1", "category:overlay"),
                ),
            ),
            shared_category="g1",
            shared_category_id="category:g1",
        )
        pool_views = [wide_pool(5)[0], colourful, *wide_pool(5)[2:]]
        offered = [(group("overlay", ["g0_0", "g1_0", "g2_0", "g3_0", "g4_0"]), False)]
        result = placeable_hidden_groups(offered, pool_views, VISIBLE_GROUPS)

        recomposed = [
            names_of(view)
            for view, _ in result
            if names_of(view) != {"g0_0", "g1_0", "g2_0", "g3_0", "g4_0"}
        ]
        assert len(recomposed) == 1
        assert "g1_0" not in recomposed[0]
