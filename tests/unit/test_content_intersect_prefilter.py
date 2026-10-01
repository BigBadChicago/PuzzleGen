"""A requirement on every member is applied to the pool, not to each subset.

Subsets are enumerated in a fixed order and the result limit fills with
whatever categories have valid subsets early. Checking the second taxonomy per
subset therefore starves a category whose first valid subset is hundreds of
combinations in: on a real day the game was handed the alphabetically first
subsets of each overlay category, most containing words the lexicon does not
have, and the category whose valid subset came late was never offered at all.
"""

from __future__ import annotations

import itertools

import pytest

from puzzlegen.content.query import ContentQuery, GroupingMode, Operation
from puzzlegen.core.errors import RejectionReason

from .test_content_siblings import TAXONOMY, build, category, entity, write

OTHER = "extra"


def world(tmp_path, repos, *, present, absent):
    """One category of words, some also in a second taxonomy and some not.

    The absent words sort first, so a combination enumerator meets invalid
    subsets before it meets a valid one.
    """
    categories = [category("kinds")]
    entities = [
        entity(word, "kinds") for word in [*absent, *present]
    ]
    extra = (
        [{"key": "x.t", "name": "tagged"}],
        [
            {"key": f"e.{w}", "name": w, "categories": ["x.t"], "confidence": 0.95}
            for w in present
        ],
    )
    return build(repos, tmp_path, categories, entities, extra=extra)


def query(**overrides) -> ContentQuery:
    fields = dict(
        operation=Operation.FIND_GROUPS,
        taxonomy=TAXONOMY,
        group_size=5,
        intersects_taxonomy=OTHER,
        minimum_intersecting_members=5,
        limit=3,
    )
    fields.update(overrides)
    return ContentQuery(**fields)


ABSENT = [f"a{n}" for n in range(1, 9)]
PRESENT = [f"z{n}" for n in range(1, 7)]


class TestEveryMemberMustIntersect:
    def test_a_late_valid_subset_is_found(self, repos, tmp_path):
        """Six valid words sort after eight invalid ones.

        Enumerated as a whole the first valid subset is thousands of
        combinations in, so a small limit fills before it is reached.
        """
        service = world(tmp_path, repos, present=PRESENT, absent=ABSENT)

        result = service.execute(query())

        assert result.groups
        for group in result.groups:
            assert {m.name for m in group.members} <= set(PRESENT)

    def test_every_offered_subset_is_made_only_of_valid_words(self, repos, tmp_path):
        service = world(tmp_path, repos, present=PRESENT, absent=ABSENT)

        result = service.execute(query(limit=10))

        assert len(result.groups) == 6
        assert all(len(g.members) == 5 for g in result.groups)
        assert all(
            not ({m.name for m in g.members} & set(ABSENT)) for g in result.groups
        )

    def test_the_words_left_out_are_counted(self, repos, tmp_path):
        service = world(tmp_path, repos, present=PRESENT, absent=ABSENT)

        result = service.execute(query())

        assert result.rejected[RejectionReason.NOT_IN_REQUIRED_TAXONOMY.value] == len(ABSENT)

    def test_fewer_valid_words_than_a_group_is_unsatisfied(self, repos, tmp_path):
        service = world(tmp_path, repos, present=PRESENT[:4], absent=ABSENT)

        result = service.execute(query())

        assert not result.groups
        assert not result.satisfied

    def test_a_category_late_in_order_is_not_starved(self, repos, tmp_path):
        """The real failure: two categories share a limit, and only the one
        whose valid subsets come first used to get any of it."""
        categories = [category("early"), category("late")]
        entities = [entity(f"e{n}", "early") for n in range(1, 9)]
        entities += [entity(word, "late") for word in [*ABSENT, *PRESENT]]
        extra = (
            [{"key": "x.t", "name": "tagged"}],
            [
                {"key": f"e.{w}", "name": w, "categories": ["x.t"], "confidence": 0.95}
                for w in [*(f"e{n}" for n in range(1, 9)), *PRESENT]
            ],
        )
        service = build(repos, tmp_path, categories, entities, extra=extra)

        result = service.execute(query(limit=12))

        assert {"early", "late"} <= {g.shared_category for g in result.groups}


