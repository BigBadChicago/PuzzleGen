"""Uniqueness enforcement.

A game declares a contract; the engine decides whether the verifier's own
numbers honour it. The asymmetry is the point: a plugin never gets to assert
that its puzzle is unique, it only reports what it found, and the judgement is
made here against that report.

Each contract has its own rule because they genuinely differ. Two of them need
evidence beyond a solution count: a tolerance-based contract needs to know the
verifier collapsed equivalent solutions itself, and a minimal-path contract
needs to know longer alternatives exist, because a "shortest path" puzzle with
no longer path is not a puzzle at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import UniquenessContract, VerificationCompleteness
from .plugin import GameDescriptor, VerificationResult

#: Contracts satisfied by exactly one solution and nothing else.
SINGLE_SOLUTION_CONTRACTS = frozenset(
    {
        UniquenessContract.EXACTLY_ONE,
        UniquenessContract.UNIQUE_GROUPING,
        UniquenessContract.UNIQUE_ORDERING,
        UniquenessContract.UNIQUE_DEDUCTION,
        UniquenessContract.NO_ALTERNATE_INTERPRETATION,
    }
)

#: Metric a minimal-path game must report: how many valid solutions exist at
#: greater length than the intended one.
LONGER_ALTERNATIVES = "longer_alternatives"

#: Metric a tolerance-based game must report: how many raw solutions were
#: collapsed into the reported count.
COLLAPSED_SOLUTIONS = "collapsed_solutions"


@dataclass(frozen=True, slots=True)
class UniquenessVerdict:
    satisfied: bool
    contract: UniquenessContract
    solution_count: int
    reason: str = ""

    def __bool__(self) -> bool:
        return self.satisfied


def check(
    descriptor: GameDescriptor, verification: VerificationResult
) -> UniquenessVerdict:
    """Whether a verification result honours the game's declared contract."""
    contract = descriptor.uniqueness_contract
    count = verification.solution_count

    def verdict(ok: bool, reason: str = "") -> UniquenessVerdict:
        return UniquenessVerdict(ok, contract, count, reason)

    if not verification.solvable:
        return verdict(False, "puzzle has no solution")

    # An incomplete enumeration can prove existence but never uniqueness: the
    # solution it did not reach is exactly the one that would break the
    # contract. So a partial search satisfies no uniqueness contract at all.
    if verification.completeness is VerificationCompleteness.SOUND_INCOMPLETE:
        if contract in SINGLE_SOLUTION_CONTRACTS or contract in (
            UniquenessContract.EXACTLY_N,
            UniquenessContract.UNIQUE_UP_TO_TOLERANCE,
            UniquenessContract.UNIQUE_MINIMAL_PATH,
        ):
            return verdict(
                False,
                "an incomplete enumeration cannot establish uniqueness; the "
                "unexamined branch is where a second solution would hide",
            )

    if contract in SINGLE_SOLUTION_CONTRACTS:
        if count == 1:
            return verdict(True)
        return verdict(False, f"{contract} requires exactly one solution, found {count}")

    if contract is UniquenessContract.EXACTLY_N:
        expected = descriptor.expected_solution_count
        if expected is None:
            return verdict(False, "EXACTLY_N declared without a count")
        if count == expected:
            return verdict(True)
        return verdict(False, f"expected {expected} solutions, found {count}")

    if contract is UniquenessContract.UNIQUE_UP_TO_TOLERANCE:
        if count != 1:
            return verdict(
                False,
                f"after collapsing equivalents, {count} distinct solutions remain",
            )
        if COLLAPSED_SOLUTIONS not in verification.metrics:
            return verdict(
                False,
                f"a tolerance contract must report {COLLAPSED_SOLUTIONS!r}, so "
                "the engine can tell collapsing from never having happened",
            )
        return verdict(True)

    if contract is UniquenessContract.UNIQUE_MINIMAL_PATH:
        if count != 1:
            return verdict(False, f"{count} solutions at minimal length")
        longer = verification.metrics.get(LONGER_ALTERNATIVES)
        if longer is None:
            return verdict(
                False,
                f"a minimal-path contract must report {LONGER_ALTERNATIVES!r}",
            )
        if longer < 1:
            return verdict(
                False,
                "no longer alternative exists, so the minimal path is the only "
                "path and the puzzle asks nothing",
            )
        return verdict(True)

    return verdict(False, f"no rule implemented for contract {contract}")


def consistency_problem(verification: VerificationResult) -> str | None:
    """Internal contradictions in a verification result.

    Separate from contract checking because these are bugs in a game's
    verifier rather than properties of its puzzle, and they should be
    reported as protocol errors rather than as content rejections.
    """
    if verification.solutions_truncated and len(verification.solutions) >= (
        verification.solution_count
    ):
        return "marked truncated but listed every solution"
    if (
        verification.completeness is VerificationCompleteness.COMPLETE
        and verification.states_examined == 0
        and verification.solution_count > 0
    ):
        return "claims a complete enumeration having examined no states"
    return None
