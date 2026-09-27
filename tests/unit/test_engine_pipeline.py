from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.port import PortBudget
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core.errors import ConfigurationError, ConflictError, ContentError
from puzzlegen.core.rng import derive_seed
from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.engine.pipeline import (
    GenerationPipeline,
    _contract_satisfied,
    day_key_for,
)
from puzzlegen.engine.plugin import (
    AccessibilityDeclaration,
    DifficultyMeasurement,
    GameDescriptor,
    PuzzleCandidate,
    VerificationResult,
)
from puzzlegen.engine.records import PuzzleStatus
from puzzlegen.engine.registry import ActivationEvent, GameRegistry
from puzzlegen.engine.storage import EngineRepositories
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider

from ..conftest import NOW
from ..support.games import (
    ExplodingGame,
    FabricatingGame,
    ImpatientGame,
    OddOneOutGame,
    RefLessGame,
    WideningGame,
)

SEEDS = "content/seeds"
DAY = "2026-09-26"

FREQUENCIES = {
    "tiger": 4.6, "lion": 4.9, "leopard": 4.2, "jaguar": 4.3, "cheetah": 4.1,
    "lynx": 3.4, "albatross": 3.0, "puffin": 2.9, "gannet": 2.2, "petrel": 2.1,
}


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
    return {
        "repos": repos,
        "snapshot": meta,
        "content": content,
        "engine": engine,
        "registry": registry,
    }


def make_pipeline(world, **kwargs) -> GenerationPipeline:
    return GenerationPipeline(
        content=world["content"],
        engine=world["engine"],
        registry=world["registry"],
        dependencies=world["repos"].dependencies,
        snapshot=world["snapshot"],
        now=NOW,
        **kwargs,
    )


def register(world, game=None):
    game = game or OddOneOutGame()
    world["registry"].register(game)
    world["registry"].activate(game.describe().game_id, day_key=DAY)
    return game


class TestGameDescriptor:
    def test_rejects_a_non_slug_id(self):
        with pytest.raises(ValueError):
            GameDescriptor(game_id="odd one out", display_name="x", game_version="1.0.0")

    def test_rejects_an_uppercase_id(self):
        with pytest.raises(ValueError):
            GameDescriptor(game_id="Odd", display_name="x", game_version="1.0.0")

    def test_exactly_n_requires_a_count(self):
        with pytest.raises(ValueError):
            GameDescriptor(
                game_id="g",
                display_name="x",
                game_version="1.0.0",
                uniqueness_contract=UniquenessContract.EXACTLY_N,
            )

    def test_incomplete_verification_requires_a_bound(self):
        with pytest.raises(ValueError):
            GameDescriptor(
                game_id="g",
                display_name="x",
                game_version="1.0.0",
                verification_completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            )

    def test_a_game_must_support_a_band(self):
        with pytest.raises(ValueError):
            GameDescriptor(
                game_id="g", display_name="x", game_version="1.0.0", supported_bands=()
            )


class TestAccessibilityDeclaration:
    def test_a_complete_declaration_passes(self):
        declaration = AccessibilityDeclaration(element_kinds=("tile",))
        assert declaration.is_complete()[0]

    def test_missing_element_kinds_fails(self):
        assert not AccessibilityDeclaration().is_complete()[0]

    def test_colour_only_information_fails(self):
        declaration = AccessibilityDeclaration(
            element_kinds=("tile",), colour_independent=False
        )
        ok, why = declaration.is_complete()
        assert not ok and "colour" in why

    def test_a_small_target_fails(self):
        declaration = AccessibilityDeclaration(
            element_kinds=("tile",), minimum_target_px=20
        )
        assert not declaration.is_complete()[0]


