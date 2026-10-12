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

#: Wrong numbers a player gets before the board closes.
#:
#: Three, and fewer than the other games allow, because the board is smaller
#: than theirs. A five-tile board has five answers and one is right; five
#: tries would let a player exhaust it and a share would say nothing. Three is
#: enough to test a reading, be wrong, and think again.
MAX_MISTAKES = 3

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
    """Help, in increasing order of how much it gives away.

    Never the answer. The first hint says how many of the numbers share the
    property, which is a restatement of the rule and helps a player who has
    misread the board. The second names the property's kind without naming it:
    whether it is about divisibility, about a sequence, or about size. The
    third names the property outright, which leaves the player to apply it to
    every tile and find the one it misses -- real work on an obscure property,
    and a reveal on an easy one, which is why there is no fourth.
    """
    current = _current(puzzle, state)
    if current["complete"]:
        return Hint(text="This puzzle is already finished", cost=0, exhausted=True)

    board_size = int(puzzle.payload["board_size"])
    name = str(puzzle.solution.get("property", ""))

    if hints_used == 0:
        return Hint(
            text=f"{board_size - 1} of the {board_size} share it; one does not",
            cost=1,
        )
    if hints_used == 1:
        return Hint(text=f"It is about {_kind_of(name)}", cost=1)
    return Hint(
        text=f"All but one are {name}",
        cost=2,
        exhausted=True,
    )


#: What kind of thing a property is, for the second hint.
#:
#: A coarse grouping rather than a per-property sentence, because the hint's
#: job is to narrow the search without naming the property: "it is about
#: divisibility" rules out the sequences and leaves four candidates. Keyed by
#: substring so a new category of the same kind needs no entry here, with a
#: fallback that says the honest thing rather than guessing.
_KINDS = (
    ("multiple", "divisibility"),
    ("even", "divisibility"),
    ("odd", "divisibility"),
    ("prime", "how many factors it has"),
    ("square", "a shape you can make from it"),
    ("triangular", "a shape you can make from it"),
    ("Fibonacci", "a sequence"),
    ("digit", "how it is written"),
)


def _kind_of(name: str) -> str:
    for needle, kind in _KINDS:
        if needle.lower() in name.lower():
            return kind
    return "something arithmetic"


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
