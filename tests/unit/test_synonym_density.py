"""Synonym density, not hyponym breadth, is what sizes a direct-membership
category. These tests fix the mistake in the tool's first draft: a synset's
own lemma count is what ``lexical_members`` actually holds, and it is a
different, usually much smaller, number than how many child concepts sit
beneath it in the hierarchy.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "tool_synonym_density", TOOLS / "synonym_density.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


synonym_density = load_tool()


def write(path: Path, synsets: list[dict], senses: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "lexicon_schema": 1,
                "lexicon": "test",
                "version": "1",
                "root": "root",
                "synsets": synsets,
                "senses": senses,
            }
        )
    )
    return path


def synset(id_: str, name: str, definition: str = "d") -> dict:
    return {
        "id": id_,
        "name": name,
        "definition": definition,
        "hypernyms": [],
        "min_depth": 1,
        "max_depth": 1,
        "lang": "en",
    }


def sense(lemma: str, synset_id: str) -> dict:
    return {
        "id": f"s.{lemma}",
        "lemma": lemma,
        "forms": [],
        "synset": synset_id,
        "definition": "d",
        "lang": "en",
    }


class TestDensityMeasuresOwnLemmasNotHyponyms:
    def test_a_synset_with_many_hyponyms_but_one_lemma_scores_one(self, tmp_path):
        """The exact ``boat`` case: bushy in the hierarchy, not in synonymy."""
        path = write(
            tmp_path / "a.lexicon.json",
            [
                synset("s.boat", "boat"),
                {**synset("s.canoe", "canoe"), "hypernyms": ["s.boat"]},
                {**synset("s.kayak", "kayak"), "hypernyms": ["s.boat"]},
                {**synset("s.dory", "dory"), "hypernyms": ["s.boat"]},
            ],
            [sense("boat", "s.boat"), sense("canoe", "s.canoe"),
             sense("kayak", "s.kayak"), sense("dory", "s.dory")],
        )

        rows = synonym_density.density([path])
        boat = next(r for r in rows if r["name"] == "boat")

        assert boat["own_lemma_count"] == 1

    def test_a_synset_with_many_true_synonyms_scores_high(self, tmp_path):
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.periwinkle", "periwinkle")],
            [
                sense(lemma, "s.periwinkle")
                for lemma in ["periwinkle", "rose periwinkle", "Cape periwinkle", "old maid"]
            ],
        )

        rows = synonym_density.density([path])

        assert rows[0]["own_lemma_count"] == 4

    def test_synsets_with_no_lemmas_are_left_out(self, tmp_path):
        """A synset with zero senses contributes nothing to membership size."""
        path = write(tmp_path / "a.lexicon.json", [synset("s.x", "x")], [])

        rows = synonym_density.density([path])

        assert rows == []


class TestSameConceptFlag:
    def test_synonym_pileup_is_flagged(self, tmp_path):
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.p", "periwinkle")],
            [
                sense(lemma, "s.p")
                for lemma in [
                    "periwinkle", "rose periwinkle", "Cape periwinkle",
                    "Madagascar periwinkle", "red periwinkle",
                ]
            ],
        )

        rows = synonym_density.density([path])

        assert synonym_density.same_concept_cluster(rows[0])

    def test_five_unrelated_words_are_not_flagged(self, tmp_path):
        """A category should not be penalized just for being the right size."""
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.tools", "hand tool")],
            [
                sense(lemma, "s.tools")
                for lemma in ["hammer", "chisel", "plane", "file", "awl"]
            ],
        )

        rows = synonym_density.density([path])

        assert not synonym_density.same_concept_cluster(rows[0])

    def test_a_pair_sharing_one_word_is_not_enough_to_flag(self, tmp_path):
        """Two lemmas overlapping the name isn't the pattern; most must."""
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.x", "boat")],
            [
                sense(lemma, "s.x")
                for lemma in ["boat", "sailboat", "canoe", "ferry", "yacht"]
            ],
        )

        rows = synonym_density.density([path])

        assert not synonym_density.same_concept_cluster(rows[0])


class TestMergedCollisionsShowUpToo:
    def test_a_name_collision_across_synsets_is_visible_as_one_row(self, tmp_path):
        """Density is computed on raw senses grouped by synset id, exactly as
        stored in the file -- a merge only happens at import time in the
        normalizer, not here. Two DIFFERENT synset ids sharing a name are
        still two separate rows at this stage, which is correct: this tool
        answers "how many senses does this exact WordNet synset have", not
        "how big will the merged category become after import".
        """
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.jackal1", "jackal"), synset("s.jackal2", "jackal")],
            [
                sense("jackal", "s.jackal1"),
                sense("golden jackal", "s.jackal1"),
                sense("jackal", "s.jackal2"),
                sense("side-striped jackal", "s.jackal2"),
            ],
        )

        rows = synonym_density.density([path])

        assert len(rows) == 2
        assert {r["own_lemma_count"] for r in rows} == {2}


class TestRendering:
    def test_min_size_filters_the_shown_rows(self, tmp_path):
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.a", "a"), synset("s.b", "b")],
            [sense("a1", "s.a"), sense("a2", "s.a"), sense("a3", "s.a"),
             sense("a4", "s.a"), sense("a5", "s.a"), sense("b1", "s.b")],
        )

        rows = synonym_density.density([path])
        text = synonym_density.render(rows, limit=40, min_size=5)

        assert "1 reach 5+" in text
        assert "lemmas: a1, a2, a3, a4, a5" in text
        assert "lemmas: b1" not in text

    def test_the_flag_text_appears_for_a_pileup(self, tmp_path):
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.p", "periwinkle")],
            [
                sense(lemma, "s.p")
                for lemma in [
                    "periwinkle", "rose periwinkle", "Cape periwinkle",
                    "Madagascar periwinkle", "red periwinkle",
                ]
            ],
        )

        rows = synonym_density.density([path])
        text = synonym_density.render(rows, limit=40, min_size=5)

        assert "check before using as a visible group" in text

    def test_main_writes_json_when_asked(self, tmp_path, capsys):
        path = write(
            tmp_path / "a.lexicon.json",
            [synset("s.tools", "hand tool")],
            [
                sense(lemma, "s.tools")
                for lemma in ["hammer", "chisel", "plane", "file", "awl"]
            ],
        )
        out = tmp_path / "density.json"

        code = synonym_density.main(
            ["--lexicon", str(path), "--min-size", "5", "--out", str(out)]
        )
        capsys.readouterr()

        assert code == 0
        document = json.loads(out.read_text())
        assert document[0]["own_lemma_count"] == 5

    def test_multiple_files_are_combined(self, tmp_path, capsys):
        a = write(tmp_path / "a.lexicon.json", [synset("s.x", "x")],
                  [sense(f"x{i}", "s.x") for i in range(5)])
        b = write(tmp_path / "b.lexicon.json", [synset("s.y", "y")],
                  [sense(f"y{i}", "s.y") for i in range(6)])

        code = synonym_density.main(
            ["--lexicon", str(a), "--lexicon", str(b), "--min-size", "5"]
        )
        out = capsys.readouterr().out

        assert code == 0
        assert "y" in out and "x" in out
