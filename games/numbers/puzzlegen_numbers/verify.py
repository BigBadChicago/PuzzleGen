"""Counting the tiles that could be the odd one out.

The rule is one sentence and the whole verifier is an application of it: a
tile is a reading of the board when some property in the vocabulary is true of
every other tile and false of it. The count the engine judges is the number of
*tiles* that are readings, not the number of property-and-tile pairs, and that
distinction is the one thing here worth being careful about.

A board whose odd number is picked out by two properties -- it is the only one
that is neither even nor a multiple of three -- has one answer and two
arguments for it. A player who gives that tile is right under either, so the
board is easier rather than ambiguous, and a verifier counting pairs would
report two solutions and have the board refused for being too generous. A
board where a *different* tile is also readable is the ambiguous one: two
players give two different tiles and the game marks one of them wrong.

What this enumeration does not examine is arithmetic. The vocabulary is the
properties the day's content offered, which is what the descriptor's
SOUND_INCOMPLETE declares in advance; over that vocabulary the scan is
exhaustive and the result says COMPLETE, which is what lets the uniqueness
contract be satisfied at all. A board whose vocabulary overran the bound
reports SOUND_INCOMPLETE and is refused publication, which is correct: the
properties it did not examine are exactly where a second answer would hide.
"""

from __future__ import annotations

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.plugin import Puzzle, VerificationResult

from .assemble import board_memberships, board_tiles, board_values
from .descriptor import SEARCH_BOUND
from .properties import (
    holds_for_all_but,
    near_readings_of,
    obscurity_of,
    readings_of,
    tier_of,
    universal_in,
    vocabulary_of,
)

#: Metric names. Constants because the difficulty model reads them by name,
#: and a typo in either place would be a silently absent number rather than an
#: error.
READINGS_FOUND = "readings_found"
REASONS = "reasons"
VOCABULARY_SIZE = "vocabulary_size"
NEAR_READINGS = "near_readings"
OBSCURITY = "obscurity"
SPREAD = "spread"


def board_problem(puzzle: Puzzle) -> str | None:
    """Anything that makes this board unjudgeable as shown.

    Each of these is a rule generation and assembly already apply, so a
    finding here is a bug in this game rather than a property of the content.
    They are checked anyway, because what each produces is a board that cannot
    be solved from what it shows, and a verifier that assumed the generator
    was right would report it as a fine puzzle with one answer.
    """
    payload = puzzle.payload
    tiles = board_tiles(puzzle)
    board_size = int(payload["board_size"])

    if len(tiles) != board_size:
        return f"the board says {board_size} tiles and shows {len(tiles)}"
    if len(set(tiles)) != len(tiles):
        return "two tiles share an id, so one number is on the board twice"
    if board_size < 3:
        return f"a board of {board_size} tiles has no majority to share anything"
    values = board_values(puzzle)
    if len(set(values.values())) != len(values):
        return "two tiles carry the same number, so they are one number twice"
    ordered = sorted(tiles, key=lambda tile: values[tile])
    if list(tiles) != ordered:
        return "the board is not in ascending order, so its layout is not its own"
    memberships = board_memberships(puzzle)
    absent = [tile for tile in tiles if tile not in memberships]
    if absent:
        return (
            f"{len(absent)} tiles carry no properties, so no reading of the "
            "board can be judged true or otherwise"
        )
    answer = puzzle.solution.get("answer")
    if answer not in set(tiles):
        return f"the stated answer {answer!r} is not a tile on the board"
    return None


def _as_solution(puzzle: Puzzle, tile: str, reasons: tuple[str, ...]) -> dict:
    """One found reading, in the shape the engine compares against.

    The engine hashes the puzzle's whole solution and looks for that hash
    among the ones the verifier reports, which is how it catches a board whose
    stated answer its own solver cannot reach. That check only works if the
    intended answer is reported in exactly the intended shape, down to key
    order, so it is returned as the puzzle's own solution object rather than
    as an equivalent rebuilt here.
    """
    if tile == puzzle.solution["answer"]:
        return dict(puzzle.solution)
    return {
        **dict(puzzle.solution),
        "answer": tile,
        "property": reasons[0] if reasons else "",
        "reasons": sorted(reasons),
    }


