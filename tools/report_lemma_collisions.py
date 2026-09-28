#!/usr/bin/env python3
"""Report what merges, what is missing, and what is ambiguous, before a build.

Entity identity is per import, and the lexical import uses ``lemma``: every
provider's assertions about the same word become one entity. That is what makes
facts reusable across games, and it is also where a snapshot goes quietly wrong.
Three failure shapes, all invisible once the build has run:

* **Polysemy.** One lemma, several synsets. Under lemma identity they merge, so
  the entity ends up carrying the definition of whichever sense arrived first
  and the taxonomic parents of all of them. A grouping game can then put
  "crane" in the bird group and the machine group at once, which is a broken
  puzzle rather than a clever one.
* **Cross-root collisions.** One lemma exported under two different roots. Same
  merge, but visible earlier, and usually a sign the two exports overlap more
  than intended.
* **Overlay members the lexicon does not have.** They still become entities,
  with no definition, no frequency band and no taxonomic parent, so they can
  only ever serve as hidden-group members. Fine in ones and twos, a sign the
  overlay and the lexicon have drifted apart in tens.

Reports; changes nothing.

    python tools/report_lemma_collisions.py \\
        --lexicon content/seeds/wordnet-animal.lexicon.json \\
        --lexicon content/seeds/wordnet-plant.lexicon.json \\
        --out build/lemma-collisions.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_OVERLAY = Path("content/seeds/overlay.curated.json")


@dataclass(slots=True)
class LemmaUse:
    """Every place one lemma appears across the exports being reported on."""

    lemma: str
    synsets: set[str] = field(default_factory=set)
    definitions: dict[str, str] = field(default_factory=dict)
    files: set[str] = field(default_factory=set)

    @property
    def senses(self) -> int:
        return len(self.synsets)

    def as_json(self) -> dict:
        return {
            "lemma": self.lemma,
            "senses": self.senses,
            "synsets": sorted(self.synsets),
            "files": sorted(self.files),
            "definitions": {k: self.definitions[k] for k in sorted(self.definitions)},
        }


def read_lexicon(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if "senses" not in document:
        raise ValueError(f"{path} is not a lexicon export: no senses")
    return document


def collect(paths: list[Path]) -> dict[str, LemmaUse]:
    uses: dict[str, LemmaUse] = {}
    for path in paths:
        document = read_lexicon(path)
        glosses = {s["id"]: s.get("definition", "") for s in document.get("synsets", ())}
        for sense in document.get("senses", ()):
            lemma = sense["lemma"].lower()
            use = uses.setdefault(lemma, LemmaUse(lemma=lemma))
            synset = sense["synset"]
            use.synsets.add(synset)
            use.files.add(path.name)
            gloss = sense.get("definition") or glosses.get(synset, "")
            if gloss:
                use.definitions[synset] = gloss
    return uses


def overlay_members(path: Path) -> dict[str, list[str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    names = {c["key"]: c["name"] for c in document.get("categories", ())}
    members: dict[str, list[str]] = {}
    for entity in document.get("entities", ()):
        members[entity["name"].lower()] = [
            names.get(key, key) for key in entity.get("categories", ())
        ]
    return members


def build_report(
    uses: dict[str, LemmaUse],
    members: dict[str, list[str]],
    *,
    limit: int | None = None,
) -> dict:
    polysemous = sorted(
        (u for u in uses.values() if u.senses > 1),
        key=lambda u: (-u.senses, u.lemma),
    )
    cross_file = sorted(
        (u for u in uses.values() if len(u.files) > 1), key=lambda u: u.lemma
    )
    ambiguous_overlay = [
        {
            "lemma": lemma,
            "overlay_categories": categories,
            "senses": uses[lemma].senses,
            "definitions": [uses[lemma].definitions[s] for s in sorted(uses[lemma].definitions)],
        }
        for lemma, categories in sorted(members.items())
        if lemma in uses and uses[lemma].senses > 1
    ]
    missing_overlay = [
        {"lemma": lemma, "overlay_categories": categories}
        for lemma, categories in sorted(members.items())
        if lemma not in uses
    ]
    histogram = collections.Counter(u.senses for u in uses.values())

    def cut(rows: list) -> list:
        return rows[:limit] if limit is not None else rows

    return {
        "counts": {
            "lemmas": len(uses),
            "polysemous": len(polysemous),
            "cross_file": len(cross_file),
            "overlay_members": len(members),
            "overlay_ambiguous": len(ambiguous_overlay),
            "overlay_missing": len(missing_overlay),
        },
        "sense_histogram": {str(k): histogram[k] for k in sorted(histogram)},
        "polysemous": cut([u.as_json() for u in polysemous]),
        "cross_file": cut([u.as_json() for u in cross_file]),
        "overlay_ambiguous": cut(ambiguous_overlay),
        "overlay_missing": cut(missing_overlay),
    }


def render(report: dict) -> str:
    counts = report["counts"]
    lines = [
        f"lemmas                : {counts['lemmas']}",
        f"polysemous lemmas     : {counts['polysemous']}",
        f"lemmas in two exports : {counts['cross_file']}",
        f"overlay members       : {counts['overlay_members']}",
        f"  ambiguous in lexicon: {counts['overlay_ambiguous']}",
        f"  absent from lexicon : {counts['overlay_missing']}",
        "",
        "senses per lemma: "
        + ", ".join(f"{k}={v}" for k, v in report["sense_histogram"].items()),
    ]
    if report["overlay_ambiguous"]:
        lines.append("")
        lines.append("overlay members with more than one sense:")
        for row in report["overlay_ambiguous"]:
            lines.append(
                f"  {row['lemma']:<16} {row['senses']} senses  "
                f"overlay: {', '.join(row['overlay_categories'])}"
            )
    if report["overlay_missing"]:
        lines.append("")
        lines.append("overlay members the lexicon does not supply:")
        for row in report["overlay_missing"]:
            lines.append(
                f"  {row['lemma']:<16} overlay: "
                f"{', '.join(row['overlay_categories'])}"
            )
    if report["cross_file"]:
        lines.append("")
        lines.append("lemmas exported under more than one root:")
        for row in report["cross_file"]:
            lines.append(f"  {row['lemma']:<16} {', '.join(row['files'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexicon", action="append", required=True, type=Path)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    missing = [p for p in args.lexicon if not p.exists()]
    if missing:
        print(f"lexicon not found: {missing[0]}", file=sys.stderr)
        return 2

    uses = collect(args.lexicon)
    members = overlay_members(args.overlay) if args.overlay.exists() else {}
    report = build_report(uses, members, limit=args.limit)

    print(render(report))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
