"""Turning one candidate into a board.

Assembly is where the puzzle stops being a category with members and becomes a
thing a player sees: tiles in an order that gives nothing away, each either
carrying one letter or carrying none, and a row of blank slots for the letters
nobody supplies.

The split between ``payload`` and ``solution`` is the only thing standing
between a working game and a spoiler, and here it is sharper than in a
grouping game, because the answer is a single word. The payload is the tiles,
their labels, their marks and the number of blanks. The solution is the word,
the category it names, which tile carried which letter, which blanks the data
forced, and the memberships and vocabulary the verifier reads. The engine
strips the solution when it publishes and when it builds a share, which is
what makes a leak structurally hard rather than merely discouraged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.engine.plugin import Puzzle, PuzzleCandidate

from .descriptor import GAME_ID, MINIMUM_BLANKS
from .marks import letters_of

#: Substream that decides tile order. Named so the order is reproducible from
#: the day and the candidate alone.
LAYOUT_STREAM = "layout"


def _require(payload: Mapping[str, Any], key: str) -> Any:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"candidate payload is missing {key!r}") from exc


def columns_for(board_size: int) -> int:
    """How wide the board is laid out.

    Two rows at every supported size, so a board is a rectangle and no row is
    ragged. The marks are the thing being read across the board, and a solver
    gathering letters scans rows: two long rows keep every tile within one
    sweep, where four narrow ones make the same six letters a search.
    """
    return board_size // 2


def tile_order(tiles: Sequence[str], rng: DeterministicRng) -> list[str]:
    """Shuffle the board.

    Tiles arrive in the order the content service issued them, which is entity
    id order, so a board presented in that order is alphabetical and the marks
    sit in a pattern that has nothing to do with the puzzle. The shuffle is
    drawn from the assembly RNG rather than a fresh one, so the same candidate
    on the same day lays out the same way every time it is regenerated, which
    the engine's reproducibility check compares against.
    """
    return rng.derive(LAYOUT_STREAM).shuffled(list(tiles))


def assemble(candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
    """One candidate, one board.

    Validates as it goes rather than trusting the candidate it was handed.
    Generation and assembly are separated so each can be tested alone, and the
    cost of that separation is that assembly has to check what generation
    promised: a mismatch here is a bug in this game, and a bug that reaches
    the verifier costs a whole enumeration to discover.
    """
    payload = candidate.payload
    letters = str(_require(payload, "letters"))
    category = str(_require(payload, "category"))
    tiles = list(_require(payload, "tiles"))
    marks: Mapping[str, str] = dict(_require(payload, "marks"))
    blanks = int(_require(payload, "blanks"))
    board_size = int(_require(payload, "board_size"))

    if len(tiles) != board_size:
        raise ValueError(
            f"a board of {board_size} tiles was built from {len(tiles)} members"
        )
    labels = {tile["id"]: tile["label"] for tile in tiles}
    if len(labels) != len(tiles):
        raise ValueError("two tiles share an id, so the board is one tile short")
    if blanks < MINIMUM_BLANKS:
        raise ValueError(
            f"a legal board never has fewer than {MINIMUM_BLANKS} blanks, got {blanks}"
        )
    unknown = sorted(set(marks) - set(labels))
    if unknown:
        raise ValueError(f"marks placed on tiles not on the board: {unknown}")
    if len(marks) + blanks != len(letters):
        raise ValueError(
            f"{len(marks)} marks and {blanks} blanks cannot spell a "
            f"{len(letters)} letter answer"
        )
    if len(marks) >= board_size:
        raise ValueError(
            "at least one tile must carry no mark, which is a settled rule and "
            "the reason a board needs one more tile than it has marks"
        )
    for tile_id, letter in marks.items():
        if letter not in letters_of(labels[tile_id]):
            raise ValueError(
                f"the mark {letter!r} is not a letter of {labels[tile_id]!r}; "
                "a mark that is not in its own tile's word is decoration"
            )
    if letters_of(category) != letters:
        raise ValueError(
            f"the candidate's letters {letters!r} are not the letters of "
            f"{category!r}"
        )

    ordered = tile_order(sorted(labels), rng)

    board_payload: dict[str, Any] = {
        "game_id": GAME_ID,
        "board_size": board_size,
        "columns": columns_for(board_size),
        # The marked letter travels with its tile, because that is where the
        # player reads it. The absence of a mark is said explicitly rather
        # than left as a missing key, so a client cannot mistake an unmarked
        # tile for a tile whose mark it failed to read.
        "tiles": [
            {
                "id": tile_id,
                "label": labels[tile_id],
                "mark": marks.get(tile_id),
            }
            for tile_id in ordered
        ],
        "blanks": blanks,
        # Said out loud: the board shows how many slots the answer has, and a
        # solver counting marks and blanks would work it out anyway. Stating
        # it is what lets a client draw the slots.
        "answer_length": len(letters),
        "marked_tiles": len(marks),
    }

    solution: dict[str, Any] = {
        "category": category,
        "category_id": payload.get("category_id"),
        "answer": category,
        "letters": letters,
        "marks": dict(sorted(marks.items())),
        # Which blanks the content forced and which this board manufactured,
        # per 4.4. The board cannot tell them apart and neither can a player;
        # this is the only record that can, and it exists so a later audit can
        # ask how often the generator withheld a letter it could have shown.
        "forced_blanks": list(payload.get("forced_blanks", ())),
        "manufactured_blanks": list(payload.get("manufactured_blanks", ())),
        # Solution side, deliberately. A client holding these could intersect
        # them and read the answer off the result.
        "memberships": {
            tile: sorted(payload.get("memberships", {}).get(tile, ()))
            for tile in sorted(labels)
        },
        "vocabulary": list(payload.get("vocabulary", ())),
    }

    presentation: dict[str, Any] = {
        "layout": "grid",
        "columns": columns_for(board_size),
        "rows": board_size // columns_for(board_size),
        # One guess, if the player sees it. Declared so the engine can measure
        # efficiency against something real; a game that declares no optimum is
        # scored as if every player were perfect.
        "optimal_attempts": 1,
        "instructions": (
            f"Every tile is one of the same thing. Rearrange the marked "
            f"letters, add the {blanks} the board does not give you, and name "
            f"it in {len(letters)} letters."
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


def board_marks(puzzle: Puzzle) -> dict[str, str]:
    """The marks as the player sees them, read off the payload.

    From the payload rather than the solution on purpose: everything play and
    verification reason about has to be what the board actually shows, and
    reading the marks from the answer's side would hide a board whose two
    copies disagree.
    """
    return {
        tile["id"]: tile["mark"]
        for tile in puzzle.payload["tiles"]
        if tile.get("mark")
    }


def unmarked_tiles(puzzle: Puzzle) -> tuple[str, ...]:
    return tuple(
        tile["id"] for tile in puzzle.payload["tiles"] if not tile.get("mark")
    )


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
    client holding the payload must not be able to read the answer off it.

    Exact values, not substrings: a tile legitimately labelled ``tulip`` on a
    board whose answer is ``tulip`` is a real leak, which generation already
    refuses, while a tile labelled ``sunflower`` on a ``flower`` board is not,
    and a substring check cannot tell them apart.
    """
    leaks: list[str] = []
    answer = puzzle.solution["answer"]
    category = puzzle.solution["category"]
    for value in _strings(puzzle.payload):
        if value in {answer, category}:
            leaks.append(f"the answer {value!r} appears in the payload")
    for key in ("answer", "letters", "category", "category_id", "solution", "vocabulary"):
        if key in puzzle.payload:
            leaks.append(f"payload carries {key!r}")
    return tuple(leaks)


__all__ = [
    "LAYOUT_STREAM",
    "assemble",
    "board_marks",
    "board_tiles",
    "columns_for",
    "leaks_solution",
    "tile_order",
    "unmarked_tiles",
]
