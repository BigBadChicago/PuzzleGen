"""The content service.

Everything a game can ask about content is answered here, and every answer
passes the same gates on the way out: status, confidence, freshness, policy,
then whatever semantic constraints the query declared. Gates are applied even
when the query should already guarantee the result, because defence in depth
is the stated requirement: a precise query is a performance property, not a
correctness proof.

This module is the only place above storage that touches repositories.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Iterable, Sequence
from dataclasses import replace

from ..core.errors import RejectionReason
from ..core.types import DifficultyBand, FrequencyBand, ReviewStatus
from ..graph.models import Entity, Fact, Relationship, SnapshotMeta
from ..graph.repositories import GraphRepositories
from ..providers.frequency import BAND_ORDER, band_spread
from .freshness import evaluate as evaluate_freshness
from .policy import PolicyService
from .query import (
    ContentQuery,
    ContentResult,
    EntityView,
    FactView,
    GroupView,
    Operation,
    PathView,
    RelationshipView,
)
from .similarity import (
    DEFAULT_STRATEGY,
    GroupSimilarity,
    SimilarityStrategy,
    TaxonomyIndex,
    build_strategy,
    centroid_distances,
    group_similarity,
)

#: Predicate that expresses category membership. Fixed here rather than
#: configurable, because the whole taxonomy layer is built on it.
MEMBERSHIP_PREDICATE = "is_a"

#: Ceiling on combinations examined by one group query. Without it a broad
#: category makes generation unbounded; with it, an exhausted search reports
#: INSUFFICIENT_CANDIDATES rather than hanging.
MAX_COMBINATIONS_EXAMINED = 20_000

#: Longest relationship chain a path query will search. Chains beyond this are
#: not puzzle material; the limit exists to bound the search, not to express a
#: game rule.
MAX_PATH_DEPTH = 8

#: Difficulty bands map onto frequency bands. Crude by design: real difficulty
#: is measured per puzzle by the game's own model, and this exists only so a
#: game can ask for "harder vocabulary" without inventing its own scale.
DIFFICULTY_TO_FREQUENCY: dict[DifficultyBand, tuple[FrequencyBand, ...]] = {
    DifficultyBand.EASY: (FrequencyBand.VERY_COMMON, FrequencyBand.COMMON),
    DifficultyBand.MEDIUM: (FrequencyBand.COMMON, FrequencyBand.UNCOMMON),
    DifficultyBand.HARD: (FrequencyBand.UNCOMMON, FrequencyBand.RARE),
    DifficultyBand.EXPERT: (FrequencyBand.RARE, FrequencyBand.OBSCURE),
}


class ContentService:
    """Answers semantic content requests against one snapshot."""

    def __init__(
        self,
        repos: GraphRepositories,
        *,
        policy: PolicyService | None = None,
        snapshot: SnapshotMeta | None = None,
        now: dt.datetime | None = None,
    ) -> None:
        self._repos = repos
        self._snapshot = snapshot
        self._now = now or dt.datetime.now(dt.timezone.utc)
        self._taxonomies: dict[str, TaxonomyIndex] = {}
        self._policy = policy or PolicyService(taxonomy=self.taxonomy())
        if policy is not None and getattr(policy, "_taxonomy", None) is None:
            # A policy built before the index exists still needs subtree
            # awareness, or a blocked category would not block its children.
            policy._taxonomy = self.taxonomy()  # noqa: SLF001

    # -- shared indexes ---------------------------------------------------

    def taxonomy(self, name: str = "default") -> TaxonomyIndex:
        if name not in self._taxonomies:
            self._taxonomies[name] = TaxonomyIndex(
                c for c in self._repos.categories.iter_all() if c.taxonomy == name
            )
        return self._taxonomies[name]

    @property
    def snapshot(self) -> SnapshotMeta | None:
        return self._snapshot

    @property
    def policy(self) -> PolicyService:
        return self._policy

    def _vectors(self, entity_ids: Sequence[str]) -> dict[str, tuple[float, ...]]:
        if not self._snapshot or not self._snapshot.embedding_model:
            return {}
        records = self._repos.embeddings.for_model(
            self._snapshot.embedding_model,
            self._snapshot.embedding_model_version or "",
            entity_ids,
        )
        return {eid: record.vector for eid, record in records.items()}

    # -- gates ------------------------------------------------------------

    def _usable(self, record) -> str | None:
        """A rejection reason, or ``None`` when the record may be used."""
        if record.status is not ReviewStatus.ACTIVE:
            return RejectionReason.LOW_CONFIDENCE.value if False else (
                f"status {record.status}"
            )
        if not record.provenance:
            return RejectionReason.MISSING_PROVENANCE.value
        for entry in record.provenance:
            source = self._repos.sources.get(entry.source_id)
            if source is not None and source.deprecated:
                return RejectionReason.DEPRECATED_SOURCE.value
        return None

    def _fresh(self, record) -> str | None:
        verdict = evaluate_freshness(
            record.freshness_class,
            record.verified_at,
            record.next_review_at,
            self._now,
        )
        if verdict.stale:
            return RejectionReason.STALE_FACT.value
        return None

    def _passes(
        self,
        record,
        query: ContentQuery,
        game_id: str | None,
        category_ids: Sequence[str],
        rejected: dict[str, int],
    ) -> bool:
        def refuse(reason: str) -> bool:
            rejected[reason] = rejected.get(reason, 0) + 1
            return False

        reason = self._usable(record)
        if reason:
            return refuse(reason)

        if query.fresh_only:
            reason = self._fresh(record)
            if reason:
                return refuse(reason)

        floor = query.minimum_confidence
        if floor is not None and record.confidence < floor:
            return refuse(RejectionReason.LOW_CONFIDENCE.value)

        verdict = self._policy.is_eligible(
            record, game_id=game_id, category_ids=category_ids
        )
        if not verdict.allowed:
            return refuse(f"{RejectionReason.POLICY_REJECTED.value}: {verdict.reason}")

        return True

    # -- entity assembly --------------------------------------------------

    def _membership_ids(self, entity_id: str) -> tuple[str, ...]:
        edges = self._repos.relationships.by_subject(entity_id, MEMBERSHIP_PREDICATE)
        return tuple(
            sorted(
                edge.object_id
                for edge in edges
                if edge.status is ReviewStatus.ACTIVE
            )
        )

    def _facts_of(
        self, entity_id: str, query: ContentQuery, game_id: str | None
    ) -> tuple[FactView, ...]:
        views: list[FactView] = []
        discard: dict[str, int] = {}
        for fact in sorted(self._repos.facts.by_subject(entity_id), key=lambda f: f.id):
            if not self._passes(fact, query, game_id, (), discard):
                continue
            views.append(
                FactView(
                    fact_id=fact.id,
                    predicate=fact.predicate,
                    value=fact.value.as_python(),
                    unit=fact.value.unit,
                )
            )
        return tuple(views)

    def _view(
        self,
        entity: Entity,
        query: ContentQuery,
        game_id: str | None,
        *,
        taxonomy: TaxonomyIndex | None = None,
    ) -> EntityView:
        index = taxonomy or self.taxonomy(query.taxonomy)
        direct = self._membership_ids(entity.id)
        # Inherited types are included, not just direct membership: a tiger is
        # a feline and a mammal and an animal, and a game asking what an
        # entity is should not have to walk the taxonomy itself. Ancestors
        # also belong in the dependency list, because retiring "mammal"
        # invalidates every puzzle that grouped its descendants.
        inherited: set[str] = set()
        for category_id in direct:
            if category_id in index:
                inherited.add(category_id)
                inherited |= index.ancestors(category_id)
        # Most specific first: a game showing one type should show the
        # informative one, not the taxonomy root.
        ordered = sorted(inherited, key=lambda cid: (-index.depth(cid), cid))
        names = tuple(
            (index.get(cid).canonical_name if index.get(cid) else cid)
            for cid in ordered
        )
        facts = self._facts_of(entity.id, query, game_id)
        return EntityView(
            entity_id=entity.id,
            name=entity.canonical_name,
            aliases=entity.aliases,
            definition=entity.definition,
            types=names,
            type_ids=tuple(ordered),
            properties={f.predicate: f.value for f in facts},
            facts=facts,
            frequency_band=entity.frequency_band,
        )

    # -- candidate pools --------------------------------------------------

    def _resolve_category(self, name: str | None, taxonomy: str) -> str | None:
        if name is None:
            return None
        if name.startswith("category:"):
            return name if name in self.taxonomy(taxonomy) else None
        matches = [
            c
            for c in self._repos.categories.by_name(name)
            if c.taxonomy == taxonomy and not c.retired
        ]
        return matches[0].id if matches else None

    def _category_scope(
        self, category_id: str, depth: int | None, taxonomy: str
    ) -> frozenset[str]:
        """The category and, when a depth is given, its subtree to that depth."""
        index = self.taxonomy(taxonomy)
        if depth is None:
            return frozenset({category_id}) | index.descendants(category_id)
        base = index.depth(category_id)
        return frozenset(
            {category_id}
            | {
                cid
                for cid in index.descendants(category_id)
                if index.depth(cid) - base <= depth
            }
        )

    def _pool(
        self, query: ContentQuery, game_id: str | None, rejected: dict[str, int]
    ) -> tuple[list[Entity], dict[str, tuple[str, ...]]]:
        """Active entities matching the query's structural constraints."""
        scope: frozenset[str] | None = None
        if query.category is not None:
            category_id = self._resolve_category(query.category, query.taxonomy)
            if category_id is None:
                return [], {}
            scope = self._category_scope(
                category_id, query.taxonomy_depth, query.taxonomy
            )

        bands = self._requested_bands(query)
        memberships: dict[str, tuple[str, ...]] = {}
        pool: list[Entity] = []

        for entity in self._repos.entities.active():
            if entity.id in query.exclude_ids:
                continue
            if entity.lang != query.lang:
                continue
            if bands is not None and entity.frequency_band not in bands:
                rejected[RejectionReason.WORD_FREQUENCY_MISMATCH.value] = (
                    rejected.get(RejectionReason.WORD_FREQUENCY_MISMATCH.value, 0) + 1
                )
                continue

            categories = self._membership_ids(entity.id)
            if scope is not None and not (set(categories) & scope):
                continue
            if not self._passes(entity, query, game_id, categories, rejected):
                continue

            memberships[entity.id] = categories
            pool.append(entity)

        pool.sort(key=lambda e: e.id)
        return pool, memberships

    def _requested_bands(self, query: ContentQuery) -> tuple[FrequencyBand, ...] | None:
        if query.frequency_band is not None:
            return (query.frequency_band,)
        if query.difficulty_band is not None:
            return DIFFICULTY_TO_FREQUENCY[query.difficulty_band]
        return None

    def _strategy(self, query: ContentQuery) -> SimilarityStrategy:
        return build_strategy(
            query.similarity_strategy or DEFAULT_STRATEGY,
            self.taxonomy(query.taxonomy),
        )

    # -- dispatch ---------------------------------------------------------

    def execute(
        self, query: ContentQuery, *, game_id: str | None = None
    ) -> ContentResult:
        handler = {
            Operation.FIND_ENTITIES: self._find_entities,
            Operation.FIND_CATEGORY_MEMBERS: self._find_entities,
            Operation.FIND_ENTITIES_BY_FREQUENCY: self._find_entities,
            Operation.FIND_ENTITIES_BY_DIFFICULTY: self._find_entities,
            Operation.FIND_SIBLINGS: self._find_siblings,
            Operation.FIND_RELATED_ENTITIES: self._find_related,
            Operation.FIND_PAIRS: self._find_pairs,
            Operation.FIND_GROUPS: self._find_groups,
            Operation.FIND_INTERSECTING_GROUPS: self._find_intersecting_groups,
            Operation.FIND_RELATIONSHIP_PATTERNS: self._find_paths,
        }[query.operation]
        return handler(query, game_id)

    # -- operations -------------------------------------------------------

    def _find_entities(
        self, query: ContentQuery, game_id: str | None
    ) -> ContentResult:
        rejected: dict[str, int] = {}
        pool, _ = self._pool(query, game_id, rejected)
        index = self.taxonomy(query.taxonomy)

        if query.predicate is not None:
            pool = [e for e in pool if self._asserts(e.id, query)]

        views = tuple(
            self._view(entity, query, game_id, taxonomy=index)
            for entity in pool[: query.limit]
        )
        return self._finish(query, views=views, examined=len(pool), rejected=rejected)

    def _asserts(self, entity_id: str, query: ContentQuery) -> bool:
        facts = self._repos.facts.by_subject_predicate(entity_id, query.predicate or "")
        usable = [f for f in facts if f.status is ReviewStatus.ACTIVE]
        if query.predicate_value is None:
            return bool(usable)
        return any(f.value.as_python() == query.predicate_value for f in usable)

    def _find_siblings(
        self, query: ContentQuery, game_id: str | None
    ) -> ContentResult:
        """Entities sharing an immediate category, filtered by similarity.

        Preferred over retrieving a broad category and cleaning up afterwards:
        the scope is narrowed at query time, and the similarity gate still
        runs, which is the defence-in-depth the design requires.
        """
        rejected: dict[str, int] = {}
        pool, memberships = self._pool(
            replace(query, taxonomy_depth=query.taxonomy_depth or 0), game_id, rejected
        )
        if not pool:
            return self._finish(query, rejected=rejected, examined=0)

        index = self.taxonomy(query.taxonomy)
        strategy = self._strategy(query)
        anchor = self._resolve_category(query.category, query.taxonomy)

        kept: list[Entity] = []
        for entity in pool:
            categories = [c for c in memberships[entity.id] if c in index]
            if not categories:
                continue
            if anchor is not None and query.minimum_similarity is not None:
                best = max(
                    strategy.similarity(cid, anchor).score for cid in categories
                )
                if best < query.minimum_similarity:
                    rejected[RejectionReason.SEMANTIC_DISTANCE_TOO_HIGH.value] = (
                        rejected.get(
                            RejectionReason.SEMANTIC_DISTANCE_TOO_HIGH.value, 0
                        )
                        + 1
                    )
                    continue
            kept.append(entity)

        views = tuple(
            self._view(entity, query, game_id, taxonomy=index)
            for entity in kept[: query.limit]
        )
        return self._finish(query, views=views, examined=len(pool), rejected=rejected)

    def _find_related(
        self, query: ContentQuery, game_id: str | None
    ) -> ContentResult:
        """Entities one relationship hop from a seed."""
        rejected: dict[str, int] = {}
        seeds = [e for e in self._repos.entities.active() if e.id in query.exclude_ids]
        anchor_ids = (
            {query.category} if query.category and query.category.startswith("entity:")
            else set()
        )
        if not anchor_ids and query.category:
            matches = self._repos.entities.by_name(query.category, query.lang)
            anchor_ids = {m.id for m in matches}
        if not anchor_ids:
            return self._finish(query, rejected=rejected, examined=0)

        index = self.taxonomy(query.taxonomy)
        neighbours: dict[str, Relationship] = {}
        for anchor in sorted(anchor_ids):
            for edge in self._repos.relationships.touching(anchor):
                if edge.predicate == MEMBERSHIP_PREDICATE:
                    continue
                if query.relationship_type and edge.predicate != query.relationship_type:
                    continue
                if not self._passes(edge, query, game_id, (), rejected):
                    continue
                other = edge.object_id if edge.subject_id == anchor else edge.subject_id
                if other in anchor_ids or other in query.exclude_ids:
                    continue
                neighbours.setdefault(other, edge)

        views: list[EntityView] = []
        relationships: list[RelationshipView] = []
        for entity_id in sorted(neighbours)[: query.limit]:
            entity = self._repos.entities.get(entity_id)
            if entity is None:
                continue
            if not self._passes(
                entity, query, game_id, self._membership_ids(entity_id), rejected
            ):
                continue
            views.append(self._view(entity, query, game_id, taxonomy=index))
            edge = neighbours[entity_id]
            relationships.append(
                RelationshipView(
                    relationship_id=edge.id,
                    subject_id=edge.subject_id,
                    predicate=edge.predicate,
                    object_id=edge.object_id,
                    symmetric=edge.symmetric,
                )
            )

        return self._finish(
            query,
            views=tuple(views),
            relationships=tuple(relationships),
            examined=len(neighbours),
            rejected=rejected,
        )

    def _find_pairs(self, query: ContentQuery, game_id: str | None) -> ContentResult:
        """Two-member groups, so pair and group queries share one gate path."""
        return self._find_groups(replace(query, group_size=2), game_id)

    def _find_groups(self, query: ContentQuery, game_id: str | None) -> ContentResult:
        rejected: dict[str, int] = {}
        size = query.group_size or 4
        pool, memberships = self._pool(query, game_id, rejected)

        minimum = query.minimum_candidate_count or size
        if len(pool) < minimum:
            rejected[RejectionReason.INSUFFICIENT_CANDIDATES.value] = 1
            return self._finish(
                query,
                rejected=rejected,
                examined=len(pool),
                satisfied=False,
                detail=f"{len(pool)} candidates, {minimum} required",
            )

        index = self.taxonomy(query.taxonomy)
        strategy = self._strategy(query)
        vectors = self._vectors([e.id for e in pool])
        views = {
            e.id: self._view(e, query, game_id, taxonomy=index) for e in pool
        }

        groups: list[GroupView] = []
        examined = 0
        for combination in itertools.combinations(pool, size):
            examined += 1
            if examined > MAX_COMBINATIONS_EXAMINED:
                rejected["COMBINATION_BUDGET_EXHAUSTED"] = 1
                break
            group = self._assess_group(
                combination, views, memberships, strategy, vectors, query, rejected, index
            )
            if group is not None:
                groups.append(group)
                if len(groups) >= query.limit:
                    break

        return self._finish(
            query,
            groups=tuple(groups),
            examined=examined,
            rejected=rejected,
            satisfied=bool(groups),
        )

    def _assess_group(
        self,
        members: Sequence[Entity],
        views: dict[str, EntityView],
        memberships: dict[str, tuple[str, ...]],
        strategy: SimilarityStrategy,
        vectors: dict[str, tuple[float, ...]],
        query: ContentQuery,
        rejected: dict[str, int],
        index: TaxonomyIndex,
    ) -> GroupView | None:
        def refuse(reason: RejectionReason) -> None:
            rejected[reason.value] = rejected.get(reason.value, 0) + 1

        member_ids = [e.id for e in members]

        shared = set(memberships[member_ids[0]])
        for entity_id in member_ids[1:]:
            shared &= set(memberships[entity_id])
        shared &= set(index._by_id)  # noqa: SLF001
        if not shared:
            refuse(RejectionReason.SEMANTIC_DISTANCE_TOO_HIGH)
            return None
        # The most specific shared category is what the group is "about".
        shared_id = max(sorted(shared), key=lambda cid: index.depth(cid))

        # Frequency fairness before the expensive similarity work.
        bands = [
            views[eid].frequency_band for eid in member_ids
            if views[eid].frequency_band is not None
        ]
        spread = band_spread(bands) if len(bands) == len(member_ids) else 0
        if (
            query.maximum_frequency_spread is not None
            and spread > query.maximum_frequency_spread
        ):
            refuse(RejectionReason.FREQUENCY_SPREAD_TOO_WIDE)
            return None

        primary_categories = [
            max(
                (c for c in memberships[eid] if c in index),
                key=lambda cid: (index.depth(cid), cid),
            )
            for eid in member_ids
        ]
        stats: GroupSimilarity = group_similarity(strategy, primary_categories)

        if query.minimum_similarity is not None and stats.minimum < query.minimum_similarity:
            refuse(RejectionReason.SEMANTIC_DISTANCE_TOO_HIGH)
            return None
        if query.maximum_similarity is not None and stats.maximum > query.maximum_similarity:
            refuse(RejectionReason.SEMANTIC_DISTANCE_TOO_LOW)
            return None
        if (
            query.maximum_similarity_spread is not None
            and stats.spread > query.maximum_similarity_spread
        ):
            refuse(RejectionReason.SEMANTIC_SPREAD_TOO_WIDE)
            return None

        distances = centroid_distances(vectors, member_ids)
        if query.minimum_centroid_similarity is not None and distances:
            if min(distances.values()) < query.minimum_centroid_similarity:
                refuse(RejectionReason.EMBEDDING_OUTLIER)
                return None

        category = index.get(shared_id)
        return GroupView(
            members=tuple(views[eid] for eid in member_ids),
            shared_category=category.canonical_name if category else None,
            shared_category_id=shared_id,
            similarity_strategy=stats.strategy,
            mean_similarity=stats.mean,
            minimum_similarity=stats.minimum,
            similarity_spread=stats.spread,
            centroid_similarity=distances,
            frequency_spread=spread,
        )

    def _find_intersecting_groups(
        self, query: ContentQuery, game_id: str | None
    ) -> ContentResult:
        """Groups sharing a category and a second axis simultaneously.

        The operation that makes a deliberate overlap constructible: a group
        whose members share both a taxonomic category and a property value can
        legitimately belong to two different groupings at once, which is the
        raw material for a puzzle whose unfairness has to be proved absent.
        """
        base = self._find_groups(query, game_id)
        if not base.groups:
            return replace(base, operation=query.operation)

        rejected = dict(base.rejected)
        secondary_id = (
            self._resolve_category(query.secondary_category, query.taxonomy)
            if query.secondary_category
            else None
        )

        kept: list[GroupView] = []
        for group in base.groups:
            label = self._secondary_axis(group, query, secondary_id)
            if label is None:
                rejected["NO_SECOND_AXIS"] = rejected.get("NO_SECOND_AXIS", 0) + 1
                continue
            kept.append(
                replace(
                    group,
                    secondary_category=label[0],
                    secondary_category_id=label[1],
                )
            )

        return ContentResult(
            operation=query.operation,
            groups=tuple(kept[: query.limit]),
            examined=base.examined,
            rejected=rejected,
            satisfied=bool(kept),
        )

    def _secondary_axis(
        self, group: GroupView, query: ContentQuery, secondary_id: str | None
    ) -> tuple[str, str | None] | None:
        """The second thing a group's members all share, if any."""
        if query.secondary_predicate:
            values = {
                member.properties.get(query.secondary_predicate)
                for member in group.members
            }
            if len(values) == 1 and None not in values:
                return (f"{query.secondary_predicate}={values.pop()}", None)
            return None

        if secondary_id is not None:
            if all(secondary_id in member.type_ids for member in group.members):
                index = self.taxonomy(query.taxonomy)
                category = index.get(secondary_id)
                return (
                    category.canonical_name if category else secondary_id,
                    secondary_id,
                )
            return None

        # No axis declared: any shared category other than the primary one.
        common = set(group.members[0].type_ids)
        for member in group.members[1:]:
            common &= set(member.type_ids)
        common.discard(group.shared_category_id)
        if not common:
            return None
        index = self.taxonomy(query.taxonomy)
        chosen = max(sorted(common), key=lambda cid: index.depth(cid))
        category = index.get(chosen)
        return (category.canonical_name if category else chosen, chosen)

    def _find_paths(self, query: ContentQuery, game_id: str | None) -> ContentResult:
        """Relationship chains, breadth-first so shortest paths come first.

        Ordering matters for a game whose uniqueness contract is
        UNIQUE_MINIMAL_PATH: it needs to know the shortest route exists and
        that longer ones also do.
        """
        rejected: dict[str, int] = {}
        start_ids = self._entity_ids_named(query.category, query.lang)
        if not start_ids:
            return self._finish(query, rejected=rejected, examined=0, satisfied=False)

        depth_cap = min(query.taxonomy_depth or MAX_PATH_DEPTH, MAX_PATH_DEPTH)
        index = self.taxonomy(query.taxonomy)
        view_cache: dict[str, EntityView] = {}

        def view_of(entity_id: str) -> EntityView | None:
            if entity_id not in view_cache:
                entity = self._repos.entities.get(entity_id)
                if entity is None or not self._passes(
                    entity, query, game_id, self._membership_ids(entity_id), rejected
                ):
                    return None
                view_cache[entity_id] = self._view(
                    entity, query, game_id, taxonomy=index
                )
            return view_cache[entity_id]

        paths: list[PathView] = []
        examined = 0
        frontier: list[tuple[list[str], list[Relationship]]] = [
            ([start], []) for start in sorted(start_ids)
        ]

        while frontier and len(paths) < query.limit:
            nodes, edges = frontier.pop(0)
            if len(edges) >= depth_cap:
                continue
            for edge in sorted(
                self._repos.relationships.touching(nodes[-1]), key=lambda r: r.id
            ):
                examined += 1
                if edge.predicate == MEMBERSHIP_PREDICATE:
                    continue
                if query.relationship_type and edge.predicate != query.relationship_type:
                    continue
                if not self._passes(edge, query, game_id, (), rejected):
                    continue
                nxt = (
                    edge.object_id if edge.subject_id == nodes[-1] else edge.subject_id
                )
                if nxt in nodes or nxt in query.exclude_ids:
                    continue
                extended_nodes = [*nodes, nxt]
                extended_edges = [*edges, edge]
                views = [view_of(nid) for nid in extended_nodes]
                if any(v is None for v in views):
                    continue
                paths.append(
                    PathView(
                        nodes=tuple(v for v in views if v is not None),
                        edges=tuple(
                            RelationshipView(
                                relationship_id=e.id,
                                subject_id=e.subject_id,
                                predicate=e.predicate,
                                object_id=e.object_id,
                                symmetric=e.symmetric,
                            )
                            for e in extended_edges
                        ),
                    )
                )
                frontier.append((extended_nodes, extended_edges))
                if len(paths) >= query.limit:
                    break

        return self._finish(
            query,
            paths=tuple(paths),
            examined=examined,
            rejected=rejected,
            satisfied=bool(paths),
        )

    def _entity_ids_named(self, name: str | None, lang: str) -> set[str]:
        if name is None:
            return set()
        if name.startswith("entity:"):
            return {name} if self._repos.entities.get(name) else set()
        return {e.id for e in self._repos.entities.by_name(name, lang)}

    # -- result assembly --------------------------------------------------

    def _finish(
        self,
        query: ContentQuery,
        *,
        views: tuple[EntityView, ...] = (),
        groups: tuple[GroupView, ...] = (),
        paths: tuple[PathView, ...] = (),
        relationships: tuple[RelationshipView, ...] = (),
        examined: int = 0,
        rejected: dict[str, int] | None = None,
        satisfied: bool | None = None,
        detail: str = "",
    ) -> ContentResult:
        count = len(views) + len(groups) + len(paths)
        minimum = query.minimum_candidate_count
        met = count >= minimum if minimum is not None else count > 0
        return ContentResult(
            operation=query.operation,
            entities=views,
            groups=groups,
            paths=paths,
            relationships=relationships,
            examined=examined,
            rejected=dict(rejected or {}),
            satisfied=met if satisfied is None else satisfied,
            detail=detail,
        )
