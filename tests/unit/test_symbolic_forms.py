"""The symbolic form subsystem: glyphs, spoken labels and order.

What these tests are about, stated once. The subsystem exists so that a tile
which is not a word still has exactly one answer to three questions: what a
client draws, what a reader says, and where it sits on a scale. Every test
below is about one of those answers being reached the same way twice, or about
a refusal being visible rather than silent.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.query import EntityView
from puzzlegen.symbols import (
    GLYPH_PREDICATE,
    KIND_PREDICATE,
    SPOKEN_PREDICATE,
    VALUE_PREDICATE,
    GlyphSource,
    MissingGlyph,
    SymbolIndex,
    SymbolKind,
    SymbolPolicy,
    form_for,
    glyph_for,
    kind_for,
    ordinal_for,
    spoken_for,
)


def view(entity_id: str, name: str, **properties) -> EntityView:
    return EntityView(entity_id=entity_id, name=name, properties=dict(properties))


def digit(value: int) -> EntityView:
    return view(
        f"entity:{value}",
        str(value),
        **{
            GLYPH_PREDICATE: str(value),
            SPOKEN_PREDICATE: ("zero one two three four five six seven eight nine"
                               .split()[value]),
            VALUE_PREDICATE: value,
            KIND_PREDICATE: "DIGIT",
        },
    )


def figure(name: str, glyph: str) -> EntityView:
    return view(
        f"entity:{name.replace(' ', '-')}",
        name,
        **{GLYPH_PREDICATE: glyph, SPOKEN_PREDICATE: name, KIND_PREDICATE: "FIGURE"},
    )


class TestTheGlyph:
    def test_a_glyph_fact_is_what_a_client_draws(self):
        glyph, source, refused = glyph_for(figure("solid red triangle", "▲"))
        assert (glyph, source, refused) == ("▲", GlyphSource.FACT, "")

    def test_a_short_name_serves_as_its_own_glyph(self):
        """A digit entity needs no curation at all, which is the point."""
        glyph, source, _ = glyph_for(view("entity:7", "7"))
        assert (glyph, source) == ("7", GlyphSource.NAME)

    def test_the_fact_beats_the_name_even_when_the_name_would_fit(self):
        """A figure named one thing and drawn as another is the ordinary case.

        A name that happened to be short enough must not win, or an entity
        deliberately given a glyph would be drawn as its key.
        """
        entity = view("entity:x", "x", **{GLYPH_PREDICATE: "△"})
        assert glyph_for(entity)[0] == "△"

    def test_a_long_name_is_refused_with_the_reason(self):
        glyph, source, refused = glyph_for(view("entity:periwinkle", "periwinkle"))
        assert glyph == ""
        assert source is GlyphSource.NONE
        assert "10 characters" in refused

    def test_use_name_admits_a_word_as_its_own_glyph(self):
        """The policy a lexical board uses, where the label is the glyph."""
        policy = SymbolPolicy(missing_glyph=MissingGlyph.USE_NAME)
        glyph, source, refused = glyph_for(view("entity:tulip", "tulip"), policy)
        assert (glyph, source, refused) == ("tulip", GlyphSource.NAME, "")

    def test_an_entity_with_neither_is_refused(self):
        glyph, source, refused = glyph_for(view("entity:blank", " "))
        assert glyph == "" and source is GlyphSource.NONE
        assert "neither a glyph nor a name" in refused

    def test_a_longer_glyph_fact_is_still_taken(self):
        """The length rule applies to a name standing in, not to a glyph.

        A two-digit number's glyph is two characters and a provider is
        entitled to assert a longer one; the ceiling exists to stop a *name*
        from being mistaken for a glyph.
        """
        entity = view("entity:100", "100", **{GLYPH_PREDICATE: "100"})
        assert glyph_for(entity)[0] == "100"

    def test_the_ceiling_is_policy(self):
        policy = SymbolPolicy(max_glyph_length=5)
        assert glyph_for(view("entity:tulip", "tulip"), policy)[0] == "tulip"

    def test_a_glyph_shorter_than_one_character_is_not_a_policy(self):
        with pytest.raises(ValueError, match="at least one character"):
            SymbolPolicy(max_glyph_length=0)


class TestTheSpokenLabel:
    def test_the_fact_is_what_a_reader_says(self):
        assert spoken_for(digit(7)) == "seven"

    def test_the_name_is_the_fallback(self):
        """Always an answer, because a tile nobody can hear is unplayable."""
        assert spoken_for(view("entity:tulip", "tulip")) == "tulip"

    def test_a_curated_override_wins_over_both(self):
        policy = SymbolPolicy(spoken={"entity:7": "the number seven"})
        assert spoken_for(digit(7), policy) == "the number seven"

    def test_an_entity_with_a_glyph_and_no_label_still_speaks(self):
        entity = view("entity:tri", "triangle", **{GLYPH_PREDICATE: "▲"})
        assert spoken_for(entity) == "triangle"
        assert form_for(entity).playable


class TestTheOrdinal:
    def test_a_value_fact_is_the_position(self):
        assert ordinal_for(digit(7)) == 7.0

    def test_an_entity_with_no_value_has_no_position(self):
        assert ordinal_for(figure("solid red triangle", "▲")) is None

    def test_a_boolean_is_a_flag_rather_than_a_position(self):
        """``isinstance(True, int)`` is true, so this is a real trap.

        A flag read as a position would sort every unordered entity to zero or
        one, in the middle of a numeric board rather than at its end.
        """
        entity = view("entity:f", "f", **{VALUE_PREDICATE: True})
        assert ordinal_for(entity) is None

    def test_a_textual_value_is_not_parsed(self):
        entity = view("entity:7", "7", **{VALUE_PREDICATE: "7"})
        assert ordinal_for(entity) is None

    def test_the_name_is_never_parsed_for_a_value(self):
        """``entity:7`` has a numeric id by accident of slugging, not design."""
        assert ordinal_for(view("entity:7", "7")) is None

    def test_asking_an_unordered_form_for_its_value_raises(self):
        form = form_for(figure("solid red triangle", "▲"))
        assert not form.ordered
        with pytest.raises(ValueError, match=VALUE_PREDICATE):
            form.value()


class TestTheKind:
    def test_the_content_declares_it(self):
        assert kind_for(digit(3)) is SymbolKind.DIGIT

    def test_an_undeclared_kind_takes_the_policy_default(self):
        assert kind_for(view("entity:x", "x")) is SymbolKind.FIGURE
        policy = SymbolPolicy(default_kind=SymbolKind.WORD)
        assert kind_for(view("entity:x", "x"), policy) is SymbolKind.WORD

    def test_an_unknown_kind_is_the_default_rather_than_an_error(self):
        """A snapshot carrying a kind this release never heard of serves boards.

        The kind changes how a tile is announced and nothing about whether it
        is correct, so failing the board would be refusing a day over a label.
        """
        entity = view("entity:x", "x", **{KIND_PREDICATE: "HOLOGRAM"})
        assert kind_for(entity) is SymbolKind.FIGURE


class TestTheIndex:
    def test_it_holds_one_form_per_entity_and_does_not_recompute(self):
        index = SymbolIndex([digit(1), digit(2)])
        first = index.form("entity:1")
        index.add([digit(1)])
        assert index.form("entity:1") is first
        assert len(index) == 2

    def test_an_entity_it_was_never_given_raises(self):
        """No default: a blank form would be a board with an empty tile."""
        index = SymbolIndex([digit(1)])
        with pytest.raises(KeyError, match="no symbolic form"):
            index.form("entity:2")

    def test_playable_and_unplayable_split_on_the_glyph(self):
        index = SymbolIndex([digit(1), view("entity:periwinkle", "periwinkle")])
        assert [f.entity_id for f in index.playable()] == ["entity:1"]
        unplayable = index.unplayable()
        assert [f.entity_id for f in unplayable] == ["entity:periwinkle"]
        assert unplayable[0].refused

    def test_ascending_orders_by_value_and_breaks_ties_on_the_id(self):
        index = SymbolIndex([digit(9), digit(2), digit(5)])
        assert index.ascending() == ("entity:2", "entity:5", "entity:9")

    def test_ascending_refuses_a_board_with_an_unordered_tile(self):
        """Sorting a mixed board quietly is how an unordered tile ends up
        presented as if it had a position."""
        index = SymbolIndex([digit(1), figure("solid red triangle", "▲")])
        with pytest.raises(ValueError, match=VALUE_PREDICATE):
            index.ascending()

    def test_ascending_takes_a_subset(self):
        index = SymbolIndex([digit(i) for i in range(5)])
        assert index.ascending(["entity:3", "entity:1"]) == ("entity:1", "entity:3")

    def test_glyph_collisions_name_the_entities_that_share_a_glyph(self):
        """Two tiles drawn identically is a board the player cannot operate.

        Reported rather than refused: a build may hold a collision, and only a
        board that draws both halves of one is broken.
        """
        index = SymbolIndex(
            [
                figure("one solid red triangle", "▲"),
                figure("one solid blue triangle", "▲"),
                figure("one solid red circle", "●"),
            ]
        )
        assert index.glyph_collisions() == {
            "▲": ("entity:one-solid-blue-triangle", "entity:one-solid-red-triangle")
        }

    def test_a_board_of_distinct_glyphs_reports_no_collision(self):
        index = SymbolIndex([digit(1), digit(2)])
        assert index.glyph_collisions() == {}


class TestReproducibility:
    def test_two_indexes_over_the_same_entities_agree(self):
        entities = [digit(1), figure("one hollow blue square", "□")]
        assert SymbolIndex(entities).fingerprint() == SymbolIndex(
            list(reversed(entities))
        ).fingerprint()

    def test_a_policy_change_changes_the_fingerprint(self):
        """A board built under one policy is not the same board under another."""
        entities = [view("entity:tulip", "tulip")]
        plain = SymbolIndex(entities).fingerprint()
        renamed = SymbolIndex(
            entities, policy=SymbolPolicy(missing_glyph=MissingGlyph.USE_NAME)
        ).fingerprint()
        assert plain != renamed

    def test_a_spoken_override_changes_the_fingerprint(self):
        entities = [digit(7)]
        assert SymbolIndex(entities).fingerprint() != SymbolIndex(
            entities, policy=SymbolPolicy(spoken={"entity:7": "lucky seven"})
        ).fingerprint()
