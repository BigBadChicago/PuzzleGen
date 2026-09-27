"""Generation.

Generate many, filter hard, ship few. The pipeline offers a game a content
bundle, takes back many candidates, and applies the engine's own gates before
anything is assembled. Every rejection is counted by structured reason and
written to a trace, whether the run succeeds or fails, because a failed day
with no trace is a day nobody can diagnose.

Generation stops at a draft. Publication is a separate call that requires
proof: a verification summary and a difficulty measurement must be handed in,
and there is no code path that publishes without them. That separation is why
"solvable is not sufficient" can be enforced rather than merely intended.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..content.port import ContentPort, PortBudget
from ..content.query import ContentRequirement
from ..content.service import ContentService
from ..core import ids
from ..core.errors import (
    BoundaryViolationError,
    ContentError,
    RejectionReason,
)
from ..core.rng import DeterministicRng, derive_seed
from ..core.types import DependencyRefKind, DifficultyBand, UniquenessContract
from ..core.versions import (
    CONTENT_SCHEMA_VERSION,
    ENGINE_VERSION,
    PLUGIN_PROTOCOL_VERSION,
    PUZZLE_FORMAT_VERSION,
    SHARE_FORMAT_VERSION,
)
from ..graph.models import SnapshotMeta
from ..graph.repositories import DependencyRepository
from . import uniqueness
from .plugin import (
    DifficultyMeasurement,
    GameDescriptor,
    GamePlugin,
    GenerationContext,
    Puzzle,
    PuzzleCandidate,
    VerificationResult,
)
from .records import (
    DifficultySummary,
    GenerationInputs,
    GenerationTrace,
    PuzzleManifest,
    PuzzleRecord,
    PuzzleStatus,
    RejectionTally,
    VerificationSummary,
    VersionSet,
)
from .registry import GameRegistry
from .storage import EngineRepositories
from .verifier import EvaluationOutcome, PuzzleVerifier

#: Ceiling on candidates the engine will consider from one game in one run.
#: A game is untrusted code; without a cap an accidental generator loop would
#: consume the whole day's generation rather than failing its own puzzle.
MAX_CANDIDATES = 512

#: How many assembly attempts the pipeline makes before giving up. Assembly is
#: where a game does its expensive work, so the cap is much lower than the
#: candidate cap.
MAX_ASSEMBLY_ATTEMPTS = 64


def day_key_for(date: dt.date) -> str:
    return date.isoformat()


@dataclass
class _Counter:
    """Rejection counts for one pipeline stage."""

    stage: str
    reasons: dict[str, int] = field(default_factory=dict)

    def add(self, reason: RejectionReason | str, detail: str = "") -> None:
        key = str(reason) if not detail else f"{reason}: {detail}"
        self.reasons[key] = self.reasons.get(key, 0) + 1

    def tally(self) -> RejectionTally:
        return RejectionTally(stage=self.stage, reasons=dict(self.reasons))

    @property
    def total(self) -> int:
        return sum(self.reasons.values())


@dataclass(frozen=True, slots=True)
class GenerationOutcome:
    """The result of one generation run, successful or not."""

    trace: GenerationTrace
    puzzle: PuzzleRecord | None = None
    candidates_considered: int = 0
    #: Present when a verifier was configured. Carries the proof the pipeline
    #: needs to publish, so the caller never has to re-run verification and
    #: risk verifying something other than what was stored.
    evaluation: EvaluationOutcome | None = None

    @property
    def succeeded(self) -> bool:
        return self.puzzle is not None


class GenerationPipeline:
    """Runs one game's generation for one day."""

    def __init__(
        self,
        *,
        content: ContentService,
        engine: EngineRepositories,
        registry: GameRegistry,
        dependencies: DependencyRepository,
        snapshot: SnapshotMeta,
        now: dt.datetime | None = None,
        seed_salt: str = "",
        port_budget: PortBudget | None = None,
        verifier: PuzzleVerifier | None = None,
    ) -> None:
        if not snapshot.sealed:
            raise ContentError(
                "generation requires a sealed snapshot; an unsealed snapshot "
                "cannot support a reproducibility claim"
            )
        self._content = content
        self._engine = engine
        self._registry = registry
        # Dependencies are the engine's ledger of what a puzzle used, not
        # governed content, which is why the engine writes them directly while
        # still holding no repository over entities, facts or categories.
        self._dependencies = dependencies
        self._snapshot = snapshot
        self._now = now or dt.datetime.now(dt.timezone.utc)
        self._seed_salt = seed_salt
        self._port_budget = port_budget
        # Verification is a generation gate, not a post-processing step. A
        # candidate that fails to verify is discarded and the next is tried,
        # which is what makes "generate many, filter hard" include the
        # expensive filters rather than only the cheap ones.
        self._verifier = verifier

    # -- versions ---------------------------------------------------------

    def _versions(self, descriptor: GameDescriptor) -> VersionSet:
        return VersionSet(
            engine=ENGINE_VERSION,
            game=descriptor.game_version,
            content_schema=CONTENT_SCHEMA_VERSION,
            puzzle_format=PUZZLE_FORMAT_VERSION,
            share_format=SHARE_FORMAT_VERSION,
            protocol=descriptor.protocol_version,
            providers=dict(self._snapshot.source_versions),
            embedding_model=self._snapshot.embedding_model,
            embedding_model_version=self._snapshot.embedding_model_version,
            frequency_source=self._snapshot.frequency_source,
            frequency_source_version=self._snapshot.frequency_source_version,
        )

    def _inputs(
        self,
        descriptor: GameDescriptor,
        day_key: str,
        seed: bytes,
        target: DifficultyBand,
        locale: str,
        candidate_id: str = "",
    ) -> GenerationInputs:
        return GenerationInputs(
            day_key=day_key,
            game_id=descriptor.game_id,
            seed_hex=seed.hex(),
            seed_salt=self._seed_salt,
            snapshot_id=self._snapshot.id,
            snapshot_content_hash=self._snapshot.content_hash,
            difficulty_target=target,
            locale=locale,
            candidate_id=candidate_id,
        )

    # -- generation -------------------------------------------------------

    def generate(
        self,
        game_id: str,
        day_key: str,
        *,
        difficulty_target: DifficultyBand = DifficultyBand.MEDIUM,
        locale: str = "en",
    ) -> GenerationOutcome:
        entry = self._registry.get(game_id)
        descriptor = entry.descriptor
        plugin = entry.plugin

        if difficulty_target not in descriptor.supported_bands:
            return self._fail(
                descriptor,
                day_key,
                difficulty_target,
                locale,
                reason=(
                    f"game does not support difficulty band {difficulty_target}"
                ),
            )
        if locale not in descriptor.supported_locales:
            return self._fail(
                descriptor,
                day_key,
                difficulty_target,
                locale,
                reason=f"game does not support locale {locale!r}",
            )

        seed = derive_seed(day_key, game_id, self._seed_salt)
        root = DeterministicRng(seed)
        port = ContentPort(self._content, game_id=game_id, budget=self._port_budget)

        requirements = list(
            plugin.get_content_requirements(
                difficulty_target=difficulty_target, locale=locale, day_key=day_key
            )
        )
        try:
            content = port.satisfy(requirements)
        except BoundaryViolationError as exc:
            return self._fail(
                descriptor,
                day_key,
                difficulty_target,
                locale,
                reason=f"content boundary violation: {exc}",
                port=port,
                requirements=requirements,
            )

        unmet = [r.name for r in requirements if not r.is_met(content[r.name])]
        if unmet:
            return self._fail(
                descriptor,
                day_key,
                difficulty_target,
                locale,
                reason=(
                    f"{RejectionReason.INSUFFICIENT_CANDIDATES}: unmet content "
                    f"requirements {unmet}"
                ),
                port=port,
                requirements=requirements,
                content=content,
            )

        context = GenerationContext(
            day_key=day_key,
            game_version=descriptor.game_version,
            difficulty_target=difficulty_target,
            locale=locale,
            rng=root.derive("candidates"),
            content=content,
            candidate_budget=MAX_CANDIDATES,
        )

        candidates = list(plugin.generate_candidates(context))
        offered = len(candidates)
        screen = _Counter("candidate_screen")
        assembly = _Counter("assembly")

        accepted = self._screen_candidates(candidates, port, screen)
        if not accepted:
            return self._fail(
                descriptor,
                day_key,
                difficulty_target,
                locale,
                reason=f"{RejectionReason.INSUFFICIENT_CANDIDATES}: no candidate "
                "survived screening",
                port=port,
                requirements=requirements,
                content=content,
                offered=offered,
                tallies=(screen, assembly),
            )

        assembled = 0
        for candidate in accepted[:MAX_ASSEMBLY_ATTEMPTS]:
            rng = root.derive(f"assemble:{candidate.candidate_id}")
            try:
                puzzle = plugin.assemble(candidate, rng)
            except Exception as exc:  # noqa: BLE001 - plugins are untrusted
                assembly.add(
                    RejectionReason.PLUGIN_PROTOCOL_ERROR, type(exc).__name__
                )
                continue
            assembled += 1

            problem = self._screen_puzzle(puzzle, candidate, descriptor, port)
            if problem is not None:
                assembly.add(*problem)
                continue

            evaluation: EvaluationOutcome | None = None
            if self._verifier is not None:
                evaluation = self._verifier.evaluate(
                    puzzle, plugin, descriptor, difficulty_target
                )
                rejection = evaluation.rejection()
                if rejection is not None:
                    assembly.add(*rejection)
                    continue

            record = self._draft(
                descriptor,
                day_key,
                seed,
                difficulty_target,
                locale,
                candidate,
                puzzle,
                status=(
                    PuzzleStatus.VERIFIED
                    if evaluation is not None
                    else PuzzleStatus.DRAFT
                ),
            )
            trace = self._trace(
                descriptor,
                day_key,
                seed,
                difficulty_target,
                locale,
                port=port,
                requirements=requirements,
                content=content,
                offered=offered,
                assembled=assembled,
                tallies=(screen, assembly),
                candidate_id=candidate.candidate_id,
                puzzle_id=record.id,
                succeeded=True,
            )
            with self._engine.transaction():
                self._engine.puzzles.put(record)
                self._engine.traces.put(trace)
            return GenerationOutcome(
                trace=trace,
                puzzle=record,
                candidates_considered=offered,
                evaluation=evaluation,
            )

        return self._fail(
            descriptor,
            day_key,
            difficulty_target,
            locale,
            reason="no candidate assembled into a valid puzzle",
            port=port,
            requirements=requirements,
            content=content,
            offered=offered,
            assembled=assembled,
            tallies=(screen, assembly),
        )

    # -- gates ------------------------------------------------------------

    def _screen_candidates(
        self,
        candidates: Sequence[PuzzleCandidate],
        port: ContentPort,
        counter: _Counter,
    ) -> list[PuzzleCandidate]:
        """Engine gates that apply before any assembly work is done."""
        seen: set[str] = set()
        kept: list[PuzzleCandidate] = []

        for candidate in candidates[:MAX_CANDIDATES]:
            if candidate.candidate_id in seen:
                counter.add(RejectionReason.DUPLICATE_ENTITY, "duplicate candidate id")
                continue
            seen.add(candidate.candidate_id)

            if not candidate.fact_refs:
                counter.add(RejectionReason.MISSING_FACT_REFS)
                continue

            fabricated = port.verify_references(candidate.fact_refs)
            if fabricated:
                # A reference the port never issued means the game invented a
                # dependency. That is a protocol violation, not a content
                # problem: dependency tracking that can be invented is not
                # dependency tracking.
                counter.add(
                    RejectionReason.PLUGIN_PROTOCOL_ERROR,
                    f"unissued references {list(fabricated)[:3]}",
                )
                continue

            kept.append(candidate)

        if len(candidates) > MAX_CANDIDATES:
            counter.add("CANDIDATE_BUDGET_EXCEEDED")
        return kept

    def _screen_puzzle(
        self,
        puzzle: Puzzle,
        candidate: PuzzleCandidate,
        descriptor: GameDescriptor,
        port: ContentPort,
    ) -> tuple[RejectionReason, str] | None:
        """Structural gates on an assembled puzzle."""
        if puzzle.game_id != descriptor.game_id:
            return (
                RejectionReason.PLUGIN_PROTOCOL_ERROR,
                "puzzle claims another game's id",
            )
        if not puzzle.solution:
            return (RejectionReason.NO_SOLUTION, "assembled puzzle has no solution")

        fabricated = port.verify_references(puzzle.fact_refs)
        if fabricated:
            return (
                RejectionReason.PLUGIN_PROTOCOL_ERROR,
                f"unissued references {list(fabricated)[:3]}",
            )

        widened = set(puzzle.fact_refs) - set(candidate.fact_refs)
        if widened:
            # Assembly may narrow what a candidate declared but never widen
            # it: a puzzle depending on records its candidate never claimed
            # means screening was applied to the wrong set.
            return (
                RejectionReason.MISSING_FACT_REFS,
                f"assembly added unscreened references {sorted(widened)[:3]}",
            )

        ok, why = descriptor.accessibility.is_complete()
        if not ok:
            return (RejectionReason.POLICY_REJECTED, f"accessibility: {why}")

        return None

    def _draft(
        self,
        descriptor: GameDescriptor,
        day_key: str,
        seed: bytes,
        target: DifficultyBand,
        locale: str,
        candidate: PuzzleCandidate,
        puzzle: Puzzle,
        status: PuzzleStatus = PuzzleStatus.DRAFT,
    ) -> PuzzleRecord:
        inputs = self._inputs(
            descriptor, day_key, seed, target, locale, candidate.candidate_id
        )
        payload = dict(puzzle.payload)
        solution = dict(puzzle.solution)
        record_id = ids.for_puzzle(
            day_key,
            descriptor.game_id,
            _puzzle_fingerprint(descriptor.game_id, payload, solution, puzzle.fact_refs),
        )
        return PuzzleRecord(
            id=record_id,
            game_id=descriptor.game_id,
            day_key=day_key,
            status=status,
            payload=payload,
            solution=solution,
            presentation=dict(puzzle.presentation),
            fact_refs=tuple(dict.fromkeys(puzzle.fact_refs)),
            inputs=inputs,
            versions=self._versions(descriptor),
            created_at=self._now,
        )

    # -- publication ------------------------------------------------------

    def publish(
        self,
        puzzle: PuzzleRecord,
        *,
        verification: VerificationResult,
        difficulty: DifficultyMeasurement,
        measured_band: DifficultyBand,
        descriptor: GameDescriptor,
        generation_id: str,
        allow_off_target: bool = False,
    ) -> PuzzleManifest:
        """Publish a verified puzzle.

        Requires proof rather than accepting a claim. The uniqueness contract
        is checked against the verifier's own solution count, an incomplete
        verification is refused unless the game declared it in advance, and a
        puzzle outside its requested difficulty band is refused unless the
        caller explicitly accepts it. Solvability alone publishes nothing.
        """
        summary = self._summarise_verification(verification, descriptor)
        if not summary.solvable:
            raise ContentError(f"{RejectionReason.NO_SOLUTION}: puzzle is unsolvable")
        # Completeness is checked before the contract, because a degraded
        # enumeration also fails every uniqueness contract and the less
        # specific message would hide the actual cause.
        if (
            summary.completeness.value == "SOUND_INCOMPLETE"
            and descriptor.verification_completeness.value == "COMPLETE"
        ):
            raise ContentError(
                f"{RejectionReason.VERIFICATION_UNSOUND}: game declared complete "
                "verification but returned a partial enumeration"
            )
        if not summary.contract_satisfied:
            raise ContentError(
                f"{RejectionReason.UNIQUENESS_CONTRACT_VIOLATED}: "
                f"{descriptor.uniqueness_contract} with "
                f"{summary.solution_count} solutions"
            )

        difficulty_summary = DifficultySummary(
            target=puzzle.inputs.difficulty_target,
            measured_band=measured_band,
            score=difficulty.score,
            confidence=difficulty.confidence,
            features=dict(difficulty.features),
        )
        if not difficulty_summary.on_target and not allow_off_target:
            raise ContentError(
                f"{RejectionReason.DIFFICULTY_OUT_OF_RANGE}: measured "
                f"{measured_band}, requested {puzzle.inputs.difficulty_target}"
            )

        manifest = PuzzleManifest(
            id=ids.for_manifest(puzzle.id),
            puzzle_id=puzzle.id,
            game_id=puzzle.game_id,
            day_key=puzzle.day_key,
            generation_id=generation_id,
            inputs=puzzle.inputs,
            versions=puzzle.versions,
            verification=summary,
            difficulty=difficulty_summary,
            fact_refs=puzzle.fact_refs,
            puzzle_content_hash=puzzle.content_hash(),
            published_at=self._now,
        )

        with self._engine.transaction():
            self._engine.manifests.put(manifest)
            self._engine.puzzles.set_status(puzzle.id, PuzzleStatus.PUBLISHED)
            self._record_dependencies(manifest)
        return manifest

    def publish_outcome(
        self, outcome: GenerationOutcome, *, allow_off_target: bool = False
    ) -> PuzzleManifest:
        """Publish a generation outcome that already carries its proof.

        Preferred over calling :meth:`publish` by hand, because it publishes
        the same evaluation the pipeline accepted. Re-verifying at publication
        time would open a window in which the puzzle verified once and was
        published on the strength of a second, different run.
        """
        if outcome.puzzle is None or outcome.evaluation is None:
            raise ContentError(
                "cannot publish an outcome without a puzzle and its evaluation; "
                "configure the pipeline with a verifier"
            )
        evaluation = outcome.evaluation
        if evaluation.verification is None or evaluation.difficulty is None:
            raise ContentError("evaluation carries no proof")
        descriptor = self._registry.get(outcome.puzzle.game_id).descriptor
        return self.publish(
            outcome.puzzle,
            verification=evaluation.verification,
            difficulty=evaluation.difficulty,
            measured_band=evaluation.measured_band or outcome.puzzle.inputs.difficulty_target,
            descriptor=descriptor,
            generation_id=outcome.trace.id,
            allow_off_target=allow_off_target,
        )

    def _summarise_verification(
        self, verification: VerificationResult, descriptor: GameDescriptor
    ) -> VerificationSummary:
        from ..core.hashing import stable_hash

        satisfied = bool(uniqueness.check(descriptor, verification))
        solution_hash = (
            stable_hash(verification.solutions[0]) if verification.solutions else ""
        )
        return VerificationSummary(
            solvable=verification.solvable,
            solution_count=verification.solution_count,
            completeness=verification.completeness,
            states_examined=verification.states_examined,
            uniqueness_contract=descriptor.uniqueness_contract,
            contract_satisfied=satisfied,
            solution_hash=solution_hash,
            detail=verification.detail,
        )

    def _record_dependencies(self, manifest: PuzzleManifest) -> None:
        """Write one edge per graph record the puzzle used."""
        refs: list[tuple[DependencyRefKind, str]] = []
        for ref_id in manifest.fact_refs:
            kind = _ref_kind(ref_id)
            if kind is not None:
                refs.append((kind, ref_id))
        if refs:
            self._dependencies.record_refs(
                manifest_id=manifest.id,
                puzzle_id=manifest.puzzle_id,
                game_id=manifest.game_id,
                day_key=manifest.day_key,
                refs=refs,
                created_at=self._now,
            )

    # -- tracing ----------------------------------------------------------

    def _fail(
        self,
        descriptor: GameDescriptor,
        day_key: str,
        target: DifficultyBand,
        locale: str,
        *,
        reason: str,
        port: ContentPort | None = None,
        requirements: Sequence[ContentRequirement] = (),
        content: Mapping | None = None,
        offered: int = 0,
        assembled: int = 0,
        tallies: Sequence[_Counter] = (),
    ) -> GenerationOutcome:
        seed = derive_seed(day_key, descriptor.game_id, self._seed_salt)
        trace = self._trace(
            descriptor,
            day_key,
            seed,
            target,
            locale,
            port=port,
            requirements=requirements,
            content=content,
            offered=offered,
            assembled=assembled,
            tallies=tallies,
            succeeded=False,
            failure_reason=reason,
        )
        self._engine.traces.put(trace)
        return GenerationOutcome(trace=trace, candidates_considered=offered)

    def _trace(
        self,
        descriptor: GameDescriptor,
        day_key: str,
        seed: bytes,
        target: DifficultyBand,
        locale: str,
        *,
        port: ContentPort | None,
        requirements: Sequence[ContentRequirement],
        content: Mapping | None,
        offered: int,
        assembled: int = 0,
        tallies: Sequence[_Counter] = (),
        candidate_id: str | None = None,
        puzzle_id: str | None = None,
        succeeded: bool = False,
        failure_reason: str | None = None,
    ) -> GenerationTrace:
        requests = {
            requirement.name: {
                "operation": str(requirement.query.operation),
                "category": requirement.query.category,
                "group_size": requirement.query.group_size,
                "minimum": requirement.minimum,
                "frequency_band": (
                    str(requirement.query.frequency_band)
                    if requirement.query.frequency_band
                    else None
                ),
                "minimum_similarity": requirement.query.minimum_similarity,
            }
            for requirement in requirements
        }
        content_rejections: dict[str, int] = {}
        if content:
            for result in content.values():
                for reason, count in result.rejected.items():
                    content_rejections[reason] = (
                        content_rejections.get(reason, 0) + count
                    )

        attempt = len(self._engine.traces.for_day(day_key, descriptor.game_id))
        return GenerationTrace(
            id=ids.for_generation(day_key, descriptor.game_id, attempt),
            day_key=day_key,
            game_id=descriptor.game_id,
            inputs=self._inputs(
                descriptor, day_key, seed, target, locale, candidate_id or ""
            ),
            versions=self._versions(descriptor),
            content_requests=requests,
            content_rejections=content_rejections,
            candidates_offered=offered,
            candidates_rejected=tuple(counter.tally() for counter in tallies),
            candidates_assembled=assembled,
            selected_candidate_id=candidate_id,
            puzzle_id=puzzle_id,
            succeeded=succeeded,
            failure_reason=failure_reason,
            port_queries=port.usage.queries if port else 0,
            port_results=port.usage.results if port else 0,
            created_at=self._now,
        )


