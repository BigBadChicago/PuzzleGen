"""Turning one candidate into a board.

Short, and the interesting thing about it is what it does not do: there is no
shuffle. The figure game lays its tiles out in a drawn order because a board
in construction order would show its trio in the first three positions. Here
the board is the numbers in ascending order, and that order is a pure function
of the values the player can read off the tiles. Position therefore carries no
information the board was not already giving away, and a drawn order would
cost the solver a mental sort before they could start.

That makes the payload/solution split the whole of this module's work. The
payload is the numbers and their order. The solution is which one is the odd
one out, what the others share, what else would have picked the same tile, and
the vocabulary the verifier scanned -- all of it answer, and none of it on the
board.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import Puzzle, PuzzleCandidate

from .descriptor import GAME_ID
from .properties import holds_for_all_but, readings_of


def _require(payload: Mapping[str, Any], key: str) -> Any:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"candidate payload is missing {key!r}") from exc


def assemble(candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
    """One candidate, one board.

    ``rng`` is accepted and unused, which is worth saying out loud rather than
    leaving to be noticed: the protocol hands assembly a substream because
    most games need one, and a game whose layout is determined by its content
    should not consume one to look busy. Drawing from it would make this
    board's order depend on a stream nothing else reads.

    Validates what the candidate claims rather than trusting it. A mismatch
    here is a bug in this game, and a bug that reaches the verifier costs a
    whole enumeration to discover.
    """
    del rng  # The board's order is its values; see the module docstring.
    payload = candidate.payload
    board_size = int(_require(payload, "board_size"))
    tiles = list(_require(payload, "tiles"))
    answer = str(_require(payload, "answer"))
    property_name = str(_require(payload, "property"))
    memberships: Mapping[str, Sequence[str]] = {
        tile: tuple(names)
        for tile, names in dict(_require(payload, "memberships")).items()
    }

    if len(tiles) != board_size:
        raise ValueError(
            f"a board of {board_size} tiles was built from {len(tiles)} numbers"
        )
    by_id = {tile["id"]: tile for tile in tiles}
    if len(by_id) != len(tiles):
        raise ValueError("two tiles share an id, so the board is one tile short")
    if answer not in by_id:
        raise ValueError(f"the answer {answer!r} is not a tile on the board")
    missing = sorted(set(by_id) - set(memberships))
    if missing:
        raise ValueError(f"these tiles carry no memberships: {missing}")
    for tile in tiles:
        if not tile.get("glyph"):
            raise ValueError(f"tile {tile['id']} has no glyph to draw")
        if not tile.get("label"):
            raise ValueError(f"tile {tile['id']} has no label to announce")
        if not isinstance(tile.get("value"), int):
            raise ValueError(f"tile {tile['id']} carries no numeric value")
    if not holds_for_all_but(property_name, answer, memberships):
        raise ValueError(
            f"{property_name!r} is not true of every tile but {answer!r}, so "
            "the board has no reading and would verify as unsolvable"
        )

    ordered = sorted(tiles, key=lambda tile: (tile["value"], tile["id"]))
    if [tile["id"] for tile in ordered] != [tile["id"] for tile in tiles]:
        # Generation already sorts, so this is a bug rather than a repair.
        # Raised rather than corrected: a board arriving out of order means
        # the candidate and the plan disagree, and quietly sorting it would
        # hide that while producing a board that happens to look right.
        raise ValueError(
            "the candidate's tiles are not in ascending order, so the board "
            "it describes is not the board it would show"
        )

    board_payload: dict[str, Any] = {
        "game_id": GAME_ID,
        "board_size": board_size,
        # One row. Said explicitly so a client does not have to infer a layout
        # from the tile count, and because the accessibility declaration
        # promises a list keyboard model rather than a grid.
        "layout": "row",
        "tiles": [
            {
                "id": tile["id"],
                "glyph": tile["glyph"],
                "label": tile["label"],
                "value": tile["value"],
            }
            for tile in ordered
        ],
        # The board shows that the numbers ascend, because they visibly do.
        # Stating it lets a client say so to a reader rather than leaving the
        # order to be discovered.
        "ordered": "ascending",
    }

    solution: dict[str, Any] = {
        "answer": answer,
        "property": property_name,
        # Every property that picks out the same tile. Not alternatives: one
        # answer with several reasons, which the verifier counts as one
        # solution and the difficulty model reads as a board made easier.
        "reasons": sorted(payload.get("reasons", ())),
        "vocabulary": sorted(payload.get("vocabulary", ())),
        "universal": sorted(payload.get("universal", ())),
        "near": sorted(payload.get("near", ())),
        # Solution side, deliberately. A client holding these could compute
        # the readings itself and read the answer off the result.
        "memberships": {
            tile: sorted(names) for tile, names in sorted(memberships.items())
        },
        "spread": int(payload.get("spread", 0)),
    }

    presentation: dict[str, Any] = {
        "layout": "row",
        "columns": board_size,
        "rows": 1,
        # One submission, if the player sees it. Declared so the engine can
        # measure efficiency against something real.
        "optimal_attempts": 1,
        "instructions": (
            "All but one of these numbers have something in common. Choose "
            "the one that does not."
        ),
    }

    return Puzzle(
        game_id=GAME_ID,
        payload=board_payload,
        solution=solution,
        fact_refs=candidate.fact_refs,
        presentation=presentation,
        candidate_id=candidate.candidate_id,
    )


def board_tiles(puzzle: Puzzle) -> tuple[str, ...]:
    """Tile ids in board order, which is ascending by value."""
    return tuple(tile["id"] for tile in puzzle.payload["tiles"])


def board_values(puzzle: Puzzle) -> dict[str, int]:
    """Values as the player sees them, read off the payload.

    From the payload rather than the solution on purpose: everything play and
    verification reason about has to be what the board actually shows, and
    reading the values from the answer's side would hide a board whose two
    copies disagree.
    """
    return {tile["id"]: int(tile["value"]) for tile in puzzle.payload["tiles"]}


def board_memberships(puzzle: Puzzle) -> dict[str, tuple[str, ...]]:
    """Each tile's properties, from the solution.

    From the solution because that is where they are: a payload carrying them
    would let a client compute the readings and read the answer off the
    result. The verifier is engine side and holds the solution, which is the
    arrangement that makes this safe.
    """
    return {
        tile: tuple(names)
        for tile, names in puzzle.solution.get("memberships", {}).items()
    }


def _strings(value: Any) -> list[str]:
    """Every string anywhere inside a payload."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, item in value.items():
            found.append(str(key))
            found.extend(_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_strings(item))
        return found
    return []


def leaks_solution(puzzle: Puzzle) -> tuple[str, ...]:
    """Anything in the payload that answers the puzzle.

    Three things count here. A key by an answer's name. The answer tile's id
    appearing twice, since once is the tile itself. And the shared property's
    name appearing at all: unlike a tile id it has no legitimate place on the
    board, so a single occurrence is a leak, and it is the one that would
    matter most -- a client showing the property would be printing the answer
    in words.
    """
    leaks: list[str] = []
    for key in ("answer", "property", "reasons", "memberships", "vocabulary", "near"):
        if key in puzzle.payload:
            leaks.append(f"payload carries {key!r}")
    seen = _strings(puzzle.payload)
    answer = puzzle.solution["answer"]
    if seen.count(answer) > 1:
        leaks.append(f"the answer tile {answer!r} appears twice in the payload")
    for name in {puzzle.solution["property"], *puzzle.solution.get("reasons", ())}:
        if name in seen:
            leaks.append(f"the property {name!r} is named in the payload")
    return tuple(leaks)


__all__ = [
    "assemble",
    "board_memberships",
    "board_tiles",
    "board_values",
    "leaks_solution",
]
