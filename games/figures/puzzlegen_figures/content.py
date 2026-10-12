"""What game 3 asks the graph for, declaratively.

One requirement: every figure in this game's taxonomy. That is the whole
request, and its shortness is the interesting part of this module.

Game one asks for groups constrained by embedding similarity, frequency
spread, a crosscutting second axis and a covering set. Game two asks for
groups of a size with members in a frequency band. This game asks for members
of a category and applies no gate at all, because none of the service's gates
is about anything a figure has. A figure has no corpus frequency: nobody
writes "two solid red triangles" often enough for a frequency table to hold
it, and the number that came back would be a fact about the English phrase
rather than about the figure. It has no useful embedding either, for the same
reason: a vector over its name measures the words.

What the gates would have measured, this game measures itself, on the
attributes. Whether a board is fair is whether its triples behave, which is
arithmetic over four attribute readings and not a semantic question the
content service could have been asked. So the request is plain and the work
is in the generator, which is the right division: the service decides what
content may be used, the game decides which of it makes a board.
"""

from __future__ import annotations

from collections.abc import Sequence

from puzzlegen.content.query import ContentQuery, ContentRequirement, Operation
from puzzlegen.core.types import DifficultyBand

from .descriptor import GAME_ID, band_target, board_size_for, board_sizes_for

#: The taxonomy the figures come from.
#:
#: This game's own, as game two's is. The figures are generated content: a
#: provider builds the attribute space, and the taxonomy it imports under is
#: this name. Category identity includes the taxonomy, so a ``square shape``
#: category here is unrelated to any lexical ``square``, which is exactly the
#: isolation a non-lexical game needs from a graph that also holds words.
TAXONOMY = "figures"

#: The root category of that taxonomy, which is every figure in it.
#:
#: Asked for by name rather than by id, because a game should not have to know
#: internal ids, and the provider's root name is part of its declaration.
ROOT = "figure"

#: Name the engine resolves this game's requirement under.
CANDIDATES = "figures"

#: How many figures to ask for.
#:
#: The default space holds eighty-one and the port caps a single query at two
#: hundred, so this asks for the cap and receives the whole space. That is
#: deliberate rather than lazy: the generator needs the attribute space to be
#: complete, because :func:`trio.completion` computes the figure that finishes
#: a trio and a board seeded from a partial space would be asking for figures
#: the day was never shown. A build whose figure taxonomy outgrew the cap
#: would need this game to sample the space instead, which is a real change
#: and not a constant.
POOL = 200

#: Confidence floor for anything reaching a board.
#:
#: Above the activation policy's 0.75 for the same reason game two is:
#: activation asks whether content may be used and this asks whether it
#: should be shown today. Generated figures are asserted with certainty, so
#: nothing here is near the floor, which is the point of having one anyway --
#: a figure taxonomy somebody later hand-edits would be held to it.
MINIMUM_CONFIDENCE = 0.8

#: The smallest figure space a board can be built in.
#:
#: Twenty-seven, which is the size of the space when one attribute is held
#: constant. Below it the generator has too little room to place fillers that
#: complete nothing, and the requirement failing is a clearer signal than a
#: day of exhausted attempts.
MINIMUM_FIGURES = 27


def figure_query(*, locale: str) -> ContentQuery:
    """Every figure in this game's taxonomy.

    ``taxonomy_depth`` is left unset, which takes the whole subtree below the
    root rather than one level of it. The provider puts each figure under one
    category per attribute, two levels below the root, so a depth of one would
    return the attribute categories' own members and miss every figure.
    """
    return ContentQuery(
        operation=Operation.FIND_CATEGORY_MEMBERS,
        category=ROOT,
        taxonomy=TAXONOMY,
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

    One requirement, required rather than optional, with a minimum that is a
    real floor rather than one. Game two declares a requirement per board size
    because its content can serve one size and not another; this game's
    content is the same space whatever the board size, so asking for it twice
    would be asking the same question twice.

    Neither the difficulty target nor the day key changes the request. They
    decide which board is built from the answer, which is the generator's job,
    and a requirement that varied by band would make a day's content depend on
    something no later audit could reconstruct from the board.
    """
    return (
        ContentRequirement(
            name=CANDIDATES,
            query=figure_query(locale=locale),
            minimum=MINIMUM_FIGURES,
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
        "band_target": list(band_target(difficulty_target)),
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
    "MINIMUM_FIGURES",
    "POOL",
    "ROOT",
    "TAXONOMY",
    "content_requirements",
    "describe_requirements",
    "figure_query",
]
