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

    Three parts, and the first is the one that took a measurement over thirty
    real days to get right. A fact is not an entity id and a flag; it is a
    *predicate* applied to a ref with a value. "This tile is not in my trio"
    and "this tile is not the odd one out" are different claims about the same
    entity, and a model that recorded both as ``(tile, False)`` would have one
    board's fact silently read as the other board's -- which measured a leak
    of 1.0 between two games that in fact share nothing. The predicate is what
    makes a fact composable only where it means the same thing.

    Predicates are namespaced by the game that defines them, because a
    predicate is part of a game's rules and two games' notions of "is in the
    answer" are not the same notion.
    """

    predicate: str
    ref: str
    value: Any = True

    def __str__(self) -> str:
        return f"{self.predicate}({self.ref})={self.value}"


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

    def understands(self) -> Iterable[str]:
        """The predicates :meth:`constrain` can actually read.

        A space is handed only the facts it declares it understands. Without
        this, a fact from another game reaches ``constrain`` as an unfamiliar
        predicate and whatever that method does with it is a measurement of a
        coincidence rather than of the rules.
        """
        ...


@dataclass(frozen=True, slots=True)
class EnumeratedSpace:
    """A solution space held as an explicit list of answers.

    Every symbolic game's space fits in memory, so holding the answers rather
    than a description of them keeps the implementations to a few lines each
    and makes the arithmetic obviously exact.

    ``admits`` is supplied by the caller: it decides what one fact means to
    one answer, which is the only game-specific knowledge in this module.

    ``predicates`` is what keeps a figure meaningful. It names the predicates
    ``admits`` can read, and a measurement applies nothing else. A space that
    accepted every fact handed to it would be interpreting another game's
    rules under its own, which is exactly the error a measurement over real
    boards caught: two games sharing entities but no predicate measured a leak
    of 1.0, because each read the other's "not in my answer" as its own.
    """

    answers: tuple[Any, ...]
    admits: Any  # Callable[[Any, Fact], bool]; typed loosely to stay hashable
    revealed: tuple[Fact, ...] = ()
    #: Predicates this space can read. A fact outside this set is not applied.
    predicates: frozenset[str] = frozenset()

    def enumerate(self) -> Iterable[Any]:
        return self.answers

    def constrain(self, fact: Fact) -> "EnumeratedSpace":
        return EnumeratedSpace(
            answers=tuple(
                answer for answer in self.answers if self.admits(answer, fact)
            ),
            admits=self.admits,
            revealed=self.revealed,
            predicates=self.predicates,
        )

    def facts(self) -> Iterable[Fact]:
        return self.revealed

    def understands(self) -> Iterable[str]:
        return self.predicates

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

    # Only the facts the target's own rules can read. A target handed a
    # predicate it does not define would be interpreting another game's rules
    # under its own, and the figure would measure that misreading.
    understood = frozenset(target.understands())
    offered = list(source.facts())
    applicable = [fact for fact in offered if fact.predicate in understood]
    narrowed = target
    for fact in applicable:
        narrowed = narrowed.constrain(fact)
    after = list(narrowed.enumerate())
    return LeakMeasurement(
        source_game_id=source_game_id,
        target_game_id=target_game_id,
        leak=1.0 - (len(after) / len(before)),
        space_before=len(before),
        space_after=len(after),
        facts_applied=len(applicable),
        detail=(
            ""
            if applicable or not offered
            else (
                f"{target_game_id} reads none of the "
                f"{len(offered)} fact(s) {source_game_id} reveals, so its "
                "answer cannot narrow this board"
            )
        ),
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
    #: Every game that served a board this day, measured or not. Recorded
    #: rather than recovered from the measurements, because a day with one
    #: board has no pairs to recover it from and would otherwise serialize as
    #: a day that served nothing.
    boards: tuple[str, ...] = ()
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
        boards=tuple(sorted(spaces)),
        measurements=measurements,
        violations=tuple(violations),
    )


@dataclass(frozen=True, slots=True)
class Window:
    """A closed interval a measurement has to land in.

    Its own type because a daily set declares two different quantities with
    the same shape, and a pair of floats named ``minimum`` and ``maximum``
    twice over is a pair of floats somebody will eventually swap.
    """

    minimum: float = 0.0
    maximum: float = 0.0

    def __post_init__(self) -> None:
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must lie on [0, 1], got {value}")
        if self.minimum > self.maximum:
            raise ValueError("a window's minimum cannot exceed its maximum")

    @property
    def is_zero(self) -> bool:
        return self.minimum == 0.0 and self.maximum == 0.0

    def admits(self, value: float | None) -> bool:
        """Whether ``value`` lands in the window. ``None`` never does.

        An unknown is not a zero. A daily set may not couple a game that
        cannot report, and the independent default is a declaration like any
        other: an unmeasurable pair fails it rather than passing unexamined.
        """
        if value is None:
            return False
        return self.minimum <= value <= self.maximum

    def describe(self) -> str:
        return f"[{self.minimum}, {self.maximum}]"

    def as_document(self) -> list[float]:
        return [self.minimum, self.maximum]

    @classmethod
    def from_document(cls, document: Any) -> "Window":
        low, high = document
        return cls(minimum=float(low), maximum=float(high))


@dataclass(frozen=True, slots=True)
class PairIntent:
    """What a daily set intends one ordered pair of its boards to be.

    Two windows, because the two questions a coupled set raises are not the
    same question and collapsing them would make one of them unaskable.

    ``leak`` is the safety bound, over the solver's space: how much of board
    B's *answer* board A's answer gives away. A board whose answer is
    derivable from outside its own clues is broken however the set is
    designed, so this window stays at zero for every pair the project has
    declared so far.

    ``advantage`` is the mechanic, over board B's deduction work: how much of
    the reasoning B still demands A's answer saves. This is the quantity a
    coupled set wants in the open interval -- more than nothing, so the
    cross-reference is worth finding, and less than everything, so B is still
    a puzzle. It is a different measurement from ``leak`` and not a rescaling
    of it: a uniquely-solvable board has a leak of zero by construction and
    can still have a large advantage, which is precisely the position a
    deliberately linked set wants to be in.
    """

    source_game_id: str
    target_game_id: str
    leak: Window = Window()
    advantage: Window = Window()
    #: Why this pair is declared the way it is. Carried into the artifact,
    #: because a window of [0.2, 0.5] with no stated reason is a number
    #: nobody can review.
    note: str = ""

    def __post_init__(self) -> None:
        if self.source_game_id == self.target_game_id:
            raise ValueError("a board is not coupled to itself")
        if self.leak.maximum >= 1.0:
            raise ValueError(
                "a declaration may not permit a leak of 1.0; that is one "
                "board printing another's answer, which no mechanic needs"
            )
        if self.advantage.maximum >= 1.0:
            raise ValueError(
                "a declaration may not permit an advantage of 1.0; a "
                "cross reference that finishes the other board's deduction "
                "has made that board not worth playing"
            )
        if self.advantage.minimum > 0.0 and self.leak.maximum > 0.0:
            raise ValueError(
                "a pair cannot declare both a required advantage and a "
                "permitted leak: the mechanic is a shortcut through the "
                "target's reasoning, and a set that also lets its answer "
                "escape has made the shortcut load bearing"
            )

    @property
    def is_coupled(self) -> bool:
        """Whether this pair intends a mechanic rather than independence."""
        return self.advantage.maximum > 0.0

    def as_target(self) -> CouplingTarget:
        """The leak half, in the form :func:`check_day` already reads."""
        return CouplingTarget(
            source_game_id=self.source_game_id,
            target_game_id=self.target_game_id,
            minimum=self.leak.minimum,
            maximum=self.leak.maximum,
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "from": self.source_game_id,
            "to": self.target_game_id,
            "leak": self.leak.as_document(),
            "advantage": self.advantage.as_document(),
            "note": self.note,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "PairIntent":
        return cls(
            source_game_id=str(document["from"]),
            target_game_id=str(document["to"]),
            leak=Window.from_document(document.get("leak", [0.0, 0.0])),
            advantage=Window.from_document(document.get("advantage", [0.0, 0.0])),
            note=str(document.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class DailySetDeclaration:
    """What one daily set says its boards are to each other.

    The thing the measurement was always missing. ``check_day`` with no
    declarations is a report and gates nothing, which was the honest state
    while nobody had said what a day's coupling should be; a declaration is
    that statement, committed, so a day that was supposed to be independent
    fails loudly instead of being checked against nothing.

    Every ordered pair of the set's games must be declared. Not for tidiness:
    a pair nobody declared is a pair nobody checked, and the failure mode of
    the thing this gate exists to prevent -- a leak nobody intended -- is
    exactly a pair that fell off the list when a game was added.
    """

    name: str
    game_ids: tuple[str, ...]
    pairs: tuple[PairIntent, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a daily set declaration must be named")
        if len(set(self.game_ids)) != len(self.game_ids):
            raise ValueError("a daily set lists each game once")
        if len(self.game_ids) < 2:
            raise ValueError(
                "a daily set with fewer than two boards has no pair to declare"
            )
        declared = {(p.source_game_id, p.target_game_id) for p in self.pairs}
        if len(declared) != len(self.pairs):
            raise ValueError("a pair is declared twice")
        expected = set(_pairs(self.game_ids))
        missing = sorted(expected - declared)
        extra = sorted(declared - expected)
        if missing:
            raise ValueError(
                "every ordered pair must be declared; these are not: "
                + ", ".join(f"{a} -> {b}" for a, b in missing)
            )
        if extra:
            raise ValueError(
                "these pairs name a game the set does not serve: "
                + ", ".join(f"{a} -> {b}" for a, b in extra)
            )

    @property
    def coupled_pairs(self) -> tuple[PairIntent, ...]:
        return tuple(pair for pair in self.pairs if pair.is_coupled)

    def targets(self) -> tuple[CouplingTarget, ...]:
        """The leak windows, for :func:`check_day`."""
        return tuple(pair.as_target() for pair in self.pairs)

    def intent_for(self, source: str, target: str) -> PairIntent | None:
        for pair in self.pairs:
            if (pair.source_game_id, pair.target_game_id) == (source, target):
                return pair
        return None

    def covers(self, game_ids: Iterable[str]) -> tuple[str, ...]:
        """Games present on a day that this declaration says nothing about.

        Returned rather than raised, because a day serving a game the
        declaration does not know is a legitimate thing to report and a
        caller decides whether it is a failure. What it is not is safe: an
        undeclared board is an unchecked board.
        """
        return tuple(sorted(set(game_ids) - set(self.game_ids)))

    def as_document(self) -> dict[str, Any]:
        return {
            "record": "daily_set_declaration",
            "name": self.name,
            "games": list(self.game_ids),
            "pairs": [pair.as_document() for pair in self.pairs],
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "DailySetDeclaration":
        return cls(
            name=str(document["name"]),
            game_ids=tuple(str(game) for game in document["games"]),
            pairs=tuple(
                PairIntent.from_document(pair) for pair in document["pairs"]
            ),
        )

    @classmethod
    def independent(
        cls, name: str, game_ids: Iterable[str], *, note: str = ""
    ) -> "DailySetDeclaration":
        """Declare every ordered pair independent and uncoupled."""
        ids = tuple(sorted(set(game_ids)))
        return cls(
            name=name,
            game_ids=ids,
            pairs=tuple(
                PairIntent(
                    source_game_id=source, target_game_id=target, note=note
                )
                for source, target in _pairs(ids)
            ),
        )


def read_declaration(path: Any) -> DailySetDeclaration | None:
    """Read a committed declaration, or ``None`` if there is not one.

    ``None`` rather than an exception, so a caller can tell "nobody has
    declared this set's coupling" from "the declaration is wrong". The two
    call for different behaviour: the first is the state the project was in
    until this file existed, and the second is a build failure.
    """
    import json
    import pathlib

    source = pathlib.Path(path)
    if not source.exists():
        return None
    return DailySetDeclaration.from_document(
        json.loads(source.read_text(encoding="utf-8"))
    )


def write_declaration(path: Any, declaration: DailySetDeclaration) -> None:
    import json
    import pathlib

    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(declaration.as_document(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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
    "DailySetDeclaration",
    "PairIntent",
    "Window",
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
    "read_declaration",
    "write_declaration",
]
