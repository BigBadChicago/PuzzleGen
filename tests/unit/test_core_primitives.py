from __future__ import annotations

import datetime as dt
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puzzlegen.core import ids
from puzzlegen.core.hashing import canonical_json, short_hash, stable_hash
from puzzlegen.core.types import ProvenanceClass, ReviewStatus
from puzzlegen.core.versions import SemVer


class TestIds:
    def test_clean_names_become_readable_ids(self):
        assert ids.for_entity("tiger", "en") == "entity:tiger"
        assert ids.for_category("animal") == "category:animal"

    def test_unclean_names_fall_back_to_a_hash(self):
        minted = ids.for_entity("Panthera tigris (Linnaeus, 1758)", "en")
        assert minted.startswith("entity:")
        assert ids.is_id(minted, ids.ENTITY)

    def test_minting_is_stable_across_calls(self):
        assert ids.for_entity("snow leopard", "en") == ids.for_entity("snow leopard", "en")

    def test_language_changes_the_id(self):
        assert ids.for_entity("tiger", "en") != ids.for_entity("tiger", "fr")

    def test_fact_id_includes_the_value(self):
        subject = ids.for_entity("tiger", "en")
        a = ids.for_fact(subject, "conservation_status", "STRING:endangered")
        b = ids.for_fact(subject, "conservation_status", "STRING:vulnerable")
        assert a != b

    def test_relationship_id_is_directional(self):
        a, b = ids.for_entity("tiger", "en"), ids.for_category("feline")
        assert ids.for_relationship(a, "is_a", b) != ids.for_relationship(b, "is_a", a)

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(ValueError):
            ids.mint("wombat", "x")

    def test_empty_key_part_is_rejected(self):
        with pytest.raises(ValueError):
            ids.mint(ids.ENTITY, "")

    def test_require_rejects_the_wrong_kind(self):
        with pytest.raises(ValueError):
            ids.require("category:animal", ids.ENTITY)

    def test_kind_of_rejects_non_ids(self):
        with pytest.raises(ValueError):
            ids.kind_of("not an id")

    @given(st.text(min_size=1, max_size=40))
    def test_minting_never_produces_an_invalid_id(self, text):
        minted = ids.mint(ids.ENTITY, text if text.strip() else "fallback")
        assert ids.is_id(minted, ids.ENTITY)


class TestCanonicalJson:
    def test_key_order_does_not_matter(self):
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_enums_serialise_to_their_wire_value(self):
        assert canonical_json(ReviewStatus.ACTIVE) == '"ACTIVE"'
        assert canonical_json(ProvenanceClass.COMPUTED) == '"COMPUTED"'

    def test_sets_serialise_in_a_deterministic_order(self):
        assert canonical_json({"c", "a", "b"}) == canonical_json({"b", "c", "a"})

    def test_aware_datetimes_normalise_to_utc_z(self):
        east = dt.datetime(2026, 9, 26, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
        assert canonical_json(east) == '"2026-09-26T12:00:00Z"'

    def test_naive_datetimes_are_refused(self):
        with pytest.raises(ValueError):
            canonical_json(dt.datetime(2026, 9, 26, 12, 0))

    def test_non_finite_floats_are_refused(self):
        for value in (math.nan, math.inf, -math.inf):
            with pytest.raises(ValueError):
                canonical_json(value)

    def test_non_string_keys_are_refused(self):
        with pytest.raises(ValueError):
            canonical_json({1: "a"})

    def test_unsupported_types_are_refused(self):
        with pytest.raises(TypeError):
            canonical_json(object())

    def test_deep_nesting_is_refused(self):
        value: object = "leaf"
        for _ in range(80):
            value = [value]
        with pytest.raises(ValueError):
            canonical_json(value)

    def test_hash_is_stable_and_hex(self):
        digest = stable_hash({"a": [1, 2, 3]})
        assert len(digest) == 64
        assert digest == stable_hash({"a": [1, 2, 3]})

    def test_short_hash_length_is_validated(self):
        assert len(short_hash({"a": 1}, 16)) == 16
        with pytest.raises(ValueError):
            short_hash({"a": 1}, 2)

    @settings(max_examples=100)
    @given(
        st.dictionaries(
            st.text(min_size=1, max_size=8),
            st.one_of(st.integers(), st.booleans(), st.text(max_size=8), st.none()),
            max_size=6,
        )
    )
    def test_reordering_a_mapping_never_changes_its_hash(self, mapping):
        reversed_mapping = dict(reversed(list(mapping.items())))
        assert stable_hash(mapping) == stable_hash(reversed_mapping)


class TestSemVer:
    def test_parses_and_round_trips(self):
        assert str(SemVer.parse("1.4.2")) == "1.4.2"

    @pytest.mark.parametrize("bad", ["1.4", "1.4.2.3", "a.b.c", "1.-4.2", ""])
    def test_rejects_malformed_versions(self, bad):
        with pytest.raises(ValueError):
            SemVer.parse(bad)

    def test_compatibility_is_by_major_only(self):
        assert SemVer.parse("1.0.0").is_compatible_with(SemVer.parse("1.9.3"))
        assert not SemVer.parse("1.0.0").is_compatible_with(SemVer.parse("2.0.0"))
