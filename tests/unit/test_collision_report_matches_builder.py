"""The collision report must predict what the builder actually does.

The report once said a later sense of a same-named category loses its parents,
and listed every one of them as lost. The builder unions the parents instead
and refuses only an edge that would make the category its own ancestor. Fourteen
overlay words were reported as losing a parent and none did, so a handoff gate
that read "zero overlay words lost a parent" could not be satisfied or trusted.

Each test here builds the same files both ways and compares, so a change to
either side that makes them disagree fails here rather than in a report nobody
cross-checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import NOW

from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core import ids
from puzzlegen.providers.wordnet import WordNetLexiconProvider

from .test_snapshot_tools import collisions, opened


def write(path: Path, synsets: list[tuple[str, str, list[str]]]) -> Path:
    """A lexicon from (id, name, hypernym ids), one sense per synset."""
    document = {
        "lexicon_schema": 1,
        "lexicon": "test",
        "version": "1",
        "exported": "2026-10-03",
        "synsets": [
            {"id": sid, "name": name, "definition": f"gloss of {sid}", "hypernyms": hyp}
            for sid, name, hyp in synsets
        ],
        "senses": [
            {"id": f"sense.{sid}", "lemma": name, "synset": sid, "definition": f"gloss of {sid}"}
            for sid, name, _ in synsets
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def built_parents(paths: list[Path], tmp_path: Path) -> dict[str, set[str]]:
    """Category name to the names of its parents, as the builder wrote them."""
    with opened(tmp_path / "graph.sqlite3") as repos:
        builder = SnapshotBuilder(repos, now=NOW)
        for path in paths:
            builder.import_provider(
                WordNetLexiconProvider(path, taxonomy="wordnet", now=NOW),
                taxonomy="wordnet",
                entity_identity="lemma",
            )
        result: dict[str, set[str]] = {}
        for row in collisions.category_collisions(paths):
            category = repos.categories.get(ids.for_category(row["name"], "en", "wordnet"))
            result[row["name"]] = {
                repos.categories.get(parent).canonical_name for parent in category.parent_ids
            }
        return result


def reported(paths: list[Path]) -> dict[str, dict]:
    return {row["name"]: row for row in collisions.category_collisions(paths)}


@pytest.fixture
def two_senses(tmp_path):
    """viola the plant under herb, viola the instrument under another family."""
    first = write(
        tmp_path / "a.lexicon.json",
        [("s.thing", "thing", []), ("s.herb", "herb", ["s.thing"]), ("s.viola1", "viola", ["s.herb"])],
    )
    second = write(
        tmp_path / "b.lexicon.json",
        [
            ("s.thing", "thing", []),
            ("s.bowed", "bowed instrument", ["s.thing"]),
            ("s.viola2", "viola", ["s.bowed"]),
        ],
    )
    return [first, second]


@pytest.fixture
def cycle(tmp_path):
    """A later sense whose parent already sits below the category.

    cup has a child teacup in the first file. The second file names another cup
    whose parent is that teacup, so keeping the edge would make cup its own
    ancestor and the builder has to refuse it.
    """
    first = write(
        tmp_path / "a.lexicon.json",
        [
            ("s.thing", "thing", []),
            ("s.cup1", "cup", ["s.thing"]),
            ("s.teacup", "teacup", ["s.cup1"]),
        ],
    )
    second = write(
        tmp_path / "b.lexicon.json",
        [
            # teacup is repeated so the later cup can name it as a parent, and
            # carries no parent of its own here: giving it cup2 would make the
            # fixture a literal loop in the lexicon rather than a name collision.
            ("s.teacup", "teacup", []),
            ("s.cup2", "cup", ["s.teacup"]),
        ],
    )
    return [first, second]


class TestTheReportMatchesTheBuilder:
    def test_a_later_sense_keeps_its_parent(self, two_senses, tmp_path):
        row = reported(two_senses)["viola"]
        assert row["parents_lost"] == []
        assert set(row["parents_kept"]) == {"herb", "bowed instrument"}

    def test_the_builder_agrees_a_later_sense_keeps_its_parent(self, two_senses, tmp_path):
        assert built_parents(two_senses, tmp_path)["viola"] == {"herb", "bowed instrument"}

    def test_a_parent_that_would_close_a_loop_is_reported_as_refused(self, cycle):
        row = reported(cycle)["cup"]
        assert row["parents_lost"] == ["teacup"]
        assert "thing" in row["parents_kept"]

    def test_the_builder_agrees_the_loop_edge_is_refused(self, cycle, tmp_path):
        assert "teacup" not in built_parents(cycle, tmp_path)["cup"]

    @pytest.mark.parametrize("fixture_name", ["two_senses", "cycle"])
    def test_report_and_builder_never_disagree(self, fixture_name, request, tmp_path):
        """The property itself, over every merged category in the fixture.

        What the report calls kept is exactly what the builder wrote, and
        nothing the report calls lost appears in the builder's parents.
        """
        paths = request.getfixturevalue(fixture_name)
        actual = built_parents(paths, tmp_path)
        for name, row in reported(paths).items():
            assert set(row["parents_kept"]) == actual[name], name
            assert not set(row["parents_lost"]) & actual[name], name

    def test_file_order_does_not_hide_a_refusal(self, cycle, tmp_path):
        """Reversing the files changes what is refused, and the report follows.

        With the second file first, teacup is not yet below cup, so the
        builder keeps it from that side. The report has to follow the order it
        is given rather than assume one, since the build order is the input.
        """
        paths = list(reversed(cycle))
        actual = built_parents(paths, tmp_path)
        for name, row in reported(paths).items():
            assert set(row["parents_kept"]) == actual[name], name
            assert not set(row["parents_lost"]) & actual[name], name
