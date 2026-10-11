"""Game 2's static contract: what it is, and what it promises.

One flat board of tiles, all members of a category nothing on the board names.
Some tiles carry one marked letter each; the solver gathers those letters,
rearranges them freely, and supplies the rest from recognising what the tiles
are. The category's own name is the answer.

Nothing here runs a puzzle. This module holds only what the engine needs
before any content is fetched: the identity, the board shape, how many letters
a day withholds, the states the interface can show, the tokens a share may
use, and where the difficulty scale cuts. Generation, assembly, verification
and play are separate modules, because each is checked by a different engine
gate and mixing them would make a change to one gate touch all four.
"""

from __future__ import annotations

from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.engine.plugin import (
    AccessibilityDeclaration,
    DifficultyThresholds,
    GameDescriptor,
    ShareTokenSpec,
    StateSymbol,
)

GAME_ID = "missing_letters"
GAME_VERSION = "1.0.0"

#: Tile counts a board may have.
#:
#: The four the coverage tool measures, and the measurement is the reason the
#: list stops at twelve: thirty categories can fill six tiles, twenty can fill
#: eight, sixteen can fill ten and twelve, and nothing in the curated taxonomy
#: holds more than twelve common members. A fourteen-tile board would be a
#: board size with no content and no measurement behind it.
BOARD_SIZES = (6, 8, 10, 12)

#: Blanks per difficulty band.
#:
#: The settled rule is that harder means more blanks, with a floor of two
#: regardless. Two, three and four are what the coverage tool measures, so
#: every band's content is a number somebody printed rather than a hope: at
#: six tiles the three bands have 30, 29 and 25 playable categories.
#:
#: The rule's other half, longer words for harder days, is a preference rather
#: than a floor here (see ``generate.ranked``). A length floor per band would
#: starve a band outright, because the curated names run three to seven
#: letters and a hard day would be left with the long tail of one taxonomy.
BLANKS_BY_BAND = {
    DifficultyBand.EASY: 2,
    DifficultyBand.MEDIUM: 3,
    DifficultyBand.HARD: 4,
}

#: The floor the requirements set, repeated here as an assertion rather than a
#: comment: a board with fewer than two blanks withholds almost nothing, and
#: the single blank in the requirements' own ``fish`` example is called out
#: there as illegal for this reason.
MINIMUM_BLANKS = 2

#: Words the verifier will examine before it gives up.
#:
#: Verification here is a scan of a vocabulary rather than a tree search, and
#: the vocabulary is the categories the day's content offered: tens of words,
#: not thousands. The bound is generous because what it protects against is a
#: build whose taxonomy has grown by two orders of magnitude, where an honest
#: SOUND_INCOMPLETE is better than a slow day.
SEARCH_BOUND = 5_000

#: Substream label for the day's board-size draw. A constant because the
#: content layer and the generator must derive the same size from the same
#: day, and a mistyped label would silently give them two answers.
BOARD_SIZE_STREAM = "board-size"


def board_rng(day_key: str, salt: str = "") -> DeterministicRng:
    """The substream that decides the day's shape.

    Separate from the generation stream, because the board size has to be
    known before any content is requested and that happens before the engine
    builds a generation context. Deriving it from the day key keeps it
    reproducible from public inputs alone.
    """
    return DeterministicRng(derive_seed(day_key, GAME_ID, salt)).derive(
        BOARD_SIZE_STREAM
    )


def board_size_for(day_key: str, salt: str = "") -> int:
    """The day's preferred tile count, from the day key alone."""
    return BOARD_SIZES[board_rng(day_key, salt).randbelow(len(BOARD_SIZES))]


def board_sizes_for(day_key: str, salt: str = "") -> tuple[int, ...]:
    """Every supported size, the day's own first, then the rest ascending.

    A board needs at least as many eligible members as it has tiles, and how
    many categories can supply them falls away with size: thirty at six tiles,
    sixteen at twelve. Drawing one size and failing when the content cannot
    serve it loses the day, and a daily game with a board four days in five is
    not a daily game.

    So the day names an order of preference and generation offers candidates
    from all of them in that order. Still a pure function of public inputs, so
    the content layer, the generator and any later audit agree without telling
    each other. The rest ascend rather than being drawn again: a smaller board
    is likelier to be servable, so a day that cannot have its own size should
    fall to the one most likely to work.
    """
    first = board_size_for(day_key, salt)
    return (first, *(size for size in BOARD_SIZES if size != first))


def blanks_for(band: DifficultyBand) -> int:
    """How many letters a board of this band withholds."""
    try:
        return BLANKS_BY_BAND[band]
    except KeyError:  # pragma: no cover - the engine screens unsupported bands
        raise ValueError(f"{band} is not a band this game supports") from None


#: Every visual state the board can show.
#:
#: Each carries a glyph and a spoken label as well as a colour, because a
#: board that distinguishes a marked tile from an unmarked one by colour alone
#: is unplayable for a substantial minority of players, and the engine's
#: accessibility gate refuses it outright. A marked tile is the one state this
#: game cannot afford to convey weakly: the mark is the puzzle.
STATE_SYMBOLS = (
    StateSymbol(name="unmarked", symbol="·", label="tile with no letter", colour="#6b7280"),
    StateSymbol(name="marked", symbol="◆", label="tile with a marked letter", colour="#2563eb"),
    StateSymbol(name="blank", symbol="_", label="letter nobody supplies", colour="#ca8a04"),
    StateSymbol(name="wrong", symbol="✕", label="wrong answer", colour="#dc2626"),
    StateSymbol(name="solved", symbol="●", label="answered", colour="#16a34a"),
    StateSymbol(name="revealed", symbol="◇", label="revealed after the puzzle ended", colour="#7c3aed"),
)

