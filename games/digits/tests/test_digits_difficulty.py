"""The difficulty model, and where code length is and is not divided out.

This model is the smallest in the repository -- three features -- and the one
with a measured size effect it does not remove. Both halves are tested: the
bands separate over the boards the engine reaches, and the gap between the two
code lengths stays inside a band rather than crossing a cut point.
"""

from __future__ import annotations

import statistics

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import DifficultyBand, VerificationCompleteness
from puzzlegen.engine.plugin import PuzzleCandidate, VerificationResult
from puzzlegen_digits.assemble import assemble
from puzzlegen_digits.descriptor import (
    CODE_LENGTHS,
    DESCRIPTOR,
    MAX_CLUES,
    PROPERTY_COUNT,
    properties_for,
)
from puzzlegen_digits.difficulty import (
    CONFIDENCE,
    ENDGAME_SATURATION,
    WEIGHTS,
    clue_share,
    endgame_share,
    measure_difficulty,
    size_neutrality_check,
    unstated_share,
)
from puzzlegen_digits.generate import payload_for, plan_for, ranked, readable
from puzzlegen_digits.lock import PROPERTY_KINDS
from puzzlegen_digits.verify import verify

from boards import digits, make_puzzle, result

EMPTY = VerificationResult(
    solvable=True,
    solution_count=1,
    completeness=VerificationCompleteness.COMPLETE,
    metrics={},
)


