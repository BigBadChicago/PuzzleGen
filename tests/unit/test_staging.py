"""Protocol 1.3: stages, content rounds and difficulty axes.

Each test here is the record of one rule, in the sense `CLAUDE.md` means: the
reason is in the docstring, and the rule cannot quietly stop being true. The
design these enforce is in `docs/architecture.md`, and why it was chosen over
hiding stages inside an opaque payload is in `docs/decision-log.md`.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core.errors import ContentError, RejectionReason
from puzzlegen.core.types import (
    DifficultyBand,
    DifficultyReduction,
    UniquenessContract,
)
from puzzlegen.engine.pipeline import GenerationPipeline
from puzzlegen.engine.plugin import (
    DifficultyMeasurement,
    GameDescriptor,
    Puzzle,
    PuzzlePart,
    dependency_order,
)
from puzzlegen.engine.records import PuzzleStatus
from puzzlegen.engine.registry import GameRegistry
from puzzlegen.engine.storage import EngineRepositories
from puzzlegen.engine.verifier import PuzzleVerifier
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider

from ..conftest import NOW
from ..support.games import (
    ChainGame,
    DeadEndChainGame,
    MisreducedAxesGame,
    RepeatingRefiningGame,
    UndeclaredStageGame,
    UngatedChainGame,
    UnrefiningGame,
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
    return {
        "repos": repos,
        "snapshot": meta,
        "content": content,
        "engine": EngineRepositories(store),
        "registry": GameRegistry(),
    }


def run(world, game, **kwargs):
    world["registry"].register(game)
    world["registry"].activate(game.describe().game_id, day_key=DAY)
    pipeline = GenerationPipeline(
        content=world["content"],
        engine=world["engine"],
        registry=world["registry"],
        dependencies=world["repos"].dependencies,
        snapshot=world["snapshot"],
        now=NOW,
        verifier=PuzzleVerifier(require_on_target=False),
        **kwargs,
    )
    return pipeline, pipeline.generate(
        "chain", DAY, difficulty_target=DifficultyBand.MEDIUM
    )


def rejections(outcome) -> str:
    """Every structured rejection the run recorded, as one searchable string.

    The trace's own ``failure_reason`` is the day's summary ("no candidate
    assembled into a valid puzzle"), which is the right thing for an operator
    and useless for asserting which gate fired. The per-stage tallies are
    where the gate names itself.
    """
    return " | ".join(
        reason for tally in outcome.trace.candidates_rejected for reason in tally.reasons
    )


# -- the shapes themselves ------------------------------------------------


class TestPuzzleParts:
    def test_a_staged_board_carries_every_stage_answer(self):
        """A staged board's solution is its stages' answers, keyed by stage.

        Everything downstream of verification reads one answer object, so a
        stage whose answer is missing from it would be invisible to grading,
        sharing and retraction.
        """
        part = PuzzlePart(part_id="one", payload={"a": 1}, solution={"x": 1})
        with pytest.raises(ValueError, match="does not carry stage"):
            Puzzle(
                game_id="g",
                payload={"a": 1},
                solution={"wrong": {"x": 1}},
                fact_refs=("entity:a",),
                parts=(part,),
            )

    def test_a_stage_answer_must_match_the_board(self):
        part = PuzzlePart(part_id="one", payload={"a": 1}, solution={"x": 1})
        with pytest.raises(ValueError, match="is not the stage's own solution"):
            Puzzle(
                game_id="g",
                payload={"a": 1},
                solution={"one": {"x": 2}},
                fact_refs=("entity:a",),
                parts=(part,),
            )

    def test_a_cycle_is_a_board_with_no_first_move(self):
        """Stages that depend on each other can never be started.

        Caught at assembly rather than at verification, because a cycle has no
        dependency order and every later check is written over one.
        """
        a = PuzzlePart(
            part_id="a", payload={"p": 1}, solution={"x": 1}, requires=("b",)
        )
        b = PuzzlePart(
            part_id="b", payload={"p": 1}, solution={"x": 2}, requires=("a",)
        )
        with pytest.raises(ValueError, match="cycle"):
            dependency_order((a, b))

    def test_a_requirement_must_name_a_declared_stage(self):
        part = PuzzlePart(
            part_id="a", payload={"p": 1}, solution={"x": 1}, requires=("ghost",)
        )
        with pytest.raises(ValueError, match="which no part declares"):
            dependency_order((part,))

    def test_dependency_order_follows_declaration_where_free(self):
        """Two runs must order a board's stages identically.

        The manifest hashes the stages, so an order that depended on set
        iteration would move a day's hash without the day changing.
        """
        a = PuzzlePart(part_id="a", payload={"p": 1}, solution={"x": 1})
        b = PuzzlePart(part_id="b", payload={"p": 1}, solution={"x": 2})
        c = PuzzlePart(
            part_id="c", payload={"p": 1}, solution={"x": 3}, requires=("a", "b")
        )
        assert [p.part_id for p in dependency_order((b, a, c))] == ["b", "a", "c"]


class TestStagedDeclaration:
    def test_a_staged_game_may_not_declare_a_witness(self):
        """A witness's costs are counted over one unconstrained space.

        On a staged board that space is the wrong denominator for every stage
        after the first, so its rungs would price as nearly free. Per-stage
        witnesses are specified and unbuilt; until they exist a staged game
        declares none rather than declaring a mis-costed one.
        """
        with pytest.raises(ValueError, match="staged game may not declare"):
            GameDescriptor(
                game_id="g",
                display_name="x",
                game_version="1.0.0",
                staged=True,
                supplies_witness=True,
            )

    def test_content_rounds_are_bounded(self):
        with pytest.raises(ValueError, match="content_rounds"):
            GameDescriptor(
                game_id="g",
                display_name="x",
                game_version="1.0.0",
                content_rounds=9,
            )


# -- the engine's judgement ----------------------------------------------


class TestStagedGeneration:
    def test_a_chained_board_generates_and_publishes(self, world):
        """The whole path, with stages: generate, verify, measure, publish."""
        pipeline, outcome = run(world, ChainGame())
        assert outcome.succeeded, outcome.trace.failure_reason
        assert outcome.puzzle is not None
        assert [p["part_id"] for p in outcome.puzzle.parts] == ["pick", "initial"]

        manifest = pipeline.publish_outcome(outcome, allow_off_target=True)
        stages = {p.part_id: p for p in manifest.verification.parts}
        assert stages["pick"].solutions_alone is None
        # The gate, recorded: before the odd one is known, more than one
        # letter is still possible; afterwards exactly one is.
        assert stages["initial"].solutions_alone > 1
        assert stages["initial"].solution_count == 1
        assert stages["initial"].requires == ("pick",)
        assert (
            world["engine"].puzzles.get(outcome.puzzle.id).status
            is PuzzleStatus.PUBLISHED
        )

    def test_the_axes_are_published_with_their_reduction(self, world):
        """A score with no axes beside it cannot be audited.

        Recorded together with the rule that made it, because the axes alone
        cannot be checked against the score.
        """
        pipeline, outcome = run(world, ChainGame())
        manifest = pipeline.publish_outcome(outcome, allow_off_target=True)
        assert set(manifest.difficulty.axes) == {"pick", "initial"}
        assert manifest.difficulty.reduction is DifficultyReduction.MAXIMUM
        assert manifest.difficulty.score == max(manifest.difficulty.axes.values())

    def test_an_ungated_stage_is_refused(self, world):
        """A stage already decided without its predecessor is decoration.

        The board would be two independent puzzles presented as a chain, and a
        player could solve them in any order.
        """
        _, outcome = run(world, UngatedChainGame())
        assert not outcome.succeeded
        assert RejectionReason.STAGE_NOT_GATED in rejections(outcome)

    def test_a_stage_that_stays_undetermined_is_refused(self, world):
        """A board that cannot be finished in its own declared order.

        Worse than an ambiguous flat board: the whole board verifies, and
        there is still no path through it.
        """
        _, outcome = run(world, DeadEndChainGame())
        assert not outcome.succeeded
        assert RejectionReason.STAGE_UNDETERMINED in rejections(outcome)

    def test_stages_from_a_game_that_declares_none_are_refused(self, world):
        """Stage gating is only verified where it is declared."""
        _, outcome = run(world, UndeclaredStageGame())
        assert not outcome.succeeded
        assert "does not declare staged" in rejections(outcome)

    def test_a_score_that_is_not_its_axes_reduction_is_refused(self, world):
        """The bands are read against the score.

        A game measuring a punishing stage honestly and banding the board on
        the gentler number would make the axes decoration, which is the
        failure this project is named for.
        """
        _, outcome = run(world, MisreducedAxesGame())
        assert not outcome.succeeded
        assert "is not the MAXIMUM of the reported axes" in rejections(outcome)


class TestContentRounds:
    def test_a_second_round_may_depend_on_the_first(self, world):
        """The question round two asks could not have been written earlier.

        ChainGame's second round picks its category from which pool round one
        returned more of, so a run that reached a board at all exercised the
        dependence.
        """
        _, outcome = run(world, ChainGame())
        assert outcome.succeeded, outcome.trace.failure_reason
        assert "decoys" in outcome.trace.content_requests

    def test_a_repeated_requirement_name_is_refused(self, world):
        """Merging would overwrite a result the game has already read.

        A game reading the overwritten one is reading content under a name no
        gate associates with it.
        """
        _, outcome = run(world, RepeatingRefiningGame())
        assert not outcome.succeeded
        assert "already answered" in outcome.trace.failure_reason

    def test_declaring_rounds_without_implementing_them_is_refused(self, world):
        """The declaration is what the engine budgeted for.

        Quietly honouring the smaller promise would hide the mistake until a
        day came out thin.
        """
        world["registry"].register(UnrefiningGame())
        world["registry"].activate("oddoneout", day_key=DAY)
        pipeline = GenerationPipeline(
            content=world["content"],
            engine=world["engine"],
            registry=world["registry"],
            dependencies=world["repos"].dependencies,
            snapshot=world["snapshot"],
            now=NOW,
            verifier=PuzzleVerifier(require_on_target=False),
        )
        outcome = pipeline.generate("oddoneout", DAY)
        assert not outcome.succeeded
        assert "refine_content_requirements" in outcome.trace.failure_reason


class TestPublicationProof:
    def test_publishing_needs_the_proof_for_this_board(self, world):
        """A board's stages and the proof's stages must be the same stages.

        They come from different calls, so a mismatch means the proof in hand
        is not this board's.
        """
        pipeline, outcome = run(world, ChainGame())
        assert outcome.puzzle is not None
        evaluation = outcome.evaluation
        assert evaluation is not None
        with pytest.raises(ContentError, match="stages"):
            pipeline.publish(
                outcome.puzzle,
                verification=evaluation.verification,
                difficulty=evaluation.difficulty,
                measured_band=evaluation.measured_band,
                descriptor=world["registry"].get("chain").descriptor,
                generation_id=outcome.trace.id,
                allow_off_target=True,
                parts=(),
            )

    def test_a_staged_board_commits_to_its_stages(self, world):
        """A stage's clues must move the content hash.

        The board's solution already carries every stage's answer, so a
        changed answer moves the hash either way. A changed clue in a later
        stage would not, and a rebuild check that cannot see it is checking
        the easy half.
        """
        _, outcome = run(world, ChainGame())
        record = outcome.puzzle
        assert record is not None
        altered = record.model_copy(
            update={
                "parts": (
                    record.parts[0],
                    {**record.parts[1], "payload": {"letters": ["Z"]}},
                )
            }
        )
        assert altered.content_hash() != record.content_hash()


class TestUniquenessClaims:
    def test_a_stage_contract_is_checked_by_the_same_rules(self):
        """One set of rules, read for a board and for a stage alike.

        A stage checked by a second, kinder copy of the uniqueness rules is a
        stage the engine does not really check.
        """
        from puzzlegen.engine import uniqueness
        from puzzlegen.engine.plugin import VerificationResult

        claim = uniqueness.ContractClaim(
            contract=UniquenessContract.EXACTLY_N, expected_solution_count=3
        )
        three = VerificationResult(solvable=True, solution_count=3, states_examined=3)
        two = VerificationResult(solvable=True, solution_count=2, states_examined=3)
        assert uniqueness.check_claim(claim, three)
        assert not uniqueness.check_claim(claim, two)
