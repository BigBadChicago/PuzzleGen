"""How hard the board is, with board size divided out.

A nine-per-group day is mechanically larger than a five-per-group one: more
tiles, more candidate groups, more states for the verifier to walk. None of
that is difficulty. If the raw numbers reached the bands, every wide board
would land in HARD for a reason that has nothing to do with the puzzle, and
the band would stop meaning anything a player could feel.

So every feature here is expressed as a ratio against what the same board
shape would produce at its easiest, and only then combined. The features are
reported alongside the score because an unexplained score cannot be audited or
improved, and because the first real snapshot will almost certainly show these
weights need moving.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from ...engine.plugin import DifficultyMeasurement, Puzzle, VerificationResult
from ...engine.uniqueness import COLLAPSED_SOLUTIONS
from .descriptor import MAX_GROUP_SIZE, MIN_GROUP_SIZE, VISIBLE_GROUPS
from .verify import (
    CANDIDATE_GROUPS,
    memberships_of,
    signature_of,
    universal_categories,
)

#: What each feature contributes. They sum to one, so the score needs no
#: rescaling and a weight can be read as "this much of the answer".
#:
#: Cross-group pull dominates because it is the thing a player actually
#: struggles with: a tile that could plausibly sit in two groups is the whole
#: difficulty of this format. Search cost is included at a low weight as a
#: proxy for how tangled the board is, not because a slow search is hard to
#: play.
WEIGHTS: Mapping[str, float] = {
    "cross_group_pull": 0.45,
    "hidden_evenness": 0.2,
    "signature_thinness": 0.2,
    "search_cost": 0.15,
}

#: Cross-group pull at or above this counts as fully hard.
#:
#: Expressed per tile pair rather than as a raw count, so it does not grow
#: with board size on its own. A pull of 0.25 means a quarter of all
#: cross-group pairs share some category, which in practice is a board where
#: most tiles look like they might belong elsewhere.
PULL_SATURATION = 0.25

#: Confidence reported with the measurement.
#:
#: Modest, and it should be. These weights are reasoned rather than fitted,
#: and nothing has calibrated them against a real board yet.
CONFIDENCE = 0.4


def cross_group_pull(puzzle: Puzzle) -> float:
    """How often tiles in different groups share a category.

    Counted over pairs and divided by the number of pairs, so a 36-tile board
    is not automatically more tempting than a 20-tile one. This is the feature
    that corresponds to the experience of the game: the tile you keep putting
    in the wrong place shares something with the wrong place.
    """
    memberships = memberships_of(puzzle)
    universal = universal_categories(memberships)
    assignment: Mapping[str, int] = puzzle.solution["assignment"]
    tiles = sorted(assignment)

    pairs = 0
    pulled = 0
    for index, left in enumerate(tiles):
        for right in tiles[index + 1 :]:
            if assignment[left] == assignment[right]:
                continue
            pairs += 1
            shared = signature_of(left, memberships, universal) & signature_of(
                right, memberships, universal
            )
            if shared:
                pulled += 1
    if pairs == 0:
        return 0.0
    return min(1.0, (pulled / pairs) / PULL_SATURATION)


def hidden_evenness(puzzle: Puzzle) -> float:
    """How evenly the hidden group is spread across the visible groups.

    A hidden group taking most of its members from one visible group is
    visible as a lump. One spread evenly leaves no group looking different, so
    an even spread is harder and scores higher. Normalised by the most uneven
    spread the same group size permits, which is what keeps it comparable
    between a five and a nine.
    """
    assignment: Mapping[str, int] = puzzle.solution["assignment"]
    members = puzzle.solution["hidden"]["members"]
    size = len(members)
    if size == 0:
        return 0.0

    counts = [0] * VISIBLE_GROUPS
    for tile in members:
        counts[assignment[tile]] += 1

    ideal = size / VISIBLE_GROUPS
    deviation = sum(abs(count - ideal) for count in counts)
    # Worst case with every group touched: one group takes everything the
    # others do not, which is the arrangement the borrowing rule permits and
    # still dislikes most.
    worst = 2 * (size - 1 - ideal) if size > VISIBLE_GROUPS else 1.0
    if worst <= 0:
        return 1.0
    return max(0.0, 1.0 - deviation / worst)


def signature_thinness(puzzle: Puzzle) -> float:
    """How little the graph says about each tile.

    A tile carrying one distinguishing category gives a player one thing to
    reason from; a tile carrying eight gives them eight, and one of them will
    be the group. Thin signatures make a harder board. Scaled against a
    generous ceiling rather than the board's own maximum, so a uniformly
    thin board does not come out average.
    """
    memberships = memberships_of(puzzle)
    universal = universal_categories(memberships)
    if not memberships:
        return 0.0
    sizes = [
        len(signature_of(tile, memberships, universal)) for tile in memberships
    ]
    mean = sum(sizes) / len(sizes)
    ceiling = 8.0
    return max(0.0, min(1.0, 1.0 - (mean - 1.0) / (ceiling - 1.0)))


def expected_states(group_size: int) -> float:
    """Roughly how many states an unconstrained search of this shape walks.

    The normaliser for search cost. Anchoring makes the enumerator pick each
    group from the remaining tiles, so the count is dominated by the first
    group's choices; this is an order-of-magnitude figure, which is all a log
    ratio needs.
    """
    tiles = group_size * VISIBLE_GROUPS
    return max(
        2.0, float(math.comb(tiles - 1, group_size - 1)) * float(VISIBLE_GROUPS)
    )


def search_cost(
    puzzle: Puzzle, verification: VerificationResult
) -> float:
    """States actually examined, against what this board shape would cost anyway.

    A log ratio, because the raw counts differ by orders of magnitude between
    a five and a nine and a linear ratio would be a board-size measurement
    wearing a difficulty label.
    """
    examined = float(
        verification.metrics.get(CANDIDATE_GROUPS, verification.states_examined)
    )
    if examined <= 1.0:
        return 0.0
    baseline = expected_states(int(puzzle.payload["group_size"]))
    ratio = math.log(examined + 1.0) / math.log(baseline + 1.0)
    return max(0.0, min(1.0, ratio))


def measure_difficulty(
    puzzle: Puzzle, verification: VerificationResult
) -> DifficultyMeasurement:
    """One score on [0, 1], with the features that produced it.

    Board size appears nowhere in the score and everywhere in the
    normalisation, which is the whole arrangement: the bands then cut a scale
    that carries only difficulty, and even thirds are the right cut.
    """
    features = {
        "cross_group_pull": cross_group_pull(puzzle),
        "hidden_evenness": hidden_evenness(puzzle),
        "signature_thinness": signature_thinness(puzzle),
        "search_cost": search_cost(puzzle, verification),
    }
    score = sum(WEIGHTS[name] * value for name, value in features.items())

    # Recorded, not scored. A board whose alternatives collapsed is not harder
    # for having done so, but a later calibration will want to know which
    # boards did.
    features["collapsed_solutions"] = float(
        verification.metrics.get(COLLAPSED_SOLUTIONS, 0.0)
    )
    features["group_size"] = float(puzzle.payload["group_size"])
    features["board_size"] = float(
        int(puzzle.payload["group_size"]) * VISIBLE_GROUPS
    )

    return DifficultyMeasurement(
        score=max(0.0, min(1.0, score)),
        confidence=CONFIDENCE,
        features=features,
        detail=(
            f"pull {features['cross_group_pull']:.2f}, "
            f"evenness {features['hidden_evenness']:.2f}, "
            f"thinness {features['signature_thinness']:.2f}, "
            f"search {features['search_cost']:.2f}"
        ),
    )


def size_neutrality_check(scores: Mapping[int, float]) -> float:
    """Spread of scores across group sizes, for calibration.

    Not used in play. It exists so the claim this module makes, that board
    size is divided out, can be measured against real boards in batch 6
    instead of being asserted in a docstring.
    """
    sizes = [size for size in scores if MIN_GROUP_SIZE <= size <= MAX_GROUP_SIZE]
    if len(sizes) < 2:
        return 0.0
    values = [scores[size] for size in sizes]
    return max(values) - min(values)


__all__ = [
    "CONFIDENCE",
    "PULL_SATURATION",
    "WEIGHTS",
    "cross_group_pull",
    "expected_states",
    "hidden_evenness",
    "measure_difficulty",
    "search_cost",
    "signature_thinness",
    "size_neutrality_check",
]
