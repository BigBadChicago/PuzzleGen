"""How hard the board is, with code length divided out as far as it goes.

A four-digit board has a space of 5,040 codes and a three-digit one 720, and
the four-digit board needs about one more clue to pin its answer down. Some of
that difference is difficulty and some is only size, and this model separates
them where it can and says where it cannot.

What the three features say, in one sentence each.

How much the board does not state: a property clue is free information, so a
board that says nothing about its digits makes the solver find both the digits
and their order from guesses alone. This is the largest weight because it is
the lever the band turns.

How many clue rows there are to cross-reference: the task is holding every
clue against every candidate, and a seven-row grid is visibly more of that
than a three-row one. Over the maximum a board may show, so it is a share.

How much the final clue had to do: a board whose last clue eliminated forty
surviving codes asked the solver to carry forty candidates to the end, and one
whose last clue eliminated one was nearly solved already. Saturated, because
past a point the solver is not holding candidates individually anyway.

Where size is not divided out, and the honest statement of it: the clue share
is normalised against ``MAX_CLUES`` rather than against the clue count typical
of each length, so a four-digit board scores higher than a three-digit one for
needing about one more clue. Measured over the boards the engine reaches, the
mean gap is 0.090 on an easy board, 0.040 on a medium one and 0.023 on a hard
one. It is largest at the easy end because that is where the clue counts
differ most: with all three facts stated a three-digit board needs a median of
three guess clues and a four-digit board four, while at the hard end both run
into the eight-clue ceiling and the difference is squeezed out.

That 0.090 is inside each band's own range rather than across a cut point, so
it moves a board within its band and not between bands -- which is what
``test_digits_difficulty.py`` holds it to. The alternative, a per-length
normalising constant fitted to the generator's output, would make the number
disappear rather than make it smaller, and this model has little enough in it
that a reader should be able to see where the size went.
"""

from __future__ import annotations

from collections.abc import Mapping

from puzzlegen.engine.plugin import DifficultyMeasurement, Puzzle, VerificationResult

from .descriptor import MAX_CLUES, PROPERTY_COUNT
from .verify import BEFORE_LAST, CLUE_COUNT, FACT_COUNT

#: What each feature contributes. They sum to one, so the score needs no
#: rescaling and a weight reads as "this much of the answer".
WEIGHTS: Mapping[str, float] = {
    "unstated_share": 0.4,
    "clue_share": 0.35,
    "endgame_share": 0.25,
}

#: Surviving codes before the final clue at which the board scores as fully
#: hard.
#:
#: Twenty. Measured over the generator's own output: the count before the last
#: clue ran from two to about sixty, with most boards under twenty, so a
#: saturation at the top of the range would compress nearly every board into
#: the bottom of the feature and measure almost nothing.
ENDGAME_SATURATION = 20.0

#: Confidence reported with the measurement.
#:
#: Modest, and it should be. These weights are reasoned rather than fitted,
#: and nothing has calibrated them against a played board.
CONFIDENCE = 0.4


def unstated_share(puzzle: Puzzle, verification: VerificationResult) -> float:
    """How much of the code's description the board withholds.

    One minus the share of the available facts it states. A board stating all
    three scores zero and a board stating none scores one, which is the
    direction the bands ask for.
    """
    stated = verification.metrics.get(FACT_COUNT)
    if stated is None:
        stated = float(puzzle.payload.get("fact_count", 0))
    if PROPERTY_COUNT <= 0:  # pragma: no cover - the kinds are a real list
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(stated) / PROPERTY_COUNT))


def clue_share(puzzle: Puzzle, verification: VerificationResult) -> float:
    """How many clue rows there are, over the most a board may show."""
    clues = verification.metrics.get(CLUE_COUNT)
    if clues is None:
        clues = float(puzzle.payload.get("clue_count", 0))
    if MAX_CLUES <= 0:  # pragma: no cover - the ceiling is a real number
        return 0.0
    return max(0.0, min(1.0, float(clues) / MAX_CLUES))


def endgame_share(puzzle: Puzzle, verification: VerificationResult) -> float:
    """How many codes survived every clue but the last, saturated.

    The measure of how much the solver had to carry. Read from the generator's
    own elimination through the verifier's metrics, so it costs nothing here
    and cannot disagree with what the board was built from.
    """
    before = verification.metrics.get(BEFORE_LAST)
    if before is None:
        before = float(puzzle.solution.get("before_last", 0))
    return max(0.0, min(1.0, float(before) / ENDGAME_SATURATION))


def measure_difficulty(
    puzzle: Puzzle, verification: VerificationResult
) -> DifficultyMeasurement:
    """One score on [0, 1], with the features that produced it."""
    features = {
        "unstated_share": unstated_share(puzzle, verification),
        "clue_share": clue_share(puzzle, verification),
        "endgame_share": endgame_share(puzzle, verification),
    }
    score = sum(WEIGHTS[name] * value for name, value in features.items())

    # Recorded, not scored. A later calibration will want the raw shape of the
    # board beside the score it produced.
    features["code_length"] = float(puzzle.payload["code_length"])
    features["clue_count"] = float(puzzle.payload.get("clue_count", 0))
    features["fact_count"] = float(puzzle.payload.get("fact_count", 0))
    features["before_last"] = float(puzzle.solution.get("before_last", 0))
    features["space_size"] = float(puzzle.solution.get("space_size", 0))

    return DifficultyMeasurement(
        score=max(0.0, min(1.0, score)),
        confidence=CONFIDENCE,
        features=features,
        detail=(
            f"unstated {features['unstated_share']:.2f}, "
            f"clues {features['clue_share']:.2f}, "
            f"endgame {features['endgame_share']:.2f}"
        ),
    )


def size_neutrality_check(scores: Mapping[int, float]) -> float:
    """Spread of scores across code lengths, for calibration.

    Not used in play. It exists so the claim this module makes about where
    size is and is not divided out can be measured against real boards rather
    than asserted in a docstring.
    """
    lengths = [length for length in scores if length > 0]
    if len(lengths) < 2:
        return 0.0
    values = [scores[length] for length in lengths]
    return max(values) - min(values)


__all__ = [
    "CONFIDENCE",
    "ENDGAME_SATURATION",
    "WEIGHTS",
    "clue_share",
    "endgame_share",
    "measure_difficulty",
    "size_neutrality_check",
    "unstated_share",
]
