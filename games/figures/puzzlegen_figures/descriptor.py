"""Game 3's static contract: what it is, and what it promises.

One flat board of simple figures, each a shape at a fill in a colour, drawn
once, twice or three times. Exactly one trio of them satisfies the rule: for
every attribute, all the same or all different. The solver names the trio.

Nothing on the board is a word, which is the point of this game existing. The
engine's two reference games are both lexical, and a platform whose every game
reads a lexicon has not demonstrated that content is replaceable; it has
demonstrated that one kind of content works. This game's tiles carry a glyph
and a spoken label and no spelling at all.

Nothing here runs a puzzle. This module holds only what the engine needs
before any content is fetched: the identity, the board shape, how dense a
day's decoys are, the states the interface can show, the tokens a share may
use, and where the difficulty scale cuts.
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

from .trio import ATTRIBUTES, TRIO

GAME_ID = "shape_trio"
GAME_VERSION = "1.0.0"

#: Tile counts a board may have.
#:
#: Nine and twelve, and the reason the list is short is the opposite of game
#: two's. There is no content shortage here: the default figure space holds
#: eighty-one figures and the generator can fill any board that fits inside
#: it. What bounds the sizes is the player. A board of nine is a trio to find
#: among eighty-four triples; a board of twelve is two hundred and twenty.
#: Fifteen would be four hundred and fifty-five and the task would stop being
#: perception and become bookkeeping.
#:
#: Both are multiples of three, so a board lays out as a square or a
#: rectangle with no ragged row, which matters more here than on a word board:
#: the solver is scanning for a pattern across rows and columns, and a hole in
#: the grid is a shape the eye keeps finding.
BOARD_SIZES = (9, 12)

#: What a board of each band aims at, on the two levers the construction has.
#:
#: Both measured on the default figure space before either was chosen, over
#: five hundred boards the generator built at each size. Neither is a knob
#: somebody liked the sound of.
#:
#: ``varying`` is how many of the four attributes the trio is all *different*
#: on, the rest being all the same. It is the primary lever because it is what
#: the solver's eye does: a trio that is three red triangles differing only in
#: fill is visible in one pass, and a trio with nothing in common has to be
#: assembled attribute by attribute. It runs one to four and never zero, which
#: is not a rule but arithmetic: a trio all-same on every attribute would be
#: three copies of one figure, and the figures on a board are distinct
#: entities. Measured frequencies at nine tiles were 59, 181, 238 and 122 out
#: of 600 for one through four, and at twelve 42, 187, 269 and 102, so every
#: target below is reachable inside the day's attempt budget.
#:
#: ``decoy`` is how many of a board's triples fail on exactly one attribute,
#: as a share of all its triples. That is what makes a board slow rather than
#: invisible: a triple right about three attributes and wrong about the fourth
#: is the one a solver reads out to the end. A share rather than a count,
#: because the number of triples is a function of board size and a count
#: would band every twelve-tile board hard.
#:
#: The decoy range is narrow and the targets respect it. Measured over the
#: same boards, the share ran 0.024 to 0.202 at nine tiles and 0.045 to 0.150
#: at twelve, with both medians near 0.10. Targets of 0.18 and 0.38 were in an
#: earlier draft of this file and are the reason the measurement happened: the
#: second is above anything either size can produce, so a hard day would have
#: ranked its candidates by an unreachable number and effectively shuffled
#: them. The two levers are close to independent -- mean decoy share by
#: varying count was 0.084, 0.100, 0.105, 0.101 at nine tiles -- so a band can
#: ask for both without one deciding the other.
BAND_TARGETS = {
    DifficultyBand.EASY: (1, 0.06),
    DifficultyBand.MEDIUM: (2, 0.10),
    DifficultyBand.HARD: (4, 0.14),
}

#: Triples the verifier will examine before it gives up.
#:
#: Four hundred, which is above the two hundred and twenty a twelve-tile board
#: holds and below what a board one size larger would need. It is a ceiling on
#: a complete enumeration rather than a budget that truncates one: a board
#: whose triples exceed it is a board this game was not built for, and the
#: honest report is that the enumeration was not finished.
SEARCH_BOUND = 400

#: Substream label for the day's board-size draw.
BOARD_SIZE_STREAM = "board-size"


def board_rng(day_key: str, salt: str = "") -> DeterministicRng:
    """The substream that decides the day's shape.

    Separate from the generation stream, because the board size has to be
    known before any content is requested and that happens before the engine
    builds a generation context.
    """
    return DeterministicRng(derive_seed(day_key, GAME_ID, salt)).derive(
        BOARD_SIZE_STREAM
    )


def board_size_for(day_key: str, salt: str = "") -> int:
    """The day's preferred tile count, from the day key alone."""
    return BOARD_SIZES[board_rng(day_key, salt).randbelow(len(BOARD_SIZES))]