class TestAtLeastOneIsUnchanged:
    """A requirement on some members is a property of the group, not the word."""

    def test_the_default_minimum_still_filters_per_group(self, repos, tmp_path):
        service = world(tmp_path, repos, present=PRESENT, absent=ABSENT)

        result = service.execute(
            query(minimum_intersecting_members=1, limit=500)
        )

        assert any(
            {m.name for m in g.members} & set(ABSENT) for g in result.groups
        )

    def test_a_minimum_below_the_size_does_not_shrink_the_pool(self, repos, tmp_path):
        service = world(tmp_path, repos, present=PRESENT, absent=ABSENT)

        result = service.execute(query(minimum_intersecting_members=4, limit=500))

        assert result.rejected.get(RejectionReason.NOT_IN_REQUIRED_TAXONOMY.value, 0) != len(ABSENT)


class TestTheGateStillRuns:
    def test_a_subset_with_an_invalid_word_is_refused_by_the_group_gate_too(
        self, repos, tmp_path
    ):
        """Defence in depth: the pool filter is a performance property."""
        service = world(tmp_path, repos, present=PRESENT, absent=ABSENT)
        entities = {e.canonical_name: e for e in service._repos.entities.active()}
        members = [entities[n] for n in ["a1", "z1", "z2", "z3", "z4"]]
        memberships = {m.id: service._membership_ids(m.id) for m in members}
        index = service.taxonomy(TAXONOMY)
        q = query()
        views = {m.id: service._view(m, q, None, taxonomy=index) for m in members}
        rejected: dict[str, int] = {}

        group = service._assess_group(
            members, views, memberships, service._strategy(q), {}, q, rejected, index
        )

        assert group is None
        assert rejected.get(RejectionReason.NOT_IN_REQUIRED_TAXONOMY.value, 0) == 1

    @pytest.mark.parametrize("mode", list(GroupingMode))
    def test_both_groupings_apply_it(self, repos, tmp_path, mode):
        service = world(tmp_path, repos, present=PRESENT, absent=ABSENT)

        result = service.execute(query(grouping=mode, limit=10))

        assert all(
            not ({m.name for m in g.members} & set(ABSENT)) for g in result.groups
        )


class TestCombinationsSpreadWithinACategory:
    """Round robin fixed the wrong half of the problem.

    Across categories it stopped a limit filling with one category's variants.
    Within a category ``itertools.combinations`` still varies only the last
    member, so the first N offers of a large category all share their earliest
    members and no two of them are disjoint. A game needing four disjoint
    groups then has nothing to work with, which is the same failure round robin
    was introduced to fix, one level down.
    """

    def big_category(self, repos, tmp_path, count=20):
        words = [f"w{n:02d}" for n in range(count)]
        categories = [category("kinds")]
        entities = [entity(word, "kinds") for word in words]
        return build(repos, tmp_path, categories, entities), words

    def offered(self, service, limit, size=5):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                taxonomy=TAXONOMY,
                group_size=size,
                limit=limit,
            )
        )
        return [frozenset(m.name for m in g.members) for g in result.groups]

    def test_two_early_offers_are_disjoint(self, repos, tmp_path):
        """The property the game needs and the old order could not give."""
        service, _ = self.big_category(repos, tmp_path)

        groups = self.offered(service, 10)

        assert any(
            not (first & second) for first, second in itertools.combinations(groups, 2)
        )

    def test_four_early_offers_are_mutually_disjoint(self, repos, tmp_path):
        """A board needs four, not two."""
        service, _ = self.big_category(repos, tmp_path)

        groups = self.offered(service, 20)

        assert any(
            all(not (a & b) for a, b in itertools.combinations(four, 2))
            for four in itertools.combinations(groups, 4)
        )

    def test_no_single_word_is_in_every_early_offer(self, repos, tmp_path):
        service, words = self.big_category(repos, tmp_path)

        groups = self.offered(service, 10)

        assert not any(all(word in group for group in groups) for word in words)

    def test_the_whole_enumeration_is_still_reachable(self, repos, tmp_path):
        """Spreading reorders; it withholds nothing. Eight members, groups of
        five, is 56 subsets and all 56 are still offered."""
        service, _ = self.big_category(repos, tmp_path, count=8)

        groups = self.offered(service, 500)

        assert len(groups) == 56

    def test_the_offers_are_distinct_and_the_right_size(self, repos, tmp_path):
        service, _ = self.big_category(repos, tmp_path)

        groups = self.offered(service, 40)

        assert len(set(groups)) == len(groups)
        assert all(len(group) == 5 for group in groups)

    def test_results_are_deterministic(self, repos, tmp_path):
        service, _ = self.big_category(repos, tmp_path)

        assert self.offered(service, 20) == self.offered(service, 20)
