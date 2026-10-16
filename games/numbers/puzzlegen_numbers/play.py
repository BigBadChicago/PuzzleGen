"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` carries the hard constraint: it must be a pure function of the
puzzle and the moves before it, because the session layer replays the whole
ledger on every submission and raises if a regrade disagrees. Nothing here
reads a clock, a random source or anything outside its arguments.

Grading is the simplest in the repository -- one tile against one tile -- and
the work in this module is almost all in what a wrong answer is told. A player
who chose the wrong number chose it for a reason, and the reason is usually a
property that tile really does share with the rest of the board. Saying which
one is information about their choice rather than about the answer, and it is
what turns a miss into a step.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from puzzlegen.engine.plugin import (
    Hint,
    MoveJudgement,
    PresentationModel,
    Puzzle,
    Score,
    SessionTelemetry,
    ShareArtifact,
)

from .assemble import board_tiles, board_values
from .verify import near_miss_for
from .witness import upcoming

#: Wrong numbers a player gets before the board closes.
#:
#: Three, and fewer than the other games allow, because the board is smaller
#: than theirs. A five-tile board has five answers and one is right; five
#: tries would let a player exhaust it and a share would say nothing. Three is
#: enough to test a reading, be wrong, and think again.
MAX_MISTAKES = 3

#: Hints one board will sell, however many true things are left to say.
#:
#: A policy, not a deduction. The derived ladder on a five-tile board has
#: three rungs before the next one would name the answer, and on a seven-tile
#: board it has five; three is what the authored ladder sold and what this
#: game's scoring is tuned against, so the cap keeps the price of help where
#: it was while the help itself changed.
MAX_HINTS = 3

#: Points.
#:
#: Flat awards rather than a curve, because a score a player cannot predict is
#: a score they cannot feel they earned. The first-try bonus is the one shaped
#: award: seeing the property without testing a wrong number is what this
#: format is for.
POINTS_FOR_ANSWER = 300
POINTS_FIRST_TRY = 150
PENALTY_PER_MISTAKE = 60
PENALTY_PER_HINT = 60

#: How much of the award difficulty can add.
DIFFICULTY_BONUS = 100


def empty_state(puzzle: Puzzle) -> dict[str, Any]:
    """What a board looks like before the first guess."""
    del puzzle  # The empty state is the same shape for every board.
    return {
        "guesses": [],
        "mistakes": 0,
        "revealed": [],
        "solved": False,
        "complete": False,
    }


