from __future__ import annotations

import pytest

from puzzlegen.content.similarity import (
    DEFAULT_STRATEGY,
    EmbeddingSimilarity,
    LeacockChodorowSimilarity,
    ResnikSimilarity,
    TaxonomyIndex,
    WuPalmerSimilarity,
    build_strategy,
    centroid_distances,
    group_similarity,
)
from puzzlegen.graph.models import Category

from ..conftest import NOW, make_category


@pytest.fixture
def tree(curated_source):
    """animal > mammal > {feline, canine}; animal > bird > raptor."""
    animal = make_category("animal", curated_source)
    mammal = make_category("mammal", curated_source, parent=animal)
    bird = make_category("bird", curated_source, parent=animal)
    feline = make_category("feline", curated_source, parent=mammal)
    canine = make_category("canine", curated_source, parent=mammal)
    raptor = make_category("raptor", curated_source, parent=bird)
    return {
        c.canonical_name: c
        for c in (animal, mammal, bird, feline, canine, raptor)
    }


@pytest.fixture
def index(tree):
    return TaxonomyIndex(tree.values())


class TestTaxonomyIndex:
    def test_depth_is_read_from_the_records(self, index, tree):
        assert index.depth(tree["feline"].id) == 2
        assert index.depth(tree["animal"].id) == 0

    def test_ancestors_are_transitive(self, index, tree):
        ancestors = index.ancestors(tree["feline"].id)
        assert ancestors == frozenset({tree["mammal"].id, tree["animal"].id})

    def test_descendants_are_transitive(self, index, tree):
        descendants = index.descendants(tree["animal"].id)
        assert len(descendants) == 5

    def test_lowest_common_ancestor_of_siblings(self, index, tree):
        lcas = index.lowest_common_ancestors(tree["feline"].id, tree["canine"].id)
        assert lcas == (tree["mammal"].id,)

    def test_lowest_common_ancestor_of_cousins(self, index, tree):
        lcas = index.lowest_common_ancestors(tree["feline"].id, tree["raptor"].id)
        assert lcas == (tree["animal"].id,)

    def test_a_category_is_its_own_lca(self, index, tree):
        assert index.lowest_common_ancestors(
            tree["feline"].id, tree["feline"].id
        ) == (tree["feline"].id,)

    def test_unrelated_taxonomies_have_no_common_ancestor(self, index):
        assert index.lowest_common_ancestors("category:x", "category:y") == ()

    def test_path_length_counts_edges(self, index, tree):
        assert index.shortest_path_length(tree["feline"].id, tree["canine"].id) == 2
        assert index.shortest_path_length(tree["feline"].id, tree["mammal"].id) == 1
        assert index.shortest_path_length(tree["feline"].id, tree["feline"].id) == 0

    def test_forked_ancestry_yields_several_lcas(self, curated_source):
        root = make_category("thing", curated_source)
        left = make_category("left", curated_source, parent=root)
        right = make_category("right", curated_source, parent=root)
        a = make_category("a", curated_source, parents=(left, right))
        b = make_category("b", curated_source, parents=(left, right))
        index = TaxonomyIndex([root, left, right, a, b])
        assert set(index.lowest_common_ancestors(a.id, b.id)) == {left.id, right.id}

    def test_lca_result_is_sorted_for_determinism(self, curated_source):
        root = make_category("thing", curated_source)
        left = make_category("left", curated_source, parent=root)
        right = make_category("right", curated_source, parent=root)
        a = make_category("a", curated_source, parents=(left, right))
        b = make_category("b", curated_source, parents=(right, left))
        index = TaxonomyIndex([root, left, right, a, b])
        assert index.lowest_common_ancestors(a.id, b.id) == tuple(
            sorted(index.lowest_common_ancestors(a.id, b.id))
        )

    def test_information_content_rises_with_specificity(self, index, tree):
        general = index.information_content(tree["animal"].id)
        specific = index.information_content(tree["feline"].id)
        assert specific > general

    def test_an_empty_index_is_safe(self):
        index = TaxonomyIndex([])
        assert len(index) == 0
        assert index.information_content("category:x") == 0.0
        assert index.max_depth() == 0


class TestWuPalmer:
    def test_identical_categories_score_one(self, index, tree):
        assert WuPalmerSimilarity(index).similarity(
            tree["feline"].id, tree["feline"].id
        ).score == 1.0

    def test_siblings_score_above_cousins(self, index, tree):
        strategy = WuPalmerSimilarity(index)
        siblings = strategy.similarity(tree["feline"].id, tree["canine"].id).score
        cousins = strategy.similarity(tree["feline"].id, tree["raptor"].id).score
        assert siblings > cousins

    def test_disconnected_categories_score_zero(self, index):
        assert WuPalmerSimilarity(index).similarity("category:x", "category:y").score == 0.0

    def test_score_is_bounded(self, index, tree):
        strategy = WuPalmerSimilarity(index)
        for a in tree.values():
            for b in tree.values():
                assert 0.0 <= strategy.similarity(a.id, b.id).score <= 1.0

    def test_is_symmetric(self, index, tree):
        strategy = WuPalmerSimilarity(index)
        forward = strategy.similarity(tree["feline"].id, tree["raptor"].id).score
        backward = strategy.similarity(tree["raptor"].id, tree["feline"].id).score
        assert forward == backward

    def test_result_names_its_strategy(self, index, tree):
        result = WuPalmerSimilarity(index).similarity(
            tree["feline"].id, tree["canine"].id
        )
        assert result.strategy == "wu_palmer"


