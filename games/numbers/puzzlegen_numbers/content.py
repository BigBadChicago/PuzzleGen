"""What game 4 asks the graph for, declaratively.

One query, and it is the first in the repository to use the content layer's
numeric vocabulary, because it is the first game whose content has a scale.

Two constraints do work here that no lexical game could have asked for.

The **value window** pins the day's numbers to one magnitude. Without it the
board gives its answer away by size -- a board of 7, 14, 21, 28 and 93 is
solved by noticing that one number is much larger -- and the digit count
becomes the easiest reading on every board. Asked of the graph rather than
applied afterwards, because a window applied after a limit is a window over an
arbitrary sample.

The **ordering** is why the window had to be a query constraint at all. The
service applies the limit, so a caller asking for numbers and sorting what
came back has sorted whichever ninety the pool happened to offer first, in
entity id order, which for numbers is lexicographic: 1, 10, 100, 11. The sort
has to happen where the whole pool is still visible, which is in the service.

Nothing else is constrained, and the absences are the same ones game three
records. No similarity, because a board never asks the player to see that the
numbers resemble each other. No frequency band, because the corpus frequency
of "47" is a fact about the string. No second axis, because a tile is a number
and means nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence

from puzzlegen.content.query import (
    ContentQuery,
    ContentRequirement,
    Operation,
    ResultOrder,
)
from puzzlegen.core.types import DifficultyBand

from .descriptor import (
    GAME_ID,
    WINDOW,
    board_size_for,
    board_sizes_for,
    tier_for,
)

#: The taxonomy the numbers come from.
#:
#: This game's own view of generated content. The same entities sit in a
#: ``digits`` taxonomy too where the ranges overlap, which is the shared graph
#: working on numbers exactly as it does on words: seven is one entity with
#: two memberships, not two sevens.
TAXONOMY = "numbers"

#: The root category of that taxonomy, which is every number in it.
ROOT = "number"

#: The predicate carrying a number's position on the scale.
#:
#: The same string the symbolic form subsystem reads an ordinal from, which is
#: not a coincidence: the window is a constraint on the value and the board's
#: order is a presentation of it, and a game using two different predicates
#: for those would sort a board by one number and filter it by another.
VALUE = "value"

#: Name the engine resolves this game's requirement under.
CANDIDATES = "numbers"

#: How many numbers to ask for.
#:
#: The window holds ninety, and the port caps a single query at two hundred,
#: so this asks for the whole window. The generator needs it whole: it picks
#: the day's property, then draws members and an outsider from everything the
#: window holds, and a sampled window would make which boards are possible
#: depend on which numbers the sample happened to include.
POOL = 200

#: Confidence floor for anything reaching a board.
#:
#: Above the activation policy's 0.75, as every game's is: activation asks
#: whether content may be used and this asks whether it should be shown
#: today. Generated numbers are asserted with certainty, so nothing here is
#: near the floor; what the floor is for is a number taxonomy somebody later
#: hand-edits.
MINIMUM_CONFIDENCE = 0.8

#: The fewest numbers a day can be built from.
#:
#: Thirty. The board needs at most seven of them, so this is not a board-size
#: floor: it is the floor at which the *vocabulary* is still a vocabulary. The
#: generator refuses a board whose property has too few members, and with a
#: window of thirty numbers the thin properties -- five Fibonacci numbers in
#: ninety, fewer in thirty -- stop being able to fill any board at all, so the
#: requirement failing is a clearer signal than a day of exhausted attempts.
MINIMUM_NUMBERS = 30


def number_query(*, locale: str) -> ContentQuery:
    """Every number in the day's window, in ascending order."""
    low, high = WINDOW
    return ContentQuery(
        operation=Operation.FIND_CATEGORY_MEMBERS,
        category=ROOT,
        taxonomy=TAXONOMY,
        predicate=VALUE,
        predicate_at_least=low,
        predicate_at_most=high,
        order=ResultOrder.PREDICATE_ASCENDING,
        minimum_confidence=MINIMUM_CONFIDENCE,
        fresh_only=True,
        lang=locale,
        limit=POOL,
    )


def content_requirements(
    *,
    difficulty_target: DifficultyBand,
    locale: str,
    day_key: str,
    salt: str = "",
) -> Sequence[ContentRequirement]:
    """This game's declared needs for one day.

    One requirement, required. Neither the band nor the day changes it: both
    decide which board is built from the answer, which is the generator's job,
    and a request that varied by band would make a day's content depend on
    something no later audit could reconstruct from the board.
    """
    return (
        ContentRequirement(
            name=CANDIDATES,
            query=number_query(locale=locale),
            minimum=MINIMUM_NUMBERS,
            optional=False,
        ),
    )


def describe_requirements(
    *, difficulty_target: DifficultyBand, locale: str, day_key: str
) -> dict[str, object]:
    """A summary for diagnostics, so a failed day can be read without a solver."""
    return {
        "game_id": GAME_ID,
        "day_key": day_key,
        "locale": locale,
        "difficulty_target": str(difficulty_target),
        "board_size": board_size_for(day_key),
        "board_sizes_tried": list(board_sizes_for(day_key)),
        "tier": tier_for(difficulty_target),
        "window": list(WINDOW),
        "taxonomy": TAXONOMY,
        "requirements": [
            {"name": r.name, "minimum": r.minimum, "optional": r.optional}
            for r in content_requirements(
                difficulty_target=difficulty_target, locale=locale, day_key=day_key
            )
        ],
    }


__all__ = [
    "CANDIDATES",
    "MINIMUM_CONFIDENCE",
    "MINIMUM_NUMBERS",
    "POOL",
    "ROOT",
    "TAXONOMY",
    "VALUE",
    "content_requirements",
    "describe_requirements",
    "number_query",
]