class TestPluginTypes:
    def test_a_puzzle_must_declare_dependencies(self):
        from puzzlegen.engine.plugin import Puzzle

        with pytest.raises(ValueError):
            Puzzle(game_id="g", payload={"a": 1}, solution={"b": 2}, fact_refs=())

    def test_a_candidate_needs_an_id(self):
        with pytest.raises(ValueError):
            PuzzleCandidate(candidate_id="", payload={})

    def test_a_solvable_result_needs_a_solution(self):
        with pytest.raises(ValueError):
            VerificationResult(solvable=True, solution_count=0)

    def test_an_unsolvable_result_cannot_have_solutions(self):
        with pytest.raises(ValueError):
            VerificationResult(solvable=False, solution_count=2)

    def test_a_difficulty_measurement_must_expose_features(self):
        with pytest.raises(ValueError):
            DifficultyMeasurement(score=0.5)

    def test_a_difficulty_score_is_bounded(self):
        with pytest.raises(ValueError):
            DifficultyMeasurement(score=1.5, features={"a": 1.0})


class TestRegistry:
    def test_registers_and_retrieves(self, world):
        game = OddOneOutGame()
        world["registry"].register(game)
        assert world["registry"].get("oddoneout").plugin is game
        assert "oddoneout" in world["registry"]

    def test_refuses_a_duplicate_version(self, world):
        world["registry"].register(OddOneOutGame())
        with pytest.raises(ConflictError):
            world["registry"].register(OddOneOutGame())

    def test_accepts_an_upgraded_version(self, world):
        world["registry"].register(OddOneOutGame(version="1.0.0"))
        world["registry"].register(OddOneOutGame(version="1.1.0"))
        assert world["registry"].get("oddoneout").descriptor.game_version == "1.1.0"

    def test_refuses_an_incompatible_protocol(self, world):
        class Ancient(OddOneOutGame):
            def describe(self):
                base = super().describe()
                return GameDescriptor(
                    game_id=base.game_id,
                    display_name=base.display_name,
                    game_version=base.game_version,
                    protocol_version="99.0.0",
                    accessibility=base.accessibility,
                )

        with pytest.raises(ConfigurationError):
            world["registry"].register(Ancient())

    def test_activation_is_required_for_scheduling(self, world):
        world["registry"].register(OddOneOutGame())
        assert world["registry"].schedule_for(DAY) == ()
        world["registry"].activate("oddoneout", day_key=DAY)
        assert [g.game_id for g in world["registry"].schedule_for(DAY)] == ["oddoneout"]

    def test_the_active_cap_is_enforced(self, world):
        registry = GameRegistry(max_active=2)
        for index in range(3):
            registry.register(OddOneOutGame(game_id=f"game{index}"))
        registry.activate("game0", day_key=DAY)
        registry.activate("game1", day_key=DAY)
        with pytest.raises(ConflictError):
            registry.activate("game2", day_key=DAY)

    def test_deactivation_frees_a_slot(self, world):
        registry = GameRegistry(max_active=1)
        registry.register(OddOneOutGame(game_id="a"))
        registry.register(OddOneOutGame(game_id="b"))
        registry.activate("a", day_key=DAY)
        registry.deactivate("a", day_key=DAY)
        registry.activate("b", day_key=DAY)
        assert registry.active_on(DAY) == ("b",)

    def test_swap_exchanges_two_games(self, world):
        registry = GameRegistry(max_active=1)
        registry.register(OddOneOutGame(game_id="a"))
        registry.register(OddOneOutGame(game_id="b"))
        registry.activate("a", day_key=DAY)
        registry.swap(out="a", into="b", day_key=DAY)
        assert registry.active_on(DAY) == ("b",)

    def test_activation_history_is_dated_and_reproducible(self, world):
        registry = GameRegistry()
        registry.register(OddOneOutGame(game_id="a"))
        registry.activate("a", day_key="2026-09-01")
        registry.deactivate("a", day_key="2026-09-20")
        assert registry.active_on("2026-09-10") == ("a",)
        assert registry.active_on("2026-09-25") == ()

    def test_a_past_day_is_unaffected_by_a_later_change(self, world):
        registry = GameRegistry()
        registry.register(OddOneOutGame(game_id="a"))
        registry.activate("a", day_key="2026-09-01")
        before = registry.active_on("2026-09-15")
        registry.deactivate("a", day_key="2026-09-20")
        assert registry.active_on("2026-09-15") == before

    def test_activating_an_unregistered_game_is_refused(self, world):
        with pytest.raises(KeyError):
            world["registry"].activate("ghost", day_key=DAY)

    def test_lowering_the_cap_below_the_active_count_is_refused(self, world):
        registry = GameRegistry(max_active=2)
        registry.register(OddOneOutGame(game_id="a"))
        registry.register(OddOneOutGame(game_id="b"))
        registry.activate("a", day_key=DAY)
        registry.activate("b", day_key=DAY)
        with pytest.raises(ConflictError):
            registry.set_max_active(1)

    def test_history_can_be_reloaded(self, world):
        registry = GameRegistry()
        registry.register(OddOneOutGame(game_id="a"))
        registry.load_history([ActivationEvent("a", "2026-09-01", True)])
        assert registry.active_on(DAY) == ("a",)

    def test_an_activation_event_validates_its_day(self):
        with pytest.raises(ValueError):
            ActivationEvent("a", "not-a-date", True)

    def test_unregistering_deactivates_first(self, world):
        registry = GameRegistry()
        registry.register(OddOneOutGame(game_id="a"))
        registry.activate("a", day_key=DAY)
        registry.unregister("a", day_key=DAY)
        assert registry.active_on(DAY) == ()
        assert "a" not in registry


