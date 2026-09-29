"""Game 1: four visible groups, and a fifth nobody mentions.

Split by what checks it rather than by convenience. The descriptor is read by
the registry and the accessibility gate, the content requirements by the
content port, and each later module by a different engine gate, so a change to
one gate touches one file.
"""

from .assemble import assemble, board_tiles, hidden_members, solution_groups
from .content import content_requirements, describe_requirements
from .difficulty import measure_difficulty
from .generate import generate_candidates, unusable_reason
from .play import (
    create_share_artifact,
    get_hint,
    grade_move,
    render,
    score,
)
from .verify import verify
from .descriptor import (
    DESCRIPTOR,
    GAME_ID,
    GAME_VERSION,
    GROUP_SIZES,
    VISIBLE_GROUPS,
    board_size_for,
    build_descriptor,
    group_size_for,
)

__all__ = [
    "DESCRIPTOR",
    "assemble",
    "board_tiles",
    "GAME_ID",
    "GAME_VERSION",
    "GROUP_SIZES",
    "VISIBLE_GROUPS",
    "board_size_for",
    "build_descriptor",
    "content_requirements",
    "generate_candidates",
    "create_share_artifact",
    "get_hint",
    "grade_move",
    "measure_difficulty",
    "render",
    "score",
    "verify",
    "hidden_members",
    "solution_groups",
    "unusable_reason",
    "describe_requirements",
    "group_size_for",
]
