"""Groups in a partition need labels that do not mean the same thing.

Tiles carry their whole ancestry, so a group is almost never justified by one
category. A rule that only compared single justifications let ten craft tiles
be split into two arbitrary groups of five in 126 ways, each justified by
``{craft, vehicle}``, and a board that can be solved 126 ways is not a puzzle.
"""

from __future__ import annotations

import itertools
import time

from puzzlegen.games.grouping.verify import (
    group_validator,
    partition_validator,
    universal_categories,
)


def tile(name: str, *categories: str) -> tuple[str, frozenset[str]]:
    return name, frozenset((name, *categories))


def board(*groups: list[tuple[str, frozenset[str]]]):
    memberships = {name: cats for group in groups for name, cats in group}
    return memberships, universal_categories(memberships)


def craft_board():
    """Five airplane kinds, five boat kinds, five songbirds, five drums."""
    planes = [tile(f"p{n}", "airplane", "craft", "vehicle") for n in range(5)]
    boats = [tile(f"b{n}", "boat", "vessel", "craft", "vehicle") for n in range(5)]
    birds = [tile(f"o{n}", "oscine", "bird") for n in range(5)]
    drums = [tile(f"d{n}", "percussion") for n in range(5)]
    return planes, boats, birds, drums


def partition(*groups):
    return tuple(tuple(g) for g in groups)


class TestTheIntendedPartition:
    def test_it_is_valid(self):
        planes, boats, birds, drums = craft_board()
        memberships, universal = board(planes, boats, birds, drums)
        names = [tuple(n for n, _ in g) for g in (planes, boats, birds, drums)]

        assert partition_validator(memberships, universal)(partition(*names))

    def test_each_group_is_a_valid_group_too(self):
        planes, boats, birds, drums = craft_board()
        memberships, universal = board(planes, boats, birds, drums)
        valid = group_validator(memberships, universal)

        assert all(valid(tuple(n for n, _ in g)) for g in (planes, boats, birds, drums))


class TestTheCraftSplit:
    def split(self, planes_in_first: int):
        planes, boats, birds, drums = craft_board()
        memberships, universal = board(planes, boats, birds, drums)
        p = [n for n, _ in planes]
        b = [n for n, _ in boats]
        first = tuple(p[:planes_in_first] + b[: 5 - planes_in_first])
        second = tuple(p[planes_in_first:] + b[5 - planes_in_first :])
        rest = (
            tuple(n for n, _ in birds),
            tuple(n for n, _ in drums),
        )
        return memberships, universal, partition(first, second, *rest)

    def test_a_mixed_split_is_not_a_solution(self):
        """Each half is justified only by categories the other half shares."""
        memberships, universal, mixed = self.split(3)

        assert not partition_validator(memberships, universal)(mixed)

    def test_every_mixed_split_is_refused(self):
        for planes_in_first in (1, 2, 3, 4):
            memberships, universal, mixed = self.split(planes_in_first)
            assert not partition_validator(memberships, universal)(mixed), planes_in_first

    def test_the_number_of_ways_to_split_is_the_number_that_was_reported(self):
        """126 is C(10,5) over two: the figure the real pipeline printed."""
        assert len(list(itertools.combinations(range(10), 5))) // 2 == 126


class TestWhatStaysValid:
    def test_groups_with_their_own_category_are_valid_despite_a_shared_ancestor(self):
        a = [tile(f"a{n}", "mammal", "animal") for n in range(3)]
        b = [tile(f"b{n}", "bird", "animal") for n in range(3)]
        c = [tile(f"c{n}", "tool") for n in range(3)]
        memberships, universal = board(a, b, c)
        names = [tuple(n for n, _ in g) for g in (a, b, c)]

        assert partition_validator(memberships, universal)(partition(*names))

    def test_the_same_single_category_twice_is_still_refused(self):
        """The case the old rule caught, which the new one must still catch."""
        a = [tile(f"a{n}", "bird") for n in range(3)]
        b = [tile(f"b{n}", "bird") for n in range(3)]
        c = [tile(f"c{n}", "tool") for n in range(3)]
        memberships, universal = board(a, b, c)
        names = [tuple(n for n, _ in g) for g in (a, b, c)]

        assert not partition_validator(memberships, universal)(partition(*names))

    def test_a_labeling_exists_when_one_group_has_a_choice(self):
        """B may be read as boat or as vehicle; only the narrow reading is
        incomparable with A's, and one valid labeling is enough."""
        a = [tile(f"a{n}", "plane") for n in range(3)]
        b = [tile(f"b{n}", "boat", "vessel") for n in range(3)]
        c = [tile(f"c{n}", "tool") for n in range(3)]
        memberships, universal = board(a, b, c)
        names = [tuple(n for n, _ in g) for g in (a, b, c)]

        assert partition_validator(memberships, universal)(partition(*names))


class TestNestedLabels:
    def test_a_broad_label_cannot_stand_beside_the_narrow_one_it_contains(self):
        """Planes labelled only "vehicle" and boats labelled "boat": boats are
        vehicles too, so the two readings overlap and neither group is its own."""
        a = [tile(f"a{n}", "vehicle") for n in range(3)]
        b = [tile(f"b{n}", "boat", "vehicle") for n in range(3)]
        c = [tile(f"c{n}", "tool") for n in range(3)]
        memberships, universal = board(a, b, c)
        names = [tuple(n for n, _ in g) for g in (a, b, c)]

        assert not partition_validator(memberships, universal)(partition(*names))

    def test_equal_extensions_are_the_same_label_whatever_they_are_called(self):
        """"craft" and "vehicle" name the same tiles on this board."""
        a = [tile(f"a{n}", "craft", "vehicle") for n in range(3)]
        b = [tile(f"b{n}", "craft", "vehicle") for n in range(3)]
        c = [tile(f"c{n}", "tool") for n in range(3)]
        memberships, universal = board(a, b, c)
        names = [tuple(n for n, _ in g) for g in (a, b, c)]

        assert not partition_validator(memberships, universal)(partition(*names))

    def test_a_group_with_no_shared_category_is_refused(self):
        a = [tile("a0", "x"), tile("a1", "y")]
        b = [tile(f"b{n}", "tool") for n in range(2)]
        memberships, universal = board(a, b)
        names = [tuple(n for n, _ in g) for g in (a, b)]

        assert not partition_validator(memberships, universal)(partition(*names))


class TestCost:
    def test_a_partition_is_judged_quickly_with_many_labels_per_group(self):
        """Backtracking over one label per group, four groups of many labels."""
        groups = [
            [tile(f"g{i}t{n}", *(f"g{i}c{k}" for k in range(8))) for n in range(5)]
            for i in range(4)
        ]
        memberships, universal = board(*groups)
        names = [tuple(n for n, _ in g) for g in groups]
        valid = partition_validator(memberships, universal)

        started = time.perf_counter()
        for _ in range(200):
            assert valid(partition(*names))

        assert time.perf_counter() - started < 1.0
