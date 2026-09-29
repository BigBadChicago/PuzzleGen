"""Provider records in, internal graph records out.

Two responsibilities that must not leak anywhere else:

* Identity. A provider key becomes an internal id exactly here. Because ids
  are derived from natural keys, two providers asserting the same entity
  converge on the same id and their provenance merges, which is what makes
  "one fact, many games" true across providers as well as across games.
* Governance. Every record leaves this module with provenance attached, a
  freshness class, a review date, and a status that is never ACTIVE. Import
  cannot publish content; only activation can, and activation is a separate,
  explicit step.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..core import ids
from ..core.types import (
    FreshnessClass,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
)
from ..graph.models import (
    Category,
    Entity,
    Fact,
    FactValue,
    Provenance,
    Relationship,
    Source,
)
from ..providers.base import (
    ProviderBundle,
    ProviderDescriptor,
    ProviderKey,
    RawCategory,
    RawEntity,
    RawFact,
    RawRelationship,
    order_categories,
    validate_bundle,
)
from .freshness import next_review_at


class NormalizationError(Exception):
    """A bundle could not be converted into valid internal records."""


@dataclass
class NormalizedBundle:
    """Internal records produced from one provider, ready to be written.

    Categories are ordered parents-first because the repository refuses a
    category whose parents are not yet stored, which is the cycle guard.
    """

    source: Source
    categories: list[Category] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    #: Provider key to internal id, for both categories and entities. Kept so
    #: a later re-verification can find the record a provider key produced.
    key_map: dict[ProviderKey, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "categories": len(self.categories),
            "entities": len(self.entities),
            "facts": len(self.facts),
            "relationships": len(self.relationships),
        }


class Normalizer:
    """Converts one provider bundle at a time."""

    def __init__(
        self,
        *,
        taxonomy: str = "default",
        now: dt.datetime | None = None,
        import_status: ReviewStatus = ReviewStatus.AUTO_VALIDATED,
        entity_identity: str = "lemma",
    ) -> None:
        if import_status is ReviewStatus.ACTIVE:
            raise ValueError(
                "import must never produce ACTIVE records; activation is a "
                "separate, explicit lifecycle step"
            )
        if entity_identity not in ("lemma", "sense"):
            raise ValueError("entity_identity must be 'lemma' or 'sense'")
        self._taxonomy = taxonomy
        self._now = now or dt.datetime.now(dt.timezone.utc)
        self._import_status = import_status
        # "lemma" merges every provider's assertions about the same word into
        # one entity, which is what makes facts reusable across games. "sense"
        # keeps polysemous words apart, which a vocabulary game needs and a
        # grouping game does not. The choice is per import, because the right
        # answer differs per provider: a curated file has no polysemy, a
        # WordNet export is full of it.
        self._entity_identity = entity_identity

    # -- entry point ------------------------------------------------------

    def normalize(self, bundle: ProviderBundle) -> NormalizedBundle:
        problems = validate_bundle(bundle)
        if problems:
            raise NormalizationError(
                f"provider {bundle.descriptor.name!r} produced an invalid bundle: "
                + "; ".join(problems[:10])
            )

        descriptor = bundle.descriptor
        source = self._source(descriptor)
        out = NormalizedBundle(source=source, warnings=list(bundle.warnings))

        built: dict[ProviderKey, Category] = {}
        # Several provider keys can resolve to one category, because identity
        # here is the canonical name: Open English WordNet has two distinct
        # "galley" synsets and both are hypernyms of "monoreme". Each key still
        # maps to the merged category, so references from either resolve, but
        # the bundle carries one record per id. Emitting two would put the same
        # id twice and let write order decide which gloss survived.
        emitted: dict[str, Category] = {}
        for raw in order_categories(bundle.categories):
            category = self._category(raw, source, descriptor, built)
            first = emitted.get(category.id)
            if first is None:
                emitted[category.id] = category
                out.categories.append(category)
            else:
                category = first
            built[raw.key] = category
            out.key_map[raw.key] = category.id

        for raw in sorted(bundle.entities, key=lambda e: e.key):
            entity = self._entity(raw, source, descriptor)
            out.entities.append(entity)
            out.key_map[raw.key] = entity.id

        for raw in bundle.facts:
            out.facts.append(self._fact(raw, source, descriptor, out.key_map))

        for raw in bundle.relationships:
            out.relationships.append(
                self._relationship(raw, source, descriptor, out.key_map)
            )

        # Category membership declared on an entity becomes a real
        # relationship, so it carries provenance like any other assertion
        # rather than being an untracked field.
        for raw in bundle.entities:
            for category_key in raw.category_keys:
                membership = RawRelationship(
                    subject_key=raw.key,
                    predicate="is_a",
                    object_key=category_key,
                    freshness_class=FreshnessClass.SLOW_CHANGING,
                    confidence=raw.confidence,
                    object_is_category=True,
                )
                candidate = self._relationship(
                    membership, source, descriptor, out.key_map
                )
                if not any(r.id == candidate.id for r in out.relationships):
                    out.relationships.append(candidate)

        return out

    # -- record builders --------------------------------------------------

    def _source(self, descriptor: ProviderDescriptor) -> Source:
        return Source(
            id=ids.for_source(descriptor.name, descriptor.version),
            name=descriptor.name,
            kind=descriptor.kind,
            version=descriptor.version,
            retrieved_at=descriptor.retrieved_at,
            url=descriptor.url,
            deprecated=descriptor.deprecated,
        )

    def _provenance(
        self,
        descriptor: ProviderDescriptor,
        source: Source,
        *,
        provenance_class: ProvenanceClass,
        confidence: float,
        source_ref: str | None,
        assertion_date: dt.date | None = None,
        reviewer: str | None = None,
        derived_from: Sequence[str] = (),
    ) -> Provenance:
        return Provenance(
            source_id=source.id,
            provenance_class=provenance_class,
            source_ref=source_ref,
            source_url=descriptor.url,
            retrieval_date=descriptor.retrieved_at,
            assertion_date=assertion_date,
            verification_date=self._now,
            verification_method=f"import from {descriptor.name} {descriptor.version}",
            confidence=confidence,
            reviewer=reviewer,
            derived_from=tuple(derived_from),
        )

    def _status_for(self, provenance_class: ProvenanceClass) -> ReviewStatus:
        """A judged assertion always waits for a human.

        Automated import may vouch for what a source said; it cannot vouch for
        an interpretation, which is precisely what JUDGED means.
        """
        if provenance_class is ProvenanceClass.JUDGED:
            return ReviewStatus.PENDING_REVIEW
        return self._import_status

    def _governance(
        self, freshness_class: FreshnessClass
    ) -> dict[str, dt.datetime | None]:
        return {
            "verified_at": self._now,
            "next_review_at": next_review_at(freshness_class, self._now),
        }

    def _category(
        self,
        raw: RawCategory,
        source: Source,
        descriptor: ProviderDescriptor,
        built: dict[ProviderKey, Category],
    ) -> Category:
        # Deduplicated by id, in first-seen order. A category is identified by
        # its name, so two source nodes whose first lemma is the same are one
        # category here by construction: Open English WordNet has two distinct
        # "galley" synsets, and both are hypernyms of "monoreme". Rejecting
        # that would be rejecting the identity rule's own consequence, and
        # keeping the duplicate would claim an edge twice.
        own_id = ids.for_category(raw.name, raw.lang, self._taxonomy)
        seen: dict[str, Category] = {}
        for key in raw.parent_keys:
            candidate = built[key]
            # A same-name ancestor, dropped rather than rejected. Name-based
            # merging means a synset and one of its own hypernyms can collapse
            # onto the same category id when they share a first lemma: a
            # narrower "plane" sense whose hypernym is a broader "plane" sense
            # a few hyponym levels up, both already merged into the single
            # "plane" category by the time this synset is processed. At depth
            # 3 the ancestor chain never ran long enough to hit this; depth 6
            # does. The edge would assert a category as its own parent, which
            # Category.build refuses outright, so it is simply not asserted:
            # the merged category already carries this synset's identity, and
            # its own real (non-colliding) ancestors, recorded when whichever
            # same-named sibling was processed first, still apply.
            if candidate.id == own_id:
                continue
            seen.setdefault(candidate.id, candidate)
        parents = tuple(seen.values())
        freshness = FreshnessClass.SLOW_CHANGING
        return Category.build(
            id=own_id,
            canonical_name=raw.name,
            parents=parents,
            lang=raw.lang,
            taxonomy=self._taxonomy,
            depth_override=raw.depth_hint if parents else None,
            gloss=raw.gloss,
            created_at=self._now,
            status=self._status_for(ProvenanceClass.SOURCED),
            freshness_class=freshness,
            confidence=0.95,
            provenance=(
                self._provenance(
                    descriptor,
                    source,
                    provenance_class=ProvenanceClass.SOURCED,
                    confidence=0.95,
                    source_ref=raw.key,
                ),
            ),
            **self._governance(freshness),
        )

    def _entity(
        self, raw: RawEntity, source: Source, descriptor: ProviderDescriptor
    ) -> Entity:
        freshness = FreshnessClass.STATIC
        entity_id = (
            ids.for_entity(raw.name, raw.lang)
            if self._entity_identity == "lemma"
            else ids.mint(ids.ENTITY, raw.name, raw.lang, raw.key)
        )
        return Entity(
            id=entity_id,
            canonical_name=raw.name,
            lang=raw.lang,
            aliases=tuple(a for a in dict.fromkeys(raw.aliases) if a != raw.name),
            definition=raw.definition,
            created_at=self._now,
            updated_at=self._now,
            status=self._status_for(ProvenanceClass.SOURCED),
            freshness_class=freshness,
            confidence=raw.confidence,
            provenance=(
                self._provenance(
                    descriptor,
                    source,
                    provenance_class=ProvenanceClass.SOURCED,
                    confidence=raw.confidence,
                    source_ref=raw.key,
                ),
            ),
            **self._governance(freshness),
        )

    def _fact(
        self,
        raw: RawFact,
        source: Source,
        descriptor: ProviderDescriptor,
        key_map: dict[ProviderKey, str],
    ) -> Fact:
        subject_id = key_map.get(raw.subject_key)
        if subject_id is None:
            raise NormalizationError(f"unmapped fact subject {raw.subject_key!r}")

        derived = tuple(
            key_map[k] for k in raw.derived_from_keys if k in key_map
        )
        if raw.provenance_class is ProvenanceClass.COMPUTED and not derived:
            raise NormalizationError(
                f"computed fact {raw.predicate!r} on {raw.subject_key!r} "
                "derives from records outside this bundle"
            )

        return Fact.build(
            subject_id=subject_id,
            predicate=raw.predicate,
            value=FactValue.of(raw.value, unit=raw.unit),
            lang=raw.lang,
            created_at=self._now,
            status=self._status_for(raw.provenance_class),
            freshness_class=raw.freshness_class,
            confidence=raw.confidence,
            provenance=(
                self._provenance(
                    descriptor,
                    source,
                    provenance_class=raw.provenance_class,
                    confidence=raw.confidence,
                    source_ref=f"{raw.subject_key}/{raw.predicate}",
                    assertion_date=raw.assertion_date,
                    reviewer=raw.reviewer,
                    derived_from=derived,
                ),
            ),
            **self._governance(raw.freshness_class),
        )

    def _relationship(
        self,
        raw: RawRelationship,
        source: Source,
        descriptor: ProviderDescriptor,
        key_map: dict[ProviderKey, str],
    ) -> Relationship:
        subject_id = key_map.get(raw.subject_key)
        object_id = key_map.get(raw.object_key)
        if subject_id is None or object_id is None:
            raise NormalizationError(
                f"unmapped relationship {raw.subject_key!r} -> {raw.object_key!r}"
            )

        derived = tuple(key_map[k] for k in raw.derived_from_keys if k in key_map)
        if raw.provenance_class is ProvenanceClass.COMPUTED and not derived:
            raise NormalizationError(
                f"computed relationship {raw.predicate!r} derives from records "
                "outside this bundle"
            )

        return Relationship.build(
            subject_id=subject_id,
            predicate=raw.predicate,
            object_id=object_id,
            symmetric=raw.symmetric,
            created_at=self._now,
            status=self._status_for(raw.provenance_class),
            freshness_class=raw.freshness_class,
            confidence=raw.confidence,
            provenance=(
                self._provenance(
                    descriptor,
                    source,
                    provenance_class=raw.provenance_class,
                    confidence=raw.confidence,
                    source_ref=f"{raw.subject_key}/{raw.predicate}/{raw.object_key}",
                    reviewer=raw.reviewer,
                    derived_from=derived,
                ),
            ),
            **self._governance(raw.freshness_class),
        )


def merge_governed(existing, incoming):
    """Combine two versions of the same record from different providers.

    Two providers independently asserting the same entity is the good case,
    not a conflict: their provenance accumulates, which is what raises
    confidence in a fact rather than duplicating it. The merged record keeps
    the earliest creation, the latest update, the union of aliases, and the
    highest confidence any single provenance entry supports.
    """
    if existing.id != incoming.id:
        raise NormalizationError("cannot merge records with different ids")
    if type(existing) is not type(incoming):
        raise NormalizationError("cannot merge records of different types")

    provenance = list(existing.provenance)
    seen = {(p.source_id, p.source_ref) for p in provenance}
    for entry in incoming.provenance:
        if (entry.source_id, entry.source_ref) not in seen:
            provenance.append(entry)
            seen.add((entry.source_id, entry.source_ref))

    updates = {
        "provenance": tuple(provenance),
        "created_at": min(existing.created_at, incoming.created_at),
        "updated_at": max(existing.updated_at, incoming.updated_at),
        "confidence": max(
            existing.confidence,
            incoming.confidence,
            key=lambda c: c,
        ),
    }

    if isinstance(existing, Entity):
        aliases = list(dict.fromkeys((*existing.aliases, *incoming.aliases)))
        updates["aliases"] = tuple(a for a in aliases if a != existing.canonical_name)
        updates["definition"] = existing.definition or incoming.definition

    # A record is only as trustworthy as its weakest surviving status: if one
    # provider's copy needs human review, the merged record does too.
    order = [
        ReviewStatus.REJECTED,
        ReviewStatus.QUARANTINED,
        ReviewStatus.RETIRED,
        ReviewStatus.CANDIDATE,
        ReviewStatus.PENDING_REVIEW,
        ReviewStatus.AUTO_VALIDATED,
        ReviewStatus.APPROVED,
        ReviewStatus.STALE,
        ReviewStatus.ACTIVE,
    ]
    updates["status"] = min(
        (existing.status, incoming.status), key=lambda s: order.index(s)
    )

    ceiling = max(p.confidence for p in provenance)
    updates["confidence"] = min(updates["confidence"], ceiling)

    return existing.model_copy(update=updates)
