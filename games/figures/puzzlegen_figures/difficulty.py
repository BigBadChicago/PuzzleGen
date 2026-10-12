"""How hard the board is, with board size divided out.

A twelve-tile board holds two hundred and twenty triples and a nine-tile board
eighty-four. Almost none of that difference is difficulty: it is the same task
over a larger field, and if the raw counts reached the bands every wide board
would land in HARD for a reason the player cannot feel. So every feature here
is a share rather than a count, which is what makes two board sizes comparable
at all.

What the four features say, in one sentence each.

How much of the trio has to be assembled rather than seen: a trio that is
three red triangles differing only in fill is one glance, and a trio with
nothing in common is four separate readings. This is the largest weight
because it is what the solver's eye actually does.

How many triples are near misses: a triple right about three attributes and
wrong about the fourth is the one that gets read out to the end, so the share
of them is how slow the board is rather than how invisible.

How much of the attribute space is in play: a board using all three values of
every attribute offers more to compare than one where every tile is red.

How far apart the trio sits: three tiles bunched in one corner are one glance
and three scattered across the grid are three. This is the one feature that
depends on the layout, which is why it is measured on the assembled board
rather than on the plan.

The weights are reasoned rather than fitted, and the confidence reported says
so. Nothing has calibrated them against a played board, because nobody has
played one.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations

from puzzlegen.engine.plugin import DifficultyMeasurement, Puzzle, VerificationResult

from .assemble import board_figures, position_of
from .trio import ATTRIBUTES, TRIO, VALUES_PER_ATTRIBUTE
from .verify import DECOY_SHARE

#: What each feature contributes. They sum to one, so the score needs no
#: rescaling and a weight reads as "this much of the answer".
#:
#: Variation carries the largest share because it is the lever the band asks
#: for first and the one a solver feels; the decoy share is next because it is
#: the other thing the generator targets. Variety and dispersion are what the
#: content and the shuffle happened to produce rather than what difficulty
#: asked for, which is the part of a measurement most worth auditing and the
#: part that should not be able to outweigh the rest.
WEIGHTS: Mapping[str, float] = {
    "variation_share": 0.4,
    "decoy_share": 0.3,
    "variety_share": 0.15,
    "dispersion_share": 0.15,
}

#: Decoy share at or above which a board scores as fully hard.
#:
#: 0.20. Measured, not chosen: over five hundred generated boards at each
#: size the share ran 0.024 to 0.202 at nine tiles and 0.045 to 0.150 at
#: twelve. A saturation much above the observed ceiling would compress the
#: feature into the bottom of its range and measure almost nothing, which is
#: what a saturation of one would have done.
DECOY_SATURATION = 0.20

#: Confidence reported with the measurement.
#:
#: Modest, and it should be. These weights are reasoned rather than fitted.
CONFIDENCE = 0.4


def variation_share(puzzle: Puzzle) -> float:
    """How many of the trio's attributes are all different rather than all same.

    Over the number of attributes, so it is a share: three of four is 0.75
    whatever the board size. Never zero in practice, because a trio all-same
    on every attribute would be three copies of one figure and the tiles are
    distinct entities; the formula does not rely on that.
    """
    varying = len(puzzle.solution.get("varying", ()))
    return min(1.0, varying / len(ATTRIBUTES)) if ATTRIBUTES else 0.0


def decoy_share(puzzle: Puzzle, verification: VerificationResult) -> float:
    """How many triples fail on exactly one attribute, saturated.

    Read from the verifier, which has already enumerated every triple, so
    this costs nothing here. Falls back to the board's own recorded share when
    a caller measures difficulty without a verification in hand, which the
    diagnostic tools do.
    """
    measured = verification.metrics.get(DECOY_SHARE)
    if measured is None:
        measured = float(puzzle.solution.get("decoy_share", 0.0))
    return max(0.0, min(1.0, float(measured) / DECOY_SATURATION))


def variety_share(puzzle: Puzzle) -> float:
    """How much of the attribute space the board puts in play.

    The mean, across attributes, of how many of the three values appear on the
    board. A board where every tile is red has nothing to compare on colour,
    which makes the other three attributes the whole puzzle and the puzzle
    smaller.
    """
    figures = board_figures(puzzle)
    if not figures or not ATTRIBUTES:
        return 0.0
    shares = []
    for attribute in ATTRIBUTES:
        present = {reading[attribute] for reading in figures.values()}
        shares.append(min(1.0, len(present) / VALUES_PER_ATTRIBUTE))
    return sum(shares) / len(shares)


def dispersion_share(puzzle: Puzzle) -> float:
    """How far apart the trio sits on the laid-out grid.

    Mean pairwise Chebyshev distance between the trio's cells, over the
    largest that distance could be on this grid. Chebyshev rather than
    Manhattan because the eye scans rows, columns and diagonals alike: two
    tiles one row and one column apart are as close as two tiles one column
    apart, and Manhattan would call the first further.

    One consequence of that choice is worth stating, because it reads as a
    bug otherwise: a trio along a row and a trio down the main diagonal score
    exactly the same, 0.67 on a three-by-three grid. That is the metric
    agreeing with its own justification -- a diagonal is as findable as a row
    -- rather than failing to tell them apart. What the feature separates is a
    trio clustered in one corner, which scores 0.5, from a trio with a tile in
    three different regions of the grid, which approaches 1.
    """
    positions = position_of(puzzle)
    trio = [tile_id for tile_id in puzzle.solution.get("trio", ()) if tile_id in positions]
    if len(trio) != TRIO:
        return 0.0
    rows = int(puzzle.payload["rows"])
    columns = int(puzzle.payload["columns"])
    furthest = max(rows - 1, columns - 1)
    if furthest <= 0:
        return 0.0
    distances = [
        max(
            abs(positions[first][0] - positions[second][0]),
            abs(positions[first][1] - positions[second][1]),
        )
        for first, second in combinations(trio, 2)
    ]
    return min(1.0, (sum(distances) / len(distances)) / furthest)


def measure_difficulty(
    puzzle: Puzzle, verification: VerificationResult
) -> DifficultyMeasurement:
    """One score on [0, 1], with the features that produced it.

    Board size appears nowhere in the score and everywhere in the
    normalisation, which is the whole arrangement: the bands then cut a scale
    that carries only difficulty.
    """
    features = {
        "variation_share": variation_share(puzzle),
        "decoy_share": decoy_share(puzzle, verification),
        "variety_share": variety_share(puzzle),
        "dispersion_share": dispersion_share(puzzle),
    }
    score = sum(WEIGHTS[name] * value for name, value in features.items())

    # Recorded, not scored. A later calibration will want the raw shape of the
    # board beside the score it produced.
    features["board_size"] = float(puzzle.payload["board_size"])
    features["varying_attributes"] = float(len(puzzle.solution.get("varying", ())))
    features["near_misses"] = float(puzzle.solution.get("near_misses", 0))
    features["raw_decoy_share"] = float(
        verification.metrics.get(DECOY_SHARE, puzzle.solution.get("decoy_share", 0.0))
    )

    return DifficultyMeasurement(
        score=max(0.0, min(1.0, score)),
        confidence=CONFIDENCE,
        features=features,
        detail=(
            f"variation {features['variation_share']:.2f}, "
            f"decoys {features['decoy_share']:.2f}, "
            f"variety {features['variety_share']:.2f}, "
            f"dispersion {features['dispersion_share']:.2f}"
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
    "DECOY_SATURATION",
    "WEIGHTS",
    "decoy_share",
    "dispersion_share",
    "measure_difficulty",
    "size_neutrality_check",
    "variation_share",
    "variety_share",
]
