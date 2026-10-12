"""Game 5: a secret code of digits, and the clues that entail it.

Split by what checks it rather than by convenience. The descriptor is read by
the registry and the accessibility gate, the content requirements by the
content port, the lock module by nothing but this game's own deduction, and
each later module by a different engine gate, so a change to one gate touches
one file.

The clues come in two kinds and the second is why this is a graph game rather
than a Mastermind clone: a property clue states how many of the code's digits
are prime or even, read off the digit entities' own facts, carried through
assembly into the solution so that the clue a player reads, the elimination
the generator did and the enumeration the verifier ran are one set of facts.
"""

from .assemble import (
    answer_code,
    assemble,
    board_digits,
    board_facts,
    board_guesses,
)
from .content import content_requirements, describe_requirements
from .descriptor import (
    CODE_LENGTHS,
    DESCRIPTOR,
    GAME_ID,
    GAME_VERSION,
    MAX_CLUES,
    PROPERTIES_BY_BAND,
    PROPERTY_COUNT,
    SEARCH_BOUND,
    build_descriptor,
    code_length_for,
    code_lengths_for,
    properties_for,
)
from .difficulty import measure_difficulty
from .generate import BoardPlan, generate_candidates, last_rejections, unusable_reason
from .lock import (
    PROPERTY_KINDS,
    CodeError,
    DigitFacts,
    GuessClue,
    PropertyClue,
    check_code,
    space_for,
    survivors,
    verdict,
)
from .play import (
    create_share_artifact,
    get_hint,
    grade_move,
    render,
    score,
)
from .plugin import DigitLockGame
from .verify import surviving_codes, verify


def build_plugin(*, salt: str = "") -> DigitLockGame:
    """What the ``puzzlegen.games`` entry point resolves to.

    A factory rather than an instance, so the registry decides when the game
    is constructed and can pass a salt without this module holding process
    state. Installing this distribution is the whole of adding the game;
    nothing in the platform names it.
    """
    return DigitLockGame(salt=salt)


__all__ = [
    "CODE_LENGTHS",
    "DESCRIPTOR",
    "GAME_ID",
    "GAME_VERSION",
    "MAX_CLUES",
    "PROPERTIES_BY_BAND",
    "PROPERTY_COUNT",
    "PROPERTY_KINDS",
    "SEARCH_BOUND",
    "BoardPlan",
    "CodeError",
    "DigitFacts",
    "DigitLockGame",
    "GuessClue",
    "PropertyClue",
    "answer_code",
    "assemble",
    "board_digits",
    "board_facts",
    "board_guesses",
    "build_descriptor",
    "build_plugin",
    "check_code",
    "code_length_for",
    "code_lengths_for",
    "content_requirements",
    "create_share_artifact",
    "describe_requirements",
    "generate_candidates",
    "get_hint",
    "grade_move",
    "last_rejections",
    "measure_difficulty",
    "properties_for",
    "render",
    "score",
    "space_for",
    "surviving_codes",
    "survivors",
    "unusable_reason",
    "verdict",
    "verify",
]
