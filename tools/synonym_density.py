#!/usr/bin/env python3
"""Which synsets already have enough of their OWN synonyms to be a category.

A lexical category's size, for grouping purposes, is the count of lemmas that
are direct senses of that exact synset -- not the number of hyponym synsets
beneath it. ``boat.n.01`` has one lemma ("boat"); ``canoe``, ``kayak``, and
``dory`` are each their own separate synset, siblings under ``boat``, not
members of it. A prior version of this tool measured hyponym breadth, which
answers a different question (how many distinct child concepts exist) than
the one the generator actually asks (how many words are synonyms of ONE
concept) -- corrected here.

The two ways a synset clears a useful-looking size are both usually a poor
visible group: true synonymy on one meaning (WordNet lists nine names for one
species of periwinkle), or several distinct synsets colliding onto one
category id because they share a first lemma (four jackal species merging
into one "jackal" category, per ``report_lemma_collisions.py``). Both produce
a category whose members are names for the same thing, not five different
things -- flagged here, not silently trusted.

Reads files already on disk. No ``wn`` import, no lexicon data needed.

    python tools/synonym_density.py --lexicon content/seeds/wordnet-plant.lexicon.json
    python tools/synonym_density.py --lexicon content/seeds/wordnet-*.lexicon.json --min-size 5
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def read_lexicon(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def density(paths: list[Path]) -> list[dict]:
    """Every synset with >=1 lemma, sized by its own direct sense count."""
    names: dict[str, dict] = {}
    lemmas: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        document = read_lexicon(path)
        for synset in document.get("synsets", ()):
            names[synset["id"]] = {**synset, "file": path.name}
        for sense in document.get("senses", ()):
            lemmas[sense["synset"]].append(sense["lemma"])

    rows = []
    for synset_id, synset in names.items():
        members = sorted(lemmas.get(synset_id, ()))
        if not members:
            continue
        rows.append(
            {
                "id": synset_id,
                "name": synset.get("name", ""),
                "definition": synset.get("definition", ""),
                "file": synset["file"],
                "own_lemma_count": len(members),
                "own_lemmas": members,
            }
        )
    rows.sort(key=lambda r: (-r["own_lemma_count"], r["name"]))
    return rows


def same_concept_cluster(row: dict) -> bool:
    """A heuristic flag, not a verdict: read the member list before trusting it.

    True synonymy and merge-collisions both tend to produce lemmas that share
    most of their words with the category's own name or with each other
    (rose periwinkle / Cape periwinkle / red periwinkle) -- the shape of a
    single concept with many names, not five different concepts.
    """
    if row["own_lemma_count"] < 3:
        return False
    name_words = set(row["name"].lower().replace("-", " ").split())
    overlapping = sum(
        1
        for lemma in row["own_lemmas"]
        if name_words & set(lemma.lower().replace("-", " ").split())
    )
    return overlapping >= row["own_lemma_count"] - 1


def render(rows: list[dict], *, limit: int, min_size: int) -> str:
    shown = [r for r in rows if r["own_lemma_count"] >= min_size][:limit]
    total_eligible = sum(1 for r in rows if r["own_lemma_count"] >= min_size)
    lines = [
        f"{len(rows)} synsets have at least one lemma of their own; "
        f"{total_eligible} reach {min_size}+ (showing top {len(shown)})",
        "",
    ]
    for row in shown:
        flag = (
            " [likely one concept, many names -- check before using as a visible group]"
            if same_concept_cluster(row)
            else ""
        )
        lines.append(
            f"{row['own_lemma_count']:>3}  {row['name']:<24} ({row['file']}){flag}"
        )
        lines.append(f"      {row['definition'][:90]}")
        lines.append(f"      lemmas: {', '.join(row['own_lemmas'])}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexicon", action="append", required=True, type=Path)
    parser.add_argument("--min-size", type=int, default=5)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = density(args.lexicon)
    text = render(rows, limit=args.limit, min_size=args.min_size)
    print(text)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"json: {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
