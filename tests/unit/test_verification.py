from __future__ import annotations

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core.errors import ContentError, RejectionReason
from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.engine import uniqueness
from puzzlegen.engine.pipeline import GenerationPipeline
from puzzlegen.engine.plugin import (
    AccessibilityDeclaration,
    DifficultyMeasurement,
    DifficultyThresholds,
    GameDescriptor,
    Puzzle,
    VerificationResult,
)
from puzzlegen.engine.records import PuzzleStatus
from puzzlegen.engine.registry import GameRegistry
from puzzlegen.engine.runner import DailyRunner
from puzzlegen.engine.storage import EngineRepositories
from puzzlegen.engine.verifier import DifficultyCalibration, PuzzleVerifier
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider

from ..conftest import NOW
from ..support.games import OddOneOutGame

SEEDS = "content/seeds"
DAY = "2026-09-26"

FREQUENCIES = {
    "tiger": 4.6, "lion": 4.9, "leopard": 4.2, "jaguar": 4.3, "cheetah": 4.1,
    "lynx": 3.4, "albatross": 3.0, "puffin": 2.9, "gannet": 2.2, "petrel": 2.1,
}


def descriptor(**overrides) -> GameDescriptor:
    base = dict(
        game_id="probe",
        display_name="Probe",
        game_version="1.0.0",
        supported_bands=(DifficultyBand.EASY, DifficultyBand.MEDIUM),
        difficulty_thresholds=DifficultyThresholds(cutoffs=(0.5,)),
        accessibility=AccessibilityDeclaration(element_kinds=("tile",)),
    )
    base.update(overrides)
    return GameDescriptor(**base)


def result(**overrides) -> VerificationResult:
    base = dict(
        solvable=True,
        solution_count=1,
        completeness=VerificationCompleteness.COMPLETE,
        states_examined=10,
        solutions=({"answer": "a"},),
    )
    base.update(overrides)
    return VerificationResult(**base)


class TestDifficultyThresholds:
    def test_cutoffs_must_ascend(self):
        with pytest.raises(ValueError):
            DifficultyThresholds(cutoffs=(0.7, 0.3))

    def test_cutoffs_must_be_inside_the_unit_interval(self):
        with pytest.raises(ValueError):
            DifficultyThresholds(cutoffs=(0.0, 0.5))

    def test_duplicate_cutoffs_are_refused(self):
        with pytest.raises(ValueError):
            DifficultyThresholds(cutoffs=(0.5, 0.5))

    def test_a_score_on_a_cutoff_lands_in_the_easier_band(self):
        thresholds = DifficultyThresholds(cutoffs=(0.5,))
        bands = (DifficultyBand.EASY, DifficultyBand.MEDIUM)
        assert thresholds.band_for(0.5, bands) is DifficultyBand.EASY
        assert thresholds.band_for(0.51, bands) is DifficultyBand.MEDIUM

    def test_the_cutoff_count_must_match_the_band_count(self):
        with pytest.raises(ValueError):
            GameDescriptor(
                game_id="g",
                display_name="g",
                game_version="1.0.0",
                supported_bands=(DifficultyBand.EASY, DifficultyBand.MEDIUM),
                difficulty_thresholds=DifficultyThresholds(cutoffs=(0.3, 0.6)),
            )

    def test_bands_must_be_declared_in_ascending_difficulty(self):
        with pytest.raises(ValueError):
            GameDescriptor(
                game_id="g",
                display_name="g",
                game_version="1.0.0",
                supported_bands=(DifficultyBand.MEDIUM, DifficultyBand.EASY),
                difficulty_thresholds=DifficultyThresholds(cutoffs=(0.5,)),
            )

    def test_two_games_may_band_the_same_score_differently(self):
        lenient = descriptor(difficulty_thresholds=DifficultyThresholds(cutoffs=(0.8,)))
        strict = descriptor(difficulty_thresholds=DifficultyThresholds(cutoffs=(0.2,)))
        assert lenient.band_for(0.5) is DifficultyBand.EASY
        assert strict.band_for(0.5) is DifficultyBand.MEDIUM


