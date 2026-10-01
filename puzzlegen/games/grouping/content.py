"""What game 1 asks the graph for, declaratively.

Requirements, not queries. The game says what it needs and the engine decides
how to satisfy it, which is what lets the query implementation change without
any game changing, and what stops a game pinning itself to one provider's idea
of a category.

Three requirements per day, and their shapes follow from the board:

* four visible groups of the day's size, from the lexical taxonomy
* one hidden group of the same size, from the overlay
* optionally, hidden groups whose members share a second overlay category too

Nothing here picks a board. Selection, decoy construction and the rule that
the hidden group takes one member from each visible group are assembly's job,
because they need the actual results in hand and this module runs before any
content has been fetched.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...content.query import ContentQuery, ContentRequirement, GroupingMode, Operation
from ...core.types import DifficultyBand
from .descriptor import GAME_ID, VISIBLE_GROUPS, group_size_for

#: Taxonomy the four visible groups are drawn from. The lexical import writes
#: here, so a visible group is always something a dictionary would recognise.
LEXICAL_TAXONOMY = "wordnet"

#: Taxonomy the hidden group is drawn from.
#:
#: "overlay" is a general storage mechanism, not a word-play mechanism: any
#: taxonomy holding categories that cut across a primary one, stored and
#: queried the same way as any other. This game uses it for lexical double
#: meanings because that is what makes a word grouping game's second axis
#: work; a number game's overlay might be "also a year", a symbol game's
#: might be "also a flag". The reusable part is the cross-cutting category
#: type, not wordplay.
OVERLAY_TAXONOMY = "overlay"

#: Names the engine resolves these requirements under. Constants because the
#: generator reads results back by name and a typo in either place would fail
#: at generation rather than here.
VISIBLE = "visible_groups"
HIDDEN = "hidden_groups"
HIDDEN_ENRICHED = "hidden_groups_two_axis"

#: How many candidate groups to ask for beyond the four a board needs.
#:
#: Generation discards most of what it is offered: groups whose members
#: overlap, groups too close to each other to tell apart, groups with no tile
#: the hidden group can borrow. Asking for four would mean any single
#: rejection fails the day, so the pool is sized to survive heavy filtering.
VISIBLE_POOL = 40

#: Candidate hidden groups. Far fewer, because the overlay is small by design
#: and a day only needs one, but more than one so a hidden group that cannot
#: take a tile from each visible group can be replaced rather than fatal.
HIDDEN_POOL = 200

#: Similarity floor inside a visible group.
#:
#: Members must be close enough that the group reads as a group once seen. Set
#: low rather than high: a strict floor produces four groups of near-synonyms,
#: which is a tidy board and a dull puzzle, and the real protection against
#: an unfair board is the uniqueness check rather than this number.
MINIMUM_GROUP_SIMILARITY = 0.35

#: Ceiling on how far apart the closest and furthest members sit.
#:
#: A group containing one obvious outlier is solvable by spotting the outlier
#: rather than by seeing the group, which is a different and worse puzzle.
MAXIMUM_SIMILARITY_SPREAD = 0.45

#: How many frequency bands a single group may span.
#:
#: One band of slack. A group mixing a very common word with an obscure one
#: gives the obscure one away by elimination, and the snapshot has plenty of
#: obscure words: 4,501 of 14,720 entities carry no frequency score at all.
MAXIMUM_FREQUENCY_SPREAD = 1

#: Confidence floor for anything reaching a board.
#:
#: Above the activation policy's 0.75, because activation asks whether content
#: may be used and this asks whether it should be shown to a player today.
MINIMUM_CONFIDENCE = 0.8

#: How the visible groups are drawn from the lexical taxonomy.
#:
#: Siblings, because that taxonomy is imported from WordNet, where a category
#: is one meaning and its direct members are that meaning's synonyms. Grouping
#: by a shared category there yields nine names for one periwinkle or five for
#: a pram, which is one tile repeated, not a group. Distinct children of one
#: parent are peers ("kinds of fish"), which is what a player can sort.
#:
#: This is a property of how the taxonomy was made, not of the game: a game
#: over a hand-authored taxonomy keeps the default shared-category rule.
VISIBLE_GROUPING = GroupingMode.SIBLINGS


def visible_group_query(
    *, group_size: int, difficulty_target: DifficultyBand, locale: str
) -> ContentQuery:
    """Candidate visible groups.

    ``difficulty_band`` rather than ``frequency_band``: the engine maps a
    difficulty target onto the frequency bands it considers appropriate, and a
    game hard-coding its own mapping would be second-guessing a decision that
    has to stay consistent across games for a difficulty label to mean
    anything.
    """
    return ContentQuery(
        operation=Operation.FIND_GROUPS,
        taxonomy=LEXICAL_TAXONOMY,
        grouping=VISIBLE_GROUPING,
        group_size=group_size,
        group_count=VISIBLE_GROUPS,
        minimum_similarity=MINIMUM_GROUP_SIMILARITY,
        maximum_similarity_spread=MAXIMUM_SIMILARITY_SPREAD,
        maximum_frequency_spread=MAXIMUM_FREQUENCY_SPREAD,
        # Every tile must also carry an overlay meaning. Without this the two
        # axes only meet by luck, and an overlay of 144 entities inside a
        # 14,720 entity graph is not lucky often: the first real day's visible
        # pool covered its best hidden group one member in five.
        intersects_taxonomy=OVERLAY_TAXONOMY,
        difficulty_band=difficulty_target,
        minimum_confidence=MINIMUM_CONFIDENCE,
        fresh_only=True,
        lang=locale,
        limit=VISIBLE_POOL,
    )


def hidden_group_query(*, group_size: int, locale: str) -> ContentQuery:
    """Candidate hidden groups, from the overlay.

    Two constraints from the visible query are deliberately dropped.

    No ``difficulty_band``: the hidden group is found by noticing a second
    meaning, not by knowing a rare word, and filtering it by frequency would
    throw away the overlay's best material. "Salmon" is a very common word and
    a perfectly hard tile to place.

    No similarity floor: this game's overlay members are not similar to each
    other in any embedding sense, which is the point of *this* overlay. A
    turtle and a walnut share a shell and nothing else, and a similarity gate
    would reject exactly the groups worth hiding. A different game's overlay
    might legitimately want a similarity floor; the constraint belongs to the
    query, not to the taxonomy.
    """
    return ContentQuery(
        operation=Operation.FIND_GROUPS,
        taxonomy=OVERLAY_TAXONOMY,
        group_size=group_size,
        group_count=1,
        minimum_confidence=MINIMUM_CONFIDENCE,
        fresh_only=True,
        lang=locale,
        limit=HIDDEN_POOL,
        # Every member must also be in the lexical taxonomy. A hidden group is
        # found by being borrowed from the visible ones, so a word the lexicon
        # does not have can never be on a board. Asked for as the first five
        # members of a category in alphabetical order, the offered groups were
        # mostly such words: a real day was handed "crane, dock, drill, drum,
        # ferry" while three of the five could not be placed.
        intersects_taxonomy=LEXICAL_TAXONOMY,
        minimum_intersecting_members=group_size,
    )


def enriched_hidden_group_query(*, group_size: int, locale: str) -> ContentQuery:
    """Hidden groups whose members share a second overlay category as well.

    ``FIND_INTERSECTING_GROUPS`` with no secondary category named, so the
    engine reports whatever second axis a group happens to share. A hidden
    group that is also, say, all kitchen things is richer material: it gives
    the hint system something true to say that is not the answer.

    Optional, and it has to be. The engine resolves a second axis within one
    taxonomy, so this can only find overlay-to-overlay intersections, and most
    overlay groups have none: of 144 seed members only six sit in two
    categories. Declaring it required would fail most days for a feature that
    is a bonus. The cross-axis intersection that matters, an overlay group
    meeting the four visible ones, is computed during assembly from the two
    result sets, because that is where the one-tile-per-visible-group rule
    lives anyway.
    """
    return ContentQuery(
        operation=Operation.FIND_INTERSECTING_GROUPS,
        taxonomy=OVERLAY_TAXONOMY,
        group_size=group_size,
        group_count=1,
        minimum_confidence=MINIMUM_CONFIDENCE,
        fresh_only=True,
        lang=locale,
        limit=HIDDEN_POOL,
        intersects_taxonomy=LEXICAL_TAXONOMY,
        minimum_intersecting_members=group_size,
    )


def content_requirements(
    *,
    difficulty_target: DifficultyBand,
    locale: str,
    day_key: str,
    salt: str = "",
) -> Sequence[ContentRequirement]:
    """This game's declared needs for one day.

    The day's group size is derived from the day key rather than passed in,
    because requirements are resolved before the engine builds a generation
    context and there is no RNG to hand yet. Deriving it from public inputs
    keeps the same number in the content layer, the generator and any later
    audit without any of them telling the others.

    Minimums are the point of a requirement. ``VISIBLE`` needs more than the
    four a board uses, because a pool of exactly four cannot survive a single
    rejection during selection, and failing here with "not enough content" is
    a far better day than failing during assembly with a half-built board.
    """
    group_size = group_size_for(day_key, salt)
    return (
        ContentRequirement(
            name=VISIBLE,
            query=visible_group_query(
                group_size=group_size,
                difficulty_target=difficulty_target,
                locale=locale,
            ),
            minimum=VISIBLE_GROUPS * 2,
        ),
        ContentRequirement(
            name=HIDDEN,
            query=hidden_group_query(group_size=group_size, locale=locale),
            minimum=1,
        ),
        ContentRequirement(
            name=HIDDEN_ENRICHED,
            query=enriched_hidden_group_query(group_size=group_size, locale=locale),
            minimum=1,
            optional=True,
        ),
    )


def describe_requirements(
    *, difficulty_target: DifficultyBand, locale: str, day_key: str
) -> dict[str, object]:
    """A summary for diagnostics, so a failed day can be read without a solver."""
    group_size = group_size_for(day_key)
    return {
        "game_id": GAME_ID,
        "day_key": day_key,
        "locale": locale,
        "difficulty_target": str(difficulty_target),
        "group_size": group_size,
        "board_size": group_size * VISIBLE_GROUPS,
        "hidden_group_size": group_size,
        "requirements": [
            {"name": r.name, "minimum": r.minimum, "optional": r.optional}
            for r in content_requirements(
                difficulty_target=difficulty_target, locale=locale, day_key=day_key
            )
        ],
    }


__all__ = [
    "HIDDEN",
    "HIDDEN_ENRICHED",
    "HIDDEN_POOL",
    "LEXICAL_TAXONOMY",
    "MAXIMUM_FREQUENCY_SPREAD",
    "MAXIMUM_SIMILARITY_SPREAD",
    "MINIMUM_CONFIDENCE",
    "MINIMUM_GROUP_SIMILARITY",
    "OVERLAY_TAXONOMY",
    "VISIBLE",
    "VISIBLE_POOL",
    "content_requirements",
    "describe_requirements",
    "enriched_hidden_group_query",
    "hidden_group_query",
    "visible_group_query",
]
