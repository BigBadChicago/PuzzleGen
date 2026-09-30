"""Unioning parents when two senses of one word become one category.

``viola`` the plant sits under ``herb`` and ``viola`` the instrument under
``bowed stringed instrument``. Category identity is the name, so the two are
one category, and keeping only the first import's parents left the other sense
homed in the wrong family. The parents are unioned instead.

That change costs the two guarantees these tests pin: a category could now gain
a parent that is already below it, which the repository's existence check
cannot catch, and a category that gains a deeper parent changes the depth of
everything beneath it.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.snapshots import ImportReport, SnapshotBuilder
from puzzlegen.core import ids
from puzzlegen.core.types import FreshnessClass, ReviewStatus
from puzzlegen.graph import Category

from ..conftest import NOW, sourced_provenance

TAXONOMY = "wordnet"


def category(name, source, *, parents=(), depth=None):
    # The id carries the taxonomy, as the normalizer mints it: two taxonomies
    # may both hold a "viola" and they are different categories.
    return Category.build(
        id=cid(name),
        canonical_name=name,
        parents=tuple(parents),
        created_at=NOW,
        taxonomy=TAXONOMY,
        status=ReviewStatus.ACTIVE,
        freshness_class=FreshnessClass.STATIC,
        confidence=0.95,
        provenance=(sourced_provenance(source),),
        **({"depth_override": depth} if depth else {}),
        **({"gloss": f"{name} gloss"}),
    )


def cid(name: str) -> str:
    return ids.for_category(name, "en", TAXONOMY)


@pytest.fixture
def builder(repos, curated_source):
    repos.sources.put(curated_source)
    return SnapshotBuilder(repos, now=NOW), repos, curated_source


def store(repos, *records):
    for record in records:
        repos.categories.put(record)


class TestUnioningParents:
    def viola(self, repos, source):
        """The plant sense first, then the instrument sense."""
        herb = category("herb", source)
        bowed = category("bowed stringed instrument", source)
        store(repos, herb, bowed)
        return (
            category("viola", source, parents=(herb,)),
            category("viola", source, parents=(bowed,)),
        )

    def test_both_senses_parents_survive(self, builder):
        snapshots, repos, source = builder
        plant, instrument = self.viola(repos, source)
        repos.categories.put(plant)

        merged = snapshots._merge_category(plant, instrument, None)

        assert set(merged.parent_ids) == {cid("herb"), cid("bowed stringed instrument")}

    def test_the_stored_record_carries_them(self, builder):
        snapshots, repos, source = builder
        plant, instrument = self.viola(repos, source)
        repos.categories.put(plant)

        snapshots._merge_category(plant, instrument, None)

        assert set(repos.categories.get(cid("viola")).parent_ids) == {
            cid("herb"),
            cid("bowed stringed instrument"),
        }

    def test_the_order_of_import_no_longer_decides(self, builder):
        snapshots, repos, source = builder
        plant, instrument = self.viola(repos, source)
        repos.categories.put(instrument)

        merged = snapshots._merge_category(instrument, plant, None)

        assert set(merged.parent_ids) == {cid("herb"), cid("bowed stringed instrument")}

    def test_a_repeated_parent_is_not_added_twice(self, builder):
        snapshots, repos, source = builder
        herb = category("herb", source)
        store(repos, herb)
        first = category("viola", source, parents=(herb,))
        repos.categories.put(first)

        merged = snapshots._merge_category(first, category("viola", source, parents=(herb,)), None)

        assert merged.parent_ids == (cid("herb"),)

    def test_provenance_still_accumulates(self, builder):
        snapshots, repos, source = builder
        plant, instrument = self.viola(repos, source)
        repos.categories.put(plant)

        merged = snapshots._merge_category(plant, instrument, None)

        assert len(merged.provenance) >= len(plant.provenance)


class TestCyclesAreRefused:
    """The guarantee ``put`` alone can no longer give.

    Both records exist by the time a parent is added, so the existence check
    passes while the edge still closes a loop.
    """

    def family(self, repos, source):
        top = category("tool", source)
        store(repos, top)
        middle = category("hammer", source, parents=(top,))
        store(repos, middle)
        return top, middle

    def test_a_parent_below_the_category_is_refused(self, builder):
        snapshots, repos, source = builder
        top, middle = self.family(repos, source)
        incoming = category("tool", source, parents=(middle,))

        merged = snapshots._merge_category(top, incoming, None)

        assert merged.parent_ids == ()

    def test_the_category_itself_is_refused_as_a_parent(self, builder):
        snapshots, repos, source = builder
        top, _ = self.family(repos, source)
        # A self edge cannot be constructed, so it is put directly.
        merged = snapshots._merge_category(
            top, top.model_copy(update={"parent_ids": (top.id,)}), None
        )

        assert merged.parent_ids == ()

    def test_a_refusal_is_reported_by_name(self, builder):
        snapshots, repos, source = builder
        top, middle = self.family(repos, source)
        report = ImportReport(snapshot_id="s", label="l")

        snapshots._merge_category(top, category("tool", source, parents=(middle,)), report)

        assert any("its own ancestor" in w and "tool" in w for w in report.warnings)

    def test_a_deeper_descendant_is_refused_too(self, builder):
        snapshots, repos, source = builder
        top, middle = self.family(repos, source)
        bottom = category("claw hammer", source, parents=(middle,))
        store(repos, bottom)

        merged = snapshots._merge_category(top, category("tool", source, parents=(bottom,)), None)

        assert merged.parent_ids == ()

    def test_a_safe_parent_is_kept_when_another_is_refused(self, builder):
        snapshots, repos, source = builder
        top, middle = self.family(repos, source)
        other = category("implement", source)
        store(repos, other)
        incoming = category("tool", source, parents=(middle, other))

        merged = snapshots._merge_category(top, incoming, None)

        assert merged.parent_ids == (cid("implement"),)

    def test_the_store_never_holds_the_refused_edge(self, builder):
        snapshots, repos, source = builder
        top, middle = self.family(repos, source)

        snapshots._merge_category(top, category("tool", source, parents=(middle,)), None)

        assert repos.categories.get(top.id).parent_ids == ()


class TestDepthIsRepaired:
    """Depth is derived from parents and read by Wu-Palmer similarity, so a
    category that gains a deeper parent must not leave stale depths below it."""

    def deep(self, repos, source):
        root = category("root", source)
        store(repos, root)
        chain = root
        for name in ("a", "b", "c"):
            chain = category(name, source, parents=(chain,))
            store(repos, chain)
        return root, chain

    def test_the_merged_category_takes_the_deeper_depth(self, builder):
        snapshots, repos, source = builder
        _, deep = self.deep(repos, source)
        shallow = category("shallow", source)
        store(repos, shallow)
        target = category("viola", source, parents=(shallow,))
        repos.categories.put(target)

        merged = snapshots._merge_category(target, category("viola", source, parents=(deep,)), None)

        assert merged.depth == deep.depth + 1
        assert merged.min_depth == shallow.min_depth + 1

    def test_a_child_depth_is_recomputed(self, builder):
        snapshots, repos, source = builder
        _, deep = self.deep(repos, source)
        shallow = category("shallow", source)
        store(repos, shallow)
        target = category("viola", source, parents=(shallow,))
        repos.categories.put(target)
        child = category("kind of viola", source, parents=(target,))
        store(repos, child)

        snapshots._merge_category(target, category("viola", source, parents=(deep,)), None)

        assert repos.categories.get(child.id).depth == deep.depth + 2

    def test_a_grandchild_is_recomputed_too(self, builder):
        snapshots, repos, source = builder
        _, deep = self.deep(repos, source)
        shallow = category("shallow", source)
        store(repos, shallow)
        target = category("viola", source, parents=(shallow,))
        repos.categories.put(target)
        child = category("kind of viola", source, parents=(target,))
        store(repos, child)
        grand = category("sort of viola", source, parents=(child,))
        store(repos, grand)

        snapshots._merge_category(target, category("viola", source, parents=(deep,)), None)

        assert repos.categories.get(grand.id).depth == deep.depth + 3

    def test_nothing_moves_when_no_parent_is_added(self, builder):
        snapshots, repos, source = builder
        shallow = category("shallow", source)
        store(repos, shallow)
        target = category("viola", source, parents=(shallow,))
        repos.categories.put(target)
        child = category("kind of viola", source, parents=(target,))
        store(repos, child)
        before = repos.categories.get(child.id).depth

        snapshots._merge_category(target, category("viola", source, parents=(shallow,)), None)

        assert repos.categories.get(child.id).depth == before


class TestTheWalks:
    def test_ancestors_climb_every_level(self, repos, curated_source):
        repos.sources.put(curated_source)
        top = category("tool", curated_source)
        store(repos, top)
        middle = category("hammer", curated_source, parents=(top,))
        store(repos, middle)
        bottom = category("claw hammer", curated_source, parents=(middle,))
        store(repos, bottom)

        assert repos.categories.ancestor_ids(bottom.id) == {middle.id, top.id}

    def test_a_root_has_no_ancestors(self, repos, curated_source):
        repos.sources.put(curated_source)
        top = category("tool", curated_source)
        store(repos, top)

        assert repos.categories.ancestor_ids(top.id) == set()

    def test_descendants_come_nearest_first(self, repos, curated_source):
        repos.sources.put(curated_source)
        top = category("tool", curated_source)
        store(repos, top)
        middle = category("hammer", curated_source, parents=(top,))
        store(repos, middle)
        bottom = category("claw hammer", curated_source, parents=(middle,))
        store(repos, bottom)

        assert repos.categories.descendant_ids(top.id) == [middle.id, bottom.id]

    def test_a_leaf_has_no_descendants(self, repos, curated_source):
        repos.sources.put(curated_source)
        top = category("tool", curated_source)
        store(repos, top)

        assert repos.categories.descendant_ids(top.id) == []
