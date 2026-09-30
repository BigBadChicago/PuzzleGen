#!/usr/bin/env python3
"""Retire and reopen overlay categories without losing them.

A category that cannot currently make a board is better set aside than
deleted, because the reason it failed and the condition for trying again are
worth more than the words. Retiring moves it out of ``categories``, takes it
off its members, drops any member left with no category, and records all of it
under ``retired``. The snapshot build never sees a retired category. Reopening
puts everything back where it was.

    python tools/overlay_seed.py list
    python tools/overlay_seed.py retire overlay.wheels \\
        --reason "..." --reopen-when "..."
    python tools/overlay_seed.py reopen overlay.wheels

The file is written in its existing layout, one object per line, so a
retirement is a small diff rather than a rewrite. ``dumps`` reproduces the
committed file byte for byte, which a test checks.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import sys
import textwrap
from pathlib import Path

DEFAULT_SEED = Path("content/seeds/overlay.curated.json")

#: Top level lists written one item per line.
LINE_PER_ITEM = ("warnings", "categories", "entities")


class SeedError(ValueError):
    """The requested change cannot be made to this seed."""


def dumps(document: dict) -> str:
    """The seed in its own layout: header keys, then one item per line."""
    keys = list(document)
    lines = ["{"]
    for position, key in enumerate(keys):
        value = document[key]
        comma = "," if position < len(keys) - 1 else ""
        if isinstance(value, list) and value and key in LINE_PER_ITEM:
            lines.append(f'  "{key}": [')
            for index, item in enumerate(value):
                tail = "," if index < len(value) - 1 else ""
                lines.append("    " + json.dumps(item, ensure_ascii=False) + tail)
            lines.append("  ]" + comma)
        elif isinstance(value, list) and value and key == "retired":
            lines.append(f'  "{key}": [')
            for index, item in enumerate(value):
                tail = "," if index < len(value) - 1 else ""
                body = json.dumps(item, ensure_ascii=False, indent=2)
                lines.append(textwrap.indent(body, "    ") + tail)
            lines.append("  ]" + comma)
        else:
            lines.append(f'  "{key}": {json.dumps(value, ensure_ascii=False)}{comma}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def retire(
    document: dict, key: str, *, reason: str, reopen_when: str, on: str
) -> dict:
    """A copy of ``document`` with the category set aside."""
    if not reason.strip() or not reopen_when.strip():
        raise SeedError("a retirement needs a reason and a condition for reopening")
    dt.date.fromisoformat(on)

    result = copy.deepcopy(document)
    if any(entry["key"] == key for entry in result.get("retired", ())):
        raise SeedError(f"{key} is already retired")
    categories = result["categories"]
    positions = [i for i, c in enumerate(categories) if c["key"] == key]
    if not positions:
        raise SeedError(f"no category {key!r} to retire")
    category = categories.pop(positions[0])

    members: list[str] = []
    removed: list[dict] = []
    kept: list[dict] = []
    for index, row in enumerate(result["entities"]):
        if key not in row["categories"]:
            kept.append(row)
            continue
        members.append(row["name"])
        row["categories"] = [c for c in row["categories"] if c != key]
        if row["categories"]:
            kept.append(row)
        else:
            removed.append({"index": index, "row": {**row, "categories": [key]}})
    result["entities"] = kept

    result.setdefault("retired", []).append(
        {
            "key": key,
            "name": category["name"],
            "gloss": category.get("gloss", ""),
            "position": positions[0],
            "members": members,
            "entities_removed": removed,
            "retired_on": on,
            "reason": reason,
            "reopen_when": reopen_when,
        }
    )
    result["updated"] = on
    return result


def reopen(document: dict, key: str, *, on: str | None = None) -> dict:
    """A copy of ``document`` with the category live again."""
    result = copy.deepcopy(document)
    entries = result.get("retired", [])
    matches = [i for i, entry in enumerate(entries) if entry["key"] == key]
    if not matches:
        raise SeedError(f"{key} is not retired")
    if any(c["key"] == key for c in result["categories"]):
        raise SeedError(f"{key} is already live")
    entry = entries.pop(matches[0])
    if not entries:
        del result["retired"]

    result["categories"].insert(
        entry["position"],
        {"key": key, "name": entry["name"], "gloss": entry["gloss"]},
    )
    for record in sorted(entry["entities_removed"], key=lambda r: r["index"]):
        result["entities"].insert(record["index"], record["row"])
    restored = {record["row"]["name"] for record in entry["entities_removed"]}
    for row in result["entities"]:
        if row["name"] in entry["members"] and row["name"] not in restored:
            if key not in row["categories"]:
                row["categories"].append(key)
    if on:
        dt.date.fromisoformat(on)
        result["updated"] = on
    return result


def describe(document: dict) -> str:
    lines = [f"{len(document['categories'])} live categories"]
    for category in document["categories"]:
        count = sum(category["key"] in e["categories"] for e in document["entities"])
        lines.append(f"  {category['key']:<18} {category['name']} ({count} members)")
    retired = document.get("retired", [])
    lines.append(f"{len(retired)} retired")
    for entry in retired:
        lines.append(f"  {entry['key']:<18} {entry['name']} ({len(entry['members'])} members)")
        lines.append(f"      retired {entry['retired_on']}: {entry['reason']}")
        lines.append(f"      reopen when: {entry['reopen_when']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    retiring = commands.add_parser("retire")
    retiring.add_argument("key")
    retiring.add_argument("--reason", required=True)
    retiring.add_argument("--reopen-when", required=True)
    retiring.add_argument("--on", default=dt.date.today().isoformat())
    reopening = commands.add_parser("reopen")
    reopening.add_argument("key")
    reopening.add_argument("--on", default=dt.date.today().isoformat())
    args = parser.parse_args(argv)

    if not args.seed.exists():
        print(f"no such seed: {args.seed}", file=sys.stderr)
        return 2
    document = json.loads(args.seed.read_text(encoding="utf-8"))

    if args.command == "list":
        print(describe(document))
        return 0
    try:
        if args.command == "retire":
            changed = retire(
                document, args.key, reason=args.reason,
                reopen_when=args.reopen_when, on=args.on,
            )
        else:
            changed = reopen(document, args.key, on=args.on)
    except SeedError as error:
        print(str(error), file=sys.stderr)
        return 2
    args.seed.write_text(dumps(changed), encoding="utf-8")
    print(describe(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
