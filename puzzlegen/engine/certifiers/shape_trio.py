"""An independent check on a ``shape_trio`` board.

The rules, read off the board and nothing else:

* Each tile carries a value for every attribute the payload names.
* Three tiles are a trio iff, for each attribute, their three values are
  either all the same or all different. No attribute is privileged and none is
  exempt.
* The board is correct iff exactly one trio exists among the tiles and it is
  the stated answer.

This is the one certifier that needs nothing from the solution record except
the answer: the attribute readings are in the payload, because the player has
to be able to read them. A twelve-tile board is 220 combinations and a
nine-tile board is 84, so the enumeration is exhaustive and the certifier
proves uniqueness.

Nothing here imports ``puzzlegen_figures``. In particular it does not import
``trio.py``, which holds this same rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from ..certify import AssuranceLevel, CertificateResult, Certifier
from ..coupling import EnumeratedSpace, Fact

GAME_ID = "shape_trio"

#: Tiles in a trio. From the payload where the board states it, and three
#: otherwise, because that is what the rule above is about.
DEFAULT_TRIO = 3


def _is_trio(
    readings: Sequence[Mapping[str, Any]], attributes: Sequence[str]
) -> bool:
    """All-same or all-different, on every attribute."""
    for attribute in attributes:
        values = [reading[attribute] for reading in readings]
        distinct = len(set(values))
        if distinct != 1 and distinct != len(values):
            return False
    return True


class ShapeTrioCertifier:
    """Enumerates every combination of tiles and counts the valid trios."""

    game_id = GAME_ID
    assurance = AssuranceLevel.UNIQUENESS_PROVED

    def certify(
        self, payload: Mapping[str, Any], solution: Mapping[str, Any]
    ) -> CertificateResult:
        try:
            attributes = [str(name) for name in payload["attributes"]]
            size = int(payload.get("trio_size", DEFAULT_TRIO))
            tiles = {
                str(tile["id"]): dict(tile) for tile in payload["tiles"]
            }
            answer = frozenset(str(tile) for tile in solution["trio"])
        except (KeyError, TypeError, ValueError) as exc:
            return self._refuse(
                (f"the board is not readable as a shape_trio board: {exc}",)
            )

        reasons: list[str] = []
        if not attributes:
            reasons.append(
                "the board names no attributes, so every three tiles are "
                "trivially a trio"
            )
        if size < 2:
            reasons.append(f"a trio of {size} tiles is not a trio")
        if len(tiles) < size:
            reasons.append(
                f"{len(tiles)} tiles cannot contain a trio of {size}"
            )
        if len(answer) != size:
            reasons.append(
                f"the stated answer names {len(answer)} distinct tiles, the "
                f"board's trio is {size}"
            )
        outside = sorted(answer - set(tiles))
        if outside:
            reasons.append(
                f"the stated answer names tiles not on the board: {outside}"
            )
        incomplete = sorted(
            tile_id
            for tile_id, tile in tiles.items()
            if any(attribute not in tile for attribute in attributes)
        )
        if incomplete:
            reasons.append(
                f"tiles {incomplete} do not carry every named attribute, so "
                "the rule cannot be applied to them"
            )
        if reasons:
            return self._refuse(tuple(reasons))

        found: list[frozenset[str]] = []
        examined = 0
        for combination in combinations(sorted(tiles), size):
            examined += 1
            if _is_trio([tiles[tile_id] for tile_id in combination], attributes):
                found.append(frozenset(combination))

        count = len(found)
        if count == 0:
            reasons.append(
                "no combination of tiles satisfies the rule on every "
                "attribute; the board is unsolvable"
            )
        elif count > 1:
            reasons.append(
                f"{count} combinations satisfy the rule, so the answer is not "
                "unique"
            )
        elif found[0] != answer:
            reasons.append(
                "the one combination satisfying the rule is "
                f"{sorted(found[0])}, not the stated {sorted(answer)}"
            )

        if reasons:
            return CertificateResult(
                game_id=GAME_ID,
                accepted=False,
                assurance=self.assurance,
                solution_count=count,
                states_examined=examined,
                reasons=tuple(reasons),
            )
        return CertificateResult(
            game_id=GAME_ID,
            accepted=True,
            assurance=self.assurance,
            solution_count=count,
            states_examined=examined,
            detail=(
                f"{examined} combinations enumerated over {len(attributes)} "
                "attributes, one satisfies the rule and it is the stated answer"
            ),
        )

    def _refuse(self, reasons: tuple[str, ...]) -> CertificateResult:
        return CertificateResult(
            game_id=GAME_ID,
            accepted=False,
            assurance=self.assurance,
            solution_count=None,
            reasons=reasons,
        )


def certifier() -> Certifier:
    return ShapeTrioCertifier()


def _admits(answer: frozenset[str], fact: Fact) -> bool:
    """Whether a candidate trio is still possible given one fact.

    ``value`` true means the named tile is in the answer, false means it is
    not. Both directions matter: another board ruling a tile out narrows this
    one as surely as ruling it in.
    """
    return (fact.ref in answer) if fact.value else (fact.ref not in answer)


def space(
    payload: Mapping[str, Any], solution: Mapping[str, Any] | None = None
) -> EnumeratedSpace | None:
    """Every trio this board admits, with the facts solving it reveals.

    The tiles are graph entity ids, so the facts are in the shared vocabulary
    and a figure against another entity-named board means something.

    ``solution`` is optional: without it the space is the board's whole
    answer set and reveals nothing, which is what a target board needs. With
    it the space also carries the facts its answer gives away, which is what
    a source board needs.
    """
    try:
        attributes = [str(name) for name in payload["attributes"]]
        size = int(payload.get("trio_size", DEFAULT_TRIO))
        tiles = {str(tile["id"]): dict(tile) for tile in payload["tiles"]}
    except (KeyError, TypeError, ValueError):
        return None
    if not attributes or size < 2 or len(tiles) < size:
        return None
    if any(
        attribute not in tile
        for tile in tiles.values()
        for attribute in attributes
    ):
        return None

    answers = tuple(
        frozenset(combination)
        for combination in combinations(sorted(tiles), size)
        if _is_trio([tiles[tile_id] for tile_id in combination], attributes)
    )
    revealed: tuple[Fact, ...] = ()
    if solution is not None and "trio" in solution:
        answer = {str(tile) for tile in solution["trio"]}
        # Every tile on the board, in or out. A board whose answer is known
        # tells you about the tiles it excludes as well as the ones it names.
        revealed = tuple(
            Fact(ref=tile_id, value=tile_id in answer)
            for tile_id in sorted(tiles)
        )
    return EnumeratedSpace(answers=answers, admits=_admits, revealed=revealed)


__all__ = [
    "GAME_ID",
    "DEFAULT_TRIO",
    "ShapeTrioCertifier",
    "certifier",
    "space",
]
