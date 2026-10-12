"""The difficulty model, and the two claims it makes.

Board size is divided out, which is a claim about numbers and is measured here
against boards the generator actually builds. And the bands separate, which on
this game they do completely -- a result that would normally be a sign of a
tautology, so the test that checks it also checks how much of the separation
the other three features contribute.
"""

from __future__ import annotations

import statistics

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import DifficultyBand, VerificationCompleteness
from puzzlegen.engine.plugin import PuzzleCandidate, VerificationResult
from puzzlegen_numbers.assemble import assemble
from puzzlegen_numbers.descriptor import BOARD_SIZES, DESCRIPTOR, WINDOW, tier_for
from puzzlegen_numbers.difficulty import (
    CONFIDENCE,
    REASON_SATURATION,
    WEIGHTS,
    measure_difficulty,
    near_share,
    obscurity,
    reason_shortage,
    size_neutrality_check,
    tightness,
)
from puzzlegen_numbers.generate import payload_for, plan_for, ranked, readable
from puzzlegen_numbers.properties import MAX_TIER, MIN_TIER
from puzzlegen_numbers.verify import verify

from boards import make_puzzle, result, window

EMPTY = VerificationResult(
    solvable=True,
    solution_count=1,
    completeness=VerificationCompleteness.COMPLETE,
    metrics={},
)


def boards(board_size: int, tier: int, count: int = 40, seed: bytes = b"diff" * 8):
    """Real boards, assembled, with their verifications."""
    pool, values, memberships, index = readable(result(window()))
    made = []
    for attempt in range(count):
        plan, _ = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=board_size, tier=tier,
            rng=DeterministicRng(seed).derive(str(attempt)),
        )
        if plan is None:
            continue
        candidate = PuzzleCandidate(
            candidate_id=plan.candidate_id(),
            payload=payload_for(plan),
            fact_refs=plan.dependency_ids(),
        )
        puzzle = assemble(candidate, DeterministicRng(seed))
        made.append((puzzle, verify(puzzle)))
    return made


def leading(board_size: int, tier: int, window_size: int = 8):
    """The boards the engine reaches: the first few the generator offers.

    The sample every claim about the difficulty bands is made over, because
    the band changes the generator's ordering rather than which boards exist.
    ``window_size`` is the engine's own in-band search window.
    """
    pool, values, memberships, index = readable(result(window()))
    plans = []
    for attempt in range(48):
        plan, _ = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=board_size, tier=tier,
            rng=DeterministicRng(b"lead" * 8).derive(str(attempt)),
        )
        if plan is not None:
            plans.append(plan)
    made = []
    for plan in ranked(plans, tier)[:window_size]:
        candidate = PuzzleCandidate(
            candidate_id=plan.candidate_id(),
            payload=payload_for(plan),
            fact_refs=plan.dependency_ids(),
        )
        puzzle = assemble(candidate, DeterministicRng(b"lay" * 10))
        made.append((puzzle, verify(puzzle)))
    return made


class TestTheFeatures:
    def test_obscurity_is_the_propertys_tier(self):
        assert obscurity(make_puzzle(property_name="even number")) == 0.0
        assert obscurity(make_puzzle(property_name="Fibonacci number")) == 1.0

    def test_an_unrated_property_rates_as_most_obscure(self):
        """The safe direction: a build that grew a category nobody has rated
        would otherwise be treated as familiar as "even" on an easy day."""
        assert obscurity(make_puzzle(property_name="whatever this is")) == 1.0

    def test_one_reason_is_the_shortest_and_three_is_none(self):
        one = make_puzzle()
        assert len(one.solution["reasons"]) == 1
        assert reason_shortage(one, EMPTY) == pytest.approx(1.0)
        many = make_puzzle([15, 16, 30, 45, 60])
        assert len(many.solution["reasons"]) >= 2
        assert reason_shortage(many, EMPTY) < 1.0

    def test_the_reason_count_comes_from_the_verifier_when_there_is_one(self):
        puzzle, verification = boards(6, MIN_TIER, 1)[0]
        assert reason_shortage(puzzle, verification) == pytest.approx(
            max(
                0.0,
                min(
                    1.0,
                    (REASON_SATURATION - max(1.0, verification.metrics["reasons"]))
                    / (REASON_SATURATION - 1.0),
                ),
            )
        )

    def test_the_near_share_is_a_share_of_the_vocabulary(self):
        """A count would double when a taxonomy doubled, without making any
        board harder."""
        puzzle = make_puzzle([12, 34, 51, 53, 76], answer=51)
        share = near_share(puzzle, EMPTY)
        assert share == pytest.approx(
            len(puzzle.solution["near"]) / len(puzzle.solution["vocabulary"])
        )

    def test_a_tight_board_scores_higher_than_a_wide_one(self):
        low, high = WINDOW
        tight = make_puzzle([11, 12, 13, 14, 15], answer=11)
        wide = make_puzzle([11, 30, 50, 70, 99], answer=11)
        assert tightness(tight) > tightness(wide)

    def test_a_board_spanning_the_window_scores_nothing_for_tightness(self):
        low, high = WINDOW
        puzzle = make_puzzle([low, 30, 50, 70, high], answer=low)
        assert tightness(puzzle) == pytest.approx(0.0)

    def test_every_feature_lies_on_the_unit_interval(self):
        for tier in (MIN_TIER, MIN_TIER + 1, MAX_TIER):
            for size in BOARD_SIZES:
                for puzzle, verification in boards(size, tier, 10):
                    measured = measure_difficulty(puzzle, verification)
                    for name in WEIGHTS:
                        assert 0.0 <= measured.features[name] <= 1.0