def boards(code_length: int, properties, count: int = 12, seed: bytes = b"dif" * 10):
    """Real boards, assembled, with their verifications."""
    chosen, known, entity_ids, index = readable(result(digits()))
    made = []
    for attempt in range(count):
        plan, _ = plan_for(
            digits=chosen, facts=known, entity_ids=entity_ids, index=index,
            code_length=code_length, properties=properties,
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


def leading(code_length: int, band: DifficultyBand, window: int = 8):
    """The boards the engine reaches: the first few the generator offers."""
    chosen, known, entity_ids, index = readable(result(digits()))
    plans = []
    for attempt in range(24):
        plan, _ = plan_for(
            digits=chosen, facts=known, entity_ids=entity_ids, index=index,
            code_length=code_length, properties=properties_for(band),
            rng=DeterministicRng(b"lead" * 8).derive(str(attempt)),
        )
        if plan is not None:
            plans.append(plan)
    made = []
    for plan in ranked(plans)[:window]:
        candidate = PuzzleCandidate(
            candidate_id=plan.candidate_id(),
            payload=payload_for(plan),
            fact_refs=plan.dependency_ids(),
        )
        puzzle = assemble(candidate, DeterministicRng(b"lay" * 10))
        made.append((puzzle, verify(puzzle)))
    return made


class TestTheFeatures:
    def test_stating_everything_withholds_nothing(self):
        puzzle = make_puzzle(properties=PROPERTY_KINDS)
        assert unstated_share(puzzle, EMPTY) == pytest.approx(0.0)

    def test_stating_nothing_withholds_everything(self):
        assert unstated_share(make_puzzle(), EMPTY) == pytest.approx(1.0)

    def test_stating_one_fact_withholds_the_rest(self):
        puzzle = make_puzzle(properties=("primes",))
        assert unstated_share(puzzle, EMPTY) == pytest.approx(
            1.0 - 1.0 / PROPERTY_COUNT
        )

    def test_the_clue_share_is_the_rows_over_the_ceiling(self):
        puzzle = make_puzzle()
        assert clue_share(puzzle, EMPTY) == pytest.approx(3 / MAX_CLUES)

    def test_the_clue_share_saturates_at_the_ceiling(self):
        puzzle, verification = boards(4, (), 1)[0]
        assert 0.0 <= clue_share(puzzle, verification) <= 1.0

    def test_the_endgame_share_is_the_survivors_before_the_last_clue(self):
        puzzle = make_puzzle()
        assert endgame_share(puzzle, EMPTY) == pytest.approx(
            4 / ENDGAME_SATURATION
        )

    def test_the_endgame_saturation_is_inside_the_observed_range(self):
        """A saturation at the top of the range would compress nearly every
        board into the bottom of the feature."""
        seen = [
            verification.metrics["before_last"]
            for verification in (v for _, v in boards(4, (), 12))
        ]
        assert ENDGAME_SATURATION >= statistics.median(seen)

    def test_the_features_read_the_verifiers_metrics_when_there_are_any(self):
        puzzle, verification = boards(3, ("primes",), 1)[0]
        assert clue_share(puzzle, verification) == pytest.approx(
            verification.metrics["clue_count"] / MAX_CLUES
        )

    def test_the_features_fall_back_to_the_board_without_a_verification(self):
        """The diagnostic tools measure difficulty without one."""
        puzzle, _ = boards(3, ("primes",), 1)[0]
        assert clue_share(puzzle, EMPTY) == pytest.approx(
            puzzle.payload["clue_count"] / MAX_CLUES
        )

    def test_every_feature_lies_on_the_unit_interval(self):
        for length in CODE_LENGTHS:
            for props in ((), ("primes",), PROPERTY_KINDS):
                for puzzle, verification in boards(length, props, 6):
                    measured = measure_difficulty(puzzle, verification)
                    for name in WEIGHTS:
                        assert 0.0 <= measured.features[name] <= 1.0


class TestTheScore:
    def test_the_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_the_score_is_the_weighted_sum_of_the_features(self):
        puzzle, verification = boards(3, ("primes",), 1)[0]
        measured = measure_difficulty(puzzle, verification)
        expected = sum(WEIGHTS[name] * measured.features[name] for name in WEIGHTS)
        assert measured.score == pytest.approx(expected)

    def test_the_measurement_exposes_the_features_that_produced_it(self):
        puzzle, verification = boards(3, (), 1)[0]
        measured = measure_difficulty(puzzle, verification)
        assert set(WEIGHTS) <= set(measured.features)
        assert measured.detail

    def test_the_raw_shape_of_the_board_is_recorded_beside_the_score(self):
        puzzle, verification = boards(4, (), 1)[0]
        features = measure_difficulty(puzzle, verification).features
        assert features["code_length"] == 4.0
        assert features["space_size"] == 5040.0

    def test_the_confidence_is_modest_and_says_so(self):
        assert CONFIDENCE <= 0.5
        puzzle, verification = boards(3, (), 1)[0]
        assert measure_difficulty(puzzle, verification).confidence == CONFIDENCE


class TestWhereCodeLengthGoes:
    def test_the_two_lengths_differ_by_less_than_a_band(self):
        """The model's honest claim. The gap is real -- a four-digit board
        needs about one more clue -- and what matters is that it moves a board
        inside its band rather than across a cut point.
        """
        for band in (DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD):
            means = {}
            for length in CODE_LENGTHS:
                scores = [
                    measure_difficulty(puzzle, verification).score
                    for puzzle, verification in leading(length, band)
                ]
                means[length] = statistics.mean(scores)
            assert size_neutrality_check(means) < 0.1, (band, means)

    def test_the_gap_is_largest_at_the_easy_end(self):
        """Because that is where the clue counts differ most: at the hard end
        both lengths run into the eight-clue ceiling."""
        gaps = {}
        for band in (DifficultyBand.EASY, DifficultyBand.HARD):
            means = {
                length: statistics.mean(
                    measure_difficulty(puzzle, verification).score
                    for puzzle, verification in leading(length, band)
                )
                for length in CODE_LENGTHS
            }
            gaps[band] = size_neutrality_check(means)
        assert gaps[DifficultyBand.EASY] >= gaps[DifficultyBand.HARD]

    def test_the_neutrality_check_reports_the_spread(self):
        assert size_neutrality_check({3: 0.4, 4: 0.5}) == pytest.approx(0.1)
        assert size_neutrality_check({3: 0.4}) == 0.0


class TestTheBandsSeparate:
    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_leading_boards_of_a_band_land_in_it(self, band):
        for length in CODE_LENGTHS:
            measured = [
                DESCRIPTOR.band_for(measure_difficulty(puzzle, verification).score)
                for puzzle, verification in leading(length, band)
            ]
            assert set(measured) == {band}, (length, measured)

    def test_the_three_bands_produce_disjoint_ranges(self):
        ranges = {}
        for band in (DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD):
            scores = [
                measure_difficulty(puzzle, verification).score
                for length in CODE_LENGTHS
                for puzzle, verification in leading(length, band)
            ]
            ranges[band] = (min(scores), max(scores))
        ordered = [
            ranges[band]
            for band in (
                DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD
            )
        ]
        for lower, upper in zip(ordered, ordered[1:]):
            assert lower[1] < upper[0], ranges

    def test_the_other_two_features_move_a_board_inside_its_band(self):
        """The separation is partly a tautology -- the largest weight is how
        many facts the board withholds, which is what the band selects -- so
        what is asserted is that the rest does something."""
        scores = [
            measure_difficulty(puzzle, verification).score
            for puzzle, verification in leading(4, DifficultyBand.MEDIUM)
        ]
        assert max(scores) - min(scores) > 0.02
