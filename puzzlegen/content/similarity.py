"""Semantic similarity over the taxonomy.

Three things live here and nowhere else:

* A :class:`TaxonomyIndex` built once per query session from the category
  repository. Similarity needs ancestor sets and subtree sizes, and computing
  those per pair against storage would dominate the cost of every sibling
  query.
* Interchangeable :class:`SimilarityStrategy` implementations. WordNet's
  classic measures are offered, plus an embedding-backed one, and the strategy
  in force is named in every result so a manifest records which measure
  produced a puzzle's thresholds.
* Group-level statistics. Pairwise similarity is not enough: a group can have
  a healthy mean while containing one member nothing else is close to, which
  is exactly the failure that makes a grouping puzzle unfair.

No game imports this module. Games state thresholds in their content
requirements; the content service applies them.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..core.types import Similarity
from ..graph.models import Category
from ..providers.embeddings import cosine


class TaxonomyIndex:
    """In-memory ancestry and subtree structure for one taxonomy.

    Built from records the repository already validated, so it assumes an
    acyclic parent graph. That assumption is safe because the repository
    refuses a category whose parents are absent, which makes a cycle
    unwritable.
    """

    def __init__(self, categories: Iterable[Category]) -> None:
        self._by_id: dict[str, Category] = {}
        for category in categories:
            self._by_id[category.id] = category

        self._ancestors: dict[str, frozenset[str]] = {}
        self._descendants: dict[str, set[str]] = {cid: set() for cid in self._by_id}

        # Shallowest first, so every parent's ancestor set is already final.
        for category in sorted(self._by_id.values(), key=lambda c: (c.depth, c.id)):
            inherited: set[str] = set()
            for parent_id in category.parent_ids:
                if parent_id in self._by_id:
                    inherited.add(parent_id)
                    inherited |= self._ancestors.get(parent_id, frozenset())
            self._ancestors[category.id] = frozenset(inherited)
            for ancestor_id in inherited:
                self._descendants[ancestor_id].add(category.id)

        self._size = len(self._by_id)

    def __len__(self) -> int:
        return self._size

    def __contains__(self, category_id: str) -> bool:
        return category_id in self._by_id

    def get(self, category_id: str) -> Category | None:
        return self._by_id.get(category_id)

    def depth(self, category_id: str) -> int:
        category = self._by_id.get(category_id)
        return category.depth if category else 0

    def ancestors(self, category_id: str) -> frozenset[str]:
        return self._ancestors.get(category_id, frozenset())

    def descendants(self, category_id: str) -> frozenset[str]:
        return frozenset(self._descendants.get(category_id, set()))

    def is_descendant_of(self, category_id: str, ancestor_id: str) -> bool:
        return ancestor_id in self.ancestors(category_id)

    def lowest_common_ancestors(self, a: str, b: str) -> tuple[str, ...]:
        """Deepest categories above both, plural because ancestry can fork.

        With multiple parents there may be several equally deep common
        ancestors, and picking one arbitrarily would make similarity depend on
        dictionary ordering.
        """
        if a == b:
            return (a,) if a in self._by_id else ()
        common = (self.ancestors(a) | {a}) & (self.ancestors(b) | {b})
        if not common:
            return ()
        best = max(self.depth(cid) for cid in common)
        return tuple(sorted(cid for cid in common if self.depth(cid) == best))

    def shortest_path_length(self, a: str, b: str) -> int | None:
        """Edges between two categories through their closest common ancestor."""
        if a == b:
            return 0
        lcas = self.lowest_common_ancestors(a, b)
        if not lcas:
            return None
        lca_depth = self.depth(lcas[0])
        return (self.depth(a) - lca_depth) + (self.depth(b) - lca_depth)

    def max_depth(self) -> int:
        return max((c.depth for c in self._by_id.values()), default=0)

    def information_content(self, category_id: str) -> float:
        """Surprise value of a category, from its share of the taxonomy.

        A proper implementation counts corpus occurrences. Subtree size is a
        standard structural substitute and, unlike a corpus count, it needs no
        additional dataset that could go stale independently of the taxonomy.
        """
        if self._size == 0 or category_id not in self._by_id:
            return 0.0
        covered = len(self._descendants[category_id]) + 1
        return -math.log(covered / self._size) if covered < self._size else 0.0

    def max_information_content(self) -> float:
        return -math.log(1 / self._size) if self._size > 1 else 1.0


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    score: Similarity
    strategy: str
    detail: str = ""


@runtime_checkable
class SimilarityStrategy(Protocol):
    """Measures how semantically close two categories are, on [0, 1]."""

    @property
    def name(self) -> str: ...

    def similarity(self, a: str, b: str) -> SimilarityResult: ...


class WuPalmerSimilarity:
    """Twice the depth of the common ancestor over the summed depths.

    Rewards pairs whose shared ancestor is specific. Naturally bounded on
    [0, 1], which is why it is the default.
    """

    name = "wu_palmer"

    def __init__(self, index: TaxonomyIndex) -> None:
        self._index = index

    def similarity(self, a: str, b: str) -> SimilarityResult:
        if a == b:
            return SimilarityResult(1.0, self.name, "identical category")
        lcas = self._index.lowest_common_ancestors(a, b)
        if not lcas:
            return SimilarityResult(0.0, self.name, "no common ancestor")
        lca_depth = self._index.depth(lcas[0])
        total = self._index.depth(a) + self._index.depth(b)
        if total == 0:
            return SimilarityResult(1.0, self.name, "both at taxonomy root")
        score = (2.0 * lca_depth) / total
        return SimilarityResult(
            min(1.0, max(0.0, score)), self.name, f"lca depth {lca_depth}"
        )


class LeacockChodorowSimilarity:
    """Path length scaled by taxonomy depth, normalised onto [0, 1].

    The raw measure is unbounded above, so it is divided by its own maximum
    for this taxonomy. Without that, a threshold tuned on one taxonomy would
    mean something different on another.
    """

    name = "leacock_chodorow"

    def __init__(self, index: TaxonomyIndex) -> None:
        self._index = index
        self._max_depth = max(index.max_depth(), 1)
        self._ceiling = math.log((2.0 * self._max_depth) + 1.0)

    def similarity(self, a: str, b: str) -> SimilarityResult:
        path = self._index.shortest_path_length(a, b)
        if path is None:
            return SimilarityResult(0.0, self.name, "no path")
        raw = -math.log((path + 1.0) / ((2.0 * self._max_depth) + 1.0))
        score = raw / self._ceiling if self._ceiling else 0.0
        return SimilarityResult(
            min(1.0, max(0.0, score)), self.name, f"path length {path}"
        )


class ResnikSimilarity:
    """Information content of the most specific shared ancestor.

    Ignores how far apart the two categories themselves are, so two very
    different categories under a specific shared parent still score highly.
    That is a real property of the measure, not a defect, and it is why it is
    offered alongside the others rather than instead of them.
    """

    name = "resnik"

    def __init__(self, index: TaxonomyIndex) -> None:
        self._index = index
        self._ceiling = index.max_information_content()

    def similarity(self, a: str, b: str) -> SimilarityResult:
        lcas = self._index.lowest_common_ancestors(a, b)
        if not lcas:
            return SimilarityResult(0.0, self.name, "no common ancestor")
        content = max(self._index.information_content(cid) for cid in lcas)
        score = content / self._ceiling if self._ceiling else 0.0
        return SimilarityResult(
            min(1.0, max(0.0, score)), self.name, f"ic {content:.3f}"
        )


class EmbeddingSimilarity:
    """Cosine similarity between two entities' frozen vectors.

    Registered as a strategy so it is interchangeable, but never authoritative
    about facts. It is a quality signal: it catches groups that the taxonomy
    calls siblings and a reader would not.
    """

    name = "embedding_cosine"

    def __init__(self, vectors: Mapping[str, Sequence[float]]) -> None:
        self._vectors = vectors

    def similarity(self, a: str, b: str) -> SimilarityResult:
        va, vb = self._vectors.get(a), self._vectors.get(b)
        if va is None or vb is None:
            return SimilarityResult(0.0, self.name, "vector missing")
        return SimilarityResult(cosine(va, vb), self.name, "")


@dataclass(frozen=True, slots=True)
class GroupSimilarity:
    """Pairwise statistics for one candidate group.

    ``minimum`` and ``spread`` matter more than ``mean``. A group of four
    where three members are tight and one is unrelated has a respectable mean
    and is still a bad puzzle.
    """

    strategy: str
    members: tuple[str, ...]
    matrix: tuple[tuple[float, ...], ...]
    mean: float
    minimum: float
    maximum: float

    @property
    def spread(self) -> float:
        return self.maximum - self.minimum

    def weakest_pair(self) -> tuple[str, str] | None:
        """The two members least alike. The first place a curator should look."""
        worst: tuple[str, str] | None = None
        worst_score = 2.0
        for i, left in enumerate(self.members):
            for j in range(i + 1, len(self.members)):
                score = self.matrix[i][j]
                if score < worst_score:
                    worst_score = score
                    worst = (left, self.members[j])
        return worst

    def outliers(self, tolerance: float = 0.15) -> tuple[str, ...]:
        """Members whose mean similarity to the rest trails the group's own.

        Expressed relative to the group rather than as an absolute threshold,
        because what counts as distant depends on how tight the group is.
        """
        if len(self.members) < 3:
            return ()
        per_member = []
        for i, member in enumerate(self.members):
            others = [self.matrix[i][j] for j in range(len(self.members)) if j != i]
            per_member.append((member, sum(others) / len(others)))
        group_mean = sum(score for _, score in per_member) / len(per_member)
        return tuple(
            member
            for member, score in per_member
            if score < group_mean - tolerance
        )


def group_similarity(
    strategy: SimilarityStrategy, members: Sequence[str]
) -> GroupSimilarity:
    """Full pairwise matrix and its statistics for a candidate group."""
    if len(members) < 2:
        raise ValueError("a group needs at least two members")

    size = len(members)
    matrix = [[1.0] * size for _ in range(size)]
    pairwise: list[float] = []
    for i in range(size):
        for j in range(i + 1, size):
            score = strategy.similarity(members[i], members[j]).score
            matrix[i][j] = matrix[j][i] = score
            pairwise.append(score)

    return GroupSimilarity(
        strategy=strategy.name,
        members=tuple(members),
        matrix=tuple(tuple(row) for row in matrix),
        mean=sum(pairwise) / len(pairwise),
        minimum=min(pairwise),
        maximum=max(pairwise),
    )


def centroid_distances(
    vectors: Mapping[str, Sequence[float]], members: Sequence[str]
) -> dict[str, float]:
    """Each member's similarity to the group centroid.

    The embedding-side outlier check from the design brief. Returned as raw
    numbers rather than a verdict so the caller, which knows the game's
    thresholds, decides what counts as an outlier.
    """
    from ..providers.embeddings import centroid

    present = [m for m in members if m in vectors]
    if len(present) < 2:
        return {}
    middle = centroid([vectors[m] for m in present])
    return {member: cosine(vectors[member], middle) for member in present}


#: Strategies available by name, so a game's declared strategy resolves
#: without the game holding a reference to any implementation.
STRATEGY_FACTORIES = {
    WuPalmerSimilarity.name: WuPalmerSimilarity,
    LeacockChodorowSimilarity.name: LeacockChodorowSimilarity,
    ResnikSimilarity.name: ResnikSimilarity,
}

DEFAULT_STRATEGY = WuPalmerSimilarity.name


def build_strategy(name: str, index: TaxonomyIndex) -> SimilarityStrategy:
    try:
        return STRATEGY_FACTORIES[name](index)
    except KeyError as exc:
        raise ValueError(
            f"unknown similarity strategy {name!r}; available: "
            f"{sorted(STRATEGY_FACTORIES)}"
        ) from exc