class TestTheScore:
    def test_the_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_score_is_the_weighted_sum_of_the_features(self):
        puzzle, verification = boards(6, MIN_TIER, 1)[0]
        measured = measure_difficulty(puzzle, verification)
        expected = sum(WEIGHTS[name] * measured.features[name] for name in WEIGHTS)
        assert measured.score == pytest.approx(expected)

    def test_the_measurement_exposes_the_features_that_produced_it(self):
        puzzle, verification = boards(6, MIN_TIER, 1)[0]
        measured = measure_difficulty(puzzle, verification)
        assert set(WEIGHTS) <= set(measured.features)
        assert measured.detail

    def test_the_raw_shape_of_the_board_is_recorded_beside_the_score(self):
        """Seven tiles at the obscure tier, which takes more than one draw.

        Five two-digit numbers are Fibonacci, so a seven-tile board can only
        be built around the ten triangular ones and most draws of the tier
        pick the other property and are refused. That is the thin content the
        fallback size order exists for, and here it is just a reason to ask
        for more than one attempt.
        """
        made = boards(7, MAX_TIER, 20)
        assert made, "no seven-tile board at the obscure tier in twenty draws"
        puzzle, verification = made[0]
        features = measure_difficulty(puzzle, verification).features
        assert features["board_size"] == 7.0
        assert features["spread"] > 0

    def test_the_confidence_is_modest_and_says_so(self):
        """These weights are reasoned rather than fitted, and the obscurity
        tiers they rest on are a curator's judgement."""
        assert CONFIDENCE <= 0.5
        puzzle, verification = boards(6, MIN_TIER, 1)[0]
        assert measure_difficulty(puzzle, verification).confidence == CONFIDENCE


class TestBoardSizeIsDividedOut:
    def test_the_three_sizes_score_alike_at_the_same_tier(self):
        """The small boards score slightly harder, which is the tightness
        feature working as intended rather than size leaking through: five
        numbers drawn from the same window span less of it than seven do.
        """
        means = {}
        for size in BOARD_SIZES:
            scores = [
                measure_difficulty(puzzle, verification).score
                for puzzle, verification in boards(size, MIN_TIER + 1, 40)
            ]
            means[size] = statistics.mean(scores)
        assert size_neutrality_check(means) < 0.1

    def test_the_neutrality_check_reports_the_spread(self):
        assert size_neutrality_check({5: 0.4, 7: 0.5}) == pytest.approx(0.1)
        assert size_neutrality_check({5: 0.4}) == 0.0


class TestTheBandsSeparate:
    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_leading_boards_of_a_band_land_in_it(self, band):
        """The thresholds' own claim, re-measured on this build.

        Unlike game three's, this one holds for every board rather than most,
        because the ranges the descriptor records are disjoint.
        """
        pool, values, memberships, index = readable(result(window()))
        plans = []
        for attempt in range(48):
            plan, _ = plan_for(
                pool=pool, values=values, memberships=memberships, index=index,
                board_size=6, tier=tier_for(band),
                rng=DeterministicRng(b"bands" * 8).derive(str(attempt)),
            )
            if plan is not None:
                plans.append(plan)
        measured = []
        for plan in ranked(plans, tier_for(band))[:8]:
            candidate = PuzzleCandidate(
                candidate_id=plan.candidate_id(),
                payload=payload_for(plan),
                fact_refs=plan.dependency_ids(),
            )
            puzzle = assemble(candidate, DeterministicRng(b"lay" * 10))
            measured.append(
                DESCRIPTOR.band_for(measure_difficulty(puzzle, verify(puzzle)).score)
            )
        assert set(measured) == {band}

    def test_the_three_tiers_produce_disjoint_ranges_once_ranked(self):
        """The disjointness the descriptor claims, and the word "ranked" is
        load bearing. See the test below it."""
        ranges = {}
        for tier in (MIN_TIER, MIN_TIER + 1, MAX_TIER):
            scores = [
                measure_difficulty(puzzle, verification).score
                for puzzle, verification in leading(6, tier)
            ]
            ranges[tier] = (min(scores), max(scores))
        ordered = [ranges[tier] for tier in sorted(ranges)]
        for lower, upper in zip(ordered, ordered[1:]):
            assert lower[1] < upper[0], ranges

    def test_unranked_the_ranges_overlap_and_the_ranking_is_why_they_do_not(self):
        """Measured, and worth a test of its own: the band promise rests on
        the ranking as much as on the scale.

        Over every board the generator builds at a tier, the ranges do
        overlap -- a familiar property with one reason outscores an obscure
        one with four, because the reason shortage carries a quarter of the
        weight. What makes the served boards separate is that the generator
        offers fewest-reasons first, so the boards the engine reaches are the
        top of each tier's range rather than all of it.

        Stated as a test because the alternative is a descriptor claiming a
        clean separation that only holds for a sample it does not name.
        """
        spans = {}
        for tier in (MIN_TIER, MIN_TIER + 1):
            scores = [
                measure_difficulty(puzzle, verification).score
                for puzzle, verification in boards(6, tier, 40)
            ]
            spans[tier] = (min(scores), max(scores))
        assert spans[MIN_TIER][1] > spans[MIN_TIER + 1][0]

    def test_the_other_features_still_move_a_board_inside_its_band(self):
        """The separation is partly a tautology -- obscurity is a step
        function of the tier the band selects on -- so what is asserted here
        is that the other three features do something: each band's range is
        an interval rather than a point.
        """
        scores = [
            measure_difficulty(puzzle, verification).score
            for puzzle, verification in boards(6, MIN_TIER + 1, 40)
        ]
        assert max(scores) - min(scores) > 0.05
