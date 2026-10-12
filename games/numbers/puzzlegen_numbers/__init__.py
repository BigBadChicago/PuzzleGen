"""Game 4: a row of numbers, and the one that does not belong.

Split by what checks it rather than by convenience. The descriptor is read by
the registry and the accessibility gate, the content requirements by the
content port, the properties module by nothing but this game's own judgement
about its vocabulary, and each later module by a different engine gate, so a
change to one gate touches one file.

This is the first game whose content has a scale, and the first to use the
content layer's numeric vocabulary: the day's numbers come from the graph
already windowed to one magnitude and already in ascending order, because both
had to be constraints on the query rather than work done afterwards.
"""

from .assemble import assemble, board_memberships, board_tiles, board_values
from .content import content_requirements, describe_requirements
from .descriptor import (
    BOARD_SIZES,
    DESCRIPTOR,
    GAME_ID,
    GAME_VERSION,
    SEARCH_BOUND,
    TIER_BY_BAND,
    WINDOW,
    board_size_for,
    board_sizes_for,
    build_descriptor,
    tier_for,
)
from .difficulty import measure_difficulty
from .generate import BoardPlan, generate_candidates, last_rejections, unusable_reason
from .play import (
    create_share_artifact,
    get_hint,
    grade_move,
    render,
    score,
)
from .plugin import OddNumberOutGame
from .properties import (
    MAX_TIER,
    MIN_TIER,
    TIERS,
    holds_for_all_but,
    near_readings_of,
    obscurity_of,
    readings_of,
    tier_of,
    vocabulary_of,
)
from .verify import verify


def build_plugin(*, salt: str = "") -> OddNumberOutGame:
    """What the ``puzzlegen.games`` entry point resolves to.

    A factory rather than an instance, so the registry decides when the game
    is constructed and can pass a salt without this module holding process
    state. Installing this distribution is the whole of adding the game;
    nothing in the platform names it.
    """
    return OddNumberOutGame(salt=salt)


__all__ = [
    "BOARD_SIZES",
    "DESCRIPTOR",
    "GAME_ID",
    "GAME_VERSION",
    "MAX_TIER",
    "MIN_TIER",
    "SEARCH_BOUND",
    "TIERS",
    "TIER_BY_BAND",
    "WINDOW",
    "BoardPlan",
    "OddNumberOutGame",
    "assemble",
    "board_memberships",
    "board_size_for",
    "board_sizes_for",
    "board_tiles",
    "board_values",
    "build_descriptor",
    "build_plugin",
    "content_requirements",
    "create_share_artifact",
    "describe_requirements",
    "generate_candidates",
    "get_hint",
    "grade_move",
    "holds_for_all_but",
    "last_rejections",
    "measure_difficulty",
    "near_readings_of",
    "obscurity_of",
    "readings_of",
    "render",
    "score",
    "tier_for",
    "tier_of",
    "unusable_reason",
    "verify",
    "vocabulary_of",
]
