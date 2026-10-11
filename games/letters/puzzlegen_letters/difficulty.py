"""How hard the board is, with board size divided out.

A twelve-tile board is mechanically larger than a six-tile one: more tiles to
read, more words to recognise. Almost none of that is difficulty. If the raw
numbers reached the bands, every wide board would land in HARD for a reason
that has nothing to do with the puzzle, and the band would stop meaning
anything a player could feel.

So every feature here is a share rather than a count: blanks against the
answer's length, unmarked tiles against the board, manufactured blanks against
the blanks. A share is already normalised by the shape it came from, which is
what makes a six-tile board and a twelve-tile board comparable at all. The
features are reported alongside the score because an unexplained score cannot
be audited or improved, and because the weights here are reasoned rather than
fitted to play data nobody has yet.

What the features say, in one sentence each: the more of the answer the board
withholds the harder it is, the longer the answer the more there is to
assemble, the more tiles carry nothing the more there is to read past, the
more other words the same letters spell the longer the solver hesitates, and a
manufactured blank is a letter the board could have shown and chose not to.

The first two are both here because the settled rule has two halves -- harder
means more blanks *and* longer words -- and one share cannot carry both. Blanks
against length rises as a board withholds more of a word; length on its own
rises as the word gets longer, which the first one by itself would read as
easier.
"""

from __future__ import annotations

from collections.abc import Mapping

from puzzlegen.engine.plugin import DifficultyMeasurement, Puzzle, VerificationResult

from .assemble import board_marks, unmarked_tiles
from .verify import LETTER_ALTERNATIVES, MANUFACTURED_BLANKS

#: What each feature contributes. They sum to one, so the score needs no
#: rescaling and a weight can be read as "this much of the answer".
#:
#: The two halves of the settled rule carry half the score between them, the
#: blank share the larger half: blanks are what difficulty sets directly, and
#: a feature set that let something else outweigh them would band a two-blank
#: board hard and make the rule untrue. The other three are what the content
#: produced rather than what difficulty asked for, which is the part of a
#: measurement worth auditing.
WEIGHTS: Mapping[str, float] = {
    "blank_share": 0.3,
    "length_share": 0.2,
    "decoy_share": 0.2,
    "letter_alternatives": 0.2,
    "manufactured_share": 0.1,
}

#: The shortest and longest answers this game's content holds.
#:
#: Three letters is the floor a two-blank board admits, since a name the
#: blanks would spell entirely is not a puzzle, and seven is the longest name
#: the smallest board can mark. They are the ends of the scale ``length_share``
#: normalises against, so a build whose names run longer has to move them:
#: a ceiling left at seven would score every longer name as fully hard.
SHORTEST_ANSWER = 3
LONGEST_ANSWER = 7

#: Alternative words at or above this count score as fully hard.
#:
#: Four. On build A2's thirty categories a board's marks typically spell one
#: or two other category names and never more than a handful, so a saturation
#: much higher than this would compress the feature into the bottom of its
#: range and measure nothing. It is a property of the vocabulary's size, so a
#: build with ten times the categories should expect to raise it.
ALTERNATIVE_SATURATION = 4.0

#: Confidence reported with the measurement.
#:
#: Modest, and it should be. These weights are reasoned rather than fitted,
#: and nothing has calibrated them against a played board.
CONFIDENCE = 0.4


def blank_share(puzzle: Puzzle) -> float:
    """How much of the answer the board withholds.

    The share rather than the count, so a three-blank answer of four letters
    is harder than a three-blank answer of seven, which is right: the shorter
    answer gives the solver one letter to work from.
    """
    length = int(puzzle.payload["answer_length"])
    if length <= 0:
        return 0.0
    return min(1.0, int(puzzle.payload["blanks"]) / length)


def length_share(puzzle: Puzzle) -> float:
    """How long the answer is, against the longest this content holds.

    The other half of the settled rule. A seven-letter answer is more to
    assemble than a four-letter one at the same blank count: more marks to
    gather, more arrangements to try, and more of the word to recognise before
    the letters confirm it. Normalised against the content's own range rather
    than against the board, because the answer's length is a property of the
    category's name and not of how many tiles it was laid out on.
    """
    length = int(puzzle.payload["answer_length"])
    span = LONGEST_ANSWER - SHORTEST_ANSWER
    if span <= 0:  # pragma: no cover - the constants are a real range
        return 0.0
    return max(0.0, min(1.0, (length - SHORTEST_ANSWER) / span))


