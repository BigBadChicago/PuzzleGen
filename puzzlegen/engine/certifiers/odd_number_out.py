"""An independent check on an ``odd_number_out`` board.

The rules, from what the board shows and what the record stores:

* The board is a row of numbers. Exactly one of them is the answer.
* A tile is a legitimate odd one out iff there is some property that every
  *other* tile on the board has and that tile does not. The property comes
  from the graph's own category memberships, which the solution record
  carries; this check reads them and computes nothing about numbers itself.
  A certifier that decided for itself what "even" or "prime" meant would be
  judging the board against arithmetic rather than against the content the
  clue was made from.
* The board is correct iff exactly one tile is a legitimate odd one out and it
  is the stated answer, and the stated property is one of its reasons.

Enumeration is one pass per tile over the properties the other tiles share, so
a nine-tile board costs nine set intersections. Exhaustive, hence a uniqueness
proof rather than an answer check.

Nothing here imports ``puzzlegen_numbers``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..certify import AssuranceLevel, CertificateResult, Certifier
from ..coupling import EnumeratedSpace, Fact

GAME_ID = "odd_number_out"


class OddNumberOutCertifier:
    """Re-derives which tiles could be the odd one out, and how many."""

    game_id = GAME_ID
    assurance = AssuranceLevel.UNIQUENESS_PROVED

    def certify(
        self, payload: Mapping[str, Any], solution: Mapping[str, Any]
    ) -> CertificateResult:
        try:
            tiles = [str(tile["id"]) for tile in payload["tiles"]]
            answer = str(solution["answer"])
            stated_property = str(solution["property"])
            memberships = {
                str(tile): frozenset(str(name) for name in names)
                for tile, names in dict(solution["memberships"]).items()
            }
        except (KeyError, TypeError) as exc:
            return self._refuse(
                (f"the board is not readable as an odd_number_out board: {exc}",)
            )

        reasons: list[str] = []
        if len(tiles) < 3:
            reasons.append(
                f"a board of {len(tiles)} tiles cannot have an odd one out: "
                "two tiles share everything or nothing"
            )
        if len(set(tiles)) != len(tiles):
            reasons.append("the board shows the same tile twice")
        if answer not in tiles:
            reasons.append(f"the stated answer {answer!r} is not a tile on the board")
        absent = sorted(set(tiles) - set(memberships))
        if absent:
            reasons.append(
                f"no recorded properties for tiles {absent}, so whether they "
                "share anything cannot be judged"
            )
        if reasons:
            return self._refuse(tuple(reasons))

        # One candidate per tile: the properties every other tile carries and
        # this one does not.
        candidates: dict[str, frozenset[str]] = {}
        examined = 0
        for tile in tiles:
            examined += 1
            others = [memberships[other] for other in tiles if other != tile]
            shared = frozenset.intersection(*others) if others else frozenset()
            distinguishing = shared - memberships[tile]
            if distinguishing:
                candidates[tile] = distinguishing

        count = len(candidates)
        if count == 0:
            reasons.append(
                "no tile is distinguished by a property the others all share; "
                "the board has no odd one out"
            )
        elif count > 1:
            reasons.append(
                f"{count} tiles could each be the odd one out "
                f"({sorted(candidates)}), so the answer is not unique"
            )
        elif answer not in candidates:
            reasons.append(
                f"the one tile that could be the odd one out is "
                f"{next(iter(candidates))!r}, not the stated {answer!r}"
            )
        elif stated_property not in candidates[answer]:
            # Not a uniqueness failure: the answer is right and the board is
            # playable, but the reason the record gives is not a reason, which
            # is a defect in the record a reviewer should see.
            reasons.append(
                f"the stated property {stated_property!r} is not one the other "
                f"tiles share and {answer!r} lacks; its actual reasons are "
                f"{sorted(candidates[answer])}"
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
                f"{examined} tiles examined, exactly one is distinguished and "
                f"it is the stated answer on {stated_property!r}"
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
    return OddNumberOutCertifier()


#: The one predicate this game's answers assert. Namespaced for the same
#: reason as every other: "is the odd one out" is this game's claim and no
#: other game's, however similar another game's wording.
IS_ODD_ONE_OUT = "odd_number_out/is_odd_one_out"


def _admits(answer: str, fact: Fact) -> bool:
    return (fact.ref == answer) if fact.value else (fact.ref != answer)


def space(
    payload: Mapping[str, Any], solution: Mapping[str, Any] | None = None
) -> EnumeratedSpace | None:
    """Every tile this board could legitimately name as the odd one out.

    The space is the tiles distinguished by some property the others share,
    which is the same derivation the certifier runs. Deriving it from the
    rules rather than from the tile list matters: a board's space is not
    "every tile", it is every tile the rules admit, and a leak measured
    against the wrong denominator is the wrong number.
    """
    try:
        tiles = [str(tile["id"]) for tile in payload["tiles"]]
        memberships = {
            str(tile): frozenset(str(name) for name in names)
            for tile, names in dict(solution or {}).get("memberships", {}).items()
        }
    except (KeyError, TypeError):
        return None
    if len(tiles) < 3 or set(tiles) - set(memberships):
        # Without the recorded memberships the admissible set cannot be
        # derived, and guessing "every tile" would overstate the space.
        return None

    answers = tuple(
        tile
        for tile in tiles
        if frozenset.intersection(
            *[memberships[other] for other in tiles if other != tile]
        )
        - memberships[tile]
    )
    revealed: tuple[Fact, ...] = ()
    if solution is not None and "answer" in solution:
        answer = str(solution["answer"])
        revealed = tuple(
            Fact(predicate=IS_ODD_ONE_OUT, ref=tile, value=tile == answer)
            for tile in sorted(tiles)
        )
    return EnumeratedSpace(
        answers=answers,
        admits=_admits,
        revealed=revealed,
        predicates=frozenset({IS_ODD_ONE_OUT}),
    )


__all__ = [
    "GAME_ID",
    "IS_ODD_ONE_OUT",
    "OddNumberOutCertifier",
    "certifier",
    "space",
]
