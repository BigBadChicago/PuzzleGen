"""Game 5's static contract: what it is, and what it promises.

A secret code of distinct digits, and a handful of clues. Some clues are codes
somebody tried and what they were told: how many digits were right and in the
right place, how many right in the wrong place. Others are facts about the
code read off the graph: how many of its digits are prime, how many are even,
what they add to. The solver deduces the code.

Nothing here runs a puzzle. This module holds only what the engine needs
before any content is fetched: the identity, the code length a day uses, which
facts a board of each band is allowed to state, the states the interface can
show, the tokens a share may use, and where the difficulty scale cuts.
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

from .lock import BASE, PROPERTY_KINDS

GAME_ID = "digit_lock"
GAME_VERSION = "1.0.0"

#: Code lengths a day may use. This game's board size.
#:
#: Three and four. Below three the space is seventy-two codes and two clues
#: exhaust it; above four it is thirty thousand and the solver is keeping a
#: table rather than reasoning. Both are small enough for the verifier to
#: enumerate the whole space, which is what lets the contract be the strong
#: one: ten digits taken three at a time is 720 codes and taken four at a time
#: is 5,040.
CODE_LENGTHS = (3, 4)

#: Clues a board may show.
#:
#: Eight, counting guesses only. Measured rather than chosen: over two hundred
#: draws at each length the construction needed a median of five guess clues
#: at three digits and six at four, with a maximum of eight, and a ceiling
#: below that would refuse the boards at the top of the range rather than the
#: ones nobody wants. A board needing more than eight is a board whose clue
#: order was unlucky, and the generator draws again.
MAX_CLUES = 8

#: Which facts a board of each band may state.
#:
#: The one knob difficulty turns, and the thing it turns is how much of the
#: puzzle is given rather than deduced. A property clue is free information:
#: it says how many digits are prime and the solver writes it down. A board
#: with all three states most of what there is to know about the code's
#: digits and leaves their order; a board with none makes the solver find both
#: from guesses alone.
#:
#: Measured, over two hundred draws at each length: with all three facts
#: stated the construction needed a median of three guess clues at three
#: digits and four at four; with one fact, five and five; with none, five and
#: six. So the lever moves the guess count by about two clues, which is the
#: difference between a board a solver reads twice and one they tabulate.
PROPERTIES_BY_BAND = {
    DifficultyBand.EASY: PROPERTY_KINDS,
    DifficultyBand.MEDIUM: ("primes",),
    DifficultyBand.HARD: (),
}

#: Codes the verifier will examine before it gives up.
#:
#: Six thousand, which is above the 5,040 a four-digit board holds and below
#: what a five-digit board would need. A ceiling on a complete enumeration
#: rather than a budget that truncates one: a board whose space exceeds it is
#: a board this game was not built for, and the honest report is that the
#: enumeration was not finished.
SEARCH_BOUND = 6_000

#: How many facts a board could state at most.
#:
#: The length of the property kinds the deduction module defines, read rather
#: than written down, so a fourth kind added there makes the easy band state
#: it and the difficulty model normalise against it without an edit here.
PROPERTY_COUNT = len(PROPERTY_KINDS)

#: Substream label for the day's length draw.
LENGTH_STREAM = "code-length"


def length_rng(day_key: str, salt: str = "") -> DeterministicRng:
    """The substream that decides the day's code length.

    Separate from the generation stream, because the length has to be known
    before any content is requested and that happens before the engine builds
    a generation context.
    """
    return DeterministicRng(derive_seed(day_key, GAME_ID, salt)).derive(LENGTH_STREAM)


def code_length_for(day_key: str, salt: str = "") -> int:
    """The day's code length, from the day key alone."""
    return CODE_LENGTHS[length_rng(day_key, salt).randbelow(len(CODE_LENGTHS))]


def code_lengths_for(day_key: str, salt: str = "") -> tuple[int, ...]:
    """Every supported length, the day's own first, then the rest ascending.

    A fallback order, and this game's is the weakest case for one in the
    repository: the construction failed five times in four hundred draws, and
    on a board with no property clues at that. The fallback costs one extra
    content query and removes a one-in-eighty chance of losing a day, which is
    worth it for a daily game and would not be for a weekly one.
    """
    first = code_length_for(day_key, salt)
    return (first, *(length for length in CODE_LENGTHS if length != first))


def properties_for(band: DifficultyBand) -> tuple[str, ...]:
    """Which facts a board of this band may state."""
    try:
        return PROPERTIES_BY_BAND[band]
    except KeyError:  # pragma: no cover - the engine screens unsupported bands
        raise ValueError(f"{band} is not a band this game supports") from None


#: Every visual state the board can show.
#:
#: A clue row's two counts are the states that matter, and they are numbers
#: rather than colours already, so this declaration is less exposed than a
#: figure board's. The glyphs are still required and still here: "this row is
#: the guess you are entering" is a state, and a board that marked it by
#: colour alone would be one some players cannot operate.
STATE_SYMBOLS = (
    StateSymbol(name="clue", symbol="=", label="a clue row", colour="#6b7280"),
    StateSymbol(name="entry", symbol="_", label="the row you are entering", colour="#2563eb"),
    StateSymbol(name="exact", symbol="●", label="right digit, right place", colour="#16a34a"),
    StateSymbol(name="misplaced", symbol="◐", label="right digit, wrong place", colour="#ca8a04"),
    StateSymbol(name="absent", symbol="○", label="digit not in the code", colour="#9ca3af"),
    StateSymbol(name="wrong", symbol="✕", label="wrong code", colour="#dc2626"),
    StateSymbol(name="solved", symbol="★", label="the code", colour="#7c3aed"),
)

