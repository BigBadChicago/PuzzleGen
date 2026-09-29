"""Game 1's static contract: what it is, and what it promises.

Four visible groups of 5 to 9, plus a fifth group nobody is told about, drawn
from an overlay category that cuts across the four. The board is the same
tiles either way; what changes is which axis explains them.

Nothing here runs a puzzle. This module holds only what the engine needs
before any content is fetched: the identity, the board shape, the states the
interface can show, the tokens a share may use, and where the difficulty scale
cuts. Generation, assembly, verification and play are separate modules,
because each of them is checked by a different engine gate and mixing them
would make a change to one of those gates touch all four.
"""

from __future__ import annotations

from ...core.rng import DeterministicRng, derive_seed
from ...core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from ...engine.plugin import (
    AccessibilityDeclaration,
    DifficultyThresholds,
    GameDescriptor,
    ShareTokenSpec,
    StateSymbol,
)

GAME_ID = "grouping"
GAME_VERSION = "1.0.0"

#: Visible groups on every board. Four, always: the number is what makes the
#: hidden fifth a surprise rather than an obvious gap, and a board whose group
#: count varied would need its own difficulty normalisation on top of the one
#: board size already forces.
VISIBLE_GROUPS = 4

#: Group size is drawn per day from this range, so a board is 20 to 36 tiles.
MIN_GROUP_SIZE = 5
MAX_GROUP_SIZE = 9
GROUP_SIZES = tuple(range(MIN_GROUP_SIZE, MAX_GROUP_SIZE + 1))

#: The hidden group has exactly as many members as the day's visible groups,
#: drawn one from each visible group where possible. Equal size is what stops
#: it being identifiable by counting.
HIDDEN_GROUPS = 1

#: States the partition enumerator may examine before it gives up. Sized for
#: the worst day rather than the average: 36 tiles into four groups of 9 is a
#: far larger search than 20 into groups of 5, and a budget sized for the mean
#: would fail only on the widest boards, which is the least debuggable way for
#: a budget to be wrong.
SEARCH_BOUND = 2_000_000

#: Substream label for the day's board-size draw. Named as a constant because
#: both the content layer and the generator must derive the same size from the
#: same day, and a mistyped label would silently give them two answers.
BOARD_SIZE_STREAM = "board-size"


def board_rng(day_key: str, salt: str = "") -> DeterministicRng:
    """The substream that decides the day's shape.

    Separate from the generation stream on purpose. Board size has to be known
    before any content is requested, which happens before the engine builds a
    generation context, so it cannot come from the context's RNG. Deriving it
    from the day key directly keeps it reproducible from public inputs alone.
    """
    return DeterministicRng(derive_seed(day_key, GAME_ID, salt)).derive(
        BOARD_SIZE_STREAM
    )


def group_size_for(day_key: str, salt: str = "") -> int:
    """The day's group size, from the day key alone.

    A pure function of public inputs, so the content requirements, the
    generator and any later audit all compute the same number without passing
    it between them.
    """
    return GROUP_SIZES[board_rng(day_key, salt).randbelow(len(GROUP_SIZES))]


def board_size_for(day_key: str, salt: str = "") -> int:
    return group_size_for(day_key, salt) * VISIBLE_GROUPS


#: Every visual state a tile can be in.
#:
#: Each carries a glyph and a spoken label as well as a colour, because a
#: board that distinguishes "solved" from "wrong" by colour alone is unplayable
#: for a substantial minority of players and the engine's accessibility gate
#: refuses it outright.
STATE_SYMBOLS = (
    StateSymbol(
        name="idle",
        symbol="·",
        label="not selected",
        colour="#6b7280",
    ),
    StateSymbol(
        name="selected",
        symbol="○",
        label="selected",
        colour="#2563eb",
    ),
    StateSymbol(
        name="solved",
        symbol="●",
        label="in a solved group",
        colour="#16a34a",
    ),
    StateSymbol(
        name="near_miss",
        symbol="◐",
        label="one tile away from a group",
        colour="#ca8a04",
    ),
    StateSymbol(
        name="rejected",
        symbol="✕",
        label="not a group",
        colour="#dc2626",
    ),
    StateSymbol(
        name="revealed",
        symbol="◆",
        label="revealed after the puzzle ended",
        colour="#7c3aed",
    ),
)

#: The complete vocabulary a share may use.
#:
#: Three renderings each. The glyph is for the paste, the plain character for
#: anything that cannot show the glyph, and the label for anything spoken. A
#: share with only glyphs is a share some players cannot read at all.
#:
#: There is no "one away" token, though there is a state symbol for it. A
#: share is built from the move ledger with payloads stripped, so it can see
#: that an attempt was wrong but never how wrong. Declaring a token the share
#: could never emit would put a word in the vocabulary that no artifact can
#: ever carry.
#:
#: ``hidden`` is deliberately distinct from ``solved``: finding the group
#: nobody mentioned is the thing worth showing off, and collapsing it into an
#: ordinary solve would throw away the only part of the result that says which
#: axis the player found.
SHARE_TOKENS = (
    ShareTokenSpec(
        name="solved",
        glyph="🟩",
        plain="#",
        label="group solved",
    ),
    ShareTokenSpec(
        name="hidden",
        glyph="🟪",
        plain="@",
        label="hidden group solved",
    ),
    ShareTokenSpec(
        name="miss",
        glyph="⬜",
        plain=".",
        label="wrong guess",
    ),
    ShareTokenSpec(
        name="hint",
        glyph="🟦",
        plain="?",
        label="hint taken",
    ),
)

