"""The measure that decides whether game 2 has content before it has code.

Game 1's coverage tool exists because the handoff's proxy count ("categories
with enough members") was double the real number, and nobody could see it
until both were printed side by side. This measure is exposed to the same
failure in a different place: the arithmetic of names against board sizes is
easy to get subtly wrong, and a tool that reports a number nobody can check
is worse than no tool. So the rules it applies are pinned here one at a time.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from puzzlegen.content.query import EntityView
from puzzlegen.surface import form_for

ROOT = Path(__file__).resolve().parents[2]


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


letters = load_tool("measure_letters_coverage")


def form(name: str, entity_id: str | None = None):
    return form_for(EntityView(entity_id=entity_id or f"entity:{name}", name=name))


def candidate(name: str, *members: str):
    return letters.Candidate(
        category_id=f"category:{name}",
        form=form(name),
        members=tuple(form(member) for member in members),
    )


class TestPlacingTheMarks:
    """Every mark is a real letter of the tile that carries it, one per tile."""

    FISH = ("salmon", "trout", "herring", "bass", "halibut", "carp")

    def test_the_worked_example_places(self):
        """``fish`` from the requirements: h, s, i on tiles and f blank."""
        placed = letters.place_marks("fish", 1, tuple(form(m) for m in self.FISH))
        assert placed is not None
        assert len(placed) == 3

    def test_a_letter_no_member_has_must_be_a_blank(self):
        """Which letters go unsupplied is the generator's choice.

        So the measure may not fix the blanks up front: it places as many as
        it can and asks whether the leftovers fit in the blank budget.
        """
        assert letters.place_marks("fish", 0, tuple(form(m) for m in self.FISH)) is None
        assert letters.place_marks("fish", 1, tuple(form(m) for m in self.FISH))

    def test_a_repeated_letter_needs_a_tile_each(self):
        """Keying the matching by letter would let the second ``s`` reuse the
        first one's tile, which no board can do."""
        assert letters.place_marks("ss", 0, (form("bass"),)) is None
        assert letters.place_marks("ss", 0, (form("bass"), form("sole"))) is not None

    def test_a_letter_is_not_taken_from_a_tile_another_letter_needs(self):
        """The reason the matching augments instead of taking the first fit.

        Only ``axe`` holds an x, and only ``axe`` and ``oak`` hold an a. A
        greedy pass that gives a to ``axe`` refuses a board that exists.
        """
        placed = letters.place_marks("ax", 0, (form("axe"), form("oak")))
        assert placed == ("entity:axe", "entity:oak")


class TestTheRefusalReasons:
    """Each refusal is a different kind of problem, so each is named.

    A content gap is fixable by curation; a name too long for the board is
    not fixable at all, and reporting them as one number would hide which.
    """

    def test_a_name_longer_than_the_board_can_mark(self):
        """A board needs one more tile than it has marks, because a settled
        rule says at least one tile carries none."""
        finding = letters.assess(candidate("hippopotamus", "a", "b", "c"), 6, 2)
        assert finding.reason == letters.NAME_TOO_LONG

    def test_a_name_that_exactly_fills_the_board_is_still_too_long(self):
        """Six marks on six tiles leaves no unmarked tile."""
        members = tuple(f"member{i}" for i in range(8))
        assert letters.assess(candidate("abcdefgh", *members), 6, 2).reason == (
            letters.NAME_TOO_LONG
        )

    def test_a_category_with_fewer_members_than_tiles(self):
        finding = letters.assess(candidate("fish", "salmon", "trout"), 6, 2)
        assert finding.reason == letters.TOO_FEW_MEMBERS

    def test_a_name_the_blanks_would_spell_entirely(self):
        """Two blanks on a three-letter name leaves one mark; on a two-letter
        name it leaves none, and a board with no marks is not a puzzle."""
        assert letters.assess(candidate("ox", "a", "b", "c", "d", "e", "f"), 6, 2).reason == (
            letters.NAME_UNUSABLE
        )

    def test_letters_the_members_cannot_supply(self):
        """The one refusal the letter matching itself produces."""
        finding = letters.assess(
            candidate("quiz", "aaa", "bbb", "ccc", "ddd", "eee", "fff"), 6, 2
        )
        assert finding.reason == letters.LETTERS_UNPLACEABLE

    def test_a_playable_category_names_the_tiles_it_used(self):
        """A measurement claiming a board exists should show the board."""
        finding = letters.assess(
            candidate("fish", "salmon", "trout", "herring", "bass", "halibut", "carp"),
            6,
            2,
        )
        assert finding.playable is True
        assert finding.reason == ""
        # More tiles than marks needed: the data can place three of fish's
        # four letters while the blank count asks for only two marks, and the
        # generator manufactures the extra blank from a tile it leaves alone.
        assert finding.marks_needed == 2
        assert len(finding.tiles) == 3


class TestTheSummary:
    def test_the_shortlist_is_shortest_name_first(self):
        """The probe's finding was that only obscure short names qualify, so
        the shortlist is read to judge the names, not to count them."""
        # Members chosen to hold the letters of both names, so the only
        # thing separating the two rows is the length of the name.
        members = ("bilberry", "coho", "cohort", "libel", "orchid", "bay", "rye", "herb")
        long_name = candidate("bilberry", *members)
        short = candidate("coho", *members)
        findings = letters.survey([long_name, short], (8,), (2,))
        summary = letters.summarise(findings, letters.SurfaceIndex())
        cell = summary["cells"][0]
        assert [row["name"] for row in cell["shortlist"]][0] == "coho"

    def test_the_summary_carries_the_policy_fingerprint(self):
        """A letter count means nothing without the rules that produced it."""
        summary = letters.summarise({}, letters.SurfaceIndex())
        assert summary["surface_policy"]

    def test_the_blank_floor_is_refused_at_the_command_line(self):
        """Measuring below the floor would report boards the game will not
        generate."""
        assert letters.main(["--blanks", "1", "--db", str(ROOT / "README.md")]) == 2