#: The complete vocabulary a share may use.
#:
#: One token per submission and nothing about the code. A share is built from
#: the move ledger with payloads stripped, so it can say a guess was wrong but
#: never which digits it was, and there is deliberately no token carrying a
#: verdict: "two exact" about an unnamed guess would still narrow the code for
#: a reader who then plays the same board.
SHARE_TOKENS = (
    ShareTokenSpec(name="solved", glyph="🟩", plain="#", label="opened the lock"),
    ShareTokenSpec(name="miss", glyph="⬜", plain=".", label="wrong code"),
    ShareTokenSpec(name="hint", glyph="🟦", plain="?", label="hint taken"),
)

ACCESSIBILITY = AccessibilityDeclaration(
    element_kinds=("clue", "digit", "verdict", "entry"),
    # A grid: the clues are rows of digits with two counts beside them, and
    # the solver moves along a row and down the column of verdicts.
    keyboard_model="grid",
    announcements={
        # Only the engine's own event names and placeholders, so a template
        # using anything else fails at registration rather than in front of
        # the player who depends on it.
        "correct": "Correct. {label}",
        "incorrect": "Not the code. {mistakes} wrong so far",
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
#: Measured over the boards the engine reaches, as games three and four are:
#: the first eight candidates of each day, which is the window the engine's
#: in-band search looks at. Twenty days at each band against the symbolic
#: build, 480 boards.
#:
#: The three ranges came out disjoint: 0.156 to 0.400, 0.510 to 0.667, and
#: 0.688 to 0.825. The pair below is the midpoint of each gap and bands all
#: 480 the way the band asked.
#:
#: Disjoint for the same partly-tautological reason game four's are: the
#: largest weight is how many facts the board withholds, and that is exactly
#: what the band selects. What the other two features do is move a board
#: inside its band, and here they do a good deal of that -- each band's range
#: is a quarter of the interval wide, because the clue count and the endgame
#: size both vary a lot between draws.
DIFFICULTY_THRESHOLDS = DifficultyThresholds(cutoffs=(0.455, 0.678))

SUPPORTED_BANDS = (
    DifficultyBand.EASY,
    DifficultyBand.MEDIUM,
    DifficultyBand.HARD,
)

DESCRIPTION = (
    "A code of digits, none repeated. Each clue is either a code somebody "
    "tried, with how many digits were right and how many were in the right "
    "place, or a fact about the code itself. Work out the code."
)


def build_descriptor() -> GameDescriptor:
    """This game's contract with the engine.

    ``UNIQUE_DEDUCTION`` rather than ``EXACTLY_ONE``, and the difference is
    not cosmetic. Both are satisfied by a single solution, and the engine
    treats them identically today, so this is a statement about what the
    single solution means. ``EXACTLY_ONE`` says the puzzle has one answer.
    ``UNIQUE_DEDUCTION`` says the clues *entail* it: exactly one code is
    consistent with everything the board states, so a solver who reasons
    correctly arrives at it without guessing. A board with one answer that
    required a coin flip at the end would satisfy the first promise and break
    this one, and the generator's stopping rule -- add clues until exactly one
    code survives -- is what makes it the one worth declaring.

    ``COMPLETE``, because the solution space is small enough to enumerate
    whole: 720 codes at three digits and 5,040 at four. The verifier tests
    every one of them against every clue, so the count it reports is the
    count. That is the second game in the repository able to promise this, and
    for the same reason as the first: the space is the board's own, not the
    language's.
    """
    return GameDescriptor(
        game_id=GAME_ID,
        display_name="Digit Lock",
        game_version=GAME_VERSION,
        uniqueness_contract=UniquenessContract.UNIQUE_DEDUCTION,
        supported_bands=SUPPORTED_BANDS,
        supported_locales=("en",),
        verification_completeness=VerificationCompleteness.COMPLETE,
        search_bound=SEARCH_BOUND,
        # Declarable for the same reason the completeness promise is: the
        # generator reaches one code by eliminating the rest, so the reason
        # is in hand and the hints are derived from it rather than written.
        supplies_witness=True,
        accessibility=ACCESSIBILITY,
        difficulty_thresholds=DIFFICULTY_THRESHOLDS,
        share_tokens=SHARE_TOKENS,
        description=DESCRIPTION,
    )


DESCRIPTOR = build_descriptor()


__all__ = [
    "ACCESSIBILITY",
    "BASE",
    "CODE_LENGTHS",
    "DESCRIPTION",
    "DESCRIPTOR",
    "DIFFICULTY_THRESHOLDS",
    "GAME_ID",
    "GAME_VERSION",
    "LENGTH_STREAM",
    "MAX_CLUES",
    "PROPERTIES_BY_BAND",
    "PROPERTY_COUNT",
    "SEARCH_BOUND",
    "SHARE_TOKENS",
    "STATE_SYMBOLS",
    "SUPPORTED_BANDS",
    "build_descriptor",
    "code_length_for",
    "code_lengths_for",
    "length_rng",
    "properties_for",
]