#: The complete vocabulary a share may use.
#:
#: Three renderings each: the glyph for the paste, the plain character for
#: anything that cannot show it, and the label for anything spoken.
#:
#: One token per guess and nothing about the answer. There is deliberately no
#: token for how close a guess was: a share is built from the move ledger with
#: payloads stripped, so it can see that a guess was wrong but never which
#: word it was, and a token the share could never emit would be a word in the
#: vocabulary no artifact can carry.
SHARE_TOKENS = (
    ShareTokenSpec(name="solved", glyph="🟩", plain="#", label="answered"),
    ShareTokenSpec(name="miss", glyph="⬜", plain=".", label="wrong guess"),
    ShareTokenSpec(name="hint", glyph="🟦", plain="?", label="hint taken"),
)

ACCESSIBILITY = AccessibilityDeclaration(
    element_kinds=("tile", "mark", "slot", "board"),
    keyboard_model="grid",
    announcements={
        # Only the engine's own event names and placeholders, so a template
        # that uses anything else fails at registration rather than in front
        # of the player who depends on it.
        "correct": "Correct. {label}",
        "incorrect": "Not it. {mistakes} wrong guesses so far",
        "hint": "Hint {hints}",
        "complete": "Puzzle finished in {attempts} guesses",
        "failed": "Out of guesses after {mistakes} wrong",
        "expired": "Today's {game} has expired",
    },
    colour_independent=True,
    keyboard_complete=True,
    minimum_target_px=44,
    state_symbols=STATE_SYMBOLS,
)

#: Cut points on the normalised difficulty scale.
#:
#: Measured, not chosen. The score is built from shares -- blanks against the
#: answer's length, the length against the longest name the content holds,
#: unmarked tiles against the board -- so board size is divided out before
#: banding. The cut points are then the pair that bands the most boards the way
#: the band table asked for them, over every candidate the generator offered on
#: ten days at each supported band against build A2: 10,210 boards, of which
#: these two numbers band 8,770 correctly.
#:
#: Why not all of them. Blanks are the band's input and the score is a set of
#: shares, so two boards at different blank counts can score alike when their
#: answers differ in length: the measured ranges were 0.32-0.45 at two blanks,
#: 0.40-0.66 at three and 0.48-0.76 at four, which overlap at the edges. That
#: is the score being right about the board and the band being a coarser thing
#: than the score. A day has hundreds of candidates, so the pipeline's in-band
#: search has plenty to choose from and the overlap costs verifications rather
#: than days: measured on the same build, all thirty days across the three
#: bands served a board inside the band they asked for.
#:
#: Rebuilding these numbers: ``tools/generate_days.py`` per band for the days,
#: and ``tests/test_letters_difficulty.py`` for the separation itself.
DIFFICULTY_THRESHOLDS = DifficultyThresholds(cutoffs=(0.42, 0.555))


SUPPORTED_BANDS = (
    DifficultyBand.EASY,
    DifficultyBand.MEDIUM,
    DifficultyBand.HARD,
)

DESCRIPTION = (
    "Every tile belongs to one category, and some of them carry a letter of "
    "its name. Rearrange the letters, supply the ones no tile gives you, and "
    "name the category."
)


def build_descriptor() -> GameDescriptor:
    """This game's contract with the engine.

    ``NO_ALTERNATE_INTERPRETATION`` rather than ``EXACTLY_ONE``, because that
    is the shape of the thing being promised. The answer is one word, so a
    count of solutions is a count of readings of the same board: another
    category that every tile also belongs to, whose name the marks and blanks
    also spell, would be a second honest answer and a player who gave it would
    be marked wrong. The contract names that, and the verifier counts it.

    ``SOUND_INCOMPLETE`` with a bound rather than ``COMPLETE``, which section
    4.1 settles and is worth restating precisely. The verifier examines a
    pinned vocabulary -- the categories the day's content offered, and the
    memberships the board's own tiles carry -- and over that vocabulary its
    enumeration is exhaustive. What it does not do is examine English: a
    solver could defend a word no taxonomy in the build holds. So the game
    declares the weaker promise once, here, and each board records which
    vocabulary was searched and how large it was. A board whose vocabulary
    overran the bound reports SOUND_INCOMPLETE and is refused publication by
    the uniqueness gate, which is the correct outcome.
    """
    return GameDescriptor(
        game_id=GAME_ID,
        display_name="Missing Letters",
        game_version=GAME_VERSION,
        uniqueness_contract=UniquenessContract.NO_ALTERNATE_INTERPRETATION,
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
    "BLANKS_BY_BAND",
    "BOARD_SIZES",
    "BOARD_SIZE_STREAM",
    "DESCRIPTION",
    "DESCRIPTOR",
    "DIFFICULTY_THRESHOLDS",
    "GAME_ID",
    "GAME_VERSION",
    "MINIMUM_BLANKS",
    "SEARCH_BOUND",
    "SHARE_TOKENS",
    "STATE_SYMBOLS",
    "SUPPORTED_BANDS",
    "blanks_for",
    "board_rng",
    "board_size_for",
    "board_sizes_for",
    "build_descriptor",
]