def _current(puzzle: Puzzle, state: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(state) if state else empty_state(puzzle)
    current.setdefault("guesses", [])
    current.setdefault("mistakes", 0)
    current.setdefault("revealed", [])
    current.setdefault("solved", False)
    current.setdefault("complete", False)
    return current


def answer_of(puzzle: Puzzle) -> str:
    return str(puzzle.solution["answer"])


def _guess(puzzle: Puzzle, payload: Mapping[str, Any]) -> str:
    """The tile a submission names, validated against the board.

    A submission naming a tile the board does not hold is a malformed move
    rather than a wrong answer: grading it as wrong would charge the player a
    mistake for a client's bug.
    """
    tile = payload.get("tile")
    if not isinstance(tile, str) or not tile:
        raise ValueError("a submission must carry a tile")
    if tile not in board_tiles(puzzle):
        raise ValueError(f"the tile {tile!r} is not on the board")
    return tile


def grade_move(
    puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
) -> MoveJudgement:
    """Rule on one chosen number, given the state the ledger replayed to."""
    current = _current(puzzle, state)
    if current["complete"]:
        # ``solved`` describes this move, not the puzzle: a move arriving
        # after the end solved nothing.
        return MoveJudgement(
            correct=False,
            complete=True,
            solved=False,
            state=current,
            note="This puzzle is already finished",
        )

    tile = _guess(puzzle, payload)
    guesses = [*current["guesses"], tile]

    if tile == answer_of(puzzle):
        return MoveJudgement(
            correct=True,
            complete=True,
            solved=True,
            state={
                **current,
                "guesses": guesses,
                "solved": True,
                "complete": True,
                "revealed": [*current["revealed"], tile],
            },
            note=f"Correct. The others are all {puzzle.solution['property']}",
        )

    mistakes = int(current["mistakes"]) + 1
    out_of_tries = mistakes >= MAX_MISTAKES
    updated = {
        **current,
        "guesses": guesses,
        "mistakes": mistakes,
        "complete": out_of_tries,
        # The answer is shown once the board closes, never before. A player
        # who ran out of tries is owed it; one with tries left is owed nothing
        # but the fact that this was not it.
        "revealed": (
            [*current["revealed"], answer_of(puzzle)]
            if out_of_tries
            else list(current["revealed"])
        ),
    }
    return MoveJudgement(
        correct=False,
        complete=out_of_tries,
        solved=False,
        state=updated,
        note=_miss_note(puzzle, tile, out_of_tries),
    )


def _miss_note(puzzle: Puzzle, tile: str, out_of_tries: bool) -> str:
    """What a wrong guess is told, without narrowing the answer.

    The board has one reading, which means there is no property only the
    chosen tile lacks -- that would be a second reading. So nothing can be
    said that singles the tile out, and the two things that can be said are
    both about the choice rather than the answer.

    The better one is a near reading the tile is an outsider of: a property
    that all but exactly two tiles have. That is almost certainly why the
    player chose it, it is true, and it is safe precisely because a property
    with two outsiders is not the board's answer. Naming the board's own
    property instead would be the answer in words, which is why the shared
    properties are not used here at all.

    When there is no near reading, the note says the structural truth and
    stops. Vague, and the alternative is a sentence that gives the puzzle
    away.
    """
    if out_of_tries:
        values = board_values(puzzle)
        return f"Out of tries. It was {values[answer_of(puzzle)]}"
    near = near_miss_for(puzzle, tile)
    if near:
        return (
            f"Not it. That one is not {near}, but it is not the only one"
        )
    return "Not it. Everything that one is, another one is too"


def get_hint(puzzle: Puzzle, state: Mapping[str, Any], hints_used: int) -> Hint:
    """The cheapest deduction the board forces and the player does not hold.

    Derived, not authored. ``witness.upcoming`` walks this board's witness --
    every statement about which tile is the odd one out, ordered by how much
    of the tile row it removes -- skips what the player's own wrong guesses
    already established, and re-costs what is left against what they hold.

    Every rung costs one. Not a shrug: an exclusion removes one tile, so the
    rungs of a five-tile board run from a fifth to a third of what remains,
    and a two-tier price would be a fiction dressed as a measurement. The
    board charges per hint in ``score`` instead, where the number is about the
    award and not about the statement.

    What this replaced is worth recording, because the measurement is the
    argument. The old first hint restated the rule and removed nothing; the
    old second and third named the property's kind and then the property, and
    on a board with one reading each of those narrows the tiles to the one it
    distinguishes -- both reveals. The ladder was one rung that said nothing
    followed by two that said everything, and nobody could see that while the
    cost of a sentence was a number somebody chose.
    """
    current = _current(puzzle, state)
    if current["complete"]:
        return Hint(text="This puzzle is already finished", cost=0, exhausted=True)
    if hints_used >= MAX_HINTS:
        return Hint(
            text=f"This board offers {MAX_HINTS} hints, and you have had them",
            cost=0,
            exhausted=True,
        )

    # Two rungs from one walk: the one to hand over, and whether anything
    # follows it.
    offered = upcoming(puzzle, current, hints_used, count=2)
    if not offered:
        return Hint(
            text="There is nothing left to tell you that would not be the answer",
            cost=0,
            exhausted=True,
        )
    step = offered[0]
    return Hint(
        text=step.statement,
        reveals=step.reveals,
        cost=1,
        exhausted=hints_used + 1 >= MAX_HINTS or len(offered) < 2,
    )


def score(puzzle: Puzzle, telemetry: SessionTelemetry) -> Score:
    """Points, from measured signals only.

    Every number read here was computed by the engine from the ledger, so a
    score can be recomputed later from stored telemetry and a game cannot
    invent a flattering signal.
    """
    del puzzle  # The award depends on the attempt, not on the board.
    first_try = telemetry.completed and telemetry.mistakes == 0
    breakdown = {
        "answer": POINTS_FOR_ANSWER if telemetry.completed else 0,
        "first_try": POINTS_FIRST_TRY if first_try else 0,
        "difficulty": (
            int(DIFFICULTY_BONUS * telemetry.difficulty_score)
            if telemetry.completed
            else 0
        ),
        "mistakes": -telemetry.mistakes * PENALTY_PER_MISTAKE,
        "hints": -telemetry.hints_used * PENALTY_PER_HINT,
    }
    return Score(
        points=max(0, sum(breakdown.values())),
        breakdown=breakdown,
        detail=(
            "found it on the first try"
            if first_try
            else ("found it" if telemetry.completed else "not found")
        ),
    )


def render(
    puzzle: Puzzle, state: Mapping[str, Any], locale: str
) -> PresentationModel:
    """A data description of the board. Never markup, never the answer.

    The property appears nowhere: not in the elements, not in the labels, and
    not in the state until the board has closed, where ``grade_move`` puts the
    answer tile. A render that named it would be printing the answer in words
    to every client that asked for one.
    """
    del locale  # One locale; the tiles are digits and carry no language.
    current = _current(puzzle, state)
    guessed = set(current.get("guesses", ()))

    tiles = tuple(
        {
            "kind": "tile",
            "id": tile["id"],
            "glyph": tile["glyph"],
            "label": tile["label"],
            "value": tile["value"],
            "state": "chosen" if tile["id"] in guessed else "idle",
        }
        for tile in puzzle.payload["tiles"]
    )

    revealed = list(current.get("revealed", ()))
    values = board_values(puzzle)
    return PresentationModel(
        elements=tiles,
        layout="row",
        labels={
            "instructions": puzzle.presentation.get("instructions", ""),
            # Present only after the board has closed, which is the only time
            # ``grade_move`` writes a reveal. The property goes with it: once
            # the puzzle is over, saying what the others shared is the whole
            # of the explanation a player is owed.
            **(
                {
                    "answer": str(values.get(revealed[-1], "")),
                    "property": str(puzzle.solution.get("property", "")),
                }
                if revealed
                else {}
            ),
        },
        state={
            "mistakes": current.get("mistakes", 0),
            "tries_left": max(0, MAX_MISTAKES - int(current.get("mistakes", 0))),
            "complete": current.get("complete", False),
            "solved": current.get("solved", False),
            "board_size": puzzle.payload.get("board_size"),
            "ordered": puzzle.payload.get("ordered"),
        },
    )


def create_share_artifact(
    puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
) -> ShareArtifact:
    """The shape of the attempt, and nothing about the numbers.

    Built from the move ledger with payloads already stripped by the engine,
    so it can say a guess was wrong but never which number it was. There is
    deliberately nothing about the property either: naming it would give the
    answer to a board the reader has not played.
    """
    submits = [
        event
        for event in telemetry.events
        if event.get("kind") in ("SUBMIT", "HINT")
    ]
    rows: list[tuple[str, ...]] = []
    for event in submits:
        if event.get("kind") == "HINT":
            rows.append(("hint",))
        elif event.get("outcome") == "CORRECT":
            rows.append(("solved",))
        else:
            rows.append(("miss",))
    if not rows:
        rows = [("miss",)] * max(0, telemetry.attempts)

    outcome = "solved" if telemetry.completed else "unsolved"
    headline = (
        f"Spotted it in {telemetry.attempts} "
        + ("try" if telemetry.attempts == 1 else "tries")
        if telemetry.completed
        else f"Did not spot it in {telemetry.attempts} tries"
    )
    return ShareArtifact(
        game_id=puzzle.game_id,
        day_key=day_key,
        outcome=outcome,
        tokens=tuple(rows),
        headline=headline,
        detail={
            "mistakes": telemetry.mistakes,
            "hints": telemetry.hints_used,
            "board_size": puzzle.payload["board_size"],
        },
    )


__all__ = [
    "DIFFICULTY_BONUS",
    "MAX_HINTS",
    "MAX_MISTAKES",
    "PENALTY_PER_HINT",
    "PENALTY_PER_MISTAKE",
    "POINTS_FIRST_TRY",
    "POINTS_FOR_ANSWER",
    "answer_of",
    "create_share_artifact",
    "empty_state",
    "get_hint",
    "grade_move",
    "render",
    "score",
]
