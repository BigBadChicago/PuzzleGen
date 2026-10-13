"""How much one board tells you about another.

The question is whether two boards served on the same day are independent. The
obvious answer is a boolean -- they share an entity or they do not -- and the
obvious answer is the wrong instrument, because the same measurement has to
serve two opposite purposes. A daily set of unrelated puzzles needs
``leak == 0``. A deliberately linked set, where solving one board narrows
another, needs ``0 < leak < 1`` against a declared target. A boolean answers
the first and cannot express the second, so building it first means building
it twice.

**The definition.** For boards A and B on one day, ``leak(A -> B)`` is the
proportion of B's solution space that knowing A's answer removes:

    leak(A -> B) = 1 - |B's space given A's answer| / |B's space|

Zero means A tells you nothing about B. One means A gives B away. It is
directed: A can constrain B while B leaves A untouched, and a symmetric figure
would hide that.

**Exact, by enumeration, or not at all.** The symbolic games' spaces are 5,040
codes, 220 trios and a handful of tiles, so an exact figure is cheap and needs
no estimator. A game whose space is not enumerable reports ``None``, and
``None`` means *unknown*, which is emphatically not the same as safe: a daily
set may not couple a game that cannot report. Treating an unknown as a zero is
how a leak ships.

What "knowing A's answer" means is each fact A's answer reveals, applied to B
as a constraint. A fact is a ``(ref, value)`` pair drawn from A's own answer,
and what a board does with one is the board's business -- which is why
:class:`SolutionSpace` has exactly one interesting method.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing solving a board tells you.

    A ref and a value rather than a bare entity id, because "the answer is
    this tile" and "this tile is not in the answer" are different facts about
    the same entity and constrain another board differently.
    """

    ref: str
    value: Any = True

    def __str__(self) -> str:
        return f"{self.ref}={self.value}"


@runtime_checkable
class SolutionSpace(Protocol):
    """The set of answers a board still admits.

    Deliberately tiny. A game's space knows how to list itself and how to
    narrow itself given a fact, and nothing here needs to know what a fact
    means to it.
    """

    def enumerate(self) -> Iterable[Any]:
        """Every answer this space still admits."""
        ...

    def constrain(self, fact: Fact) -> "SolutionSpace":
        """This space narrowed by one fact. Never mutates the original."""
        ...

    def facts(self) -> Iterable[Fact]:
        """The facts solving this board reveals, when its answer is known."""
        ...


@dataclass(frozen=True, slots=True)
class EnumeratedSpace:
    """A solution space held as an explicit list of answers.

    Every symbolic game's space fits in memory, so holding the answers rather
    than a description of them keeps the implementations to a few lines each
    and makes the arithmetic obviously exact.

    ``admits`` is supplied by the caller: it decides what one fact means to
    one answer, which is the only game-specific knowledge in this module.

    ``namespace`` is what makes a cross-game figure meaningful. A fact is only
    a fact about the same thing in both boards if both boards name things the
    same way. Two boards that drew from the same entities but describe their
    answers in different vocabularies would measure a leak of exactly zero,
    and that zero would be false -- which is the one failure mode a quantity
    is supposed to prevent. So a space declares its vocabulary and a
    measurement across two different ones is unknown, not zero.
    """

    answers: tuple[Any, ...]
    admits: Any  # Callable[[Any, Fact], bool]; typed loosely to stay hashable
    revealed: tuple[Fact, ...] = ()
    #: ``"entity"`` where the board names graph entity ids, which is what
    #: makes a figure comparable with another board's. A game whose payload
    #: names things in its own terms declares its own namespace and is
    #: therefore not comparable with any other.
    namespace: str = "entity"

    def enumerate(self) -> Iterable[Any]:
        return self.answers

    def constrain(self, fact: Fact) -> "EnumeratedSpace":
        return EnumeratedSpace(
            answers=tuple(
                answer for answer in self.answers if self.admits(answer, fact)
            ),
            admits=self.admits,
            revealed=self.revealed,
            namespace=self.namespace,
        )

    def facts(self) -> Iterable[Fact]:
        return self.revealed

    def __len__(self) -> int:
        return len(self.answers)