class TestGeneration:
    def test_produces_a_draft_puzzle(self, world):
        register(world)
        outcome = make_pipeline(world).generate("oddoneout", DAY)
        assert outcome.succeeded
        assert outcome.puzzle.status is PuzzleStatus.DRAFT
        assert outcome.puzzle.day_key == DAY

    def test_the_puzzle_is_stored(self, world):
        register(world)
        outcome = make_pipeline(world).generate("oddoneout", DAY)
        assert world["engine"].puzzles.get(outcome.puzzle.id) is not None

    def test_a_trace_is_always_written(self, world):
        register(world)
        make_pipeline(world).generate("oddoneout", DAY)
        assert len(world["engine"].traces.for_day(DAY, "oddoneout")) == 1

    def test_the_trace_records_the_full_recipe(self, world):
        register(world)
        trace = make_pipeline(world).generate("oddoneout", DAY).trace
        assert trace.inputs.seed_hex == derive_seed(DAY, "oddoneout").hex()
        assert trace.inputs.snapshot_id == world["snapshot"].id
        assert trace.versions.game == "1.0.0"
        assert trace.versions.providers

    def test_the_trace_records_content_requests(self, world):
        register(world)
        trace = make_pipeline(world).generate("oddoneout", DAY).trace
        assert set(trace.content_requests) == {"family", "outsiders"}
        assert trace.content_requests["family"]["operation"] == "find_category_members"

    def test_the_trace_records_port_usage(self, world):
        register(world)
        trace = make_pipeline(world).generate("oddoneout", DAY).trace
        assert trace.port_queries == 2
        assert trace.port_results > 0

    def test_generation_is_reproducible(self, world, store):
        register(world)
        first = make_pipeline(world).generate("oddoneout", DAY)
        world["engine"].puzzles.delete(first.puzzle.id)
        second = make_pipeline(world).generate("oddoneout", DAY)
        assert first.puzzle.id == second.puzzle.id
        assert first.puzzle.payload == second.puzzle.payload
        assert first.puzzle.solution == second.puzzle.solution

    def test_a_different_day_produces_a_different_puzzle(self, world):
        register(world)
        pipeline = make_pipeline(world)
        a = pipeline.generate("oddoneout", DAY)
        b = pipeline.generate("oddoneout", "2026-09-27")
        assert a.puzzle.payload != b.puzzle.payload

    def test_a_salt_changes_the_stream(self, world):
        register(world)
        plain = make_pipeline(world).generate("oddoneout", DAY)
        salted = make_pipeline(world, seed_salt="staging").generate("oddoneout", DAY)
        assert plain.puzzle.payload != salted.puzzle.payload

    def test_an_unsealed_snapshot_is_refused(self, world):
        with pytest.raises(ContentError):
            GenerationPipeline(
                content=world["content"],
                engine=world["engine"],
                registry=world["registry"],
                dependencies=world["repos"].dependencies,
                snapshot=world["snapshot"].model_copy(update={"sealed": False}),
            )

    def test_an_unsupported_difficulty_band_fails_cleanly(self, world):
        register(world)
        outcome = make_pipeline(world).generate(
            "oddoneout", DAY, difficulty_target=DifficultyBand.EXPERT
        )
        assert not outcome.succeeded
        assert "difficulty band" in outcome.trace.failure_reason

    def test_an_unsupported_locale_fails_cleanly(self, world):
        register(world)
        outcome = make_pipeline(world).generate("oddoneout", DAY, locale="fr")
        assert not outcome.succeeded
        assert "locale" in outcome.trace.failure_reason

    def test_unmet_content_requirements_fail_with_a_reason(self, world):
        register(world, ImpatientGame(game_id="impatient"))
        outcome = make_pipeline(world).generate("impatient", DAY)
        assert not outcome.succeeded
        assert "INSUFFICIENT_CANDIDATES" in outcome.trace.failure_reason
        assert "family" in outcome.trace.failure_reason


