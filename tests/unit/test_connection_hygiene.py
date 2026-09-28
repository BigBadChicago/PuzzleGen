"""No tool may leave an sqlite connection open when its command ends.

Python 3.13 and later emit a ResourceWarning when an unclosed connection is
garbage collected, and this repository turns warnings into errors. The failure
does not land on the leaking test: it lands on whichever unrelated test the
collector happens to interrupt, which made a leak in ``tools/`` show up as five
failures in ``test_rng`` and ``test_solvers``. These tests move the failure to
the command that caused it, on any Python version, by counting connections
rather than waiting for the collector.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
MINI_LEXICON = ROOT / "content" / "seeds" / "wordnet-mini.lexicon.json"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"hygiene_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_snapshot = load_tool("build_snapshot")
propose_overlay = load_tool("propose_overlay")
review_tool = load_tool("review")


class Tracked(sqlite3.Connection):
    """A connection that reports whether it was ever closed."""

    closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.fixture
def connections(monkeypatch) -> list[Tracked]:
    opened: list[Tracked] = []
    real = sqlite3.connect

    def connect(*args, **kwargs):
        kwargs.setdefault("factory", Tracked)
        connection = real(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return opened


def unclosed(connections: list[Tracked]) -> int:
    return sum(1 for c in connections if not c.closed)


@pytest.fixture
def built(tmp_path, connections) -> Path:
    db = tmp_path / "graph.sqlite"
    build_snapshot.main(
        [
            "--lexicon",
            str(MINI_LEXICON),
            "--db",
            str(db),
            "--label",
            "hygiene",
            "--dev-embeddings",
        ]
    )
    return db


class TestTheTrackerItself:
    def test_it_counts_a_connection_that_is_never_closed(self, connections):
        leaked = sqlite3.connect(":memory:")
        try:
            assert len(connections) == 1
            assert unclosed(connections) == 1
        finally:
            leaked.close()

    def test_it_does_not_count_one_that_is_closed(self, connections):
        sqlite3.connect(":memory:").close()
        assert len(connections) == 1
        assert unclosed(connections) == 0


class TestBuildSnapshot:
    def test_a_build_closes_its_store(self, built, connections):
        assert connections, "the build opened no connection, so this proves nothing"
        assert unclosed(connections) == 0

    def test_a_build_that_fails_still_closes_its_store(
        self, tmp_path, connections, monkeypatch
    ):
        def explode(*args, **kwargs):
            raise RuntimeError("provider failed")

        monkeypatch.setattr(build_snapshot, "providers", explode)
        with pytest.raises(RuntimeError):
            build_snapshot.main(
                [
                    "--lexicon",
                    str(MINI_LEXICON),
                    "--db",
                    str(tmp_path / "graph.sqlite"),
                    "--label",
                    "hygiene",
                ]
            )
        # The store is already open when providers() runs, so this is the
        # failure path the try/finally exists for.
        assert connections
        assert unclosed(connections) == 0


class TestProposer:
    def test_a_proposal_run_closes_its_store(self, built, tmp_path, connections):
        connections.clear()
        propose_overlay.main(
            [
                "--db",
                str(built),
                "--batch",
                "b1",
                "--manifest",
                str(tmp_path / "b1.json"),
            ]
        )
        assert connections
        assert unclosed(connections) == 0

    def test_a_dry_run_closes_its_store(self, built, tmp_path, connections):
        connections.clear()
        propose_overlay.main(
            ["--db", str(built), "--batch", "b1", "--dry-run"]
        )
        assert connections
        assert unclosed(connections) == 0

    def test_a_proposer_that_fails_mid_run_still_closes_its_store(
        self, built, connections, monkeypatch
    ):
        connections.clear()

        def explode(*args, **kwargs):
            raise RuntimeError("scoring failed")

        monkeypatch.setattr(propose_overlay, "propose", explode)
        with pytest.raises(RuntimeError):
            propose_overlay.main(["--db", str(built), "--batch", "b1"])
        assert connections
        assert unclosed(connections) == 0


class TestReviewTool:
    @pytest.fixture
    def candidate(self, built, tmp_path, connections) -> str:
        manifest = tmp_path / "b1.json"
        propose_overlay.main(
            ["--db", str(built), "--batch", "b1", "--manifest", str(manifest)]
        )
        rows = json.loads(manifest.read_text())["candidates"]
        if rows:
            return rows[0]["subject_ref"]
        pytest.skip("the mini lexicon proposes nothing to review")

    def test_the_queue_closes_its_store(self, built, connections):
        connections.clear()
        review_tool.main(["--db", str(built), "queue"])
        assert connections
        assert unclosed(connections) == 0

    def test_an_empty_queue_closes_its_store(self, built, connections):
        """The early return in ``cmd_queue`` sits inside the ``with`` block."""
        connections.clear()
        code = review_tool.main(["--db", str(built), "queue"])
        assert code == 0
        assert unclosed(connections) == 0

    def test_history_closes_its_store_even_when_empty(self, built, connections):
        connections.clear()
        ref = "relationship:does-not-exist"
        review_tool.main(["--db", str(built), "history", ref])
        assert connections
        assert unclosed(connections) == 0

    def test_a_refused_decision_closes_its_store(self, built, connections):
        connections.clear()
        code = review_tool.main(
            [
                "--db",
                str(built),
                "accept",
                "relationship:does-not-exist",
                "--reviewer",
                "ada",
                "--batch",
                "b1",
            ]
        )
        assert code == 1
        assert connections
        assert unclosed(connections) == 0

    def test_a_missing_reviewer_opens_nothing_at_all(
        self, built, connections, monkeypatch
    ):
        """Refused before the store is touched, so there is nothing to close."""
        monkeypatch.delenv(review_tool.REVIEWER_ENV, raising=False)
        connections.clear()
        code = review_tool.main(
            [
                "--db",
                str(built),
                "accept",
                "relationship:x",
                "--batch",
                "b1",
            ]
        )
        assert code == 2
        assert connections == []
