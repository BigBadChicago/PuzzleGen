"""How hard a board is, and what the number is made of.

Two kinds of test. The first checks each feature says what its docstring
claims, on boards built to differ in one thing only. The second checks the
property the whole module exists for: that board size is divided out, so a
twelve-tile board and a six-tile board carrying the same puzzle score the
same, and the bands cut a scale that carries only difficulty.

The band separation is measured rather than asserted. The numbers in
``DIFFICULTY_THRESHOLDS`` came from scoring every candidate the generator
offered on ten days at each band against build A2; what is pinned here is the
shape that made those numbers possible -- the score rises with the blanks, and
rises with the answer's length -- because a feature that inverted either would
make the band table untrue while leaving every cutoff in place.
"""

from __future__ import annotations

from puzzlegen.core.types import DifficultyBand, VerificationCompleteness
from puzzlegen.engine.plugin import VerificationResult
from puzzlegen_letters.descriptor import DESCRIPTOR, blanks_for
from puzzlegen_letters.difficulty import (
    ALTERNATIVE_SATURATION,
    LONGEST_ANSWER,
    SHORTEST_ANSWER,
    WEIGHTS,
    blank_share,
    decoy_share,
    length_share,
    letter_alternatives,
    manufactured_share,
    measure_difficulty,
    size_neutrality_check,
)
from puzzlegen_letters.verify import LETTER_ALTERNATIVES, verify

from boards import FISH, fish_board, make_puzzle

#: Twelve members, so the same answer can be laid out on the largest board.
MANY_FISH = (
    *FISH,
    "sole",
    "perch",
    "mullet",
    "anchovy",
    "sardine",
    "mackerel",
)


def measured(board):
    return measure_difficulty(board, verify(board))


def result(alternatives: float = 0.0) -> VerificationResult:
    return VerificationResult(
        solvable=True,
        solution_count=1,
        completeness=VerificationCompleteness.COMPLETE,
        states_examined=4,
        metrics={LETTER_ALTERNATIVES: alternatives},
    )


