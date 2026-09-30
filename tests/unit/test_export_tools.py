"""The export tools the snapshot runbook calls, and the report it reads.

Three defects found by running the runbook against the real 2024 lexicon are
pinned here so they cannot return:

* ``--root animal.n.01`` failed with ``no such synset``, because ``wn`` takes
  lemmas and synset ids and does not understand that notation.
* ``export_frequency.py`` reported "wordfreq is required" on a machine that had
  it, because the package has no ``__version__`` and the import failed.
* The build report kept only the last of five lexicons' counts, because they
  share one provider name and the counts were assigned rather than summed.

Most tests use stubs so they run without ``wn`` or its data. The last class
runs against the real lexicon and skips itself when it is not installed.
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from conftest import NOW

from ..support.overlay_counts import CATEGORIES, ENTITIES

from puzzlegen.content.snapshots import ImportReport, SnapshotBuilder
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.wordnet import WordNetLexiconProvider

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
SEEDS = ROOT / "content" / "seeds"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"export_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


export_wordnet = load_tool("export_wordnet")
export_frequency = load_tool("export_frequency")
export_embeddings = load_tool("export_embeddings")


class StubLexicon:
    language = "en"


class StubWord:
    def __init__(self, lemma: str, forms: tuple[str, ...] = ()) -> None:
        self._lemma = lemma
        self._forms = forms

    def lemma(self) -> str:
        return self._lemma

    def forms(self) -> list[str]:
        return [self._lemma, *self._forms]


class StubSense:
    def __init__(self, sense_id: str, word: StubWord) -> None:
        self.id = sense_id
        self._word = word

    def word(self) -> StubWord:
        return self._word


class StubSynset:
    """Just the surface of a ``wn`` synset that the export tool calls."""

    def __init__(self, synset_id: str, lemmas: tuple[str, ...], gloss: str) -> None:
        self.id = synset_id
        self._lemmas = lemmas
        self._gloss = gloss
        self._children: list[StubSynset] = []
        self._parents: list[StubSynset] = []
        self._depth = 0

    def link(self, child: "StubSynset") -> "StubSynset":
        self._children.append(child)
        child._parents.append(self)
        child._depth = self._depth + 1
        return child

    def definition(self) -> str:
        return self._gloss

    def lemmas(self) -> list[str]:
        return list(self._lemmas)

    def hyponyms(self) -> list["StubSynset"]:
        return list(self._children)

    def hypernyms(self) -> list["StubSynset"]:
        return list(self._parents)

    def min_depth(self) -> int:
        return self._depth

    def max_depth(self) -> int:
        return self._depth

    def lexicon(self) -> StubLexicon:
        return StubLexicon()

    def senses(self) -> list[StubSense]:
        return [
            StubSense(f"sense.{self.id}.{lemma}", StubWord(lemma))
            for lemma in self._lemmas
        ]


class StubWordnet:
    def __init__(self, senses: dict[tuple[str, str], list[StubSynset]]) -> None:
        self._senses = senses
        self._by_id = {s.id: s for group in senses.values() for s in group}

    def synsets(self, form: str, pos: str | None = None) -> list[StubSynset]:
        found: list[StubSynset] = []
        for (lemma, part), group in self._senses.items():
            if lemma == form and pos in (None, part):
                found.extend(group)
        return found

    def synset(self, synset_id: str) -> StubSynset:
        return self._by_id[synset_id]


@pytest.fixture
def wordnet() -> StubWordnet:
    plant_factory = StubSynset("s.factory", ("plant",), "buildings for industrial labor")
    plant_organism = StubSynset(
        "s.organism", ("plant", "flora"), "(botany) a living organism lacking locomotion"
    )
    plant_spy = StubSynset("s.spy", ("plant",), "a person placed to gather information")
    tool = StubSynset("s.tool", ("tool",), "an implement used in a vocation")
    instrument = StubSynset(
        "s.instrument", ("musical instrument",), "a device that produces musical tones"
    )
    return StubWordnet(
        {
            ("plant", "n"): [plant_factory, plant_organism, plant_spy],
            ("plant", "v"): [StubSynset("s.sow", ("plant",), "put seeds into the ground")],
            ("tool", "n"): [tool],
            ("musical instrument", "n"): [instrument],
        }
    )


class TestResolvingARoot:
    def test_a_sense_name_picks_the_numbered_sense(self, wordnet):
        assert export_wordnet.resolve_root(wordnet, "plant.n.02").id == "s.organism"

    def test_sense_one_is_the_first_sense_listed(self, wordnet):
        assert export_wordnet.resolve_root(wordnet, "plant.n.01").id == "s.factory"

    def test_the_part_of_speech_in_the_name_filters_the_senses(self, wordnet):
        assert export_wordnet.resolve_root(wordnet, "plant.v.01").id == "s.sow"

    def test_underscores_in_a_multiword_lemma_become_spaces(self, wordnet):
        found = export_wordnet.resolve_root(wordnet, "musical_instrument.n.01")
        assert found.id == "s.instrument"

    def test_a_sense_beyond_the_last_is_refused_with_the_candidates(self, wordnet):
        with pytest.raises(export_wordnet.RootError) as caught:
            export_wordnet.resolve_root(wordnet, "plant.n.09")
        message = str(caught.value)
        assert "3 n sense(s)" in message
        assert "plant.n.02" in message and "s.organism" in message

    def test_sense_zero_does_not_exist(self, wordnet):
        with pytest.raises(export_wordnet.RootError, match="does not exist"):
            export_wordnet.resolve_root(wordnet, "plant.n.00")

    def test_a_bare_lemma_with_one_sense_resolves(self, wordnet):
        assert export_wordnet.resolve_root(wordnet, "tool").id == "s.tool"

    def test_a_bare_lemma_with_several_senses_is_refused_and_lists_them(self, wordnet):
        with pytest.raises(export_wordnet.RootError) as caught:
            export_wordnet.resolve_root(wordnet, "plant", pos="n")
        message = str(caught.value)
        assert "3 senses" in message
        assert "lemma.pos.NN" in message
        assert "s.factory" in message and "s.spy" in message

    def test_a_part_of_speech_can_make_a_bare_lemma_unique(self, wordnet):
        assert export_wordnet.resolve_root(wordnet, "plant", pos="v").id == "s.sow"

    def test_a_synset_id_resolves_exactly(self, wordnet):
        assert export_wordnet.resolve_root(wordnet, "s.spy").id == "s.spy"

    def test_something_that_is_nothing_is_refused(self, wordnet):
        with pytest.raises(export_wordnet.RootError, match="neither a lemma"):
            export_wordnet.resolve_root(wordnet, "no-such-thing")

    def test_the_candidate_listing_is_numbered_the_way_names_count(self, wordnet):
        lines = export_wordnet.candidate_lines(
            wordnet.synsets("plant", pos="n"), "plant", "n"
        )
        assert [line.split()[0] for line in lines] == [
            "plant.n.01",
            "plant.n.02",
            "plant.n.03",
        ]

    def test_a_long_definition_is_cut_in_the_listing(self, wordnet):
        long = StubSynset("s.long", ("thing",), "x" * 200)
        lines = export_wordnet.candidate_lines([long], "thing", "n")
        assert len(lines[0]) < 140


class TestCollecting:
    def tree(self) -> StubSynset:
        root = StubSynset("s.animal", ("animal",), "a living organism")
        mammal = root.link(StubSynset("s.mammal", ("mammal",), "a warm blooded animal"))
        dog = mammal.link(StubSynset("s.dog", ("dog",), "a domestic canine"))
        dog.link(StubSynset("s.puppy", ("puppy",), "a young dog"))
        return root

    def test_every_level_up_to_the_depth_is_included(self):
        found = export_wordnet.collect(self.tree(), 2)
        assert set(found) == {"s.animal", "s.mammal", "s.dog"}

    def test_the_depth_limit_excludes_deeper_levels(self):
        assert "s.puppy" not in export_wordnet.collect(self.tree(), 2)

    def test_depth_zero_is_the_root_alone(self):
        assert set(export_wordnet.collect(self.tree(), 0)) == {"s.animal"}

    def test_a_synset_reached_twice_is_collected_once(self):
        root = StubSynset("s.root", ("root",), "r")
        left = root.link(StubSynset("s.left", ("left",), "l"))
        right = root.link(StubSynset("s.right", ("right",), "r"))
        shared = StubSynset("s.shared", ("shared",), "s")
        left.link(shared)
        right.link(shared)
        found = export_wordnet.collect(root, 3)
        assert sorted(found) == ["s.left", "s.right", "s.root", "s.shared"]


@pytest.fixture
def fake_wn(monkeypatch, wordnet):
    module = types.ModuleType("wn")
    module.Wordnet = lambda lexicon: wordnet
    monkeypatch.setitem(sys.modules, "wn", module)
    return module


class TestWordnetCommandLine:
    def test_an_export_writes_a_file_the_provider_can_read(
        self, tmp_path, capsys, monkeypatch, fake_wn
    ):
        root = StubSynset("s.animal", ("animal",), "a living organism")
        root.link(StubSynset("s.mammal", ("mammal", "mammalian"), "a warm blooded animal"))
        monkeypatch.setattr(
            export_wordnet,
            "resolve_root",
            lambda wordnet, spec, pos=None: root,
        )
        out = tmp_path / "animal.lexicon.json"
        code = export_wordnet.main(["--root", "animal.n.01", "--out", str(out)])
        assert code == 0
        printed = capsys.readouterr().out
        assert "root: s.animal" in printed and "wrote 2 synsets" in printed

        document = json.loads(out.read_text())
        assert document["root"] == "s.animal"
        assert [s["id"] for s in document["synsets"]] == ["s.animal", "s.mammal"]

        # The unknown "root" key must not upset the reader.
        bundle = WordNetLexiconProvider(out).load()
        assert bundle.entities

    def test_the_resolved_root_is_printed_before_anything_is_written(
        self, tmp_path, capsys, fake_wn
    ):
        out = tmp_path / "plant.lexicon.json"
        export_wordnet.main(["--root", "plant.n.02", "--out", str(out)])
        assert capsys.readouterr().out.startswith("root: s.organism  (botany)")

    def test_an_ambiguous_root_exits_one_and_writes_nothing(
        self, tmp_path, capsys, fake_wn
    ):
        out = tmp_path / "plant.lexicon.json"
        code = export_wordnet.main(["--root", "plant", "--pos", "n", "--out", str(out)])
        assert code == 1
        assert "3 senses" in capsys.readouterr().err
        assert not out.exists()

    def test_list_prints_the_candidates_and_writes_nothing(
        self, tmp_path, capsys, fake_wn
    ):
        code = export_wordnet.main(["--root", "plant", "--pos", "n", "--list"])
        assert code == 0
        assert capsys.readouterr().out.count("plant.n.0") == 3

    def test_list_for_an_unknown_lemma_exits_one(self, capsys, fake_wn):
        assert export_wordnet.main(["--root", "zzz", "--list"]) == 1
        assert "no senses" in capsys.readouterr().err

    def test_an_export_without_out_is_refused(self, capsys, fake_wn):
        assert export_wordnet.main(["--root", "tool.n.01"]) == 2
        assert "--out is required" in capsys.readouterr().err

    def test_a_missing_wn_package_exits_two(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setitem(sys.modules, "wn", None)
        code = export_wordnet.main(
            ["--root", "tool.n.01", "--out", str(tmp_path / "x.json")]
        )
        assert code == 2
        assert "wn package is required" in capsys.readouterr().err


class FakeWordfreq(types.ModuleType):
    """A wordfreq with no ``__version__``, which is how the real one is."""

    def __init__(self) -> None:
        super().__init__("wordfreq")

    @staticmethod
    def zipf_frequency(term: str, lang: str) -> float:
        return {"salmon": 4.0, "quokka": 0.0}.get(term, 3.5)


class TestFrequencyExport:
    @pytest.fixture
    def terms(self, tmp_path) -> Path:
        path = tmp_path / "terms.txt"
        path.write_text("salmon\nquokka\nolive\n", encoding="utf-8")
        return path

    def test_a_wordfreq_without_a_version_attribute_still_works(
        self, tmp_path, terms, monkeypatch, capsys
    ):
        assert not hasattr(FakeWordfreq(), "__version__")
        monkeypatch.setitem(sys.modules, "wordfreq", FakeWordfreq())
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
        out = tmp_path / "frequency.json"
        code = export_frequency.main(["--terms", str(terms), "--out", str(out)])
        assert code == 0
        document = json.loads(out.read_text())
        assert document["version"] == "9.9.9"
        assert "wrote 2 scores" in capsys.readouterr().out

    def test_unscored_terms_are_left_out(self, tmp_path, terms, monkeypatch):
        monkeypatch.setitem(sys.modules, "wordfreq", FakeWordfreq())
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
        out = tmp_path / "frequency.json"
        export_frequency.main(["--terms", str(terms), "--out", str(out)])
        assert set(json.loads(out.read_text())["scores"]) == {"olive", "salmon"}

    def test_a_missing_package_still_says_so(
        self, tmp_path, terms, monkeypatch, capsys
    ):
        monkeypatch.setitem(sys.modules, "wordfreq", None)
        code = export_frequency.main(
            ["--terms", str(terms), "--out", str(tmp_path / "f.json")]
        )
        assert code == 2
        assert "wordfreq package is required" in capsys.readouterr().err

    def test_the_output_loads_as_a_frequency_table(self, tmp_path, terms, monkeypatch):
        from puzzlegen.providers.frequency import TableFrequencyProvider

        monkeypatch.setitem(sys.modules, "wordfreq", FakeWordfreq())
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.9.9")
        out = tmp_path / "frequency.json"
        export_frequency.main(["--terms", str(terms), "--out", str(out)])
        provider = TableFrequencyProvider.from_file(out)
        assert provider.score("salmon", "en") is not None
        assert provider.describe().version == "9.9.9"


class TestBuildReportCounts:
    def import_two(self, repos, name: str) -> ImportReport:
        report = ImportReport(snapshot_id="", label="counts")
        builder = SnapshotBuilder(repos, now=NOW)
        builder.import_provider(
            CuratedJSONProvider(SEEDS / "animals.curated.json", name=name, now=NOW),
            report=report,
        )
        builder.import_provider(
            CuratedJSONProvider(SEEDS / "overlay.curated.json", name=name, now=NOW),
            taxonomy="overlay",
            report=report,
        )
        return report

    def test_two_imports_under_one_name_are_summed(self, repos):
        report = self.import_two(repos, "shared")
        assert list(report.provider_counts) == ["shared"]
        counts = report.provider_counts["shared"]
        # The animals seed and the overlay share lemmas now, and lemma
        # identity merges them, so the per provider sum counts what each
        # import produced while the graph holds fewer records than the total.
        assert counts["entities"] == 23 + ENTITIES
        assert counts["categories"] == 8 + CATEGORIES

    def test_two_imports_under_two_names_stay_separate(self, repos):
        report = ImportReport(snapshot_id="", label="counts")
        builder = SnapshotBuilder(repos, now=NOW)
        builder.import_provider(
            CuratedJSONProvider(SEEDS / "animals.curated.json", name="a", now=NOW),
            report=report,
        )
        builder.import_provider(
            CuratedJSONProvider(SEEDS / "overlay.curated.json", name="b", now=NOW),
            taxonomy="overlay",
            report=report,
        )
        assert report.provider_counts["a"]["entities"] == 23
        assert report.provider_counts["b"]["entities"] == ENTITIES

    def test_the_summed_count_is_what_each_import_produced(self, repos):
        """Not what the graph ends up holding.

        The two seeds share lemmas, and lemma identity merges them, so the
        graph holds fewer entities than the imports produced between them. The
        report answers "what did this provider contribute", which is the
        question a build operator is asking.
        """
        report = self.import_two(repos, "shared")
        produced = report.provider_counts["shared"]["entities"]
        assert produced == 23 + ENTITIES
        assert repos.entities.count() <= produced


ORACLE = [
    ("animal.n.01", "oewn-00015568-n", "living organism"),
    ("plant.n.02", "oewn-00017402-n", "botany"),
    ("tool.n.01", "oewn-04459089-n", "implement"),
    ("vehicle.n.01", "oewn-04531608-n", "conveyance"),
    ("musical_instrument.n.01", "oewn-03806455-n", "musical"),
]


@pytest.fixture(scope="module")
def oewn():
    wn = pytest.importorskip("wn")
    try:
        return wn.Wordnet(lexicon="oewn:2024")
    except Exception:  # noqa: BLE001
        pytest.skip("oewn:2024 is not installed: python -m wn download oewn:2024")


class TestAgainstTheRealLexicon:
    """The runbook's five roots, resolved by the real library.

    Skipped without ``wn`` and its data, so the suite still runs on a plain
    ``pip install -e .[dev]``. Where they do run, these are what would have
    caught the original failure.
    """

    @pytest.mark.parametrize("spec,synset_id,fragment", ORACLE)
    def test_each_runbook_root_resolves_to_the_intended_sense(
        self, oewn, spec, synset_id, fragment
    ):
        found = export_wordnet.resolve_root(oewn, spec)
        assert found.id == synset_id
        assert fragment in found.definition()

    def test_the_wrong_plant_sense_is_the_factory(self, oewn):
        """Why the resolved root is printed: sense numbers matter."""
        found = export_wordnet.resolve_root(oewn, "plant.n.01")
        assert "industrial" in found.definition()

    def test_a_polysemous_bare_lemma_is_refused(self, oewn):
        with pytest.raises(export_wordnet.RootError):
            export_wordnet.resolve_root(oewn, "plant", pos="n")

    def test_the_original_failing_form_no_longer_raises(self, oewn):
        assert export_wordnet.resolve_root(oewn, "animal.n.01") is not None

    def test_a_real_synset_id_resolves(self, oewn):
        assert export_wordnet.resolve_root(oewn, "oewn-00015568-n").id == (
            "oewn-00015568-n"
        )

    def test_a_shallow_real_export_is_readable_by_the_provider(self, oewn, tmp_path):
        out = tmp_path / "vehicle.lexicon.json"
        code = export_wordnet.main(
            ["--root", "vehicle.n.01", "--depth", "1", "--out", str(out)]
        )
        assert code == 0
        document = json.loads(out.read_text())
        assert document["root"] == "oewn-04531608-n"
        assert document["synsets"] and document["senses"]
        assert WordNetLexiconProvider(out).load().entities


class TestEmbeddingFileFormat:
    """One line per vector.

    ``json.dumps(..., indent=2)`` puts each of 384 components on its own line,
    about 6.4 kB per vector. At 14,844 entities that is a 94 MB file, within
    reach of GitHub's per-file limit, and a diff nobody can read.
    """

    def rows(self, count: int = 50, dims: int = 384) -> dict[str, list[float]]:
        import random

        random.seed(7)
        return {
            f"word{i}: a gloss of about the usual length for a synset": [
                round(random.gauss(0, 0.05), 4) for _ in range(dims)
            ]
            for i in range(count)
        }

    def header(self) -> dict:
        return {
            "model_name": "sentence-transformers/all-MiniLM-L6-v2",
            "model_version": "unversioned",
            "similarity_metric": "cosine",
            "computed_at": "2026-09-28T12:00:00+00:00",
        }

    def test_the_output_is_valid_json(self):
        document = json.loads(export_embeddings.render(self.header(), self.rows()))
        assert len(document["vectors"]) == 50

    def test_every_vector_survives_the_round_trip(self):
        rows = self.rows()
        document = json.loads(export_embeddings.render(self.header(), rows))
        assert document["vectors"] == rows

    def test_the_header_fields_survive(self):
        document = json.loads(export_embeddings.render(self.header(), self.rows()))
        assert document["model_name"].endswith("all-MiniLM-L6-v2")
        assert document["similarity_metric"] == "cosine"

    def test_one_line_per_vector(self):
        text = export_embeddings.render(self.header(), self.rows())
        vector_lines = [line for line in text.splitlines() if line.startswith('    "word')]
        assert len(vector_lines) == 50

    def test_a_vector_costs_far_less_than_the_indented_form(self):
        rows = self.rows()
        compact = len(export_embeddings.render(self.header(), rows).encode())
        indented = len(
            json.dumps({**self.header(), "vectors": rows}, indent=2, sort_keys=True).encode()
        )
        # Measured: 2903 bytes against 5596, a fraction under half. The
        # saving is the 384 newlines and their indentation, not the digits.
        assert compact < indented * 0.55
        assert compact / len(rows) < 3000

    def test_keys_are_sorted_so_two_machines_agree(self):
        rows = self.rows()
        shuffled = dict(reversed(list(rows.items())))
        assert export_embeddings.render(self.header(), rows) == export_embeddings.render(
            self.header(), shuffled
        )

    def test_a_single_vector_needs_no_trailing_comma(self):
        text = export_embeddings.render(self.header(), {"a": [0.1, 0.2]})
        assert json.loads(text)["vectors"] == {"a": [0.1, 0.2]}

    def test_a_text_containing_quotes_is_escaped(self):
        rows = {'galley: a ship\'s kitchen': [0.1, 0.2]}
        document = json.loads(export_embeddings.render(self.header(), rows))
        assert document["vectors"] == rows

    def test_the_table_provider_reads_it(self, tmp_path):
        from puzzlegen.providers.embeddings import TableEmbeddingProvider

        rows = self.rows(count=3, dims=8)
        path = tmp_path / "embeddings.json"
        path.write_text(export_embeddings.render(self.header(), rows), encoding="utf-8")
        provider = TableEmbeddingProvider.from_file(path)
        vectors = provider.embed(list(rows))
        assert len(vectors) == 3
        assert provider.describe().dimensions == 8

    def test_four_decimals_is_below_the_noise_floor_of_a_cosine(self):
        """Rounding changes no comparison a gate would make.

        Normalised 384 component vectors have components near 0.05, so the
        fourth decimal is a part in five hundred of one component and a far
        smaller part of a dot product over all of them.
        """
        import math
        import random

        random.seed(11)

        def unit(dims: int) -> list[float]:
            raw = [random.gauss(0, 1) for _ in range(dims)]
            norm = math.sqrt(sum(x * x for x in raw))
            return [x / norm for x in raw]

        def cosine(a, b):
            return sum(x * y for x, y in zip(a, b))

        worst = 0.0
        for _ in range(200):
            a, b = unit(384), unit(384)
            exact = cosine(a, b)
            rounded = cosine(
                [round(x, 4) for x in a], [round(x, 4) for x in b]
            )
            worst = max(worst, abs(exact - rounded))
        # Measured worst case over 200 random pairs: 1.3e-4. Similarity
        # thresholds in this engine are set to two decimals, so a drift in the
        # fourth changes no gate's answer.
        assert worst < 1e-3