def decoy_share(puzzle: Puzzle) -> float:
    """How much of the board carries no letter.

    An unmarked tile still has to be read and recognised, and it offers
    nothing towards the spelling, so it is the board's noise. As a share it is
    comparable between sizes; as a count it would only say how large the board
    was.
    """
    board_size = int(puzzle.payload["board_size"])
    if board_size <= 0:
        return 0.0
    return len(unmarked_tiles(puzzle)) / board_size


def manufactured_share(puzzle: Puzzle) -> float:
    """How many blanks were the generator's choice rather than the content's.

    A manufactured blank is a letter a tile could have carried, withheld to
    hit the day's difficulty, so it is withheld information in exactly the way
    difficulty asks for. It is the smallest weight because 4.4 already treats
    manufacturing as a cost to minimise: a model that rewarded it would push
    the generator towards the boards that ranking deliberately avoids.
    """
    blanks = int(puzzle.payload["blanks"])
    if blanks <= 0:
        return 0.0
    manufactured = len(puzzle.solution.get("manufactured_blanks", ()))
    return min(1.0, manufactured / blanks)


def letter_alternatives(verification: VerificationResult) -> float:
    """How many other words the board's own letters spell.

    Measured by the verifier, which has already scanned the vocabulary, so
    this costs nothing here. These are not answers -- each is untrue of at
    least one tile -- which is precisely why they are difficulty: the solver
    cannot tell that from the letters, and has to look at the tiles to rule
    them out.
    """
    count = float(verification.metrics.get(LETTER_ALTERNATIVES, 0.0))
    return max(0.0, min(1.0, count / ALTERNATIVE_SATURATION))


def measure_difficulty(
    puzzle: Puzzle, verification: VerificationResult
) -> DifficultyMeasurement:
    """One score on [0, 1], with the features that produced it.

    Board size appears nowhere in the score and everywhere in the
    normalisation, which is the whole arrangement: the bands then cut a scale
    that carries only difficulty.
    """
    features = {
        "blank_share": blank_share(puzzle),
        "length_share": length_share(puzzle),
        "decoy_share": decoy_share(puzzle),
        "letter_alternatives": letter_alternatives(verification),
        "manufactured_share": manufactured_share(puzzle),
    }
    score = sum(WEIGHTS[name] * value for name, value in features.items())

    # Recorded, not scored. A later calibration will want the raw shape of the
    # board beside the score it produced.
    features["blanks"] = float(puzzle.payload["blanks"])
    features["board_size"] = float(puzzle.payload["board_size"])
    features["answer_length"] = float(puzzle.payload["answer_length"])
    features["marked_tiles"] = float(len(board_marks(puzzle)))
    features["manufactured_blanks"] = float(
        verification.metrics.get(MANUFACTURED_BLANKS, 0.0)
    )

    return DifficultyMeasurement(
        score=max(0.0, min(1.0, score)),
        confidence=CONFIDENCE,
        features=features,
        detail=(
            f"blanks {features['blank_share']:.2f}, "
            f"length {features['length_share']:.2f}, "
            f"decoys {features['decoy_share']:.2f}, "
            f"alternatives {features['letter_alternatives']:.2f}, "
            f"manufactured {features['manufactured_share']:.2f}"
        ),
    )


def size_neutrality_check(scores: Mapping[int, float]) -> float:
    """Spread of scores across board sizes, for calibration.

    Not used in play. It exists so the claim this module makes, that board
    size is divided out, can be measured against real boards rather than
    asserted in a docstring.
    """
    sizes = [size for size in scores if size > 0]
    if len(sizes) < 2:
        return 0.0
    values = [scores[size] for size in sizes]
    return max(values) - min(values)


__all__ = [
    "ALTERNATIVE_SATURATION",
    "CONFIDENCE",
    "LONGEST_ANSWER",
    "SHORTEST_ANSWER",
    "WEIGHTS",
    "blank_share",
    "decoy_share",
    "length_share",
    "letter_alternatives",
    "manufactured_share",
    "measure_difficulty",
    "size_neutrality_check",
]
