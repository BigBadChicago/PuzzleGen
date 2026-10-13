"""The engine checks, the game proposes.

A game ships a generator and a verifier written by the same author, from the
same reading of the rules, often sharing the same helper module. When they
agree on a false answer there is nothing in the system that disagrees. That is
not a hypothetical: the verifier is the only thing standing between a
mis-generated board and a published day, and it was written by the party whose
board it judges.

A certifier is the engine's own second opinion. It is written from the rules as
stated, reads the published payload and the stored solution, and reaches its
own verdict. Its value comes entirely from being *independent*, and
independence is a discipline no type system enforces: a certifier that imported
the game's own deduction helpers and called them would pass every test and
check nothing.

So the discipline is converted into a test. A certifier module may import
:mod:`puzzlegen.engine.solvers` and the standard library, and nothing from its
game's distribution at all. ``TestCertifierIndependence`` in the invariant
suite parses the modules and fails the build on a violation, the same way the
plugin boundary is enforced. The consequence is deliberate duplication: the
``digit_lock`` certifier re-derives the code space and the clue arithmetic
rather than importing ``lock.py``. That duplication is the second opinion.

Assurance is declared per certifier because it is not uniform. A game whose
solution space is enumerable can prove uniqueness; one whose uniqueness
contract is ``SOUND_INCOMPLETE`` by design cannot, and claiming otherwise
would be the same overreach in the opposite direction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class AssuranceLevel(StrEnum):
    """How much a certifier's acceptance is worth.

    Ordered, and comparable through :meth:`at_least`, so a caller can ask for
    a floor rather than enumerating the levels it will accept.
    """

    #: The stated answer satisfies the stated rules. Says nothing about
    #: whether another answer would too.
    ANSWER_VALID = "ANSWER_VALID"
    #: The number of answers satisfying the rules is known exactly.
    SOLUTION_COUNT_KNOWN = "SOLUTION_COUNT_KNOWN"
    #: The stated answer is the only answer, proved by exhaustion.
    UNIQUENESS_PROVED = "UNIQUENESS_PROVED"

    def at_least(self, floor: "AssuranceLevel") -> bool:
        order = (
            AssuranceLevel.ANSWER_VALID,
            AssuranceLevel.SOLUTION_COUNT_KNOWN,
            AssuranceLevel.UNIQUENESS_PROVED,
        )
        return order.index(self) >= order.index(floor)


@dataclass(frozen=True, slots=True)
class CertificateResult:
    """One certifier's verdict on one board.

    ``reasons`` carries every finding rather than the first, because a board
    that fails two ways is a different diagnosis from one that fails once, and
    a certifier that stopped at the first would hide the difference.
    """

    game_id: str
    accepted: bool
    assurance: AssuranceLevel
    #: Exact count where the certifier enumerated, ``None`` where it did not.
    #: ``None`` is not zero and not one; a caller that cannot tell them apart
    #: should not be reading this field.
    solution_count: int | None = None
    states_examined: int = 0
    reasons: tuple[str, ...] = ()
    detail: str = ""

    @property
    def rejected(self) -> bool:
        return not self.accepted

    def describe(self) -> str:
        if self.accepted:
            return (
                f"{self.game_id}: certified at {self.assurance}"
                + (
                    f", {self.solution_count} solution(s)"
                    if self.solution_count is not None
                    else ""
                )
            )
        return f"{self.game_id}: refused -- " + "; ".join(self.reasons)


@runtime_checkable
class Certifier(Protocol):
    """The engine's independent check on one game's boards."""

    @property
    def game_id(self) -> str: ...

    @property
    def assurance(self) -> AssuranceLevel:
        """The strongest claim this certifier is able to make."""
        ...

    def certify(
        self, payload: Mapping[str, Any], solution: Mapping[str, Any]
    ) -> CertificateResult: ...


class CertifierRegistry:
    """Certifiers by game id.

    Deliberately not the game registry. A game is registered because it is
    installed; a certifier exists because somebody wrote one, and the set of
    games with a certifier is smaller than the set of games. Merging them
    would make "no certifier for this game" indistinguishable from "this game
    is not installed", and those call for different behaviour.
    """

    def __init__(self) -> None:
        self._certifiers: dict[str, Certifier] = {}

    def register(self, certifier: Certifier) -> None:
        game_id = certifier.game_id
        if game_id in self._certifiers:
            raise ValueError(f"a certifier for {game_id!r} is already registered")
        self._certifiers[game_id] = certifier

    def get(self, game_id: str) -> Certifier | None:
        """The certifier for a game, or ``None`` if there is not one."""
        return self._certifiers.get(game_id)

    def has(self, game_id: str) -> bool:
        return game_id in self._certifiers

    def game_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._certifiers))

    def certify(
        self,
        game_id: str,
        payload: Mapping[str, Any],
        solution: Mapping[str, Any],
    ) -> CertificateResult | None:
        """Certify a board, or ``None`` where no certifier is registered.

        ``None`` rather than an acceptance. An unexamined board has not
        passed; a caller that treated a missing certifier as a pass would get
        exactly the false confidence this module exists to remove.
        """
        certifier = self.get(game_id)
        if certifier is None:
            return None
        return certifier.certify(payload, solution)


def default_registry() -> CertifierRegistry:
    """Every certifier this release ships.

    Imported here rather than at module scope so that importing the protocol
    does not import every certifier, and so a certifier module's own imports
    stay visible to the independence test as that module's imports.
    """
    from .certifiers import digit_lock, grouping, odd_number_out, shape_trio

    registry = CertifierRegistry()
    for module in (digit_lock, grouping, odd_number_out, shape_trio):
        registry.register(module.certifier())
    return registry


@dataclass(frozen=True, slots=True)
class Disagreement:
    """A certifier and a game's own verifier reaching different verdicts.

    Its own type because the differential test over committed day runs is
    this module's immediate payoff, and a disagreement needs both verdicts
    side by side to be worth reporting.
    """

    game_id: str
    day_key: str
    certifier_accepted: bool
    verifier_accepted: bool
    certifier_count: int | None
    verifier_count: int | None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def describe(self) -> str:
        return (
            f"{self.day_key} {self.game_id}: certifier "
            f"{'accepted' if self.certifier_accepted else 'refused'} "
            f"({self.certifier_count}), verifier "
            f"{'accepted' if self.verifier_accepted else 'refused'} "
            f"({self.verifier_count})"
            + (f" -- {'; '.join(self.reasons)}" if self.reasons else "")
        )


def differential(outcome: Any, registry: CertifierRegistry) -> Disagreement | None:
    """Compare a certifier's verdict with the one generation accepted.

    ``None`` means no disagreement, which includes the two cases where there
    is nothing to compare: no certifier for the game, and a run that produced
    no board. A failed run is not a disagreement -- the certifier was never
    offered a board -- and reporting it as one would bury real findings under
    every content-starved day.

    Takes the outcome structurally rather than by type, so this module does
    not import the pipeline it is called from.
    """
    puzzle = getattr(outcome, "puzzle", None)
    evaluation = getattr(outcome, "evaluation", None)
    if puzzle is None or not registry.has(puzzle.game_id):
        return None
    result = registry.certify(puzzle.game_id, puzzle.payload, puzzle.solution)
    if result is None:
        return None

    verification = getattr(evaluation, "verification", None) if evaluation else None
    verifier_count = (
        verification.solution_count if verification is not None else None
    )
    verifier_accepted = bool(getattr(outcome, "succeeded", False))
    # Verdicts, not acceptances: equality of outcome with an unequal count is
    # two checks agreeing by luck, and it is exactly the case where a
    # generator and a verifier share a mistaken reading of the rules.
    if (
        result.accepted == verifier_accepted
        and result.solution_count == verifier_count
    ):
        return None
    return Disagreement(
        game_id=puzzle.game_id,
        day_key=getattr(getattr(outcome, "trace", None), "day_key", ""),
        certifier_accepted=result.accepted,
        verifier_accepted=verifier_accepted,
        certifier_count=result.solution_count,
        verifier_count=verifier_count,
        reasons=result.reasons,
    )


__all__ = [
    "AssuranceLevel",
    "CertificateResult",
    "Certifier",
    "CertifierRegistry",
    "Disagreement",
    "default_registry",
    "differential",
]
