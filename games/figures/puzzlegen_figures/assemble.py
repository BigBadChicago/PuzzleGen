"""Turning one candidate into a board.

Assembly is where the puzzle stops being a set of figures and becomes a thing
a player sees: tiles in a grid, in an order that gives nothing away, each
carrying a glyph, a colour named as a word, and a count.

The payload and solution split is simple here and has one trap in it. The
answer is three tile ids, so a payload must not carry them, must not carry
anything positionally equivalent to them, and must not order the tiles so that
the trio is findable from the order. The first two are a matter of what goes
in which dictionary. The third is the shuffle, and it is the reason the tiles
arrive from generation in content order and are laid out here: a board
presented in the order the construction built it would show its trio in the
first three positions, every day, forever.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import Puzzle, PuzzleCandidate

from .descriptor import GAME_ID, TRIO
from .trio import ATTRIBUTES, is_trio

#: Substream that decides tile order. Named so the layout is reproducible
#: from the day and the candidate alone.
LAYOUT_STREAM = "layout"


def _require(payload: Mapping[str, Any], key: str) -> Any:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"candidate payload is missing {key!r}") from exc


def columns_for(board_size: int) -> int:
    """How wide the board is laid out.

    Three columns, at both supported sizes. The rule the solver applies reads
    three figures at a time, so a three-wide grid makes every row a candidate
    triple and every column one too; a four-wide grid at twelve tiles would
    offer rows of four, which is a shape the rule says nothing about and the
    eye keeps trying anyway. Both sizes are multiples of three, so no row is
    ragged.
    """
    del board_size  # Both supported sizes lay out three wide.
    return 3


def tile_order(tile_ids: Sequence[str], rng: DeterministicRng) -> list[str]:
    """Shuffle the board.

    Drawn from the assembly RNG rather than a fresh one, so the same candidate
    on the same day lays out the same way every time it is regenerated, which
    the engine's reproducibility check compares against.
    """
    return rng.derive(LAYOUT_STREAM).shuffled(list(tile_ids))


def assemble(candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
    """One candidate, one board.

    Validates what the candidate claims rather than trusting it. Generation
    and assembly are separated so each can be tested alone, and the price of
    that separation is that assembly checks the claim: a mismatch here is a
    bug in this game, and a bug that reaches the verifier costs a whole
    enumeration to discover.
    """
    payload = candidate.payload
    board_size = int(_require(payload, "board_size"))
    tiles = list(_require(payload, "tiles"))
    answer = tuple(_require(payload, "answer"))

    if len(tiles) != board_size:
        raise ValueError(
            f"a board of {board_size} tiles was built from {len(tiles)} figures"
        )
    by_id = {tile["id"]: tile for tile in tiles}
    if len(by_id) != len(tiles):
        raise ValueError("two tiles share an id, so the board is one tile short")
    if len(set(answer)) != TRIO:
        raise ValueError(f"a trio is {TRIO} distinct tiles, got {sorted(answer)}")
    missing = sorted(set(answer) - set(by_id))
    if missing:
        raise ValueError(f"the trio names tiles not on the board: {missing}")
    for tile in tiles:
        if not tile.get("glyph"):
            raise ValueError(f"tile {tile['id']} has no glyph to draw")
        if not tile.get("label"):
            raise ValueError(f"tile {tile['id']} has no label to announce")
        unknown = sorted(set(ATTRIBUTES) - set(tile.get("attributes", {})))
        if unknown:
            raise ValueError(f"tile {tile['id']} carries no {unknown}")
    if not is_trio([by_id[tile_id]["attributes"] for tile_id in answer]):
        raise ValueError(
            "the candidate's own trio does not satisfy the rule, so the board "
            "would verify as unsolvable"
        )

    ordered = tile_order(sorted(by_id), rng)
    columns = columns_for(board_size)

    board_payload: dict[str, Any] = {
        "game_id": GAME_ID,
        "board_size": board_size,
        "columns": columns,
        "rows": board_size // columns,
        # Attributes travel with the tile, because that is what the player
        # reads and what the rule is about. The colour is a word as well as
        # whatever hue a client paints: two figures can differ only in colour,
        # so a board that conveyed it by hue alone would be unplayable for
        # some players and look complete to everybody else.
        "tiles": [
            {
                "id": tile_id,
                "glyph": by_id[tile_id]["glyph"],
                "label": by_id[tile_id]["label"],
                **{
                    attribute: by_id[tile_id]["attributes"][attribute]
                    for attribute in ATTRIBUTES
                },
            }
            for tile_id in ordered
        ],
        "trio_size": TRIO,
        "attributes": list(ATTRIBUTES),
        # The values each attribute takes in this build. The solver can read
        # them off the board anyway when all three are present, and a client
        # drawing a legend needs them stated.
        "space": {
            attribute: list(values)
            for attribute, values in sorted(
                dict(payload.get("space", {})).items()
            )
        },
    }

    solution: dict[str, Any] = {
        # Sorted, so the solution is a set rather than an order: a trio is
        # three tiles and the sequence a player clicked them in is not part of
        # the answer.
        "trio": sorted(answer),
        "varying": sorted(payload.get("varying", ())),
        "near_misses": int(payload.get("near_misses", 0)),
        "decoy_share": float(payload.get("decoy_share", 0.0)),
        # Attribute readings on the solution side as well, so the verifier
        # judges the board from one record rather than re-reading the payload
        # it is supposed to be checking.
        "figures": {
            tile_id: dict(by_id[tile_id]["attributes"]) for tile_id in sorted(by_id)
        },
    }

    presentation: dict[str, Any] = {
        "layout": "grid",
        "columns": columns,
        "rows": board_size // columns,
        # One submission, if the player sees it. Declared so the engine can
        # measure efficiency against something real; a game that declares no
        # optimum is scored as if every player were perfect.
        "optimal_attempts": 1,
        "instructions": (
            "Find the three tiles where shape, fill, count and colour are "
            "each either all the same or all different."
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
    """Tile ids in board order."""
    return tuple(tile["id"] for tile in puzzle.payload["tiles"])


def board_figures(puzzle: Puzzle) -> dict[str, dict[str, str]]:
    """Attribute readings as the player sees them, read off the payload.

    From the payload rather than the solution on purpose: everything play and
    verification reason about has to be what the board actually shows, and
    reading the attributes from the answer's side would hide a board whose two
    copies disagree.
    """
    return {
        tile["id"]: {attribute: tile[attribute] for attribute in ATTRIBUTES}
        for tile in puzzle.payload["tiles"]
    }


def position_of(puzzle: Puzzle) -> dict[str, tuple[int, int]]:
    """Each tile's ``(row, column)`` in the laid-out grid."""
    columns = int(puzzle.payload["columns"])
    return {
        tile["id"]: divmod(index, columns)
        for index, tile in enumerate(puzzle.payload["tiles"])
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

    The rule the whole payload/solution split exists for: a client holding the
    payload must not be able to read the answer off it. Two things count as a
    leak here. A key by that name, which is the obvious one. And the trio's
    tile ids appearing anywhere the payload does not need them, which is the
    one worth checking: every tile id is legitimately in the payload once, as
    its own tile, so the check is for a *second* appearance rather than for
    the id itself.
    """
    leaks: list[str] = []
    trio = set(puzzle.solution["trio"])
    for key in ("trio", "answer", "solution", "varying", "figures", "near_misses"):
        if key in puzzle.payload:
            leaks.append(f"payload carries {key!r}")
    seen = _strings(puzzle.payload)
    for tile_id in sorted(trio):
        if seen.count(tile_id) > 1:
            leaks.append(f"the trio tile {tile_id!r} appears twice in the payload")
    return tuple(leaks)


__all__ = [
    "LAYOUT_STREAM",
    "assemble",
    "board_figures",
    "board_tiles",
    "columns_for",
    "leaks_solution",
    "position_of",
    "tile_order",
]
