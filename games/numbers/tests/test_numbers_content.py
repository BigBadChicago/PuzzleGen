"""What the game asks the graph for, and why two of the constraints had to be
part of the query rather than work done afterwards.

This is the first content module in the repository to use a value window and a
value ordering, and both are here for reasons a test can state precisely. The
window keeps the board from giving its answer away by size. The ordering is
needed because the service applies the limit: a caller sorting what came back
has sorted whichever ninety numbers the pool offered first, in entity id order,
which for numbers is lexicographic.
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
from puzzlegen_numbers.content import (
    CANDIDATES,
    MINIMUM_CONFIDENCE,
    MINIMUM_NUMBERS,
    POOL,
    ROOT,
    TAXONOMY,
    VALUE,
    content_requirements,
    describe_requirements,
    number_query,
)
from puzzlegen_numbers.descriptor import BOARD_SIZES, GAME_ID, WINDOW
from puzzlegen_numbers.generate import readable
from puzzlegen_numbers.properties import universal_in, vocabulary_of

NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
DAY = "2026-10-01"


class TestTheQuery:
    def test_it_asks_for_the_members_of_the_taxonomys_root(self):
        query = number_query(locale="en")
        assert query.operation is Operation.FIND_CATEGORY_MEMBERS
        assert query.category == ROOT
        assert query.taxonomy == TAXONOMY

    def test_it_pins_the_magnitude_to_one_window(self):
        low, high = WINDOW
        query = number_query(locale="en")
        assert query.predicate == VALUE
        assert query.predicate_at_least == low
        assert query.predicate_at_most == high

    def test_it_asks_for_ascending_order(self):
        assert number_query(locale="en").order is ResultOrder.PREDICATE_ASCENDING

    def test_the_window_predicate_and_the_order_predicate_are_the_same(self):
        """A game using two different predicates for those would sort a board
        by one number and filter it by another."""
        query = number_query(locale="en")
        assert query.predicate == VALUE
        from puzzlegen.symbols import VALUE_PREDICATE

        assert VALUE == VALUE_PREDICATE

    def test_it_applies_no_similarity_or_frequency_gate(self):
        query = number_query(locale="en")
        assert query.minimum_similarity is None
        assert query.minimum_centroid_similarity is None
        assert query.frequency_band is None
        assert query.difficulty_band is None

    def test_it_asks_for_no_second_axis(self):
        query = number_query(locale="en")
        assert query.intersects_taxonomy is None
        assert query.covering_taxonomy is None

    def test_it_asks_for_the_whole_window(self):
        low, high = WINDOW
        assert POOL >= high - low + 1

    def test_the_confidence_floor_is_above_the_activation_policys(self):
        assert MINIMUM_CONFIDENCE > 0.75
        assert number_query(locale="en").minimum_confidence == MINIMUM_CONFIDENCE


class TestTheRequirements:
    def test_there_is_one_and_it_is_required(self):
        requirements = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert len(requirements) == 1
        assert requirements[0].name == CANDIDATES
        assert not requirements[0].optional

    def test_the_minimum_is_a_vocabulary_floor_not_a_board_floor(self):
        requirements = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert requirements[0].minimum == MINIMUM_NUMBERS
        assert MINIMUM_NUMBERS > max(BOARD_SIZES) * 4

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
        assert summary["window"] == list(WINDOW)
        assert summary["board_size"] in BOARD_SIZES


class TestAgainstAGeneratedSnapshot:
    """The request, run against content a provider actually built."""

    @pytest.fixture
    def port(self, repos):
        from puzzlegen.providers.generated import NumberRangeProvider

        meta, _ = SnapshotBuilder(repos, now=NOW).build(
            "numbers-test",
            providers=[
                (
                    NumberRangeProvider(0, 99, name=TAXONOMY, now=NOW),
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
        low, high = WINDOW
        assert len(result.entities) == high - low + 1

    def test_only_the_window_comes_back(self, result):
        low, high = WINDOW
        values = [view.properties["value"] for view in result.entities]
        assert min(values) == low and max(values) == high

    def test_the_order_survives_the_limit(self, port):
        """The reason the ordering had to be a query constraint. A caller
        asking for twenty numbers and sorting them would hold a sorted sample
        of whichever twenty came first in id order."""
        from dataclasses import replace

        query = replace(number_query(locale="en"), limit=20)
        values = [
            view.properties["value"] for view in port.request(query).entities
        ]
        low, _ = WINDOW
        assert values == list(range(low, low + 20))

    def test_every_number_comes_back_readable_with_a_glyph(self, result):
        pool, values, memberships, index = readable(result)
        assert len(pool) == len(result.entities)
        assert all(index.form(view.entity_id).playable for view in pool)

    def test_the_digit_count_is_universal_inside_the_window(self, result):
        """The window's whole purpose: otherwise digit count is the easiest
        reading on every board, every day."""
        _, _, memberships, _ = readable(result)
        assert "two-digit number" in universal_in(memberships)
        assert "two-digit number" not in vocabulary_of(memberships)

    def test_the_vocabulary_the_window_offers_is_the_rated_one(self, result):
        from puzzlegen_numbers.properties import TIERS

        _, _, memberships, _ = readable(result)
        assert set(vocabulary_of(memberships)) <= set(TIERS)

    def test_the_port_issued_every_record_the_game_will_report(self, result, port):
        assert set(result.dependency_ids()) <= port.issued_ids()
