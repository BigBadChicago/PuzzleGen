"""Verification and difficulty, orchestrated.

The game does the solving, because only it knows its rules. The engine does
the judging, because a game judging its own puzzle is not a check. This module
is the seam: it calls the plugin's verifier, contains its failures, checks the
result for internal consistency, applies the uniqueness contract, compares the
intended solution against what the solver actually found, measures difficulty
through the game's own model, and bands the score against the game's own
thresholds.

A puzzle that fails any of these is rejected with a structured reason, which
the pipeline counts and the trace records. Nothing here publishes; the
pipeline does that, and only with an outcome from here in hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.errors import RejectionReason
from ..core.hashing import stable_hash
from ..core.types import (
    DifficultyBand,
    DifficultyReduction,
    VerificationCompleteness,
)
from . import staging, uniqueness
from .plugin import (
    DifficultyMeasurement,
    GameDescriptor,
    GamePlugin,
    Puzzle,
    VerificationResult,
)
from .staging import PartOutcome

#: How far a game's banded score may sit from the reduction of its own axes
#: before the engine calls it a protocol error rather than arithmetic.
AXIS_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """The engine's judgement on one assembled puzzle."""

    accepted: bool
    verification: VerificationResult | None = None
    difficulty: DifficultyMeasurement | None = None
    measured_band: DifficultyBand | None = None
    reason: RejectionReason | None = None
    detail: str = ""
    #: True when the puzzle is valid but missed its requested band. Kept
    #: distinct from rejection so a caller can choose to publish it anyway.
    off_target: bool = False
    #: One entry per stage of a composite board, empty for a flat one. Carried
    #: out of here because the manifest records what was established per
    #: stage: a board that passed its gating and a board that had no stages to
    #: gate must not look alike in the published record.
    parts: tuple[PartOutcome, ...] = ()

    def rejection(self) -> tuple[RejectionReason, str] | None:
        if self.accepted:
            return None
        return (self.reason or RejectionReason.NO_SOLUTION, self.detail)


