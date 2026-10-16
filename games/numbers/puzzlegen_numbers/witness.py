"""The elimination this game already does, emitted as a deduction witness.

This game's answer is one tile, and ``verify`` already decides which tiles
could legitimately be it: a tile is a reading iff some property every other
tile carries is one it lacks. A board whose contract holds has exactly one
reading. The elimination that establishes it was being thrown away, and the
help was three authored sentences in its place.

Measuring the authored ladder is what makes the case for replacing it, so it
is worth recording what the measurement said. Counted over the board's tiles
-- the answers its vocabulary admits before the rules are read -- the three
authored hints were:

* "four of the five share it; one does not" removes nothing at all. It
  restates the rule the instructions already state, so its cost is zero and
  it is a vacuous rung rather than a cheap one.
* "it is about divisibility" leaves one tile. On a board with one reading,
  naming the kind of property narrows the tile set to the tiles distinguished
  by a property of that kind, and there is exactly one.
* "all but one are prime" leaves one tile, for the same reason, which the
  authored docstring half-admitted by calling it "a reveal on an easy one".

So the ladder a player was offered was one statement that said nothing
followed by two that said everything. Neither fact was discoverable while the
cost of a sentence was a number somebody chose.

**What replaces it is tile exclusion, cheapest first.** "The 35 is not the
one" removes a fifth of a five-tile board, and the ladder runs until two tiles
remain, because the next exclusion would name the answer. That is less
instructive than naming the kind of property and it is the honest trade: the
derived rungs are checkable, they are entailed by the board, and none of them
is the answer.

**The cost is counted over the tiles, not over the readings.** A board with
one reading admits one answer once its memberships are read, so narrowing
measured over the readings is zero for every true statement. The tile space is
what gives the exclusions their price.

**A statement is admissible only if every reading satisfies it.** Each
candidate here is built from the stated answer and so is true of it; the
entailment check against the readings is what makes a hint something the
player could have worked out, and it is also what would catch a board with two
readings offering a statement that holds of only one of them.

**The assurance is one level below digit_lock's, and says so.** Readings are
derived over the properties the day's content offered, which is the same
bounded vocabulary this game's verifier declares ``SOUND_INCOMPLETE`` over. An
exclusion is therefore entailed by the board as searched rather than by
arithmetic, so :data:`ASSURANCE` is ``ANSWER_VALID`` and a caller that needs
more is refused rather than quietly served.
"""

from __future__ import annotations

from collections.abc import Mapping
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

from .assemble import board_memberships, board_tiles
from .descriptor import GAME_ID
from .properties import readings_of, vocabulary_of

#: This game's one predicate: whether a named tile is the odd one out.
#:
#: Namespaced, like every other game's. "Is the odd one out" is this game's
#: claim about its own board, and another game's similar wording is another
#: game's rules.
#:
#: One predicate rather than several, because the answer is one tile and
#: there is nothing else about it to state. The properties are the board's
#: *reasons*, and a statement about a reason is not a statement about the
#: answer -- which is what the measurement in this module's docstring found
#: when the authored hints about the property turned out to be reveals.
IS_ODD_ONE_OUT = f"{GAME_ID}/is_odd_one_out"

#: The assurance a witness over this game's boards carries.
#:
#: ``ANSWER_VALID``, which is weaker than digit_lock's and is the honest
#: level. The statements here are true of the stated answer and their costs
#: are exact counts over the tiles. What they are not is proved against every
#: possible reading, because this game declares ``SOUND_INCOMPLETE``: the
#: readings are derived over the properties the day's content offered, and a
#: solver could defend a property no taxonomy in the build holds. "The 12 is
#: not the one" is entailed by the vocabulary the board searched, not by
#: arithmetic, and a witness that said ``UNIQUENESS_PROVED`` would be
#: borrowing a word the game's own verifier refuses.
#:
#: This is what the declared level is for. A caller that needs a hint proved
#: against every reading asks for a floor and is refused this witness; a
#: caller offering help takes it, because a hint derived over the board's own
#: vocabulary is exactly as good as the board is.
ASSURANCE = AssuranceLevel.ANSWER_VALID


