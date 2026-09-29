#!/usr/bin/env python3
"""Can a board generate today, and if not, what content would let it.

Replaces the ad hoc count in ``docs/handoff.md``. That table asked how many
lexical categories hold at least ``group_size`` members and at least one
overlay member, which is a property of a single category; the generator needs
four disjoint categories and one overlay category whose members are spread
across all four. The old number can double while this one stays at zero, so
it is reported here too, labelled as the proxy it is, rather than dropped:
seeing both is what shows the proxy overstating.

    python tools/measure_coverage.py
    python tools/measure_coverage.py --db content/graph.sqlite --json out.json

Exit status is 0 when at least one group size can generate a board, and 1
otherwise, so the external checklist's step 4 is a command with an answer
rather than a table to read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from overlay_coverage import (
    DEFAULT_BUDGET,
    Report,
    Snapshot,
    distinct_homes,
    load,
    survey,
)

from puzzlegen.games.grouping.descriptor import GROUP_SIZES, VISIBLE_GROUPS
from puzzlegen.graph import GraphRepositories, SqliteDocumentStore

DEFAULT_DB = Path("content/graph.sqlite")

#: How many near misses to name per group size. A curator reads a shortlist;
#: a hundred rows is a file nobody opens.
SHORTLIST = 8


def proxy_count(snapshot: Snapshot, group_size: int) -> int:
    """The handoff's original measure, kept for comparison only."""
    overlay = set().union(*snapshot.overlay_members.values()) if (
        snapshot.overlay_members
    ) else set()
    return sum(
        1
        for members in snapshot.lexical_members.values()
        if len(members) >= group_size and members & overlay
    )


def summarise(snapshot: Snapshot, report: Report) -> dict:
    sizes = []
    for group_size in report.group_sizes:
        findings = report.at(group_size)
        feasible = [f for f in findings if f.feasible]
        reasons = Counter(f.reason for f in findings if not f.feasible)
        sizes.append(
            {
                "group_size": group_size,
                "board_size": group_size * VISIBLE_GROUPS,
                "feasible_hidden_groups": len(feasible),
                "hidden_groups_examined": len(findings),
                "bounded_searches": sum(1 for f in findings if not f.exhausted),
                "proxy_usable_categories": proxy_count(snapshot, group_size),
                "reasons": dict(sorted(reasons.items())),
                "feasible_examples": [f.category_name for f in feasible[:SHORTLIST]],
            }
        )
    return {
        "generatable": report.any_feasible(),
        "visible_groups": VISIBLE_GROUPS,
        "overlay_categories": len(snapshot.overlay_members),
        "lexical_categories": len(snapshot.lexical_members),
        "entities": len(snapshot.entity_names),
        "sizes": sizes,
    }


def targeting(snapshot: Snapshot, report: Report, group_size: int) -> list[dict]:
    """Near misses, and the lexical categories that would close each one.

    Ordered by how close the overlay category already is, because a category
    reaching three of the four homes it needs is one accepted word away and a
    category reaching none is a different problem.
    """
    rows = []
    for finding in report.at(group_size):
        if finding.feasible:
            continue
        reached = distinct_homes(snapshot, finding.category_id, group_size)
        missing = [
            cid
            for cid in snapshot.usable_lexical(group_size)
            if cid not in reached
        ]
        missing.sort(
            key=lambda cid: (-len(snapshot.lexical_members[cid]), cid)
        )
        rows.append(
            {
                "hidden_group": finding.category_name,
                "hidden_group_id": finding.category_id,
                "members": len(
                    snapshot.overlay_members.get(finding.category_id, ())
                ),
                "reason": finding.reason,
                "homes_reached": len(reached),
                "homes_needed": VISIBLE_GROUPS,
                "homeless_members": [
                    snapshot.name_of(m) for m in finding.homeless
                ],
                "propose_from": [
                    {
                        "category": snapshot.lexical_names.get(cid, cid),
                        "category_id": cid,
                        "members": len(snapshot.lexical_members[cid]),
                    }
                    for cid in missing[:SHORTLIST]
                ],
            }
        )
    rows.sort(key=lambda row: (-row["homes_reached"], row["hidden_group"]))
    return rows[:SHORTLIST]


def render(summary: dict, targets: dict[int, list[dict]]) -> str:
    lines = [
        "overlay coverage against the generator's actual precondition",
        "",
        f"entities {summary['entities']}, "
        f"lexical categories {summary['lexical_categories']}, "
        f"overlay categories {summary['overlay_categories']}",
        "",
        "size  board  feasible  bounded  proxy(old table)",
    ]
    for row in summary["sizes"]:
        lines.append(
            f"{row['group_size']:>4}  {row['board_size']:>5}  "
            f"{row['feasible_hidden_groups']:>8}  {row['bounded_searches']:>7}  "
            f"{row['proxy_usable_categories']:>16}"
        )
    lines.append("")
    lines.append(
        "feasible = overlay categories that can be a hidden group with four "
        "disjoint visible groups"
    )
    lines.append(
        "proxy = lexical categories with enough members and one overlay word; "
        "an upper bound that does not imply a board"
    )

    for row in summary["sizes"]:
        if row["reasons"]:
            reasons = ", ".join(f"{k} {v}" for k, v in row["reasons"].items())
            lines.append(f"  size {row['group_size']}: {reasons}")

    for group_size, rows in sorted(targets.items()):
        if not rows:
            continue
        lines.append("")
        lines.append(f"closest at group size {group_size}")
        for row in rows:
            lines.append(
                f"  {row['hidden_group']} ({row['members']} members) reaches "
                f"{row['homes_reached']} of {row['homes_needed']} homes "
                f"[{row['reason']}]"
            )
            if row["homeless_members"]:
                lines.append(
                    "    no usable home: "
                    + ", ".join(row["homeless_members"][:10])
                )
            if row["propose_from"]:
                offered = ", ".join(
                    f"{entry['category']}({entry['members']})"
                    for entry in row["propose_from"][:5]
                )
                lines.append(f"    propose words from: {offered}")

    lines.append("")
    lines.append(
        "generatable: yes" if summary["generatable"] else "generatable: no"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument(
        "--group-size",
        type=int,
        action="append",
        default=None,
        help="measure one size only; repeatable. Defaults to every supported size.",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        parser.error(f"no such database: {args.db}")

    sizes = tuple(args.group_size) if args.group_size else GROUP_SIZES

    repos = GraphRepositories(SqliteDocumentStore(args.db))
    try:
        snapshot = load(repos)
    finally:
        # Python 3.13 and later report a collected-unclosed connection as a
        # warning, which the suite turns into a failure somewhere unrelated.
        repos.close()

    report = survey(snapshot, group_sizes=sizes, budget=args.budget)
    summary = summarise(snapshot, report)
    targets = {size: targeting(snapshot, report, size) for size in sizes}

    print(render(summary, targets))

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {"summary": summary, "targets": targets},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"json: {args.json}")

    return 0 if summary["generatable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
