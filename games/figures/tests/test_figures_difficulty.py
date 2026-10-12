"""The difficulty model, and the claim that board size is divided out.

Every feature is a share, which is what makes a nine-tile and a twelve-tile
board comparable. That is a claim about numbers, so it is measured here
against boards the generator actually builds rather than asserted in the
module's docstring.
"""

from __future__ import annotations

import statistics

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import DifficultyBand
from puzzlegen_figures.descriptor import BOARD_SIZES, DESCRIPTOR, band_target
from puzzlegen_figures.difficulty import (
    CONFIDENCE,
    DECOY_SATURATION,
    WEIGHTS,
    decoy_share,
    dispersion_share,
    measure_difficulty,
    size_neutrality_check,
    variation_share,
    variety_share,
)
from puzzlegen_figures.generate import payload_for, plan_for, ranked, readable
from puzzlegen_figures.assemble import assemble
from puzzlegen_figures.trio import ATTRIBUTES, check_space
from puzzlegen_figures.verify import verify
from puzzlegen.engine.plugin import PuzzleCandidate, VerificationResult
from puzzlegen.core.types import VerificationCompleteness

from boards import INERT_READINGS, TRIO_READINGS, figure, make_puzzle, result, whole_space

EMPTY = VerificationResult(
    solvable=True,
    solution_count=1,
    completeness=VerificationCompleteness.COMPLETE,
    metrics={},
)


def boards(board_size: int, count: int = 40, seed: bytes = b"difficulty" * 4):
    """Real boards, assembled, with their verifications."""
    views = whole_space()
    pool, figures, index = readable(result(views))
    space = check_space([figures[v.entity_id] for v in pool])
    made = []
    for attempt in range(count):
        plan, _ = plan_for(
            pool=pool, figures=figures, index=index, space=space,
            board_size=board_size, rng=DeterministicRng(seed).derive(str(attempt)),
        )
        candidate = PuzzleCandidate(
            candidate_id=plan.candidate_id(),
            payload=payload_for(plan),
            fact_refs=plan.dependency_ids(),
        )
        puzzle = assemble(candidate, DeterministicRng(seed).derive(f"lay{attempt}"))
        made.append((puzzle, verify(puzzle)))
    return made


class TestTheFeatures:
    def test_variation_is_the_share_of_attributes_all_different(self):
        puzzle = make_puzzle()
        assert puzzle.solution["varying"] == ["shape"]
        assert variation_share(puzzle) == pytest.approx(0.25)

    def test_a_trio_all_different_everywhere_scores_one(self):
        readings = [
            figure("triangle", "solid", "one", "red"),
            figure("circle", "hollow", "two", "green"),
            figure("square", "striped", "three", "blue"),
            *INERT_READINGS,
        ]
        assert variation_share(make_puzzle(readings)) == pytest.approx(1.0)

    def test_the_decoy_share_is_read_from_the_verifier(self):
        puzzle, verification = boards(9, 1)[0]
        assert decoy_share(puzzle, verification) == pytest.approx(
            min(1.0, verification.metrics["decoy_share"] / DECOY_SATURATION)
        )

    def test_the_decoy_share_falls_back_to_the_boards_own_record(self):
        """The diagnostic tools measure difficulty without a verification."""
        puzzle = make_puzzle(decoy_share=0.1)
        assert decoy_share(puzzle, EMPTY) == pytest.approx(0.1 / DECOY_SATURATION)

    def test_the_decoy_saturation_is_above_what_boards_reach(self):
        """A saturation below the observed ceiling would clip; one far above
        it would compress the feature into the bottom of its range."""
        reached = [
            verification.metrics["decoy_share"]
            for _, verification in boards(9, 40) + boards(12, 40)
        ]
        assert max(reached) <= DECOY_SATURATION + 0.02

    def test_variety_is_the_mean_share_of_values_in_play(self):
        """A board where every tile is red has nothing to compare on colour."""
        all_red = [
            *TRIO_READINGS,
            *[{**r, "colour": "red"} for r in INERT_READINGS],
        ]
        narrow = variety_share(make_puzzle(all_red))
        wide = variety_share(boards(9, 1)[0][0])
        assert narrow < wide

    def test_a_clustered_trio_disperses_less_than_a_spread_one(self):
        clustered = make_puzzle(
            [
                TRIO_READINGS[0], TRIO_READINGS[1], INERT_READINGS[0],
                TRIO_READINGS[2], INERT_READINGS[1], INERT_READINGS[2],
                INERT_READINGS[3], INERT_READINGS[4], INERT_READINGS[5],
            ],
            trio=(0, 1, 3),
        )
        spread = make_puzzle(
            [
                TRIO_READINGS[0], INERT_READINGS[0], TRIO_READINGS[1],
                INERT_READINGS[1], INERT_READINGS[2], INERT_READINGS[3],
                INERT_READINGS[4], TRIO_READINGS[2], INERT_READINGS[5],
            ],
            trio=(0, 2, 7),
        )
        assert dispersion_share(clustered) == pytest.approx(0.5)
        assert dispersion_share(spread) > dispersion_share(clustered)

    def test_a_row_and_a_diagonal_disperse_alike(self):
        """Not an oversight: Chebyshev distance says a diagonal is as close as
        a row, which is the reason it was chosen over Manhattan."""
        row = make_puzzle([*TRIO_READINGS, *INERT_READINGS], trio=(0, 1, 2))
        diagonal = make_puzzle(
            [
                TRIO_READINGS[0], INERT_READINGS[0], INERT_READINGS[1],
                INERT_READINGS[2], TRIO_READINGS[1], INERT_READINGS[3],
                INERT_READINGS[4], INERT_READINGS[5], TRIO_READINGS[2],
            ],
            trio=(0, 4, 8),
        )
        assert dispersion_share(row) == pytest.approx(dispersion_share(diagonal))

    def test_a_trio_the_board_does_not_hold_disperses_nothing(self):
        puzzle = make_puzzle()
        tampered = type(puzzle)(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution={**puzzle.solution, "trio": ["entity:a", "entity:b", "entity:c"]},
            fact_refs=puzzle.fact_refs,
        )
        assert dispersion_share(tampered) == 0.0

    def test_every_feature_lies_on_the_unit_interval(self):
        for puzzle, verification in boards(9, 20) + boards(12, 20):
            measured = measure_difficulty(puzzle, verification)
            for name in WEIGHTS:
                assert 0.0 <= measured.features[name] <= 1.0


