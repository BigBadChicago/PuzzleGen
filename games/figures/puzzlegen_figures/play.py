"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` is the module's centre of gravity and the one function here
with a hard constraint on it. It must be a pure function of the puzzle and the
moves before it, because the session layer replays the entire ledger on every
submission and raises ``DeterminismError`` if a regrade disagrees with a
recorded outcome. Nothing here reads a clock, a random source or anything
outside its arguments, and nothing here caches across calls.

Grading is a set comparison, which is simpler than a word comparison and has
one decision in it worth naming: the order a player clicks three tiles in is
not part of the answer. A trio is three tiles. Comparing sequences would mark
the same three tiles right or wrong depending on which one the player noticed
first, which is not a rule anybody would defend out loud.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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

from .assemble import board_figures, board_tiles
from .descriptor import TRIO
from .trio import ATTRIBUTES, describe
from .verify import why_not

#: Wrong trios a player gets before the board closes.
#:
#: Five. A nine-tile board holds eighty-four triples, so guessing is not a
#: strategy five tries rewards, and the task is perceptual rather than
#: deductive: a player who has seen the pattern submits it, and a player who
#: has not needs room to test the two or three triples that look promising.
MAX_MISTAKES = 5

#: Points.
#:
#: Flat awards rather than a curve, because a score a player cannot predict
#: is a score they cannot feel they earned. The first-try bonus is the one
#: shaped award: seeing the trio without testing a wrong one is the thing
#: this format is for.
POINTS_FOR_TRIO = 300
POINTS_FIRST_TRY = 150
PENALTY_PER_MISTAKE = 40
PENALTY_PER_HINT = 60

#: How much of the award difficulty can add.
DIFFICULTY_BONUS = 100


def empty_state(puzzle: Puzzle) -> dict[str, Any]:
    """What a board looks like before the first submission."""
    del puzzle  # The empty state is the same shape for every board.
    return {
        "submitted": [],
        "mistakes": 0,
        "revealed": [],
        "solved": False,
        "complete": False,
    }