class TestCandidateGates:
    def test_fabricated_references_are_rejected(self, world):
        register(world, FabricatingGame(game_id="fabricator"))
        outcome = make_pipeline(world).generate("fabricator", DAY)
        assert not outcome.succeeded
        reasons = outcome.trace.rejection_summary()
        assert any("PLUGIN_PROTOCOL_ERROR" in r for r in reasons)

    def test_missing_references_are_rejected(self, world):
        register(world, RefLessGame(game_id="refless"))
        outcome = make_pipeline(world).generate("refless", DAY)
        assert not outcome.succeeded
        assert any(
            "MISSING_FACT_REFS" in r for r in outcome.trace.rejection_summary()
        )

    def test_widening_references_during_assembly_is_rejected(self, world):
        register(world, WideningGame(game_id="widener"))
        outcome = make_pipeline(world).generate("widener", DAY)
        assert not outcome.succeeded
        reasons = outcome.trace.rejection_summary()
        assert any("PLUGIN_PROTOCOL_ERROR" in r or "MISSING_FACT_REFS" in r for r in reasons)

    def test_an_exploding_assembly_is_contained(self, world):
        register(world, ExplodingGame(game_id="exploder"))
        outcome = make_pipeline(world).generate("exploder", DAY)
        assert not outcome.succeeded
        assert any(
            "PLUGIN_PROTOCOL_ERROR" in r for r in outcome.trace.rejection_summary()
        )

    def test_duplicate_candidate_ids_are_counted(self, world):
        class Repeater(OddOneOutGame):
            def generate_candidates(self, context):
                base = list(super().generate_candidates(context))
                return [*base, base[0]]

        register(world, Repeater(game_id="repeater"))
        outcome = make_pipeline(world).generate("repeater", DAY)
        assert outcome.succeeded
        assert any(
            "DUPLICATE_ENTITY" in r for r in outcome.trace.rejection_summary()
        )

    def test_the_port_budget_failure_is_reported_not_raised(self, world):
        register(world)
        outcome = make_pipeline(
            world, port_budget=PortBudget(max_queries=1)
        ).generate("oddoneout", DAY)
        assert not outcome.succeeded
        assert "boundary violation" in outcome.trace.failure_reason