class TestTheWeights:
    def test_they_sum_to_one_so_a_weight_reads_as_a_share_of_the_answer(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

    def test_the_two_halves_of_the_settled_rule_carry_half_the_score(self):
        """Harder means more blanks and longer words, and nothing else may
        outweigh the pair of them."""
        assert WEIGHTS["blank_share"] + WEIGHTS["length_share"] >= 0.5
        assert WEIGHTS["blank_share"] == max(WEIGHTS.values())


class TestTheFeatures:
    def test_the_blank_share_is_what_the_board_withholds(self):
        board = fish_board()
        assert blank_share(board) == 2 / 4

    def test_a_longer_answer_at_the_same_blanks_withholds_proportionally_less(self):
        short = make_puzzle("gem", {"garnet": "g"}, ["opal", "jade", "ruby"], blanks=2)
        long = make_puzzle(
            "emotion",
            {"anger": "n", "joy": "o", "envy": "e", "grief": "i", "pride": "p"},
            ["fear"],
            blanks=2,
        )
        assert blank_share(short) > blank_share(long)

    def test_the_length_share_rises_with_the_answer(self):
        short = make_puzzle("gem", {"garnet": "g"}, ["opal", "jade", "ruby"], blanks=2)
        long = make_puzzle(
            "emotion",
            {"anger": "n", "joy": "o", "envy": "e", "grief": "i", "pride": "p"},
            ["fear"],
            blanks=2,
        )
        assert length_share(long) > length_share(short)

    def test_the_length_share_is_normalised_against_the_content_s_own_range(self):
        shortest = make_puzzle("gem", {"garnet": "g"}, ["opal", "jade"], blanks=2)
        assert length_share(shortest) == 0.0
        assert SHORTEST_ANSWER < LONGEST_ANSWER

    def test_the_decoy_share_is_the_part_of_the_board_carrying_nothing(self):
        board = fish_board()
        assert decoy_share(board) == 4 / 6

    def test_the_manufactured_share_is_the_generator_s_own_contribution(self):
        board = fish_board(forced=["f"], manufactured=["s"])
        assert manufactured_share(board) == 0.5
        assert manufactured_share(fish_board(forced=["f", "i"])) == 0.0

    def test_alternatives_saturate_rather_than_growing_without_bound(self):
        assert letter_alternatives(result(0.0)) == 0.0
        assert letter_alternatives(result(ALTERNATIVE_SATURATION)) == 1.0
        assert letter_alternatives(result(ALTERNATIVE_SATURATION * 10)) == 1.0


class TestTheScore:
    def test_it_lies_on_the_unit_interval(self):
        score = measured(fish_board()).score
        assert 0.0 <= score <= 1.0

    def test_it_exposes_the_features_that_produced_it(self):
        """An unexplained score cannot be audited or improved."""
        features = measured(fish_board()).features
        for name in WEIGHTS:
            assert name in features

    def test_it_records_the_board_s_shape_beside_the_score(self):
        features = measured(fish_board()).features
        for name in ("blanks", "board_size", "answer_length", "marked_tiles"):
            assert name in features

    def test_more_blanks_on_the_same_answer_scores_harder(self):
        """The settled rule, stated as a property of the measurement."""
        two = make_puzzle(
            "emotion",
            {"anger": "n", "joy": "o", "envy": "e", "grief": "i", "pride": "p"},
            ["fear"],
            blanks=2,
        )
        four = make_puzzle(
            "emotion",
            {"anger": "n", "joy": "o", "grief": "i"},
            ["fear", "envy", "pride"],
            blanks=4,
        )
        assert measured(four).score > measured(two).score

    def test_a_longer_answer_at_the_same_blanks_scores_harder(self):
        """The other half of the rule, on the two features that carry it.

        A longer answer at the same blanks needs more marks, which on a board
        of a given size leaves fewer decoys, so two whole boards never differ
        in length alone and comparing their scores would be comparing the
        decoy share as well. What the rule is about is the pair below: the
        blank share falls with length, which is exactly why ``length_share``
        exists, and together they have to rise.
        """
        short = make_puzzle(
            "gem", {"garnet": "g"}, ["opal", "jade", "ruby", "amber"], blanks=2
        )
        long = make_puzzle(
            "emotion",
            {"anger": "n", "joy": "o", "envy": "e", "grief": "i", "pride": "p"},
            ["fear"],
            blanks=2,
        )

        def carried(board) -> float:
            return (
                WEIGHTS["blank_share"] * blank_share(board)
                + WEIGHTS["length_share"] * length_share(board)
            )

        assert blank_share(long) < blank_share(short)
        assert carried(long) > carried(short)

    def test_a_confidence_is_reported_and_is_modest(self):
        """These weights are reasoned rather than fitted to played boards."""
        assert 0.0 < measured(fish_board()).confidence < 0.6


class TestBoardSizeIsDividedOut:
    def test_the_same_puzzle_on_a_larger_board_scores_within_a_hair(self):
        """Not identically: a larger board carries more unmarked tiles, which
        is the one effect of size the model deliberately keeps, because the
        extra tiles are extra words to read past."""
        small = make_puzzle(
            "fish", {"herring": "h", "bass": "s"}, ["salmon", "trout", "carp", "halibut"]
        )
        large = make_puzzle(
            "fish",
            {"herring": "h", "bass": "s"},
            [name for name in MANY_FISH if name not in ("herring", "bass")],
        )
        assert abs(measured(large).score - measured(small).score) < 0.1

    def test_the_neutrality_check_reports_the_spread(self):
        small = make_puzzle(
            "fish", {"herring": "h", "bass": "s"}, ["salmon", "trout", "carp", "halibut"]
        )
        large = make_puzzle(
            "fish",
            {"herring": "h", "bass": "s"},
            [name for name in MANY_FISH if name not in ("herring", "bass")],
        )
        spread = size_neutrality_check(
            {6: measured(small).score, 12: measured(large).score}
        )
        assert spread < 0.1

    def test_one_size_alone_has_no_spread(self):
        assert size_neutrality_check({6: 0.4}) == 0.0


class TestTheBandsCutWhereTheMeasurementSeparates:
    #: The ranges measured on build A2, one per blank count. Recorded here as
    #: the numbers the cutoffs were chosen against, so a change to a feature
    #: that moved a band shows up as a failure here rather than as off-target
    #: days nobody traced back.
    MEASURED_RANGES = {2: (0.317, 0.445), 3: (0.395, 0.663), 4: (0.483, 0.757)}

    def test_each_band_s_measured_middle_lands_in_that_band(self):
        for band in DESCRIPTOR.supported_bands:
            low, high = self.MEASURED_RANGES[blanks_for(band)]
            middle = (low + high) / 2
            assert DESCRIPTOR.band_for(middle) is band

    def test_the_cutoffs_sit_inside_the_overlap_rather_than_outside_the_data(self):
        easy_high = self.MEASURED_RANGES[2][1]
        hard_low = self.MEASURED_RANGES[4][0]
        low, high = DESCRIPTOR.difficulty_thresholds.cutoffs
        assert self.MEASURED_RANGES[3][0] < low < easy_high
        assert hard_low < high < self.MEASURED_RANGES[3][1]

    def test_a_board_at_the_cut_point_bands_easier(self):
        """Ties have to break somewhere, and breaking easier is the kinder
        failure."""
        low = DESCRIPTOR.difficulty_thresholds.cutoffs[0]
        assert DESCRIPTOR.band_for(low) is DifficultyBand.EASY
