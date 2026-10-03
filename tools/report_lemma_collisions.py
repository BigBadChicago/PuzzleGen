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


def _hypernym_edges(paths: list[Path]) -> dict[str, tuple[str, ...]]:
    # Unioned across files, as the builder unions a category's parents. A
    # synset exported under two roots keeps only the hypernyms inside each root's
    # subtree, so letting the later file overwrite the earlier one dropped edges
    # and hid exactly the loops this report exists to show.
    edges: dict[str, tuple[str, ...]] = {}
    for path in paths:
        for synset in read_lexicon(path).get("synsets", ()):
            known = edges.get(synset["id"], ())
            edges[synset["id"]] = known + tuple(
                h for h in synset.get("hypernyms", ()) if h not in known
            )
    return edges


def _reaches(edges: dict[str, tuple[str, ...]], start: str, targets: set[str]) -> str | None:
    """The first of ``targets`` reachable by walking hypernyms from ``start``.

    WordNet's hypernym edges are acyclic upstream, so the walk ends on real
    data; the visited set below only keeps a malformed file from looping.
    """
    frontier = list(edges.get(start, ()))
    # A visited set costs nothing on the acyclic input WordNet guarantees, and
    # turns a malformed or hand-edited lexicon that does contain a loop into a
    # finished report instead of a process that never returns.
    seen: set[str] = set()
    while frontier:
        current = frontier.pop()
        if current in targets:
            return current
        if current in seen:
            continue
        seen.add(current)
        frontier.extend(edges.get(current, ()))
    return None


