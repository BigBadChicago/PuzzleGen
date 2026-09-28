"""The collision report and the snapshot build driver.

Both are loaded by path, as the other tools are. The mini lexicon shipped in
``content/seeds/`` is the fixture: it is small, it is committed, and it already
contains the forked-ancestry and dangling-hypernym cases the providers were
written against.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import NOW

from puzzlegen.core.types import ReviewStatus
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.graph import GraphRepositories, SqliteDocumentStore

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
SEEDS = ROOT / "content" / "seeds"
MINI_LEXICON = SEEDS / "wordnet-mini.lexicon.json"
OVERLAY = SEEDS / "overlay.curated.json"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"tool_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def opened(path):
    """A store over ``path``, closed on exit.

    Python 3.13 and later warn about an sqlite connection collected unclosed,
    and the repository turns warnings into errors, so a leaked connection fails
    whichever unrelated test the collector happens to interrupt.
    """
    repos = GraphRepositories(SqliteDocumentStore(path))
    try:
        yield repos
    finally:
        repos.close()


collisions = load_tool("report_lemma_collisions")
build_snapshot = load_tool("build_snapshot")


def write_lexicon(path: Path, senses: list[dict], synsets: list[dict] | None = None):
    document = {
        "lexicon_schema": 1,
        "lexicon": "test",
        "version": "1",
        "exported": "2026-09-28",
        "synsets": synsets
        or [
            {"id": s["synset"], "name": s["lemma"], "definition": f"gloss of {s['lemma']}", "hypernyms": []}
            for s in senses
        ],
        "senses": senses,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def sense(lemma: str, synset: str, definition: str | None = None) -> dict:
    row = {"id": f"sense.{synset}.{lemma}", "lemma": lemma, "synset": synset}
    if definition:
        row["definition"] = definition
    return row


class TestCollecting:
    def test_one_lemma_in_two_synsets_is_one_use_with_two_senses(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("crane", "s.bird"), sense("crane", "s.machine")],
        )
        uses = collisions.collect([path])
        assert uses["crane"].senses == 2

    def test_lemmas_are_lowercased_so_case_does_not_split_a_merge(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("Crane", "s.bird"), sense("crane", "s.machine")],
        )
        assert collisions.collect([path])["crane"].senses == 2

    def test_the_source_file_is_recorded_per_lemma(self, tmp_path):
        one = write_lexicon(tmp_path / "one.lexicon.json", [sense("crane", "s.bird")])
        two = write_lexicon(tmp_path / "two.lexicon.json", [sense("crane", "s.machine")])
        assert collisions.collect([one, two])["crane"].files == {
            "one.lexicon.json",
            "two.lexicon.json",
        }

    def test_a_sense_definition_beats_the_synset_gloss(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("crane", "s.bird", "a long-necked wading bird")],
        )
        uses = collisions.collect([path])
        assert uses["crane"].definitions["s.bird"] == "a long-necked wading bird"

    def test_a_file_without_senses_is_refused(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text(json.dumps({"lexicon_schema": 1}), encoding="utf-8")
        with pytest.raises(ValueError, match="not a lexicon export"):
            collisions.collect([path])

    def test_the_committed_mini_lexicon_reads(self):
        uses = collisions.collect([MINI_LEXICON])
        assert uses


class TestOverlayMembers:
    def test_members_are_read_with_their_category_names(self):
        members = collisions.overlay_members(OVERLAY)
        assert members["turtle"] == ["thing with a shell"]

    def test_a_member_in_two_categories_lists_both(self):
        members = collisions.overlay_members(OVERLAY)
        assert len(members["salmon"]) == 2

    def test_every_seed_member_is_present(self):
        assert len(collisions.overlay_members(OVERLAY)) == 144


class TestTheReport:
    def build(self, tmp_path, senses, overlay=None):
        path = write_lexicon(tmp_path / "a.lexicon.json", senses)
        uses = collisions.collect([path])
        members = overlay if overlay is not None else {}
        return collisions.build_report(uses, members)

    def test_a_polysemous_lemma_is_listed(self, tmp_path):
        report = self.build(
            tmp_path, [sense("crane", "s.bird"), sense("crane", "s.machine")]
        )
        assert report["counts"]["polysemous"] == 1
        assert report["polysemous"][0]["lemma"] == "crane"

    def test_an_unambiguous_lemma_is_not_listed(self, tmp_path):
        report = self.build(tmp_path, [sense("otter", "s.otter")])
        assert report["counts"]["polysemous"] == 0

    def test_polysemous_lemmas_come_worst_first(self, tmp_path):
        report = self.build(
            tmp_path,
            [
                sense("crane", "s.bird"),
                sense("crane", "s.machine"),
                sense("crane", "s.verb"),
                sense("bat", "s.animal"),
                sense("bat", "s.club"),
            ],
        )
        assert [r["lemma"] for r in report["polysemous"]] == ["crane", "bat"]

    def test_a_lemma_in_two_exports_is_listed_separately(self, tmp_path):
        one = write_lexicon(tmp_path / "one.lexicon.json", [sense("seal", "s.animal")])
        two = write_lexicon(tmp_path / "two.lexicon.json", [sense("seal", "s.stamp")])
        report = collisions.build_report(collisions.collect([one, two]), {})
        assert report["counts"]["cross_file"] == 1

    def test_an_ambiguous_overlay_member_is_flagged_with_its_categories(self, tmp_path):
        report = self.build(
            tmp_path,
            [sense("frost", "s.weather"), sense("frost", "s.poet")],
            overlay={"frost": ["cold thing", "sharp thing"]},
        )
        assert report["counts"]["overlay_ambiguous"] == 1
        row = report["overlay_ambiguous"][0]
        assert row["overlay_categories"] == ["cold thing", "sharp thing"]
        assert len(row["definitions"]) == 2

    def test_an_overlay_member_the_lexicon_lacks_is_flagged(self, tmp_path):
        report = self.build(
            tmp_path, [sense("otter", "s.otter")], overlay={"burlap": ["texture"]}
        )
        assert report["counts"]["overlay_missing"] == 1
        assert report["overlay_missing"][0]["lemma"] == "burlap"

    def test_an_unambiguous_overlay_member_is_flagged_as_neither(self, tmp_path):
        report = self.build(
            tmp_path, [sense("burlap", "s.cloth")], overlay={"burlap": ["texture"]}
        )
        assert report["counts"]["overlay_ambiguous"] == 0
        assert report["counts"]["overlay_missing"] == 0

    def test_the_histogram_accounts_for_every_lemma(self, tmp_path):
        report = self.build(
            tmp_path,
            [sense("crane", "s.bird"), sense("crane", "s.machine"), sense("otter", "s.otter")],
        )
        assert sum(report["sense_histogram"].values()) == report["counts"]["lemmas"]

    def test_a_limit_truncates_the_lists_but_not_the_counts(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [
                sense("crane", "s.bird"),
                sense("crane", "s.machine"),
                sense("bat", "s.animal"),
                sense("bat", "s.club"),
            ],
        )
        report = collisions.build_report(collisions.collect([path]), {}, limit=1)
        assert report["counts"]["polysemous"] == 2
        assert len(report["polysemous"]) == 1

    def test_the_report_renders_without_the_optional_sections(self, tmp_path):
        text = collisions.render(self.build(tmp_path, [sense("otter", "s.otter")]))
        assert "lemmas" in text

    def test_the_report_is_json_serialisable(self, tmp_path):
        report = self.build(
            tmp_path, [sense("crane", "s.bird"), sense("crane", "s.machine")]
        )
        assert json.loads(json.dumps(report))["counts"]["polysemous"] == 1


class TestCollisionsCommandLine:
    def test_a_run_prints_and_writes_a_report(self, tmp_path, capsys):
        out = tmp_path / "report.json"
        code = collisions.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--overlay",
                str(OVERLAY),
                "--out",
                str(out),
            ]
        )
        assert code == 0
        assert "overlay members" in capsys.readouterr().out
        assert json.loads(out.read_text())["counts"]["overlay_members"] == 144

    def test_a_missing_lexicon_exits_two(self, tmp_path, capsys):
        code = collisions.main(["--lexicon", str(tmp_path / "nope.json")])
        assert code == 2
        assert "not found" in capsys.readouterr().err

    def test_it_runs_without_an_overlay_file(self, tmp_path, capsys):
        code = collisions.main(
            ["--lexicon", str(MINI_LEXICON), "--overlay", str(tmp_path / "none.json")]
        )
        assert code == 0
        assert "overlay members       : 0" in capsys.readouterr().out


class TestInputLists:
    def test_terms_are_lemmas_and_texts_carry_the_gloss(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json", [sense("otter", "s.otter", "a river mammal")]
        )
        terms, texts = build_snapshot.input_lists([path], OVERLAY, NOW)
        assert "otter" in terms
        assert "otter: a river mammal" in texts

    def test_a_lemma_without_a_gloss_embeds_as_its_bare_name(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("otter", "s.otter")],
            synsets=[{"id": "s.otter", "name": "otter", "definition": "", "hypernyms": []}],
        )
        _, texts = build_snapshot.input_lists([path], OVERLAY, NOW)
        assert "otter" in texts
        assert not any(x.startswith("otter:") for x in texts)

    def test_overlay_members_join_the_term_list(self, tmp_path):
        path = write_lexicon(tmp_path / "a.lexicon.json", [sense("otter", "s.otter")])
        terms, _ = build_snapshot.input_lists([path], OVERLAY, NOW)
        assert "turtle" in terms and "otter" in terms

    def test_the_two_lists_stay_aligned(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("otter", "s.otter", "a river mammal"), sense("badger", "s.badger")],
        )
        terms, texts = build_snapshot.input_lists([path], OVERLAY, NOW)
        assert len(terms) == len(texts)
        assert all(text.startswith(term) for term, text in zip(terms, texts))

    def test_terms_are_sorted_so_two_machines_write_the_same_file(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("otter", "s.otter"), sense("badger", "s.badger")],
        )
        terms, _ = build_snapshot.input_lists([path], OVERLAY, NOW)
        assert terms == sorted(terms)

    def test_write_inputs_writes_both_files(self, tmp_path):
        path = write_lexicon(tmp_path / "a.lexicon.json", [sense("otter", "s.otter")])
        terms_path, texts_path = build_snapshot.write_inputs(
            tmp_path / "build", [path], OVERLAY, NOW
        )
        assert terms_path.read_text().splitlines()
        assert texts_path.read_text().splitlines()


class TestProviderOrder:
    def test_the_overlay_is_always_last(self, tmp_path):
        import datetime as dt

        chosen = build_snapshot.providers(
            [MINI_LEXICON], OVERLAY, dt.datetime.now(dt.timezone.utc)
        )
        assert chosen[-1][1]["taxonomy"] == build_snapshot.OVERLAY_TAXONOMY
        assert all(
            options["taxonomy"] == build_snapshot.LEXICAL_TAXONOMY
            for _, options in chosen[:-1]
        )

    def test_every_import_uses_lemma_identity(self, tmp_path):
        import datetime as dt

        chosen = build_snapshot.providers(
            [MINI_LEXICON], OVERLAY, dt.datetime.now(dt.timezone.utc)
        )
        assert all(o["entity_identity"] == "lemma" for _, o in chosen)

    def test_the_overlay_is_not_optional(self):
        """No flag removes it. A snapshot without a second axis is four piles."""
        parser_args = build_snapshot.main.__doc__ or ""
        assert "--no-overlay" not in parser_args
        assert build_snapshot.OVERLAY_SEED.name == "overlay.curated.json"


class TestBuildCommandLine:
    def test_write_inputs_mode_builds_nothing(self, tmp_path, capsys):
        code = build_snapshot.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--write-inputs",
                str(tmp_path / "build"),
            ]
        )
        assert code == 0
        assert (tmp_path / "build" / "terms.txt").exists()
        assert "wrote" in capsys.readouterr().out

    def test_a_missing_input_exits_two(self, tmp_path, capsys):
        code = build_snapshot.main(["--lexicon", str(tmp_path / "nope.json")])
        assert code == 2
        assert "not found" in capsys.readouterr().err

    def test_building_without_a_db_or_label_exits_two(self, capsys):
        code = build_snapshot.main(["--lexicon", str(MINI_LEXICON)])
        assert code == 2
        assert "required to build" in capsys.readouterr().err

    def test_a_full_build_seals_a_snapshot(self, tmp_path, capsys):
        db = tmp_path / "graph.sqlite"
        code = build_snapshot.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--db",
                str(db),
                "--label",
                "2026.09.1",
                "--dev-embeddings",
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "snapshot" in out and "2026.09.1" in out

        with opened(db) as repos:
            meta = repos.snapshots.by_label("2026.09.1")
            assert meta is not None and meta.sealed
            assert meta.content_hash

    def test_a_full_build_activates_the_overlay(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        build_snapshot.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--db",
                str(db),
                "--label",
                "2026.09.1",
                "--dev-embeddings",
            ]
        )
        with opened(db) as repos:
            overlay = repos.categories.live(build_snapshot.OVERLAY_TAXONOMY)
            assert len(overlay) == 15
            assert all(c.status is ReviewStatus.ACTIVE for c in overlay)

    def test_a_dev_embedding_build_warns_that_it_is_not_publishable(
        self, tmp_path, capsys
    ):
        build_snapshot.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--db",
                str(tmp_path / "graph.sqlite"),
                "--label",
                "dev",
                "--dev-embeddings",
            ]
        )
        assert "development embeddings" in capsys.readouterr().out

    def hash_of(self, tmp_path, name: str, *extra: str) -> str:
        db = tmp_path / f"{name}.sqlite"
        build_snapshot.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--db",
                str(db),
                "--label",
                "2026.09.1",
                "--dev-embeddings",
                *extra,
            ]
        )
        with opened(db) as repos:
            return repos.snapshots.by_label("2026.09.1").content_hash

    def test_two_builds_at_a_pinned_time_agree_on_the_content_hash(self, tmp_path):
        """The property the whole snapshot design exists for."""
        pinned = ["--now", "2026-09-28T12:00:00+00:00"]
        assert self.hash_of(tmp_path, "one", *pinned) == self.hash_of(
            tmp_path, "two", *pinned
        )

    def test_two_builds_at_the_wall_clock_do_not(self, tmp_path):
        """Not a bug, and worth pinning down.

        Every governed record carries a creation time and the hash covers it,
        so an unpinned build is reproducible in content but not in bytes. A
        release build passes --now; a local one does not care.
        """
        assert self.hash_of(tmp_path, "three") != self.hash_of(tmp_path, "four")

    def test_a_naive_now_is_refused(self, tmp_path, capsys):
        code = build_snapshot.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--db",
                str(tmp_path / "g.sqlite"),
                "--label",
                "x",
                "--now",
                "2026-09-28T12:00:00",
            ]
        )
        assert code == 2
        assert "timezone-aware" in capsys.readouterr().err


class TestTheInputListsMatchTheBuild:
    """The list the export commands consume must be the list the build asks for.

    ``attach_embeddings`` looks the table up by ``name: definition``, and the
    merge decides which sense's gloss an entity keeps. A prepare step that
    guessed the gloss from file order produced 31 texts the build never asked
    for and omitted 31 it did, and the build died at its last step with a
    KeyError naming five plant names.
    """

    def two_senses(self, tmp_path) -> Path:
        """One lemma, two synsets, two glosses: the case that broke."""
        return write_lexicon(
            tmp_path / "a.lexicon.json",
            [
                sense("carriage", "s.horse", "a vehicle drawn by horses"),
                sense("carriage", "s.pram", "a small vehicle for a baby"),
                sense("otter", "s.otter", "a river mammal"),
            ],
        )

    def requested_texts(self, repos) -> set[str]:
        from puzzlegen.content.snapshots import embedding_text

        return {embedding_text(e) for e in repos.entities.iter_all()}

    def test_every_text_the_build_asks_for_was_written(self, tmp_path, repos):
        path = self.two_senses(tmp_path)
        _, texts = build_snapshot.input_lists([path], OVERLAY, NOW)

        builder = SnapshotBuilder(repos, now=NOW)
        for provider, options in build_snapshot.providers([path], OVERLAY, NOW):
            builder.import_provider(provider, **options)

        assert self.requested_texts(repos) - set(texts) == set()

    def test_no_text_was_written_that_the_build_never_asks_for(self, tmp_path, repos):
        path = self.two_senses(tmp_path)
        _, texts = build_snapshot.input_lists([path], OVERLAY, NOW)

        builder = SnapshotBuilder(repos, now=NOW)
        for provider, options in build_snapshot.providers([path], OVERLAY, NOW):
            builder.import_provider(provider, **options)

        assert set(texts) - self.requested_texts(repos) == set()

    def test_a_merged_lemma_keeps_one_text_not_two(self, tmp_path):
        path = self.two_senses(tmp_path)
        terms, texts = build_snapshot.input_lists([path], OVERLAY, NOW)
        assert terms.count("carriage") == 1
        assert sum(1 for x in texts if x.startswith("carriage")) == 1

    def test_the_overlay_members_are_present_without_a_gloss(self, tmp_path):
        path = self.two_senses(tmp_path)
        _, texts = build_snapshot.input_lists([path], OVERLAY, NOW)
        assert "turtle" in texts

    def test_a_table_built_from_the_list_satisfies_the_build(self, tmp_path):
        """End to end: prepare, fake command 7, build. No KeyError."""
        path = self.two_senses(tmp_path)
        _, texts = build_snapshot.input_lists([path], OVERLAY, NOW)
        table = tmp_path / "embeddings.json"
        table.write_text(
            json.dumps(
                {
                    "model_name": "stand-in",
                    "model_version": "0",
                    "similarity_metric": "cosine",
                    "computed_at": "2026-09-28T12:00:00+00:00",
                    "vectors": {text: [0.1, 0.2, 0.3] for text in texts},
                }
            ),
            encoding="utf-8",
        )
        code = build_snapshot.main(
            [
                "--lexicon",
                str(path),
                "--db",
                str(tmp_path / "graph.sqlite"),
                "--label",
                "end-to-end",
                "--now",
                "2026-09-28T12:00:00+00:00",
                "--embeddings",
                str(table),
            ]
        )
        assert code == 0

    def test_the_prepare_step_leaves_no_store_open(self, tmp_path):
        """It builds a throwaway graph, which still has to be closed."""
        import sqlite3

        before = sqlite3.connect
        opened = []
        try:
            sqlite3.connect = lambda *a, **k: opened.append(1) or before(*a, **k)
            build_snapshot.input_lists(
                [self.two_senses(tmp_path)], OVERLAY, NOW
            )
        finally:
            sqlite3.connect = before
        assert opened == []


class TestMergedCategories:
    """Synsets that become one category, reported before a build rather than
    discovered during one.

    Two distinct `galley` synsets are both hypernyms of `monoreme` in Open
    English WordNet. The import merges them by name, which is the identity rule
    working, but the duplicate parent edge aborted a depth 6 build before the
    normalizer learned to deduplicate.
    """

    def galley(self, tmp_path) -> Path:
        return write_lexicon(
            tmp_path / "vehicle.lexicon.json",
            [
                sense("galley", "s.kitchen"),
                sense("galley", "s.ship"),
                sense("monoreme", "s.monoreme"),
            ],
            synsets=[
                {
                    "id": "s.kitchen",
                    "name": "galley",
                    "definition": "a ship's kitchen",
                    "hypernyms": [],
                },
                {
                    "id": "s.ship",
                    "name": "galley",
                    "definition": "a ship propelled by oars",
                    "hypernyms": [],
                },
                {
                    "id": "s.monoreme",
                    "name": "monoreme",
                    "definition": "a galley with one bank of oars",
                    "hypernyms": ["s.kitchen", "s.ship"],
                },
            ],
        )

    def test_two_synsets_sharing_a_name_are_reported(self, tmp_path):
        found = collisions.category_collisions([self.galley(tmp_path)])
        assert [row["name"] for row in found] == ["galley"]
        assert len(found[0]["synsets"]) == 2

    def test_both_definitions_are_shown_so_a_person_can_judge(self, tmp_path):
        found = collisions.category_collisions([self.galley(tmp_path)])
        glosses = [s["definition"] for s in found[0]["synsets"]]
        assert "a ship's kitchen" in glosses
        assert "a ship propelled by oars" in glosses

    def test_a_child_inheriting_the_merge_twice_is_named(self, tmp_path):
        found = collisions.category_collisions([self.galley(tmp_path)])
        assert found[0]["children_inheriting_it_twice"] == ["monoreme"]

    def test_a_child_inheriting_it_once_is_not_named(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("galley", "s.kitchen"), sense("galley", "s.ship"), sense("skiff", "s.skiff")],
            synsets=[
                {"id": "s.kitchen", "name": "galley", "definition": "k", "hypernyms": []},
                {"id": "s.ship", "name": "galley", "definition": "s", "hypernyms": []},
                {"id": "s.skiff", "name": "skiff", "definition": "a small boat", "hypernyms": ["s.ship"]},
            ],
        )
        found = collisions.category_collisions([path])
        assert found[0]["children_inheriting_it_twice"] == []

    def test_distinct_names_are_not_reported(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("otter", "s.otter"), sense("badger", "s.badger")],
        )
        assert collisions.category_collisions([path]) == []

    def test_a_name_shared_across_two_files_is_reported(self, tmp_path):
        one = write_lexicon(tmp_path / "one.lexicon.json", [sense("bugle", "s.horn")])
        two = write_lexicon(tmp_path / "two.lexicon.json", [sense("bugle", "s.plant")])
        found = collisions.category_collisions([one, two])
        assert [row["name"] for row in found] == ["bugle"]
        assert {s["file"] for s in found[0]["synsets"]} == {
            "one.lexicon.json",
            "two.lexicon.json",
        }

    def test_the_counts_reach_the_report(self, tmp_path):
        path = self.galley(tmp_path)
        report = collisions.build_report(
            collisions.collect([path]), {}, categories=collisions.category_collisions([path])
        )
        assert report["counts"]["merged_categories"] == 1
        assert report["counts"]["merged_categories_with_double_inheritance"] == 1

    def test_the_section_renders(self, tmp_path):
        path = self.galley(tmp_path)
        report = collisions.build_report(
            collisions.collect([path]), {}, categories=collisions.category_collisions([path])
        )
        text = collisions.render(report)
        assert "become one category" in text
        assert "inherited twice by: monoreme" in text

    def test_a_report_without_the_section_still_renders(self, tmp_path):
        path = write_lexicon(tmp_path / "a.lexicon.json", [sense("otter", "s.otter")])
        text = collisions.render(collisions.build_report(collisions.collect([path]), {}))
        assert "become one category" not in text

    def test_the_command_line_includes_the_section(self, tmp_path, capsys):
        collisions.main(
            ["--lexicon", str(self.galley(tmp_path)), "--overlay", str(tmp_path / "none.json")]
        )
        out = capsys.readouterr().out
        assert "merged categories" in out
        assert "monoreme" in out


class TestStaleInputs:
    """A frequency or embedding file older than a lexicon it was derived from.

    Re-export a lexicon and forget the other two commands, and the old build
    imported everything, ran for minutes, and died at its last step with a
    KeyError naming five plant names. Nothing in that message says which
    command to re-run.
    """

    def pair(self, tmp_path, *, derived_first: bool) -> tuple[Path, Path]:
        import time

        lexicon = tmp_path / "a.lexicon.json"
        derived = tmp_path / "frequency.json"
        first, second = (derived, lexicon) if derived_first else (lexicon, derived)
        first.write_text("{}", encoding="utf-8")
        time.sleep(0.01)
        second.write_text("{}", encoding="utf-8")
        return lexicon, derived

    def test_a_derived_file_older_than_its_source_is_stale(self, tmp_path):
        lexicon, derived = self.pair(tmp_path, derived_first=True)
        assert build_snapshot.stale_inputs([derived], [lexicon]) == [(derived, lexicon)]

    def test_a_derived_file_newer_than_its_source_is_not(self, tmp_path):
        lexicon, derived = self.pair(tmp_path, derived_first=False)
        assert build_snapshot.stale_inputs([derived], [lexicon]) == []

    def test_one_stale_source_among_several_is_enough(self, tmp_path):
        import time

        fresh = tmp_path / "fresh.lexicon.json"
        fresh.write_text("{}", encoding="utf-8")
        time.sleep(0.01)
        derived = tmp_path / "frequency.json"
        derived.write_text("{}", encoding="utf-8")
        time.sleep(0.01)
        stale_source = tmp_path / "stale.lexicon.json"
        stale_source.write_text("{}", encoding="utf-8")
        found = build_snapshot.stale_inputs([derived], [fresh, stale_source])
        assert found == [(derived, stale_source)]

    def test_a_derived_file_that_does_not_exist_is_not_stale(self, tmp_path):
        lexicon = tmp_path / "a.lexicon.json"
        lexicon.write_text("{}", encoding="utf-8")
        assert build_snapshot.stale_inputs([tmp_path / "absent.json"], [lexicon]) == []

    def test_the_build_refuses_and_names_the_command_to_run(self, tmp_path, capsys):
        import time

        frequency = tmp_path / "frequency.json"
        frequency.write_text("{}", encoding="utf-8")
        time.sleep(0.01)
        lexicon = tmp_path / "a.lexicon.json"
        lexicon.write_text(MINI_LEXICON.read_text(), encoding="utf-8")

        code = build_snapshot.main(
            [
                "--lexicon",
                str(lexicon),
                "--db",
                str(tmp_path / "graph.sqlite"),
                "--label",
                "stale",
                "--frequency",
                str(frequency),
            ]
        )
        assert code == 2
        err = capsys.readouterr().err
        assert "is older than" in err
        assert "--write-inputs" in err
        assert not (tmp_path / "graph.sqlite").exists()

    def test_ignore_stale_builds_anyway(self, tmp_path, capsys):
        """An override, because a lexicon touched by a checkout is not a
        changed lexicon and a person can see that when the tool cannot."""
        import time

        frequency = tmp_path / "frequency.json"
        frequency.write_text(
            json.dumps(
                {
                    "name": "wordfreq",
                    "version": "1",
                    "retrieved_at": "2026-09-28T12:00:00+00:00",
                    "lang": "en",
                    "scores": {},
                }
            ),
            encoding="utf-8",
        )
        time.sleep(0.01)
        lexicon = tmp_path / "a.lexicon.json"
        lexicon.write_text(MINI_LEXICON.read_text(), encoding="utf-8")

        code = build_snapshot.main(
            [
                "--lexicon",
                str(lexicon),
                "--db",
                str(tmp_path / "graph.sqlite"),
                "--label",
                "stale",
                "--frequency",
                str(frequency),
                "--dev-embeddings",
                "--ignore-stale",
            ]
        )
        assert code == 0

    def test_a_fresh_build_is_not_blocked(self, tmp_path):
        import time

        lexicon = tmp_path / "a.lexicon.json"
        lexicon.write_text(MINI_LEXICON.read_text(), encoding="utf-8")
        time.sleep(0.01)
        frequency = tmp_path / "frequency.json"
        frequency.write_text(
            json.dumps(
                {
                    "name": "wordfreq",
                    "version": "1",
                    "retrieved_at": "2026-09-28T12:00:00+00:00",
                    "lang": "en",
                    "scores": {},
                }
            ),
            encoding="utf-8",
        )
        code = build_snapshot.main(
            [
                "--lexicon",
                str(lexicon),
                "--db",
                str(tmp_path / "graph.sqlite"),
                "--label",
                "fresh",
                "--frequency",
                str(frequency),
                "--dev-embeddings",
            ]
        )
        assert code == 0


class TestBuildingIntoAPopulatedDatabase:
    """A build merges into whatever the store already holds.

    This is the failure that cost two round trips. The depth 3 snapshot was
    still in ``content/graph.sqlite`` when the depth 6 build ran, so for a
    lemma with several senses the old database's definition won over the one
    the export files produce. The prepare step computed the texts from the
    files, the embedding table was keyed on those, and the build died at its
    last step with a KeyError naming five plant names.
    """

    def build_once(self, db: Path, label: str = "first", *extra: str) -> int:
        return build_snapshot.main(
            [
                "--lexicon",
                str(MINI_LEXICON),
                "--db",
                str(db),
                "--label",
                label,
                "--now",
                "2026-09-28T12:00:00+00:00",
                "--dev-embeddings",
                *extra,
            ]
        )

    def test_a_first_build_into_a_fresh_file_succeeds(self, tmp_path):
        assert self.build_once(tmp_path / "graph.sqlite") == 0

    def test_a_second_build_is_refused(self, tmp_path, capsys):
        db = tmp_path / "graph.sqlite"
        assert self.build_once(db) == 0
        capsys.readouterr()
        assert self.build_once(db, "second") == 2

    def test_the_refusal_says_what_is_there_and_what_to_do(self, tmp_path, capsys):
        db = tmp_path / "graph.sqlite"
        self.build_once(db)
        capsys.readouterr()
        self.build_once(db, "second")
        err = capsys.readouterr().err
        assert "already holds content" in err
        assert "entities=" in err
        assert "--reuse-db" in err
        assert "Delete the file" in err

    def test_the_refused_build_changes_nothing(self, tmp_path, capsys):
        db = tmp_path / "graph.sqlite"
        self.build_once(db)
        repos = GraphRepositories(SqliteDocumentStore(db))
        before = repos.entities.count()
        repos.close()

        self.build_once(db, "second")
        with opened(db) as repos:
            assert repos.entities.count() == before
            assert repos.snapshots.by_label("second") is None

    def test_reuse_db_merges_deliberately(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        self.build_once(db)
        assert self.build_once(db, "second", "--reuse-db") == 0
        with opened(db) as repos:
            assert repos.snapshots.by_label("second") is not None

    def test_an_empty_file_is_not_treated_as_populated(self, tmp_path):
        """Opening a store creates the file and its tables, so existence alone
        must not count as content."""
        db = tmp_path / "graph.sqlite"
        repos = GraphRepositories(SqliteDocumentStore(db))
        repos.close()
        assert db.exists()
        assert self.build_once(db) == 0

    def test_existing_content_reports_nothing_for_an_empty_store(self, tmp_path):
        with opened(tmp_path / "graph.sqlite") as repos:
            assert build_snapshot.existing_content(repos) == {}

    def test_existing_content_counts_each_collection_it_finds(self, tmp_path):
        db = tmp_path / "graph.sqlite"
        self.build_once(db)
        with opened(db) as repos:
            held = build_snapshot.existing_content(repos)
        assert set(held) == {"snapshots", "entities", "categories", "relationships"}
        assert all(count > 0 for count in held.values())