def board_sizes_for(day_key: str, salt: str = "") -> tuple[int, ...]:
    """Every supported size, the day's own first, then the rest ascending.

    The same arrangement game two uses, for a weaker reason, which is worth
    being honest about. There, a size could be unservable because no category
    had enough members. Here the content can serve every size every day, so
    the fallback is not what keeps the game daily; it is what keeps a day from
    failing on a generator that happened not to find a board inside its
    attempt budget at one size. It has never fired on the default space, and
    it costs one extra content query.
    """
    first = board_size_for(day_key, salt)
    return (first, *(size for size in BOARD_SIZES if size != first))


def band_target(band: DifficultyBand) -> tuple[int, float]:
    """The ``(varying, decoy)`` pair a board of this band aims at."""
    try:
        return BAND_TARGETS[band]
    except KeyError:  # pragma: no cover - the engine screens unsupported bands
        raise ValueError(f"{band} is not a band this game supports") from None


#: Every visual state the board can show.
#:
#: A figure board is the hardest case the accessibility gate has to refuse,
#: and the reason is this game's own content: two figures can differ only in
#: colour, so a board that conveyed colour by hue alone would be unplayable
#: for a substantial minority of players and still look complete to everybody
#: else. The answer is in three parts, and all three are here or in the
#: declaration below: every tile carries its colour as a word as well as a
#: hue, every state carries a glyph and a spoken label as well as a colour,
#: and ``colour_independent`` is a promise the engine checks rather than a
#: hope.
STATE_SYMBOLS = (
    StateSymbol(name="idle", symbol="·", label="tile not chosen", colour="#6b7280"),
    StateSymbol(name="chosen", symbol="◆", label="tile chosen", colour="#2563eb"),
    StateSymbol(name="wrong", symbol="✕", label="not a trio", colour="#dc2626"),
    StateSymbol(name="solved", symbol="●", label="part of the trio", colour="#16a34a"),
    StateSymbol(
        name="revealed", symbol="◇", label="revealed after the puzzle ended",
        colour="#7c3aed",
    ),
)

#: The complete vocabulary a share may use.
#:
#: One token per submitted trio and nothing about which tiles it was. A share
#: is built from the move ledger with payloads stripped, so it can say a trio
#: was wrong but never which three figures were chosen, and a token for "two
#: of the three were right" would be a word in the vocabulary no artifact can
#: carry: the engine never shows the share layer the move.
SHARE_TOKENS = (
    ShareTokenSpec(name="solved", glyph="🟩", plain="#", label="found the trio"),
    ShareTokenSpec(name="miss", glyph="⬜", plain=".", label="wrong trio"),
    ShareTokenSpec(name="hint", glyph="🟦", plain="?", label="hint taken"),
)

ACCESSIBILITY = AccessibilityDeclaration(
    element_kinds=("tile", "trio", "board"),
    keyboard_model="grid",
    announcements={
        # Only the engine's own event names and placeholders, so a template
        # using anything else fails at registration rather than in front of
        # the player who depends on it.
        "correct": "Correct. {label}",
        "incorrect": "Not a trio. {mistakes} wrong so far",
        "hint": "Hint {hints}",
        "complete": "Puzzle finished in {attempts} tries",
        "failed": "Out of tries after {mistakes} wrong",
        "expired": "Today's {game} has expired",
    },
    colour_independent=True,
    keyboard_complete=True,
    minimum_target_px=44,
    state_symbols=STATE_SYMBOLS,
)

