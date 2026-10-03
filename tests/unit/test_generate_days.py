"""The end to end driver, against a small world built to make boards.

The world is four peer parents under one domain, each with six children, and
an overlay category of five words spread across them, which is exactly what a
size 5 board needs and nothing a size 6 board can use. So one size generates
and the others cannot, which is the situation the real content is in.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from puzzlegen.content.snapshots import ActivationPolicy, SnapshotBuilder
from puzzlegen.games.grouping.descriptor import GROUP_SIZES, group_size_for
from puzzlegen.graph import GraphRepositories, SqliteDocumentStore
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider

from ..conftest import NOW

ROOT = Path(__file__).resolve().parents[2]


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "tool_generate_days", ROOT / "tools" / "generate_days.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()

PARENTS = ("wagons", "boats", "planes", "trains")


def kids(parent: str) -> list[str]:
    return [f"{parent[:2]}{n}" for n in range(1, 7)]


def write_world(tmp_path: Path) -> tuple[Path, list[str]]:
    categories = [{"key": "c.domain", "name": "domain"}]
    entities = []
    words: list[str] = []
    for parent in PARENTS:
        categories.append({"key": f"c.{parent}", "name": parent, "parents": ["c.domain"]})
        for kid in kids(parent):
            categories.append({"key": f"c.{kid}", "name": kid, "parents": [f"c.{parent}"]})
            entities.append(
                {"key": f"e.{kid}", "name": kid, "categories": [f"c.{kid}"], "confidence": 0.95}
            )
            words.append(kid)
    lexical = tmp_path / "lexical.json"
    lexical.write_text(
        json.dumps(
            {
                "curated_schema": 1,
                "version": "1",
                "updated": "2026-09-26",
                "categories": categories,
                "entities": entities,
            }
        ),
        encoding="utf-8",
    )
    hidden = [kids("wagons")[0], kids("wagons")[1], kids("boats")[0], kids("planes")[0], kids("trains")[0]]
    overlay = tmp_path / "overlay.json"
    overlay.write_text(
        json.dumps(
            {
                "curated_schema": 1,
                "version": "1",
                "updated": "2026-09-26",
                "categories": [{"key": "o.axis", "name": "shared axis"}],
                "entities": [
                    {"key": f"e.{w}", "name": w, "categories": ["o.axis"], "confidence": 0.95}
                    for w in hidden
                ],
            }
        ),
        encoding="utf-8",
    )

    db = tmp_path / "graph.sqlite"
    repos = GraphRepositories(SqliteDocumentStore(db))
    try:
        SnapshotBuilder(repos, now=NOW).build(
            "test-snapshot",
            providers=[
                (
                    CuratedJSONProvider(lexical, name="lexical", now=NOW),
                    {"taxonomy": "wordnet", "entity_identity": "lemma"},
                ),
                (
                    CuratedJSONProvider(overlay, name="overlay-seed", now=NOW),
                    {"taxonomy": "overlay", "entity_identity": "lemma"},
                ),
            ],
            frequency=TableFrequencyProvider(
                {w: 4.5 for w in words}, name="wordfreq", version="1", retrieved_at=NOW
            ),
            embeddings=DevHashEmbeddingProvider(now=NOW),
            policy=ActivationPolicy(minimum_confidence=0.75),
        )
    finally:
        repos.close()
    return db, hidden


def day_of_size(size: int) -> dt.date:
    day = dt.date(2026, 10, 1)
    for _ in range(400):
        if group_size_for(day.isoformat()) == size:
            return day
        day += dt.timedelta(days=1)
    raise AssertionError(f"no day of size {size}")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return write_world(tmp_path_factory.mktemp("world"))


class TestARealDay:
    def test_the_supported_size_generates_a_board(self, world):
        db, _ = world

        [result] = tool.run(db, start=day_of_size(5), days=1, now=NOW)

        assert result.generated, result.reason
        assert result.group_size == 5

    def test_it_verifies_as_unique_and_complete(self, world):
        db, _ = world

        [result] = tool.run(db, start=day_of_size(5), days=1, now=NOW)

        assert result.solutions == 1
        assert result.completeness == "COMPLETE"
        assert result.states and result.states > 0

    def test_it_scores(self, world):
        db, _ = world

        [result] = tool.run(db, start=day_of_size(5), days=1, now=NOW)

        assert 0.0 <= result.difficulty <= 1.0
        assert result.measured_band

    def test_it_names_the_hidden_axis_and_the_four_groups(self, world):
        db, _ = world

        [result] = tool.run(db, start=day_of_size(5), days=1, now=NOW)

        assert result.hidden == "shared axis"
        assert sorted(result.visible) == sorted(PARENTS)

    def test_an_unsupported_size_fails_and_says_why(self, world):
        db, _ = world

        [result] = tool.run(db, start=day_of_size(6), days=1, now=NOW)

        assert not result.generated
        assert result.group_size == 6
        assert result.reason.strip()


class TestDescribingAFailure:
    """Built from the record's real tally type.

    The first version read a tally as though it were one reason and raised on
    the first real day that recorded one. Stubs hid it, so these use the real
    ``RejectionTally`` and only stub the parts that need a whole pipeline.
    """

    def outcome(self, *, tallies=(), content=None, failure="no candidates"):
        from puzzlegen.engine.records import RejectionTally

        return SimpleNamespace(
            trace=SimpleNamespace(
                day_key="2026-10-05",
                failure_reason=failure,
                candidates_rejected=tuple(
                    RejectionTally(stage=stage, reasons=reasons) for stage, reasons in tallies
                ),
                content_rejections=content or {},
            )
        )

    @pytest.fixture(autouse=True)
    def no_game_state(self, monkeypatch):
        monkeypatch.setattr(tool, "last_rejections", lambda: {})

    def test_the_stated_reason_comes_first(self):
        text = tool.describe_failure(self.outcome(tallies=[("assembly", {"x": 1})]))

        assert text.startswith("no candidates")

    def test_a_late_stage_is_shown_before_the_content_counts(self):
        """The failure that cost an afternoon: 126 solutions behind 2844 words."""
        text = tool.describe_failure(
            self.outcome(
                tallies=[("assembly", {"MULTIPLE_SOLUTIONS: 126 distinct": 1})],
                content={"WORD_FREQUENCY_MISMATCH": 2844},
            )
        )

        assert text.index("MULTIPLE_SOLUTIONS") < text.index("WORD_FREQUENCY_MISMATCH")

    def test_the_stage_is_named(self):
        text = tool.describe_failure(self.outcome(tallies=[("assembly", {"thin": 2})]))

        assert "assembly: thin (2)" in text

    def test_the_commonest_stage_reasons_lead_and_three_are_shown(self):
        text = tool.describe_failure(
            self.outcome(tallies=[("assembly", {f"r{n}": n + 1 for n in range(6)})])
        )

        assert text.index("r5") < text.index("r4") < text.index("r3")
        assert "r2" not in text

    def test_content_counts_are_labelled(self):
        text = tool.describe_failure(self.outcome(content={"NO_SECOND_AXIS": 24}))

        assert "content: NO_SECOND_AXIS 24" in text

    def test_the_games_own_refusals_are_included(self, monkeypatch):
        # The game keys refusals by size, as the real generator does.
        monkeypatch.setattr(
            tool,
            "last_rejections",
            lambda: {"size 5: not_disjoint": 90, "size 5: thin": 2},
        )

        text = tool.describe_failure(self.outcome())

        assert "game: size 5: not_disjoint 90, size 5: thin 2" in text

    def test_nothing_recorded_leaves_just_the_reason(self):
        assert tool.describe_failure(self.outcome()) == "no candidates"

    def test_a_missing_reason_still_reads(self):
        assert "no reason recorded" in tool.describe_failure(self.outcome(failure=None))

    def test_a_real_failing_day_describes_itself(self, world):
        db, _ = world
        [result] = tool.run(db, start=day_of_size(6), days=1, now=NOW)

        assert not result.generated
        assert result.reason.strip()


class TestARunOfDays:
    def test_a_run_covers_consecutive_days(self, world):
        db, _ = world
        start = day_of_size(5)

        results = tool.run(db, start=start, days=3, now=NOW)

        assert [r.day for r in results] == [
            (start + dt.timedelta(days=i)).isoformat() for i in range(3)
        ]

    def test_the_run_writes_nothing_to_the_snapshot(self, world):
        db, _ = world
        before = db.stat().st_size

        tool.run(db, start=day_of_size(5), days=2, now=NOW)

        assert db.stat().st_size == before


class TestBySize:
    def test_every_size_the_game_can_draw_is_listed(self):
        counts = tool.by_size([])

        assert set(counts) == set(GROUP_SIZES)

    def test_it_counts_days_and_boards(self):
        results = [
            tool.DayResult(day="a", group_size=5, generated=True),
            tool.DayResult(day="b", group_size=5, generated=False),
            tool.DayResult(day="c", group_size=6, generated=False),
        ]

        counts = tool.by_size(results)

        assert counts[5] == (1, 2)
        assert counts[6] == (0, 1)
        assert counts[9] == (0, 0)


class TestSnapshotChoice:
    def test_the_newest_sealed_snapshot_is_the_default(self, world):
        db, _ = world
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            assert tool.pick_snapshot(repos, None).label == "test-snapshot"
        finally:
            repos.close()

    def test_an_unknown_label_is_refused(self, world):
        db, _ = world
        repos = GraphRepositories(SqliteDocumentStore(db))
        try:
            with pytest.raises(tool.SnapshotError, match="no snapshot labelled"):
                tool.pick_snapshot(repos, "nope")
        finally:
            repos.close()

    def test_an_empty_database_is_refused(self, tmp_path):
        repos = GraphRepositories(SqliteDocumentStore(tmp_path / "empty.sqlite"))
        try:
            with pytest.raises(tool.SnapshotError, match="no sealed snapshot"):
                tool.pick_snapshot(repos, None)
        finally:
            repos.close()


class TestTheReport:
    def results(self, world):
        db, _ = world
        return tool.run(db, start=day_of_size(5), days=6, now=NOW)

    def test_a_board_prints_its_proof(self, world):
        text = tool.render(self.results(world))

        assert "BOARD  unique, COMPLETE" in text
        assert "hidden: shared axis" in text

    def test_the_summary_says_how_many_sizes_are_served(self, world):
        text = tool.render(self.results(world))

        assert "days generated a board" in text
        # "drew" and "served" are counted separately now. The old single
        # "boards" column was filled from the size each day drew rather than
        # the size it was served, which reported boards at sizes the content
        # could not build.
        assert "size  drew  served" in text

    def test_failures_are_grouped_by_reason(self, world):
        text = tool.render(self.results(world))

        assert "why days failed:" in text


class TestTheCommandLine:
    def test_a_fully_generated_run_exits_zero(self, world, capsys):
        db, _ = world

        code = tool.main(
            ["--db", str(db), "--start", day_of_size(5).isoformat(), "--days", "1",
             "--now", NOW.isoformat()]
        )

        assert code == 0
        assert "BOARD" in capsys.readouterr().out

    def test_a_run_with_a_failed_day_exits_one(self, world, capsys):
        db, _ = world

        code = tool.main(
            ["--db", str(db), "--start", day_of_size(6).isoformat(), "--days", "1",
             "--now", NOW.isoformat()]
        )

        assert code == 1
        capsys.readouterr()

    def test_a_missing_database_is_an_argument_error(self, tmp_path):
        with pytest.raises(SystemExit):
            tool.main(["--db", str(tmp_path / "absent.sqlite")])

    def test_zero_days_is_an_argument_error(self, world):
        db, _ = world
        with pytest.raises(SystemExit):
            tool.main(["--db", str(db), "--days", "0"])

    def test_a_naive_now_is_refused(self, world):
        db, _ = world
        with pytest.raises(SystemExit):
            tool.main(["--db", str(db), "--now", "2026-09-26T12:00:00"])

    def test_an_empty_database_exits_two(self, tmp_path, capsys):
        path = tmp_path / "empty.sqlite"
        GraphRepositories(SqliteDocumentStore(path)).close()

        code = tool.main(["--db", str(path), "--days", "1"])

        assert code == 2
        assert "no sealed snapshot" in capsys.readouterr().err

    def test_json_carries_every_day(self, world, tmp_path, capsys):
        db, _ = world
        out = tmp_path / "days.json"

        tool.main(
            ["--db", str(db), "--start", day_of_size(5).isoformat(), "--days", "2",
             "--now", NOW.isoformat(), "--json", str(out)]
        )
        capsys.readouterr()

        document = json.loads(out.read_text())
        assert len(document) == 2
        assert {"day", "group_size", "generated", "solutions"} <= set(document[0])


class TestTheDaysOwnSizeLeadsTheReason:
    """A day tries its own size first, then the rest. The fallbacks failing is
    expected, so the day's own size has to lead the reason or it is buried."""

    def outcome(self, day_key, game):
        return SimpleNamespace(
            trace=SimpleNamespace(
                day_key=day_key,
                failure_reason="no candidates",
                candidates_rejected=(),
                content_rejections={},
            )
        )

    def test_the_days_size_is_shown_even_when_a_fallback_has_more(self, monkeypatch):
        day = "2026-10-05"
        own = group_size_for(day)
        other = next(s for s in GROUP_SIZES if s != own)
        monkeypatch.setattr(
            tool,
            "last_rejections",
            lambda: {
                f"size {own}: not_disjoint": 5,
                f"size {other}: hidden_word_on_no_visible_group": 900,
            },
        )

        text = tool.describe_failure(self.outcome(day, None))

        assert f"size {own}: not_disjoint" in text
        own_pos = text.index(f"size {own}:")
        other_pos = text.index(f"size {other}:")
        assert own_pos < other_pos
