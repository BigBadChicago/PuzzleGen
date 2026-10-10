"""Every committed lexicon says which subtree it holds.

A filename is not evidence. `wordnet-tool.lexicon.json` held the `hand tool`
subtree (oewn-03494245-n) while a root list, a rebuild script and three
documents all said `tool`, and nothing errored: a build with the wrong subtree
produces a smaller snapshot, not a failure. The export tool records the synset
it resolved in the file, so these tests read that rather than the name.

Cheap on purpose. This is the version of that bug that fails in half a second
on every push, rather than ten minutes into a runner or a week into a coverage
investigation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SEEDS = Path(__file__).resolve().parents[2] / "content" / "seeds"

#: Hand authored, exported from nothing, and the one lexicon meant to be read
#: in full by a person. Exempt from the root declaration rule for that reason.
FIXTURES = {"wordnet-mini.lexicon.json"}


def lexicons() -> list[Path]:
    return sorted(SEEDS.glob("wordnet-*.lexicon.json"))


def names() -> list[str]:
    return [path.name for path in lexicons()]


@pytest.fixture(scope="module")
def documents() -> dict[str, dict]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in lexicons()
    }


def test_there_are_lexicons_to_check(documents):
    assert documents, f"no lexicon exports in {SEEDS}"


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

    def test_it_holds_synsets_and_senses(self, name, documents):
        document = documents[name]
        assert document.get("synsets"), f"{name} holds no synsets"
        assert document.get("senses"), f"{name} holds no senses"

    def test_it_records_the_lexicon_and_edition_it_came_from(self, name, documents):
        """Two exports from different WordNet editions are different content.

        A snapshot that mixed them would be reproducible only by accident, so
        the edition is part of what a seed has to state.
        """
        document = documents[name]
        assert document.get("lexicon"), f"{name} names no source lexicon"
        assert document.get("version"), f"{name} names no edition"