def verify(puzzle: Puzzle, *, reading_budget: int = SEARCH_BOUND) -> VerificationResult:
    """Every tile that could be the odd one out, counted honestly."""
    problem = board_problem(puzzle)
    if problem is not None:
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.COMPLETE,
            detail=problem,
        )

    memberships = board_memberships(puzzle)
    # Rebuilt from the board rather than read from the candidate's record of
    # it. The recorded vocabulary is what generation scanned; this is what the
    # board actually offers, and a board whose tiles changed between the two
    # would otherwise be checked against the wrong list.
    vocabulary = vocabulary_of(memberships)
    examined = len(vocabulary) * len(memberships)
    if examined > reading_budget:
        # Not a truncated search: it is not started. A board this large is one
        # this game was not built for, and an honest report of an unfinished
        # enumeration is refused publication by the uniqueness gate.
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            states_examined=0,
            detail=(
                f"{len(memberships)} tiles against {len(vocabulary)} properties "
                f"is {examined} readings, beyond the {reading_budget} this game "
                "examines"
            ),
        )

    found = readings_of(memberships, vocabulary)
    near = near_readings_of(memberships, vocabulary)
    stated = puzzle.solution["answer"]
    values = board_values(puzzle)

    metrics = {
        READINGS_FOUND: float(len(found)),
        REASONS: float(len(found.get(stated, ()))),
        VOCABULARY_SIZE: float(len(vocabulary)),
        NEAR_READINGS: float(len(near)),
        OBSCURITY: obscurity_of(str(puzzle.solution.get("property", ""))),
        SPREAD: float(max(values.values()) - min(values.values())) if values else 0.0,
    }

    detail = ""
    if not found:
        detail = "no tile on this board is the odd one out under any property"
    elif stated not in found:
        # Verifies, is unique, and marks the player wrong: the most dangerous
        # way for a board to be broken, and one scan catches it.
        detail = "the stated answer is not among the readings the board holds"
    elif len(found) > 1:
        detail = (
            f"{len(found)} tiles could be the odd one out: "
            + ", ".join(sorted(found))
        )

    return VerificationResult(
        solvable=bool(found),
        solution_count=len(found),
        completeness=VerificationCompleteness.COMPLETE,
        states_examined=examined,
        solutions=tuple(
            _as_solution(puzzle, tile, reasons) for tile, reasons in found.items()
        ),
        metrics=metrics,
        detail=detail,
    )


def why_not(puzzle: Puzzle, tile: str) -> tuple[str, ...]:
    """Properties the chosen tile shares with all but at most one other tile.

    What a wrongly chosen tile has going against it. A board with one reading
    guarantees there is no property only this tile lacks -- that would be a
    second reading -- so the honest thing to say about a wrong guess is the
    reverse: everything it is, nearly everything else is too. These are those
    properties.

    "All but at most one" rather than "all", because the answer tile is the
    one that lacks the board's own property, and a stricter rule would return
    only the universal ones. ``two-digit number`` is a true thing to say about
    a wrong guess and a useless one.
    """
    memberships = board_memberships(puzzle)
    if tile not in memberships:
        return ()
    total = len(memberships)
    shared: list[str] = []
    for name in memberships[tile]:
        holders = sum(1 for names in memberships.values() if name in names)
        if holders >= total - 1:
            shared.append(name)
    return tuple(sorted(shared))


def near_miss_for(puzzle: Puzzle, tile: str) -> str:
    """A property this tile is one of exactly two not to have, if there is one.

    The most likely reason a player chose a wrong tile, and the one piece of
    feedback this game can give that is both informative and safe. A near
    reading has two outsiders, so naming it tells the player their tile is
    unusual in a way that nevertheless does not single it out -- which is
    exactly the mistake they made -- and it names a property that is not the
    board's answer, because a property with two outsiders is not a reading.

    Empty when there is none, and grading says the structural thing instead.
    """
    memberships = board_memberships(puzzle)
    if tile not in memberships:
        return ""
    vocabulary = vocabulary_of(memberships)
    for name in near_readings_of(memberships, vocabulary):
        if name not in memberships[tile]:
            return name
    return ""


def readings(puzzle: Puzzle) -> dict[str, tuple[str, ...]]:
    """Which tiles could be the odd one out, for tests and diagnostics."""
    memberships = board_memberships(puzzle)
    return readings_of(memberships, vocabulary_of(memberships))


__all__ = [
    "NEAR_READINGS",
    "OBSCURITY",
    "READINGS_FOUND",
    "REASONS",
    "SPREAD",
    "VOCABULARY_SIZE",
    "board_problem",
    "near_miss_for",
    "readings",
    "verify",
    "why_not",
]
