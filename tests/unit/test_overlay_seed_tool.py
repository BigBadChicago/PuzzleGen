"""Retiring and reopening an overlay category, and being able to undo it.

A category that cannot make a board is better set aside than deleted, because
the reason it failed and the condition for trying again are worth more than
the words. These tests hold the two operations to being exact inverses, on a
small document that has every awkward case in it and on the committed seed.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from puzzlegen.providers.curated import CuratedJSONProvider

from ..conftest import NOW

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "content" / "seeds" / "overlay.curated.json"


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "tool_overlay_seed", ROOT / "tools" / "overlay_seed.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()

WHY = "its parents nest under one another"
WHEN = "four peer parents each hold five kinds"


def row(name: str, *categories: str) -> dict:
    return {"key": f"e.{name}", "name": name, "categories": list(categories), "confidence": 0.95}


def small() -> dict:
    """Two live categories: one member exclusive to it, one shared, one other."""
    return {
        "curated_schema": 1,
        "version": "test",
        "updated": "2026-09-01",
        "warnings": ["a warning"],
        "categories": [
            {"key": "o.keep", "name": "kept", "gloss": "stays"},
            {"key": "o.go", "name": "going", "gloss": "leaves"},
        ],
        "entities": [
            row("alone", "o.go"),
            row("both", "o.keep", "o.go"),
            row("stays", "o.keep"),
            row("also_alone", "o.go"),
        ],
    }


def retire(document, key="o.go", **overrides):
    arguments = {"reason": WHY, "reopen_when": WHEN, "on": "2026-09-30", **overrides}
    return tool.retire(document, key, **arguments)


class TestRetiring:
    def test_the_category_leaves_the_live_list(self):
        result = retire(small())

        assert [c["key"] for c in result["categories"]] == ["o.keep"]

    def test_the_original_document_is_left_alone(self):
        original = small()
        snapshot = copy.deepcopy(original)

        retire(original)

        assert original == snapshot

    def test_a_shared_member_stays_with_its_other_category(self):
        result = retire(small())

        both = next(e for e in result["entities"] if e["name"] == "both")
        assert both["categories"] == ["o.keep"]

    def test_a_member_left_with_no_category_is_set_aside(self):
        result = retire(small())

        assert {e["name"] for e in result["entities"]} == {"both", "stays"}
        entry = result["retired"][0]
        assert [r["row"]["name"] for r in entry["entities_removed"]] == ["alone", "also_alone"]

    def test_it_records_who_was_in_it_and_why_it_went(self):
        entry = retire(small())["retired"][0]

        assert entry["members"] == ["alone", "both", "also_alone"]
        assert entry["reason"] == WHY
        assert entry["reopen_when"] == WHEN
        assert entry["retired_on"] == "2026-09-30"
        assert entry["position"] == 1

    def test_the_update_date_moves(self):
        assert retire(small())["updated"] == "2026-09-30"

    @pytest.mark.parametrize("field", ["reason", "reopen_when"])
    def test_a_retirement_needs_a_reason_and_a_way_back(self, field):
        with pytest.raises(tool.SeedError, match="reason"):
            retire(small(), **{field: "  "})

    def test_an_unknown_category_is_refused(self):
        with pytest.raises(tool.SeedError, match="no category"):
            retire(small(), key="o.nope")

    def test_a_retired_category_cannot_be_retired_twice(self):
        with pytest.raises(tool.SeedError, match="already retired"):
            retire(retire(small()))

    def test_a_bad_date_is_refused(self):
        with pytest.raises(ValueError):
            retire(small(), on="last tuesday")


class TestReopening:
    def test_it_is_the_exact_inverse_when_members_were_exclusive(self):
        document = small()
        document["entities"] = [e for e in document["entities"] if e["name"] != "both"]

        restored = tool.reopen(retire(document), "o.go", on="2026-09-01")

        assert restored == document

    def test_a_shared_member_gets_its_category_back(self):
        restored = tool.reopen(retire(small()), "o.go")

        both = next(e for e in restored["entities"] if e["name"] == "both")
        assert set(both["categories"]) == {"o.keep", "o.go"}

    def test_every_membership_comes_back(self):
        original = small()

        restored = tool.reopen(retire(original), "o.go")

        def memberships(document):
            return {(e["name"], k) for e in document["entities"] for k in e["categories"]}

        assert memberships(restored) == memberships(original)

    def test_the_category_returns_to_its_position(self):
        restored = tool.reopen(retire(small()), "o.go")

        assert [c["key"] for c in restored["categories"]] == ["o.keep", "o.go"]

    def test_the_retired_section_disappears_when_empty(self):
        assert "retired" not in tool.reopen(retire(small()), "o.go")

    def test_another_retirement_stays_put(self):
        twice = retire(retire(small()), key="o.keep")

        once = tool.reopen(twice, "o.go")

        assert [e["key"] for e in once["retired"]] == ["o.keep"]

    def test_a_category_that_is_not_retired_is_refused(self):
        with pytest.raises(tool.SeedError, match="not retired"):
            tool.reopen(small(), "o.go")

    def test_a_live_category_is_refused(self):
        broken = retire(small())
        broken["categories"].append({"key": "o.go", "name": "going", "gloss": "leaves"})

        with pytest.raises(tool.SeedError, match="already live"):
            tool.reopen(broken, "o.go")


class TestTheFileLayout:
    def test_it_reproduces_the_committed_seed_byte_for_byte(self):
        text = SEED.read_text(encoding="utf-8")

        assert tool.dumps(json.loads(text)) == text

    def test_a_retirement_stays_in_the_same_layout(self):
        retired = retire(small())

        assert tool.dumps(json.loads(tool.dumps(retired))) == tool.dumps(retired)

    def test_categories_and_entities_are_one_per_line(self):
        text = tool.dumps(small())

        assert '    {"key": "o.keep", "name": "kept", "gloss": "stays"},' in text
        assert text.endswith("}\n")

    def test_the_committed_retirement_undoes_exactly(self):
        document = json.loads(SEED.read_text(encoding="utf-8"))
        if not document.get("retired"):
            pytest.skip("nothing is retired in the committed seed")
        entry = document["retired"][0]

        reopened = tool.reopen(document, entry["key"])
        again = tool.retire(
            reopened,
            entry["key"],
            reason=entry["reason"],
            reopen_when=entry["reopen_when"],
            on=entry["retired_on"],
        )

        assert again == document


class TestTheProviderIgnoresWhatIsRetired:
    def write(self, tmp_path: Path, document: dict) -> Path:
        path = tmp_path / "seed.json"
        path.write_text(tool.dumps(document), encoding="utf-8")
        return path

    def test_a_retired_category_is_never_loaded(self, tmp_path):
        provider = CuratedJSONProvider(self.write(tmp_path, retire(small())), now=NOW)

        bundle = provider.load()

        assert [c.name for c in bundle.categories] == ["kept"]
        assert {e.name for e in bundle.entities} == {"both", "stays"}

    def test_it_is_reported_for_anyone_who_wants_to_know(self, tmp_path):
        provider = CuratedJSONProvider(self.write(tmp_path, retire(small())), now=NOW)

        assert [e["key"] for e in provider.retired_categories()] == ["o.go"]

    def test_a_file_with_nothing_retired_reports_none(self, tmp_path):
        provider = CuratedJSONProvider(self.write(tmp_path, small()), now=NOW)

        assert provider.retired_categories() == ()


class TestTheCommandLine:
    def seed(self, tmp_path: Path) -> Path:
        path = tmp_path / "seed.json"
        path.write_text(tool.dumps(small()), encoding="utf-8")
        return path

    def test_retire_writes_the_file_and_prints_the_result(self, tmp_path, capsys):
        path = self.seed(tmp_path)

        code = tool.main(
            ["--seed", str(path), "retire", "o.go", "--reason", WHY,
             "--reopen-when", WHEN, "--on", "2026-09-30"]
        )

        assert code == 0
        assert "1 live categories" in capsys.readouterr().out
        assert json.loads(path.read_text())["retired"][0]["key"] == "o.go"

    def test_reopen_undoes_it(self, tmp_path, capsys):
        path = self.seed(tmp_path)
        tool.main(["--seed", str(path), "retire", "o.go", "--reason", WHY,
                   "--reopen-when", WHEN, "--on", "2026-09-30"])

        code = tool.main(["--seed", str(path), "reopen", "o.go", "--on", "2026-10-01"])

        assert code == 0
        assert "retired" not in json.loads(path.read_text())
        capsys.readouterr()

    def test_list_shows_the_reason_and_the_way_back(self, tmp_path, capsys):
        path = self.seed(tmp_path)
        tool.main(["--seed", str(path), "retire", "o.go", "--reason", WHY,
                   "--reopen-when", WHEN, "--on", "2026-09-30"])
        capsys.readouterr()

        tool.main(["--seed", str(path), "list"])

        out = capsys.readouterr().out
        assert WHY in out and f"reopen when: {WHEN}" in out

    def test_a_refusal_exits_two_and_leaves_the_file_alone(self, tmp_path, capsys):
        path = self.seed(tmp_path)
        before = path.read_text()

        code = tool.main(["--seed", str(path), "reopen", "o.go"])

        assert code == 2
        assert "not retired" in capsys.readouterr().err
        assert path.read_text() == before

    def test_a_missing_seed_exits_two(self, tmp_path, capsys):
        code = tool.main(["--seed", str(tmp_path / "absent.json"), "list"])

        assert code == 2