def _admits(tile: str, fact: Fact) -> bool:
    """Whether one tile is still possible given one statement."""
    if fact.predicate == IS_ODD_ONE_OUT:
        return (fact.ref == tile) if fact.value else (fact.ref != tile)
    return True


def tile_space(puzzle: Puzzle) -> EnumeratedSpace:
    """Every tile the board could name, before its memberships are read.

    The space costs are counted over. Five to seven answers, which is small
    enough that every figure in a witness over this game is an exact count.
    """
    return EnumeratedSpace(
        answers=board_tiles(puzzle),
        admits=_admits,
        predicates=frozenset({IS_ODD_ONE_OUT}),
    )


def reading_space(puzzle: Puzzle) -> EnumeratedSpace:
    """Every tile the board's memberships actually admit as the odd one out.

    The game's own derivation, not the certifier's: the certifier exists to
    disagree with this one, and a witness built from it would be the game
    checking itself against the thing meant to check it.
    """
    memberships = board_memberships(puzzle)
    readings = readings_of(memberships, vocabulary_of(memberships))
    return EnumeratedSpace(
        answers=tuple(tile for tile in board_tiles(puzzle) if tile in readings),
        admits=_admits,
        predicates=frozenset({IS_ODD_ONE_OUT}),
    )


def tile_elements(puzzle: Puzzle) -> tuple[str, ...]:
    """The render element ids a deduction rests on.

    Every tile, because a statement entailed by the board is read off the
    whole board: what makes one tile the odd one out is what every other tile
    has. Naming a smaller set would be naming a reason, and a reason is the
    one thing this game's hints may not give away. What a statement *speaks
    about* is narrower and is carried separately, on each step's ``reveals``.
    """
    return board_tiles(puzzle)


def _glyph(puzzle: Puzzle) -> dict[str, str]:
    return {
        str(tile["id"]): str(tile.get("glyph", tile["id"]))
        for tile in puzzle.payload["tiles"]
    }


def _candidates(puzzle: Puzzle, space: EnumeratedSpace) -> list[Deduction]:
    """Every statement this game can make about its own answer.

    Built from the stated answer, so each is true of it; whether each is
    *deducible* is settled against the readings by :func:`witness_for`.
    """
    answer = str(puzzle.solution["answer"])
    glyph = _glyph(puzzle)
    tiles = board_tiles(puzzle)

    offered = [
        deduction_for(
            f"The {glyph[tile]} is not the one",
            [Fact(predicate=IS_ODD_ONE_OUT, ref=tile, value=False)],
            space,
        )
        for tile in tiles
        if tile != answer
    ]
    # No "it is the 51" candidate, deliberately. The statement is true and
    # the board forces it, so it would be admissible; it is also the answer,
    # and a witness that holds the answer makes the no-reveal rule a
    # negotiation with the hint service rather than a property of the
    # witness. It is left out here and `check` treats its presence as a
    # finding, which is the same arrangement digit_lock's witness has.
    #
    # Everything but two tiles ruled out at once. A real deduction the board
    # forces, and measured, one the hint ladder never reaches: it is the
    # conjunction of the single exclusions, so every one of them is cheaper
    # and the walk takes those first, by which point this says nothing. It is
    # here because a witness describes the board rather than the ladder --
    # what a cross reference measures is the deductions a board still
    # demands, and "it is one of these two" is one of them.
    rest = [tile for tile in tiles if tile != answer]
    if len(rest) > 1:
        kept = rest[0]
        offered.append(
            deduction_for(
                f"It is either the {glyph[answer]} or the {glyph[kept]}",
                [
                    Fact(predicate=IS_ODD_ONE_OUT, ref=tile, value=False)
                    for tile in rest[1:]
                ],
                space,
            )
        )
    return offered