class TestLeacockChodorow:
    def test_is_normalised_onto_the_unit_interval(self, index, tree):
        strategy = LeacockChodorowSimilarity(index)
        for a in tree.values():
            for b in tree.values():
                assert 0.0 <= strategy.similarity(a.id, b.id).score <= 1.0

    def test_closer_categories_score_higher(self, index, tree):
        strategy = LeacockChodorowSimilarity(index)
        near = strategy.similarity(tree["feline"].id, tree["mammal"].id).score
        far = strategy.similarity(tree["feline"].id, tree["raptor"].id).score
        assert near > far

    def test_no_path_scores_zero(self, index):
        assert (
            LeacockChodorowSimilarity(index).similarity("category:x", "category:y").score
            == 0.0
        )


class TestResnik:
    def test_specific_shared_ancestor_scores_higher(self, index, tree):
        strategy = ResnikSimilarity(index)
        under_mammal = strategy.similarity(tree["feline"].id, tree["canine"].id).score
        under_animal = strategy.similarity(tree["feline"].id, tree["raptor"].id).score
        assert under_mammal > under_animal

    def test_is_bounded(self, index, tree):
        strategy = ResnikSimilarity(index)
        for a in tree.values():
            for b in tree.values():
                assert 0.0 <= strategy.similarity(a.id, b.id).score <= 1.0

    def test_no_common_ancestor_scores_zero(self, index):
        assert ResnikSimilarity(index).similarity("category:x", "category:y").score == 0.0


class TestStrategyRegistry:
    @pytest.mark.parametrize(
        "name", ["wu_palmer", "leacock_chodorow", "resnik"]
    )
    def test_every_registered_strategy_builds(self, index, name):
        assert build_strategy(name, index).name == name

    def test_the_default_is_registered(self, index):
        assert build_strategy(DEFAULT_STRATEGY, index) is not None

    def test_an_unknown_strategy_is_refused(self, index):
        with pytest.raises(ValueError):
            build_strategy("vibes", index)


class TestEmbeddingSimilarity:
    def test_uses_frozen_vectors(self):
        strategy = EmbeddingSimilarity({"a": (1.0, 0.0), "b": (1.0, 0.0)})
        assert strategy.similarity("a", "b").score == pytest.approx(1.0)

    def test_a_missing_vector_scores_zero_rather_than_raising(self):
        strategy = EmbeddingSimilarity({"a": (1.0, 0.0)})
        assert strategy.similarity("a", "absent").score == 0.0


class TestGroupSimilarity:
    def test_matrix_is_symmetric_with_a_unit_diagonal(self, index, tree):
        members = [tree["feline"].id, tree["canine"].id, tree["raptor"].id]
        stats = group_similarity(WuPalmerSimilarity(index), members)
        assert all(stats.matrix[i][i] == 1.0 for i in range(3))
        assert stats.matrix[0][2] == stats.matrix[2][0]

    def test_statistics_agree_with_the_matrix(self, index, tree):
        members = [tree["feline"].id, tree["canine"].id, tree["raptor"].id]
        stats = group_similarity(WuPalmerSimilarity(index), members)
        pairwise = [stats.matrix[0][1], stats.matrix[0][2], stats.matrix[1][2]]
        assert stats.minimum == min(pairwise)
        assert stats.maximum == max(pairwise)
        assert stats.mean == pytest.approx(sum(pairwise) / 3)

    def test_spread_is_zero_for_a_uniform_group(self, index, tree):
        members = [tree["feline"].id, tree["canine"].id]
        stats = group_similarity(WuPalmerSimilarity(index), members)
        assert stats.spread == 0.0

    def test_weakest_pair_identifies_the_outlier_pairing(self, index, tree):
        members = [tree["feline"].id, tree["canine"].id, tree["raptor"].id]
        stats = group_similarity(WuPalmerSimilarity(index), members)
        assert tree["raptor"].id in stats.weakest_pair()

    def test_outliers_finds_the_distant_member(self, index, tree):
        members = [tree["feline"].id, tree["canine"].id, tree["raptor"].id]
        stats = group_similarity(WuPalmerSimilarity(index), members)
        assert stats.outliers(tolerance=0.05) == (tree["raptor"].id,)

    def test_a_tight_group_has_no_outliers(self, curated_source):
        root = make_category("animal", curated_source)
        mammal = make_category("mammal", curated_source, parent=root)
        kids = [
            make_category(name, curated_source, parent=mammal)
            for name in ("feline", "canine", "ursid")
        ]
        index = TaxonomyIndex([root, mammal, *kids])
        stats = group_similarity(WuPalmerSimilarity(index), [k.id for k in kids])
        assert stats.outliers() == ()

    def test_a_group_needs_two_members(self, index, tree):
        with pytest.raises(ValueError):
            group_similarity(WuPalmerSimilarity(index), [tree["feline"].id])

    def test_group_similarity_is_deterministic(self, index, tree):
        members = [tree["feline"].id, tree["canine"].id, tree["raptor"].id]
        strategy = WuPalmerSimilarity(index)
        assert group_similarity(strategy, members) == group_similarity(strategy, members)


class TestCentroidDistances:
    def test_a_tight_group_sits_close_to_its_centroid(self):
        vectors = {"a": (1.0, 0.0), "b": (0.99, 0.14), "c": (0.98, 0.2)}
        distances = centroid_distances(vectors, ["a", "b", "c"])
        assert all(score > 0.9 for score in distances.values())

    def test_an_outlier_sits_furthest_from_the_centroid(self):
        vectors = {"a": (1.0, 0.0), "b": (0.99, 0.14), "c": (-1.0, 0.0)}
        distances = centroid_distances(vectors, ["a", "b", "c"])
        assert min(distances, key=distances.get) == "c"

    def test_missing_vectors_are_skipped(self):
        distances = centroid_distances({"a": (1.0, 0.0)}, ["a", "b"])
        assert distances == {}
