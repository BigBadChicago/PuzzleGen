"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` is the module's centre of gravity and the one function here
with a hard constraint on it. It must be a pure function of the puzzle and the
moves before it, because the session layer replays the entire ledger on every
submission and raises ``DeterminismError`` if a regrade disagrees with a
recorded outcome. Nothing here reads a clock, a random source or anything
outside its arguments, and nothing here caches across calls.

Grading is a word comparison, which is simpler than a grouping game's and has
one subtlety worth naming: the comparison is between letter forms, not between
strings. A player who types ``Fish`` or ``fishes`` is not typing the same word
as the board's answer in the first case only by accident of case, so both go
through the same surface policy the board was built under. That is the whole
reason the policy is a constant rather than an argument: a board built under
one set of letter rules and graded under another would mark a correct answer
wrong.
"""

from __future__ import annotations

from collections import Counter
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

from .assemble import board_marks, unmarked_tiles
from .marks import letters_of, spells
from .verify import readings_of, memberships_of

#: Wrong guesses a player gets before the board closes.
#:
#: Five. The answer is one word rather than a sequence of moves, so there is
#: no partial progress to protect and no reason to end a session on the first
#: miss; five is enough to work through the obvious anagram and the two words
#: it turns out not to be.
MAX_MISTAKES = 5

#: Points.
#:
#: Flat awards rather than a curve, because a score a player cannot predict is
#: a score they cannot feel they earned. The first-guess bonus is the one
#: shaped award: naming the category from the letters without a wrong guess is
#: the thing this format is for.
POINTS_FOR_ANSWER = 300
POINTS_FIRST_GUESS = 150
PENALTY_PER_MISTAKE = 40
PENALTY_PER_HINT = 60

#: How much of the award difficulty can add.
DIFFICULTY_BONUS = 100


def empty_state(puzzle: Puzzle) -> dict[str, Any]:
    """What a board looks like before the first guess."""
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


def answer_letters(puzzle: Puzzle) -> str:
    return str(puzzle.solution["letters"])


def blank_letters(puzzle: Puzzle) -> tuple[str, ...]:
    """The letters no tile carries, which is what the solver supplies.

    Taken from the record of forced and manufactured blanks together, because
    a player cannot tell them apart and a hint that distinguished them would
    be telling them something about the generator rather than about the
    answer.
    """
    solution = puzzle.solution
    return tuple(
        sorted(
            [*solution.get("forced_blanks", ()), *solution.get("manufactured_blanks", ())]
        )
    )


def _guess(payload: Mapping[str, Any]) -> str:
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("a submission must carry an answer")
    return answer


def grade_move(
    puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
) -> MoveJudgement:
    """Rule on one guess, given the state the ledger replayed to."""
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

    guess = letters_of(_guess(payload))
    answer = answer_letters(puzzle)
    guesses = [*current["guesses"], guess]

    if guess == answer:
        return MoveJudgement(
            correct=True,
            complete=True,
            solved=True,
            state={
                **current,
                "guesses": guesses,
                "solved": True,
                "complete": True,
                "revealed": [*current["revealed"], puzzle.solution["answer"]],
            },
            note=f"Correct: {puzzle.solution['answer']}",
        )

    mistakes = int(current["mistakes"]) + 1
    out_of_guesses = mistakes >= MAX_MISTAKES
    updated = {
        **current,
        "guesses": guesses,
        "mistakes": mistakes,
        "complete": out_of_guesses,
        # The answer is shown once the board closes, never before. A player
        # who ran out of guesses is owed the word; one who has guesses left is
        # owed nothing but the fact that this was not it.
        "revealed": (
            [*current["revealed"], puzzle.solution["answer"]]
            if out_of_guesses
            else list(current["revealed"])
        ),
    }
    return MoveJudgement(
        correct=False,
        complete=out_of_guesses,
        solved=False,
        state=updated,
        note=_miss_note(puzzle, guess, out_of_guesses),
    )


def _miss_note(puzzle: Puzzle, guess: str, out_of_guesses: bool) -> str:
    """What a wrong guess is told, without naming any part of the answer.

    Three different wrong answers deserve three different sentences, and none
    of them may narrow the word down. A guess of the wrong length and a guess
    that ignores the marks are both mistakes about the board rather than about
    the category, and saying so costs nothing: the board already shows how
    many slots there are and which letters are on it.
    """
    if out_of_guesses:
        return f"Out of guesses. It was {puzzle.solution['answer']}"
    length = int(puzzle.payload["answer_length"])
    if len(letters_of(guess)) != length:
        return f"Not it. The answer has {length} letters"
    # The multiset of marked letters, not the tile-to-letter map: two tiles
    # can carry the same letter, and the solver's arithmetic is about letters.
    shown = Counter(board_marks(puzzle).values())
    if not spells(guess, shown, int(puzzle.payload["blanks"])):
        return "Not it. That word does not use every letter the board marks"
    return "Not it"


def get_hint(puzzle: Puzzle, state: Mapping[str, Any], hints_used: int) -> Hint:
    """Help, in increasing order of how much it gives away.

    Never the answer. The first hint names one letter the board withholds, the
    second names the letter the answer starts with, and the third names every
    withheld letter -- which leaves the player holding the whole multiset and
    still having to arrange it, so it is a real last hint rather than a
    reveal. A fourth would be the word, so there is no fourth.
    """
    current = _current(puzzle, state)
    if current["complete"]:
        return Hint(text="This puzzle is already finished", cost=0, exhausted=True)

    answer = answer_letters(puzzle)
    blanks = blank_letters(puzzle)

    if hints_used == 0 and blanks:
        return Hint(
            text=f"One letter no tile gives you is {blanks[0]!r}",
            cost=1,
        )
    if hints_used <= 1:
        return Hint(text=f"The answer starts with {answer[0]!r}", cost=1)
    return Hint(
        text=(
            "The letters no tile gives you are "
            + ", ".join(repr(letter) for letter in blanks)
        ),
        cost=2,
        exhausted=True,
    )


def score(puzzle: Puzzle, telemetry: SessionTelemetry) -> Score:
    """Points, from measured signals only.

    Every number read here was computed by the engine from the ledger, so a
    score can be recomputed later from stored telemetry and a game cannot
    invent a flattering signal.
    """
    first_guess = telemetry.completed and telemetry.mistakes == 0
    breakdown = {
        "answer": POINTS_FOR_ANSWER if telemetry.completed else 0,
        "first_guess": POINTS_FIRST_GUESS if first_guess else 0,
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
            "answered on the first guess"
            if first_guess
            else ("answered" if telemetry.completed else "unanswered")
        ),
    )


def render(
    puzzle: Puzzle, state: Mapping[str, Any], locale: str
) -> PresentationModel:
    """A data description of the board. Never markup, never the answer.

    The answer appears only through ``state``, where ``grade_move`` puts it
    once the board has closed. Reading it from the solution here would put the
    word in front of every client that asked for a render, which is the one
    thing this function must not do.
    """
    current = _current(puzzle, state)
    marks = board_marks(puzzle)

    tiles = tuple(
        {
            "kind": "tile",
            "id": tile["id"],
            "label": tile["label"],
            "mark": tile.get("mark"),
            "state": "marked" if tile.get("mark") else "unmarked",
        }
        for tile in puzzle.payload["tiles"]
    )
    slots = tuple(
        {
            "kind": "slot",
            "id": f"slot:{index}",
            "state": "blank",
        }
        for index in range(int(puzzle.payload["blanks"]))
    )

    revealed = list(current.get("revealed", ()))
    return PresentationModel(
        elements=(*tiles, *slots),
        layout="grid",
        labels={
            "instructions": puzzle.presentation.get("instructions", ""),
            # Present only after the board has closed, which is the only time
            # ``grade_move`` writes it.
            **({"answer": revealed[-1]} if revealed else {}),
        },
        state={
            "mistakes": current.get("mistakes", 0),
            "guesses_left": max(0, MAX_MISTAKES - int(current.get("mistakes", 0))),
            "complete": current.get("complete", False),
            "solved": current.get("solved", False),
            "columns": puzzle.payload.get("columns"),
            "answer_length": puzzle.payload.get("answer_length"),
            "blanks": puzzle.payload.get("blanks"),
            "marked_tiles": len(marks),
        },
    )


def create_share_artifact(
    puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
) -> ShareArtifact:
    """The shape of the attempt, and nothing about the answer.

    Built from the move ledger with payloads already stripped by the engine,
    so it can say a guess was wrong but never which word it was. One row per
    submission, in the order they were made, which is what makes two people's
    grids comparable without either naming the category.
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
        f"Spelled it in {telemetry.attempts} "
        + ("guess" if telemetry.attempts == 1 else "guesses")
        if telemetry.completed
        else f"Did not get it in {telemetry.attempts} guesses"
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
            "blanks": puzzle.payload["blanks"],
            "answer_length": puzzle.payload["answer_length"],
        },
    )


def readings(puzzle: Puzzle) -> frozenset[str]:
    """Categories the whole board belongs to, for tests and diagnostics."""
    return readings_of(memberships_of(puzzle))


__all__ = [
    "DIFFICULTY_BONUS",
    "MAX_MISTAKES",
    "PENALTY_PER_HINT",
    "PENALTY_PER_MISTAKE",
    "POINTS_FIRST_GUESS",
    "POINTS_FOR_ANSWER",
    "answer_letters",
    "blank_letters",
    "create_share_artifact",
    "empty_state",
    "get_hint",
    "grade_move",
    "readings",
    "render",
    "score",
]
