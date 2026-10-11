"""Game 2: a category spelled from the letters its own members carry.

Split by what checks it rather than by convenience. The descriptor is read by
the registry and the accessibility gate, the content requirements by the
content port, the marks module by nothing but this game's own letter work, and
each later module by a different engine gate, so a change to one gate touches
one file.
"""

from .assemble import assemble, board_marks, board_tiles, unmarked_tiles
from .content import content_requirements, describe_requirements
from .descriptor import (
    BLANKS_BY_BAND,
    BOARD_SIZES,
    DESCRIPTOR,
    GAME_ID,
    GAME_VERSION,
    MINIMUM_BLANKS,
    SEARCH_BOUND,
    blanks_for,
    board_size_for,
    board_sizes_for,
    build_descriptor,
)
from .difficulty import measure_difficulty
from .generate import generate_candidates, last_rejections, unusable_reason
from .marks import MarkPlan, letters_of, place_marks, plan_marks, spells
from .play import (
    create_share_artifact,
    get_hint,
    grade_move,
    render,
    score,
)
from .plugin import MissingLettersGame
from .verify import verify


def build_plugin(*, salt: str = "") -> MissingLettersGame:
    """What the ``puzzlegen.games`` entry point resolves to.

    A factory rather than an instance, so the registry decides when the game
    is constructed and can pass a salt without this module holding process
    state. Installing this distribution is the whole of adding the game;
    nothing in the platform names it.
    """
    return MissingLettersGame(salt=salt)


__all__ = [
    "BLANKS_BY_BAND",
    "BOARD_SIZES",
    "DESCRIPTOR",
    "GAME_ID",
    "GAME_VERSION",
    "MINIMUM_BLANKS",
    "SEARCH_BOUND",
    "MarkPlan",
    "MissingLettersGame",
    "assemble",
    "blanks_for",
    "board_marks",
    "board_size_for",
    "board_sizes_for",
    "board_tiles",
    "build_descriptor",
    "build_plugin",
    "content_requirements",
    "create_share_artifact",
    "describe_requirements",
    "generate_candidates",
    "get_hint",
    "grade_move",
    "last_rejections",
    "letters_of",
    "measure_difficulty",
    "place_marks",
    "plan_marks",
    "render",
    "score",
    "spells",
    "unmarked_tiles",
    "unusable_reason",
    "verify",
]
