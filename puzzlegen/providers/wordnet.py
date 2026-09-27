"""A provider over an exported WordNet lexicon.

The engine must run with no network and no heavy dependencies, so it does not
import the ``wn`` package at all. Instead a separate exporter (see
``tools/export_wordnet.py``) runs once, with ``wn`` installed, and writes a
plain JSON lexicon that this provider reads. Three things follow:

* The engine's dependency list stays small and its install stays fast.
* The exported file is a versioned artifact, so two machines importing the
  same lexicon file produce the same snapshot.
* WordNet's hypernym hierarchy is maintained acyclic upstream and the exporter
  records each synset's minimum and maximum depth, so those two integers are
  copied rather than recomputed. That is why :class:`RawCategory` carries a
  ``depth_hint``.

The exported format is one JSON object with ``synsets`` and ``senses``. A
synset becomes a category; a sense (a word in a synset) becomes an entity that
is a member of that category.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..core.types import FreshnessClass, ProvenanceClass, SourceKind
from .base import (
    ProviderBundle,
    ProviderDescriptor,
    ProviderError,
    ProviderFreshness,
    ProviderKey,
    RawCategory,
    RawEntity,
    RawRelationship,
)

LEXICON_SCHEMA = 1

#: Predicate used for synset membership. Named for what it asserts rather than
#: for WordNet's own terminology, because the graph is provider-independent.
MEMBER_PREDICATE = "is_a"


class WordNetLexiconProvider:
    """Reads an exported WordNet lexicon file."""

    def __init__(
        self,
        path: str | Path,
        *,
        taxonomy: str = "wordnet",
        now: dt.datetime | None = None,
    ) -> None:
        self._path = Path(path)
        self._taxonomy = taxonomy
        self._now = now or dt.datetime.now(dt.timezone.utc)
        self._document: dict[str, Any] | None = None

    @property
    def taxonomy(self) -> str:
        return self._taxonomy

    def _read(self) -> dict[str, Any]:
        if self._document is not None:
            return self._document
        if not self._path.exists():
            raise ProviderError(f"wordnet lexicon export not found: {self._path}")
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"wordnet lexicon is not valid JSON: {exc}") from exc
        schema = document.get("lexicon_schema", LEXICON_SCHEMA)
        if schema != LEXICON_SCHEMA:
            raise ProviderError(
                f"wordnet lexicon uses schema v{schema}, expected v{LEXICON_SCHEMA}"
            )
        for required in ("lexicon", "version", "synsets", "senses"):
            if required not in document:
                raise ProviderError(f"wordnet lexicon is missing {required!r}")
        self._document = document
        return document

    # -- protocol ---------------------------------------------------------

    def describe(self) -> ProviderDescriptor:
        document = self._read()
        return ProviderDescriptor(
            name=f"wordnet:{document['lexicon']}",
            kind=SourceKind.LEXICAL,
            version=str(document["version"]),
            retrieved_at=self._now,
            url=document.get("url"),
            upstream_last_updated=_maybe_date(document.get("exported")),
        )

    def freshness_status(self) -> ProviderFreshness:
        """WordNet moves slowly, so age alone is not a failure.

        A lexicon release stays valid for years; what matters is whether the
        export is old enough that the curator should confirm a newer release
        has not changed the senses in play.
        """
        document = self._read()
        exported = _maybe_date(document.get("exported"))
        if exported is None:
            return ProviderFreshness(
                healthy=True,
                checked_at=self._now,
                detail="lexicon export declares no date",
            )
        age = (self._now.date() - exported).days
        return ProviderFreshness(
            healthy=age <= 1095,
            checked_at=self._now,
            age_days=age,
            detail=f"lexicon exported {exported.isoformat()}",
        )

    def load(self) -> ProviderBundle:
        document = self._read()
        categories: list[RawCategory] = []
        entities: list[RawEntity] = []
        relationships: list[RawRelationship] = []

        known_synsets = {row["id"] for row in document["synsets"]}

        for row in document["synsets"]:
            # Hypernyms outside the export are dropped rather than dangling.
            # A partial export is normal: exporting a subtree of WordNet is
            # how a snapshot stays small enough to reason about.
            parents = tuple(
                sorted(h for h in row.get("hypernyms", []) if h in known_synsets)
            )
            depth_hint = (
                (int(row["min_depth"]), int(row["max_depth"]))
                if parents and "min_depth" in row and "max_depth" in row
                else None
            )
            categories.append(
                RawCategory(
                    key=row["id"],
                    name=row.get("name") or row["id"],
                    parent_keys=parents,
                    gloss=row.get("definition"),
                    lang=row.get("lang", "en"),
                    depth_hint=depth_hint,
                )
            )

        for row in document["senses"]:
            synset_id = row["synset"]
            if synset_id not in known_synsets:
                continue
            entities.append(
                RawEntity(
                    key=row["id"],
                    name=row["lemma"],
                    aliases=tuple(row.get("forms", ())),
                    definition=row.get("definition"),
                    lang=row.get("lang", "en"),
                    category_keys=(synset_id,),
                    confidence=float(row.get("confidence", 0.95)),
                )
            )
            relationships.append(
                RawRelationship(
                    subject_key=row["id"],
                    predicate=MEMBER_PREDICATE,
                    object_key=synset_id,
                    freshness_class=FreshnessClass.SLOW_CHANGING,
                    provenance_class=ProvenanceClass.SOURCED,
                    confidence=float(row.get("confidence", 0.95)),
                    object_is_category=True,
                )
            )

        return ProviderBundle(
            descriptor=self.describe(),
            categories=tuple(categories),
            entities=tuple(entities),
            facts=(),
            relationships=tuple(relationships),
        )

    def fetch_entity(self, key: ProviderKey) -> RawEntity | None:
        for row in self._read()["senses"]:
            if row["id"] == key:
                return RawEntity(
                    key=row["id"],
                    name=row["lemma"],
                    aliases=tuple(row.get("forms", ())),
                    definition=row.get("definition"),
                    lang=row.get("lang", "en"),
                    category_keys=(row["synset"],),
                )
        return None


def _maybe_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ProviderError(f"not an ISO date: {value!r}") from exc
