"""The vocabulary games use to ask for content.

Requests are semantic (``find_siblings``), never structural (``SELECT``).
Results are flattened, frozen views that deliberately omit provenance, source
identifiers, confidence internals and provider keys: a game that could read
those could infer which provider supplied a fact, and the whole point of the
abstraction is that it cannot.

Record ids are present, and must be. A game has to return the ids of what it
used so the engine can record dependencies, and a fabricated id is detectable
precisely because the port kept a ledger of what it issued.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.types import DifficultyBand, FrequencyBand


class Operation(StrEnum):
    """The semantic operations the content service exposes."""

    FIND_ENTITIES = "find_entities"
    FIND_RELATED_ENTITIES = "find_related_entities"
    FIND_CATEGORY_MEMBERS = "find_category_members"
    FIND_SIBLINGS = "find_siblings"
    FIND_PAIRS = "find_pairs"
    FIND_GROUPS = "find_groups"
    FIND_INTERSECTING_GROUPS = "find_intersecting_groups"
    FIND_RELATIONSHIP_PATTERNS = "find_relationship_patterns"
    FIND_ENTITIES_BY_FREQUENCY = "find_entities_by_frequency"
    FIND_ENTITIES_BY_DIFFICULTY = "find_entities_by_difficulty"


class GroupingMode(StrEnum):
    """What makes a set of entities a group in a group query.

    The two modes answer different questions about how a taxonomy encodes
    "these belong together", and which one is right depends on how the
    taxonomy was made, not on the game asking.

    ``SHARED_CATEGORY``: every member is a direct member of one category.
    Right for a taxonomy someone authored by hand, where "even numbers" is a
    category holding every even number. Wrong for one derived from an is-a
    hierarchy such as WordNet, where a category is one meaning and its direct
    members are synonyms: nine names for one periwinkle is one thing, not a
    group of nine.

    ``SIBLINGS``: every member stands for a different child of one parent
    category, so the group is "kinds of" that parent. Right for a derived
    hierarchy, where peers are children of a common parent and are never
    synonyms of one another. Each child is represented by the entity carrying
    its own name, which is the first lemma the exporter chose for it, so a
    child with many synonyms still contributes exactly one tile.
    """

    SHARED_CATEGORY = "shared_category"
    SIBLINGS = "siblings"


@dataclass(frozen=True, slots=True)
class ContentQuery:
    """Constraints on a content request.

    One type for every operation rather than one per operation. The
    constraints overlap heavily, and a game that learns the shape once can
    express any request; splitting them would multiply the surface a plugin
    author has to learn without adding any expressive power.
    """

    operation: Operation
    #: Category to search within, by name or id. Names are accepted because a
    #: game should not have to know internal ids to ask a question.
    category: str | None = None
    taxonomy: str = "default"
    #: How many levels below ``category`` are acceptable. 0 means the category
    #: itself, 1 its immediate children, and so on.
    taxonomy_depth: int | None = None
    entity_type: str | None = None
    relationship_type: str | None = None
    predicate: str | None = None
    #: Restrict to entities asserting this predicate with this value.
    predicate_value: Any | None = None

    similarity_strategy: str | None = None
    minimum_similarity: float | None = None
    maximum_similarity: float | None = None
    maximum_similarity_spread: float | None = None
    #: Embedding distance-to-centroid floor for group operations.
    minimum_centroid_similarity: float | None = None

    frequency_band: FrequencyBand | None = None
    maximum_frequency_spread: int | None = None
    difficulty_band: DifficultyBand | None = None

    lang: str = "en"
    minimum_confidence: float | None = None
    fresh_only: bool = True
    #: Second axis for intersecting-group queries: the other category a group
    #: must also share. This is what makes a deliberate overlap constructible.
    secondary_category: str | None = None
    secondary_predicate: str | None = None
    #: Require a group to contain members that also belong to some category in
    #: this taxonomy, whichever one.
    #:
    #: This is a storage-and-query pattern, not a game mechanic: an entity can
    #: sit in categories from more than one taxonomy at once (a primary one
    #: that organizes it, plus any number of others that cut across it), and
    #: this is how a query asks for the places two taxonomies' memberships
    #: overlap, with no assumption that either taxonomy is about words. See
    #: "The primary-plus-crosscutting storage pattern" near the top of phase 3
    #: in docs/architecture.md for the full definition and a non-word example.
    #:
    #: Game 1 (grouping) is one caller, and its numbers illustrate why the
    #: field exists rather than what it means: an overlay of 144 entities
    #: inside a 14,720 entity graph is touched by 157 lexical categories out
    #: of 6,511, so groups sampled from the lexicon almost never contain
    #: overlay members without this constraint. A number game asking
    #: `intersects_taxonomy="notable_years"` against a `parity` taxonomy would
    #: use this exact field the same way, with no words involved.
    intersects_taxonomy: str | None = None
    #: How many of a group's members must carry that second meaning. One by
    #: default, because requiring all of them is a different and much stronger
    #: claim: measured on the real snapshot, no lexical category contains even
    #: three overlay words, so "every member" makes a group impossible while
    #: "at least one" is exactly what a board needs, since each visible group
    #: gives up one tile to the hidden group.
    minimum_intersecting_members: int = 1

    #: Require the returned groups, taken together, to cover whole groups of
    #: this taxonomy.
    #:
    #: ``intersects_taxonomy`` is a constraint on each group on its own: this
    #: word also means something else. This is a constraint on the result as a
    #: set: among everything returned, the members of some whole category of
    #: the covering taxonomy are all present, spread over enough groups to be
    #: usable. The difference decides whether a cross-cutting second axis is
    #: reachable or only nearly reachable, and no per-group constraint can
    #: express it, because whether a category is covered is not a property of
    #: any one group.
    #:
    #: Without it a caller needing both axes draws two independent samples and
    #: hopes they meet. Measured on game 1's real snapshot: 200 candidate
    #: second-axis groups, every member of every one of them present in the
    #: graph, and 199 of 200 unusable because the 40 groups fetched for the
    #: first axis happened not to contain them. One survived, every day for a
    #: month, which is a sampling accident reported as a content limit.
    #:
    #: Nothing here is about words. A number game whose boards must also be
    #: readable as "years that were leap years" states the same requirement
    #: against its own taxonomy.
    covering_taxonomy: str | None = None
    #: Members of one covering category the result must contain. Defaults to
    #: ``group_size``, the common case being a covered group the same size as
    #: the groups returned.
    minimum_covered_members: int | None = None
    #: Distinct returned groups those members must be spread over.
    #:
    #: One by default, which asks only that the members be present. A caller
    #: that will take one member from each returned group raises it: five
    #: members sitting in two groups cannot be borrowed one per group, and a
    #: result that satisfied only the member count would still be unusable.
    minimum_covered_spread: int = 1
    #: Covering categories the result must cover to count as satisfied.
    #:
    #: One is the floor a single puzzle needs. Selection covers as many as the
    #: limit allows regardless, since the point of the second axis is a choice
    #: among them rather than one forced reading.
    minimum_covered_categories: int = 1

    #: How a group query decides what a group is. Defaults to the original
    #: rule so nothing that already asks for groups changes behaviour.
    grouping: GroupingMode = GroupingMode.SHARED_CATEGORY

    group_size: int | None = None
    group_count: int | None = None
    minimum_candidate_count: int | None = None
    exclude_ids: frozenset[str] = frozenset()
    limit: int = 50

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")
        if self.limit > 500:
            raise ValueError("limit is capped at 500 to bound plugin memory use")
        for name in (
            "minimum_similarity",
            "maximum_similarity",
            "maximum_similarity_spread",
            "minimum_centroid_similarity",
            "minimum_confidence",
        ):
            value = getattr(self, name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must lie on [0, 1]")
        if (
            self.minimum_similarity is not None
            and self.maximum_similarity is not None
            and self.minimum_similarity > self.maximum_similarity
        ):
            raise ValueError("minimum_similarity exceeds maximum_similarity")
        if self.group_size is not None and self.group_size < 2:
            raise ValueError("group_size must be at least 2")
        self._check_covering()
        if self.taxonomy_depth is not None and self.taxonomy_depth < 0:
            raise ValueError("taxonomy_depth must be non-negative")

    def _check_covering(self) -> None:
        """A covering request is refused here rather than silently ignored.

        A caller that sets a covering number and no taxonomy believes it has
        asked for coverage. Accepting that quietly is how a result that meets
        nothing passes for one that meets everything.
        """
        named = self.covering_taxonomy is not None
        for name in ("minimum_covered_members", "minimum_covered_spread"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.minimum_covered_categories < 1:
            raise ValueError("minimum_covered_categories must be at least 1")
        if named:
            members = self.minimum_covered_members or self.group_size
            if members is None:
                raise ValueError(
                    "covering_taxonomy needs minimum_covered_members or group_size"
                )
            if self.minimum_covered_spread > members:
                raise ValueError(
                    "minimum_covered_spread exceeds minimum_covered_members: "
                    "members cannot spread over more groups than there are members"
                )
            return
        set_without_taxonomy = [
            name
            for name, default in (
                ("minimum_covered_members", None),
                ("minimum_covered_spread", 1),
                ("minimum_covered_categories", 1),
            )
            if getattr(self, name) != default
        ]
        if set_without_taxonomy:
            raise ValueError(
                f"{set_without_taxonomy} set without covering_taxonomy"
            )


@dataclass(frozen=True, slots=True)
class FactView:
    """One assertion, as a game sees it."""

    fact_id: str
    predicate: str
    value: Any
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class EntityView:
    """An entity flattened for presentation and reasoning.

    This is the shape the design brief sketched conceptually: name, types and
    properties together. It is assembled on read from normalised records, so
    the convenience costs nothing in governance.
    """

    entity_id: str
    name: str
    aliases: tuple[str, ...] = ()
    definition: str | None = None
    #: Category names, most specific first.
    types: tuple[str, ...] = ()
    #: Category ids, aligned with ``types``. Needed for dependency reporting.
    type_ids: tuple[str, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)
    facts: tuple[FactView, ...] = ()
    frequency_band: FrequencyBand | None = None

    def fact_ids(self) -> tuple[str, ...]:
        return tuple(f.fact_id for f in self.facts)

    def dependency_ids(self) -> tuple[str, ...]:
        """Every graph record this view exposes, for dependency reporting."""
        return (self.entity_id, *self.type_ids, *self.fact_ids())


@dataclass(frozen=True, slots=True)
class RelationshipView:
    relationship_id: str
    subject_id: str
    predicate: str
    object_id: str
    symmetric: bool = False


@dataclass(frozen=True, slots=True)
class GroupView:
    """A set of entities the service considers mutually comparable."""

    members: tuple[EntityView, ...]
    #: The category the group shares, if any.
    shared_category: str | None = None
    shared_category_id: str | None = None
    #: The second shared axis, for intersecting groups.
    secondary_category: str | None = None
    secondary_category_id: str | None = None
    similarity_strategy: str = ""
    mean_similarity: float = 0.0
    minimum_similarity: float = 0.0
    similarity_spread: float = 0.0
    centroid_similarity: Mapping[str, float] = field(default_factory=dict)
    frequency_spread: int = 0

    def dependency_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for member in self.members:
            ids.extend(member.dependency_ids())
        for category_id in (self.shared_category_id, self.secondary_category_id):
            if category_id:
                ids.append(category_id)
        return tuple(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class PathView:
    """A chain of relationships between two entities."""

    nodes: tuple[EntityView, ...]
    edges: tuple[RelationshipView, ...]

    @property
    def length(self) -> int:
        return len(self.edges)

    def dependency_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for node in self.nodes:
            ids.extend(node.dependency_ids())
        ids.extend(edge.relationship_id for edge in self.edges)
        return tuple(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class ContentResult:
    """What a content request returns, with its own diagnostics.

    ``rejected`` carries the reason counts from every gate the request
    applied. A game receives it too: a plugin that asked for something
    unsatisfiable should be able to say so rather than silently emitting no
    candidates.
    """

    operation: Operation
    entities: tuple[EntityView, ...] = ()
    groups: tuple[GroupView, ...] = ()
    paths: tuple[PathView, ...] = ()
    relationships: tuple[RelationshipView, ...] = ()
    examined: int = 0
    rejected: Mapping[str, int] = field(default_factory=dict)
    satisfied: bool = True
    detail: str = ""

    def __len__(self) -> int:
        return len(self.entities) + len(self.groups) + len(self.paths)

    def is_empty(self) -> bool:
        return len(self) == 0

    def dependency_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for entity in self.entities:
            ids.extend(entity.dependency_ids())
        for group in self.groups:
            ids.extend(group.dependency_ids())
        for path in self.paths:
            ids.extend(path.dependency_ids())
        ids.extend(r.relationship_id for r in self.relationships)
        return tuple(dict.fromkeys(ids))


@dataclass(frozen=True, slots=True)
class ContentRequirement:
    """A game's declared need, resolved by the engine into queries.

    Games declare requirements; the engine decides how to satisfy them. That
    indirection is what lets the query implementation change without any game
    changing, and what stops a game from pinning itself to one provider's
    idea of a category.
    """

    name: str
    query: ContentQuery
    #: Minimum results below which the requirement is unmet and the game
    #: should not attempt assembly.
    minimum: int = 1
    optional: bool = False

    def is_met(self, result: ContentResult) -> bool:
        return self.optional or len(result) >= self.minimum


def resolve_queries(
    requirements: Sequence[ContentRequirement],
) -> dict[str, ContentQuery]:
    """Flatten declared requirements into named queries, rejecting duplicates."""
    resolved: dict[str, ContentQuery] = {}
    for requirement in requirements:
        if requirement.name in resolved:
            raise ValueError(f"duplicate content requirement {requirement.name!r}")
        resolved[requirement.name] = requirement.query
    return resolved
