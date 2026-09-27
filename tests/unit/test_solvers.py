from __future__ import annotations

import pytest

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.solvers import (
    collapse_equivalent,
    count_perfect_matchings,
    enumerate_partitions,
    enumerate_paths,
    minimal_paths,
)


class TestPartitionEnumeration:
    def test_counts_each_partition_once(self):
        """Four items into two pairs has three partitions, not twelve.

        The anchoring trick is what collapses orderings; without it this
        returns a multiple of three and no grouping game could ever prove
        uniqueness.
        """
        result = enumerate_partitions(
            list("abcd"),
            group_size=2,
            group_count=2,
            is_valid_group=lambda g: True,
        )
        assert result.count == 3
        assert result.exhausted

    def test_a_single_group_is_the_whole_set(self):
        result = enumerate_partitions(
            list("abc"), group_size=3, group_count=1, is_valid_group=lambda g: True
        )
        assert result.count == 1

    def test_group_validity_prunes(self):
        def same_case(group):
            return len({item.isupper() for item in group}) == 1

        result = enumerate_partitions(
            ["a", "b", "C", "D"],
            group_size=2,
            group_count=2,
            is_valid_group=same_case,
        )
        assert result.count == 1
        assert sorted(sorted(g) for g in result.first()) == [["C", "D"], ["a", "b"]]

    def test_partition_level_validity_rejects_valid_ingredients(self):
        """The combination gate: every group fine, the partition not.

        This is the failure the design calls mandatory to catch, and it can
        only be caught once the whole partition exists.
        """
        groups_only = enumerate_partitions(
            list("abcd"),
            group_size=2,
            group_count=2,
            is_valid_group=lambda g: True,
        )
        with_partition_rule = enumerate_partitions(
            list("abcd"),
            group_size=2,
            group_count=2,
            is_valid_group=lambda g: True,
            is_valid_partition=lambda p: ("a", "b") in p,
        )
        assert groups_only.count == 3
        assert with_partition_rule.count == 1

    def test_twelve_into_three_fours_matches_the_known_count(self):
        """12!/(4!^3 * 3!) = 5775. A wrong anchoring scheme fails this loudly."""
        result = enumerate_partitions(
            list(range(12)),
            group_size=4,
            group_count=4 - 1,
            is_valid_group=lambda g: True,
        )
        assert result.count == 5775
        assert result.exhausted

    def test_an_unconstrained_large_partition_exceeds_the_budget(self):
        """Sixteen into four fours is 2,627,625 partitions: beyond the budget.

        Recorded as a test rather than discovered in production. A grouping
        game over that many items must prune at the group level, and the
        honest SOUND_INCOMPLETE flag is what tells it so instead of silently
        returning a wrong count.
        """
        result = enumerate_partitions(
            list(range(16)),
            group_size=4,
            group_count=4,
            is_valid_group=lambda g: True,
        )
        assert not result.exhausted
        assert result.completeness is VerificationCompleteness.SOUND_INCOMPLETE

    def test_group_pruning_makes_a_large_partition_exact(self):
        """With a real constraint the same space becomes provable.

        Twenty items in five declared families, only same-family groups
        valid: exactly one partition exists and the search finds it within
        budget. This is the shape the reference grouping game occupies.
        """
        families = {i: i // 4 for i in range(20)}
        result = enumerate_partitions(
            list(range(20)),
            group_size=4,
            group_count=5,
            is_valid_group=lambda g: len({families[i] for i in g}) == 1,
        )
        assert result.count == 1
        assert result.exhausted

    def test_too_few_items_yields_nothing(self):
        result = enumerate_partitions(
            list("ab"), group_size=2, group_count=2, is_valid_group=lambda g: True
        )
        assert result.count == 0 and result.states_examined == 0

    def test_a_budget_overrun_reports_incompleteness(self):
        result = enumerate_partitions(
            list(range(16)),
            group_size=4,
            group_count=4,
            is_valid_group=lambda g: True,
            state_budget=100,
        )
        assert not result.exhausted
        assert result.completeness is VerificationCompleteness.SOUND_INCOMPLETE

    def test_max_solutions_stops_early_and_says_so(self):
        result = enumerate_partitions(
            list("abcd"),
            group_size=2,
            group_count=2,
            is_valid_group=lambda g: True,
            max_solutions=1,
        )
        assert result.count == 1 and not result.exhausted

    def test_results_are_deterministic(self):
        def run():
            return enumerate_partitions(
                list("abcdef"),
                group_size=2,
                group_count=3,
                is_valid_group=lambda g: True,
            ).solutions

        assert run() == run()

    def test_invalid_shape_is_refused(self):
        with pytest.raises(ValueError):
            enumerate_partitions(
                list("ab"), group_size=0, group_count=1, is_valid_group=lambda g: True
            )


class TestPathEnumeration:
    GRAPH = {
        "a": [("b", "ab"), ("c", "ac")],
        "b": [("d", "bd")],
        "c": [("d", "cd"), ("e", "ce")],
        "d": [("z", "dz")],
        "e": [("d", "ed")],
        "z": [],
    }

    def neighbours(self, node):
        return self.GRAPH.get(node, [])

    def test_finds_every_simple_path(self):
        result = enumerate_paths("a", "z", self.neighbours, max_depth=6)
        assert result.count == 3

    def test_reports_the_minimal_length_and_its_count(self):
        result = enumerate_paths("a", "z", self.neighbours, max_depth=6)
        assert result.metrics["minimal_length"] == 3.0
        assert result.metrics["minimal_count"] == 2.0

    def test_reports_longer_alternatives(self):
        result = enumerate_paths("a", "z", self.neighbours, max_depth=6)
        assert result.metrics["longer_alternatives"] == 1.0

    def test_minimal_paths_extracts_the_shortest(self):
        result = enumerate_paths("a", "z", self.neighbours, max_depth=6)
        shortest = minimal_paths(result)
        assert len(shortest) == 2
        assert all(len(edges) == 3 for _, edges in shortest)

    def test_a_depth_cap_hides_longer_routes(self):
        result = enumerate_paths("a", "z", self.neighbours, max_depth=3)
        assert result.count == 2
        assert result.metrics["longer_alternatives"] == 0.0

    def test_no_route_yields_nothing(self):
        result = enumerate_paths("z", "a", self.neighbours, max_depth=6)
        assert result.count == 0 and result.metrics == {}

    def test_paths_never_revisit_a_node(self):
        cyclic = {"a": [("b", 1)], "b": [("a", 2), ("z", 3)], "z": []}
        result = enumerate_paths("a", "z", lambda n: cyclic.get(n, []), max_depth=6)
        for nodes, _ in result.solutions:
            assert len(nodes) == len(set(nodes))

    def test_shortest_paths_are_found_first(self):
        result = enumerate_paths("a", "z", self.neighbours, max_depth=6)
        lengths = [len(edges) for _, edges in result.solutions]
        assert lengths == sorted(lengths)

    def test_results_are_deterministic(self):
        first = enumerate_paths("a", "z", self.neighbours, max_depth=6).solutions
        second = enumerate_paths("a", "z", self.neighbours, max_depth=6).solutions
        assert first == second

    def test_a_budget_overrun_reports_incompleteness(self):
        result = enumerate_paths(
            "a", "z", self.neighbours, max_depth=6, state_budget=2
        )
        assert not result.exhausted

    def test_zero_depth_is_refused(self):
        with pytest.raises(ValueError):
            enumerate_paths("a", "z", self.neighbours, max_depth=0)


class TestMatchingCounting:
    def test_counts_a_free_matching_exactly(self):
        result = count_perfect_matchings(
            [1, 2, 3], ["a", "b", "c"], lambda left, right: True
        )
        assert result.count == 6

    def test_a_constrained_matching_has_one_solution(self):
        pairs = {1: "a", 2: "b", 3: "c"}
        result = count_perfect_matchings(
            [1, 2, 3], ["a", "b", "c"], lambda left, right: pairs[left] == right
        )
        assert result.count == 1

    def test_a_polysemy_collision_creates_a_second_matching(self):
        """Two items that each fit two glosses admit two consistent matchings.

        This is the shape that makes definition matching non-trivial and the
        reason counting matters: a sampled solver would report "solvable" and
        miss that the puzzle has no unique answer.
        """
        allowed = {
            "bat": {"mammal", "club"},
            "bank": {"river", "finance"},
            "kite": {"bird"},
        }
        result = count_perfect_matchings(
            ["bat", "bank", "kite"],
            ["mammal", "river", "bird"],
            lambda word, gloss: gloss in allowed[word],
        )
        assert result.count == 1

        ambiguous = {
            "bat": {"mammal", "club"},
            "club": {"mammal", "club"},
            "kite": {"bird"},
        }
        second = count_perfect_matchings(
            ["bat", "club", "kite"],
            ["mammal", "club", "bird"],
            lambda word, gloss: gloss in ambiguous[word],
        )
        assert second.count == 2

    def test_an_impossible_row_short_circuits(self):
        result = count_perfect_matchings(
            [1, 2], ["a", "b"], lambda left, right: left == 1 and right == "a"
        )
        assert result.count == 0
        assert result.metrics["dead_ends"] == 1.0

    def test_mismatched_sizes_yield_nothing(self):
        result = count_perfect_matchings([1], ["a", "b"], lambda left, right: True)
        assert result.count == 0

    def test_twelve_by_twelve_is_tractable(self):
        left = list(range(12))
        right = list(range(12))
        result = count_perfect_matchings(
            left, right, lambda a, b: abs(a - b) <= 1
        )
        # Fibonacci-shaped band matrix: far smaller than 12 factorial.
        assert result.count == 233
        assert result.states_examined < 5000

    def test_a_budget_overrun_reports_incompleteness(self):
        result = count_perfect_matchings(
            list(range(8)), list(range(8)), lambda a, b: True, state_budget=50
        )
        assert not result.exhausted

    def test_results_are_deterministic(self):
        def run():
            return count_perfect_matchings(
                [1, 2, 3], ["a", "b", "c"], lambda left, right: True
            ).solutions

        assert run() == run()


class TestCollapseEquivalent:
    def test_collapses_by_key_and_counts_absorptions(self):
        solutions = [{"v": 1.01}, {"v": 1.02}, {"v": 2.0}]
        kept, collapsed = collapse_equivalent(
            solutions, key=lambda s: round(s["v"])
        )
        assert len(kept) == 2 and collapsed == 1

    def test_nothing_to_collapse_reports_zero(self):
        kept, collapsed = collapse_equivalent([{"v": 1}, {"v": 2}], key=lambda s: s["v"])
        assert len(kept) == 2 and collapsed == 0

    def test_an_empty_input_is_safe(self):
        assert collapse_equivalent([], key=lambda s: s) == ([], 0)
