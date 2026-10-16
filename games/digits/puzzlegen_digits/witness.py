"""The elimination this game already does, emitted as a deduction witness.

``lock.space_for`` is the whole code space and ``lock.survivors`` is the
elimination over it. Generation uses both; nothing used to keep the result,
so help had to be authored -- three sentences written by a person who then
judged by eye how much each gave away. This module keeps it instead, and the
hint becomes the cheapest true statement about the code that the board's own
clues already force.

Three decisions are worth stating.

**The cost of a statement is counted over the whole code space, not over the
survivors.** A board of this game admits exactly one code once its clues are
read, so a statement's narrowing of the *survivor* set is zero for every true
statement, which would order the ladder by nothing at all. Counted over every
code of this length over these digits, "the 7 is not in the code" leaves five
hundred and four of seven hundred and twenty, "a 7 is in the code" leaves two
hundred and sixteen, and "the code starts with 7" leaves seventy-two. That is
the order a player means by a small hint, and it falls out of the arithmetic
rather than being asserted.

**A statement is admissible only if every survivor satisfies it.** Every
candidate here is built from the code, so each is true of the answer by
construction; that is not the same as being deducible. The entailment check
against the survivor set is what makes a hint something the player could have
worked out, and it is also the one guard that would catch a board whose
uniqueness contract was not in fact met.

**Exclusions come before inclusions.** The ladder is derived, so the first
hint this game now gives is that some digit is *not* in the code, where the
authored version named a digit that was. That is not a style choice; an
exclusion over ten digits removes less of the space than an inclusion does,
and the whole claim of the witness is that the cheapest true thing is offered
first.

**What is offered is the witness re-costed, not the witness read in order.**
The witness is absolute: every cost is counted over the empty board, which is
what makes it checkable by anyone holding the puzzle. A player on their fourth
hint is not on the empty board. Measured, the difference is real -- seven
exclusions lead the absolute ladder, four lead the walk, and three of the
later exclusions have gone vacuous by the time they would be reached -- so
:func:`upcoming` walks the witness marginally through
:func:`puzzlegen.engine.witness.walk` instead of indexing it. The ladder is
also capped per board by ``play.MAX_HINTS``, which is a policy about how much
help a daily puzzle should sell rather than a claim about deduction, and so
lives with the mistake limit and not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from puzzlegen.engine.certify import AssuranceLevel
from puzzlegen.engine.coupling import EnumeratedSpace, Fact
from puzzlegen.engine.plugin import Puzzle
from puzzlegen.engine.witness import (
    Deduction,
    DeductionWitness,
    build_witness,
    deduction_for,
    entails,
    walk,
)

from .assemble import answer_code, board_digits, board_facts, board_guesses
from .descriptor import GAME_ID
from .lock import DigitFacts, space_for, survivors

#: This game's predicates, namespaced because a predicate is part of a game's
#: rules. ``AT`` carries a position; ``USES`` carries whether the digit
#: appears at all. Two predicates rather than one with a ``False`` value,
#: because "the 7 is somewhere in the code" is the cheapest rung of the ladder
#: and a positional predicate cannot state it.
AT = f"{GAME_ID}/at"
USES = f"{GAME_ID}/uses"

#: The assurance a witness over this game's boards carries.
#:
#: ``UNIQUENESS_PROVED``, and only because the space is enumerated in full:
#: every statement's cost is an exact count over every code of this length,
#: and every statement is checked against every code the clues admit. A game
#: that sampled its space would declare ``ANSWER_VALID`` here and be telling
#: the truth; borrowing this level would not.
ASSURANCE = AssuranceLevel.UNIQUENESS_PROVED


def _admits(code: tuple[int, ...], fact: Fact) -> bool:
    """Whether one code is still possible given one statement."""
    digit = int(fact.ref)
    if fact.predicate == USES:
        return (digit in code) is bool(fact.value)
    if fact.predicate == AT:
        position = int(fact.value)
        return 0 <= position < len(code) and code[position] == digit
    return True


def _facts_for(puzzle: Puzzle) -> dict[int, DigitFacts]:
    stated: Mapping[str, Mapping[str, Any]] = puzzle.solution["digit_facts"]
    return {
        int(digit): DigitFacts(
            value=int(entry["value"]),
            prime=bool(entry["prime"]),
            even=bool(entry["even"]),
        )
        for digit, entry in stated.items()
    }


def code_space(puzzle: Puzzle) -> EnumeratedSpace:
    """Every code the board's vocabulary admits, before a clue is read.

    The space costs are counted over. Five thousand and forty codes at four
    digits, which is why this is an exact figure rather than an estimate.
    """
    return EnumeratedSpace(
        answers=tuple(
            space_for(int(puzzle.payload["code_length"]), board_digits(puzzle))
        ),
        admits=_admits,
        predicates=frozenset({AT, USES}),
    )


def survivor_space(puzzle: Puzzle) -> EnumeratedSpace:
    """Every code the board's clues admit: the elimination's own result."""
    return EnumeratedSpace(
        answers=tuple(
            survivors(
                space_for(
                    int(puzzle.payload["code_length"]), board_digits(puzzle)
                ),
                board_guesses(puzzle),
                board_facts(puzzle),
                _facts_for(puzzle),
            )
        ),
        admits=_admits,
        predicates=frozenset({AT, USES}),
    )


def clue_elements(puzzle: Puzzle) -> tuple[str, ...]:
    """The render element ids a deduction rests on.

    Every clue and every stated fact, because a statement entailed by the
    board is entailed by the board: naming a minimal sufficient subset would
    be a better answer and costs a search over clue subsets per statement,
    which is not a price a hint request can pay. Element ids rather than prose
    so a shell can highlight the rows the deduction was read off.
    """
    return tuple(
        f"clue:{index}" for index in range(len(puzzle.payload.get("clues", ())))
    ) + tuple(
        f"fact:{index}" for index in range(len(puzzle.payload.get("facts", ())))
    )


def _candidates(
    puzzle: Puzzle, space: EnumeratedSpace
) -> list[Deduction]:
    """Every statement this game can make about its own answer.

    Built from the code, so each is true of it; whether each is *deducible*
    is settled against the survivor set by :func:`witness_for`.
    """
    code = answer_code(puzzle)
    glyphs: Mapping[str, str] = puzzle.payload.get("glyphs", {})

    def glyph(digit: int) -> str:
        return glyphs.get(str(digit), str(digit))

    offered: list[Deduction] = []
    for digit in board_digits(puzzle):
        inside = digit in code
        offered.append(
            deduction_for(
                (
                    f"The digit {glyph(digit)} is in the code"
                    if inside
                    else f"The digit {glyph(digit)} is not in the code"
                ),
                [Fact(predicate=USES, ref=str(digit), value=inside)],
                space,
            )
        )
    for position, digit in enumerate(code):
        offered.append(
            deduction_for(
                f"Position {position + 1} is {glyph(digit)}",
                [Fact(predicate=AT, ref=str(digit), value=position)],
                space,
            )
        )
    offered.append(
        deduction_for(
            "The code uses "
            + ", ".join(glyph(digit) for digit in sorted(code))
            + ", in some order",
            [Fact(predicate=USES, ref=str(digit), value=True) for digit in sorted(code)],
            space,
        )
    )
    return offered


def witness_for(puzzle: Puzzle) -> DeductionWitness:
    """This board's deductions, cheapest first.

    A statement no survivor contradicts is a statement the clues force. On a
    board whose contract holds there is one survivor and the check passes for
    every true statement; on a board with several it drops the statements that
    happen to hold of the stated answer and not of the others, which is
    exactly the set a player could not have derived.
    """
    space = code_space(puzzle)
    admitted = survivor_space(puzzle)
    elements = clue_elements(puzzle)
    deducible = [
        replace(step, justification=elements)
        for step in _candidates(puzzle, space)
        if entails(admitted, step.establishes)
    ]
    return build_witness(
        game_id=GAME_ID,
        assurance=ASSURANCE,
        space=space,
        candidates=deducible,
        detail=(
            f"{len(admitted.answers)} of {len(space.answers)} codes survive "
            f"the board's {len(elements)} clue(s)"
        ),
    )


def known_facts(puzzle: Puzzle, state: Mapping[str, Any]) -> tuple[Fact, ...]:
    """What the player's own attempts have already told them.

    Only what is readable off an attempt row without search, because the
    point of the test is to skip a hint the player demonstrably has rather
    than to model what they could in principle have inferred. Two rows carry
    that much: a guess told none of its digits is in the code, and a guess
    told every digit of it is, in whatever order.

    Read from the replayed state rather than from a store, so a hint is a pure
    function of the puzzle, the moves before it and the hint count -- which is
    what lets a regrade reproduce it.
    """
    length = int(puzzle.payload["code_length"])
    held: list[Fact] = []
    for attempt in state.get("attempts", ()) or ():
        guess = [int(digit) for digit in attempt.get("guess", ())]
        exact = int(attempt.get("exact", 0))
        misplaced = int(attempt.get("misplaced", 0))
        if exact == 0 and misplaced == 0:
            held.extend(
                Fact(predicate=USES, ref=str(digit), value=False)
                for digit in guess
            )
        elif exact + misplaced == length:
            held.extend(
                Fact(predicate=USES, ref=str(digit), value=True)
                for digit in guess
            )
    return tuple(dict.fromkeys(held))


def upcoming(
    puzzle: Puzzle,
    state: Mapping[str, Any],
    hints_used: int,
    count: int = 1,
) -> tuple[Deduction, ...]:
    """The next ``count`` deductions to offer, costed as the player will feel.

    A marginal walk rather than a read off the witness, because the witness is
    ordered by each statement's narrowing of the empty board and that stops
    describing a player who already holds three rungs of it. The walk re-costs
    what is unspent against what they hold, so the fourth hint is the cheapest
    thing left to say *to them* and a rung their earlier hints already forced
    is dropped instead of being charged for.

    ``count`` is asked for rather than taken one at a time so a caller that
    needs to know whether a rung after this one exists pays for one walk and
    not two; the walk re-enumerates the code space per unspent statement per
    step, which is affordable once and visible twice.
    """
    walked = walk(
        witness_for(puzzle),
        code_space(puzzle),
        known_facts(puzzle, state),
        limit=hints_used + count,
        floor=AssuranceLevel.ANSWER_VALID,
    )
    return walked[hints_used:]


def next_step(
    puzzle: Puzzle, state: Mapping[str, Any], hints_used: int
) -> Deduction | None:
    """The next deduction to offer, or ``None`` when only the answer is left."""
    offered = upcoming(puzzle, state, hints_used)
    return offered[0] if offered else None


def check(puzzle: Puzzle) -> tuple[str, ...]:
    """Findings against this board's witness, empty when it holds up.

    The witness re-derived against the space it describes, plus the two claims
    a hint rests on: every step is entailed by the clues, and no step is a
    reveal. Not called from ``verify``, deliberately -- verification already
    enumerates this space to prove the board, and a second enumeration per
    candidate would double the cost of every generation to recheck arithmetic
    the same pass established. It is a check for the suite and for a
    diagnostic over real days, which is where a witness that did not hold up
    would be a finding about this game rather than about one board.
    """
    witness = witness_for(puzzle)
    findings = list(witness.holds_up(code_space(puzzle)))
    admitted = survivor_space(puzzle)
    findings.extend(
        f"{step.statement}: not entailed by the board's clues"
        for step in witness.steps
        if not entails(admitted, step.establishes)
    )
    reveals = [step for step in witness.steps if step.is_reveal]
    findings.extend(
        f"{step.statement}: states the whole code, so it is a reveal rather "
        "than a deduction"
        for step in reveals
    )
    return tuple(findings)


__all__ = [
    "ASSURANCE",
    "AT",
    "USES",
    "check",
    "clue_elements",
    "code_space",
    "known_facts",
    "next_step",
    "survivor_space",
    "upcoming",
    "witness_for",
]