@dataclass(frozen=True, slots=True)
class LeakMeasurement:
    """How much one board narrows another, with the figures behind it."""

    source_game_id: str
    target_game_id: str
    #: ``None`` means unknown, because one of the two spaces is not
    #: enumerable. It is not zero and must never be read as safe.
    leak: float | None
    space_before: int | None = None
    space_after: int | None = None
    facts_applied: int = 0
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.leak is not None

    @property
    def independent(self) -> bool:
        """Whether the source provably tells you nothing about the target.

        An unknown measurement is not independent. This property is what a
        safety gate reads, so it is the one place the ``None``-is-not-safe
        rule has to be written down in code.
        """
        return self.leak == 0.0

    @property
    def gives_it_away(self) -> bool:
        return self.leak == 1.0

    def describe(self) -> str:
        if self.leak is None:
            return (
                f"{self.source_game_id} -> {self.target_game_id}: unknown"
                + (f" ({self.detail})" if self.detail else "")
            )
        return (
            f"{self.source_game_id} -> {self.target_game_id}: "
            f"{self.leak:.4f} ({self.space_before} -> {self.space_after} "
            f"answers under {self.facts_applied} fact(s))"
        )


def measure_leak(
    source: SolutionSpace | None,
    target: SolutionSpace | None,
    *,
    source_game_id: str,
    target_game_id: str,
) -> LeakMeasurement:
    """``leak(source -> target)``, exactly, or ``None`` where it cannot be.

    A target space that is already empty is a broken board, not a leak of
    one: dividing by zero would report 0.0, which would read as independence.
    """
    if source is None or target is None:
        missing = source_game_id if source is None else target_game_id
        return LeakMeasurement(
            source_game_id=source_game_id,
            target_game_id=target_game_id,
            leak=None,
            detail=(
                f"{missing} has no enumerable solution space, so the leak is "
                "unknown rather than zero"
            ),
        )

    source_namespace = getattr(source, "namespace", "entity")
    target_namespace = getattr(target, "namespace", "entity")
    if source_namespace != target_namespace:
        return LeakMeasurement(
            source_game_id=source_game_id,
            target_game_id=target_game_id,
            leak=None,
            detail=(
                f"{source_game_id} names its answers in the "
                f"{source_namespace!r} vocabulary and {target_game_id} in "
                f"{target_namespace!r}; a figure across two vocabularies "
                "would be a zero that means nothing was compared"
            ),
        )

    before = list(target.enumerate())
    if not before:
        return LeakMeasurement(
            source_game_id=source_game_id,
            target_game_id=target_game_id,
            leak=None,
            space_before=0,
            detail=(
                f"{target_game_id} admits no answer at all, so there is no "
                "space for the other board to narrow"
            ),
        )

    facts = list(source.facts())
    narrowed = target
    for fact in facts:
        narrowed = narrowed.constrain(fact)
    after = list(narrowed.enumerate())
    return LeakMeasurement(
        source_game_id=source_game_id,
        target_game_id=target_game_id,
        leak=1.0 - (len(after) / len(before)),
        space_before=len(before),
        space_after=len(after),
        facts_applied=len(facts),
    )


def measure_day(
    spaces: Mapping[str, SolutionSpace | None],
) -> tuple[LeakMeasurement, ...]:
    """Every ordered pair of distinct boards on one day.

    Ordered, because the relation is not symmetric, and a day's report that
    collapsed each pair to one figure would hide the asymmetry that matters
    for a mechanic.
    """
    ids = sorted(spaces)
    return tuple(
        measure_leak(
            spaces[source],
            spaces[target],
            source_game_id=source,
            target_game_id=target,
        )
        for source in ids
        for target in ids
        if source != target
    )


@dataclass(frozen=True, slots=True)
class CouplingTarget:
    """What a daily set declares about one directed pair.

    ``maximum`` of zero with ``minimum`` of zero is the independent default:
    the pair must provably leak nothing. A mechanic declares a window instead.
    """

    source_game_id: str
    target_game_id: str
    minimum: float = 0.0
    maximum: float = 0.0

    def __post_init__(self) -> None:
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must lie on [0, 1], got {value}")
        if self.minimum > self.maximum:
            raise ValueError(
                "a coupling target's minimum cannot exceed its maximum"
            )

    def admits(self, measurement: LeakMeasurement) -> bool:
        """Whether a measurement satisfies this declaration.

        An unknown never satisfies one, including the independent default.
        A daily set may not couple a game that cannot report.
        """
        if measurement.leak is None:
            return False
        return self.minimum <= measurement.leak <= self.maximum


