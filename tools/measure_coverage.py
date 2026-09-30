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
    explain_homeless,
    gain_of,
    load,
    survey,
)

from puzzlegen.games.grouping.content import VISIBLE_GROUPING
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
                "homeless_reasons": {
                    snapshot.name_of(m): explain_homeless(snapshot, m, group_size)
                    for m in finding.homeless
                },
                "home_names": sorted(
                    snapshot.lexical_names.get(cid, cid) for cid in reached
                )[:8],
                "why_no_quadruple": [
                    {"cause": cause, "count": count}
                    for cause, count in finding.failures
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
                f"{row['homes_reached']} homes (needs {row['homes_needed']}) "
                f"[{row['reason']}]"
            )
            if row.get("home_names"):
                lines.append("    homes: " + ", ".join(row["home_names"]))
            if row["homeless_members"]:
                lines.append(
                    "    no usable home: "
                    + ", ".join(row["homeless_members"][:10])
                )
                # Once, at the smallest size: the same words are homeless for
                # the same reasons at every size, and repeating five times
                # buries the part that changes.
                if group_size == min(targets):
                    for word, why in list(row["homeless_reasons"].items())[:8]:
                        lines.append(f"      {word}: {why}")
            if row.get("why_no_quadruple"):
                causes = ", ".join(
                    f"{c['cause']} {c['count']}" for c in row["why_no_quadruple"]
                )
                lines.append(f"    why no board: {causes}")
            if row["propose_from"]:
                offered = ", ".join(
                    f"{entry['category']}({entry['members']})"
                    for entry in row["propose_from"][:5]
                )
                lines.append(f"    propose words from: {offered}")

    lines.append("")
    lines.append(
        "propose words from: lists parents chosen by size alone. Whether one "
        "suits a category's meaning is a human call the tool cannot make."
    )
    lines.append(
        "generatable: yes" if summary["generatable"] else "generatable: no"
    )
    return "\n".join(lines)


def resolve_category(snapshot: Snapshot, name_or_id: str) -> str | None:
    """A category argument can be given as a name or an id; try both.

    Names are what a curator actually has in hand when proposing a candidate
    ("thing that can be smoked"); ids are what the rest of this module works
    in. Ambiguous names (two overlay categories sharing a lowercased name)
    are rejected rather than guessed at, since a silent wrong match would
    misreport gain for the wrong hidden group.
    """
    if name_or_id in snapshot.overlay_names:
        return name_or_id
    matches = [
        cid
        for cid, name in snapshot.overlay_names.items()
        if name.lower() == name_or_id.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_entity(snapshot: Snapshot, name_or_id: str) -> str | None:
    if name_or_id in snapshot.entity_names:
        return name_or_id
    matches = [
        eid
        for eid, name in snapshot.entity_names.items()
        if name.lower() == name_or_id.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def load_candidates(path: Path) -> list[dict]:
    """A JSON array of ``{"entity": ..., "category": ...}`` rows.

    Plain JSON, not a manifest schema of its own, because this is a working
    list a curator edits by hand while deciding what to add to the seed --
    the same shape ``overlay.curated.json`` uses for entity-to-category pairs,
    kept separate so trying a candidate is not the same act as accepting one.
    """
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a JSON array of candidate rows")
    for row in rows:
        if "entity" not in row or "category" not in row:
            raise ValueError(f"{path}: each row needs 'entity' and 'category'")
    return rows


def rank_candidates(
    snapshot: Snapshot,
    rows: list[dict],
    *,
    group_sizes: tuple[int, ...],
    exact: bool,
    budget: int,
) -> list[dict]:
    """Every candidate's gain, worst problems surfaced rather than buried.

    An unresolved entity or category is reported as its own row instead of
    raising, because a batch of forty candidates should not abort on the
    first typo -- the curator fixes what the report shows and reruns.
    """
    results = []
    for row in rows:
        entity_id = resolve_entity(snapshot, row["entity"])
        category_id = resolve_category(snapshot, row["category"])
        if entity_id is None or category_id is None:
            results.append(
                {
                    "entity": row["entity"],
                    "category": row["category"],
                    "error": (
                        "unknown entity" if entity_id is None else "unknown category"
                    ),
                }
            )
            continue
        gain = gain_of(
            snapshot,
            entity_id,
            category_id,
            group_sizes=group_sizes,
            exact=exact,
            budget=budget,
        )
        results.append(
            {
                "entity": row["entity"],
                "category": row["category"],
                "new_homes": [
                    snapshot.lexical_names.get(cid, cid) for cid in gain.new_homes
                ],
                "unblocks": list(gain.unblocks),
                "checked": gain.checked,
                "rank": gain.rank,
            }
        )
    results.sort(
        key=lambda r: r.get("rank", (0, 0, r["entity"])) if "error" not in r else (1, 0, r["entity"])
    )
    return results


def render_candidates(results: list[dict]) -> str:
    lines = ["candidate gain, best first"]
    problems = [r for r in results if "error" in r]
    ranked = [r for r in results if "error" not in r]
    for row in ranked:
        flag = "UNBLOCKS" if row["unblocks"] else ("new home" if row["new_homes"] else "no gain")
        lines.append(
            f"  [{flag:>8}] {row['entity']:<16} -> {row['category']:<32} "
            + (f"unblocks sizes {row['unblocks']}" if row["unblocks"] else "")
            + (f" new: {', '.join(row['new_homes'])}" if row["new_homes"] else "")
        )
        if not row["checked"] and not row["new_homes"]:
            lines.append("             (already reaches every home this category has)")
    if problems:
        lines.append("")
        lines.append("could not evaluate:")
        for row in problems:
            lines.append(f"  {row['entity']} -> {row['category']}: {row['error']}")
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
    parser.add_argument(
        "--grouping",
        choices=("shared_category", "siblings"),
        default=str(VISIBLE_GROUPING),
        help=(
            "how a visible group is formed. Defaults to what game 1 actually "
            "does, so the numbers describe the game rather than a rule it "
            "no longer uses."
        ),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=None,
        help=(
            "JSON array of {'entity': ..., 'category': ...} rows to rank by "
            "coverage gain, instead of the default full survey"
        ),
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="with --candidates, run the exact feasibility flip check, not just the cheap new-homes count",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        parser.error(f"no such database: {args.db}")

    sizes = tuple(args.group_size) if args.group_size else GROUP_SIZES

    repos = GraphRepositories(SqliteDocumentStore(args.db))
    try:
        snapshot = load(repos, grouping=args.grouping)
    finally:
        # Python 3.13 and later report a collected-unclosed connection as a
        # warning, which the suite turns into a failure somewhere unrelated.
        repos.close()

    if args.candidates is not None:
        rows = load_candidates(args.candidates)
        results = rank_candidates(
            snapshot, rows, group_sizes=sizes, exact=args.exact, budget=args.budget
        )
        print(render_candidates(results))
        if args.json is not None:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(
                json.dumps(results, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"json: {args.json}")
        return 0 if any(r.get("unblocks") for r in results) else 1

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
