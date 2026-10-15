"""The licensed leak: what one board's answer saves you on another.

Both reviews that reached NEW-3 asked for a gate proving no board leaks
another. This module is the inversion: a daily set may *deliberately license*
a controlled shortcut, so that a deduction available only in today's board A
collapses work board B would otherwise demand. The games stop being five
independent dailies and become one object where the solve order matters.

The whole design turns on a quantity the obvious reading of "leak" cannot
express, so it is worth stating why there are two measurements rather than
one.

**Leak is the safety bound, and it must stay zero.** ``coupling.measure_leak``
asks how much of board B's *answer* board A's answer gives away, over B's
solution space. A board whose answer is derivable from outside its own clues
is broken whatever the set intends, so no declaration permits a non-zero
leak. And a board whose uniqueness contract holds admits exactly one answer
once its clues are read, which means its leak is zero by construction -- which
is also why leak alone cannot express a mechanic. Measured over thirty real
days every pair reads zero, and a set built to that figure alone would be a
set with nothing in it.

**Advantage is the mechanic, and it is measured over work rather than over
answers.** A player holding board B's clues has not performed B's deductions;
the witness is the list of deductions B still demands. A cross reference hands
the player a fact, and its advantage is the proportion of B's remaining
deductions that fact settles. That figure is non-zero on exactly the boards
leak is zero on, which is the resolution: the set can be provably safe and
genuinely linked at the same time, and the two claims are separate numbers
that cannot be mistaken for each other.

**The coupling is never load bearing.** A cross reference is admissible only
if the target board is already solvable alone -- its own clues entail its own
answer, with no fact from anywhere else. So a player who plays one game is
never punished, which is the risk the review names and the only one that
matters, because most people play one game. The shortcut is strictly a
shortcut.

**A shortcut that finishes the board is not a shortcut.** A cross reference
whose facts jointly determine the target's answer has not saved the player
work, it has done the puzzle. Such an offer is refused, by the same rule and
for the same reason the hint ladder stops before the answer.

What this module does not do is decide which facts cross. A fact is read by
the board whose predicates understand it, and this release's games namespace
their predicates per game, so no pair shares one and every real advantage is
currently zero. Making a pair shareable means a board publishing the entity
behind a tile rather than only its glyph, which is a change to what a day
publishes and a content decision, not a measurement. The machinery and its
gate are here so that the change is a payload decision rather than a
mechanic to design under time pressure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .coupling import DailySetDeclaration, Fact, SolutionSpace
from .witness import DeductionWitness, remaining


@dataclass(frozen=True, slots=True)
class CrossReference:
    """What board A's answer is worth to board B, and whether it is allowed.

    ``advantage`` is ``None`` for unknown, never zero: a target with no
    witness is a board whose remaining work nobody measured, and reading that
    as "no shortcut" would let an unmeasured pair ship as a measured one.
    """

    source_game_id: str
    target_game_id: str
    #: The source's facts the target's own rules can read. The rest are not
    #: applied: a target handed a predicate it does not define would be
    #: interpreting another game's rules under its own.
    offered: tuple[Fact, ...]
    #: Deductions the target still demanded before the offer, and after it.
    steps_before: int | None = None
    steps_after: int | None = None
    advantage: float | None = None
    #: Target answers remaining once every offered fact is believed. One
    #: means the offer finished the board.
    answers_after: int | None = None
    #: Whether the target's own clues entail its own answer with no help.
    solvable_alone: bool = False
    detail: str = ""

    @property
    def known(self) -> bool:
        return self.advantage is not None

    @property
    def is_shortcut(self) -> bool:
        """Whether the offer saves the player anything at all."""
        return bool(self.advantage)

    @property
    def finishes_the_board(self) -> bool:
        """Whether the offer settles every deduction the target had left.

        Measured over the witness rather than over the target's answers, and
        the distinction is not pedantic. The target's clued space holds one
        answer whenever the board stands alone, so an offer can never reduce
        *that* and a rule written over it would either be vacuous or fire on
        every admissible board. What an offer can finish is the reasoning,
        and an offer that finishes the reasoning has done the puzzle.
        """
        return self.advantage == 1.0

    @property
    def contradicts_the_target(self) -> bool:
        """Whether believing the offer leaves the target no answer at all.

        Not a leak and not a shortcut: two boards asserting incompatible
        things about the same entity. It is a content fault rather than a
        design one, and it has to fail loudly because the player it reaches
        is a player holding two true-looking claims and no solvable board.
        """
        return self.answers_after == 0

    def findings(self) -> tuple[str, ...]:
        """Why this cross reference may not be served, empty when it may.

        Four rules, and the first is the one the review's risk turns on.
        """
        found: list[str] = []
        if not self.solvable_alone:
            found.append(
                f"{self.target_game_id} is not solvable from its own clues, so "
                "a cross reference to it would be load bearing: a player who "
                "plays only this game could not finish it"
            )
        if self.advantage is None:
            found.append(
                f"{self.target_game_id}'s remaining work is unmeasured, so the "
                "advantage is unknown rather than zero"
            )
        if self.finishes_the_board:
            found.append(
                f"the offer settles every deduction {self.target_game_id} had "
                "left, so it does the puzzle rather than shortening it"
            )
        if self.contradicts_the_target:
            found.append(
                f"believing the offer leaves {self.target_game_id} no answer, "
                "so the two boards disagree about what they share"
            )
        return tuple(found)

    @property
    def admissible(self) -> bool:
        return not self.findings()

    def describe(self) -> str:
        if self.advantage is None:
            return (
                f"{self.source_game_id} -> {self.target_game_id}: unknown"
                + (f" ({self.detail})" if self.detail else "")
            )
        return (
            f"{self.source_game_id} -> {self.target_game_id}: "
            f"{self.advantage:.4f} ({self.steps_before} -> {self.steps_after} "
            f"deduction(s) under {len(self.offered)} fact(s))"
        )


def offer(
    source: SolutionSpace | None,
    target_witness: DeductionWitness | None,
    target_space: SolutionSpace | None,
    *,
    source_game_id: str,
    target_game_id: str,
    known: Iterable[Fact] = (),
) -> CrossReference:
    """``advantage(source -> target)``, exactly, or ``None`` where it cannot be.

    ``target_space`` is the target's *clued* space -- the answers its own
    clues admit -- and is what settles whether the board stands alone.
    ``target_witness`` is counted over the unconstrained space, because that
    is where a statement's cost is a property of the statement; the two are
    different spaces on purpose and :mod:`puzzlegen.engine.witness` says why.

    ``known`` is what the player already holds, so the advantage is measured
    against the work actually left rather than against a fresh board.
    """
    if source is None or target_witness is None or target_space is None:
        missing = source_game_id if source is None else target_game_id
        return CrossReference(
            source_game_id=source_game_id,
            target_game_id=target_game_id,
            offered=(),
            detail=(
                f"{missing} has no enumerable space or no witness, so the "
                "advantage is unknown rather than zero"
            ),
        )

    alone = len(list(target_space.enumerate())) == 1
    understood = target_witness.predicates()
    crossing = tuple(
        fact for fact in source.facts() if fact.predicate in understood
    )
    held = frozenset(known)
    before = remaining(target_witness, held)
    after = remaining(target_witness, held | frozenset(crossing))

    narrowed = target_space
    for fact in crossing:
        narrowed = narrowed.constrain(fact)
    answers_after = len(list(narrowed.enumerate()))

    if not before:
        return CrossReference(
            source_game_id=source_game_id,
            target_game_id=target_game_id,
            offered=crossing,
            steps_before=0,
            steps_after=0,
            answers_after=answers_after,
            solvable_alone=alone,
            detail=(
                f"{target_game_id} demands no further deduction, so there is "
                "no work for the other board to save"
            ),
        )

    return CrossReference(
        source_game_id=source_game_id,
        target_game_id=target_game_id,
        offered=crossing,
        steps_before=len(before),
        steps_after=len(after),
        advantage=1.0 - (len(after) / len(before)),
        answers_after=answers_after,
        solvable_alone=alone,
        detail=(
            ""
            if crossing
            else (
                f"{target_game_id} reads none of the facts "
                f"{source_game_id}'s answer reveals, so its answer cannot "
                "shorten this board"
            )
        ),
    )


@dataclass(frozen=True, slots=True)
class CrossReferenceReport:
    """One day's cross references, and which declarations they fail."""

    day_key: str
    set_name: str = ""
    references: tuple[CrossReference, ...] = ()
    #: A reference and the reason it fails: a broken invariant, or an
    #: advantage outside the window its pair declared.
    violations: tuple[tuple[CrossReference, str], ...] = ()
    #: Games on the day the declaration says nothing about. Not a violation
    #: by itself, and never safe: an undeclared board is an unchecked one.
    undeclared: tuple[str, ...] = ()

    @property
    def shortcuts(self) -> tuple[CrossReference, ...]:
        return tuple(ref for ref in self.references if ref.is_shortcut)

    @property
    def passes(self) -> bool:
        return not self.violations and not self.undeclared

    def describe(self) -> tuple[str, ...]:
        lines = [ref.describe() for ref in self.references]
        lines.extend(
            f"VIOLATION {ref.source_game_id} -> {ref.target_game_id}: {why}"
            for ref, why in self.violations
        )
        lines.extend(
            f"UNDECLARED {game_id}: this day served a board the declaration "
            "does not cover"
            for game_id in self.undeclared
        )
        return tuple(lines)


