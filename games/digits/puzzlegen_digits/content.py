"""What game 5 asks the graph for, declaratively.

The shortest request in the repository, and the one whose shortness says the
most: ten digits. A board needs every one of them, because a clue may name any
digit and the solver has to be able to rule each of them in or out.

What makes this a content request at all rather than a constant is the facts
the digits carry. The property clues state how many of a code's digits are
prime and how many are even, and those come from the digit entities'
``is_prime`` and ``parity`` facts rather than from arithmetic in this package.
A build whose digits taxonomy said something different would produce different
clues, and -- this is the part that matters -- would produce them consistently:
the clue a player reads, the elimination the generator did and the enumeration
the verifier ran would all agree, because all three read the same facts.

The alternative, computing primality in the game, is one line shorter and
wrong in a way that would be hard to find: the clue would say one thing and
the content another, and the board would be unsolvable from what it shows.
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

from .descriptor import GAME_ID, code_length_for, code_lengths_for, properties_for

#: The taxonomy the digits come from.
#:
#: Its own, even though the same ten entities also sit in a ``numbers``
#: taxonomy where the ranges overlap. That overlap is the shared graph working
#: as designed -- seven is one entity with two memberships -- and the separate
#: taxonomy is what lets this game ask for exactly the ten digits rather than
#: for the numbers under ten, which is a different question with the same
#: answer only by coincidence of this build's ranges.
TAXONOMY = "digits"

#: The root category of that taxonomy, which is every digit in it.
ROOT = "digit"

#: The predicate carrying a digit's value.
VALUE = "value"

#: Name the engine resolves this game's requirement under.
CANDIDATES = "digits"

#: How many digits to ask for.
#:
#: Twenty, for ten digits. The slack is not caution about the content: the
#: limit is a ceiling the port applies and asking for exactly ten would make
#: a build with an eleventh digit silently serve a board missing one. Asking
#: for more than exist costs nothing and makes the shortage visible in the
#: requirement rather than invisible in the board.
POOL = 20

#: Confidence floor for anything reaching a board.
MINIMUM_CONFIDENCE = 0.8

#: The fewest digits a board can be built from.
#:
#: Ten. Not a board-size floor but a completeness floor: a clue may name any
#: digit, so a solver ruling digits in and out needs all of them present. A
#: build with nine digits would make every board's space smaller in a way the
#: clue language does not say out loud, which is a different game rather than
#: a thinner one.
MINIMUM_DIGITS = 10


def digit_query(*, locale: str) -> ContentQuery:
    """Every digit in this game's taxonomy, in ascending order.

    Ordered, which looks unnecessary for ten entities and is not. The
    generator builds the whole code space from the digits it is given, and
    that space is what the verifier enumerates; built from an id-ordered pool
    the space would be the same set in a different order, and every board's
    candidate id -- a hash over what the generator produced -- would move when
    the service's id derivation changed. Asking the graph to order them makes
    the space a function of the digits' values.
    """
    return ContentQuery(
        operation=Operation.FIND_CATEGORY_MEMBERS,
        category=ROOT,
        taxonomy=TAXONOMY,
        predicate=VALUE,
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

    One requirement, required. The band decides which facts the board states
    and the day decides the code length, and neither changes what is asked
    for: both are choices made from the answer, which is the generator's job.
    """
    return (
        ContentRequirement(
            name=CANDIDATES,
            query=digit_query(locale=locale),
            minimum=MINIMUM_DIGITS,
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
        "code_length": code_length_for(day_key),
        "code_lengths_tried": list(code_lengths_for(day_key)),
        "properties": list(properties_for(difficulty_target)),
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
    "MINIMUM_DIGITS",
    "POOL",
    "ROOT",
    "TAXONOMY",
    "VALUE",
    "content_requirements",
    "describe_requirements",
    "digit_query",
]
