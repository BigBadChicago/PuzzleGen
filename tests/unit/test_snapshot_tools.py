"""The collision report and the snapshot build driver.

Both are loaded by path, as the other tools are. The mini lexicon shipped in
``content/seeds/`` is the fixture: it is small, it is committed, and it already
contains the forked-ancestry and dangling-hypernym cases the providers were
written against.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from puzzlegen.core.types import ReviewStatus
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
        assert members["salmon"] == ["color"]

    def test_a_member_in_two_categories_lists_both(self):
        members = collisions.overlay_members(OVERLAY)
        assert len(members["watch"]) == 2

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
        terms, texts = build_snapshot.input_lists([path], tmp_path / "absent.json")
        assert terms == ["otter"]
        assert texts == ["otter: a river mammal"]

    def test_a_lemma_without_a_gloss_embeds_as_its_bare_name(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("otter", "s.otter")],
            synsets=[{"id": "s.otter", "name": "otter", "definition": "", "hypernyms": []}],
        )
        _, texts = build_snapshot.input_lists([path], tmp_path / "absent.json")
        assert texts == ["otter"]

    def test_overlay_members_join_the_term_list(self, tmp_path):
        path = write_lexicon(tmp_path / "a.lexicon.json", [sense("otter", "s.otter")])
        terms, _ = build_snapshot.input_lists([path], OVERLAY)
        assert "burlap" in terms and "otter" in terms

    def test_the_two_lists_stay_aligned(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("otter", "s.otter", "a river mammal"), sense("badger", "s.badger")],
        )
        terms, texts = build_snapshot.input_lists([path], OVERLAY)
        assert len(terms) == len(texts)
        assert all(text.startswith(term) for term, text in zip(terms, texts))

    def test_terms_are_sorted_so_two_machines_write_the_same_file(self, tmp_path):
        path = write_lexicon(
            tmp_path / "a.lexicon.json",
            [sense("otter", "s.otter"), sense("badger", "s.badger")],
        )
        terms, _ = build_snapshot.input_lists([path], tmp_path / "absent.json")
        assert terms == sorted(terms)

    def test_write_inputs_writes_both_files(self, tmp_path):
        path = write_lexicon(tmp_path / "a.lexicon.json", [sense("otter", "s.otter")])
        terms_path, texts_path = build_snapshot.write_inputs(
            tmp_path / "build", [path], OVERLAY
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

        repos = GraphRepositories(SqliteDocumentStore(db))
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
        repos = GraphRepositories(SqliteDocumentStore(db))
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
        repos = GraphRepositories(SqliteDocumentStore(db))
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
