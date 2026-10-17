"""Stage verification: what makes a chain of stages a chain.

A composite board claims two things its single-stage ancestor could not, and
both are claims rather than descriptions, so both are checked here.

**That a later stage is not already decided.** A stage declaring a predecessor
is saying the player cannot answer it yet. Measured, that is a count: how many
answers does this stage admit on its own clues, knowing nothing? One answer
means the ordering is decoration. The stages could be played in any order, the
board is a set of independent puzzles wearing a chain's clothes, and a player
who notices is being asked to pretend. So a gated stage must admit something
other than exactly one answer before its predecessors are known, and the
engine refuses the board when it does not.

Zero is admissible there and worth saying why. A stage whose clues read the
previous stage's answer may have no consistent answer at all until that answer
exists, which is not ambiguity but inapplicability. Both are honest forms of
"not yet decided", and distinguishing them would make a game's internal
representation of an unanswered clue into a protocol rule.

**That a later stage is decided once its predecessors are.** The same verifier,
told the predecessors' answers, must satisfy the stage's own contract. A stage
that stays ambiguous there is a board with no path through it, which is worse
than an ambiguous single-stage board: it verifies as a whole and cannot be
finished in its own declared order.

An entry stage is checked once, under its own contract, knowing nothing, which
is exactly what a single-stage board's verification already is.

What this module does not do is decide what a stage means. It calls the game's
own ``verify_part`` and judges the numbers that come back, for the reason the
whole engine is arranged this way: a game that judged its own staging would be
reporting its intention rather than its board.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import RejectionReason
from ..core.hashing import stable_hash
from ..core.types import UniquenessContract, VerificationCompleteness
from . import uniqueness
from .plugin import GamePlugin, Puzzle, PuzzlePart, StagedGame, VerificationResult


@dataclass(frozen=True, slots=True)
class PartOutcome:
    """What the engine established about one stage."""

    part_id: str
    #: Answers the stage admits once its predecessors are known.
    verification: VerificationResult
    contract_satisfied: bool
    #: The stage's own contract and dependencies, carried here so a record can
    #: be written from the outcome alone. A caller that had to go back to the
    #: board for them could pair a stage's numbers with another stage's
    #: contract, which is a record that misstates what was checked.
    uniqueness_contract: UniquenessContract = UniquenessContract.EXACTLY_ONE
    expected_solution_count: int | None = None
    requires: tuple[str, ...] = ()
    #: Answers the stage admitted knowing nothing. ``None`` for an entry
    #: stage, which is never asked the question: it is decided by its own
    #: clues by definition, and asking would be the same call twice.
    solutions_alone: int | None = None

    @property
    def is_gated(self) -> bool:
        return self.solutions_alone is not None


@dataclass(frozen=True, slots=True)
class StagedVerdict:
    """The engine's judgement on a board's stages."""

    accepted: bool
    outcomes: tuple[PartOutcome, ...] = ()
    reason: RejectionReason | None = None
    detail: str = ""
    failed_part: str = ""

    def __bool__(self) -> bool:
        return self.accepted


@dataclass
class _Run:
    known: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    outcomes: list[PartOutcome] = field(default_factory=list)


def check_parts(puzzle: Puzzle, plugin: GamePlugin) -> StagedVerdict:
    """Verify every stage of a composite board, and its gating.

    Stages are visited in dependency order, so a stage is always asked its
    second question with its predecessors' answers already established. The
    first failure stops the walk: a board is rejected whole, and continuing
    would report consequences of the failure as further findings.
    """
    if not puzzle.parts:
        return StagedVerdict(accepted=True)
    if not isinstance(plugin, StagedGame):
        return StagedVerdict(
            accepted=False,
            reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
            detail=(
                "the board declares stages but the game does not implement "
                "verify_part, so no stage can be verified"
            ),
        )

    run = _Run()
    for part in puzzle.ordered_parts():
        if part.is_entry:
            failure = _check_entry(puzzle, plugin, part, run)
        else:
            failure = _check_gated(puzzle, plugin, part, run)
        if failure is not None:
            return failure
    return StagedVerdict(accepted=True, outcomes=tuple(run.outcomes))


# -- the two cases --------------------------------------------------------


def _check_entry(
    puzzle: Puzzle, plugin: StagedGame, part: PuzzlePart, run: _Run
) -> StagedVerdict | None:
    result = _verify(puzzle, plugin, part, {})
    if isinstance(result, StagedVerdict):
        return result
    verdict = _judge(part, result)
    if verdict is not None:
        return verdict
    run.outcomes.append(_outcome(part, result))
    run.known[part.part_id] = part.solution
    return None


def _check_gated(
    puzzle: Puzzle, plugin: StagedGame, part: PuzzlePart, run: _Run
) -> StagedVerdict | None:
    alone = _verify(puzzle, plugin, part, {})
    if isinstance(alone, StagedVerdict):
        return alone
    if alone.solution_count == 1:
        return StagedVerdict(
            accepted=False,
            reason=RejectionReason.STAGE_NOT_GATED,
            failed_part=part.part_id,
            detail=(
                f"stage {part.part_id!r} declares it depends on "
                f"{list(part.requires)} and is already decided without them, "
                "so the dependency is decoration and the stages can be played "
                "in any order"
            ),
        )

    known = {name: run.known[name] for name in part.requires}
    result = _verify(puzzle, plugin, part, known)
    if isinstance(result, StagedVerdict):
        return result
    verdict = _judge(part, result, undetermined=RejectionReason.STAGE_UNDETERMINED)
    if verdict is not None:
        return verdict
    run.outcomes.append(_outcome(part, result, alone=alone.solution_count))
    run.known[part.part_id] = part.solution
    return None


def _outcome(
    part: PuzzlePart, result: VerificationResult, *, alone: int | None = None
) -> PartOutcome:
    return PartOutcome(
        part_id=part.part_id,
        verification=result,
        contract_satisfied=True,
        uniqueness_contract=part.uniqueness_contract,
        expected_solution_count=part.expected_solution_count,
        requires=part.requires,
        solutions_alone=alone,
    )


# -- shared judgement -----------------------------------------------------


def _verify(
    puzzle: Puzzle,
    plugin: StagedGame,
    part: PuzzlePart,
    known: Mapping[str, Mapping[str, Any]],
) -> VerificationResult | StagedVerdict:
    try:
        return plugin.verify_part(puzzle, part.part_id, known)
    except Exception as exc:  # noqa: BLE001 - plugins are untrusted
        return StagedVerdict(
            accepted=False,
            reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
            failed_part=part.part_id,
            detail=(
                f"verify_part raised {type(exc).__name__} on stage "
                f"{part.part_id!r}: {exc}"
            ),
        )


def _judge(
    part: PuzzlePart,
    result: VerificationResult,
    *,
    undetermined: RejectionReason = RejectionReason.UNIQUENESS_CONTRACT_VIOLATED,
) -> StagedVerdict | None:
    """Consistency, the stage's own contract, and its own stated answer."""
    problem = uniqueness.consistency_problem(result)
    if problem is not None:
        return StagedVerdict(
            accepted=False,
            reason=RejectionReason.PLUGIN_PROTOCOL_ERROR,
            failed_part=part.part_id,
            detail=f"inconsistent verification of stage {part.part_id!r}: {problem}",
        )
    if not result.solvable:
        return StagedVerdict(
            accepted=False,
            reason=RejectionReason.NO_SOLUTION,
            failed_part=part.part_id,
            detail=(
                f"stage {part.part_id!r} has no solution"
                + (f": {result.detail}" if result.detail else "")
            ),
        )
    claim = uniqueness.ContractClaim(
        contract=part.uniqueness_contract,
        expected_solution_count=part.expected_solution_count,
    )
    verdict = uniqueness.check_claim(claim, result)
    if not verdict:
        return StagedVerdict(
            accepted=False,
            reason=undetermined,
            failed_part=part.part_id,
            detail=f"stage {part.part_id!r}: {verdict.reason}",
        )
    mismatch = _intended_problem(part, result)
    if mismatch is not None:
        return StagedVerdict(
            accepted=False,
            reason=RejectionReason.UNFAIR_COMBINATION,
            failed_part=part.part_id,
            detail=mismatch,
        )
    return None


