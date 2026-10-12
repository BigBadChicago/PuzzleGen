"""Figures, content results and boards, built by hand.

Synthetic rather than drawn from a snapshot, so a failing test points at the
game rather than at whichever figures the build happened to hold. The tests
that run against real content say so in their own names.

The builders mirror the two shapes this game works in. :func:`result` builds
what the content service would have returned, for the generator.
:func:`make_puzzle` builds an assembled board directly, for verification,
difficulty and play, which each need boards of exact shapes the generator
would refuse to produce.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import product

from puzzlegen.content.query import ContentResult, EntityView, Operation
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import DifficultyBand
from puzzlegen.engine.plugin import GenerationContext, Puzzle
from puzzlegen_figures import content as content_module
from puzzlegen_figures.descriptor import GAME_ID
from puzzlegen_figures.trio import ATTRIBUTES

#: The attribute space the builders work in, matching the provider's default.
SPACE: dict[str, tuple[str, ...]] = {
    "shape": ("triangle", "circle", "square"),
    "fill": ("solid", "hollow", "striped"),
    "count": ("one", "two", "three"),
    "colour": ("red", "green", "blue"),
}

#: A glyph per shape and fill, short and distinct, as the provider supplies.
GLYPHS = {
    ("triangle", "solid"): "▲",
    ("triangle", "hollow"): "△",
    ("triangle", "striped"): "◮",
    ("circle", "solid"): "●",
    ("circle", "hollow"): "○",
    ("circle", "striped"): "◍",
    ("square", "solid"): "■",
    ("square", "hollow"): "□",
    ("square", "striped"): "▨",
}


def figure(shape: str, fill: str, count: str, colour: str) -> dict[str, str]:
    return {"shape": shape, "fill": fill, "count": count, "colour": colour}


def name_of(reading: Mapping[str, str]) -> str:
    return "-".join(reading[attribute] for attribute in ATTRIBUTES)


def entity(reading: Mapping[str, str]) -> EntityView:
    """One figure, as the content service would return it.

    Every attribute is a fact and a membership both, as the provider emits
    them, because the generator reads the facts and the dependency ledger
    reads the category ids: a helper supplying only one of them would hide a
    confusion between the two.
    """
    name = name_of(reading)
    return EntityView(
        entity_id=f"entity:{name}",
        name=name,
        properties={
            "glyph": GLYPHS[(reading["shape"], reading["fill"])],
            "spoken": name.replace("-", " "),
            "symbol_kind": "FIGURE",
            **dict(reading),
        },
        types=tuple(f"{reading[a]} {a}" for a in ATTRIBUTES),
        type_ids=tuple(f"category:{reading[a]}-{a}" for a in ATTRIBUTES),
    )


def whole_space() -> list[EntityView]:
    """Every figure in :data:`SPACE`, which is eighty-one of them."""
    return [
        entity(dict(zip(ATTRIBUTES, values)))
        for values in product(*(SPACE[a] for a in ATTRIBUTES))
    ]


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


#: Three figures that are a trio: all different on shape, all the same on the
#: rest. The worked example every test about the rule starts from.
TRIO_READINGS = (
    figure("triangle", "solid", "one", "red"),
    figure("circle", "solid", "one", "red"),
    figure("square", "solid", "one", "red"),
)

#: Six figures that complete no trio with each other or with
#: :data:`TRIO_READINGS`, so a nine-tile board of the two together holds
#: exactly one trio.
#:
#: Computed rather than hand-picked, which is worth recording because the
#: hand-picked version was wrong. The first draft chose six figures that
#: looked unrelated and one of them closed a second trio with a member of
#: ``TRIO_READINGS``: three circles whose fills, counts and colours were all
#: different. "Looks unrelated" is not a property the rule knows about, so
#: these were produced by the same construction the generator uses, taking the
#: first six figures in declaration order that complete nothing already down.
#:
#: ``test_figures_boards.py`` asserts the property on the built board, so a
#: later edit here that broke it fails loudly rather than quietly making
#: other tests pass for the wrong reason.
INERT_READINGS = (
    figure("triangle", "solid", "one", "green"),
    figure("triangle", "solid", "two", "red"),
    figure("triangle", "solid", "two", "green"),
    figure("triangle", "hollow", "one", "red"),
    figure("triangle", "hollow", "one", "green"),
    figure("triangle", "hollow", "two", "red"),
)

#: Three more that keep the property at twelve tiles.
WIDER_READINGS = (
    figure("triangle", "hollow", "two", "green"),
    figure("circle", "solid", "one", "green"),
    figure("circle", "solid", "two", "red"),
)


def make_puzzle(
    readings: Sequence[Mapping[str, str]] = (),
    *,
    trio: Sequence[int] = (0, 1, 2),
    board_size: int | None = None,
    columns: int = 3,
    varying: Sequence[str] | None = None,
    near_misses: int = 0,
    decoy_share: float = 0.0,
) -> Puzzle:
    """An assembled board, built directly rather than through assembly.

    Takes the figures in board order and the indices of the trio, so a test
    can place the answer anywhere on the grid, which is what the dispersion
    feature and the hint tests need and what generation would never produce
    on demand.
    """
    chosen = list(readings) or [*TRIO_READINGS, *INERT_READINGS]
    size = board_size or len(chosen)
    tiles = [
        {
            "id": f"entity:{name_of(reading)}",
            "glyph": GLYPHS[(reading["shape"], reading["fill"])],
            "label": name_of(reading).replace("-", " "),
            **dict(reading),
        }
        for reading in chosen
    ]
    answer = sorted(tiles[index]["id"] for index in trio)
    if varying is None:
        varying = sorted(
            attribute
            for attribute in ATTRIBUTES
            if len({chosen[index][attribute] for index in trio}) == 3
        )
    return Puzzle(
        game_id=GAME_ID,
        payload={
            "game_id": GAME_ID,
            "board_size": size,
            "columns": columns,
            "rows": max(1, size // columns),
            "tiles": tiles,
            "trio_size": 3,
            "attributes": list(ATTRIBUTES),
            "space": {a: list(v) for a, v in sorted(SPACE.items())},
        },
        solution={
            "trio": answer,
            "varying": sorted(varying),
            "near_misses": near_misses,
            "decoy_share": decoy_share,
            "figures": {
                tile["id"]: {a: tile[a] for a in ATTRIBUTES} for tile in tiles
            },
        },
        fact_refs=tuple(tile["id"] for tile in tiles),
        presentation={"optimal_attempts": 1, "columns": columns, "instructions": "x"},
        candidate_id="hand-built",
    )


__all__ = [
    "GLYPHS",
    "INERT_READINGS",
    "SPACE",
    "TRIO_READINGS",
    "WIDER_READINGS",
    "context",
    "entity",
    "figure",
    "make_puzzle",
    "name_of",
    "result",
    "whole_space",
]
