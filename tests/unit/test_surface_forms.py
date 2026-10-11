"""The letter definition the games share.

These tests are the readable record of the decisions in
:mod:`puzzlegen.surface.forms`: each one states a rule a game would otherwise
have invented for itself, and a board's legality depends on every one of them.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.query import EntityView
from puzzlegen.surface import (
    Diacritics,
    FormSource,
    Separators,
    SurfaceIndex,
    SurfacePolicy,
    canonical_letters,
    form_for,
    letter_pool,
)


def entity(entity_id: str, name: str, *aliases: str) -> EntityView:
    return EntityView(entity_id=entity_id, name=name, aliases=aliases)


class TestTheCanonicalLetters:
    def test_case_folds(self):
        assert canonical_letters("Salmon")[0] == "salmon"

    def test_a_diacritic_folds_to_its_base_letter(self):
        """The mark on a tile and the letter the answer needs must compare equal."""
        assert canonical_letters("jalapeño")[0] == "jalapeno"

    def test_a_diacritic_can_be_kept_instead(self):
        letters, refused = canonical_letters(
            "jalapeño", SurfacePolicy(diacritics=Diacritics.KEEP)
        )
        assert letters == "jalapeo"
        assert refused == ("ñ",)

    def test_a_sharp_s_folds_to_two_letters(self):
        """Case folding, not decomposition, so the length changes."""
        assert canonical_letters("straße")[0] == "strasse"

    def test_a_multi_word_name_collapses(self):
        assert canonical_letters("food fish")[0] == "foodfish"

    def test_hyphens_and_apostrophes_collapse_too(self):
        assert canonical_letters("hawk's-beard")[0] == "hawksbeard"

    def test_a_dropped_separator_is_not_a_refused_character(self):
        """A space is handled, not rejected; a curator reading ``refused``
        should see only what the alphabet could not take."""
        assert canonical_letters("food fish")[1] == ()

    def test_a_separator_kept_is_refused_by_the_alphabet(self):
        letters, refused = canonical_letters(
            "food fish", SurfacePolicy(separators=Separators.KEEP)
        )
        assert letters == "foodfish"
        assert refused == (" ",)

    def test_a_digit_is_refused_and_reported(self):
        """No tile can carry a 7, so it cannot be part of a letter form."""
        letters, refused = canonical_letters("vitamin b12")
        assert letters == "vitaminb"
        assert refused == ("1", "2")

    def test_a_refused_character_is_reported_once(self):
        assert canonical_letters("a1b1c1")[1] == ("1",)


class TestTheFormOfAnEntity:
    def test_the_primary_name_wins_by_default(self):
        form = form_for(entity("entity:cat", "cat", "moggy", "true cat"))
        assert form.display == "cat"
        assert form.source is FormSource.NAME

    def test_an_unreviewed_alias_does_not_win_for_being_short(self):
        """WordNet synonym sets put ``moggy`` beside ``cat``.

        Nothing in the graph says which alias is the common name, so taking
        the shortest would spell boards out of words no solver reaches for.
        """
        form = form_for(entity("entity:cat", "cat", "cv"))
        assert form.display == "cat"

    def test_a_curator_can_substitute_a_shorter_true_synonym(self):
        """The ``bowed stringed instrument`` case from the requirements."""
        form = form_for(
            entity("entity:fiddle", "bowed stringed instrument", "bowed instrument"),
            SurfacePolicy(preferred={"entity:fiddle": "bowed instrument"}),
        )
        assert form.display == "bowed instrument"
        assert form.letters == "bowedinstrument"
        assert form.source is FormSource.CURATED_ALIAS

    def test_a_preference_naming_the_primary_name_is_not_a_substitution(self):
        form = form_for(
            entity("entity:cat", "cat", "moggy"),
            SurfacePolicy(preferred={"entity:cat": "cat"}),
        )
        assert form.source is FormSource.NAME

    def test_a_preference_for_a_name_the_entity_lacks_is_refused(self):
        """A typo in curation must not be able to rename an entity."""
        with pytest.raises(ValueError) as exc:
            form_for(
                entity("entity:cat", "cat", "moggy"),
                SurfacePolicy(preferred={"entity:cat": "dog"}),
            )
        assert "is not a name of" in str(exc.value)

    def test_the_shortest_alias_can_be_opted_into(self):
        form = form_for(
            entity("entity:x", "long name", "short"),
            SurfacePolicy(prefer_shorter_alias=True),
        )
        assert form.display == "short"
        assert form.source is FormSource.SHORTER_ALIAS

    def test_a_longer_alias_never_wins(self):
        form = form_for(
            entity("entity:x", "cat", "domestic cat"),
            SurfacePolicy(prefer_shorter_alias=True),
        )
        assert form.display == "cat"

    def test_an_alias_tie_is_broken_alphabetically(self):
        """Export order must not decide which word a board spells."""
        policy = SurfacePolicy(prefer_shorter_alias=True)
        first = form_for(entity("entity:x", "aaaa", "bbb", "ccc"), policy)
        second = form_for(entity("entity:x", "aaaa", "ccc", "bbb"), policy)
        assert first.display == second.display == "bbb"

    def test_the_word_count_survives_the_collapse(self):
        form = form_for(entity("entity:x", "food fish"))
        assert form.word_count == 2
        assert form.letters == "foodfish"

    def test_a_name_with_no_letters_is_not_playable(self):
        form = form_for(entity("entity:x", "1984"))
        assert form.letters == ""
        assert form.playable is False

    def test_the_multiset_counts_repeats(self):
        """A name needing two ``s`` needs two tiles that can supply one."""
        assert form_for(entity("entity:x", "bass")).multiset() == {
            "b": 1,
            "a": 1,
            "s": 2,
        }


class TestTheIndex:
    def test_it_holds_a_form_per_entity(self):
        index = SurfaceIndex([entity("entity:a", "bass"), entity("entity:b", "carp")])
        assert len(index) == 2
        assert index.letters("entity:a") == "bass"
        assert "entity:b" in index

    def test_a_repeated_entity_is_computed_once(self):
        """Daily builds ask for overlapping member sets.

        The second arrival keeps the form already held rather than replacing
        it, which is also what makes a stale duplicate harmless.
        """
        index = SurfaceIndex([entity("entity:a", "bass")])
        index.add([entity("entity:a", "renamed"), entity("entity:b", "carp")])
        assert index.letters("entity:a") == "bass"
        assert len(index) == 2

    def test_asking_for_an_entity_it_was_never_given_raises(self):
        """A default would spell a board out of nothing."""
        with pytest.raises(KeyError):
            SurfaceIndex().form("entity:missing")

    def test_get_is_the_forgiving_reader(self):
        assert SurfaceIndex().get("entity:missing") is None

    def test_forms_come_back_in_entity_id_order(self):
        index = SurfaceIndex([entity("entity:b", "carp"), entity("entity:a", "bass")])
        assert [form.entity_id for form in index.forms()] == ["entity:a", "entity:b"]

    def test_the_fingerprint_does_not_depend_on_insertion_order(self):
        first = SurfaceIndex([entity("entity:a", "bass"), entity("entity:b", "carp")])
        second = SurfaceIndex([entity("entity:b", "carp"), entity("entity:a", "bass")])
        assert first.fingerprint() == second.fingerprint()

    def test_a_different_policy_is_a_different_fingerprint(self):
        """A board generated under one policy is not the same board under
        another, and the difference has to be visible."""
        members = [entity("entity:a", "jalapeño")]
        folded = SurfaceIndex(members)
        kept = SurfaceIndex(members, policy=SurfacePolicy(diacritics=Diacritics.KEEP))
        assert folded.fingerprint() != kept.fingerprint()

    def test_a_policy_that_changes_nothing_here_still_changes_the_fingerprint(self):
        """The policy is hashed whole, not only its effect on these entities.

        Two indexes agreeing by accident on one member set would answer
        differently on the next, so the fingerprint tracks the rules.
        """
        members = [entity("entity:a", "bass")]
        plain = SurfaceIndex(members)
        preferring = SurfaceIndex(
            members, policy=SurfacePolicy(preferred={"entity:other": "x"})
        )
        assert plain.fingerprint() != preferring.fingerprint()


class TestTheLetterPool:
    def test_it_counts_the_tiles_that_can_supply_a_letter(self):
        """Not the letters in the words: one tile carries at most one mark."""
        forms = [form_for(entity("entity:a", "bass")), form_for(entity("entity:b", "carp"))]
        pool = letter_pool(forms)
        assert pool["s"] == 1
        assert pool["a"] == 2

    def test_a_name_needing_two_of_a_letter_needs_two_tiles(self):
        forms = [form_for(entity("entity:a", "seahorse"))]
        assert letter_pool(forms)["s"] == 1
