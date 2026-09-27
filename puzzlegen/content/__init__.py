"""The content service: normalisation, freshness, policy, query, snapshots."""

from .freshness import REVIEW_INTERVAL_DAYS, evaluate, next_review_at
from .normalizer import NormalizationError, NormalizedBundle, Normalizer, merge_governed
from .policy import (
    DEFAULT_PLATFORM_POLICY,
    ContentPolicy,
    EligibilityVerdict,
    PolicyOutcome,
    PolicyService,
)
from .port import ContentPort, PortBudget, PortUsage
from .query import (
    ContentQuery,
    ContentRequirement,
    ContentResult,
    EntityView,
    FactView,
    GroupView,
    Operation,
    PathView,
    RelationshipView,
    resolve_queries,
)
from .service import ContentService
from .similarity import (
    DEFAULT_STRATEGY,
    GroupSimilarity,
    LeacockChodorowSimilarity,
    ResnikSimilarity,
    SimilarityStrategy,
    TaxonomyIndex,
    WuPalmerSimilarity,
    build_strategy,
    centroid_distances,
    group_similarity,
)
from .snapshots import (
    ActivationPolicy,
    ImportReport,
    SnapshotBuilder,
    compute_content_hash,
    verify_snapshot,
)

__all__ = [
    "ActivationPolicy",
    "ContentPolicy",
    "ContentPort",
    "ContentQuery",
    "ContentRequirement",
    "ContentResult",
    "ContentService",
    "DEFAULT_PLATFORM_POLICY",
    "DEFAULT_STRATEGY",
    "EligibilityVerdict",
    "EntityView",
    "FactView",
    "GroupSimilarity",
    "GroupView",
    "ImportReport",
    "LeacockChodorowSimilarity",
    "NormalizationError",
    "NormalizedBundle",
    "Normalizer",
    "Operation",
    "PathView",
    "PolicyOutcome",
    "PolicyService",
    "PortBudget",
    "PortUsage",
    "REVIEW_INTERVAL_DAYS",
    "RelationshipView",
    "ResnikSimilarity",
    "SimilarityStrategy",
    "SnapshotBuilder",
    "TaxonomyIndex",
    "WuPalmerSimilarity",
    "build_strategy",
    "centroid_distances",
    "compute_content_hash",
    "evaluate",
    "group_similarity",
    "merge_governed",
    "next_review_at",
    "resolve_queries",
    "verify_snapshot",
]
