"""What game 2 asks the graph for, declaratively.

One requirement per board size, and each asks the same thing: categories of
this game's own taxonomy together with enough frequency-passing members to
fill a board. A candidate board is one of those groups; the category's name is
the answer and its members are the tiles.

That is the whole request, which is worth stating because the game's hard
constraint is not expressible here. Whether a category's letters can be
distributed one per tile over members that contain them is a character-level
question, the content service has no character-level index, and section 2.3
settles that it should not grow one. So the letters are checked in the
generator, over members fetched through the ordinary member queries, and this
module asks only for the members.

Nothing here picks a board. Which category a day uses, which of its members
become tiles, and which letters go unmarked are generation's job, because they
need the actual results in hand and this module runs before any content has
been fetched.
"""

from __future__ import annotations

from collections.abc import Sequence

from puzzlegen.content.query import (
    ContentQuery,
    ContentRequirement,
    GroupingMode,
    Operation,
)
from puzzlegen.core.types import DifficultyBand

from .descriptor import GAME_ID, blanks_for, board_size_for, board_sizes_for

#: The taxonomy the targets come from.
#:
#: This game's own, and section 6 is the measurement that put it there: at
#: medium frequency exactly one WordNet category in 7,050 had six or more
#: eligible members, because a lexical category's members are its hyponyms and
#: a frequency filter cuts across that structure rather than along it. A board
#: needs a set organised by what a player knows, which no import can infer.
#:
#: Category identity already includes the taxonomy, so these collide with
#: neither ``wordnet`` nor ``overlay``, and a member the graph already holds is
#: the same entity with one more membership rather than a duplicate.
TAXONOMY = "letters"

#: Name the engine resolves this game's requirement under. A constant because
#: the generator reads results back by name and a typo in either place would
#: fail at generation rather than here.
CANDIDATES = "letter_categories"

#: How many candidate groups to ask for per board size.
#:
#: A group is one category and one subset of its members, and the two are not
#: interchangeable: a category whose name needs an ``f`` is playable with the
#: subset holding its ``fig`` and refused with the subset that does not. The
#: service spreads its groups across categories and within each category, so a
#: pool this size offers the thirty curated categories several subsets each,
#: which is what lets the letters constraint be met by choosing rather than by
#: luck.
POOL = 240

#: How the groups are formed.
#:
#: ``SHARED_CATEGORY``, which is the default and the right rule for a
#: hand-authored taxonomy: a category here is a thing a player recognises and
#: its direct members are examples of it, so members of one category are
#: exactly the tiles a board wants. Game 1 needs ``SIBLINGS`` because its
#: taxonomy is derived from WordNet, where direct members are synonyms of one
#: meaning; nothing in that applies to a curated file.
GROUPING = GroupingMode.SHARED_CATEGORY

#: Confidence floor for anything reaching a board. Above the activation
#: policy's 0.75, because activation asks whether content may be used and this
#: asks whether it should be shown to a player today.
MINIMUM_CONFIDENCE = 0.8


def category_query(
    *, board_size: int, difficulty_target: DifficultyBand, locale: str
) -> ContentQuery:
    """Candidate categories with enough members to fill a board of this size.

    ``difficulty_band`` rather than ``frequency_band``: the engine maps a
    difficulty target onto the frequency bands it considers appropriate, and a
    game hard-coding its own mapping would second-guess a decision that has to
    stay consistent across games for a difficulty label to mean anything.

    Three constraints game 1 applies are deliberately absent.

    No similarity floor and no centroid gate. A board never asks the player to
    see that the tiles resemble each other in an embedding sense; it asks them
    to recognise the category and spell it. The members are curated for being
    the common words of that category, and a similarity gate over them would
    refuse a set chosen for exactly the right reason -- ``polo`` is in
    ``sport`` because ``sport`` needs a ``p``, and no embedding will like it.

    No frequency spread ceiling. A single band is already the filter here: the
    tiles are drawn from one curated category whose members were chosen for
    being common, so a spread ceiling would only refuse the band's own edges.

    And no covering or intersecting taxonomy. This game has one axis. A tile
    means one thing, the category, and the second meaning game 1's hidden
    group rests on has no counterpart here.
    """
    return ContentQuery(
        operation=Operation.FIND_GROUPS,
        taxonomy=TAXONOMY,
        grouping=GROUPING,
        group_size=board_size,
        group_count=1,
        difficulty_band=difficulty_target,
        minimum_confidence=MINIMUM_CONFIDENCE,
        fresh_only=True,
        lang=locale,
        limit=POOL,
    )


def sized(name: str, board_size: int) -> str:
    """The requirement name for one size, since a day asks for several."""
    return f"{name}:{board_size}"


def content_requirements(
    *,
    difficulty_target: DifficultyBand,
    locale: str,
    day_key: str,
    salt: str = "",
) -> Sequence[ContentRequirement]:
    """This game's declared needs for one day.

    The day's board size is derived from the day key rather than passed in,
    because requirements are resolved before the engine builds a generation
    context and there is no RNG to hand yet.

    Every size is optional, the day's own included. A size is a preference:
    generation offers candidates from all of them in preference order, so
    whether the day can be served is decided there, across the whole set, and
    no single size's content can decide it alone. Marking the preferred size
    required would fail a day that has a perfectly good board one size down,
    which is the whole reason the preference is an order rather than a choice.
    A day where nothing can be served fails on candidates instead, and
    ``generate.unusable_reason`` supplies the narrative a curator acts on.
    """
    return tuple(
        ContentRequirement(
            name=sized(CANDIDATES, board_size),
            query=category_query(
                board_size=board_size,
                difficulty_target=difficulty_target,
                locale=locale,
            ),
            minimum=1,
            optional=True,
        )
        for board_size in board_sizes_for(day_key, salt)
    )


def describe_requirements(
    *, difficulty_target: DifficultyBand, locale: str, day_key: str
) -> dict[str, object]:
    """A summary for diagnostics, so a failed day can be read without a solver."""
    board_size = board_size_for(day_key)
    return {
        "game_id": GAME_ID,
        "day_key": day_key,
        "locale": locale,
        "difficulty_target": str(difficulty_target),
        "board_size": board_size,
        "board_sizes_tried": list(board_sizes_for(day_key)),
        "blanks": blanks_for(difficulty_target),
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
    "GROUPING",
    "MINIMUM_CONFIDENCE",
    "POOL",
    "TAXONOMY",
    "category_query",
    "content_requirements",
    "describe_requirements",
    "sized",
]