def witness_for(puzzle: Puzzle) -> DeductionWitness:
    """This board's deductions, cheapest first.

    A statement no reading contradicts is a statement the board forces. On a
    board whose contract holds there is one reading and the check passes for
    every true statement; on a board with two it drops the statements that
    hold of the stated answer and not of the other, which is exactly the set a
    player could not have derived.
    """
    space = tile_space(puzzle)
    admitted = reading_space(puzzle)
    elements = tile_elements(puzzle)
    deducible = [
        # Justified by the whole board and speaking about the tiles it names.
        # The two differ here and the difference matters: a client highlights
        # ``reveals``, and a statement that named every tile would highlight
        # the answer's tile along with the rest.
        replace(
            step,
            justification=elements,
            reveals=tuple(fact.ref for fact in step.establishes),
        )
        for step in _candidates(puzzle, space)
        if entails(admitted, step.establishes)
    ]
    return build_witness(
        game_id=GAME_ID,
        assurance=ASSURANCE,
        space=space,
        candidates=deducible,
        detail=(
            f"{len(admitted.answers)} of {len(space.answers)} tiles are "
            f"readings of this board"
        ),
    )


def known_facts(puzzle: Puzzle, state: Mapping[str, Any]) -> tuple[Fact, ...]:
    """What the player's own wrong guesses have already told them.

    Exact on this board, unlike on a board whose verdicts are counts: a
    submission names one tile and is told it is wrong, so the player holds
    "that tile is not the one" and nothing weaker. Read from the replayed
    state rather than from a store, so a hint stays a pure function of the
    puzzle, the moves before it and the hint count.
    """
    answer = str(puzzle.solution["answer"])
    tiles = set(board_tiles(puzzle))
    wrong = [
        str(guess)
        for guess in state.get("guesses", ()) or ()
        if str(guess) in tiles and str(guess) != answer
    ]
    return tuple(
        dict.fromkeys(
            Fact(predicate=IS_ODD_ONE_OUT, ref=tile, value=False) for tile in wrong
        )
    )


def upcoming(
    puzzle: Puzzle,
    state: Mapping[str, Any],
    hints_used: int,
    count: int = 1,
) -> tuple[Deduction, ...]:
    """The next ``count`` deductions to offer, costed as the player will feel.

    A marginal walk rather than a read off the witness: the witness is ordered
    by each statement's narrowing of the whole tile row, and that stops
    describing a player who has already ruled two tiles out. On this game the
    difference is sharp, because every exclusion costs the same absolutely and
    they get steadily dearer as they accumulate -- a fifth of five tiles, then
    a quarter of four, then a third of three.
    """
    walked = walk(
        witness_for(puzzle),
        tile_space(puzzle),
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
    a hint rests on: every step is entailed by the board, and no step names the
    answer. Not called from ``verify``, deliberately -- verification already
    derives the readings, and a second derivation per candidate statement would
    double the cost of every generation to recheck arithmetic the same pass
    established. It is a check for the suite and a diagnostic over real days.
    """
    witness = witness_for(puzzle)
    findings = list(witness.holds_up(tile_space(puzzle)))
    admitted = reading_space(puzzle)
    findings.extend(
        f"{step.statement}: not entailed by the board"
        for step in witness.steps
        if not entails(admitted, step.establishes)
    )
    findings.extend(
        f"{step.statement}: names the answer, so it is a reveal rather than a "
        "deduction"
        for step in witness.steps
        if step.is_reveal
    )
    return tuple(findings)


__all__ = [
    "ASSURANCE",
    "IS_ODD_ONE_OUT",
    "check",
    "known_facts",
    "next_step",
    "reading_space",
    "tile_elements",
    "tile_space",
    "upcoming",
    "witness_for",
]
