"""The invariants game 2's curated categories are authored to.

The measure on build A2 found the lexicon has the letters and not the
members: ``letters_unplaceable`` was zero in every cell, and at medium
frequency exactly one WordNet category had six or more eligible members. So
game 2 carries its own taxonomy, and its categories are authored against a
board rather than inherited from a lexicon.

That makes the file a content artifact with rules, and a rule nobody checks
is a rule a later batch breaks quietly. These pin the three that decide
whether a category can be a board at all: how many members it has, how long
its name is, and whether its own letters can be placed on its own members.
Frequency is not pinned here, because frequency comes from a table built
beside the snapshot and not from this file; the coverage tool measures it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from puzzlegen.content.query import EntityView
from puzzlegen.surface import form_for

ROOT = Path(__file__).resolve().parents[2]


def load_tool(name: str):
    """The coverage tool, by path.

    ``tools/`` is a directory of scripts rather than a package, so the one
    place that knows how to import it is here and in the coverage test; the
    placement rule lives in the tool and is not restated.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


coverage = load_tool("measure_letters_coverage")
SEED = ROOT / "games" / "letters" / "content" / "letters.curated.json"

#: The batch's floor and ceiling on members. The floor is the smallest board
#: the game generates; the ceiling is where a category stops being a set a
#: player holds in their head and starts being a lexicon subtree again.
MIN_MEMBERS = 6
MAX_MEMBERS = 8

#: The longest name that fits the smallest board. A board needs one more tile
#: than it has marks, and marks are the name's letters less the blanks, so
#: six tiles with the floor of two blanks admits seven letters.
MAX_LETTERS_ON_SIX_TILES = 7


def document() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


def members_by_category(doc: dict) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {row["key"]: [] for row in doc["categories"]}
    for entity in doc["entities"]:
        for key in entity["categories"]:
            members[key].append(entity["name"])
    return members


def letters_of(name: str) -> str:
    return form_for(EntityView(entity_id=f"entity:{name}", name=name)).letters


class TestTheBatchIsSizedForABoard:
    def test_every_category_has_six_to_eight_members(self):
        """The route this content took: members first, naming afterwards.

        A category below the floor cannot fill the smallest board, and the
        refusal the coverage tool reports for it is the one that blocked
        every lexicon category.
        """
        too_small = {
            key: len(names)
            for key, names in members_by_category(document()).items()
            if not MIN_MEMBERS <= len(names) <= MAX_MEMBERS
        }
        assert too_small == {}

    def test_no_member_is_claimed_twice_by_one_category(self):
        """A board's tiles are distinct, so a duplicate member is a board one
        tile smaller than the file says it is."""
        doc = document()
        for entity in doc["entities"]:
            assert len(entity["categories"]) == len(set(entity["categories"]))

    def test_every_membership_names_a_category_the_file_declares(self):
        declared = {row["key"] for row in document()["categories"]}
        named = {
            key for entity in document()["entities"] for key in entity["categories"]
        }
        assert named <= declared

    def test_every_category_is_named_once(self):
        doc = document()
        keys = [row["key"] for row in doc["categories"]]
        names = [row["name"] for row in doc["categories"]]
        assert len(keys) == len(set(keys))
        assert len(names) == len(set(names))


class TestTheNamesFitTheBoardsTheyAreFor:
    def test_no_name_is_longer_than_the_smallest_board_can_mark(self):
        """``name_too_long`` is the one refusal curation cannot fix later: a
        category whose name overruns every board size is content that can
        never be played, however good its members are."""
        overlong = {
            row["name"]: len(letters_of(row["name"]))
            for row in document()["categories"]
            if len(letters_of(row["name"])) > MAX_LETTERS_ON_SIX_TILES
        }
        # Eight and nine letter names are legitimate at three and four
        # blanks, so this is asserted against the batch's own intent rather
        # than against the tool's widest cell. Add a longer name only
        # together with the board size that can hold it.
        assert overlong == {}

    def test_no_name_is_so_short_the_blanks_would_spell_it(self):
        """Two blanks is the floor, and a mark count below one is not a
        puzzle, so a name needs at least three letters."""
        for row in document()["categories"]:
            assert len(letters_of(row["name"])) >= 3

    def test_every_category_can_spell_its_own_name_on_its_own_members(self):
        """The check the whole lens rests on.

        A category whose members cannot carry its letters is unplayable for a
        reason neither the member count nor the name length reveals, and this
        is the only place in the file where the two interact.
        """
        members = members_by_category(document())
        unplaceable = []
        for row in document()["categories"]:
            name = row["name"]
            forms = tuple(
                form_for(EntityView(entity_id=f"entity:{m}", name=m))
                for m in members[row["key"]]
            )
            # Two blanks: the floor, and so the hardest case in the file.
            if coverage.place_marks(letters_of(name), 2, forms) is None:
                unplaceable.append(name)
        assert unplaceable == []


class TestTheFileDeclaresWhatItIs:
    def test_the_schema_and_the_taxonomy_are_stated_in_the_file(self):
        """The taxonomy is passed on the command line, so the file is the only
        place a reader can learn which one it is meant for."""
        doc = document()
        assert doc["curated_schema"] == 1
        assert any("taxonomy='letters'" in warning for warning in doc["warnings"])

    def test_every_category_carries_a_gloss(self):
        """A curator reopening this file needs to know what the category was
        taken to mean, because the members are chosen for frequency and do
        not define it."""
        for row in document()["categories"]:
            assert row.get("gloss")