class TestTheScore:
    def test_the_weights_sum_to_one(self):
        """So the score needs no rescaling and a weight reads as a share."""
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_score_is_the_weighted_sum_of_the_features(self):
        puzzle, verification = boards(9, 1)[0]
        measured = measure_difficulty(puzzle, verification)
        expected = sum(WEIGHTS[name] * measured.features[name] for name in WEIGHTS)
        assert measured.score == pytest.approx(expected)

    def test_the_measurement_exposes_the_features_that_produced_it(self):
        """An unexplained score cannot be audited or improved, and the engine
        refuses a measurement that reports none."""
        puzzle, verification = boards(9, 1)[0]
        measured = measure_difficulty(puzzle, verification)
        assert set(WEIGHTS) <= set(measured.features)
        assert measured.detail

    def test_the_raw_shape_of_the_board_is_recorded_beside_the_score(self):
        puzzle, verification = boards(12, 1)[0]
        features = measure_difficulty(puzzle, verification).features
        assert features["board_size"] == 12.0
        assert features["varying_attributes"] == float(len(puzzle.solution["varying"]))

    def test_the_confidence_is_modest_and_says_so(self):
        """These weights are reasoned rather than fitted to play data."""
        assert CONFIDENCE <= 0.5
        puzzle, verification = boards(9, 1)[0]
        assert measure_difficulty(puzzle, verification).confidence == CONFIDENCE


class TestBoardSizeIsDividedOut:
    def test_the_two_sizes_score_alike_on_comparable_boards(self):
        """The model's central claim, measured.

        Forty boards at each size, means compared. A model that let board size
        through would put every twelve-tile board in a higher band for a
        reason the player cannot feel.
        """
        means = {}
        for size in BOARD_SIZES:
            scores = [
                measure_difficulty(puzzle, verification).score
                for puzzle, verification in boards(size, 40)
            ]
            means[size] = statistics.mean(scores)
        assert size_neutrality_check(means) < 0.1

    def test_the_neutrality_check_reports_the_spread(self):
        assert size_neutrality_check({9: 0.4, 12: 0.5}) == pytest.approx(0.1)
        assert size_neutrality_check({9: 0.4}) == 0.0


class TestTheBandsSeparate:
    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_leading_boards_of_a_band_mostly_land_in_it(self, band):
        """The thresholds' own claim, re-measured on this build.

        The generator ranks boards by the band's target and the engine takes
        the first that lands in the band, so what has to hold is that the
        leading boards mostly do. 'Mostly' is the honest word: the EASY and
        MEDIUM score ranges overlap, which the descriptor records, and the
        cost is verifications rather than days.
        """
        views = whole_space()
        pool, figures, index = readable(result(views))
        space = check_space([figures[v.entity_id] for v in pool])
        plans = []
        for attempt in range(48):
            plan, _ = plan_for(
                pool=pool, figures=figures, index=index, space=space,
                board_size=9, rng=DeterministicRng(b"bands" * 8).derive(str(attempt)),
            )
            plans.append(plan)
        leading = ranked(plans, band_target(band))[:8]
        measured = []
        for plan in leading:
            candidate = PuzzleCandidate(
                candidate_id=plan.candidate_id(),
                payload=payload_for(plan),
                fact_refs=plan.dependency_ids(),
            )
            puzzle = assemble(candidate, DeterministicRng(b"lay" * 10))
            score = measure_difficulty(puzzle, verify(puzzle)).score
            measured.append(DESCRIPTOR.band_for(score))
        assert measured.count(band) >= len(measured) // 2

    def test_the_three_bands_are_ordered_by_the_scores_they_produce(self):
        """A board built for HARD must not score below one built for EASY."""
        views = whole_space()
        pool, figures, index = readable(result(views))
        space = check_space([figures[v.entity_id] for v in pool])
        plans = []
        for attempt in range(48):
            plan, _ = plan_for(
                pool=pool, figures=figures, index=index, space=space,
                board_size=9, rng=DeterministicRng(b"order" * 8).derive(str(attempt)),
            )
            plans.append(plan)
        medians = []
        for band in (DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD):
            scores = []
            for plan in ranked(plans, band_target(band))[:8]:
                candidate = PuzzleCandidate(
                    candidate_id=plan.candidate_id(),
                    payload=payload_for(plan),
                    fact_refs=plan.dependency_ids(),
                )
                puzzle = assemble(candidate, DeterministicRng(b"lay" * 10))
                scores.append(measure_difficulty(puzzle, verify(puzzle)).score)
            medians.append(statistics.median(scores))
        assert medians == sorted(medians)
