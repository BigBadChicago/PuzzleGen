#!/usr/bin/env python3
"""Export a WordNet subtree into the lexicon file the engine reads.

Run this once, with ``wn`` installed, to produce a versioned artifact. The
engine never imports ``wn``: keeping the heavy dependency in a build step is
what lets the runtime install stay small and lets two machines importing the
same file produce the same snapshot.

Depths come from WordNet itself rather than being recomputed. Its hypernym
graph is maintained acyclic upstream and already exposes minimum and maximum
depth, so copying two integers is both cheaper and more faithful than deriving
them from a partial export.

    python tools/export_wordnet.py --root carnivore.n.01 --depth 3 \
        --out content/seeds/wordnet-carnivores.lexicon.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexicon", default="oewn:2024")
    parser.add_argument("--root", required=True, help="synset id or lemma to start from")
    parser.add_argument("--depth", type=int, default=3, help="hyponym levels to include")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    try:
        import wn
    except ImportError:
        print(
            "the wn package is required for export: pip install 'puzzlegen[snapshot]'",
            file=sys.stderr,
        )
        return 2

    lexicon_id, _, version = args.lexicon.partition(":")
    wordnet = wn.Wordnet(lexicon=args.lexicon)

    roots = wordnet.synsets(args.root) or [wordnet.synset(args.root)]
    collected: dict[str, object] = {}
    frontier = [(s, 0) for s in roots]
    while frontier:
        synset, level = frontier.pop()
        if synset.id in collected or level > args.depth:
            continue
        collected[synset.id] = synset
        for hyponym in synset.hyponyms():
            frontier.append((hyponym, level + 1))

    synsets = []
    senses = []
    for synset in sorted(collected.values(), key=lambda s: s.id):
        synsets.append(
            {
                "id": synset.id,
                "name": synset.lemmas()[0] if synset.lemmas() else synset.id,
                "definition": synset.definition(),
                "hypernyms": sorted(h.id for h in synset.hypernyms()),
                "min_depth": synset.min_depth(),
                "max_depth": synset.max_depth(),
                "lang": synset.lexicon().language,
            }
        )
        for sense in synset.senses():
            senses.append(
                {
                    "id": sense.id,
                    "lemma": sense.word().lemma(),
                    "forms": sorted(set(sense.word().forms()) - {sense.word().lemma()}),
                    "synset": synset.id,
                    "definition": synset.definition(),
                    "lang": synset.lexicon().language,
                }
            )

    document = {
        "lexicon_schema": 1,
        "lexicon": lexicon_id,
        "version": version or "unversioned",
        "exported": dt.date.today().isoformat(),
        "url": "https://en-word.net/",
        "synsets": synsets,
        "senses": senses,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(synsets)} synsets and {len(senses)} senses to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
