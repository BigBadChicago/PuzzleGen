"""How hard the board is, with board size divided out.

A seven-tile board is a longer list to check than a five-tile one, and almost
none of that difference is difficulty: it is the same act repeated. So every
feature here is a share rather than a count, which is what makes the three
sizes comparable and lets the bands cut a scale carrying only difficulty.

What the four features say, in one sentence each.

How obscure the shared property is: "even" and "triangular" are both true of
six, and a player who needs a moment for the first needs a different kind of
moment for the second. This is the largest weight because it is the thing the
band asks for and the thing the solver actually meets.

How few reasons the answer has: a board whose odd number is the only one that
is neither even nor a multiple of three gives the solver two ways in, and a
board with one gives them one. More reasons is an easier board, so the feature
is the shortage of them.

How many near readings the board holds: a property true of all but exactly two
tiles is what a solver tries and discards, and the share of them is how slow
the board is rather than how invisible.

How tight the numbers are: a board of 11, 13, 17, 19 and 24 offers nothing to
the eye but the properties, while one spanning the whole window gives the
solver a shape to look at first. Tightness is the reciprocal of spread, so a
tight board scores high.
"""

from __future__ import annotations

from collections.abc import Mapping

from puzzlegen.engine.plugin import DifficultyMeasurement, Puzzle, VerificationResult

from .assemble import board_values
from .descriptor import WINDOW
from .properties import obscurity_of
from .verify import NEAR_READINGS, REASONS, VOCABULARY_SIZE

#: What each feature contributes. They sum to one, so the score needs no
#: rescaling and a weight reads as "this much of the answer".
#:
#: Obscurity carries the largest share because it is the lever the band turns.
#: The reason shortage is next, since it is the other thing the generator
#: ranks on. Near readings and tightness are what the draw happened to
#: produce rather than what difficulty asked for, which is the part of a
#: measurement most worth auditing and the part that should not be able to
#: outweigh the rest.
WEIGHTS: Mapping[str, float] = {
    "obscurity": 0.4,
    "reason_shortage": 0.25,
    "near_share": 0.2,
    "tightness": 0.15,
}

#: Reasons at or above which the answer counts as fully confirmed.
#:
#: Three. Measured over the generator's own output: a board's odd number is
#: picked out by one property most often and by four at the extreme, so a
#: saturation of three puts the feature's floor where most boards sit and
#: leaves the top of its range reachable. A saturation equal to the
#: vocabulary's size would compress almost every board into the hard end.
REASON_SATURATION = 3.0

#: Confidence reported with the measurement.
#:
#: Modest, and it should be. These weights are reasoned rather than fitted,
#: and the obscurity tiers they rest on are a curator's judgement rather than
#: a measurement.
CONFIDENCE = 0.4


def obscurity(puzzle: Puzzle) -> float:
    """How unfamiliar the shared property is.

    Read from the property's tier through ``properties.obscurity_of``, which
    is the one place this game's judgement about its content lives. An
    unrecognised property rates as most obscure, which is the safe direction:
    a build that grew a category nobody has rated would otherwise have it
    treated as familiar as "even" on an easy day.
    """
    return obscurity_of(str(puzzle.solution.get("property", "")))


def reason_shortage(puzzle: Puzzle, verification: VerificationResult) -> float:
    """How few properties pick out the answer.

    One reason scores high and three or more score zero. The count comes from
    the verifier, which has already scanned the vocabulary, and falls back to
    the board's own record for the diagnostic tools that measure difficulty
    without a verification in hand.
    """
    measured = verification.metrics.get(REASONS)
    if measured is None:
        measured = float(len(puzzle.solution.get("reasons", ())))
    reasons = max(1.0, float(measured))
    return max(0.0, min(1.0, (REASON_SATURATION - reasons) / (REASON_SATURATION - 1.0)))


def near_share(puzzle: Puzzle, verification: VerificationResult) -> float:
    """How much of the vocabulary is a near reading.

    A share of the vocabulary rather than a count, because the vocabulary's
    size is a property of the build: a taxonomy with twice the categories
    would double every count without making any board harder.
    """
    near = verification.metrics.get(NEAR_READINGS)
    if near is None:
        near = float(len(puzzle.solution.get("near", ())))
    size = verification.metrics.get(VOCABULARY_SIZE)
    if size is None:
        size = float(len(puzzle.solution.get("vocabulary", ())))
    if not size:
        return 0.0
    return max(0.0, min(1.0, float(near) / float(size)))


def tightness(puzzle: Puzzle) -> float:
    """How little of the window the board's numbers span.

    One minus the spread over the window's width. A board filling the window
    scores zero and a board of five consecutive numbers scores nearly one,
    which is the right direction: the tighter the board, the less its
    magnitudes tell the solver and the more the properties have to.
    """
    values = board_values(puzzle)
    if len(values) < 2:
        return 0.0
    low, high = WINDOW
    width = high - low
    if width <= 0:  # pragma: no cover - the window is a real range
        return 0.0
    spread = max(values.values()) - min(values.values())
    return max(0.0, min(1.0, 1.0 - spread / width))


def measure_difficulty(
    puzzle: Puzzle, verification: VerificationResult
) -> DifficultyMeasurement:
    """One score on [0, 1], with the features that produced it."""
    features = {
        "obscurity": obscurity(puzzle),
        "reason_shortage": reason_shortage(puzzle, verification),
        "near_share": near_share(puzzle, verification),
        "tightness": tightness(puzzle),
    }
    score = sum(WEIGHTS[name] * value for name, value in features.items())

    # Recorded, not scored. A later calibration will want the raw shape of the
    # board beside the score it produced.
    values = board_values(puzzle)
    features["board_size"] = float(puzzle.payload["board_size"])
    features["reasons"] = float(len(puzzle.solution.get("reasons", ())))
    features["near_readings"] = float(len(puzzle.solution.get("near", ())))
    features["spread"] = float(
        max(values.values()) - min(values.values()) if len(values) > 1 else 0
    )

    return DifficultyMeasurement(
        score=max(0.0, min(1.0, score)),
        confidence=CONFIDENCE,
        features=features,
        detail=(
            f"obscurity {features['obscurity']:.2f}, "
            f"reasons {features['reason_shortage']:.2f}, "
            f"near {features['near_share']:.2f}, "
            f"tightness {features['tightness']:.2f}"
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
    "CONFIDENCE",
    "REASON_SATURATION",
    "WEIGHTS",
    "measure_difficulty",
    "near_share",
    "obscurity",
    "reason_shortage",
    "size_neutrality_check",
    "tightness",
]