class TestVerificationResultConsistency:
    def test_a_listed_solution_count_must_match(self):
        with pytest.raises(ValueError):
            VerificationResult(
                solvable=True,
                solution_count=3,
                solutions=({"a": 1},),
            )

    def test_a_truncated_list_is_allowed_to_be_short(self):
        record = VerificationResult(
            solvable=True,
            solution_count=3,
            solutions=({"a": 1},),
            solutions_truncated=True,
        )
        assert record.solutions_truncated

    def test_truncation_claimed_with_a_full_list_is_flagged(self):
        record = VerificationResult(
            solvable=True,
            solution_count=1,
            solutions=({"a": 1},),
            solutions_truncated=True,
        )
        assert uniqueness.consistency_problem(record) is not None

    def test_a_complete_claim_with_no_states_examined_is_flagged(self):
        record = result(states_examined=0)
        assert "examined no states" in uniqueness.consistency_problem(record)

    def test_a_clean_result_has_no_consistency_problem(self):
        assert uniqueness.consistency_problem(result()) is None


class TestUniquenessContracts:
    @pytest.mark.parametrize("contract", sorted(uniqueness.SINGLE_SOLUTION_CONTRACTS))
    def test_single_solution_contracts(self, contract):
        spec = descriptor(uniqueness_contract=contract)
        assert uniqueness.check(spec, result(solution_count=1))
        assert not uniqueness.check(
            spec, result(solution_count=2, solutions=({"a": 1}, {"a": 2}))
        )

    def test_an_unsolvable_puzzle_satisfies_nothing(self):
        spec = descriptor()
        verdict = uniqueness.check(
            spec, VerificationResult(solvable=False, solution_count=0)
        )
        assert not verdict and "no solution" in verdict.reason

    def test_an_incomplete_enumeration_establishes_no_uniqueness(self):
        spec = descriptor()
        verdict = uniqueness.check(
            spec,
            result(completeness=VerificationCompleteness.SOUND_INCOMPLETE),
        )
        assert not verdict
        assert "incomplete enumeration" in verdict.reason

    def test_exactly_n_matches_its_count(self):
        spec = descriptor(
            uniqueness_contract=UniquenessContract.EXACTLY_N,
            expected_solution_count=3,
        )
        three = result(
            solution_count=3, solutions=({"a": 1}, {"a": 2}, {"a": 3})
        )
        assert uniqueness.check(spec, three)
        assert not uniqueness.check(spec, result(solution_count=1))

    def test_a_tolerance_contract_requires_collapse_evidence(self):
        spec = descriptor(
            uniqueness_contract=UniquenessContract.UNIQUE_UP_TO_TOLERANCE
        )
        without = uniqueness.check(spec, result())
        assert not without and "collapsed_solutions" in without.reason
        with_evidence = uniqueness.check(
            spec, result(metrics={uniqueness.COLLAPSED_SOLUTIONS: 2.0})
        )
        assert with_evidence

    def test_a_tolerance_contract_still_needs_one_survivor(self):
        spec = descriptor(
            uniqueness_contract=UniquenessContract.UNIQUE_UP_TO_TOLERANCE
        )
        verdict = uniqueness.check(
            spec,
            result(
                solution_count=2,
                solutions=({"a": 1}, {"a": 2}),
                metrics={uniqueness.COLLAPSED_SOLUTIONS: 1.0},
            ),
        )
        assert not verdict

    def test_a_minimal_path_contract_requires_longer_alternatives(self):
        spec = descriptor(uniqueness_contract=UniquenessContract.UNIQUE_MINIMAL_PATH)
        missing = uniqueness.check(spec, result())
        assert not missing and "longer_alternatives" in missing.reason

        none_exist = uniqueness.check(
            spec, result(metrics={uniqueness.LONGER_ALTERNATIVES: 0.0})
        )
        assert not none_exist
        assert "asks nothing" in none_exist.reason

        good = uniqueness.check(
            spec, result(metrics={uniqueness.LONGER_ALTERNATIVES: 3.0})
        )
        assert good

    def test_a_verdict_is_truthy_when_satisfied(self):
        assert bool(uniqueness.check(descriptor(), result()))