class PuzzleVerifier:
    """Runs and judges a game's own verification."""

    def __init__(self, *, require_on_target: bool = True) -> None:
        self._require_on_target = require_on_target

    def evaluate(
        self,
        puzzle: Puzzle,
        plugin: GamePlugin,
        descriptor: GameDescriptor,
        target: DifficultyBand,
    ) -> EvaluationOutcome:
        verification = self._run_verification(puzzle, plugin)
        if isinstance(verification, EvaluationOutcome):
            return verification

        problem = uniqueness.consistency_problem(verification)
        if problem is not None:
            # A self-contradictory result is a bug in the game's verifier, not
            # a property of its puzzle, so it is reported as a protocol error.
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
                detail=f"inconsistent verification: {problem}",
            )

        if not verification.solvable:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=RejectionReason.NO_SOLUTION,
                detail=verification.detail or "verifier found no solution",
            )

        degraded = self._completeness_problem(verification, descriptor)
        if degraded is not None:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=RejectionReason.VERIFICATION_UNSOUND,
                detail=degraded,
            )

        verdict = uniqueness.check(descriptor, verification)
        if not verdict:
            reason = (
                RejectionReason.MULTIPLE_SOLUTIONS
                if verification.solution_count > 1
                else RejectionReason.UNIQUENESS_CONTRACT_VIOLATED
            )
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=reason,
                detail=verdict.reason,
            )

        mismatch = self._intended_solution_problem(puzzle, verification)
        if mismatch is not None:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=RejectionReason.UNFAIR_COMBINATION,
                detail=mismatch,
            )

        declared = self._staging_declaration_problem(puzzle, descriptor)
        if declared is not None:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
                detail=declared,
            )

        staged = staging.check_parts(puzzle, plugin)
        if not staged:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=staged.reason or RejectionReason.STRUCTURAL_RULE_VIOLATION,
                detail=staged.detail,
            )
        degraded_stage = staging.completeness_problem(
            staged.outcomes, descriptor.verification_completeness
        )
        if degraded_stage is not None:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=RejectionReason.VERIFICATION_UNSOUND,
                detail=degraded_stage,
            )

        return self._measure(
            puzzle, plugin, descriptor, target, verification, staged.outcomes
        )

    # -- steps ------------------------------------------------------------

    def _run_verification(
        self, puzzle: Puzzle, plugin: GamePlugin
    ) -> VerificationResult | EvaluationOutcome:
        try:
            return plugin.verify(puzzle)
        except Exception as exc:  # noqa: BLE001 - plugins are untrusted
            return EvaluationOutcome(
                accepted=False,
                reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
                detail=f"verifier raised {type(exc).__name__}: {exc}",
            )

    def _staging_declaration_problem(
        self, puzzle: Puzzle, descriptor: GameDescriptor
    ) -> str | None:
        """Whether the board's stages and the game's declaration agree.

        Both directions are errors, and the second is the one worth the check.
        A flat board from a staged game would have its gating verified against
        no stages at all, so the declaration would read as a kept promise
        while nothing was checked.
        """
        if puzzle.parts and not descriptor.staged:
            return (
                "the board carries stages but the game does not declare "
                "staged; stage gating is only verified where it is declared"
            )
        if descriptor.staged and not puzzle.parts:
            return (
                "the game declares staged and assembled a board with no "
                "stages, so its staging was verified against nothing"
            )
        return None

    def _completeness_problem(
        self, verification: VerificationResult, descriptor: GameDescriptor
    ) -> str | None:
        if verification.completeness is VerificationCompleteness.COMPLETE:
            return None
        if descriptor.verification_completeness is VerificationCompleteness.COMPLETE:
            return (
                "game declared complete verification but returned a partial "
                "enumeration"
            )
        bound = descriptor.search_bound
        if bound is not None and verification.states_examined > bound:
            return (
                f"verification examined {verification.states_examined} states, "
                f"beyond the declared bound of {bound}"
            )
        return None

    def _intended_solution_problem(
        self, puzzle: Puzzle, verification: VerificationResult
    ) -> str | None:
        """Whether the puzzle's own answer is among what the solver found.

        A puzzle whose stated solution the solver cannot reach is broken in
        the most dangerous way: it verifies, it is unique, and it marks the
        player wrong. Checking costs one hash and catches it.

        Truncation excuses the solutions a verifier did not list, never the
        comparison itself. A verifier that stopped collecting still found the
        intended answer if the puzzle is sound, so its absence from a truncated
        list is evidence against the puzzle rather than an artifact of the
        truncation. The empty case cannot arrive here at all: a truncated list
        naming nothing is refused as a protocol error by
        ``uniqueness.consistency_problem``, which runs first.
        """
        if not verification.solutions:
            return "verifier reported no solution values to compare against"
        intended = stable_hash(dict(puzzle.solution))
        found = {stable_hash(dict(solution)) for solution in verification.solutions}
        if intended in found:
            return None
        if verification.solutions_truncated:
            return (
                "the puzzle's stated solution is not among the solutions the "
                "verifier listed, and a truncated list is not an excuse for "
                "omitting the intended one"
            )
        return "the puzzle's stated solution is not among those the solver found"

    def _measure(
        self,
        puzzle: Puzzle,
        plugin: GamePlugin,
        descriptor: GameDescriptor,
        target: DifficultyBand,
        verification: VerificationResult,
        parts: tuple[PartOutcome, ...] = (),
    ) -> EvaluationOutcome:
        try:
            difficulty = plugin.measure_difficulty(puzzle, verification)
        except Exception as exc:  # noqa: BLE001
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
                detail=f"difficulty model raised {type(exc).__name__}: {exc}",
            )

        axes = self._axis_problem(puzzle, descriptor, difficulty)
        if axes is not None:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                difficulty=difficulty,
                reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
                detail=axes,
                parts=parts,
            )

        band = descriptor.band_for(difficulty.score)
        on_target = band is target

        if not on_target and self._require_on_target:
            return EvaluationOutcome(
                accepted=False,
                verification=verification,
                difficulty=difficulty,
                measured_band=band,
                reason=RejectionReason.DIFFICULTY_OUT_OF_RANGE,
                detail=f"measured {band}, requested {target}",
                off_target=True,
                parts=parts,
            )

        return EvaluationOutcome(
            accepted=True,
            verification=verification,
            difficulty=difficulty,
            measured_band=band,
            off_target=not on_target,
            parts=parts,
        )

    def _axis_problem(
        self,
        puzzle: Puzzle,
        descriptor: GameDescriptor,
        difficulty: DifficultyMeasurement,
    ) -> str | None:
        """Whether the axes, the declared reduction and the score agree.

        Three failures, and the third is why this check exists at all. Axes
        with no declared reduction are unreadable: nobody downstream knows
        what they were supposed to make. A reduction with no axes is a promise
        with nothing behind it. And a score that is not the reduction of the
        axes is the real hazard, because the bands are read against the score:
        a game could measure a punishing stage honestly and still band the
        board EASY. The axes are then decoration, which is the failure this
        project is named for.
        """
        reduction = descriptor.difficulty_reduction
        if reduction is DifficultyReduction.NONE:
            if difficulty.axes:
                return (
                    "difficulty axes were reported but the game declares no "
                    "reduction, so nothing downstream knows what they make"
                )
            return None
        if not difficulty.axes:
            return (
                f"game declares the {reduction} reduction and reported no "
                "difficulty axes to reduce"
            )
        expected = reduction.apply(difficulty.axes)
        if abs(expected - difficulty.score) > AXIS_TOLERANCE:
            return (
                f"the score {difficulty.score} is not the {reduction} of the "
                f"reported axes, which is {expected}; the bands are read "
                "against the score, so the axes would be decoration"
            )
        if puzzle.parts:
            missing = [
                part.part_id
                for part in puzzle.parts
                if part.part_id not in difficulty.axes
            ]
            if missing:
                return (
                    f"a staged board must report a difficulty axis per stage; "
                    f"stages {missing} have none, so their difficulty is "
                    "averaged into a number that names no stage"
                )
        return None


@dataclass
class DifficultyCalibration:
    """Observed difficulty scores for one game, for threshold review.

    Thresholds are declared per game, which means they can be wrong per game.
    Collecting what a game's model actually produces is how a curator finds
    out that a band nobody ever lands in is misconfigured rather than rare.
    """

    game_id: str
    scores: list[float] = field(default_factory=list)
    bands: dict[str, int] = field(default_factory=dict)

    def record(self, score: float, band: DifficultyBand) -> None:
        self.scores.append(score)
        self.bands[str(band)] = self.bands.get(str(band), 0) + 1

    def summary(self) -> dict[str, float]:
        if not self.scores:
            return {}
        ordered = sorted(self.scores)
        middle = len(ordered) // 2
        return {
            "count": float(len(ordered)),
            "minimum": ordered[0],
            "maximum": ordered[-1],
            "mean": sum(ordered) / len(ordered),
            "median": (
                ordered[middle]
                if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / 2
            ),
        }

    def unused_bands(self, supported: tuple[DifficultyBand, ...]) -> tuple[str, ...]:
        return tuple(str(b) for b in supported if str(b) not in self.bands)