ACCESSIBILITY = AccessibilityDeclaration(
    element_kinds=("tile", "group", "board"),
    keyboard_model="grid",
    announcements={
        # Only the engine's own event names and placeholders. The vocabulary
        # is fixed so wording stays consistent across games, and a template
        # using anything outside it is refused at registration rather than
        # failing silently in front of a player who depends on it.
        "selected": "{label} selected, {remaining} tiles unplaced",
        "deselected": "{label} deselected, {remaining} tiles unplaced",
        "correct": "Correct. {remaining} groups left",
        "incorrect": "Not a group. {mistakes} wrong guesses so far",
        "hint": "Hint {hints}",
        "complete": "Puzzle finished. {total} groups found in {attempts} guesses",
        "failed": "Out of guesses after {mistakes} wrong",
        "expired": "Today's {game} has expired",
        "axis_switch": "The first axis is finished. {remaining} tiles remain",
    },
    colour_independent=True,
    keyboard_complete=True,
    minimum_target_px=44,
    state_symbols=STATE_SYMBOLS,
)

#: Cut points on the normalised difficulty scale.
#:
#: Even thirds, deliberately. Board size is divided out before a score reaches
#: these cutoffs, because a 9-per-group day is mechanically larger than a
#: 5-per-group one and banding on the raw number would put every wide board in
#: the hardest band for a reason that has nothing to do with the puzzle. Once
#: that effect is removed the scale carries only puzzle difficulty, and any
#: asymmetry here would be a second, hidden correction to something already
#: corrected.
DIFFICULTY_THRESHOLDS = DifficultyThresholds(cutoffs=(1 / 3, 2 / 3))

SUPPORTED_BANDS = (
    DifficultyBand.EASY,
    DifficultyBand.MEDIUM,
    DifficultyBand.HARD,
)

DESCRIPTION = (
    "Sort every tile into four groups. One more group is hiding across them, "
    "and the board will tell you when it is all that remains."
)


def build_descriptor() -> GameDescriptor:
    """This game's contract with the engine.

    ``UNIQUE_UP_TO_TOLERANCE`` rather than ``UNIQUE_GROUPING`` because a
    grouping board genuinely has equivalent solutions: swapping two tiles that
    both belong to both of their groups produces a different partition that is
    the same puzzle. The tolerance contract is the one that lets the verifier
    collapse those and still claim uniqueness, and it obliges the verifier to
    report how many it collapsed, so an engine can tell a real collapse from a
    game that never applied its own rule.

    ``SOUND_INCOMPLETE`` with a bound rather than ``COMPLETE``, because on the
    widest boards the enumeration can legitimately run out of budget. A game
    that declared COMPLETE and then returned a partial enumeration would be
    committing a protocol violation; declaring the weaker promise and reporting
    COMPLETE per puzzle whenever the search actually finished is the honest
    arrangement. A day that overruns reports SOUND_INCOMPLETE and is refused
    publication by the uniqueness gate, which is the correct outcome: an
    unexamined branch is exactly where a second solution would hide.
    """
    return GameDescriptor(
        game_id=GAME_ID,
        display_name="Grouping",
        game_version=GAME_VERSION,
        uniqueness_contract=UniquenessContract.UNIQUE_UP_TO_TOLERANCE,
        supported_bands=SUPPORTED_BANDS,
        supported_locales=("en",),
        verification_completeness=VerificationCompleteness.SOUND_INCOMPLETE,
        search_bound=SEARCH_BOUND,
        accessibility=ACCESSIBILITY,
        difficulty_thresholds=DIFFICULTY_THRESHOLDS,
        share_tokens=SHARE_TOKENS,
        description=DESCRIPTION,
    )


DESCRIPTOR = build_descriptor()


__all__ = [
    "ACCESSIBILITY",
    "BOARD_SIZE_STREAM",
    "DESCRIPTION",
    "DESCRIPTOR",
    "DIFFICULTY_THRESHOLDS",
    "GAME_ID",
    "GAME_VERSION",
    "GROUP_SIZES",
    "HIDDEN_GROUPS",
    "MAX_GROUP_SIZE",
    "MIN_GROUP_SIZE",
    "SEARCH_BOUND",
    "SHARE_TOKENS",
    "STATE_SYMBOLS",
    "SUPPORTED_BANDS",
    "VISIBLE_GROUPS",
    "board_rng",
    "board_size_for",
    "build_descriptor",
    "group_size_for",
]
