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

The root is given in one of three forms. ``animal.n.01`` is the familiar
lemma, part of speech and sense number, and means the first noun sense of
"animal" in the order ``wn`` lists them. A bare lemma such as ``animal`` works
only when it has exactly one sense. A synset id such as ``oewn-00015568-n``
is exact. The tool prints the synset it resolved and its definition before it
exports anything, because sense numbers come from the lexicon and can differ
between lexicons and editions; reading one line is cheaper than discovering a
plant export full of factories.

    python tools/export_wordnet.py --root animal.n.01 --depth 3 \\
        --out content/seeds/wordnet-animal.lexicon.json

    python tools/export_wordnet.py --root plant --pos n --list
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

#: ``lemma.pos.NN``, where NN is the 1-based position among that lemma's
#: senses of that part of speech. Multi-word lemmas use underscores here and
#: spaces in the lexicon, as in the notation this comes from.
SENSE_NAME = re.compile(r"^(?P<lemma>.+)\.(?P<pos>[nvasr])\.(?P<sense>\d{2})$")


class RootError(ValueError):
    """The root did not name exactly one synset."""


def candidate_lines(candidates, lemma: str, pos: str | None) -> list[str]:
    """One line per sense, numbered the way ``lemma.pos.NN`` counts them."""
    marker = pos or "?"
    name = lemma.replace(" ", "_")
    return [
        f"  {name}.{marker}.{index:02d}  {synset.id}  {synset.definition()[:80]}"
        for index, synset in enumerate(candidates, start=1)
    ]


def resolve_root(wordnet, spec: str, pos: str | None = None):
    """The one synset ``spec`` names, or a :class:`RootError` saying why not.

    Takes the wordnet as an argument and imports nothing, so the rules can be
    tested without the lexicon installed.
    """
    named = SENSE_NAME.match(spec)
    if named:
        lemma = named["lemma"].replace("_", " ")
        found = named["pos"]
        candidates = wordnet.synsets(lemma, pos=found)
        index = int(named["sense"])
        if not 1 <= index <= len(candidates):
            listing = "\n".join(candidate_lines(candidates, lemma, found))
            raise RootError(
                f"{spec}: {lemma!r} has {len(candidates)} {found} sense(s), "
                f"so sense {index:02d} does not exist\n{listing}"
            )
        return candidates[index - 1]

    lemma = spec.replace("_", " ")
    candidates = wordnet.synsets(lemma, pos=pos)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        listing = "\n".join(candidate_lines(candidates, lemma, pos))
        raise RootError(
            f"{spec!r} has {len(candidates)} senses; name one as lemma.pos.NN "
            f"or by synset id\n{listing}"
        )

    try:
        return wordnet.synset(spec)
    except Exception as error:  # noqa: BLE001
        # wn raises its own Error type and the stub in the tests raises
        # KeyError. Both mean the same thing here, and importing wn to catch
        # one precisely would defeat resolving without it.
        raise RootError(
            f"{spec!r} is neither a lemma, a lemma.pos.NN name nor a synset id "
            f"in this lexicon"
        ) from error


def collect(root, depth: int) -> dict:
    """Every synset within ``depth`` hyponym levels of ``root``."""
    collected: dict[str, object] = {}
    frontier = [(root, 0)]
    while frontier:
        synset, level = frontier.pop()
        if synset.id in collected or level > depth:
            continue
        collected[synset.id] = synset
        for hyponym in synset.hyponyms():
            frontier.append((hyponym, level + 1))
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lexicon", default="oewn:2024")
    parser.add_argument(
        "--root", required=True, help="lemma.pos.NN, a single-sense lemma, or a synset id"
    )
    parser.add_argument("--pos", default=None, help="part of speech for a bare lemma")
    parser.add_argument("--depth", type=int, default=3, help="hyponym levels to include")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the senses the root could mean and stop",
    )
    args = parser.parse_args(argv)

    if not args.list and args.out is None:
        print("--out is required unless --list is given", file=sys.stderr)
        return 2

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

    if args.list:
        lemma = args.root.replace("_", " ")
        candidates = wordnet.synsets(lemma, pos=args.pos)
        if not candidates:
            print(f"no senses of {lemma!r} in {args.lexicon}", file=sys.stderr)
            return 1
        print("\n".join(candidate_lines(candidates, lemma, args.pos)))
        return 0

    try:
        root = resolve_root(wordnet, args.root, args.pos)
    except RootError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"root: {root.id}  {root.definition()}")

    collected = collect(root, args.depth)

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
        "root": root.id,
        "synsets": synsets,
        "senses": senses,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(synsets)} synsets and {len(senses)} senses to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
