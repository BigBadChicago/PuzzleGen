"""Every committed lexicon says which subtree it holds, and is held to it.

A filename is not evidence. `wordnet-tool.lexicon.json` held the `hand tool`
subtree (oewn-03494245-n) while a root list, a rebuild script and three
documents all said `tool`, and nothing errored: a build with the wrong subtree
produces a smaller snapshot, not a failure. The export tool records the synset
it resolved in the file, so these tests read that rather than the name.

Reading the file alone is not enough, which is the part the first version of
this module got wrong. The exporter writes whatever synset it resolved and the
collected subtree always contains it, so an export of `hand tool` declares
`oewn-03494245-n` and contains it, and a test asking only "is the declared
root one of your own synsets" passes in exactly the state that cost a week.
What was missing is a statement of intent outside the file. That is
`content/seeds/lexicon-roots.json`: one entry per root name, naming the synset
id, the lemma that synset actually carries, and the depth, resolved once
against the edition the manifest names. The rebuild script reads the same
manifest, so there is one place a root is declared and a mismatch between name
and subtree fails here rather than in a coverage investigation.

The lemma is checked as well as the id, against the file's own senses. An id
alone can be swapped in both places; an id whose synset has to go on carrying
the lemma the manifest claims cannot be, without also faking the content.

Cheap on purpose. This is the version of that bug that fails in half a second
on every push, rather than ten minutes into a runner or a week into a coverage
investigation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SEEDS = Path(__file__).resolve().parents[2] / "content" / "seeds"

#: The one place a root is declared. Read here and by the rebuild script.
MANIFEST = SEEDS / "lexicon-roots.json"

#: Hand authored, exported from nothing, and the one lexicon meant to be read
#: in full by a person. Exempt from the root declaration rule for that reason.
FIXTURES = {"wordnet-mini.lexicon.json"}

#: Three root names are shorthands for a longer lemma, chosen because the
#: longer subtree is not the one a puzzle wants: `fruit` is `edible fruit`,
#: `instrument` is `musical instrument`, `kitchen` is `kitchen utensil`. The
#: manifest records the real lemma for each, which is the point: a shorthand
#: that is written down is a shorthand, and one that is not is the `hand tool`
#: bug waiting to happen again.


def lexicons() -> list[Path]:
    return sorted(SEEDS.glob("wordnet-*.lexicon.json"))


def names() -> list[str]:
    return [path.name for path in lexicons()]


def root_name(name: str) -> str:
    """The root a lexicon file is named for: `wordnet-hand_tool...` -> `hand_tool`."""
    return name[len("wordnet-") : -len(".lexicon.json")]


@pytest.fixture(scope="module")
def documents() -> dict[str, dict]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in lexicons()
    }


@pytest.fixture(scope="module")
def manifest() -> dict[str, dict]:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return document["roots"]


def test_there_are_lexicons_to_check(documents):
    assert documents, f"no lexicon exports in {SEEDS}"


def test_the_manifest_names_the_edition_its_ids_came_from():
    """Synset ids are stable within an edition, not guaranteed across them.

    A manifest that did not say which edition resolved these would be a list
    of ids with nothing to check them against the day WordNet renumbers one.
    """
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert document.get("lexicon"), f"{MANIFEST.name} names no source edition"


def test_every_manifest_root_has_a_committed_export(manifest):
    """The manifest is the root list, so a root in it with no file is a gap.

    The old root list went stale at seven entries while the build grew to
    sixteen, and the tests then reported 78 overlay words as unsupplied when
    46 were. A list nothing checks drifts.
    """
    missing = sorted(
        root for root in manifest if not (SEEDS / f"wordnet-{root}.lexicon.json").exists()
    )
    assert not missing, f"manifest declares roots with no export: {missing}"


@pytest.mark.parametrize("name", names())
class TestEveryLexicon:
    def test_it_declares_the_synset_it_was_exported_from(self, name, documents):
        if name in FIXTURES:
            pytest.skip("hand authored, exported from no synset")
        root = documents[name].get("root")
        assert root, (
            f"{name} declares no root synset, so nothing in the repository "
            "records which subtree it holds"
        )

    def test_the_manifest_declares_it(self, name, manifest):
        """An export nothing declared is an export nobody chose."""
        if name in FIXTURES:
            pytest.skip("hand authored, exported from no synset")
        assert root_name(name) in manifest, (
            f"{name} is committed but {MANIFEST.name} does not declare root "
            f"{root_name(name)!r}, so nothing states which subtree it should hold"
        )

    def test_it_holds_the_subtree_the_manifest_declares(self, name, documents, manifest):
        """The check the `hand tool` incident needed.

        Comparing the file against an independent declaration rather than
        against itself: a file named `tool` holding the `hand tool` synset
        fails here, which is the state the repository was in for a week.
        """
        if name in FIXTURES:
            pytest.skip("hand authored, exported from no synset")
        declared = manifest[root_name(name)]["synset"]
        assert documents[name]["root"] == declared, (
            f"{name} holds {documents[name]['root']!r} but {MANIFEST.name} "
            f"declares {declared!r} for {root_name(name)!r}: either the export "
            "is of the wrong subtree or the manifest is out of date"
        )

    def test_the_declared_root_is_one_of_its_own_synsets(self, name, documents):
        """A root naming a synset the file does not contain is a truncated or
        mismatched export, which reads as a thin category rather than an error.
        """
        if name in FIXTURES:
            pytest.skip("hand authored, exported from no synset")
        document = documents[name]
        ids = {synset["id"] for synset in document["synsets"]}
        assert document["root"] in ids, (
            f"{name} declares root {document['root']!r}, which is not among "
            f"its {len(ids)} synsets"
        )

    def test_the_root_synset_carries_the_lemma_the_manifest_claims(
        self, name, documents, manifest
    ):
        """Names and ids can both be edited; the content is harder to fake.

        The manifest says `kitchen` means the synset whose lemma is `kitchen
        utensil`. If a later export resolved the room instead, the id changes
        and the previous test catches it; if someone reconciled both files by
        editing the id, this one still fails, because the senses in the file
        would have to carry the claimed lemma too.
        """
        if name in FIXTURES:
            pytest.skip("hand authored, exported from no synset")
        document = documents[name]
        expected = manifest[root_name(name)]["lemma"]
        lemmas = {
            sense["lemma"]
            for sense in document["senses"]
            if sense["synset"] == document["root"]
        }
        assert expected in lemmas, (
            f"{name} declares root {document['root']!r}, whose senses are "
            f"{sorted(lemmas)}, but {MANIFEST.name} calls it {expected!r}"
        )

    def test_it_holds_synsets_and_senses(self, name, documents):
        document = documents[name]
        assert document.get("synsets"), f"{name} holds no synsets"
        assert document.get("senses"), f"{name} holds no senses"

    def test_it_records_the_lexicon_and_edition_it_came_from(self, name, documents):
        """Two exports from different WordNet editions are different content.

        A snapshot that mixed them would be reproducible only by accident, so
        the edition is part of what a seed has to state. ``unversioned`` is
        rejected along with a missing value: the exporter writes that sentinel
        when ``--lexicon`` carries no edition, and a truthiness check accepted
        it, so the guard passed exports that name no edition at all.
        """
        document = documents[name]
        assert document.get("lexicon"), f"{name} names no source lexicon"
        version = document.get("version")
        assert version, f"{name} names no edition"
        assert version != "unversioned", (
            f"{name} names no edition: {version!r} is the exporter's sentinel "
            "for a --lexicon argument with no edition after the colon"
        )

    def test_its_edition_is_the_one_the_manifest_resolved_against(
        self, name, documents, manifest
    ):
        """One build, one edition.

        The manifest's ids were resolved against the edition it names. An
        export from another edition may hold a different subtree under the
        same id, which is the one way the comparison above can pass and still
        be wrong.
        """
        if name in FIXTURES:
            pytest.skip("hand authored, exported from no synset")
        declared = json.loads(MANIFEST.read_text(encoding="utf-8"))["lexicon"]
        document = documents[name]
        assert f"{document['lexicon']}:{document['version']}" == declared, (
            f"{name} came from {document['lexicon']}:{document['version']} but "
            f"{MANIFEST.name} resolved its ids against {declared}"
        )