#: Cut points on the normalised difficulty scale.
#:
#: Measured against the generator's own output, and the measurement had to be
#: set up carefully, which is the part worth recording.
#:
#: The obvious sample is every candidate the generator offers, which is what
#: game two's thresholds were fitted over. It is the wrong sample here,
#: because of a difference between the two games. There, the band changes the
#: board: a hard day withholds four letters and an easy one two, so the
#: candidates at one band are boards that could not exist at another. Here the
#: band changes only the *order*: the content is the same figure space every
#: day, the generator builds the same ninety-six boards, and the band decides
#: which of them it offers first. Fitted over all of them, the three bands'
#: score distributions are nearly identical and the best possible pair of cut
#: points bands 43% of them correctly, which measures the sample rather than
#: the scale.
#:
#: The sample that means something is the boards the engine actually reaches:
#: the first ``ON_TARGET_ATTEMPTS`` candidates of each day, which is the
#: window its in-band search looks at before it settles. Over thirty days at
#: each band against the symbolic build, 720 boards: EASY ran 0.386 to 0.630
#: with a median of 0.500, MEDIUM 0.460 to 0.689 with a median of 0.592, and
#: HARD 0.657 to 0.914 with a median of 0.799. The pair below bands 649 of the
#: 720 the way the band asked, which is 90%.
#:
#: The 10% that miss are the EASY and MEDIUM ranges overlapping between about
#: 0.46 and 0.63, which is the score being right about the board and the band
#: being a coarser thing than the score: both bands target the same decoy
#: density range and differ by one attribute of variation, which four
#: weighted shares cannot always separate. It costs verifications rather than
#: days, because a day offers dozens of candidates and the engine keeps
#: looking.
#:
#: Board size is divided out, measured rather than asserted: within each band
#: the mean score at nine tiles and at twelve differed by 0.010, 0.013 and
#: 0.027.
#:
#: Rebuilding these numbers: ``tools/generate_days.py`` per band for the days,
#: and ``tests/test_figures_difficulty.py`` for the separation itself.
DIFFICULTY_THRESHOLDS = DifficultyThresholds(cutoffs=(0.555, 0.655))

SUPPORTED_BANDS = (
    DifficultyBand.EASY,
    DifficultyBand.MEDIUM,
    DifficultyBand.HARD,
)

DESCRIPTION = (
    "Every tile is a shape at a fill, in a colour, drawn once, twice or three "
    "times. Find the three tiles where each of those four things is either "
    "the same across all three or different across all three."
)


def build_descriptor() -> GameDescriptor:
    """This game's contract with the engine.

    ``EXACTLY_ONE`` and ``COMPLETE``, which neither reference game could
    promise and this one can, for a reason about the content rather than about
    the code. A grouping board's solution space is the partitions of sixteen
    tiles and a spelling board's is the English language; both are too large
    to enumerate, so one game promises a unique grouping and the other
    promises soundness inside a pinned vocabulary. A trio board's solution
    space is the triples of its own tiles: two hundred and twenty at the
    largest size. The verifier examines every one of them, so the count it
    reports is the count, and the contract can be the strong one.

    The bound is declared anyway. It is not a budget this game expects to
    spend -- a complete enumeration that hit it would be refused publication,
    which is correct -- but a ceiling that makes a board larger than this
    game was built for fail loudly instead of slowly.
    """
    return GameDescriptor(
        game_id=GAME_ID,
        display_name="Shape Trio",
        game_version=GAME_VERSION,
        uniqueness_contract=UniquenessContract.EXACTLY_ONE,
        supported_bands=SUPPORTED_BANDS,
        supported_locales=("en",),
        verification_completeness=VerificationCompleteness.COMPLETE,
        search_bound=SEARCH_BOUND,
        accessibility=ACCESSIBILITY,
        difficulty_thresholds=DIFFICULTY_THRESHOLDS,
        share_tokens=SHARE_TOKENS,
        description=DESCRIPTION,
    )


DESCRIPTOR = build_descriptor()


__all__ = [
    "ACCESSIBILITY",
    "ATTRIBUTES",
    "BOARD_SIZES",
    "BOARD_SIZE_STREAM",
    "BAND_TARGETS",
    "DESCRIPTION",
    "DESCRIPTOR",
    "DIFFICULTY_THRESHOLDS",
    "GAME_ID",
    "GAME_VERSION",
    "SEARCH_BOUND",
    "SHARE_TOKENS",
    "STATE_SYMBOLS",
    "SUPPORTED_BANDS",
    "TRIO",
    "board_rng",
    "board_size_for",
    "board_sizes_for",
    "build_descriptor",
    "band_target",
]