def category_collisions(paths: list[Path]) -> list[dict]:
    """Synsets that become one category because they share a first lemma.

    A category is identified by its canonical name, which is the synset's first
    lemma, so two synsets named alike are one category on import. Open English
    WordNet has two distinct "galley" synsets, a ship's kitchen and a rowed
    ship, and both are hypernyms of "monoreme". The import merges them, which
    is the identity rule working as designed, but it also means a child can
    inherit one parent twice and a category can carry a gloss from a sense
    nobody meant. Worth seeing before a build rather than during one.

    A third shape is fatal rather than merely worth seeing:
    ``self_ancestor_pairs`` names any pair in the group where one synset's own
    hypernym chain reaches the other. Once merged, that pair asserts a single
    category as its own parent; ``Category.build`` refuses this, and it is
    what a deep export can hit that a shallow one never reaches, because the
    two colliding synsets have to be far enough apart in the tree for one to
    be a genuine ancestor of the other rather than an unrelated sibling.
    """
    by_name: dict[str, dict[str, dict]] = {}
    for path in paths:
        document = read_lexicon(path)
        in_file = {row["id"]: row.get("name") or row["id"] for row in document.get("synsets", ())}
        for synset in document.get("synsets", ()):
            # Case sensitive, because the id is minted from the name as
            # written: "Cardigan" the corgi and "cardigan" the sweater are two
            # categories and never merge. Lowercasing here reported a
            # collision the graph does not have, and named a parent as lost
            # that was never at risk.
            name = synset.get("name") or ""
            if not name:
                continue
            by_name.setdefault(name, {})[synset["id"]] = {
                "id": synset["id"],
                "definition": synset.get("definition", ""),
                "file": path.name,
                "parents": sorted(
                    {in_file[h] for h in synset.get("hypernyms", ()) if h in in_file}
                ),
                "parent_ids": [h for h in synset.get("hypernyms", ()) if h in in_file],
                "parent_names": {
                    h: in_file[h] for h in synset.get("hypernyms", ()) if h in in_file
                },
            }

    edges = _hypernym_edges(paths)
    merged = []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        ids = set(group)
        children = []
        for path in paths:
            for synset in read_lexicon(path).get("synsets", ()):
                if len(ids & set(synset.get("hypernyms", ()))) > 1:
                    children.append(synset.get("name") or synset["id"])

        self_ancestor_pairs = []
        for descendant in sorted(ids):
            others = ids - {descendant}
            ancestor = _reaches(edges, descendant, others)
            if ancestor is not None:
                self_ancestor_pairs.append(
                    {"descendant": descendant, "ancestor": ancestor}
                )

        # Mirrors ``SnapshotBuilder._merge_category``: a category's parents are
        # the union of every same-named sense's parents, and the only parent
        # refused is one that would make the category its own ancestor, which
        # is the category itself or anything already below it. An earlier
        # version of this report claimed the first import fixes a category's
        # parents and listed every later sense's parents as lost. The builder
        # does not do that, so every "lost" line it printed was a false alarm,
        # and tests/unit/test_collision_report_matches_builder.py now fails if
        # the two ever disagree again.
        #
        # Order follows the files as given, which must be the order the build
        # imports them. Senses inside one file are ordered by the importer and
        # are not simulated; they are treated as already merged.
        order = [path.name for path in paths]
        first_file = min({g["file"] for g in group.values()}, key=order.index)
        merged_ids = {k for k, g in group.items() if g["file"] == first_file}
        kept = {
            name_of
            for k in merged_ids
            for name_of in group[k]["parent_names"].values()
        }
        refused: set[str] = set()
        for later_file in sorted(
            {g["file"] for g in group.values()} - {first_file}, key=order.index
        ):
            arriving = {k for k, g in group.items() if g["file"] == later_file}
            for k in sorted(arriving):
                for parent_id, parent_name in group[k]["parent_names"].items():
                    below = parent_id in ids or _reaches(edges, parent_id, merged_ids)
                    if below:
                        refused.add(parent_name)
                    else:
                        kept.add(parent_name)
            merged_ids |= arriving
        # A name both kept through one sense and refused through another is
        # kept: the builder only refuses an edge it has not already written.
        refused -= kept
        merged.append(
            {
                "name": name,
                "synsets": [group[k] for k in sorted(group)],
                "children_inheriting_it_twice": sorted(set(children)),
                "self_ancestor_pairs": self_ancestor_pairs,
                "first_file": first_file,
                "parents_kept": sorted(kept),
                "parents_lost": sorted(refused),
            }
        )
    return merged


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
    categories: list[dict] | None = None,
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
    losing = [c for c in (categories or ()) if c.get("parents_lost")]
    by_lemma = {c["name"].lower(): c for c in losing}
    overlay_losing = [
        {
            "lemma": lemma,
            "overlay_categories": cats,
            "kept": by_lemma[lemma.lower()]["parents_kept"],
            "lost": by_lemma[lemma.lower()]["parents_lost"],
        }
        for lemma, cats in sorted(members.items())
        if lemma.lower() in by_lemma
    ]

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
            "merged_categories": len(categories or ()),
            "merged_categories_with_double_inheritance": sum(
                1 for c in (categories or ()) if c["children_inheriting_it_twice"]
            ),
            "merged_categories_self_ancestor": sum(
                1 for c in (categories or ()) if c.get("self_ancestor_pairs")
            ),
            "merged_categories_losing_parents": len(losing),
            "overlay_words_losing_parents": len(overlay_losing),
        },
        "sense_histogram": {str(k): histogram[k] for k in sorted(histogram)},
        "polysemous": cut([u.as_json() for u in polysemous]),
        "cross_file": cut([u.as_json() for u in cross_file]),
        "overlay_ambiguous": cut(ambiguous_overlay),
        "overlay_missing": cut(missing_overlay),
        "merged_categories": cut(list(categories or ())),
        "overlay_words_losing_parents": overlay_losing,
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
        f"merged categories     : {counts['merged_categories']}"
        f" ({counts['merged_categories_with_double_inheritance']} inherited twice)",
        f"  losing a parent     : {counts['merged_categories_losing_parents']}"
        f" ({counts['overlay_words_losing_parents']} are overlay words)",
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
    if report.get("merged_categories"):
        lines.append("")
        lines.append("synsets that become one category (same first lemma):")
        for row in report["merged_categories"]:
            lines.append(f"  {row['name']}")
            for synset in row["synsets"]:
                lines.append(f"    {synset['id']}  {synset['definition'][:70]}")
            if row.get("parents_lost"):
                lines.append(
                    f"    parents refused (would make it its own ancestor): "
                    f"{', '.join(row['parents_lost'])}"
                    f" (kept: {', '.join(row['parents_kept'])})"
                )
            if row["children_inheriting_it_twice"]:
                lines.append(
                    "    inherited twice by: "
                    + ", ".join(row["children_inheriting_it_twice"])
                )
            for pair in row.get("self_ancestor_pairs", ()):
                lines.append(
                    f"    FATAL: {pair['descendant']} is a descendant of "
                    f"{pair['ancestor']} -- same name would make the merged "
                    f"category its own parent (normalizer now drops this "
                    f"edge automatically; shown so the responsible synset "
                    f"pair is visible rather than silently resolved)"
                )
    if report.get("overlay_words_losing_parents"):
        lines.append("")
        lines.append("overlay words whose second meaning had a parent refused in the merge:")
        for row in report["overlay_words_losing_parents"]:
            lines.append(
                f"  {row['lemma']:<16} kept: {', '.join(row['kept'])}; "
                f"lost: {', '.join(row['lost'])}"
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
    report = build_report(
        uses,
        members,
        limit=args.limit,
        categories=category_collisions(args.lexicon),
    )

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