def _contract_satisfied(
    contract: UniquenessContract, count: int, expected: int | None
) -> bool:
    """Coarse contract check used when no verification evidence is available.

    The real rule lives in :mod:`puzzlegen.engine.uniqueness`, which needs the
    whole verification result. This shim covers the narrow case of a caller
    publishing a hand-built result, and is deliberately stricter than the full
    rule: the two contracts that require evidence fail here rather than pass
    unchecked.
    """
    if contract in uniqueness.SINGLE_SOLUTION_CONTRACTS:
        return count == 1
    if contract is UniquenessContract.EXACTLY_N:
        return expected is not None and count == expected
    return False


def _ref_kind(ref_id: str) -> DependencyRefKind | None:
    mapping = {
        ids.ENTITY: DependencyRefKind.ENTITY,
        ids.FACT: DependencyRefKind.FACT,
        ids.RELATIONSHIP: DependencyRefKind.RELATIONSHIP,
        ids.CATEGORY: DependencyRefKind.CATEGORY,
    }
    try:
        return mapping.get(ids.kind_of(ref_id))
    except ValueError:
        return None


def _puzzle_fingerprint(
    game_id: str,
    payload: Mapping,
    solution: Mapping,
    fact_refs: Sequence[str],
) -> str:
    from ..core.hashing import short_hash

    return short_hash(
        {
            "game": game_id,
            "payload": payload,
            "solution": solution,
            "refs": sorted(fact_refs),
        },
        16,
    )