def _intended_problem(part: PuzzlePart, result: VerificationResult) -> str | None:
    """Whether the stage's own answer is among what its verifier reached.

    The same check the whole board gets, for the same reason: a stage that
    verifies, satisfies its contract and marks the player wrong is the most
    expensive failure this engine can ship, and it costs one hash to catch.
    """
    if not result.solutions:
        return (
            f"stage {part.part_id!r} reported no solution values to compare "
            "its own answer against"
        )
    intended = stable_hash(dict(part.solution))
    found = {stable_hash(dict(solution)) for solution in result.solutions}
    if intended in found:
        return None
    if result.solutions_truncated:
        return (
            f"stage {part.part_id!r}: the stage's stated answer is not among "
            "those its verifier listed, and a truncated list is not an excuse "
            "for omitting the intended one"
        )
    return (
        f"stage {part.part_id!r}: the stage's stated answer is not among those "
        "its verifier found"
    )


def completeness_problem(
    outcomes: tuple[PartOutcome, ...], declared: VerificationCompleteness
) -> str | None:
    """Whether any stage enumerated less than its game promised.

    A board's completeness is its weakest stage's: a game declaring COMPLETE
    and verifying one stage within a bound has not completely verified the
    board, and the stage is where that would otherwise go unnoticed.
    """
    if declared is not VerificationCompleteness.COMPLETE:
        return None
    partial = [
        outcome.part_id
        for outcome in outcomes
        if outcome.verification.completeness
        is not VerificationCompleteness.COMPLETE
    ]
    if partial:
        return (
            "game declared complete verification but enumerated stages "
            f"{partial} only within a bound"
        )
    return None