class TestPublication:
    def _generate(self, world):
        register(world)
        pipeline = make_pipeline(world)
        outcome = pipeline.generate("oddoneout", DAY)
        game = world["registry"].get("oddoneout").plugin
        puzzle = outcome.puzzle
        from puzzlegen.engine.plugin import Puzzle as EnginePuzzle

        assembled = EnginePuzzle(
            game_id=puzzle.game_id,
            payload=puzzle.payload,
            solution=puzzle.solution,
            fact_refs=puzzle.fact_refs,
        )
        verification = game.verify(assembled)
        difficulty = game.measure_difficulty(assembled, verification)
        return pipeline, outcome, game, verification, difficulty

    def test_publishes_a_verified_puzzle(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        assert manifest.verification.contract_satisfied
        assert world["engine"].puzzles.require(outcome.puzzle.id).status is (
            PuzzleStatus.PUBLISHED
        )

    def test_a_manifest_carries_no_solution_or_payload(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        dumped = manifest.model_dump(mode="json")
        assert "solution" not in dumped and "payload" not in dumped

    def test_the_public_view_leaks_no_dependency_ledger(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        public = manifest.public_view()
        answer = outcome.puzzle.solution["answer"]
        assert answer not in str(public)
        assert not any(
            ref in str(public) for ref in manifest.fact_refs
        )
        assert "fact_refs" not in public and "puzzle_content_hash" not in public

    def test_an_unsolvable_puzzle_cannot_be_published(self, world):
        pipeline, outcome, game, _, difficulty = self._generate(world)
        with pytest.raises(ContentError):
            pipeline.publish(
                outcome.puzzle,
                verification=VerificationResult(solvable=False, solution_count=0),
                difficulty=difficulty,
                measured_band=DifficultyBand.MEDIUM,
                descriptor=game.describe(),
                generation_id=outcome.trace.id,
            )

    def test_multiple_solutions_violate_the_contract(self, world):
        pipeline, outcome, game, _, difficulty = self._generate(world)
        with pytest.raises(ContentError) as exc:
            pipeline.publish(
                outcome.puzzle,
                verification=VerificationResult(
                    solvable=True, solution_count=2, solutions=({"a": 1}, {"a": 2})
                ),
                difficulty=difficulty,
                measured_band=DifficultyBand.MEDIUM,
                descriptor=game.describe(),
                generation_id=outcome.trace.id,
            )
        assert "UNIQUENESS_CONTRACT_VIOLATED" in str(exc.value)

    def test_a_degraded_verification_is_refused(self, world):
        pipeline, outcome, game, _, difficulty = self._generate(world)
        with pytest.raises(ContentError) as exc:
            pipeline.publish(
                outcome.puzzle,
                verification=VerificationResult(
                    solvable=True,
                    solution_count=1,
                    completeness=VerificationCompleteness.SOUND_INCOMPLETE,
                    solutions=({"a": 1},),
                ),
                difficulty=difficulty,
                measured_band=DifficultyBand.MEDIUM,
                descriptor=game.describe(),
                generation_id=outcome.trace.id,
            )
        assert "VERIFICATION_UNSOUND" in str(exc.value)

    def test_an_off_target_difficulty_is_refused(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        with pytest.raises(ContentError) as exc:
            pipeline.publish(
                outcome.puzzle,
                verification=verification,
                difficulty=difficulty,
                measured_band=DifficultyBand.EASY,
                descriptor=game.describe(),
                generation_id=outcome.trace.id,
            )
        assert "DIFFICULTY_OUT_OF_RANGE" in str(exc.value)

    def test_an_off_target_difficulty_can_be_accepted_explicitly(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.EASY,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
            allow_off_target=True,
        )
        assert not manifest.difficulty.on_target

    def test_dependencies_are_recorded(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        edges = world["repos"].dependencies.dependencies_of(manifest.id)
        assert len(edges) == len(manifest.fact_refs)

    def test_blast_radius_finds_the_published_puzzle(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        some_ref = manifest.fact_refs[0]
        radius = world["repos"].dependencies.blast_radius(some_ref)
        assert radius["puzzle_count"] == 1
        assert radius["game_ids"] == ["oddoneout"]

    def test_a_published_manifest_is_immutable(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        tampered = manifest.model_copy(
            update={"difficulty": manifest.difficulty.model_copy(update={"score": 0.99})}
        )
        with pytest.raises(ConflictError):
            world["engine"].manifests.put(tampered)

    def test_republishing_identical_content_is_idempotent(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        args = dict(
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        first = pipeline.publish(outcome.puzzle, **args)
        second = pipeline.publish(outcome.puzzle, **args)
        assert first.manifest_hash() == second.manifest_hash()

    def test_a_manifest_describes_its_own_regeneration(self, world):
        pipeline, outcome, game, verification, difficulty = self._generate(world)
        manifest = pipeline.publish(
            outcome.puzzle,
            verification=verification,
            difficulty=difficulty,
            measured_band=DifficultyBand.MEDIUM,
            descriptor=game.describe(),
            generation_id=outcome.trace.id,
        )
        assert manifest.is_reproducible_from(manifest)
        assert manifest.inputs.seed_hex == derive_seed(DAY, "oddoneout").hex()
        assert manifest.inputs.snapshot_content_hash == world["snapshot"].content_hash


class TestContractChecking:
    @pytest.mark.parametrize(
        "contract",
        [
            UniquenessContract.EXACTLY_ONE,
            UniquenessContract.UNIQUE_GROUPING,
            UniquenessContract.UNIQUE_ORDERING,
            UniquenessContract.UNIQUE_DEDUCTION,
            UniquenessContract.NO_ALTERNATE_INTERPRETATION,
        ],
    )
    def test_single_solution_contracts_require_exactly_one(self, contract):
        assert _contract_satisfied(contract, 1, None)
        assert not _contract_satisfied(contract, 2, None)
        assert not _contract_satisfied(contract, 0, None)

    @pytest.mark.parametrize(
        "contract",
        [
            UniquenessContract.UNIQUE_UP_TO_TOLERANCE,
            UniquenessContract.UNIQUE_MINIMAL_PATH,
        ],
    )
    def test_evidence_bearing_contracts_fail_the_coarse_shim(self, contract):
        """These two need evidence a bare count cannot supply.

        The shim is deliberately stricter than the real rule: a caller
        publishing a hand-built result for one of these contracts must go
        through the verifier, which sees the metrics.
        """
        assert not _contract_satisfied(contract, 1, None)

    def test_exactly_n_matches_its_declared_count(self):
        assert _contract_satisfied(UniquenessContract.EXACTLY_N, 3, 3)
        assert not _contract_satisfied(UniquenessContract.EXACTLY_N, 4, 3)
        assert not _contract_satisfied(UniquenessContract.EXACTLY_N, 3, None)


class TestTraceAnalysis:
    def test_rejection_totals_aggregate_across_runs(self, world):
        register(world, RefLessGame(game_id="refless"))
        pipeline = make_pipeline(world)
        pipeline.generate("refless", DAY)
        pipeline.generate("refless", "2026-09-27")
        totals = world["engine"].traces.rejection_totals("refless")
        assert any("MISSING_FACT_REFS" in key for key in totals)
        assert sum(totals.values()) > 0

    def test_failures_are_listable(self, world):
        register(world, RefLessGame(game_id="refless"))
        make_pipeline(world).generate("refless", DAY)
        assert len(world["engine"].traces.failures()) == 1

    def test_a_successful_run_records_no_failure_reason(self, world):
        register(world)
        trace = make_pipeline(world).generate("oddoneout", DAY).trace
        assert trace.succeeded and trace.failure_reason is None

    def test_day_key_helper_formats_a_date(self):
        assert day_key_for(dt.date(2026, 9, 26)) == "2026-09-26"
