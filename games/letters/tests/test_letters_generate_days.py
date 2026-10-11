"""The end to end driver, against a small world built to make boards.

``world`` is one category whose name the letters of its own members can spell,
with twelve members so every board size can be served. ``unspellable`` is the same
world renamed: its category is ``jazzwax``, and the letters j, w, x and z
appear in no member, so five blanks would be needed where four is the most any
band allows and no size can be served. A failing day is worth a world of
its own because the report a curator reads is the failure report -- the counts
that say whether a day was lost to a naming problem or a content gap -- and a
report nobody has seen fail is a report nobody has tested.

The tool is loaded by path rather than imported, because ``tools`` is not a
package: it holds scripts that are run, and making it importable would make
the scripts part of the distribution's API.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from puzzlegen.content.snapshots import ActivationPolicy, SnapshotBuilder
from puzzlegen.core.types import DifficultyBand
from puzzlegen.graph import GraphRepositories, SqliteDocumentStore
from puzzlegen.providers.curated import CuratedJSONProvider
from puzzlegen.providers.embeddings import DevHashEmbeddingProvider
from puzzlegen.providers.frequency import TableFrequencyProvider
from puzzlegen.testing import NOW
from puzzlegen_letters.descriptor import BOARD_SIZES, board_size_for

from paths import GAME as ROOT


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "tool_letters_generate_days", ROOT / "tools" / "generate_days.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = load_tool()

#: Twelve members holding between them the letters of ``fish`` but not its ``f``,
#: so a board here always has at least one forced blank -- which is the normal
#: case on real content and the one worth testing against.
MEMBERS = (
    "salmon",
    "trout",
    "herring",
    "bass",
    "halibut",
    "carp",
    "sole",
    "perch",
    "mullet",
    "anchovy",
    "sardine",
    "mackerel",
)


def write_world(tmp_path: Path, *, category: str = "fish") -> Path:
    curated = tmp_path / "letters.json"
    curated.write_text(
        json.dumps(
            {
                "curated_schema": 1,
                "version": "1",
                "updated": "2026-10-01",
                "categories": [{"key": "l.one", "name": category}],
                "entities": [
                    {
                        "key": f"e.{name}",
                        "name": name,
                        "categories": ["l.one"],
                        "confidence": 0.95,
                    }
                    for name in MEMBERS
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
                    CuratedJSONProvider(curated, name="letters", now=NOW),
                    {"taxonomy": "letters", "entity_identity": "lemma"},
                ),
            ],
            frequency=TableFrequencyProvider(
                {name: 4.5 for name in MEMBERS},
                name="wordfreq",
                version="1",
                retrieved_at=NOW,
            ),
            embeddings=DevHashEmbeddingProvider(now=NOW),
            policy=ActivationPolicy(minimum_confidence=0.75),
        )
    finally:
        repos.close()
    return db


def day_of_size(size: int) -> dt.date:
    day = dt.date(2026, 10, 1)
    for _ in range(400):
        if board_size_for(day.isoformat()) == size:
            return day
        day += dt.timedelta(days=1)
    raise AssertionError(f"no day draws {size} tiles")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return write_world(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def unspellable(tmp_path_factory):
    return write_world(tmp_path_factory.mktemp("unspellable"), category="jazzwax")


class TestARealDay:
    def test_the_day_generates_a_board(self, world):
        [result] = tool.run(world, start=day_of_size(6), days=1, now=NOW)

        assert result.generated, result.reason
        assert result.board_size == 6

    def test_it_verifies_as_one_reading(self, world):
        [result] = tool.run(world, start=day_of_size(6), days=1, now=NOW)

        assert result.readings == 1
        assert result.completeness == "COMPLETE"
        assert result.words_examined and result.words_examined > 0

    def test_it_reports_the_board_a_curator_would_want_to_see(self, world):
        [result] = tool.run(world, start=day_of_size(6), days=1, now=NOW)

        assert result.answer == "fish"
        assert result.answer_length == 4
        assert result.blanks and result.blanks >= 2
        assert result.marked_tiles and result.marked_tiles >= 1
        assert len(result.tiles) == 6

    def test_it_scores_and_bands(self, world):
        [result] = tool.run(world, start=day_of_size(6), days=1, now=NOW)

        assert 0.0 <= result.difficulty <= 1.0
        assert result.measured_band

    def test_the_same_day_twice_is_the_same_board(self, world):
        """The whole reproducibility claim, run through the real pipeline."""
        first = tool.run(world, start=day_of_size(6), days=1, now=NOW)
        second = tool.run(world, start=day_of_size(6), days=1, now=NOW)

        assert first == second

    def test_a_size_the_content_cannot_serve_falls_back(self, world):
        """The day keeps its board; only the size gives way.

        This world has twelve members, so it can serve any size, and the
        fallback is exercised by asking the largest and reading back what was
        served. A day lost because its own size was unservable while a smaller
        board sat ready is the failure the fallback exists for.
        """
        [result] = tool.run(world, start=day_of_size(12), days=1, now=NOW)

        assert result.generated, result.reason
        assert result.drawn_size == 12
        assert result.board_size in BOARD_SIZES


class TestADayThatFails:
    def test_it_fails_rather_than_serving_a_board_it_cannot_spell(self, unspellable):
        [result] = tool.run(unspellable, start=day_of_size(6), days=1, now=NOW)

        assert not result.generated
        assert result.answer is None

    def test_it_says_why(self, unspellable):
        """A day that comes back empty with no reason is a day nobody can
        act on."""
        [result] = tool.run(unspellable, start=day_of_size(6), days=1, now=NOW)

        assert result.reason


class TestTheReport:
    def test_the_size_table_counts_draws_and_boards_apart(self):
        results = [
            tool.DayResult(day="2026-10-01", board_size=6, generated=True, drawn_size=6),
            tool.DayResult(day="2026-10-02", board_size=6, generated=True, drawn_size=12),
            tool.DayResult(day="2026-10-03", board_size=8, generated=False, drawn_size=8),
        ]

        table = tool.by_size(results)

        assert table[6] == (2, 1)
        assert table[12] == (0, 1)
        assert table[8] == (0, 1)

    def test_every_supported_size_has_a_row_even_at_zero(self):
        """A size missing from the table reads as a size nobody asked about."""
        assert set(tool.by_size([])) == set(BOARD_SIZES)

    def test_a_served_day_is_rendered_with_its_answer_and_tiles(self):
        results = [
            tool.DayResult(
                day="2026-10-01",
                board_size=6,
                generated=True,
                drawn_size=6,
                answer="fish",
                answer_length=4,
                blanks=2,
                marked_tiles=2,
                manufactured_blanks=1,
                readings=1,
                completeness="COMPLETE",
                words_examined=9,
                difficulty=0.37,
                measured_band="EASY",
                on_target=True,
                tiles=("bass", "carp"),
            )
        ]

        report = tool.render(results)

        assert "one reading" in report
        assert "answer: fish (4 letters, 2 marks, 2 blanks of which 1 manufactured)" in report
        assert "bass, carp" in report
        assert "1 of 1 days generated a board" in report
        assert "OFF TARGET" not in report

    def test_a_fallback_is_shown_rather_than_corrected_away(self):
        results = [
            tool.DayResult(
                day="2026-10-01",
                board_size=6,
                generated=True,
                drawn_size=12,
                answer="fish",
                answer_length=4,
                blanks=2,
                marked_tiles=2,
                manufactured_blanks=1,
                readings=1,
                completeness="COMPLETE",
                words_examined=9,
                difficulty=0.37,
                measured_band="EASY",
                on_target=True,
                tiles=("bass",),
            )
        ]

        assert "(drew 12)" in tool.render(results)

    def test_an_off_target_board_says_so(self):
        """A board served outside the band it was asked for is the thing the
        report exists to make visible."""
        results = [
            tool.DayResult(
                day="2026-10-01",
                board_size=6,
                generated=True,
                drawn_size=6,
                answer="fish",
                answer_length=4,
                blanks=2,
                marked_tiles=2,
                manufactured_blanks=1,
                readings=1,
                completeness="COMPLETE",
                words_examined=9,
                difficulty=0.7,
                measured_band="HARD",
                on_target=False,
                tiles=("bass",),
            )
        ]

        assert "OFF TARGET" in tool.render(results)

    def test_the_answers_served_are_counted(self):
        """Day to day variety, which is the one property a single day cannot
        show. Two days on the same answer is what a flat candidate order
        produced and what the answer-first draw exists to avoid."""
        results = [
            tool.DayResult(
                day=f"2026-10-0{n}",
                board_size=6,
                generated=True,
                drawn_size=6,
                answer=answer,
                readings=1,
            )
            for n, answer in enumerate(("fish", "fish", "gem"), start=1)
        ]

        report = tool.render(results)

        assert "2 distinct answers: fish 2, gem 1" in report

    def test_failed_days_are_grouped_by_reason(self):
        results = [
            tool.DayResult(
                day="2026-10-01",
                board_size=6,
                generated=False,
                reason="no categories at 6 tiles; 3 refused",
            ),
            tool.DayResult(
                day="2026-10-02",
                board_size=6,
                generated=False,
                reason="no categories at 6 tiles; 4 refused",
            ),
        ]

        report = tool.render(results)

        assert "why days failed:" in report
        assert "2  no categories at 6 tiles" in report


class TestPickingASnapshot:
    def test_a_label_nothing_matches_is_an_error_rather_than_a_default(self, world):
        with pytest.raises(tool.SnapshotError, match="no snapshot labelled"):
            tool.run(world, start=day_of_size(6), days=1, label="nothing", now=NOW)

    def test_the_named_snapshot_is_used(self, world):
        [result] = tool.run(
            world, start=day_of_size(6), days=1, label="test-snapshot", now=NOW
        )

        assert result.generated, result.reason


class TestStrictMode:
    def test_strict_either_serves_the_band_asked_for_or_nothing(self, world):
        """What ``--strict`` is for. Without it the engine settles for a board
        outside the band after its in-band attempts run out, which is the
        right behaviour for a live day and the wrong one for measuring: a run
        that reports thirty boards says nothing about whether thirty of them
        were the difficulty anybody asked for."""
        [strict] = tool.run(
            world,
            start=day_of_size(6),
            days=1,
            difficulty=DifficultyBand.MEDIUM,
            strict=True,
            now=NOW,
        )

        if strict.generated:
            assert strict.on_target
            assert strict.measured_band == DifficultyBand.MEDIUM.name
        else:
            assert strict.reason

    def test_without_it_a_day_may_be_served_off_target(self, world):
        [lenient] = tool.run(
            world,
            start=day_of_size(6),
            days=1,
            difficulty=DifficultyBand.MEDIUM,
            strict=False,
            now=NOW,
        )

        assert lenient.generated, lenient.reason
        assert lenient.on_target is not None
