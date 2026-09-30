"""Which word stands for each child category in a sibling group.

A category imported from a hierarchy is one meaning, and its direct members are
that meaning's synonyms. A sibling group shows one tile per child, so something
has to choose which synonym that is. Two rules, in order:

1. When the query needs members that also belong to a second taxonomy, the
   word that carries it wins. The overlay exists to name the word whose other
   meaning is the hidden group, so the word a curator tagged is the word the
   player should see, whether or not WordNet lists it first.
2. Otherwise, or when no member carries it, the word bearing the category's own
   name, which is the first lemma the exporter chose.

Three constraints keep the choice safe:

* A word is a tile for at most one category. A word filed under two children
  of one parent (``horn`` under both ``cornet`` and ``French horn``) would
  otherwise appear twice in one group.
* A category's own-name word is reserved for it. A word that is one category's
  first lemma is never taken as another's tagged synonym, so tagging a
  synonym cannot cost a different category its tile.
* Ties are broken by name and id, never by iteration order, so two runs over
  one graph choose the same tiles.

Pure and dependency free so the content service and the coverage tool call the
same code. They used to carry two copies of the rule, and two copies of a rule
drift silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def choose_representatives(
    names: Mapping[str, str],
    members: Mapping[str, Sequence[tuple[str, str]]],
    carrying: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Category id to the entity id that stands for it.

    ``names`` maps a category id to its canonical name. ``members`` maps it to
    its direct members as ``(entity_id, entity_name)``. ``carrying`` holds the
    entity ids that belong to the second taxonomy, empty when the query asks
    for none. A category with no eligible word is left out rather than given a
    guess.
    """
    own: dict[str, str] = {}
    for category_id, entries in members.items():
        for entity_id, entity_name in sorted(entries):
            if entity_name == names[category_id]:
                own[category_id] = entity_id
                break

    reserved = frozenset(own.values())
    chosen: dict[str, str] = {}
    taken: set[str] = set()

    if carrying:
        for category_id in sorted(members, key=lambda c: (names[c], c)):
            options = sorted(
                (
                    entity_id
                    for entity_id, _ in members[category_id]
                    if entity_id in carrying
                    and entity_id not in taken
                    and (entity_id not in reserved or own.get(category_id) == entity_id)
                ),
                key=lambda e: (e != own.get(category_id), e),
            )
            if options:
                chosen[category_id] = options[0]
                taken.add(options[0])

    for category_id, entity_id in own.items():
        chosen.setdefault(category_id, entity_id)
    return chosen


__all__ = ["choose_representatives"]
