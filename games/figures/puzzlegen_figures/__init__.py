"""Game 3: one trio of simple figures, hidden among figures that nearly are.

Split by what checks it rather than by convenience. The descriptor is read by
the registry and the accessibility gate, the content requirements by the
content port, the trio module by nothing but this game's own attribute
algebra, and each later module by a different engine gate, so a change to one
gate touches one file.

This is the platform's first game over content that is not words. Nothing here
spells anything, nothing reads a lexicon, and the tiles carry a glyph and a
spoken label supplied by the graph rather than a name a client prints.
"""

from .assemble import assemble, board_figures, board_tiles, position_of
from .content import content_requirements, describe_requirements
from .descriptor import (
    BAND_TARGETS,
    BOARD_SIZES,
    DESCRIPTOR,
    GAME_ID,
    GAME_VERSION,
    SEARCH_BOUND,
    TRIO,
    band_target,
    board_size_for,
    board_sizes_for,
    build_descriptor,
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
from .plugin import ShapeTrioGame
from .trio import (
    ATTRIBUTES,
    SpaceError,
    check_space,
    completion,
    is_trio,
    near_trios,
    trios_in,
)
from .verify import verify


def build_plugin(*, salt: str = "") -> ShapeTrioGame:
    """What the ``puzzlegen.games`` entry point resolves to.

    A factory rather than an instance, so the registry decides when the game
    is constructed and can pass a salt without this module holding process
    state. Installing this distribution is the whole of adding the game;
    nothing in the platform names it.
    """
    return ShapeTrioGame(salt=salt)


__all__ = [
    "ATTRIBUTES",
    "BAND_TARGETS",
    "BOARD_SIZES",
    "DESCRIPTOR",
    "GAME_ID",
    "GAME_VERSION",
    "SEARCH_BOUND",
    "TRIO",
    "BoardPlan",
    "ShapeTrioGame",
    "SpaceError",
    "assemble",
    "band_target",
    "board_figures",
    "board_size_for",
    "board_sizes_for",
    "board_tiles",
    "build_descriptor",
    "build_plugin",
    "check_space",
    "completion",
    "content_requirements",
    "create_share_artifact",
    "describe_requirements",
    "generate_candidates",
    "get_hint",
    "grade_move",
    "is_trio",
    "last_rejections",
    "measure_difficulty",
    "near_trios",
    "position_of",
    "render",
    "score",
    "trios_in",
    "unusable_reason",
    "verify",
]
