"""What the game asks the graph for, and why the facts have to come from there.

The request is ten digits, which looks like a constant and is not. The
property clues state how many of a code's digits are prime and how many are
even, and those numbers come from the digit entities' own facts. Computing
primality in the game instead is one line shorter and wrong in a way that
would be hard to find: the clue would say one thing and the content another,
and the board would be unsolvable from what it shows.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.port import ContentPort
from puzzlegen.content.query import Operation, ResultOrder
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core.types import DifficultyBand
from puzzlegen_digits.content import (
    CANDIDATES,
    MINIMUM_CONFIDENCE,
    MINIMUM_DIGITS,
    POOL,
    ROOT,
    TAXONOMY,
    VALUE,
    content_requirements,
    describe_requirements,
    digit_query,
)
from puzzlegen_digits.descriptor import CODE_LENGTHS, GAME_ID, properties_for
from puzzlegen_digits.generate import readable
from puzzlegen_digits.lock import BASE, PROPERTY_KINDS, property_clues_for

NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
DAY = "2026-10-01"


class TestTheQuery:
    def test_it_asks_for_the_members_of_the_taxonomys_root(self):
        query = digit_query(locale="en")
        assert query.operation is Operation.FIND_CATEGORY_MEMBERS
        assert query.category == ROOT
        assert query.taxonomy == TAXONOMY

    def test_it_asks_for_ascending_order(self):
        """Ten entities and the order still matters: the generator builds the
        whole code space from the digits it is given, and a board's candidate
        id is a hash over what it produced."""
        query = digit_query(locale="en")
        assert query.order is ResultOrder.PREDICATE_ASCENDING
        assert query.predicate == VALUE

    def test_it_asks_for_more_digits_than_exist(self):
        """So a build with an eleventh digit makes the shortage visible in
        the requirement rather than invisible in the board."""
        assert POOL > BASE

    def test_it_applies_no_similarity_or_frequency_gate(self):
        query = digit_query(locale="en")
        assert query.minimum_similarity is None
        assert query.frequency_band is None
        assert query.difficulty_band is None

    def test_the_confidence_floor_is_above_the_activation_policys(self):
        assert MINIMUM_CONFIDENCE > 0.75
        assert digit_query(locale="en").minimum_confidence == MINIMUM_CONFIDENCE


class TestTheRequirements:
    def test_there_is_one_and_it_is_required(self):
        requirements = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert len(requirements) == 1
        assert requirements[0].name == CANDIDATES
        assert not requirements[0].optional

    def test_the_minimum_is_a_completeness_floor(self):
        """A clue may name any digit, so a solver ruling digits in and out
        needs all of them. A build with nine digits is a different game rather
        than a thinner one."""
        requirements = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert requirements[0].minimum == MINIMUM_DIGITS == BASE
        assert MINIMUM_DIGITS > max(CODE_LENGTHS)

    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_band_does_not_change_the_request(self, band):
        mine = content_requirements(
            difficulty_target=band, locale="en", day_key=DAY
        )
        theirs = content_requirements(
            difficulty_target=DifficultyBand.EASY, locale="en", day_key=DAY
        )
        assert mine[0].query == theirs[0].query

    def test_the_day_does_not_change_the_request(self):
        first = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key="2026-10-01"
        )
        second = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key="2027-01-01"
        )
        assert first[0].query == second[0].query

    def test_the_summary_names_what_a_curator_needs(self):
        summary = describe_requirements(
            difficulty_target=DifficultyBand.HARD, locale="en", day_key=DAY
        )
        assert summary["game_id"] == GAME_ID
        assert summary["taxonomy"] == TAXONOMY
        assert summary["code_length"] in CODE_LENGTHS
        assert summary["properties"] == list(properties_for(DifficultyBand.HARD))


class TestAgainstAGeneratedSnapshot:
    """The request, run against content a provider actually built."""

    @pytest.fixture
    def port(self, repos):
        from puzzlegen.providers.generated import NumberRangeProvider

        meta, _ = SnapshotBuilder(repos, now=NOW).build(
            "digits-test",
            providers=[
                (
                    NumberRangeProvider(
                        0, 9, name=TAXONOMY, kind="DIGIT", root_name=ROOT, now=NOW
                    ),
                    {"taxonomy": TAXONOMY, "entity_identity": "lemma"},
                )
            ],
        )
        service = ContentService(
            repos,
            policy=PolicyService(
                ContentPolicy(name="platform", minimum_confidence=0.7)
            ),
            snapshot=meta,
            now=NOW,
        )
        return ContentPort(service, game_id=GAME_ID)

    @pytest.fixture
    def result(self, port):
        return port.satisfy(
            content_requirements(
                difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
            )
        )[CANDIDATES]

    def test_the_requirement_is_met(self, result):
        assert result.satisfied, result.detail
        assert len(result.entities) == BASE

    def test_they_come_back_ascending(self, result):
        values = [view.properties["value"] for view in result.entities]
        assert values == list(range(BASE))

    def test_every_digit_carries_the_facts_a_clue_is_read_from(self, result):
        digits, facts, entity_ids, index = readable(result)
        assert digits == list(range(BASE))
        assert len(facts) == BASE
        assert all(index.form(entity_ids[digit]).playable for digit in digits)

    def test_the_graphs_primes_are_the_primes(self, result):
        """The clue says "exactly two are prime" and this is where the two
        comes from."""
        _, facts, _, _ = readable(result)
        assert {digit for digit, fact in facts.items() if fact.prime} == {2, 3, 5, 7}

    def test_a_property_clue_built_from_them_is_true_of_its_code(self, result):
        _, facts, _, _ = readable(result)
        clues = property_clues_for((1, 4, 7), facts, PROPERTY_KINDS)
        assert len(clues) == len(PROPERTY_KINDS)
        for clue in clues:
            assert clue.admits((1, 4, 7), facts)

    def test_a_digits_glyph_is_its_digit_and_its_label_is_the_word(self, result):
        """The clearest case the symbolic form subsystem exists for: neither
        is derivable from the other."""
        _, _, entity_ids, index = readable(result)
        form = index.form(entity_ids[7])
        assert form.glyph == "7"
        assert form.spoken == "seven"

    def test_the_port_issued_every_record_the_game_will_report(self, result, port):
        assert set(result.dependency_ids()) <= port.issued_ids()
