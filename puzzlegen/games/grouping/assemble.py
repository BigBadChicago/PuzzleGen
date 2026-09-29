"""Turning one candidate into a board.

Assembly is where the puzzle stops being a set of groups and becomes a thing a
player sees: tiles in an order that gives nothing away, and a solution kept
somewhere the player is never sent.

The split between ``payload`` and ``solution`` is the only thing standing
between a working game and a spoiler, so it is worth stating plainly. The
payload is everything the client may hold: the tiles and their labels, the
group size, the day's shape. The solution is everything that answers the
puzzle: which tile belongs to which group, what the groups are called, and
which tiles form the hidden one. The engine strips the solution when it
publishes and when it builds a share, which is what makes a leak structurally
hard rather than merely discouraged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...core.rng import DeterministicRng
from ...engine.plugin import Puzzle, PuzzleCandidate
from .descriptor import GAME_ID, VISIBLE_GROUPS

#: Substream that decides tile order. Named so the order is reproducible from
#: the day and the candidate alone.
LAYOUT_STREAM = "layout"

#: Columns the shell lays tiles out in.
#:
#: Four, matching the group count, so a board of any supported size is a
#: rectangle: 20 tiles are five rows of four, 36 are nine rows. A column count
#: that did not divide the board would leave a ragged last row, and a ragged
#: row tells a player how many tiles are left without them counting.
COLUMNS = VISIBLE_GROUPS


def _require(payload: Mapping[str, Any], key: str) -> Any:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"candidate payload is missing {key!r}") from exc


def tile_order(
    tiles: Sequence[str], rng: DeterministicRng
) -> list[str]:
    """Shuffle the board.

    Tiles arrive grouped, because that is how they were chosen, and a board
    presented in that order is solved by reading it left to right. The shuffle
    is drawn from the assembly RNG rather than a fresh one, so the same
    candidate on the same day lays out the same way every time it is
    regenerated, which the engine's reproducibility check compares against.
    """
    return rng.derive(LAYOUT_STREAM).shuffled(list(tiles))


def assemble(candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
    """One candidate, one board.

    Validates as it goes rather than trusting the candidate it was handed.
    Generation and assembly are separated so each can be tested alone, and the
    cost of that separation is that assembly has to check what generation
    promised: a mismatch here is a bug in this game, and a bug that reaches the
    verifier costs a whole enumeration to discover.
    """
    payload = candidate.payload
    visible = _require(payload, "visible")
    hidden = _require(payload, "hidden")
    group_size = int(_require(payload, "group_size"))

    if len(visible) != VISIBLE_GROUPS:
        raise ValueError(
            f"a board needs {VISIBLE_GROUPS} visible groups, got {len(visible)}"
        )
    for group in visible:
        if len(group["members"]) != group_size:
            raise ValueError(
                f"visible group {group['category']!r} has "
                f"{len(group['members'])} members, expected {group_size}"
            )
    if len(hidden["members"]) != group_size:
        raise ValueError(
            "the hidden group must be exactly as large as a visible one, or it "
            "can be identified by counting"
        )

    names: dict[str, str] = {}
    assignment: dict[str, int] = {}
    for index, group in enumerate(visible):
        for entity_id, name in zip(group["members"], group["names"]):
            if entity_id in assignment:
                raise ValueError(
                    f"{name!r} appears in two visible groups, so the intended "
                    "partition is not the only one"
                )
            assignment[entity_id] = index
            names[entity_id] = name

    hidden_ids = tuple(hidden["members"])
    missing = [tile for tile in hidden_ids if tile not in assignment]
    if missing:
        raise ValueError(
            "every hidden member must already be on the board; missing "
            f"{len(missing)} of {len(hidden_ids)}"
        )
    if len({assignment[tile] for tile in hidden_ids}) != VISIBLE_GROUPS:
        raise ValueError(
            "the hidden group must take a tile from every visible group, or "
            "the untouched group gives it away"
        )

    ordered = tile_order(sorted(assignment), rng)

    board_payload: dict[str, Any] = {
        "game_id": GAME_ID,
        "group_size": group_size,
        "group_count": VISIBLE_GROUPS,
        "columns": COLUMNS,
        "tiles": [
            {"id": entity_id, "label": names[entity_id]} for entity_id in ordered
        ],
        # Said out loud, because a player who is never told there is a fifth
        # group cannot be expected to look for one, and a board that changes
        # what it asks without warning is a board that loses them.
        "hidden_group_exists": True,
        "hidden_group_size": group_size,
    }

    solution: dict[str, Any] = {
        "groups": [
            {
                "index": index,
                "category": group["category"],
                "category_id": group["category_id"],
                "members": list(group["members"]),
            }
            for index, group in enumerate(visible)
        ],
        "assignment": dict(sorted(assignment.items())),
        "hidden": {
            "category": hidden["category"],
            "category_id": hidden["category_id"],
            "secondary_category": hidden.get("secondary_category"),
            "members": list(hidden_ids),
        },
        "borrowings": [list(pair) for pair in payload.get("borrowings", ())],
        # Solution side, deliberately. A client holding these could rank the
        # tiles by shared category and read the groups straight off, so they
        # travel with the answer and are stripped with it.
        "memberships": {
            tile: sorted(payload.get("memberships", {}).get(tile, ()))
            for tile in sorted(assignment)
        },
    }

    presentation: dict[str, Any] = {
        "layout": "grid",
        "columns": COLUMNS,
        "rows": (group_size * VISIBLE_GROUPS) // COLUMNS,
        "select_limit": group_size,
        # A hint the shell may show before the first move. It names the shape
        # of the puzzle, never any part of its answer.
        # Four visible groups and one hidden round. Declared so the engine can
        # measure efficiency against something real; a game that declares no
        # optimum is scored as if every player were perfect.
        "optimal_attempts": VISIBLE_GROUPS + 1,
        "instructions": (
            f"Sort all {group_size * VISIBLE_GROUPS} tiles into "
            f"{VISIBLE_GROUPS} groups of {group_size}."
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
    return tuple(tile["id"] for tile in puzzle.payload["tiles"])


def solution_groups(puzzle: Puzzle) -> tuple[frozenset[str], ...]:
    """The intended partition, as sets.

    Sets rather than lists: the intended answer is a partition, and comparing
    it as ordered lists would call a correct solve wrong because the player
    found the groups in a different order.
    """
    return tuple(
        frozenset(group["members"]) for group in puzzle.solution["groups"]
    )


def hidden_members(puzzle: Puzzle) -> frozenset[str]:
    return frozenset(puzzle.solution["hidden"]["members"])


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

    Used by the tests and cheap enough to keep available at runtime. The rule
    it enforces is the one the whole payload/solution split exists for: a
    client holding the payload must not be able to reconstruct the answer.
    """
    leaks: list[str] = []
    payload = puzzle.payload
    categories = {
        group["category"] for group in puzzle.solution["groups"] if group["category"]
    }
    hidden = puzzle.solution["hidden"]
    for key in ("category", "secondary_category"):
        if hidden.get(key):
            categories.add(hidden[key])

    # Exact values, not substrings. A tile legitimately labelled "salmon" in a
    # board whose hidden category is "salmon" would be a real leak; a tile
    # labelled "salmonberry" would not, and a substring check cannot tell them
    # apart.
    for value in _strings(payload):
        if value in categories:
            leaks.append(f"category {value!r} appears in the payload")
    for key in ("assignment", "groups", "hidden", "borrowings", "solution"):
        if key in payload:
            leaks.append(f"payload carries {key!r}")
    return tuple(leaks)


__all__ = [
    "COLUMNS",
    "LAYOUT_STREAM",
    "assemble",
    "board_tiles",
    "hidden_members",
    "leaks_solution",
    "solution_groups",
    "tile_order",
]
