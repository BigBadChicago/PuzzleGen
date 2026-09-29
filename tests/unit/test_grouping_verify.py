"""Verification and difficulty for game 1.

Boards are built directly here rather than generated, so each test can create
the exact shape it is about: an ambiguous board, a board whose tiles are
indistinguishable, a board too large for its budget.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.plugin import Puzzle
from puzzlegen.engine.uniqueness import COLLAPSED_SOLUTIONS, check
from puzzlegen.games.grouping.descriptor import (
    DESCRIPTOR,
    GAME_ID,
    MAX_GROUP_SIZE,
    MIN_GROUP_SIZE,
    VISIBLE_GROUPS,
)
from puzzlegen.games.grouping.difficulty import (
    WEIGHTS,
    cross_group_pull,
    expected_states,
    hidden_evenness,
    measure_difficulty,
    search_cost,
    signature_thinness,
    size_neutrality_check,
)
from puzzlegen.games.grouping.verify import (
    ALTERNATIVE_PARTITIONS,
    INTERCHANGEABLE_TILES,
    as_sets,
    group_validator,
    interchangeable_tiles,
    partition_validator,
    signature_of,
    universal_categories,
    verify,
)


def make_puzzle(
    groups: list[list[str]],
    memberships: dict[str, list[str]],
    *,
    hidden: list[str] | None = None,
    group_size: int | None = None,
) -> Puzzle:
    size = group_size or len(groups[0])
    assignment = {
        tile: index for index, members in enumerate(groups) for tile in members
    }
    tiles = sorted(assignment)
    chosen = hidden if hidden is not None else [members[0] for members in groups]
    return Puzzle(
        game_id=GAME_ID,
        payload={
            "game_id": GAME_ID,
            "group_size": size,
            "group_count": VISIBLE_GROUPS,
            "columns": VISIBLE_GROUPS,
            "tiles": [{"id": tile, "label": tile} for tile in tiles],
            "hidden_group_exists": True,
            "hidden_group_size": len(chosen),
        },
        solution={
            "groups": [
                {
                    "index": index,
                    "category": f"category:c{index}",
                    "category_id": f"category:c{index}",
                    "members": list(members),
                }
                for index, members in enumerate(groups)
            ],
            "assignment": assignment,
            "hidden": {
                "category": "category:overlay",
                "category_id": "category:overlay",
                "secondary_category": None,
                "members": list(chosen),
            },
            "borrowings": [],
            "memberships": {tile: sorted(cats) for tile, cats in memberships.items()},
        },
        fact_refs=tuple(tiles),
    )


def ambiguous_board(size: int = 5) -> Puzzle:
    """A board with a real second reading.

    Two tiles can swap groups, and they are told apart by the graph: one
    carries a category the other does not. That asymmetry is what makes the
    swap a different puzzle rather than the same one relabelled, and so what
    keeps the tolerance rule from absorbing it.
    """
    groups = [[f"g{i}_{n}" for n in range(size)] for i in range(VISIBLE_GROUPS)]
    memberships = {
        tile: [f"category:c{index}", "category:root"]
        for index, members in enumerate(groups)
        for tile in members
    }
    memberships["g0_0"] += ["category:c1", "category:distinct"]
    memberships["g1_0"] += ["category:c0"]
    return make_puzzle(groups, memberships)


def clean_board(size: int = 5, *, root: str = "category:root") -> Puzzle:
    """Four groups whose members share only their own category and a root."""
    groups = [[f"g{index}_{n}" for n in range(size)] for index in range(VISIBLE_GROUPS)]
    memberships = {
        tile: [f"category:c{index}", root]
        for index, members in enumerate(groups)
        for tile in members
    }
    hidden = [members[0] for members in groups]
    hidden += [groups[n % VISIBLE_GROUPS][1] for n in range(size - VISIBLE_GROUPS)]
    return make_puzzle(groups, memberships, hidden=hidden)


class TestUniversalCategories:
    def test_a_category_on_every_tile_is_universal(self):
        puzzle = clean_board()
        memberships = {
            tile: frozenset(cats)
            for tile, cats in puzzle.solution["memberships"].items()
        }
        assert "category:root" in universal_categories(memberships)

    def test_a_category_on_some_tiles_is_not(self):
        puzzle = clean_board()
        memberships = {
            tile: frozenset(cats)
            for tile, cats in puzzle.solution["memberships"].items()
        }
        assert "category:c0" not in universal_categories(memberships)

    def test_an_empty_board_has_no_universal_categories(self):
        assert universal_categories({}) == frozenset()

    def test_a_signature_excludes_the_universal_ones(self):
        memberships = {"a": frozenset({"x", "root"}), "b": frozenset({"y", "root"})}
        universal = universal_categories(memberships)
        assert signature_of("a", memberships, universal) == frozenset({"x"})


class TestGroupValidity:
    def memberships(self) -> dict[str, frozenset[str]]:
        return {
            "a": frozenset({"cat", "root"}),
            "b": frozenset({"cat", "root"}),
            "c": frozenset({"dog", "root"}),
        }

    def test_members_sharing_a_distinguishing_category_are_a_group(self):
        memberships = self.memberships()
        universal = universal_categories(memberships)
        assert group_validator(memberships, universal)(("a", "b"))

    def test_members_sharing_nothing_are_not(self):
        memberships = self.memberships()
        universal = universal_categories(memberships)
        assert not group_validator(memberships, universal)(("a", "c"))

    def test_a_category_covering_the_whole_board_cannot_justify_a_group(self):
        """Otherwise an all-animal board makes every partition valid and
        uniqueness unprovable."""
        memberships = {tile: frozenset({"root"}) for tile in "abcd"}
        universal = universal_categories(memberships)
        assert not group_validator(memberships, universal)(("a", "b"))

    def test_a_tile_the_board_does_not_know_invalidates_its_group(self):
        memberships = self.memberships()
        universal = universal_categories(memberships)
        assert not group_validator(memberships, universal)(("a", "unknown"))


class TestPartitionValidity:
    def test_two_groups_justified_by_the_same_single_category_are_refused(self):
        """Four groups all reading 'these are birds' is one group split four
        ways, and a player who found it would be right in a way the board
        cannot mark."""
        memberships = {
            tile: frozenset({"bird", "root"}) for tile in ("a", "b", "c", "d")
        }
        memberships["e"] = frozenset({"fish", "root"})
        universal = universal_categories(memberships)
        validator = partition_validator(memberships, universal)
        assert not validator((("a", "b"), ("c", "d")))

    def test_groups_with_different_reasons_are_accepted(self):
        memberships = {
            "a": frozenset({"bird", "root"}),
            "b": frozenset({"bird", "root"}),
            "c": frozenset({"fish", "root"}),
            "d": frozenset({"fish", "root"}),
        }
        universal = universal_categories(memberships)
        validator = partition_validator(memberships, universal)
        assert validator((("a", "b"), ("c", "d")))


class TestVerification:
    def test_a_clean_board_has_exactly_one_solution(self):
        result = verify(clean_board())
        assert result.solvable
        assert result.solution_count == 1

    def test_a_clean_board_enumerates_completely(self):
        assert verify(clean_board()).completeness is VerificationCompleteness.COMPLETE

    def test_the_intended_solution_is_among_those_found(self):
        """Reported in the puzzle's own shape, because that is what the engine
        compares.

        The engine hashes the whole solution object and looks for that hash
        among the verifier's, which catches a puzzle whose stated answer its
        own solver cannot reach. An equivalent rebuilt from the search would
        hash differently and fail a check it actually passes.
        """
        puzzle = clean_board()
        result = verify(puzzle)
        intended = as_sets(
            tuple(tuple(g["members"]) for g in puzzle.solution["groups"])
        )
        found = [
            as_sets(tuple(tuple(g["members"]) for g in s["groups"]))
            for s in result.solutions
        ]
        assert intended in found

    def test_the_intended_solution_is_reported_verbatim(self):
        from puzzlegen.core.hashing import stable_hash

        puzzle = clean_board()
        result = verify(puzzle)
        hashes = {stable_hash(dict(s)) for s in result.solutions}
        assert stable_hash(dict(puzzle.solution)) in hashes

    def test_a_unique_board_satisfies_the_contract(self):
        puzzle = clean_board()
        verdict = check(DESCRIPTOR, verify(puzzle))
        assert verdict.satisfied, verdict.reason

    def test_the_tolerance_metric_is_always_reported(self):
        """Without it the engine cannot tell collapsing from never having
        happened, and refuses the contract outright."""
        assert COLLAPSED_SOLUTIONS in verify(clean_board()).metrics

    def test_a_board_with_a_second_reading_reports_it(self):
        result = verify(ambiguous_board())
        assert result.solution_count > 1
        assert result.metrics[ALTERNATIVE_PARTITIONS] >= 1

    def test_an_ambiguous_board_fails_the_contract(self):
        verdict = check(DESCRIPTOR, verify(ambiguous_board()))
        assert not verdict.satisfied

    def test_a_symmetric_swap_is_collapsed_rather_than_counted(self):
        """Two tiles that each carry both categories and nothing else are
        indistinguishable, so exchanging them is the same puzzle.

        This is the case the tolerance contract exists for, and the reason it
        has to report what it collapsed: the board is unique, and saying so
        without evidence would be indistinguishable from never having looked.
        """
        size = 5
        groups = [[f"g{i}_{n}" for n in range(size)] for i in range(VISIBLE_GROUPS)]
        memberships = {
            tile: [f"category:c{index}", "category:root"]
            for index, members in enumerate(groups)
            for tile in members
        }
        memberships["g0_0"].append("category:c1")
        memberships["g1_0"].append("category:c0")
        result = verify(make_puzzle(groups, memberships))
        assert result.solution_count == 1
        assert result.metrics[COLLAPSED_SOLUTIONS] >= 1
        assert check(DESCRIPTOR, result).satisfied

    def test_a_board_whose_tiles_have_no_memberships_is_unsolvable(self):
        puzzle = clean_board()
        broken = Puzzle(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "memberships": {}},
            fact_refs=puzzle.fact_refs,
        )
        result = verify(broken)
        assert not result.solvable
        assert "no category memberships" in result.detail

    def test_the_overlay_axis_is_not_counted_as_a_fifth_answer(self):
        """The hidden group is a second reading offered after the first is
        exhausted, not another answer to the question the board asks."""
        puzzle = clean_board()
        memberships = dict(puzzle.solution["memberships"])
        for tile in puzzle.solution["hidden"]["members"]:
            memberships[tile] = [*memberships[tile], "category:overlay"]
        enriched = Puzzle(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "memberships": memberships},
            fact_refs=puzzle.fact_refs,
        )
        # The overlay category is in the data, and still only one grouping is
        # valid, because no four-group partition can be built from it.
        assert verify(enriched).solution_count == 1


class TestTolerance:
    def indistinguishable_board(self) -> Puzzle:
        """Two tiles in one group carrying exactly the same categories."""
        size = 5
        groups = [[f"g{i}_{n}" for n in range(size)] for i in range(VISIBLE_GROUPS)]
        memberships = {
            tile: [f"category:c{index}", "category:root"]
            for index, members in enumerate(groups)
            for tile in members
        }
        return make_puzzle(groups, memberships)

    def test_tiles_with_identical_signatures_are_counted(self):
        puzzle = self.indistinguishable_board()
        memberships = {
            tile: frozenset(cats)
            for tile, cats in puzzle.solution["memberships"].items()
        }
        universal = universal_categories(memberships)
        assert interchangeable_tiles(memberships, universal) > 0

    def test_the_count_reaches_the_metrics(self):
        result = verify(self.indistinguishable_board())
        assert result.metrics[INTERCHANGEABLE_TILES] > 0

    def test_a_board_with_no_interchangeable_tiles_collapses_nothing(self):
        """A verifier claiming a collapse on such a board claims something
        impossible."""
        size = 5
        groups = [[f"g{i}_{n}" for n in range(size)] for i in range(VISIBLE_GROUPS)]
        memberships = {
            tile: [f"category:c{index}", f"category:{tile}", "category:root"]
            for index, members in enumerate(groups)
            for tile in members
        }
        result = verify(make_puzzle(groups, memberships))
        assert result.metrics[INTERCHANGEABLE_TILES] == 0
        assert result.metrics[COLLAPSED_SOLUTIONS] == 0

    def test_tolerance_does_not_collapse_genuinely_different_readings(self):
        """The rule is narrow on purpose.

        A tolerance wide enough to absorb a real alternative would let an
        ambiguous board claim uniqueness, which is the failure the contract
        exists to prevent. Here the two swappable tiles differ by one
        category, and the alternative survives.
        """
        result = verify(ambiguous_board())
        assert result.solution_count > 1


class TestBudget:
    def test_an_overrun_reports_an_incomplete_search(self):
        result = verify(clean_board(), state_budget=10)
        assert result.completeness is VerificationCompleteness.SOUND_INCOMPLETE

    def test_an_overrun_says_its_count_is_a_lower_bound(self):
        """Never a number dressed up as exact."""
        result = verify(clean_board(), state_budget=10)
        assert "lower bound" in result.detail

    def test_an_incomplete_search_satisfies_no_uniqueness_contract(self):
        """The unexamined branch is where a second solution would hide."""
        verdict = check(DESCRIPTOR, verify(clean_board(), state_budget=10))
        assert not verdict.satisfied
        assert "incomplete" in verdict.reason

    def test_the_states_examined_are_reported(self):
        assert verify(clean_board()).states_examined > 0


class TestDifficultyFeatures:
    def test_cross_group_pull_is_zero_when_groups_share_nothing(self):
        assert cross_group_pull(clean_board()) == 0.0

    def test_cross_group_pull_rises_when_tiles_resemble_other_groups(self):
        puzzle = clean_board()
        memberships = dict(puzzle.solution["memberships"])
        for index, tile in enumerate(sorted(memberships)):
            memberships[tile] = [*memberships[tile], f"category:pair{index % 3}"]
        tempted = Puzzle(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "memberships": memberships},
            fact_refs=puzzle.fact_refs,
        )
        assert cross_group_pull(tempted) > cross_group_pull(puzzle)

    def test_cross_group_pull_is_a_ratio_not_a_count(self):
        """Otherwise it would grow with board size on its own."""
        assert 0.0 <= cross_group_pull(clean_board(9)) <= 1.0

    def test_an_evenly_spread_hidden_group_scores_higher(self):
        even = clean_board(8)
        lumpy_hidden = [
            even.solution["groups"][0]["members"][n] for n in range(5)
        ] + [even.solution["groups"][i]["members"][0] for i in range(1, 4)]
        lumpy = Puzzle(
            game_id=even.game_id,
            payload=even.payload,
            solution={
                **even.solution,
                "hidden": {**even.solution["hidden"], "members": lumpy_hidden},
            },
            fact_refs=even.fact_refs,
        )
        assert hidden_evenness(even) > hidden_evenness(lumpy)

    def test_thin_signatures_score_higher_than_rich_ones(self):
        thin = clean_board()
        puzzle = clean_board()
        # Distinct extras per tile. Adding the same six categories to every
        # tile would make them universal, and a universal category tells a
        # player nothing, so the signature would be unchanged.
        memberships = {
            tile: [*cats, *(f"category:{tile}_x{n}" for n in range(6))]
            for tile, cats in puzzle.solution["memberships"].items()
        }
        rich = Puzzle(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "memberships": memberships},
            fact_refs=puzzle.fact_refs,
        )
        assert signature_thinness(thin) > signature_thinness(rich)

    def test_every_feature_stays_on_the_unit_interval(self):
        for size in range(MIN_GROUP_SIZE, MAX_GROUP_SIZE + 1):
            puzzle = clean_board(size)
            result = verify(puzzle, state_budget=200_000)
            for value in (
                cross_group_pull(puzzle),
                hidden_evenness(puzzle),
                signature_thinness(puzzle),
                search_cost(puzzle, result),
            ):
                assert 0.0 <= value <= 1.0

    def test_the_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_search_baseline_grows_with_board_size(self):
        assert expected_states(9) > expected_states(5)


class TestDifficultyMeasurement:
    def measure(self, size: int):
        puzzle = clean_board(size)
        return measure_difficulty(puzzle, verify(puzzle, state_budget=200_000))

    def test_a_score_lies_on_the_unit_interval(self):
        assert 0.0 <= self.measure(5).score <= 1.0

    def test_the_features_are_reported(self):
        """An unexplained score cannot be audited or improved."""
        features = self.measure(5).features
        assert set(WEIGHTS) <= set(features)

    def test_the_board_shape_is_recorded_without_being_scored(self):
        features = self.measure(6).features
        assert features["group_size"] == 6
        assert features["board_size"] == 6 * VISIBLE_GROUPS

    def test_the_collapse_count_is_recorded(self):
        assert "collapsed_solutions" in self.measure(5).features

    def test_confidence_is_modest_because_nothing_has_been_calibrated(self):
        assert self.measure(5).confidence < 0.6

    def test_measurement_is_deterministic(self):
        assert self.measure(5).score == self.measure(5).score

    def test_a_score_bands(self):
        assert DESCRIPTOR.band_for(self.measure(5).score) in DESCRIPTOR.supported_bands


class TestBoardSizeIsDividedOut:
    """The claim this module makes, measured rather than asserted.

    Structurally identical boards at every supported size should score close
    together. If they do not, the score is partly a board-size measurement
    wearing a difficulty label, and every wide day would land in HARD for a
    reason no player could feel.
    """

    def scores(self) -> dict[int, float]:
        found = {}
        for size in range(MIN_GROUP_SIZE, MAX_GROUP_SIZE + 1):
            puzzle = clean_board(size)
            result = verify(puzzle, state_budget=200_000)
            found[size] = measure_difficulty(puzzle, result).score
        return found

    def test_every_size_produces_a_score(self):
        assert len(self.scores()) == MAX_GROUP_SIZE - MIN_GROUP_SIZE + 1

    def test_the_spread_across_sizes_is_small(self):
        spread = size_neutrality_check(self.scores())
        assert spread < 0.2, f"board size still drives the score: spread {spread:.3f}"

    def test_the_widest_board_is_not_automatically_the_hardest(self):
        scores = self.scores()
        assert scores[MAX_GROUP_SIZE] <= max(scores.values())
        assert DESCRIPTOR.band_for(scores[MIN_GROUP_SIZE]) == DESCRIPTOR.band_for(
            scores[MAX_GROUP_SIZE]
        )

    def test_the_neutrality_check_needs_two_sizes_to_say_anything(self):
        assert size_neutrality_check({5: 0.4}) == 0.0
