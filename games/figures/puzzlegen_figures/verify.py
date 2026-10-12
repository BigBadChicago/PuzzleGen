"""Proving the board has exactly one trio.

This is the one place in the repository where a game can promise the strong
thing honestly, and the reason is the content rather than the code. A grouping
board's solution space is the partitions of sixteen tiles and a spelling
board's is the English language; both are too large to enumerate, so one game
promises a unique grouping and the other promises soundness inside a pinned
vocabulary. A trio board's solution space is the triples of its own tiles:
eighty-four at nine tiles, two hundred and twenty at twelve. Every one of them
is examined, so the count reported is the count, the completeness is COMPLETE
without qualification, and the contract is EXACTLY_ONE.

Enumerated rather than computed, which is deliberate and costs almost
nothing. The generator builds a board using the fact that two figures
determine a third, so a board's trios are its pairs whose completion is
present. The verifier could use the same shortcut and must not: the thing that
built the board and the thing that checks it would then be making the same
mistake, and a bug in the algebra would be invisible to both. Two hundred and
twenty triples is a cost worth paying for a check that is independent.
"""

from __future__ import annotations

from collections.abc import Mapping

from puzzlegen.core.types import VerificationCompleteness
from puzzlegen.engine.plugin import Puzzle, VerificationResult

from .assemble import board_figures, board_tiles
from .descriptor import SEARCH_BOUND, TRIO
from .trio import ATTRIBUTES, failing_attributes, is_trio, near_trios, trios_in

#: Metric names. Constants because the difficulty model reads them by name,
#: and a typo in either place would be a silently absent number rather than an
#: error.
TRIPLES_EXAMINED = "triples_examined"
TRIOS_FOUND = "trios_found"
NEAR_MISSES = "near_misses"
DECOY_SHARE = "decoy_share"
VARYING_ATTRIBUTES = "varying_attributes"


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
        return "two tiles share an id, so a trio could name the same tile twice"
    if len(tiles) < TRIO:
        return f"a board of {len(tiles)} tiles cannot hold a trio"
    figures = board_figures(puzzle)
    for tile_id, reading in figures.items():
        missing = sorted(set(ATTRIBUTES) - set(reading))
        if missing:
            return f"tile {tile_id} carries no {missing}, so no triple can be judged"
    trio = list(puzzle.solution.get("trio", ()))
    if len(set(trio)) != TRIO:
        return f"the stated answer is not {TRIO} distinct tiles: {sorted(trio)}"
    unknown = sorted(set(trio) - set(tiles))
    if unknown:
        return f"the stated answer names tiles not on the board: {unknown}"
    return None


def _as_solution(puzzle: Puzzle, trio: tuple[str, ...]) -> dict:
    """One found trio, in the shape the engine compares against.

    The engine hashes the puzzle's whole solution and looks for that hash
    among the ones the verifier reports, which is how it catches a board whose
    stated answer its own solver cannot reach. That check only works if the
    intended answer is reported in exactly the intended shape, down to key
    order, so it is returned as the puzzle's own solution object rather than
    as an equivalent rebuilt here.
    """
    chosen = sorted(trio)
    if chosen == sorted(puzzle.solution["trio"]):
        return dict(puzzle.solution)
    figures = board_figures(puzzle)
    return {
        **dict(puzzle.solution),
        "trio": chosen,
        "varying": sorted(
            attribute
            for attribute in ATTRIBUTES
            if len({figures[tile_id][attribute] for tile_id in chosen}) == TRIO
        ),
    }


def verify(puzzle: Puzzle, *, triple_budget: int = SEARCH_BOUND) -> VerificationResult:
    """Every trio on this board, counted by examining every triple."""
    problem = board_problem(puzzle)
    if problem is not None:
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.COMPLETE,
            detail=problem,
        )

    tiles = board_tiles(puzzle)
    figures = board_figures(puzzle)
    readings = [figures[tile_id] for tile_id in tiles]

    triples = len(tiles) * (len(tiles) - 1) * (len(tiles) - 2) // 6
    if triples > triple_budget:
        # Not a truncated search: the enumeration is not started. A board this
        # large is one this game was not built for, and an honest report of an
        # unfinished enumeration is refused publication by the uniqueness
        # gate, which is the correct outcome.
        return VerificationResult(
            solvable=False,
            solution_count=0,
            completeness=VerificationCompleteness.SOUND_INCOMPLETE,
            states_examined=0,
            detail=(
                f"a board of {len(tiles)} tiles holds {triples} triples, "
                f"beyond the {triple_budget} this game enumerates"
            ),
        )

    found = [
        tuple(tiles[index] for index in indices) for indices in trios_in(readings)
    ]
    near = near_trios(readings)
    varying = len(puzzle.solution.get("varying", ()))

    metrics = {
        TRIPLES_EXAMINED: float(triples),
        TRIOS_FOUND: float(len(found)),
        NEAR_MISSES: float(len(near)),
        DECOY_SHARE: len(near) / triples if triples else 0.0,
        VARYING_ATTRIBUTES: float(varying),
    }

    stated = sorted(puzzle.solution["trio"])
    detail = ""
    if not found:
        detail = "no three tiles on this board satisfy the rule"
    elif stated not in [sorted(trio) for trio in found]:
        # Verifies, is unique, and marks the player wrong: the most dangerous
        # way for a board to be broken, and one enumeration catches it.
        detail = "the stated answer is not among the trios the board holds"
    elif len(found) > 1:
        detail = f"the board holds {len(found)} trios, so its answer is not unique"

    return VerificationResult(
        solvable=bool(found),
        solution_count=len(found),
        completeness=VerificationCompleteness.COMPLETE,
        states_examined=triples,
        solutions=tuple(_as_solution(puzzle, trio) for trio in found),
        metrics=metrics,
        detail=detail,
    )


def closest_misses(puzzle: Puzzle, limit: int = 5) -> tuple[tuple[str, ...], ...]:
    """Triples that fail on exactly one attribute, for a hint or a diagnostic.

    Not used in grading. A curator reading a board wants to know which triples
    it is actually making the solver reject, and the share alone does not say.
    """
    tiles = board_tiles(puzzle)
    figures = board_figures(puzzle)
    readings = [figures[tile_id] for tile_id in tiles]
    return tuple(
        tuple(tiles[index] for index in indices)
        for indices in near_trios(readings)[:limit]
    )


def why_not(puzzle: Puzzle, chosen: Mapping[int, str] | tuple[str, ...]) -> tuple[str, ...]:
    """Which attributes a submitted triple fails on.

    Used by grading to say something true about a wrong answer without naming
    any part of the right one.
    """
    figures = board_figures(puzzle)
    tiles = tuple(chosen.values()) if isinstance(chosen, Mapping) else tuple(chosen)
    readings = [figures[tile_id] for tile_id in tiles if tile_id in figures]
    if len(readings) != TRIO:
        return ATTRIBUTES
    if is_trio(readings):
        return ()
    return failing_attributes(readings)


__all__ = [
    "DECOY_SHARE",
    "NEAR_MISSES",
    "TRIOS_FOUND",
    "TRIPLES_EXAMINED",
    "VARYING_ATTRIBUTES",
    "board_problem",
    "closest_misses",
    "verify",
    "why_not",
]
