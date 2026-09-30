"""Which word stands for a child category, decided once for every caller."""

from __future__ import annotations

import itertools
import random

import pytest

from puzzlegen.content.representatives import choose_representatives

NAMES = {"c.violin": "violin", "c.viola": "viola", "c.cornet": "cornet", "c.french": "French horn"}


def members(**by_category):
    """Direct members as (id, name), with the id the name lowercased."""
    return {
        cid: [(f"e:{name}", name) for name in names] for cid, names in by_category.items()
    }


class TestTheFirstLemma:
    def test_the_word_bearing_the_categorys_name_stands_for_it(self):
        chosen = choose_representatives(
            {"c.violin": "violin"}, members(**{"c.violin": ["violin", "fiddle"]})
        )

        assert chosen == {"c.violin": "e:violin"}

    def test_a_category_with_no_such_word_is_left_out(self):
        chosen = choose_representatives(
            {"c.violin": "violin"}, members(**{"c.violin": ["fiddle"]})
        )

        assert chosen == {}

    def test_nothing_carrying_means_the_first_lemma_everywhere(self):
        chosen = choose_representatives(
            NAMES,
            members(**{"c.violin": ["violin", "fiddle"], "c.viola": ["viola"]}),
            frozenset(),
        )

        assert chosen == {"c.violin": "e:violin", "c.viola": "e:viola"}


class TestTheTaggedWord:
    def test_a_tagged_synonym_beats_the_first_lemma(self):
        chosen = choose_representatives(
            {"c.violin": "violin"},
            members(**{"c.violin": ["violin", "fiddle"]}),
            frozenset({"e:fiddle"}),
        )

        assert chosen == {"c.violin": "e:fiddle"}

    def test_when_the_first_lemma_is_tagged_it_stays(self):
        chosen = choose_representatives(
            {"c.violin": "violin"},
            members(**{"c.violin": ["violin", "fiddle"]}),
            frozenset({"e:violin", "e:fiddle"}),
        )

        assert chosen == {"c.violin": "e:violin"}

    def test_two_tagged_synonyms_are_settled_by_id(self):
        chosen = choose_representatives(
            {"c.violin": "violin"},
            members(**{"c.violin": ["violin", "fiddle", "kit"]}),
            frozenset({"e:kit", "e:fiddle"}),
        )

        assert chosen == {"c.violin": "e:fiddle"}

    def test_a_tagged_word_stands_even_with_no_word_bearing_the_name(self):
        chosen = choose_representatives(
            {"c.violin": "violin"},
            members(**{"c.violin": ["fiddle"]}),
            frozenset({"e:fiddle"}),
        )

        assert chosen == {"c.violin": "e:fiddle"}


class TestASharedWord:
    def shared(self):
        return {
            "c.cornet": [("e:cornet", "cornet"), ("e:horn", "horn")],
            "c.french": [("e:French horn", "French horn"), ("e:horn", "horn")],
        }

    def test_a_word_stands_for_at_most_one_category(self):
        """``horn`` sits under two children of one parent. Standing for both
        would put it in a group twice."""
        chosen = choose_representatives(NAMES, self.shared(), frozenset({"e:horn"}))

        assert len(set(chosen.values())) == len(chosen)
        assert list(chosen.values()).count("e:horn") == 1

    def test_the_category_that_loses_it_keeps_its_own_name(self):
        chosen = choose_representatives(NAMES, self.shared(), frozenset({"e:horn"}))

        assert chosen == {"c.french": "e:horn", "c.cornet": "e:cornet"}

    def test_the_winner_is_the_same_whatever_order_the_input_arrives_in(self):
        outcomes = set()
        entries = list(self.shared().items())
        for order in itertools.permutations(entries):
            reordered = {cid: list(reversed(found)) for cid, found in order}
            outcomes.add(
                tuple(sorted(choose_representatives(NAMES, reordered, frozenset({"e:horn"})).items()))
            )

        assert len(outcomes) == 1


class TestAnOwnNameIsReserved:
    def test_one_categorys_first_lemma_is_never_anothers_tagged_synonym(self):
        """"Horn" is the first lemma of the horn category and a synonym of
        cornet. Tagging it must not cost the horn category its tile."""
        names = {"c.horn": "horn", "c.cornet": "cornet"}
        found = {
            "c.horn": [("e:horn", "horn")],
            "c.cornet": [("e:cornet", "cornet"), ("e:horn", "horn")],
        }

        chosen = choose_representatives(names, found, frozenset({"e:horn"}))

        assert chosen == {"c.horn": "e:horn", "c.cornet": "e:cornet"}


class TestDeterminism:
    @pytest.mark.parametrize("seed", range(5))
    def test_shuffling_the_input_changes_nothing(self, seed):
        names = {f"c.{n}": n for n in "abcdef"}
        found = {
            f"c.{n}": [(f"e:{n}", n), (f"e:{n}x", f"{n}x"), ("e:shared", "shared")]
            for n in "abcdef"
        }
        carrying = frozenset({"e:shared", "e:bx", "e:dx"})
        expected = choose_representatives(names, found, carrying)

        rng = random.Random(seed)
        shuffled = {}
        for cid in rng.sample(list(found), len(found)):
            entries = list(found[cid])
            rng.shuffle(entries)
            shuffled[cid] = entries

        assert choose_representatives(names, shuffled, carrying) == expected