def check_day(
    day_key: str,
    spaces: Mapping[str, SolutionSpace | None],
    witnesses: Mapping[str, DeductionWitness | None],
    declaration: DailySetDeclaration,
) -> CrossReferenceReport:
    """Measure a day's cross references and check them against the set.

    Takes the declaration rather than defaulting to one. The measurement
    without a declaration is a report, and a report that gates nothing is
    what this project already had; the declaration is what makes the same
    arithmetic a gate.
    """
    references: list[CrossReference] = []
    violations: list[tuple[CrossReference, str]] = []
    for pair in declaration.pairs:
        if pair.source_game_id not in spaces or pair.target_game_id not in spaces:
            # A declaration about a pair not on the board is a stale
            # declaration, and reporting it beats ignoring it.
            reference = CrossReference(
                source_game_id=pair.source_game_id,
                target_game_id=pair.target_game_id,
                offered=(),
                detail="no such pair on this day",
            )
            references.append(reference)
            violations.append((reference, "the day served neither board"))
            continue
        reference = offer(
            spaces[pair.source_game_id],
            witnesses.get(pair.target_game_id),
            spaces[pair.target_game_id],
            source_game_id=pair.source_game_id,
            target_game_id=pair.target_game_id,
        )
        references.append(reference)
        if pair.is_coupled:
            # Only a pair declaring a mechanic has to satisfy the mechanic's
            # invariants. An independent pair is allowed an unmeasurable
            # target: it is not offering anything to it.
            for finding in reference.findings():
                violations.append((reference, finding))
            if not pair.advantage.admits(reference.advantage):
                violations.append(
                    (
                        reference,
                        f"declared advantage {pair.advantage.describe()}, "
                        + (
                            "measured unknown"
                            if reference.advantage is None
                            else f"measured {reference.advantage:.4f}"
                        ),
                    )
                )
        elif reference.is_shortcut:
            violations.append(
                (
                    reference,
                    "this pair is declared uncoupled and the source's answer "
                    f"settles {reference.steps_before - reference.steps_after} "
                    "of the target's deductions",
                )
            )
    return CrossReferenceReport(
        day_key=day_key,
        set_name=declaration.name,
        references=tuple(references),
        violations=tuple(violations),
        undeclared=declaration.covers(spaces),
    )


def witnesses_for(
    boards: Mapping[str, tuple[Mapping[str, object], Mapping[str, object]]],
    builders: Mapping[str, object] | None = None,
) -> dict[str, DeductionWitness | None]:
    """Build a witness per board from ``(payload, solution)`` pairs.

    A game with no witness builder maps to ``None``: unmeasured, and so not
    a board a declaration may couple to. Absence is a refusal to couple, for
    the same reason it is in :func:`puzzlegen.engine.coupling.default_spaces`
    -- adding a builder is a deliberate act and forgetting one cannot produce
    a false zero.
    """
    available: Mapping[str, object] = builders if builders is not None else {}
    built: dict[str, DeductionWitness | None] = {}
    for game_id, (payload, solution) in boards.items():
        builder = available.get(game_id)
        built[game_id] = builder(payload, solution) if builder is not None else None
    return built


__all__ = [
    "CrossReference",
    "CrossReferenceReport",
    "check_day",
    "offer",
    "witnesses_for",
]
