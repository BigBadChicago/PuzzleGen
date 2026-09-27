from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puzzlegen.core.rng import DeterministicRng, derive_seed

SEED = derive_seed("2026-09-26", "grouping")


def fresh() -> DeterministicRng:
    return DeterministicRng(SEED)


class TestSeeding:
    def test_seed_is_reproducible(self):
        assert derive_seed("2026-09-26", "grouping") == SEED

    def test_different_days_give_different_seeds(self):
        assert derive_seed("2026-09-27", "grouping") != SEED

    def test_different_games_give_different_seeds(self):
        assert derive_seed("2026-09-26", "chain") != SEED

    def test_salt_partitions_the_stream(self):
        assert derive_seed("2026-09-26", "grouping", "staging") != SEED

    def test_short_seeds_are_refused(self):
        with pytest.raises(ValueError):
            DeterministicRng(b"tooshort")

    def test_non_bytes_seeds_are_refused(self):
        with pytest.raises(TypeError):
            DeterministicRng("a string seed")  # type: ignore[arg-type]


class TestStream:
    def test_same_seed_gives_the_same_sequence(self):
        a = [fresh().next_u64() for _ in range(1)]
        b = [fresh().next_u64() for _ in range(1)]
        assert a == b

    def test_stream_advances(self):
        rng = fresh()
        assert rng.next_u64() != rng.next_u64()

    def test_consumed_counts_blocks(self):
        rng = fresh()
        rng.next_u64()
        rng.next_u64()
        assert rng.consumed == 2

    def test_random_is_in_the_unit_interval(self):
        rng = fresh()
        values = [rng.random() for _ in range(500)]
        assert all(0.0 <= v < 1.0 for v in values)

    def test_randbelow_one_is_always_zero(self):
        assert fresh().randbelow(1) == 0

    @pytest.mark.parametrize("n", [0, -1])
    def test_randbelow_rejects_non_positive(self, n):
        with pytest.raises(ValueError):
            fresh().randbelow(n)

    @settings(max_examples=50)
    @given(st.integers(min_value=1, max_value=1000))
    def test_randbelow_stays_in_range(self, n):
        rng = fresh()
        assert all(0 <= rng.randbelow(n) < n for _ in range(20))

    def test_randbelow_covers_its_range(self):
        rng = fresh()
        seen = {rng.randbelow(6) for _ in range(400)}
        assert seen == set(range(6))


class TestSubstreams:
    def test_substreams_are_reproducible(self):
        assert fresh().derive("assemble").next_u64() == fresh().derive("assemble").next_u64()

    def test_different_labels_give_different_streams(self):
        assert fresh().derive("a").next_u64() != fresh().derive("b").next_u64()

    def test_substream_is_independent_of_parent_consumption(self):
        early = fresh()
        early_child = early.derive("assemble").next_u64()

        late = fresh()
        for _ in range(37):
            late.next_u64()
        late_child = late.derive("assemble").next_u64()

        assert early_child == late_child

    def test_substream_labels_compose(self):
        assert fresh().derive("a").derive("b").label == "root/a/b"

    def test_empty_label_is_refused(self):
        with pytest.raises(ValueError):
            fresh().derive("")


class TestSequenceOperations:
    def test_choice_is_reproducible(self):
        items = list("abcdef")
        assert fresh().choice(items) == fresh().choice(items)

    def test_choice_rejects_empty(self):
        with pytest.raises(ValueError):
            fresh().choice([])

    def test_shuffled_does_not_mutate_its_input(self):
        items = list(range(10))
        fresh().shuffled(items)
        assert items == list(range(10))

    def test_shuffled_is_a_permutation(self):
        items = list(range(25))
        assert sorted(fresh().shuffled(items)) == items

    def test_shuffled_is_reproducible(self):
        items = list(range(25))
        assert fresh().shuffled(items) == fresh().shuffled(items)

    def test_shuffled_actually_reorders(self):
        items = list(range(25))
        assert fresh().shuffled(items) != items

    def test_sample_returns_distinct_members(self):
        drawn = fresh().sample(list(range(50)), 10)
        assert len(drawn) == len(set(drawn)) == 10

    def test_sample_rejects_oversized_k(self):
        with pytest.raises(ValueError):
            fresh().sample([1, 2, 3], 4)

    def test_sample_rejects_negative_k(self):
        with pytest.raises(ValueError):
            fresh().sample([1, 2, 3], -1)

    def test_weighted_choice_honours_zero_weight(self):
        rng = fresh()
        picks = {rng.weighted_choice([("a", 0.0), ("b", 1.0)]) for _ in range(50)}
        assert picks == {"b"}

    def test_weighted_choice_is_reproducible(self):
        pairs = [("a", 1.0), ("b", 2.0), ("c", 3.0)]
        assert fresh().weighted_choice(pairs) == fresh().weighted_choice(pairs)

    def test_weighted_choice_rejects_negative_weights(self):
        with pytest.raises(ValueError):
            fresh().weighted_choice([("a", -1.0), ("b", 1.0)])

    def test_weighted_choice_rejects_zero_total(self):
        with pytest.raises(ValueError):
            fresh().weighted_choice([("a", 0.0), ("b", 0.0)])

    def test_weighted_choice_rejects_empty(self):
        with pytest.raises(ValueError):
            fresh().weighted_choice([])
