"""Providers whose content is a declaration.

Two claims are under test. The first is arithmetic: the properties these
providers assert about a number are the properties that number has. The second
is the one that makes them usable at all: the bundle is a pure function of the
declaration, so two builds of the same range or the same attribute space
produce byte-identical content and a snapshot hash means something.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core.types import ProvenanceClass, SourceKind
from puzzlegen.providers.base import validate_bundle
from puzzlegen.providers.generated import (
    CERTAIN,
    FIGURE_ATTRIBUTES,
    FIGURE_COUNTS,
    FIGURE_GLYPHS,
    GLYPH,
    NUMBER_CATEGORIES,
    SPOKEN,
    SYMBOL_KIND,
    VALUE,
    FigureProvider,
    NumberRangeProvider,
    digit_sum,
    is_fibonacci,
    is_prime,
    is_square,
    is_triangular,
    spoken_number,
)
from puzzlegen.providers.base import ProviderError

NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)


class TestTheArithmetic:
    @pytest.mark.parametrize(
        "value,expected",
        [(0, False), (1, False), (2, True), (3, True), (4, False), (97, True), (99, False)],
    )
    def test_primality(self, value, expected):
        assert is_prime(value) is expected

    @pytest.mark.parametrize("value", [0, 1, 4, 9, 16, 25, 81])
    def test_squares(self, value):
        assert is_square(value)

    @pytest.mark.parametrize("value", [2, 3, 5, 99])
    def test_non_squares(self, value):
        assert not is_square(value)

    def test_triangular_numbers_are_the_running_sums(self):
        """Checked against the definition rather than against a list.

        The implementation solves ``8v + 1`` for a perfect square, which is a
        different computation from summing, so the two disagreeing would be
        caught here. A hand-copied list would only prove the list was copied.
        """
        running = 0
        expected = set()
        for step in range(1, 20):
            running += step
            expected.add(running)
        expected.add(0)
        found = {value for value in range(100) if is_triangular(value)}
        assert found == {value for value in expected if value < 100}

    def test_fibonacci_numbers_are_the_sequence(self):
        found = {value for value in range(100) if is_fibonacci(value)}
        assert found == {0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89}

    @pytest.mark.parametrize("value,expected", [(0, 0), (7, 7), (19, 10), (99, 18)])
    def test_digit_sums(self, value, expected):
        assert digit_sum(value) == expected


class TestSpokenNumbers:
    @pytest.mark.parametrize(
        "value,words",
        [
            (0, "zero"),
            (7, "seven"),
            (13, "thirteen"),
            (20, "twenty"),
            (42, "forty-two"),
            (90, "ninety"),
            (99, "ninety-nine"),
            (100, "one hundred"),
            (101, "one hundred and one"),
            (115, "one hundred and fifteen"),
            (999, "nine hundred and ninety-nine"),
            (1000, "one thousand"),
            (1007, "one thousand seven"),
        ],
    )
    def test_the_words(self, value, words):
        assert spoken_number(value) == words

    def test_the_range_stops_where_the_provider_stops(self):
        with pytest.raises(ValueError, match="9,999"):
            spoken_number(10_000)

    def test_a_negative_number_has_no_form(self):
        with pytest.raises(ValueError):
            spoken_number(-1)


class TestTheNumberRange:
    def test_the_bundle_is_structurally_valid(self):
        bundle = NumberRangeProvider(0, 99, now=NOW).load()
        assert validate_bundle(bundle) == []

    def test_one_entity_per_value(self):
        bundle = NumberRangeProvider(5, 9, now=NOW).load()
        assert [e.name for e in bundle.entities] == ["5", "6", "7", "8", "9"]

    def test_the_entity_name_is_the_digits_and_the_word_is_a_fact(self):
        """One entity serves a board that draws ``7`` and a reader saying it."""
        bundle = NumberRangeProvider(7, 7, now=NOW).load()
        (entity,) = bundle.entities
        assert entity.name == "7"
        facts = {f.predicate: f.value for f in bundle.facts}
        assert facts[GLYPH] == "7"
        assert facts[SPOKEN] == "seven"
        assert facts[VALUE] == 7

    def test_memberships_follow_the_arithmetic(self):
        bundle = NumberRangeProvider(0, 20, now=NOW).load()
        by_name = {e.name: set(e.category_keys) for e in bundle.entities}
        prime = "numbers/category/prime"
        assert prime in by_name["17"]
        assert prime not in by_name["18"]
        assert "numbers/category/even" in by_name["18"]
        assert "numbers/category/odd" in by_name["17"]

    def test_an_empty_category_is_not_emitted(self):
        """A category with no members cannot be told apart, by a game, from
        one whose members were all filtered out."""
        bundle = NumberRangeProvider(0, 9, now=NOW).load()
        names = {c.name for c in bundle.categories}
        assert "single-digit number" in names
        assert "two-digit number" not in names

    def test_every_declared_category_can_be_reached(self):
        """A category nothing ever joins is a declaration with no content."""
        bundle = NumberRangeProvider(0, 999, now=NOW).load()
        emitted = {c.name for c in bundle.categories}
        assert {c.name for c in NUMBER_CATEGORIES} <= emitted

    def test_the_kind_is_the_providers_own(self):
        digits = NumberRangeProvider(0, 9, name="digits", kind="DIGIT", now=NOW)
        kinds = {f.value for f in digits.load().facts if f.predicate == SYMBOL_KIND}
        assert kinds == {"DIGIT"}

    def test_the_declaration_is_the_version(self):
        """Two ranges are two sources, not one source at two times."""
        assert NumberRangeProvider(0, 9, now=NOW).describe().version == "0-9"
        assert NumberRangeProvider(0, 99, now=NOW).describe().version == "0-99"

    def test_the_source_kind_is_computed_and_the_facts_are_sourced(self):
        """COMPUTED provenance means derived from other graph records.

        These have no graph inputs, so the class is SOURCED and the source
        itself is the computation, which is what the descriptor's kind says.
        """
        provider = NumberRangeProvider(0, 9, now=NOW)
        assert provider.describe().kind is SourceKind.COMPUTED
        assert {f.provenance_class for f in provider.load().facts} == {
            ProvenanceClass.SOURCED
        }

    def test_facts_are_asserted_with_certainty(self):
        """That seven is odd is not a claim a source might have got wrong."""
        facts = NumberRangeProvider(0, 9, now=NOW).load().facts
        assert {f.confidence for f in facts} == {CERTAIN}

    def test_two_loads_are_identical(self):
        first = NumberRangeProvider(0, 50, now=NOW).load()
        second = NumberRangeProvider(0, 50, now=NOW).load()
        assert first.entities == second.entities
        assert first.facts == second.facts
        assert first.categories == second.categories

    def test_freshness_has_no_upstream_to_age(self):
        status = NumberRangeProvider(0, 9, now=NOW).freshness_status()
        assert status.healthy and status.age_days == 0

    def test_fetch_entity_finds_one_by_key(self):
        provider = NumberRangeProvider(0, 9, now=NOW)
        assert provider.fetch_entity("numbers/7").name == "7"
        assert provider.fetch_entity("numbers/70") is None

    @pytest.mark.parametrize(
        "low,high,message",
        [(-1, 5, "zero or above"), (9, 5, "empty range"), (0, 10_001, "9,999")],
    )
    def test_a_range_it_cannot_serve_is_refused_at_construction(
        self, low, high, message
    ):
        with pytest.raises(ProviderError, match=message):
            NumberRangeProvider(low, high, now=NOW)


class TestTheFigureSpace:
    def test_the_bundle_is_structurally_valid(self):
        assert validate_bundle(FigureProvider(now=NOW).load()) == []

    def test_the_default_space_is_every_combination(self):
        provider = FigureProvider(now=NOW)
        expected = 1
        for _, values in FIGURE_ATTRIBUTES:
            expected *= len(values)
        assert len(provider.combinations()) == expected == 81

    def test_every_attribute_has_three_values(self):
        """Three is what makes "all same or all different" a construction.

        At three values any two figures determine exactly one third that
        completes a set, so a board's valid sets are a property of the board.
        The default space depends on that, so it is asserted rather than
        assumed.
        """
        assert {len(values) for _, values in FIGURE_ATTRIBUTES} == {3}

    def test_every_figure_has_a_distinct_name(self):
        """Entity ids are derived from names, so a repeat would merge figures."""
        names = [e.name for e in FigureProvider(now=NOW).load().entities]
        assert len(set(names)) == len(names)

    def test_the_name_reads_as_english_and_pluralises(self):
        names = {e.name for e in FigureProvider(now=NOW).load().entities}
        assert "one solid red triangle" in names
        assert "two solid red triangles" in names

    def test_the_glyph_is_the_shape_at_its_fill(self):
        bundle = FigureProvider(now=NOW).load()
        glyphs = {
            e.name: next(
                f.value
                for f in bundle.facts
                if f.subject_key == e.key and f.predicate == GLYPH
            )
            for e in bundle.entities
        }
        assert glyphs["one solid red triangle"] == FIGURE_GLYPHS[("triangle", "solid")]
        assert glyphs["one hollow blue circle"] == FIGURE_GLYPHS[("circle", "hollow")]

    def test_colour_is_never_the_only_channel(self):
        """Two figures differing only in colour share a glyph, which is why
        the attribute is carried as a name a client can also print.

        The engine's accessibility gate refuses a game that conveys anything
        by colour alone, so this is the property a figure game's declaration
        rests on.
        """
        bundle = FigureProvider(now=NOW).load()
        by_key = {e.key: e for e in bundle.entities}
        colours = {
            f.subject_key: f.value for f in bundle.facts if f.predicate == "colour"
        }
        glyphs = {f.subject_key: f.value for f in bundle.facts if f.predicate == GLYPH}
        red = next(k for k, v in colours.items() if v == "red")
        same_but_blue = next(
            k
            for k, v in colours.items()
            if v == "blue" and glyphs[k] == glyphs[red]
            and by_key[k].name.split()[0] == by_key[red].name.split()[0]
        )
        assert glyphs[same_but_blue] == glyphs[red]
        assert colours[same_but_blue] != colours[red]

    def test_each_attribute_becomes_a_subtree(self):
        categories = {c.name: c for c in FigureProvider(now=NOW).load().categories}
        assert categories["figure shape"].parent_keys == ("figures/category/root",)
        assert categories["triangle shape"].parent_keys == (
            "figures/category/shape",
        )

    def test_every_attribute_is_also_a_fact(self):
        """A game holding a tile reads its attributes without parsing category
        names back into attribute and value."""
        bundle = FigureProvider(now=NOW).load()
        first = bundle.entities[0]
        facts = {
            f.predicate: f.value for f in bundle.facts if f.subject_key == first.key
        }
        for attribute, _ in FIGURE_ATTRIBUTES:
            assert attribute in facts
        assert facts["count_value"] == FIGURE_COUNTS[facts["count"]]

    def test_a_smaller_space_is_legal(self):
        provider = FigureProvider(
            (("shape", ("triangle", "circle")), ("fill", ("solid", "hollow"))),
            now=NOW,
        )
        assert len(provider.load().entities) == 4

    def test_two_loads_are_identical(self):
        assert FigureProvider(now=NOW).load().entities == (
            FigureProvider(now=NOW).load().entities
        )

    def test_the_attribute_space_is_the_version(self):
        assert FigureProvider(now=NOW).describe().version == (
            "shape:3+fill:3+count:3+colour:3"
        )

    @pytest.mark.parametrize(
        "attributes,message",
        [
            ((), "at least one attribute"),
            ((("shape", ("triangle",)), ("fill", ("solid", "hollow"))), "one value"),
            (
                (("shape", ("triangle", "triangle")), ("fill", ("solid", "hollow"))),
                "repeats a value",
            ),
            ((("shape", ("triangle", "circle")),), "needs a .shape. and a .fill."),
            (
                (("fill", ("solid", "hollow")), ("colour", ("red", "blue"))),
                "needs a .shape. and a .fill.",
            ),
        ],
    )
    def test_a_space_it_cannot_draw_is_refused_at_construction(
        self, attributes, message
    ):
        with pytest.raises(ProviderError, match=message):
            FigureProvider(attributes, now=NOW)

    def test_a_shape_with_no_glyph_is_refused_at_load(self):
        """Refused rather than imported as a figure nothing can draw."""
        provider = FigureProvider(
            (("shape", ("triangle", "pentagon")), ("fill", ("solid", "hollow"))),
            now=NOW,
        )
        with pytest.raises(ProviderError, match="no glyph"):
            provider.load()
