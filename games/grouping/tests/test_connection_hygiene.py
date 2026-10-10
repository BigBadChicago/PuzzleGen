"""No tool in this distribution may leave an sqlite connection open.

The platform has the same test for its own tools, and deliberately does not
cover these: that suite has to pass with no game installed. The rule is the
platform's, the enforcement is each distribution's, which is the shape every
shared rule takes once games ship separately.

Python 3.13 and later emit a ResourceWarning when an unclosed connection is
garbage collected, and warnings are errors here. The failure does not land on
the leaking test: it lands on whichever unrelated test the collector happens to
interrupt, so these count connections rather than waiting for the collector.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys

import pytest

from paths import LEXICON_SEEDS, PLATFORM_TOOLS, TOOLS, has_repository

pytestmark = pytest.mark.skipif(
    not has_repository(),
    reason="needs the platform checkout for a lexicon to build a graph from",
)

MINI_LEXICON = LEXICON_SEEDS / "wordnet-mini.lexicon.json"


def load_tool(name: str, directory=None):
    home = directory or TOOLS
    spec = importlib.util.spec_from_file_location(
        f"hygiene_{name}", home / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Tracked(sqlite3.Connection):
    """A connection that reports whether it was ever closed."""

    closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.fixture
def connections(monkeypatch) -> list[Tracked]:
    """Every connection opened during a test, in order."""
    opened: list[Tracked] = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        kwargs["factory"] = Tracked
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return opened


def unclosed(opened: list[Tracked]) -> int:
    return sum(0 if c.closed else 1 for c in opened)


@pytest.fixture
def built(tmp_path, connections):
    """A snapshot to run the tools against, built by the platform's tool."""
    build_snapshot = load_tool("build_snapshot", PLATFORM_TOOLS)
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


class TestProposer:
    def test_a_proposal_run_closes_its_store(self, built, tmp_path, connections):
        propose_overlay = load_tool("propose_overlay")
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

    def test_a_dry_run_closes_its_store(self, built, connections):
        propose_overlay = load_tool("propose_overlay")
        connections.clear()
        propose_overlay.main(["--db", str(built), "--batch", "b1", "--dry-run"])
        assert connections
        assert unclosed(connections) == 0

    def test_a_proposer_that_fails_mid_run_still_closes_its_store(
        self, built, connections, monkeypatch
    ):
        propose_overlay = load_tool("propose_overlay")
        connections.clear()

        def explode(*args, **kwargs):
            raise RuntimeError("scoring failed")

        monkeypatch.setattr(propose_overlay, "propose", explode)
        with pytest.raises(RuntimeError):
            propose_overlay.main(["--db", str(built), "--batch", "b1"])
        assert connections
        assert unclosed(connections) == 0


class TestCoverageTools:
    def test_measuring_coverage_closes_its_store(self, built, connections):
        measure_coverage = load_tool("measure_coverage")
        connections.clear()
        measure_coverage.main(["--db", str(built), "--difficulty", "medium"])
        assert connections
        assert unclosed(connections) == 0
