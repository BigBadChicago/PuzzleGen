"""Playing the board: grading, hints, scoring, rendering, sharing.

``grade_move`` must be a pure function of the puzzle and the moves before it,
because the session layer replays the whole ledger on every submission and
raises if a regrade disagrees. Nothing here reads a clock or a random source.

The decision this module makes that the other games do not have to is what a
wrong submission is told. This game already has a language for that -- a guess
and its two counts is what every clue on the board is -- so a wrong guess is
graded with exactly that, and the board gains a row. A note saying "two right,
one in the right place" is not a leak in any new way: the board is made of such
statements, and a solver who submits a guess has bought the same information a
clue would have given them. What it costs is a try, which is the whole trade.
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

from .assemble import answer_code, board_digits, board_facts, board_guesses
from .lock import CodeError, check_code, verdict
from .witness import next_step

#: Wrong codes a player gets before the lock stays shut.
#:
#: Four. Every wrong guess returns real information, so a generous allowance
#: would turn deduction into search: with eight tries a player can brute force
#: a three-digit board from the verdicts alone. Four is enough to be wrong
#: twice while reasoning and still finish.
MAX_MISTAKES = 4

#: Points.
#:
#: Flat awards rather than a curve, because a score a player cannot predict is
#: a score they cannot feel they earned. The first-try bonus is the largest in
#: the repository relative to the base, because on this board a first-try
#: answer means the deduction was completed before anything was submitted,
#: which is the whole of what the format asks for.
POINTS_FOR_CODE = 300
POINTS_FIRST_TRY = 200
PENALTY_PER_MISTAKE = 50
PENALTY_PER_HINT = 70

#: How much of the award difficulty can add.
DIFFICULTY_BONUS = 100


def empty_state(puzzle: Puzzle) -> dict[str, Any]:
    """What a board looks like before the first submission."""
    del puzzle  # The empty state is the same shape for every board.
    return {
        "attempts": [],
        "mistakes": 0,
        "revealed": [],
        "solved": False,
        "complete": False,
    }


def _current(puzzle: Puzzle, state: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(state) if state else empty_state(puzzle)
    current.setdefault("attempts", [])
    current.setdefault("mistakes", 0)
    current.setdefault("revealed", [])
    current.setdefault("solved", False)
    current.setdefault("complete", False)
    return current


def _submission(puzzle: Puzzle, payload: Mapping[str, Any]) -> tuple[int, ...]:
    """The code a submission names, validated against the board.

    A submission of the wrong length, or with a repeated digit, or naming a
    digit the board does not hold, is a malformed move rather than a wrong
    answer: grading it as wrong would charge the player a mistake for a
    client's bug. ``CodeError`` is a ``ValueError``, so the session layer sees
    one kind of failure.
    """
    code = payload.get("code")
    if not isinstance(code, (list, tuple)):
        raise ValueError("a submission must carry a code")
    return check_code(
        code,
        length=int(puzzle.payload["code_length"]),
        digits=board_digits(puzzle),
    )


def grade_move(
    puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
) -> MoveJudgement:
    """Rule on one submitted code, given the state the ledger replayed to."""
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

    guess = _submission(puzzle, payload)
    code = answer_code(puzzle)
    exact, misplaced = verdict(guess, code)
    attempts = [
        *current["attempts"],
        {"guess": list(guess), "exact": exact, "misplaced": misplaced},
    ]

    if guess == code:
        return MoveJudgement(
            correct=True,
            complete=True,
            solved=True,
            state={
                **current,
                "attempts": attempts,
                "solved": True,
                "complete": True,
                "revealed": [*current["revealed"], list(code)],
            },
            note="Open. That is the code",
        )

    mistakes = int(current["mistakes"]) + 1
    out_of_tries = mistakes >= MAX_MISTAKES
    updated = {
        **current,
        "attempts": attempts,
        "mistakes": mistakes,
        "complete": out_of_tries,
        # The code is shown once the board closes, never before.
        "revealed": (
            [*current["revealed"], list(code)]
            if out_of_tries
            else [list(seen) for seen in current["revealed"]]
        ),
    }
    return MoveJudgement(
        correct=False,
        complete=out_of_tries,
        solved=False,
        state=updated,
        note=_miss_note(code, exact, misplaced, out_of_tries),
    )


def _miss_note(
    code: tuple[int, ...], exact: int, misplaced: int, out_of_tries: bool
) -> str:
    """What a wrong code is told: the verdict, in the board's own language.

    Not a leak in any new way. The board is made of exactly these statements,
    and a solver who submits a guess has bought the same information a clue
    would have given them, at the cost of a try. Withholding it would make a
    wrong guess strictly worse than a clue, which is a rule nobody would
    defend: the player would simply stop guessing.
    """
    if out_of_tries:
        return "The lock stays shut. The code was " + "".join(
            str(digit) for digit in code
        )
    if exact == 0 and misplaced == 0:
        return "None of those digits is in the code"
    parts = []
    if exact:
        parts.append(f"{exact} in the right place")
    if misplaced:
        parts.append(f"{misplaced} in the wrong place")
    return "Not it. " + ", ".join(parts)


#: Where a deduction stops being cheap. A statement removing nine tenths of
#: the code space or more is the expensive end of the ladder and is charged
#: accordingly, which is the one judgement in the hint path that is a price
#: rather than a measurement.
DEAR_ABOVE = 0.9


def get_hint(puzzle: Puzzle, state: Mapping[str, Any], hints_used: int) -> Hint:
    """The cheapest deduction the board's clues force and the player lacks.

    Derived, not authored. ``witness.next_step`` walks this board's witness --
    every statement about the code, ordered by how much of the code space it
    removes -- skips the ones the player's own attempts already established,
    and returns the next rung. Its cost is read off the same measurement: a
    statement that removes nine tenths of the space or more is charged two,
    the rest one.

    The ladder runs out before the answer does. A statement that alone
    determines the code is never offered, so ``exhausted`` here means there is
    nothing true left to say that is not a reveal -- which is the whole claim
    the witness exists to make good on.
    """
    current = _current(puzzle, state)
    if current["complete"]:
        return Hint(text="This puzzle is already finished", cost=0, exhausted=True)

    step = next_step(puzzle, current, hints_used)
    if step is None:
        return Hint(
            text=(
                "There is nothing left to tell you that would not be the code "
                "itself"
            ),
            cost=0,
            exhausted=True,
        )
    return Hint(
        text=step.statement,
        reveals=step.justification,
        cost=2 if step.information >= DEAR_ABOVE else 1,
        # Exhausted when the rung after this one would be a reveal or does not
        # exist, so a client can stop offering before the refusal above.
        exhausted=next_step(puzzle, current, hints_used + 1) is None,
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
        "code": POINTS_FOR_CODE if telemetry.completed else 0,
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
            "deduced it without a wrong guess"
            if first_try
            else ("opened it" if telemetry.completed else "lock stayed shut")
        ),
    )


def render(
    puzzle: Puzzle, state: Mapping[str, Any], locale: str
) -> PresentationModel:
    """A data description of the board. Never markup, never the code.

    The clue rows and the stated facts are elements; so are the player's own
    attempts, because on this board an attempt *is* a clue and a client that
    could not show them would be asking the player to remember their own
    verdicts.
    """
    del locale  # One locale; the tiles are digits and carry no language.
    current = _current(puzzle, state)
    glyphs: Mapping[str, str] = puzzle.payload.get("glyphs", {})
    labels: Mapping[str, str] = puzzle.payload.get("labels", {})

    def row(kind: str, index: int, guess, exact: int, misplaced: int) -> dict:
        digits = [int(digit) for digit in guess]
        return {
            "kind": kind,
            "id": f"{kind}:{index}",
            "digits": digits,
            "glyphs": [glyphs.get(str(digit), str(digit)) for digit in digits],
            "labels": [labels.get(str(digit), str(digit)) for digit in digits],
            "exact": exact,
            "misplaced": misplaced,
            "state": "clue" if kind == "clue" else "entry",
        }

    clues = tuple(
        row("clue", index, item["guess"], int(item["exact"]), int(item["misplaced"]))
        for index, item in enumerate(puzzle.payload.get("clues", ()))
    )
    facts = tuple(
        {
            "kind": "fact",
            "id": f"fact:{index}",
            "text": item.get("text", ""),
            "state": "clue",
        }
        for index, item in enumerate(puzzle.payload.get("facts", ()))
    )
    attempts = tuple(
        row(
            "attempt",
            index,
            item["guess"],
            int(item["exact"]),
            int(item["misplaced"]),
        )
        for index, item in enumerate(current.get("attempts", ()))
    )

    revealed = [list(seen) for seen in current.get("revealed", ())]
    return PresentationModel(
        elements=(*facts, *clues, *attempts),
        layout="grid",
        labels={
            "instructions": puzzle.presentation.get("instructions", ""),
            # Present only after the board has closed, which is the only time
            # ``grade_move`` writes a reveal.
            **(
                {"code": "".join(str(digit) for digit in revealed[-1])}
                if revealed
                else {}
            ),
        },
        state={
            "mistakes": current.get("mistakes", 0),
            "tries_left": max(0, MAX_MISTAKES - int(current.get("mistakes", 0))),
            "complete": current.get("complete", False),
            "solved": current.get("solved", False),
            "code_length": puzzle.payload.get("code_length"),
            "digits": list(puzzle.payload.get("digits", ())),
            "clue_count": puzzle.payload.get("clue_count"),
            "fact_count": puzzle.payload.get("fact_count"),
        },
    )


def create_share_artifact(
    puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
) -> ShareArtifact:
    """The shape of the attempt, and nothing about the code.

    Built from the move ledger with payloads already stripped by the engine,
    so it can say a guess was wrong but never which digits it was. There is
    deliberately no token carrying a verdict either: "two exact" about an
    unnamed guess would still narrow the code for a reader who then plays the
    same board.
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
        f"Opened it in {telemetry.attempts} "
        + ("try" if telemetry.attempts == 1 else "tries")
        if telemetry.completed
        else f"Lock stayed shut after {telemetry.attempts} tries"
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
            "code_length": puzzle.payload["code_length"],
            "clue_count": puzzle.payload.get("clue_count"),
        },
    )


__all__ = [
    "DEAR_ABOVE",
    "DIFFICULTY_BONUS",
    "MAX_MISTAKES",
    "PENALTY_PER_HINT",
    "PENALTY_PER_MISTAKE",
    "POINTS_FIRST_TRY",
    "POINTS_FOR_CODE",
    "create_share_artifact",
    "empty_state",
    "get_hint",
    "grade_move",
    "render",
    "score",
]