class TestPuzzleVerifier:
    def puzzle(self, solution=None) -> Puzzle:
        return Puzzle(
            game_id="probe",
            payload={"options": ["a", "b"]},
            solution=solution or {"answer": "a"},
            fact_refs=("entity:a",),
        )

    class _Plugin:
        def __init__(self, verification=None, difficulty=None, raises=None):
            self._verification = verification
            self._difficulty = difficulty or DifficultyMeasurement(
                score=0.6, confidence=0.9, features={"x": 1.0}
            )
            self._raises = raises

        def verify(self, puzzle):
            if self._raises == "verify":
                raise RuntimeError("boom")
            return self._verification

        def measure_difficulty(self, puzzle, verification):
            if self._raises == "difficulty":
                raise RuntimeError("boom")
            return self._difficulty

    def test_accepts_a_clean_puzzle(self):
        plugin = self._Plugin(verification=result())
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.accepted
        assert outcome.measured_band is DifficultyBand.MEDIUM

    def test_a_raising_verifier_is_contained(self):
        plugin = self._Plugin(raises="verify")
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert not outcome.accepted
        assert outcome.reason is RejectionReason.PLUGIN_PROTOCOL_ERROR

    def test_a_raising_difficulty_model_is_contained(self):
        plugin = self._Plugin(verification=result(), raises="difficulty")
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert not outcome.accepted
        assert outcome.reason is RejectionReason.PLUGIN_PROTOCOL_ERROR

    def test_an_unsolvable_puzzle_is_rejected(self):
        plugin = self._Plugin(
            verification=VerificationResult(solvable=False, solution_count=0)
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.NO_SOLUTION

    def test_multiple_solutions_are_rejected(self):
        plugin = self._Plugin(
            verification=result(solution_count=2, solutions=({"answer": "a"}, {"answer": "b"}))
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.MULTIPLE_SOLUTIONS

    def test_a_degraded_enumeration_is_rejected(self):
        plugin = self._Plugin(
            verification=result(completeness=VerificationCompleteness.SOUND_INCOMPLETE)
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.VERIFICATION_UNSOUND

    def test_an_incomplete_verification_within_a_declared_bound_is_allowed(self):
        spec = descriptor(
            uniqueness_contract=UniquenessContract.EXACTLY_N,
            expected_solution_count=1,
            verification_completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            search_bound=1000,
        )
        plugin = self._Plugin(
            verification=result(completeness=VerificationCompleteness.SOUND_INCOMPLETE)
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, spec, DifficultyBand.MEDIUM
        )
        # Still refused, because no uniqueness contract survives an incomplete
        # enumeration, but refused for the contract rather than the soundness.
        assert outcome.reason is RejectionReason.UNIQUENESS_CONTRACT_VIOLATED

    def test_exceeding_a_declared_search_bound_is_rejected(self):
        spec = descriptor(
            verification_completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            search_bound=5,
        )
        plugin = self._Plugin(
            verification=result(
                completeness=VerificationCompleteness.SOUND_INCOMPLETE,
                states_examined=1000,
            )
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, spec, DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.VERIFICATION_UNSOUND
        assert "beyond the declared bound" in outcome.detail

    def test_an_inconsistent_result_is_a_protocol_error(self):
        plugin = self._Plugin(verification=result(states_examined=0))
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.PLUGIN_PROTOCOL_ERROR

    def test_a_puzzle_whose_answer_the_solver_never_found_is_rejected(self):
        """Verifies, unique, and marks the player wrong. The worst failure."""
        plugin = self._Plugin(verification=result(solutions=({"answer": "z"},)))
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.UNFAIR_COMBINATION
        assert "stated solution" in outcome.detail

    def test_a_truncated_solution_list_skips_the_intended_check(self):
        """Truncation is honoured, but the contract still has to hold.

        A truncated list means the engine cannot confirm the intended answer
        is among those found, so that check is skipped. The solution count
        itself is unaffected, which is why this one is refused on the
        contract rather than accepted.
        """
        plugin = self._Plugin(
            verification=result(
                solution_count=5,
                solutions=({"answer": "z"},),
                solutions_truncated=True,
            )
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.MULTIPLE_SOLUTIONS
        assert "stated solution" not in outcome.detail

    def test_an_off_target_band_is_rejected_by_default(self):
        plugin = self._Plugin(
            verification=result(),
            difficulty=DifficultyMeasurement(score=0.1, confidence=0.9, features={"x": 1.0}),
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.reason is RejectionReason.DIFFICULTY_OUT_OF_RANGE
        assert outcome.off_target
        assert outcome.measured_band is DifficultyBand.EASY

    def test_off_target_can_be_tolerated(self):
        plugin = self._Plugin(
            verification=result(),
            difficulty=DifficultyMeasurement(score=0.1, confidence=0.9, features={"x": 1.0}),
        )
        outcome = PuzzleVerifier(require_on_target=False).evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        assert outcome.accepted and outcome.off_target

    def test_a_rejection_yields_a_structured_reason(self):
        plugin = self._Plugin(
            verification=VerificationResult(solvable=False, solution_count=0)
        )
        outcome = PuzzleVerifier().evaluate(
            self.puzzle(), plugin, descriptor(), DifficultyBand.MEDIUM
        )
        reason, detail = outcome.rejection()
        assert reason is RejectionReason.NO_SOLUTION and detail


class TestCalibration:
    def test_records_scores_and_bands(self):
        calibration = DifficultyCalibration(game_id="probe")
        calibration.record(0.2, DifficultyBand.EASY)
        calibration.record(0.8, DifficultyBand.MEDIUM)
        summary = calibration.summary()
        assert summary["count"] == 2 and summary["mean"] == pytest.approx(0.5)

    def test_reports_bands_nothing_ever_lands_in(self):
        calibration = DifficultyCalibration(game_id="probe")
        calibration.record(0.2, DifficultyBand.EASY)
        unused = calibration.unused_bands(
            (DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD)
        )
        assert unused == ("MEDIUM", "HARD")

    def test_an_empty_calibration_summarises_to_nothing(self):
        assert DifficultyCalibration(game_id="probe").summary() == {}


@pytest.fixture
def world(repos, store):
    builder = SnapshotBuilder(repos, now=NOW)
    meta, _ = builder.build(
        DAY,
        providers=[(CuratedJSONProvider(f"{SEEDS}/animals.curated.json", now=NOW), {})],
        frequency=TableFrequencyProvider(
            FREQUENCIES, name="wordfreq", version="3.1", retrieved_at=NOW
        ),
        embeddings=DevHashEmbeddingProvider(now=NOW),
    )
    content = ContentService(
        repos,
        policy=PolicyService(ContentPolicy(name="platform", minimum_confidence=0.7)),
        snapshot=meta,
        now=NOW,
    )
    engine = EngineRepositories(store)
    registry = GameRegistry()
    game = OddOneOutGame()
    registry.register(game)
    registry.activate("oddoneout", day_key=DAY)
    pipeline = GenerationPipeline(
        content=content,
        engine=engine,
        registry=registry,
        dependencies=repos.dependencies,
        snapshot=meta,
        now=NOW,
        verifier=PuzzleVerifier(),
    )
    return {"pipeline": pipeline, "engine": engine, "registry": registry, "repos": repos}


class TestVerifiedGeneration:
    def test_generation_carries_its_proof(self, world):
        outcome = world["pipeline"].generate("oddoneout", DAY)
        assert outcome.succeeded
        assert outcome.evaluation is not None
        assert outcome.evaluation.accepted
        assert outcome.evaluation.verification.solution_count == 1

    def test_a_verified_puzzle_is_marked_verified(self, world):
        outcome = world["pipeline"].generate("oddoneout", DAY)
        assert outcome.puzzle.status is PuzzleStatus.VERIFIED

    def test_publishing_reuses_the_accepted_evaluation(self, world):
        outcome = world["pipeline"].generate("oddoneout", DAY)
        manifest = world["pipeline"].publish_outcome(outcome)
        assert manifest.verification.contract_satisfied
        assert manifest.difficulty.on_target
        assert manifest.difficulty.features

    def test_publishing_without_an_evaluation_is_refused(self, repos, store, world):
        pipeline = GenerationPipeline(
            content=world["pipeline"]._content,
            engine=world["engine"],
            registry=world["registry"],
            dependencies=repos.dependencies,
            snapshot=world["pipeline"]._snapshot,
            now=NOW,
        )
        outcome = pipeline.generate("oddoneout", "2026-10-01")
        with pytest.raises(ContentError):
            pipeline.publish_outcome(outcome)

    def test_a_failing_verifier_rejects_every_candidate(self, world):
        game = world["registry"].get("oddoneout").plugin

        def broken(puzzle):
            return VerificationResult(solvable=False, solution_count=0)

        game.verify = broken
        outcome = world["pipeline"].generate("oddoneout", "2026-10-02")
        assert not outcome.succeeded
        assert any(
            "NO_SOLUTION" in reason
            for reason in outcome.trace.rejection_summary()
        )

    def test_the_verifier_gate_is_recorded_in_the_trace(self, world):
        game = world["registry"].get("oddoneout").plugin
        game.measure_difficulty = lambda puzzle, verification: DifficultyMeasurement(
            score=0.01, confidence=0.9, features={"x": 1.0}
        )
        outcome = world["pipeline"].generate("oddoneout", "2026-10-03")
        assert not outcome.succeeded
        assert any(
            "DIFFICULTY_OUT_OF_RANGE" in reason
            for reason in outcome.trace.rejection_summary()
        )


class TestDailyRunner:
    def test_runs_and_publishes_the_days_games(self, world):
        result = DailyRunner(world["pipeline"]).run(DAY)
        assert result.published == ("oddoneout",)
        assert result.complete

    def test_records_calibration(self, world):
        result = DailyRunner(world["pipeline"]).run(DAY)
        calibration = result.calibration["oddoneout"]
        assert calibration.summary()["count"] == 1

    def test_a_failing_game_is_isolated(self, world):
        game = world["registry"].get("oddoneout").plugin
        game.verify = lambda puzzle: VerificationResult(
            solvable=False, solution_count=0
        )
        result = DailyRunner(world["pipeline"]).run(DAY)
        assert not result.complete
        assert "oddoneout" in result.failures
        assert result.published == ()

    def test_retry_bands_are_tried_in_order(self, world):
        runner = DailyRunner(
            world["pipeline"],
            default_band=DifficultyBand.EASY,
            retry_bands=(DifficultyBand.MEDIUM,),
        )
        result = runner.run(DAY)
        # The game's model scores 0.4, which its thresholds band as MEDIUM, so
        # the EASY attempt is refused and the retry succeeds.
        assert result.published == ("oddoneout",)
        assert result.manifests["oddoneout"].difficulty.measured_band is (
            DifficultyBand.MEDIUM
        )

    def test_an_inactive_game_is_not_run(self, world):
        world["registry"].deactivate("oddoneout", day_key=DAY)
        result = DailyRunner(world["pipeline"]).run(DAY)
        assert result.published == () and result.complete

    def test_the_summary_is_serialisable(self, world):
        summary = DailyRunner(world["pipeline"]).run(DAY).summary()
        assert summary["day"] == DAY and summary["published"] == ["oddoneout"]
