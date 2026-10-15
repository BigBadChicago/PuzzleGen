"""The deduction a generator already did, emitted so a hint can be derived.

Every daily game's hints are authored strings or reveals. Both are the same
admission: the generator knew why the answer follows and threw that knowledge
away, so help has to be written by a person who then guesses how much it
gives away. A generator that builds a board by elimination rather than by
search has the reason in hand, and this module is the form it is kept in.

**A deduction is a statement, not a reveal.** It is one true claim about the
answer, carried as the facts it establishes, together with the exact count of
how many of the board's candidate answers remain once it is believed. That
count is the whole of what makes a hint system non-spoiling, so it is worth
being precise about which space it is counted over.

**The space is the board's unconstrained space, not its solved one.** A board
whose uniqueness contract is satisfied admits exactly one answer under its own
clues, so a statement's narrowing *of the clued space* is zero for every true
statement and useless as a measure. Measured instead over every answer the
board's vocabulary admits -- every code of this length over these digits,
before a clue is read -- a statement's narrowing is exactly how much of the
answer the statement itself states. "Some digit of the code is 7" leaves most
of the space; "the code starts with 7" leaves a tenth of it; "the code is
7391" leaves one. That ordering is the hint ladder, derived rather than
authored, and it is the ordering a player means by a small hint.

**Minimal means cheapest first, and never a reveal.** The service hands back
the least informative statement the player does not already hold. A statement
that alone determines the answer is refused unless a caller explicitly asks
for a reveal, which is what "cannot spoil" means operationally: the ladder
runs out before the answer does.

**A witness is absolute; what one player is handed is a walk.** The costs in a
witness are counted over the empty board, because that is what makes them
checkable by anyone holding the puzzle. A player three hints in is not on the
empty board, and measured, the difference reorders the ladder and makes some
of its rungs vacuous. :func:`walk` re-costs what is unspent against what the
player holds. Its result is a sequence rather than a witness, and the two are
kept apart on purpose: the fixed ordering is the evidence, and the marginal
one is the help.

**Assurance is declared, never implied.** A witness is not a uniqueness
proof. It says the statements in it are true of the stated answer and that
their costs are exact; whether the answer is the *only* one is the
certifier's claim, not this one. So a witness carries an
:class:`~puzzlegen.engine.certify.AssuranceLevel`, a caller may demand a
floor, and a game whose space is not enumerable declares
``ANSWER_VALID`` rather than borrowing a stronger word.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from .certify import AssuranceLevel
from .coupling import Fact, SolutionSpace


class WitnessError(ValueError):
    """A witness could not be built, or does not hold up."""


@dataclass(frozen=True, slots=True)
class Deduction:
    """One true statement about a board's answer, and what it costs.

    ``establishes`` is the statement in the engine's fact vocabulary, so the
    same value can be checked against a solution space, compared with what a
    player already holds, and handed to another board as a cross reference.
    ``statement`` is the wording a client shows; it is the game's, because the
    phrasing of a claim about the rules is part of the rules.

    ``space_before`` and ``space_after`` are counted over the board's
    unconstrained space, which is what makes :attr:`information` a property of
    the statement rather than of whatever else happens to be on the board.
    """

    statement: str
    establishes: tuple[Fact, ...]
    space_before: int
    space_after: int
    #: What makes the statement true: the clues it is read off, named so a
    #: hint can show its working rather than assert itself.
    justification: tuple[str, ...] = ()
    #: Presentation element ids this statement speaks about, for a shell that
    #: wants to highlight them.
    reveals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.statement:
            raise WitnessError("a deduction must state something")
        if not self.establishes:
            raise WitnessError(
                "a deduction must carry the facts it establishes; a statement "
                "with no facts cannot be checked against a space or compared "
                "with what a player already holds"
            )
        if self.space_before < 1:
            raise WitnessError(
                "a deduction is counted over a space with at least one answer"
            )
        if not 0 <= self.space_after <= self.space_before:
            raise WitnessError(
                f"{self.space_after} answers remain of {self.space_before}, "
                "which is not a narrowing"
            )
        if self.space_after == 0:
            raise WitnessError(
                "a deduction that leaves no answer is false about this board, "
                "not a hint about it"
            )

    @property
    def information(self) -> float:
        """The proportion of the unconstrained space this statement removes.

        The same formula as a coupling leak, deliberately: both ask how much
        one piece of knowledge narrows a set of answers, and two formulas for
        one quantity would eventually disagree.
        """
        return 1.0 - (self.space_after / self.space_before)

    @property
    def is_reveal(self) -> bool:
        """Whether this statement alone determines the answer."""
        return self.space_after == 1 and self.space_before > 1

    @property
    def is_vacuous(self) -> bool:
        """Whether it removes nothing, and so tells a player nothing."""
        return self.space_after == self.space_before

    def is_held_by(self, known: frozenset[Fact]) -> bool:
        """Whether a player holding ``known`` already has this statement.

        Every fact, not any: a statement establishing two facts of which the
        player holds one is still worth something, and treating it as held
        would skip the cheapest rung of the ladder.
        """
        return all(fact in known for fact in self.establishes)

    def describe(self) -> str:
        return (
            f"{self.statement} [{self.space_after}/{self.space_before} "
            f"answers, {self.information:.4f} given]"
        )


@dataclass(frozen=True, slots=True)
class DeductionWitness:
    """A board's deductions, cheapest first, with a declared assurance.

    The ordering is the witness's content, not a convenience: a hint service
    walking it in order is what makes "minimal" mean anything, and a witness
    built out of order would make the first hint an arbitrary one. So the
    order is checked here rather than trusted.
    """

    game_id: str
    assurance: AssuranceLevel
    steps: tuple[Deduction, ...]
    #: The unconstrained space every step is counted over. Carried so a
    #: caller can tell a board with one candidate answer from a board whose
    #: steps happen to leave one.
    space: int
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.game_id:
            raise WitnessError("a witness must name the game it is about")
        if self.space < 1:
            raise WitnessError("a witness is counted over a non-empty space")
        for step in self.steps:
            if step.space_before != self.space:
                raise WitnessError(
                    f"step {step.statement!r} is counted over "
                    f"{step.space_before} answers, the witness over "
                    f"{self.space}; costs over different spaces are not "
                    "comparable and so cannot be ordered"
                )
            if step.is_vacuous:
                raise WitnessError(
                    f"step {step.statement!r} removes nothing, so it is not a "
                    "deduction about this board"
                )
        costs = [step.space_after for step in self.steps]
        if costs != sorted(costs, reverse=True):
            raise WitnessError(
                "a witness's steps run cheapest first; an unordered witness "
                "would make the first hint an arbitrary one"
            )

    @property
    def exhausted(self) -> bool:
        return not self.steps

    def holds_up(self, space: SolutionSpace) -> tuple[str, ...]:
        """Statements whose recorded cost ``space`` does not agree with.

        The witness re-derived against the space it claims to describe. This
        is the check that makes a witness evidence rather than an assertion,
        and it is cheap for exactly the games that can have one: a space small
        enough to enumerate is small enough to re-enumerate per step.
        """
        wrong: list[str] = []
        before = len(list(space.enumerate()))
        if before != self.space:
            # One finding, not a per-step list: every step's cost is counted
            # over the witness's own space, so a space of another size makes
            # all of them unverifiable for one reason.
            return (
                f"the witness is counted over {self.space} answers, the space "
                f"offers {before}",
            )
        understood = frozenset(space.understands())
        for step in self.steps:
            unknown = [
                fact.predicate
                for fact in step.establishes
                if fact.predicate not in understood
            ]
            if unknown:
                wrong.append(
                    f"{step.statement}: the space reads none of "
                    f"{sorted(set(unknown))}, so its cost is unchecked"
                )
                continue
            narrowed = space
            for fact in step.establishes:
                narrowed = narrowed.constrain(fact)
            after = len(list(narrowed.enumerate()))
            if after != step.space_after:
                wrong.append(
                    f"{step.statement}: recorded {step.space_after} answers "
                    f"remaining, the space leaves {after}"
                )
        return tuple(wrong)

    def facts(self) -> frozenset[Fact]:
        """Every fact any step establishes."""
        return frozenset(
            fact for step in self.steps for fact in step.establishes
        )

    def predicates(self) -> frozenset[str]:
        """The predicates this witness's steps are stated in.

        What a cross reference filters another board's facts against: a fact
        in a predicate no step of this witness uses settles no step of it,
        and applying one anyway would be reading another game's rules under
        this game's.
        """
        return frozenset(fact.predicate for fact in self.facts())

    def describe(self) -> tuple[str, ...]:
        return (
            f"{self.game_id}: {len(self.steps)} deduction(s) over "
            f"{self.space} answers, assurance {self.assurance}",
            *(f"  {step.describe()}" for step in self.steps),
        )


def build_witness(
    *,
    game_id: str,
    assurance: AssuranceLevel,
    space: SolutionSpace,
    candidates: Iterable[Deduction],
    detail: str = "",
) -> DeductionWitness:
    """Order ``candidates`` into a witness, dropping the ones that say nothing.

    The ordering and the dropping are here rather than in each game, because
    they are the arithmetic that makes a hint minimal and one implementation
    of it can be tested once. What a game supplies is its own vocabulary: the
    statements it can make and the wording for each.

    Ties are broken by statement text, so a board's witness is the same
    witness on every machine. A hint is replayed when a session is regraded,
    and a tie broken by iteration order would make two replays disagree.
    """
    kept = [step for step in candidates if not step.is_vacuous]
    over = len(list(space.enumerate()))
    mismatched = [s for s in kept if s.space_before != over]
    if mismatched:
        raise WitnessError(
            f"{len(mismatched)} candidate(s) are counted over a different "
            f"space than the one supplied ({over} answers): "
            + ", ".join(sorted(s.statement for s in mismatched)[:3])
        )
    kept.sort(key=lambda step: (-step.space_after, step.statement))
    return DeductionWitness(
        game_id=game_id,
        assurance=assurance,
        steps=tuple(kept),
        space=over,
        detail=detail,
    )


def next_deduction(
    witness: DeductionWitness,
    known: Iterable[Fact] = (),
    *,
    given: int = 0,
    floor: AssuranceLevel = AssuranceLevel.ANSWER_VALID,
    allow_reveal: bool = False,
) -> Deduction | None:
    """The cheapest statement a player holding ``known`` does not already have.

    ``given`` is how many statements this player has already been handed, so
    a second request moves one rung up the ladder rather than repeating the
    first. It is a count rather than a list because that is what the engine
    stores: a hint is counted, and its text is re-derived on replay from the
    puzzle, the state and the count, which is what keeps grading a pure
    function of the moves.

    ``None`` means there is nothing left to say that is not the answer, which
    is the state a non-spoiling hint system is supposed to reach. It is
    returned rather than raised because running out of hints is a normal
    position in a game, not an error.

    ``floor`` refuses a witness whose assurance is below what the caller will
    accept. A caller that did not state a floor gets ``ANSWER_VALID``, the
    weakest level, which is the honest default: the alternative is a caller
    silently treating every witness as a uniqueness proof.
    """
    if not witness.assurance.at_least(floor):
        raise WitnessError(
            f"{witness.game_id}'s witness is assured at {witness.assurance}, "
            f"below the {floor} this caller requires"
        )
    if given < 0:
        raise WitnessError("a player cannot have been given fewer than no hints")
    for step in remaining(witness, known)[given:]:
        if step.is_reveal and not allow_reveal:
            # Ordered cheapest first, so every later step is at least as
            # informative: once the ladder reaches a reveal there is nothing
            # cheaper further down.
            return None
        return step
    return None


def walk(
    witness: DeductionWitness,
    space: SolutionSpace,
    known: Iterable[Fact] = (),
    *,
    limit: int,
    floor: AssuranceLevel = AssuranceLevel.ANSWER_VALID,
    allow_reveal: bool = False,
) -> tuple[Deduction, ...]:
    """The next ``limit`` statements, each costed against the ones before it.

    :func:`next_deduction` reads the ladder absolutely: a step's cost is its
    narrowing of the board's unconstrained space, fixed when the witness was
    built and checkable by anyone against that space alone. That is what a
    witness has to be. It is not what a player experiences by their third
    hint, because the first two have already narrowed the space the third
    would be measured over.

    Measured on a four-digit board, the two orders differ. The absolute ladder
    offers seven exclusions before its first inclusion; re-costed as the
    player accumulates them, the fourth exclusion is dearer than the first
    inclusion and the two kinds interleave. Some statements go further than
    reordering: a digit the player can already rule out from earlier rungs
    removes nothing once they are held, and a statement that removes nothing
    is not a hint.

    So this walk re-costs every unspent statement against the space the
    player's knowledge actually leaves, takes the cheapest, applies it and
    repeats, dropping each statement that has gone vacuous rather than
    offering it at a cost of nothing. The result is a sequence, not a witness:
    its costs rise and fall as earlier rungs are applied, so it cannot satisfy
    :class:`DeductionWitness`'s cheapest-first invariant and is deliberately
    not wrapped in one. The witness stays absolute and verifiable; this is
    what one player is handed at request time.

    ``limit`` is required rather than defaulted, because each step re-counts
    every unspent statement over the narrowed space: the walk costs about
    ``limit`` times the witness's length in enumerations, and a caller that
    wants two rungs should not pay for fourteen.
    """
    if not witness.assurance.at_least(floor):
        raise WitnessError(
            f"{witness.game_id}'s witness is assured at {witness.assurance}, "
            f"below the {floor} this caller requires"
        )
    if limit < 0:
        raise WitnessError("a walk cannot be shorter than no steps")
    readable = frozenset(space.understands())
    held = frozenset(known)
    current = space
    for fact in held:
        if fact.predicate in readable:
            current = current.constrain(fact)
    unspent = list(remaining(witness, held))
    walked: list[Deduction] = []
    while unspent and len(walked) < limit:
        before = len(list(current.enumerate()))
        costed: list[tuple[int, str, Deduction, SolutionSpace]] = []
        for step in unspent:
            narrowed = current
            for fact in step.establishes:
                if fact.predicate in readable:
                    narrowed = narrowed.constrain(fact)
            after = len(list(narrowed.enumerate()))
            if after == before or after == 0:
                # Dropped, not offered: either what the player holds already
                # forces this statement, or -- on a board whose uniqueness
                # contract did not in fact hold -- it contradicts what they
                # hold. Neither is something to charge a hint for.
                continue
            costed.append((after, step.statement, step, narrowed))
        if not costed:
            break
        # Cheapest first, ties broken on text, exactly as the absolute ladder
        # is ordered: the same two boards must produce the same walk, because
        # a hint is re-derived from the count when a session is regraded.
        costed.sort(key=lambda row: (-row[0], row[1]))
        after, _, step, narrowed = costed[0]
        marginal = replace(step, space_before=before, space_after=after)
        if marginal.is_reveal and not allow_reveal:
            # The ladder still runs out before the answer does, now against
            # the player's own accumulated knowledge rather than against the
            # empty board: the stronger of the two claims.
            break
        walked.append(marginal)
        current = narrowed
        unspent.remove(step)
    return tuple(walked)


def remaining(
    witness: DeductionWitness, known: Iterable[Fact] = ()
) -> tuple[Deduction, ...]:
    """Every statement in the witness a player holding ``known`` lacks.

    What a cross reference's saving is measured against: handing a board a
    fact is worth the steps it removes from this list.
    """
    held = frozenset(known)
    return tuple(step for step in witness.steps if not step.is_held_by(held))


def deduction_for(
    statement: str,
    facts: Sequence[Fact],
    space: SolutionSpace,
    *,
    justification: Sequence[str] = (),
    reveals: Sequence[str] = (),
) -> Deduction:
    """Build one deduction by counting ``facts`` against ``space``.

    A convenience with one job: it is the only place a step's cost is counted,
    so a game cannot supply a cost that disagrees with the space it claims to
    be about. A game building a :class:`Deduction` by hand is free to, and its
    witness will be caught by :meth:`DeductionWitness.holds_up`.
    """
    before = len(list(space.enumerate()))
    narrowed = space
    for fact in facts:
        narrowed = narrowed.constrain(fact)
    return Deduction(
        statement=statement,
        establishes=tuple(facts),
        space_before=before,
        space_after=len(list(narrowed.enumerate())),
        justification=tuple(justification),
        reveals=tuple(reveals),
    )


def entails(space: SolutionSpace, facts: Sequence[Fact]) -> bool:
    """Whether every answer ``space`` still admits satisfies ``facts``.

    The guard that makes a step a deduction rather than a leak of the answer.
    A game assembles candidate statements from the answer it holds, so every
    one of them is true *of the answer* by construction; what has to be
    checked is that the board's own clues already force it. Passed the
    survivors of the clue set, this is that check, and it is the difference
    between a hint a player could have derived and one they could not.
    """
    narrowed = space
    for fact in facts:
        narrowed = narrowed.constrain(fact)
    return len(list(narrowed.enumerate())) == len(list(space.enumerate()))


__all__ = [
    "Deduction",
    "DeductionWitness",
    "WitnessError",
    "build_witness",
    "deduction_for",
    "entails",
    "next_deduction",
    "remaining",
    "walk",
]
