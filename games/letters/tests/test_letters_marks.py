"""The character work the whole lens rests on.

Every rule the requirements settle about marks is a line in ``marks.py``, and
each is pinned here one at a time: a mark is a real letter of its own tile, a
tile carries at most one, a repeated letter needs a tile each, and the blank
count is hit exactly, by taking marks off tiles when the data forces fewer
blanks than difficulty asked for.

The exactness of the matching gets its own test, because the failure it
prevents is silent: a greedy pass refuses boards that exist, and a refusal
looks exactly like content that is not there.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.query import EntityView
from puzzlegen.core.rng import DeterministicRng, derive_seed
from puzzlegen.surface import form_for
from puzzlegen_letters.marks import (
    SURFACE_POLICY,
    letters_of,
    marks_are_honest,
    place_marks,
    plan_marks,
    spells,
)

from boards import FISH


def form(name: str):
    return form_for(EntityView(entity_id=f"entity:{name}", name=name), SURFACE_POLICY)


def forms(*names: str):
    return tuple(form(name) for name in names)


def rng(label: str = "test") -> DeterministicRng:
    return DeterministicRng(derive_seed("2026-10-01", "missing_letters")).derive(label)


class TestPlacingTheMarks:
    def test_every_mark_is_a_letter_of_its_own_tile(self):
        """Rule 4.2, which is what makes a mark a clue rather than a label."""
        marks, _ = place_marks("fish", forms(*FISH))
        for entity_id, letter in marks.items():
            name = entity_id.removeprefix("entity:")
            assert letter in name

    def test_one_tile_carries_at_most_one_mark(self):
        marks, _ = place_marks("emotion", forms("anger", "fear", "pride", "joy"))
        assert len(set(marks)) == len(marks)

    def test_a_repeated_letter_needs_a_tile_each(self):
        """Keying the matching by letter would let the second ``s`` reuse the
        first one's tile, which no board can do."""
        _, unplaced = place_marks("ss", forms("bass"))
        assert unplaced == ("s",)
        _, unplaced = place_marks("ss", forms("bass", "sole"))
        assert unplaced == ()

    def test_a_letter_is_not_taken_from_a_tile_another_letter_needs(self):
        """Why the matching augments instead of taking the first fit.

        Only ``axe`` holds an x, and only ``axe`` and ``oak`` hold an a. A
        greedy pass that gives the a to ``axe`` refuses a board that exists.
        """
        marks, unplaced = place_marks("ax", forms("axe", "oak"))
        assert unplaced == ()
        assert marks == {"entity:axe": "x", "entity:oak": "a"}

    def test_a_letter_no_member_holds_is_reported_rather_than_forced(self):
        marks, unplaced = place_marks("fish", forms(*FISH))
        assert unplaced == ("f",)
        assert "f" not in marks.values()

    def test_the_same_members_place_the_same_marks_every_time(self):
        """A board that regenerates differently is not reproducible, and a
        maximum matching is not unique: only its size is."""
        first, _ = place_marks("emotion", forms("anger", "fear", "pride", "joy", "envy", "grief"))
        second, _ = place_marks("emotion", forms("grief", "envy", "joy", "pride", "fear", "anger"))
        assert first == second


class TestHittingTheBlankCount:
    def test_the_data_can_force_every_blank(self):
        plan = plan_marks("fish", 2, forms(*FISH), rng())
        assert plan is not None
        assert plan.blanks == 2
        assert "f" in plan.forced_blanks

    def test_a_blank_difficulty_wants_is_manufactured_from_a_marked_tile(self):
        """Rule 4.4: the generator leaves a tile deliberately unmarked.

        The board cannot show the difference, so the record of it is the only
        place it survives.
        """
        plan = plan_marks("fish", 3, forms(*FISH), rng())
        assert plan is not None
        assert plan.blanks == 3
        assert len(plan.forced_blanks) == 1
        # Three blanks, one of which the content forced, so two tiles were
        # left alone deliberately.
        assert len(plan.manufactured_blanks) == 2
        assert plan.marked == 1

    def test_a_category_whose_letters_it_cannot_carry_is_refused(self):
        assert plan_marks("quiz", 2, forms("aaa", "bbb", "ccc"), rng()) is None

    def test_a_name_shorter_than_the_blank_count_is_refused(self):
        """Every mark removed and still not enough blanks: a board with no
        marks is not a puzzle."""
        assert plan_marks("ox", 3, forms("box", "fox"), rng()) is None

    def test_which_mark_is_dropped_comes_from_the_day_rather_than_the_order(self):
        """Taking the first would make the same category withhold the same
        letter on every board it ever appeared on."""
        dropped = {
            tuple(
                plan_marks("fabric", 4, forms("denim", "cotton", "velvet", "canvas", "tweed", "satin"), rng(label)).manufactured_blanks
            )
            for label in ("one", "two", "three", "four", "five")
        }
        assert len(dropped) > 1

    def test_the_same_stream_drops_the_same_marks(self):
        made = [
            plan_marks("fabric", 4, forms("denim", "cotton", "velvet", "canvas", "tweed", "satin"), rng()).marks
            for _ in range(3)
        ]
        assert made[0] == made[1] == made[2]

    def test_a_negative_blank_count_is_a_programming_error(self):
        with pytest.raises(ValueError):
            plan_marks("fish", -1, forms(*FISH), rng())


class TestSpelling:
    def test_the_marks_plus_the_blanks_spell_the_answer(self):
        assert spells("fish", {"a": 1, "b": 1}, 2) is False
        assert spells("fish", {"i": 1, "s": 1}, 2) is True

    def test_a_word_of_another_length_is_not_an_answer_to_this_board(self):
        """The board shows how many slots there are."""
        assert spells("fishes", {"i": 1, "s": 1}, 2) is False

    def test_a_word_missing_one_of_the_marks_is_not_an_answer(self):
        """A mark with nothing to be is a tile the answer cannot use."""
        assert spells("fish", {"z": 1, "s": 1}, 2) is False

    def test_case_and_spaces_are_the_surface_policy_and_not_this_module(self):
        assert letters_of("Food Fish") == "foodfish"
        assert spells("Fish", {"i": 1, "s": 1}, 2) is True


class TestTheHonestyCheck:
    def test_a_mark_in_its_own_tile_passes(self):
        held = {form.entity_id: form for form in forms(*FISH)}
        marks, _ = place_marks("fish", forms(*FISH))
        assert marks_are_honest(marks, held) == ()

    def test_a_mark_the_tile_does_not_hold_is_named(self):
        held = {form.entity_id: form for form in forms(*FISH)}
        assert marks_are_honest({"entity:carp": "z"}, held) == ("entity:carp",)

    def test_a_mark_on_a_tile_the_board_does_not_have_is_named(self):
        assert marks_are_honest({"entity:nothing": "n"}, {}) == ("entity:nothing",)
