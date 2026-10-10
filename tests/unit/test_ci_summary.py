"""The CI summary is what a session reads instead of a build log, so a wrong
line in it is a wrong decision. These pin the cases that matter: a red suite
must never render green, a suite that died before pytest's closing line must
say so, and a run of days that all share one board must show it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script():
    spec = importlib.util.spec_from_file_location("script_ci_summary", SCRIPTS / "ci_summary.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ci_summary = load_script()


def coverage(entities: int, sizes: dict[int, int]) -> dict:
    return {
        "summary": {
            "difficulty": "medium",
            "entities": entities,
            "entities_usable_as_tiles": entities // 4,
            "lexical_categories": 10,
            "generatable": True,
            "sizes": [{"group_size": k, "feasible_hidden_groups": v} for k, v in sizes.items()],
        },
        "targets": [],
    }


def day(hidden: str, generated: bool = True, size: int = 5, reason: str = "") -> dict:
    return {"day": "2026-10-01", "generated": generated, "hidden": hidden if generated else "",
            "group_size": size, "on_target": generated, "reason": reason}


class TestSuiteLines:
    def test_green_line(self):
        result, failures = ci_summary.summarise_suite("....\n12 passed, 2 skipped in 1.20s\n")
        assert result == "12 passed, 2 skipped in 1.20s"
        assert failures == []
        assert ci_summary.suite_ok(result)

    def test_ruled_line_and_failures(self):
        log = (
            "FAILED tests/unit/test_a.py::test_x - AssertionError\n"
            "ERROR tests/unit/test_b.py::test_y\n"
            "==== 1 failed, 9 passed, 1 error in 2.00s ====\n"
        )
        result, failures = ci_summary.summarise_suite(log)
        assert result == "1 failed, 9 passed, 1 error in 2.00s"
        assert failures == ["tests/unit/test_a.py::test_x", "tests/unit/test_b.py::test_y"]
        assert not ci_summary.suite_ok(result)

    def test_collection_errors_are_red(self):
        assert not ci_summary.suite_ok("2 errors in 0.50s")

    def test_all_skipped_is_not_green(self):
        assert not ci_summary.suite_ok("5 skipped in 0.10s")

    def test_unfinished_suite_is_red(self):
        result, _ = ci_summary.summarise_suite("Traceback (most recent call last):\nImportError\n")
        assert "did not finish" in result
        assert not ci_summary.suite_ok(result)


class TestRenderTests:
    def test_one_red_suite_makes_the_run_red(self, tmp_path):
        good = tmp_path / "platform.log"
        good.write_text("3 passed in 0.1s\n")
        bad = tmp_path / "grouping.log"
        bad.write_text("FAILED tests/test_g.py::test_z\n1 failed, 2 passed in 0.1s\n")
        out = ci_summary.render_tests([good, bad])
        assert "**red**" in out
        assert "| platform | ok: 3 passed in 0.1s |" in out
        assert "| grouping | FAIL: 1 failed, 2 passed in 0.1s |" in out
        assert "`grouping: tests/test_g.py::test_z`" in out

    def test_failure_list_is_capped(self, tmp_path):
        log = tmp_path / "platform.log"
        n = ci_summary.MAX_FAILURES + 4
        log.write_text("".join(f"FAILED t.py::test_{i}\n" for i in range(n)) + f"{n} failed in 1s\n")
        out = ci_summary.render_tests([log])
        assert out.count("- `platform: t.py::test_") == ci_summary.MAX_FAILURES
        assert "and 4 more in the log" in out


class TestRenderCoverage:
    def test_deltas_against_the_committed_file(self):
        out = ci_summary.render_coverage(coverage(120, {5: 7, 6: 3}), coverage(100, {5: 7, 6: 5}), True)
        assert "**artifacts changed**" in out
        assert "- entities: 120 (+20)" in out
        assert "| 5 | 7 |" in out
        assert "| 6 | 3 (-2) |" in out

    def test_no_committed_file(self):
        out = ci_summary.render_coverage(coverage(100, {5: 1}), None, None)
        assert "- entities: 100\n" in out
        assert "artifacts" not in out

    def test_cli_treats_an_empty_old_file_as_none(self, tmp_path, capsys):
        new = tmp_path / "new.json"
        new.write_text(json.dumps(coverage(100, {5: 1})))
        old = tmp_path / "old.json"
        old.write_text("")
        assert ci_summary.main(["coverage", str(new), "--old", str(old), "--changed", "no"]) == 0
        out = capsys.readouterr().out
        assert "**artifacts unchanged**" in out
        assert "- entities: 100\n" in out


class TestRenderDays:
    def test_one_board_every_day_is_visible(self):
        out = ci_summary.render_days([day("colour") for _ in range(4)])
        assert "- generated: 4/4" in out
        assert "- distinct hidden groups: 1" in out
        assert "colour (4)" in out

    def test_failures_and_sizes(self):
        days = [day("a", size=5), day("b", size=6), day("", generated=False, reason="no board")]
        out = ci_summary.render_days(days)
        assert "- generated: 2/3" in out
        assert "- on target band: 2/2" in out
        assert "- distinct hidden groups: 2" in out
        assert "- group sizes: 5x1, 6x1" in out
        assert "- not generated: no board (1)" in out

    def test_nothing_generated(self):
        out = ci_summary.render_days([day("", generated=False)])
        assert "- generated: 0/1" in out
        assert "- group sizes: none" in out
        assert "not generated: unknown (1)" in out
