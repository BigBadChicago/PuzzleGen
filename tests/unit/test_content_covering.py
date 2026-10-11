"""Coverage is a property of the result set, so the set has to be chosen.

``intersects_taxonomy`` constrains each group on its own: this word also means
something else. ``covering_taxonomy`` constrains the result as a whole: among
everything returned, some whole category of the second taxonomy is present,
spread over enough groups to be usable. No per-group constraint can express
that, and a caller that needs it and cannot say it draws two independent
samples and hopes they meet. On the real snapshot they did not: 199 of 200
candidate second-axis groups were unplaceable against the groups fetched for
the first axis, and the one that survived won every day for a month.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.query import (
    ContentQuery,
    ContentRequirement,
    GroupingMode,
    Operation,
)
from puzzlegen.content.service import _cover_for
from puzzlegen.core.errors import RejectionReason

from .test_content_siblings import TAXONOMY, build, category, entity, family

OVERLAY = "extra"


def overlay(name: str, members: list[str]) -> tuple[list[dict], list[dict]]:
    return (
        [{"key": f"x.{name}", "name": name}],
        [
            {
                "key": f"e.{word}",
                "name": word,
                "categories": [f"x.{name}"],
                "confidence": 0.95,
            }
            for word in members
        ],
    )


def world(repos, tmp_path, parents: dict[str, list[str]], overlays: dict[str, list[str]]):
    """Parents of distinct children, plus overlay categories cutting across.

    ``parents`` maps a parent to its children, each child contributing one
    tile carrying its own name. ``overlays`` maps an overlay category to the
    tiles that also belong to it, which is how a second axis is stored.
    """
    categories = [category("thing")]
    entities: list[dict] = []
    for parent, kids in parents.items():
        categories.append(category(parent, "thing"))
        entities.append(entity(parent, parent))
        family(categories, entities, parent, {kid: [] for kid in kids})

    overlay_categories: list[dict] = []
    overlay_entities: list[dict] = []
    for name, members in overlays.items():
        cats, ents = overlay(name, members)
        overlay_categories += cats
        overlay_entities += ents
    return build(
        repos,
        tmp_path,
        categories,
        entities,
        extra=(overlay_categories, overlay_entities),
    )


def query(**overrides) -> ContentQuery:
    fields = dict(
        operation=Operation.FIND_GROUPS,
        taxonomy=TAXONOMY,
        grouping=GroupingMode.SIBLINGS,
        group_size=3,
        covering_taxonomy=OVERLAY,
        minimum_covered_members=3,
        minimum_covered_spread=3,
        limit=4,
    )
    fields.update(overrides)
    return ContentQuery(**fields)


#: Four parents of three children each. The overlay category "marked" takes
#: one tile from each of three different parents, so covering it needs three
#: groups and no two of them come from the same parent.
PARENTS = {
    "alpha": ["a1", "a2", "a3"],
    "beta": ["b1", "b2", "b3"],
    "gamma": ["g1", "g2", "g3"],
    "delta": ["d1", "d2", "d3"],
}


class TestTheResultCoversWholeGroups:
    def test_a_covered_category_is_present_and_spread(self, repos, tmp_path):
        service = world(
            repos, tmp_path, PARENTS, {"marked": ["a1", "b1", "g1"]}
        )
        result = service.execute(query())

        assert result.satisfied
        assert "covering 1 extra categories" == result.detail
        tiles = {m.entity_id for group in result.groups for m in group.members}
        marked = {"entity:a1", "entity:b1", "entity:g1"}
        assert marked <= tiles

        # Spread, not merely presence: each marked tile in a different group,
        # because a caller borrowing one tile per group cannot use three in
        # two groups.
        homes = {
            next(
                index
                for index, group in enumerate(result.groups)
                if any(m.entity_id == tile for m in group.members)
            )
            for tile in marked
        }
        assert len(homes) >= 3

    def test_an_uncoverable_category_is_reported_at_resolve_time(
        self, repos, tmp_path
    ):
        """Two of the three members do not exist as tiles at all.

        The honest answer is that the request cannot be met, said once, here.
        A result that quietly returned groups anyway is what produced a month
        of identical puzzles and a content investigation that found nothing
        wrong with the content.
        """
        service = world(repos, tmp_path, PARENTS, {"marked": ["a1"]})
        result = service.execute(query())

        assert not result.satisfied
        assert result.rejected[RejectionReason.COVERAGE_NOT_MET.value] == 1
        assert "covered 0 extra categories, 1 required" in result.detail

    def test_concentrated_members_do_not_count_as_covered(self, repos, tmp_path):
        """All three marked tiles under one parent.

        They are present, and present is not enough: the groups holding them
        are variations of one parent, and a board needs one tile from each of
        three different groups.
        """
        service = world(
            repos, tmp_path, PARENTS, {"marked": ["a1", "a2", "a3"]}
        )
        result = service.execute(query())

        assert not result.satisfied
        assert result.rejected[RejectionReason.COVERAGE_NOT_MET.value] == 1

    def test_several_categories_are_covered_when_the_limit_allows(
        self, repos, tmp_path
    ):
        """The point of a second axis is a choice among readings.

        One covered category is the floor a single puzzle needs; a day with
        one has no choice, which is the same board every day by another route.
        """
        service = world(
            repos,
            tmp_path,
            PARENTS,
            {
                "marked": ["a1", "b1", "g1"],
                "other": ["a2", "b2", "d1"],
            },
        )
        result = service.execute(query(limit=8))

        assert result.satisfied
        assert result.detail == "covering 2 extra categories"

    def test_the_unordered_remainder_still_follows(self, repos, tmp_path):
        """Coverage adds groups to the front; it does not shrink the result.

        A caller asking for a pool of four and getting the three that cover
        its axis has lost the partners the fourth slot was for.
        """
        service = world(
            repos, tmp_path, PARENTS, {"marked": ["a1", "b1", "g1"]}
        )
        result = service.execute(query(limit=4))

        assert len(result.groups) == 4

    def test_a_query_with_no_covering_taxonomy_is_unchanged(self, repos, tmp_path):
        service = world(
            repos, tmp_path, PARENTS, {"marked": ["a1", "b1", "g1"]}
        )
        plain = service.execute(
            query(
                covering_taxonomy=None,
                minimum_covered_members=None,
                minimum_covered_spread=1,
            )
        )
        assert plain.satisfied
        assert plain.detail == ""
        assert len(plain.groups) == 4


class TestAnUnsatisfiedResultIsAnUnmetRequirement:
    """The verdict has to survive the trip back to the engine.

    ``COVERAGE_NOT_MET`` leaves the harvest's filler groups in the result,
    because a caller asking for a pool of 120 still wants 120 if it decides to
    proceed. A requirement that counted only the groups therefore read that
    result as met and handed an uncovered pool to generation, which is the
    silent fallback this whole change exists to end.
    """

    def requirement(self, **overrides) -> ContentRequirement:
        return ContentRequirement(name="pool", query=query(**overrides), minimum=1)

    def test_a_coverage_failure_is_unmet_despite_the_filler_groups(
        self, repos, tmp_path
    ):
        service = world(repos, tmp_path, PARENTS, {"marked": ["a1"]})
        result = service.execute(query())

        assert result.groups
        assert not self.requirement().is_met(result)

    def test_a_covered_result_is_met(self, repos, tmp_path):
        service = world(repos, tmp_path, PARENTS, {"marked": ["a1", "b1", "g1"]})
        result = service.execute(query())

        assert self.requirement().is_met(result)

    def test_an_optional_requirement_still_passes(self, repos, tmp_path):
        service = world(repos, tmp_path, PARENTS, {"marked": ["a1"]})
        result = service.execute(query())
        optional = ContentRequirement(
            name="pool", query=query(), minimum=1, optional=True
        )

        assert optional.is_met(result)


class TestCoverForChoosesUsableCovers:
    def test_spread_before_members(self):
        """One member per group first, so the cover spreads as wide as it can.

        Taking everything each group offers reaches the member count in two
        groups and then cannot spread, which is a cover no borrower can use.
        """
        by_group = {0: {"a", "b", "c"}, 1: {"d"}, 2: {"e"}}
        assert _cover_for(by_group, 3, 3) == (0, 1, 2)

    def test_members_are_filled_from_groups_holding_more(self):
        by_group = {0: {"a", "b"}, 1: {"c"}}
        # Two groups is all the spread there is; the third member comes from
        # the group holding two.
        assert _cover_for(by_group, 3, 2) == (0, 1)

    def test_none_when_the_spread_is_impossible(self):
        assert _cover_for({0: {"a", "b", "c"}}, 3, 3) is None

    def test_none_when_the_members_are_not_there(self):
        assert _cover_for({0: {"a"}, 1: {"b"}}, 3, 2) is None

    def test_the_ceiling_concentrates_the_surplus(self):
        """Four members, five groups holding one each, a ceiling of three.

        Without the ceiling this cover opens a group per member and comes back
        four wide, which is the widest spread the content allows and one no
        caller with three groups can borrow from. The fourth member has to
        come from a group already in the cover instead.
        """
        by_group = {0: {"a", "b"}, 1: {"c"}, 2: {"d"}, 3: {"e"}, 4: {"f"}}
        unbounded = _cover_for(by_group, 4, 3)
        assert unbounded is not None and len(unbounded) == 4
        cover = _cover_for(by_group, 4, 3, None, 3)
        # Group 0 is the only one with a second member to give, so it is in
        # the cover and two of the single holders join it.
        assert cover is not None
        assert len(cover) == 3
        assert 0 in cover

    def test_the_ceiling_does_not_commit_to_the_biggest_holders(self):
        """Four groups holding the same four members, and one holding a fifth.

        Five members over four groups is possible and only by using the
        small one. Taking the biggest holders first fills the ceiling with
        groups that overlap and then reports that the content cannot do it,
        which under a ceiling is a wrong answer rather than a worse cover.
        """
        by_group = {
            0: {"a", "b", "c", "d"},
            1: {"a", "b", "c", "d"},
            2: {"a", "b", "c", "d"},
            3: {"a", "b", "c", "d"},
            4: {"e"},
        }
        cover = _cover_for(by_group, 5, 4, None, 4)
        assert cover is not None
        assert len(cover) == 4
        assert 4 in cover

    def test_a_cover_needs_a_member_of_its_own_for_each_group(self):
        """Three groups, two of which hold nothing but the same member.

        Every group in a cover gives one member up, so a group whose only
        member another group has already given is not in the cover. The union
        is large enough and the cover is still impossible.
        """
        by_group = {0: {"a"}, 1: {"a"}, 2: {"b", "c"}}
        assert _cover_for(by_group, 3, 3, None, 3) is None

    def test_a_group_whose_member_can_move_is_still_usable(self):
        """The same shape with the shared member available elsewhere.

        Group 2 holds ``a`` as well, so it can take ``b`` and leave ``a`` to
        group 0. Assigning the smallest free member group by group gets this
        wrong, which is why the check searches rather than assigns.
        """
        by_group = {0: {"a"}, 1: {"b"}, 2: {"a", "b", "c"}}
        cover = _cover_for(by_group, 3, 3, None, 3)
        assert cover == (0, 1, 2)

    def test_none_when_the_members_need_more_groups_than_the_ceiling(self):
        """Every group holds one member, so four members need four groups.

        The honest answer is that this content cannot serve the request, given
        at resolve time rather than as a cover the caller cannot use.
        """
        by_group = {0: {"a"}, 1: {"b"}, 2: {"c"}, 3: {"d"}}
        assert _cover_for(by_group, 4, 3, None, 3) is None

    def test_the_ceiling_does_not_narrow_a_cover_that_fits(self):
        """A ceiling is a bound, not a target."""
        by_group = {0: {"a"}, 1: {"b"}, 2: {"c"}}
        assert _cover_for(by_group, 3, 3, None, 4) == (0, 1, 2)

    def test_holders_under_one_parent_do_not_crowd_out_others(self):
        """Distinct defining categories first.

        Groups under one parent share tiles, so a cover made of three of them
        gives a caller one combination and no second try. The parent-blind
        order picked exactly those three, because they each held one member
        and sorted first.
        """
        by_group = {0: {"a"}, 1: {"b"}, 2: {"c"}, 3: {"d"}}
        parents = {0: "p", 1: "p", 2: "p", 3: "q"}
        cover = _cover_for(by_group, 2, 2, parents)
        assert cover is not None
        assert len({parents[position] for position in cover}) == 2


class TestTheQueryRefusesHalfARequest:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"minimum_covered_members": 3},
            {"minimum_covered_spread": 2},
            {"maximum_covered_spread": 2},
            {"minimum_covered_categories": 2},
        ],
    )
    def test_covering_numbers_need_a_covering_taxonomy(self, overrides):
        """Silently ignored, a covering number is a guarantee that is not there."""
        with pytest.raises(ValueError, match="without covering_taxonomy"):
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                taxonomy=TAXONOMY,
                group_size=3,
                **overrides,
            )

    def test_spread_cannot_exceed_members(self):
        with pytest.raises(ValueError, match="minimum_covered_spread exceeds"):
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                taxonomy=TAXONOMY,
                group_size=3,
                covering_taxonomy=OVERLAY,
                minimum_covered_members=3,
                minimum_covered_spread=4,
            )

    def test_the_ceiling_cannot_sit_below_the_floor(self):
        """Two bounds that cannot both hold is a mistake, not a narrow range."""
        with pytest.raises(ValueError, match="maximum_covered_spread is below"):
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                taxonomy=TAXONOMY,
                group_size=5,
                covering_taxonomy=OVERLAY,
                minimum_covered_members=5,
                minimum_covered_spread=4,
                maximum_covered_spread=3,
            )

    def test_a_covering_request_needs_a_member_count_from_somewhere(self):
        with pytest.raises(ValueError, match="minimum_covered_members or group_size"):
            ContentQuery(
                operation=Operation.FIND_PAIRS,
                taxonomy=TAXONOMY,
                covering_taxonomy=OVERLAY,
            )

    @pytest.mark.parametrize(
        "operation",
        [
            Operation.FIND_ENTITIES,
            Operation.FIND_SIBLINGS,
            Operation.FIND_RELATIONSHIP_PATTERNS,
        ],
    )
    def test_an_operation_that_cannot_cover_refuses_the_request(self, operation):
        """Only a handler that selects its whole result can honour coverage.

        Accepted on an operation that ignores it, a covering request comes
        back unconstrained and marked satisfied, which is the exact failure
        the request exists to prevent rather than a milder version of it.
        """
        with pytest.raises(ValueError, match="not honoured by"):
            ContentQuery(
                operation=operation,
                taxonomy=TAXONOMY,
                group_size=3,
                covering_taxonomy=OVERLAY,
            )

    def test_group_size_supplies_the_member_count(self):
        query = ContentQuery(
            operation=Operation.FIND_GROUPS,
            taxonomy=TAXONOMY,
            group_size=5,
            covering_taxonomy=OVERLAY,
        )
        assert query.minimum_covered_members is None
        assert query.minimum_covered_spread == 1
