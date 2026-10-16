"""Game 4's static contract: what it is, and what it promises.

One row of numbers in ascending order. All but one of them share something;
the solver names the one that does not. The something is a property the graph
holds over numbers -- even, prime, square, triangular, a multiple of five --
and nothing on the board names it.

Nothing here runs a puzzle. This module holds only what the engine needs
before any content is fetched: the identity, the board shape, the magnitude
window a day draws from, how familiar the day's property should be, the states
the interface can show, the tokens a share may use, and where the difficulty
scale cuts.
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

from .properties import MAX_TIER, MIN_TIER

GAME_ID = "odd_number_out"
GAME_VERSION = "1.0.0"

#: Tile counts a board may have.
#:
#: Five, six and seven. The lower end is set by the puzzle rather than by the
#: content: with four tiles, three sharing a property and one not, there are
#: too few numbers for the shared property to be the only thing three of them
#: have in common, and boards get refused for ambiguity far more often than
#: they are served. The upper end is set by the reading: the solver holds every
#: tile in mind while testing a property against all of them, and past seven
#: that stops being a glance and becomes a list.
BOARD_SIZES = (5, 6, 7)

#: The magnitude window a day's numbers are drawn from.
#:
#: Ten to ninety-nine: the two-digit numbers. Two decisions in one, and the
#: second is the load-bearing one.
#:
#: A window at all, rather than the whole range the content holds, because
#: without one the board gives its answer away by size. A board of 7, 14, 21,
#: 28 and 93 is solved by noticing that one number is much larger, which is
#: not the puzzle and not a property the vocabulary holds.
#:
#: And one window rather than several, so that every number on a board has the
#: same digit count. "Two-digit number" is then true of every tile, which
#: makes it a property that distinguishes nothing and is excluded by name in
#: ``properties.vocabulary_of``. A board mixing one- and two-digit numbers
#: would make digit count the easiest reading on every board, every day.
WINDOW = (10, 99)

#: How familiar the day's property should be, per band.
#:
#: The one knob difficulty turns here, and it turns the thing the player
#: actually meets. A board whose answer is "the odd one is not even" and a
#: board whose answer is "the odd one is not triangular" can be the same five
#: numbers in the same order; what differs is whether the solver has the
#: property to hand. The tiers are in ``properties.TIERS``, which is this
#: game's one piece of judgement about its content and is data for that
#: reason.
TIER_BY_BAND = {
    DifficultyBand.EASY: MIN_TIER,
    DifficultyBand.MEDIUM: MIN_TIER + 1,
    DifficultyBand.HARD: MAX_TIER,
}

#: Readings the verifier will examine before it gives up.
#:
#: Seven tiles times a vocabulary of nine properties is sixty-three, and the
#: bound is two thousand. Generous on purpose: what it protects against is a
#: build whose number taxonomy has grown by an order of magnitude, where an
#: honest refusal is better than a slow day. It is not a budget this game
#: expects to spend.
SEARCH_BOUND = 2_000

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
    """The day's tile count, from the day key alone."""
    return BOARD_SIZES[board_rng(day_key, salt).randbelow(len(BOARD_SIZES))]


def board_sizes_for(day_key: str, salt: str = "") -> tuple[int, ...]:
    """Every supported size, the day's own first, then the rest ascending.

    A fallback order rather than a single size, for a reason this game has and
    the figure game does not: whether a board of a given size can be built
    depends on how many numbers in the window carry the day's property, and
    the obscure properties are thin. Counted over the two-digit numbers, the
    vocabulary runs 45 even, 45 odd, 30 multiples of three, 21 prime, 18
    multiples of five, 10 triangular, 9 multiples of ten, 6 square and 5
    Fibonacci. So a seven-tile board whose property is Fibonacci would need
    six of the five there are, which is not a thin constraint but an
    impossible one, and a six-tile board needs all five. A smaller board is
    likelier to be servable, so the fallbacks ascend.
    """
    first = board_size_for(day_key, salt)
    return (first, *(size for size in BOARD_SIZES if size != first))