def _current(puzzle: Puzzle, state: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(state) if state else empty_state(puzzle)
    current.setdefault("submitted", [])
    current.setdefault("mistakes", 0)
    current.setdefault("revealed", [])
    current.setdefault("solved", False)
    current.setdefault("complete", False)
    return current


def answer_of(puzzle: Puzzle) -> tuple[str, ...]:
    return tuple(sorted(puzzle.solution["trio"]))


def _submitted(puzzle: Puzzle, payload: Mapping[str, Any]) -> tuple[str, ...]:
    """The three tiles a submission names, validated against the board.

    A submission naming a tile the board does not hold, or naming two tiles
    once each and a third twice, is a malformed move rather than a wrong
    answer: grading it as wrong would charge the player a mistake for a
    client's bug.
    """
    chosen = payload.get("tiles")
    if not isinstance(chosen, (list, tuple)):
        raise ValueError("a submission must carry a list of tiles")
    tiles = tuple(str(tile) for tile in chosen)
    if len(set(tiles)) != TRIO:
        raise ValueError(f"a trio is {TRIO} distinct tiles, got {list(tiles)}")
    unknown = sorted(set(tiles) - set(board_tiles(puzzle)))
    if unknown:
        raise ValueError(f"these tiles are not on the board: {unknown}")
    return tuple(sorted(tiles))


def grade_move(
    puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
) -> MoveJudgement:
    """Rule on one submitted trio, given the state the ledger replayed to."""
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

    chosen = _submitted(puzzle, payload)
    submitted = [*current["submitted"], list(chosen)]

    if chosen == answer_of(puzzle):
        return MoveJudgement(
            correct=True,
            complete=True,
            solved=True,
            state={
                **current,
                "submitted": submitted,
                "solved": True,
                "complete": True,
                "revealed": [*current["revealed"], list(chosen)],
            },
            note="Correct. That is the trio",
        )

    mistakes = int(current["mistakes"]) + 1
    out_of_tries = mistakes >= MAX_MISTAKES
    updated = {
        **current,
        "submitted": submitted,
        "mistakes": mistakes,
        "complete": out_of_tries,
        # The trio is shown once the board closes, never before. A player who
        # ran out of tries is owed the answer; one with tries left is owed
        # nothing but the fact that this was not it.
        "revealed": (
            [*current["revealed"], list(answer_of(puzzle))]
            if out_of_tries
            else [list(seen) for seen in current["revealed"]]
        ),
    }
    return MoveJudgement(
        correct=False,
        complete=out_of_tries,
        solved=False,
        state=updated,
        note=_miss_note(puzzle, chosen, out_of_tries),
    )


def _miss_note(
    puzzle: Puzzle, chosen: Sequence[str], out_of_tries: bool
) -> str:
    """What a wrong trio is told, without naming any part of the answer.

    Naming the attribute the submission fails on is information about the
    submission rather than about the trio, and the player already holds the
    three tiles it is about: they can see that two are red and one is blue.
    Saying which attribute broke it saves them the re-reading and gives away
    nothing, which is the line a note has to stay on.
    """
    if out_of_tries:
        return "Out of tries. The trio is now shown"
    failing = why_not(puzzle, tuple(chosen))
    if len(failing) == 1:
        return f"Not a trio: {failing[0]} is neither all the same nor all different"
    if failing:
        return (
            "Not a trio: "
            + ", ".join(failing[:-1])
            + f" and {failing[-1]} each break the rule"
        )
    return "Not a trio"


def get_hint(puzzle: Puzzle, state: Mapping[str, Any], hints_used: int) -> Hint:
    """Help, in increasing order of how much it gives away.

    Never the trio. The first hint names an attribute the trio is all
    different on, which narrows nothing on its own: most boards have several
    triples matching that. The second names one tile of the trio, which leaves
    the player choosing two from the rest. The third names a second tile,
    which leaves one to find and is a real last hint rather than a reveal,
    because the third tile is then determined and the player still has to
    recognise which tile it is. A fourth would be the answer, so there is no
    fourth.
    """
    current = _current(puzzle, state)
    if current["complete"]:
        return Hint(text="This puzzle is already finished", cost=0, exhausted=True)

    trio = answer_of(puzzle)
    varying = sorted(puzzle.solution.get("varying", ()))
    figures = board_figures(puzzle)

    if hints_used == 0:
        if varying:
            return Hint(
                text=f"In the trio, {varying[0]} is all different",
                cost=1,
            )
        same = sorted(set(ATTRIBUTES) - set(varying))
        return Hint(text=f"In the trio, {same[0]} is all the same", cost=1)
    if hints_used == 1:
        return Hint(
            text=f"One of the three is the {describe(figures[trio[0]])}",
            reveals=(trio[0],),
            cost=1,
        )
    return Hint(
        text=(
            "Two of the three are the "
            f"{describe(figures[trio[0]])} and the {describe(figures[trio[1]])}"
        ),
        reveals=(trio[0], trio[1]),
        cost=2,
        exhausted=True,
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
        "trio": POINTS_FOR_TRIO if telemetry.completed else 0,
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

    Each tile carries its glyph, its spoken label and every attribute by name.
    The colour is a word here as well as whatever hue a client paints it,
    which is the whole of this game's claim to be operable without colour
    vision: two figures can differ only in colour, so a client given a hue and
    nothing else would be drawing a board some players cannot read.

    The answer appears only through ``state``, where ``grade_move`` puts it
    once the board has closed.
    """
    del locale  # One locale, and the tiles carry no language anyway.
    current = _current(puzzle, state)
    chosen = set(
        tile for submitted in current.get("submitted", ()) for tile in submitted
    )

    tiles = tuple(
        {
            "kind": "tile",
            "id": tile["id"],
            "glyph": tile["glyph"],
            "label": tile["label"],
            **{attribute: tile[attribute] for attribute in ATTRIBUTES},
            "state": "chosen" if tile["id"] in chosen else "idle",
        }
        for tile in puzzle.payload["tiles"]
    )

    revealed = [list(seen) for seen in current.get("revealed", ())]
    return PresentationModel(
        elements=tiles,
        layout="grid",
        labels={
            "instructions": puzzle.presentation.get("instructions", ""),
            # Present only after the board has closed, which is the only time
            # ``grade_move`` writes it.
            **({"trio": ", ".join(revealed[-1])} if revealed else {}),
        },
        state={
            "mistakes": current.get("mistakes", 0),
            "tries_left": max(0, MAX_MISTAKES - int(current.get("mistakes", 0))),
            "complete": current.get("complete", False),
            "solved": current.get("solved", False),
            "columns": puzzle.payload.get("columns"),
            "rows": puzzle.payload.get("rows"),
            "trio_size": TRIO,
            "attributes": list(ATTRIBUTES),
        },
    )


def create_share_artifact(
    puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
) -> ShareArtifact:
    """The shape of the attempt, and nothing about the trio.

    Built from the move ledger with payloads already stripped by the engine,
    so it can say a submission was wrong but never which three tiles it was.
    One row per submission, in the order they were made, which is what makes
    two people's grids comparable without either naming a figure.
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
        f"Found the trio in {telemetry.attempts} "
        + ("try" if telemetry.attempts == 1 else "tries")
        if telemetry.completed
        else f"Did not find it in {telemetry.attempts} tries"
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
            "varying": len(puzzle.solution.get("varying", ())),
        },
    )


__all__ = [
    "DIFFICULTY_BONUS",
    "MAX_MISTAKES",
    "PENALTY_PER_HINT",
    "PENALTY_PER_MISTAKE",
    "POINTS_FIRST_TRY",
    "POINTS_FOR_TRIO",
    "answer_of",
    "create_share_artifact",
    "empty_state",
    "get_hint",
    "grade_move",
    "render",
    "score",
]
