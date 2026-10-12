"""Numbers, content results and boards, built by hand.

Synthetic rather than drawn from a snapshot, so a failing test points at the
game rather than at whichever numbers the window happened to hold. The tests
that run against real content say so in their own names.

The properties a built number carries are computed from its value rather than
listed, which matters more here than in the other games' fixtures: a hand
written membership list that said 51 was prime would make a uniqueness test
pass against arithmetic nobody does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from puzzlegen.content.query import ContentResult, EntityView, Operation
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.plugin import GenerationContext, Puzzle
from puzzlegen.providers.generated import (
    digit_sum,
    is_fibonacci,
    is_prime,
    is_square,
    is_triangular,
    spoken_number,
)
from puzzlegen_numbers import content as content_module
from puzzlegen_numbers.descriptor import GAME_ID

#: The property tests, by the name the taxonomy uses.
#:
#: Imported from the provider rather than reimplemented, which is the point:
#: the fixtures and the content agree about what a prime is because they are
#: the same function. A fixture with its own arithmetic would be a second
#: place for it to be wrong.
TESTS = {
    "even number": lambda v: v % 2 == 0,
    "odd number": lambda v: v % 2 == 1,
    "prime number": is_prime,
    "square number": is_square,
    "triangular number": is_triangular,
    "Fibonacci number": is_fibonacci,
    "multiple of three": lambda v: v % 3 == 0,
    "multiple of five": lambda v: v % 5 == 0,
    "multiple of ten": lambda v: v % 10 == 0,
    "two-digit number": lambda v: len(str(v)) == 2,
}


def properties_of(value: int) -> tuple[str, ...]:
    """Every property ``value`` has, in the order the taxonomy sorts them."""
    return tuple(sorted(name for name, test in TESTS.items() if test(value)))


def entity(value: int) -> EntityView:
    """One number, as the content service would return it."""
    names = properties_of(value)
    return EntityView(
        entity_id=f"entity:{value}",
        name=str(value),
        definition=f"the number {spoken_number(value)}",
        properties={
            "glyph": str(value),
            "spoken": spoken_number(value),
            "symbol_kind": "NUMBER",
            "value": value,
            "parity": "even" if value % 2 == 0 else "odd",
            "digit_sum": digit_sum(value),
        },
        types=(*names, "number"),
        type_ids=tuple(f"category:{name.replace(' ', '-')}" for name in (*names, "number")),
    )


def window(low: int = 10, high: int = 99) -> list[EntityView]:
    """Every number in a window, ascending, as the query returns them."""
    return [entity(value) for value in range(low, high + 1)]


def result(entities: Sequence[EntityView]) -> ContentResult:
    return ContentResult(
        operation=Operation.FIND_CATEGORY_MEMBERS,
        entities=tuple(entities),
        examined=len(entities),
    )


def context(
    entities: Sequence[EntityView],
    *,
    day_key: str = "2026-10-01",
    band: DifficultyBand = DifficultyBand.MEDIUM,
    budget: int = 64,
    salt: str = "",
) -> GenerationContext:
    """A generation context carrying one content result, as the engine builds it."""
    return GenerationContext(
        day_key=day_key,
        game_version="1.0.0",
        difficulty_target=band,
        locale="en",
        rng=DeterministicRng(derive_seed(day_key, GAME_ID, salt)).derive("candidates"),
        content={content_module.CANDIDATES: result(entities)},
        candidate_budget=budget,
    )


#: Five numbers, four even and one not, and nothing else they share.
#:
#: Checked by ``test_numbers_boards.py`` rather than asserted here: four even
#: numbers chosen carelessly are also all multiples of something, and a board
#: with a second reading would make other tests pass for the wrong reason.
EVEN_BOARD = (12, 34, 51, 76, 98)


def make_puzzle(
    values: Sequence[int] = EVEN_BOARD,
    *,
    answer: int | None = None,
    property_name: str | None = None,
) -> Puzzle:
    """An assembled board, built directly rather than through assembly.

    ``answer`` and ``property_name`` default to the odd one out the values
    actually have, computed the way the verifier computes it, so a fixture
    cannot claim an answer the board does not hold unless a test asks it to.
    """
    ordered = sorted(values)
    tiles = [
        {
            "id": f"entity:{value}",
            "glyph": str(value),
            "label": spoken_number(value),
            "value": value,
        }
        for value in ordered
    ]
    memberships = {f"entity:{value}": list(properties_of(value)) for value in ordered}

    from puzzlegen_numbers.properties import (
        near_readings_of,
        readings_of,
        universal_in,
        vocabulary_of,
    )

    vocabulary = vocabulary_of(memberships)
    found = readings_of(memberships, vocabulary)
    if answer is None:
        answer_id = next(iter(found)) if found else tiles[0]["id"]
    else:
        answer_id = f"entity:{answer}"
    reasons = found.get(answer_id, ())
    chosen = property_name or (reasons[0] if reasons else "")

    return Puzzle(
        game_id=GAME_ID,
        payload={
            "game_id": GAME_ID,
            "board_size": len(tiles),
            "layout": "row",
            "tiles": tiles,
            "ordered": "ascending",
        },
        solution={
            "answer": answer_id,
            "property": chosen,
            "reasons": sorted(reasons),
            "vocabulary": sorted(vocabulary),
            "universal": sorted(universal_in(memberships)),
            "near": sorted(near_readings_of(memberships, vocabulary)),
            "memberships": {k: sorted(v) for k, v in sorted(memberships.items())},
            "spread": ordered[-1] - ordered[0],
        },
        fact_refs=tuple(tile["id"] for tile in tiles),
        presentation={"optimal_attempts": 1, "layout": "row", "instructions": "x"},
        candidate_id="hand-built",
    )


__all__ = [
    "EVEN_BOARD",
    "TESTS",
    "context",
    "entity",
    "make_puzzle",
    "properties_of",
    "result",
    "window",
]