def tier_for(band: DifficultyBand) -> int:
    """How familiar a board of this band's property should be."""
    try:
        return TIER_BY_BAND[band]
    except KeyError:  # pragma: no cover - the engine screens unsupported bands
        raise ValueError(f"{band} is not a band this game supports") from None


#: Every visual state the board can show.
#:
#: A number board's states are easier to convey than a figure board's, because
#: a tile already carries characters a reader can read. The glyphs are still
#: here and still required: the engine's accessibility gate refuses a
#: declaration that conveys a state by colour alone, and "this tile is the one
#: you chose" is a state, however legible the tile under it.
STATE_SYMBOLS = (
    StateSymbol(name="idle", symbol="·", label="number not chosen", colour="#6b7280"),
    StateSymbol(name="chosen", symbol="◆", label="number chosen", colour="#2563eb"),
    StateSymbol(name="wrong", symbol="✕", label="not the odd one", colour="#dc2626"),
    StateSymbol(name="solved", symbol="●", label="the odd one out", colour="#16a34a"),
    StateSymbol(
        name="revealed", symbol="◇", label="revealed after the puzzle ended",
        colour="#7c3aed",
    ),
)

#: The complete vocabulary a share may use.
#:
#: One token per guess and nothing about the numbers. A share is built from the
#: move ledger with payloads stripped, so it can say a guess was wrong but
#: never which tile it was, and there is deliberately no token for the
#: property: naming it would give the next reader the answer to a board they
#: have not played.
SHARE_TOKENS = (
    ShareTokenSpec(name="solved", glyph="🟩", plain="#", label="found the odd one"),
    ShareTokenSpec(name="miss", glyph="⬜", plain=".", label="wrong number"),
    ShareTokenSpec(name="hint", glyph="🟦", plain="?", label="hint taken"),
)

