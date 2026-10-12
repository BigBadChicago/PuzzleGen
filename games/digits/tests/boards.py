"""Digits, content results and boards, built by hand.

Synthetic rather than drawn from a snapshot, so a failing test points at the
game rather than at the snapshot. The tests that run against real content say
so in their own names.

The digit facts a built entity carries are the ones the provider emits, so a
fixture and the content agree about what a prime is. That matters more in this
game than in any other: a property clue is read off those facts, and a fixture
with its own arithmetic would make a clue true in a test and false in
production.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from puzzlegen.content.query import ContentResult, EntityView, Operation
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.plugin import GenerationContext, Puzzle
from puzzlegen.providers.generated import is_prime, spoken_number
from puzzlegen_digits import content as content_module
from puzzlegen_digits.descriptor import GAME_ID
from puzzlegen_digits.lock import (
    DigitFacts,
    GuessClue,
    PropertyClue,
    property_clues_for,
    verdict,
)

#: The ten digits, as the content supplies them.
DIGITS = tuple(range(10))


def entity(value: int) -> EntityView:
    """One digit, as the content service would return it."""
    return EntityView(
        entity_id=f"entity:{value}",
        name=str(value),
        definition=f"the number {spoken_number(value)}",
        properties={
            "glyph": str(value),
            "spoken": spoken_number(value),
            "symbol_kind": "DIGIT",
            "value": value,
            "is_prime": is_prime(value),
            "parity": "even" if value % 2 == 0 else "odd",
        },
        types=("digit",),
        type_ids=("category:digit",),
    )


def digits(values: Sequence[int] = DIGITS) -> list[EntityView]:
    """Every digit, ascending, as the query returns them."""
    return [entity(value) for value in values]


def facts(values: Sequence[int] = DIGITS) -> dict[int, DigitFacts]:
    """The facts the deduction reads, built from the provider's arithmetic."""
    return {
        value: DigitFacts(
            value=value, prime=is_prime(value), even=value % 2 == 0
        )
        for value in values
    }


def result(entities: Sequence[EntityView]) -> ContentResult:
    return ContentResult(
        operation=Operation.FIND_CATEGORY_MEMBERS,
        entities=tuple(entities),
        examined=len(entities),
    )


def context(
    entities: Sequence[EntityView] | None = None,
    *,
    day_key: str = "2026-10-01",
    band: DifficultyBand = DifficultyBand.MEDIUM,
    budget: int = 32,
    salt: str = "",
) -> GenerationContext:
    """A generation context carrying one content result, as the engine builds it."""
    chosen = digits() if entities is None else entities
    return GenerationContext(
        day_key=day_key,
        game_version="1.0.0",
        difficulty_target=band,
        locale="en",
        rng=DeterministicRng(derive_seed(day_key, GAME_ID, salt)).derive("candidates"),
        content={content_module.CANDIDATES: result(chosen)},
        candidate_budget=budget,
    )


#: A code and three guesses that between them leave exactly one survivor.
#:
#: Found by searching, and the first attempt at it is why the search happened:
#: the obvious clue set -- 012, 345, 678, one row per third of the digits --
#: leaves twenty-four codes alive, because three rows of three disjoint digits
#: say which digits are in the code and almost nothing about their order. A
#: hand-chosen set that left two survivors would have made every test using it
#: pass for the wrong reason, so these were searched for over the real
#: elimination and ``test_digits_boards.py`` asserts the property on the built
#: board.
#:
#: Three rows rather than the five or six a generated board carries, so a test
#: about a board's shape does not have to read eight of them.
CODE = (1, 4, 7)
GUESSES = ((4, 3, 7), (4, 6, 1), (6, 0, 3))


def make_puzzle(
    code: Sequence[int] = CODE,
    guesses: Sequence[Sequence[int]] = GUESSES,
    *,
    properties: Sequence[str] = (),
    values: Sequence[int] = DIGITS,
    exact_override: Mapping[int, tuple[int, int]] | None = None,
) -> Puzzle:
    """An assembled board, built directly rather than through assembly.

    ``exact_override`` doctors one clue's verdict, which is how the tests that
    break a board on purpose do it; every other verdict is computed from the
    code, so a fixture cannot accidentally state a false clue.
    """
    chosen = tuple(code)
    known = facts(values)
    stated = property_clues_for(chosen, known, properties)
    rows = []
    for index, guess in enumerate(guesses):
        got = verdict(tuple(guess), chosen)
        if exact_override and index in exact_override:
            got = exact_override[index]
        rows.append(
            {
                "kind": "guess",
                "guess": list(guess),
                "exact": got[0],
                "misplaced": got[1],
            }
        )
    return Puzzle(
        game_id=GAME_ID,
        payload={
            "game_id": GAME_ID,
            "code_length": len(chosen),
            "digits": list(values),
            "glyphs": {str(value): str(value) for value in values},
            "labels": {str(value): spoken_number(value) for value in values},
            "clues": rows,
            "facts": [
                {"kind": clue.kind, "value": clue.value, "text": clue.sentence()}
                for clue in stated
            ],
            "clue_count": len(rows),
            "fact_count": len(stated),
        },
        solution={
            "code": list(chosen),
            "digit_facts": {
                str(value): {
                    "value": known[value].value,
                    "prime": known[value].prime,
                    "even": known[value].even,
                }
                for value in values
            },
            "space_size": 720 if len(chosen) == 3 else 5040,
            "after_properties": 720 if len(chosen) == 3 else 5040,
            "before_last": 4,
            "clue_count": len(rows),
            "fact_count": len(stated),
        },
        fact_refs=tuple(f"entity:{value}" for value in values),
        presentation={"optimal_attempts": 1, "layout": "grid", "instructions": "x"},
        candidate_id="hand-built",
    )


__all__ = [
    "CODE",
    "DIGITS",
    "GUESSES",
    "context",
    "digits",
    "entity",
    "facts",
    "make_puzzle",
    "result",
]
