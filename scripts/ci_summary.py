#!/usr/bin/env python3
"""Render a CI run as a short Markdown summary.

A session that dispatches a workflow should read a dozen lines, not a build
log. Every job ends by printing what this renders, so the tail of the log is
the answer and the rest is there only for when the answer is a failure.

    ci_summary.py tests build/ci/platform.log build/ci/grouping.log
    ci_summary.py coverage build/coverage-A2.json --old OLD.json
    ci_summary.py days build/days-30.json

Standard library only: it runs before anything is installed when an install
step is what failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

#: pytest -q's closing line, with or without the ``=`` rule around it.
_RESULT = re.compile(r"^=*\s*(\d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warnings?)\b.*?)\s*=*$")
_FAILED = re.compile(r"^(FAILED|ERROR) (\S+)")
#: Enough to name the broken tests; past that the log is the place to look.
MAX_FAILURES = 15


def summarise_suite(log: str) -> tuple[str, list[str]]:
    result = "no pytest result line; the suite did not finish"
    failures: list[str] = []
    for line in log.splitlines():
        line = line.strip()
        if match := _RESULT.match(line):
            result = match.group(1)
        elif match := _FAILED.match(line):
            failures.append(match.group(2))
    return result, failures


def suite_ok(result: str) -> bool:
    return " passed" in f" {result}" and not re.search(r"\b(failed|errors?)\b", result)


def render_tests(logs: list[Path]) -> str:
    lines = ["### Tests", "", "| suite | result |", "|---|---|"]
    failures: list[str] = []
    all_ok = True
    for path in logs:
        result, failed = summarise_suite(path.read_text(encoding="utf-8", errors="replace"))
        ok = suite_ok(result)
        all_ok &= ok
        lines.append(f"| {path.stem} | {'ok' if ok else 'FAIL'}: {result} |")
        failures += [f"{path.stem}: {f}" for f in failed]
    lines.insert(1, f"**{'green' if all_ok else 'red'}**")
    if failures:
        lines += ["", "Failing:"] + [f"- `{f}`" for f in failures[:MAX_FAILURES]]
        if len(failures) > MAX_FAILURES:
            lines.append(f"- and {len(failures) - MAX_FAILURES} more in the log")
    return "\n".join(lines) + "\n"


def _sizes(coverage: dict) -> dict[int, int]:
    return {s["group_size"]: s["feasible_hidden_groups"] for s in coverage["summary"]["sizes"]}


def _delta(new: int, old: int | None) -> str:
    if old is None or old == new:
        return str(new)
    return f"{new} ({new - old:+d})"


def render_coverage(new: dict, old: dict | None, changed: bool | None) -> str:
    s = new["summary"]
    o = old["summary"] if old else {}
    old_sizes = _sizes(old) if old else {}
    lines = ["### Coverage (build A2)", ""]
    if changed is not None:
        lines += [
            "**artifacts changed**: the committed files no longer match the code"
            if changed
            else "**artifacts unchanged**: the committed files match the code",
            "",
        ]
    lines += [
        f"- entities: {_delta(s['entities'], o.get('entities'))}",
        f"- usable as tiles: {_delta(s['entities_usable_as_tiles'], o.get('entities_usable_as_tiles'))}",
        f"- lexical categories: {_delta(s['lexical_categories'], o.get('lexical_categories'))}",
        f"- generatable at {s['difficulty']}: {s['generatable']}",
        "",
        "| group size | feasible hidden groups |",
        "|---|---|",
    ]
    for size, feasible in sorted(_sizes(new).items()):
        lines.append(f"| {size} | {_delta(feasible, old_sizes.get(size))} |")
    return "\n".join(lines) + "\n"


def render_days(days: list[dict]) -> str:
    generated = [d for d in days if d.get("generated")]
    hidden = Counter(d["hidden"] for d in generated if d.get("hidden"))
    sizes = Counter(d["group_size"] for d in generated)
    reasons = Counter(d.get("reason") or "unknown" for d in days if not d.get("generated"))
    on_target = sum(1 for d in generated if d.get("on_target"))
    lines = [
        "### Days",
        "",
        f"- generated: {len(generated)}/{len(days)}",
        f"- on target band: {on_target}/{len(generated)}",
        # One distinct board across every day is the starvation gap, not variety.
        f"- distinct hidden groups: {len(hidden)}",
        f"- group sizes: {', '.join(f'{k}x{v}' for k, v in sorted(sizes.items())) or 'none'}",
    ]
    if hidden:
        lines.append("- most common hidden: " + ", ".join(f"{h} ({n})" for h, n in hidden.most_common(3)))
    if reasons:
        lines.append("- not generated: " + ", ".join(f"{r} ({n})" for r, n in reasons.most_common()))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="kind", required=True)
    tests = sub.add_parser("tests")
    tests.add_argument("logs", nargs="+", type=Path)
    coverage = sub.add_parser("coverage")
    coverage.add_argument("new", type=Path)
    coverage.add_argument("--old", type=Path, default=None)
    coverage.add_argument("--changed", choices=["yes", "no"], default=None)
    days = sub.add_parser("days")
    days.add_argument("days", type=Path)
    args = parser.parse_args(argv)

    if args.kind == "tests":
        out = render_tests(args.logs)
    elif args.kind == "coverage":
        old = json.loads(args.old.read_text()) if args.old and args.old.stat().st_size else None
        changed = None if args.changed is None else args.changed == "yes"
        out = render_coverage(json.loads(args.new.read_text()), old, changed)
    else:
        out = render_days(json.loads(args.days.read_text()))
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