ACCESSIBILITY = AccessibilityDeclaration(
    element_kinds=("tile", "row", "board"),
    # A row rather than a grid: the board is one line of numbers in ascending
    # order, so the keyboard model that matches it is a list and a grid model
    # would have the player pressing down arrows that do nothing.
    keyboard_model="list",
    announcements={
        # Only the engine's own event names and placeholders, so a template
        # using anything else fails at registration rather than in front of
        # the player who depends on it.
        "correct": "Correct. {label}",
        "incorrect": "Not the odd one. {mistakes} wrong so far",
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
#: Measured over the boards the engine reaches, as game three's are and for
#: the same reason: the band changes which boards the generator offers first
#: rather than which boards exist, so a sample of every candidate measures the
#: pool and not the scale. Thirty days at each band against the symbolic
#: build, the first eight candidates of each, 720 boards.
#:
#: The three ranges came out disjoint: 0.250 to 0.399, 0.450 to 0.638, and
#: 0.650 to 0.828. The pair below is the midpoint of each gap, and it bands
#: all 720 the way the band asked.
#:
#: "The boards the engine reaches" is load bearing and not a convenience.
#: Over *every* board the generator builds at a tier the ranges do overlap: a
#: familiar property with one reason outscores an obscure one with four,
#: because the reason shortage carries a quarter of the weight. What separates
#: the served boards is that the generator offers fewest-reasons first, so
#: what the engine sees is the top of each tier's range rather than all of it.
#: The band promise therefore rests on the ranking as much as on the scale,
#: which ``tests/test_numbers_difficulty.py`` asserts in both directions.
#:
#: That is a cleaner separation than game three manages and it is worth being
#: honest about why, because a hundred percent is usually a sign of a
#: tautology rather than of a good model. It is partly one. Obscurity carries
#: the largest weight and is a step function of the property's tier, and the
#: tiers are what the bands select on, so the scale restates the band's own
#: choice before the other three features say anything. What those three
#: actually do is move a board inside its band rather than between bands: the
#: reason shortage, the near readings and the tightness together span about
#: 0.15 of the scale, which is why each band's measured range is a sixth of
#: the interval wide rather than a point.
#:
#: The cost of that arrangement is real and is the thing a later calibration
#: should attack: a board whose property is familiar cannot measure hard
#: however tight its numbers are, so the model cannot discover that an easy
#: property on a tight board is harder than the tier says. The alternative --
#: weighting obscurity below the other three -- would make the bands overlap
#: heavily on a scale whose other inputs nobody has validated either, which is
#: a worse trade until there is play data.
#:
#: Board size is divided out, measured rather than asserted: within each band
#: the means at five, six and seven tiles differ by 0.022, 0.044 and 0.036.
#: The small boards score slightly harder, which is the tightness feature
#: working as intended rather than size leaking through -- five numbers drawn
#: from the same window span less of it than seven do.
#:
#: Rebuilding these numbers: ``tools/generate_days.py`` per band for the days,
#: and ``tests/test_numbers_difficulty.py`` for the separation itself.
DIFFICULTY_THRESHOLDS = DifficultyThresholds(cutoffs=(0.425, 0.644))

SUPPORTED_BANDS = (
    DifficultyBand.EASY,
    DifficultyBand.MEDIUM,
    DifficultyBand.HARD,
)

DESCRIPTION = (
    "All but one of these numbers share something. Name the one that does "
    "not."
)


def build_descriptor() -> GameDescriptor:
    """This game's contract with the engine.

    ``EXACTLY_ONE``, because the answer is a tile and exactly one tile is it.
    That is worth distinguishing from the thing it is not: a board can have
    one answer for several reasons -- the odd one out is not even *and* not a
    multiple of three -- and those are not two solutions. They are one
    solution with two arguments for it, and such a board is easier rather than
    ambiguous. Only a second *tile* breaks the contract, and the verifier
    counts tiles.

    ``SOUND_INCOMPLETE`` with a bound rather than ``COMPLETE``, and the
    distinction is the same one game two draws. The verifier examines every
    tile against every property the day's content offered, and over that
    vocabulary its enumeration is exhaustive, so a board whose scan finished
    reports COMPLETE and can satisfy the contract. What it does not examine is
    arithmetic: a solver could defend a property no taxonomy in the build
    holds -- "the odd one is the only one whose digits differ by more than
    four" -- and no enumeration here would have seen it. The game declares the
    weaker promise once, here, and each board records which vocabulary was
    searched and how large it was.
    """
    return GameDescriptor(
        game_id=GAME_ID,
        display_name="Odd Number Out",
        game_version=GAME_VERSION,
        uniqueness_contract=UniquenessContract.EXACTLY_ONE,
        supported_bands=SUPPORTED_BANDS,
        supported_locales=("en",),
        verification_completeness=VerificationCompleteness.SOUND_INCOMPLETE,
        search_bound=SEARCH_BOUND,
        # The readings are derived by elimination, so the reason is in hand
        # and the hints are read off it. The witness declares ANSWER_VALID
        # rather than borrowing the stronger word, for the same reason the
        # promise above is the weaker one: the vocabulary is bounded.
        supplies_witness=True,
        accessibility=ACCESSIBILITY,
        difficulty_thresholds=DIFFICULTY_THRESHOLDS,
        share_tokens=SHARE_TOKENS,
        description=DESCRIPTION,
    )


DESCRIPTOR = build_descriptor()


__all__ = [
    "ACCESSIBILITY",
    "BOARD_SIZES",
    "BOARD_SIZE_STREAM",
    "DESCRIPTION",
    "DESCRIPTOR",
    "DIFFICULTY_THRESHOLDS",
    "GAME_ID",
    "GAME_VERSION",
    "SEARCH_BOUND",
    "SHARE_TOKENS",
    "STATE_SYMBOLS",
    "SUPPORTED_BANDS",
    "TIER_BY_BAND",
    "WINDOW",
    "board_rng",
    "board_size_for",
    "board_sizes_for",
    "build_descriptor",
    "tier_for",
]
