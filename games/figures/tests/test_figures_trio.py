"""The attribute algebra, which everything else in the game rests on.

Two properties carry the whole design and each is tested against its
definition rather than against a table somebody typed. That two figures
determine exactly one third is what makes the generator a construction. That
a board's trios are exactly its pairs whose completion is present is what
makes "does this figure create a second trio" a set lookup.
"""

from __future__ import annotations

from itertools import combinations, product

import pytest

from puzzlegen_figures.trio import (
    ATTRIBUTES,
    TRIO,
    VALUES_PER_ATTRIBUTE,
    SpaceError,
    attribute_values,
    check_space,
    completion,
    describe,
    failing_attributes,
    forbidden_completions,
    is_trio,
    key_of,
    near_trios,
    trios_in,
)

from boards import SPACE, figure, whole_space

ALL = [dict(zip(ATTRIBUTES, values)) for values in product(*(SPACE[a] for a in ATTRIBUTES))]


class TestTheRule:
    def test_all_different_on_one_attribute_and_same_on_the_rest_is_a_trio(self):
        assert is_trio(
            [
                figure("triangle", "solid", "one", "red"),
                figure("circle", "solid", "one", "red"),
                figure("square", "solid", "one", "red"),
            ]
        )

    def test_all_different_on_every_attribute_is_a_trio(self):
        assert is_trio(
            [
                figure("triangle", "solid", "one", "red"),
                figure("circle", "hollow", "two", "green"),
                figure("square", "striped", "three", "blue"),
            ]
        )

    def test_two_the_same_and_one_different_is_not(self):
        assert not is_trio(
            [
                figure("triangle", "solid", "one", "red"),
                figure("triangle", "solid", "one", "green"),
                figure("triangle", "solid", "one", "red"),
            ]
        )

    def test_the_failing_attribute_is_named(self):
        failing = failing_attributes(
            [
                figure("triangle", "solid", "one", "red"),
                figure("circle", "solid", "one", "red"),
                figure("square", "solid", "one", "green"),
            ]
        )
        assert failing == ("colour",)

    @pytest.mark.parametrize("count", [0, 1, 2, 4])
    def test_only_three_figures_can_be_a_trio(self, count):
        """A caller holding a pair or a quartet has a bug, and a function that
        silently judged them would hide it."""
        assert not is_trio(ALL[:count])


class TestTheCompletion:
    def test_two_figures_determine_a_third_that_completes_the_trio(self):
        """The property the whole generator rests on, over every pair.

        Three thousand two hundred and forty pairs in the default space, each
        checked by computing the completion and then asking the rule
        independently whether the three are a trio. If the two ever disagreed,
        the generator would be building boards the verifier rejects.
        """
        for first, second in combinations(ALL, 2):
            third = completion(first, second, SPACE)
            assert is_trio([first, second, third])

    def test_the_completion_is_the_only_figure_that_works(self):
        """Unique, not merely existent. Checked by brute force over the space.

        Sampled rather than exhaustive: the exhaustive version is eighty-one
        figures times three thousand pairs, which is a quarter of a million
        rule evaluations for a property the first fifty pairs already
        establish.
        """
        for first, second in list(combinations(ALL, 2))[:50]:
            third = completion(first, second, SPACE)
            working = [c for c in ALL if is_trio([first, second, c])]
            assert [key_of(c) for c in working] == [key_of(third)]

    def test_a_pair_agreeing_on_an_attribute_keeps_that_value(self):
        first = figure("triangle", "solid", "one", "red")
        second = figure("circle", "solid", "two", "red")
        third = completion(first, second, SPACE)
        assert third["fill"] == "solid" and third["colour"] == "red"

    def test_a_pair_differing_on_an_attribute_takes_the_remaining_value(self):
        first = figure("triangle", "solid", "one", "red")
        second = figure("circle", "solid", "one", "red")
        assert completion(first, second, SPACE)["shape"] == "square"

    def test_a_figure_paired_with_itself_completes_to_itself(self):
        """Not a board a generator builds, and worth knowing the answer to:
        the rule says all-same is a trio, so three copies would be one."""
        first = ALL[0]
        assert key_of(completion(first, first, SPACE)) == key_of(first)

    def test_an_attribute_with_the_wrong_number_of_values_is_refused(self):
        space = {**SPACE, "colour": ("red", "green")}
        with pytest.raises(SpaceError, match="do not determine a third"):
            completion(ALL[0], ALL[1], space)


class TestTheSpaceCheck:
    def test_the_default_space_passes(self):
        assert check_space(ALL) == {
            attribute: tuple(sorted(values)) for attribute, values in SPACE.items()
        }

    def test_a_space_with_four_values_on_an_attribute_is_refused(self):
        """A build whose figure taxonomy grew a fourth colour would import
        cleanly and produce trios whose third figure cannot be determined.

        The failure would look like unfair puzzles rather than like a content
        change, which is why it is refused here with the count named.
        """
        extra = [*ALL, figure("triangle", "solid", "one", "yellow")]
        with pytest.raises(SpaceError, match="colour: 4"):
            check_space(extra)

    def test_a_space_missing_a_value_is_refused(self):
        only_red = [reading for reading in ALL if reading["colour"] == "red"]
        with pytest.raises(SpaceError, match="colour: 1"):
            check_space(only_red)

    def test_a_figure_missing_an_attribute_is_refused_by_name(self):
        broken = [{k: v for k, v in ALL[0].items() if k != "fill"}]
        with pytest.raises(SpaceError, match="'fill'"):
            attribute_values(broken)

    def test_the_rule_needs_three_values_and_the_constant_says_so(self):
        assert VALUES_PER_ATTRIBUTE == TRIO == 3
        assert all(len(values) == VALUES_PER_ATTRIBUTE for values in SPACE.values())


class TestTheBoardLevelQuestions:
    def test_forbidden_completions_are_what_would_make_a_second_trio(self):
        """The generator's gate, checked against the enumeration it replaces."""
        board = ALL[:6]
        forbidden = forbidden_completions(board, SPACE)
        for candidate in ALL:
            if key_of(candidate) in {key_of(reading) for reading in board}:
                continue
            widened = [*board, candidate]
            made_one = len(trios_in(widened)) > len(trios_in(board))
            assert made_one is (key_of(candidate) in forbidden)

    def test_a_board_built_from_non_completions_holds_no_trio(self):
        board: list[dict] = []
        forbidden: set = set()
        for candidate in ALL:
            if key_of(candidate) in forbidden:
                continue
            forbidden |= {
                key_of(completion(candidate, other, SPACE)) for other in board
            }
            board.append(candidate)
            if len(board) == 9:
                break
        assert trios_in(board) == ()

    def test_trios_in_finds_the_trio_wherever_it_sits(self):
        board = [*ALL[20:23], *ALL[:3]]
        found = trios_in(board)
        assert (3, 4, 5) in found

    def test_near_trios_fail_on_exactly_one_attribute(self):
        board = whole_space()[:9]
        readings = [
            {a: view.properties[a] for a in ATTRIBUTES} for view in board
        ]
        for indices in near_trios(readings):
            chosen = [readings[index] for index in indices]
            assert len(failing_attributes(chosen)) == 1
            assert not is_trio(chosen)

    def test_describe_names_every_attribute(self):
        text = describe(figure("triangle", "solid", "one", "red"))
        assert text == "triangle solid one red"