@dataclass(frozen=True, slots=True)
class CouplingReport:
    """One day's measurements, and which declarations they fail."""

    day_key: str
    measurements: tuple[LeakMeasurement, ...] = ()
    violations: tuple[tuple[CouplingTarget, LeakMeasurement], ...] = ()

    @property
    def unknown(self) -> tuple[LeakMeasurement, ...]:
        return tuple(m for m in self.measurements if not m.known)

    @property
    def leaking(self) -> tuple[LeakMeasurement, ...]:
        """Measured pairs that are not independent, declared or not."""
        return tuple(m for m in self.measurements if m.known and not m.independent)

    @property
    def passes(self) -> bool:
        return not self.violations

    def describe(self) -> tuple[str, ...]:
        lines = [m.describe() for m in self.measurements]
        lines.extend(
            f"VIOLATION {target.source_game_id} -> {target.target_game_id}: "
            f"declared [{target.minimum}, {target.maximum}], measured "
            f"{'unknown' if measurement.leak is None else f'{measurement.leak:.4f}'}"
            for target, measurement in self.violations
        )
        return tuple(lines)


def check_day(
    day_key: str,
    spaces: Mapping[str, SolutionSpace | None],
    targets: Sequence[CouplingTarget] = (),
) -> CouplingReport:
    """Measure a day and check it against its declared coupling.

    With no declarations this is a report and gates nothing, which is the
    honest state while nobody has said what a day's coupling should be. Pass
    declarations and the same call becomes the gate.
    """
    measurements = measure_day(spaces)
    by_pair = {
        (m.source_game_id, m.target_game_id): m for m in measurements
    }
    violations = []
    for target in targets:
        measurement = by_pair.get(
            (target.source_game_id, target.target_game_id)
        )
        if measurement is None:
            # A declaration about a pair that is not on the board is a stale
            # declaration, and reporting it is better than ignoring it.
            violations.append(
                (
                    target,
                    LeakMeasurement(
                        source_game_id=target.source_game_id,
                        target_game_id=target.target_game_id,
                        leak=None,
                        detail="no such pair on this day",
                    ),
                )
            )
        elif not target.admits(measurement):
            violations.append((target, measurement))
    return CouplingReport(
        day_key=day_key,
        measurements=measurements,
        violations=tuple(violations),
    )


def independent_targets(game_ids: Iterable[str]) -> tuple[CouplingTarget, ...]:
    """Declare every ordered pair independent.

    The default a daily set of unrelated puzzles wants, written out rather
    than implied, so a day that is supposed to be independent fails loudly
    instead of being checked against nothing.
    """
    ids = sorted(set(game_ids))
    return tuple(
        CouplingTarget(source_game_id=source, target_game_id=target)
        for source in ids
        for target in ids
        if source != target
    )


def default_spaces() -> Mapping[str, Any]:
    """Space builders by game id, for the games that have one.

    A game absent from this mapping has no enumerable space, and
    :func:`measure_leak` reports unknown for every pair it appears in. Absence
    is therefore a refusal to couple, which is the right default: adding a
    builder is a deliberate act and forgetting one cannot produce a false
    zero.
    """
    from .certifiers import digit_lock, odd_number_out, shape_trio

    return {
        module.GAME_ID: module.space
        for module in (digit_lock, odd_number_out, shape_trio)
    }


def spaces_for_day(
    boards: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
    builders: Mapping[str, Any] | None = None,
) -> dict[str, SolutionSpace | None]:
    """Build a space per board from ``(payload, solution)`` pairs.

    A game with no builder, or a board a builder cannot read, maps to
    ``None``: unknown, and not safe.
    """
    available = builders if builders is not None else default_spaces()
    built: dict[str, SolutionSpace | None] = {}
    for game_id, (payload, solution) in boards.items():
        builder = available.get(game_id)
        built[game_id] = (
            builder(payload, solution) if builder is not None else None
        )
    return built


def _pairs(ids: Sequence[str]) -> Iterator[tuple[str, str]]:
    for source in ids:
        for target in ids:
            if source != target:
                yield source, target


__all__ = [
    "CouplingReport",
    "CouplingTarget",
    "EnumeratedSpace",
    "Fact",
    "LeakMeasurement",
    "SolutionSpace",
    "check_day",
    "default_spaces",
    "spaces_for_day",
    "independent_targets",
    "measure_day",
    "measure_leak",
]
