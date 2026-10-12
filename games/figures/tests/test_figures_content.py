"""What the game asks the graph for, and what it deliberately does not.

The request is one query with no similarity, frequency or covering constraint,
which is the shortest content module in the repository. That shortness is a
claim rather than an omission: none of the service's gates measures anything a
figure has, and the tests below say so one gate at a time, because a later
author adding a frequency band here would be adding a filter over a number
that does not exist.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.port import ContentPort
from puzzlegen.content.query import Operation
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core.types import DifficultyBand
from puzzlegen_figures.content import (
    CANDIDATES,
    MINIMUM_CONFIDENCE,
    MINIMUM_FIGURES,
    POOL,
    ROOT,
    TAXONOMY,
    content_requirements,
    describe_requirements,
    figure_query,
)
from puzzlegen_figures.descriptor import BOARD_SIZES, GAME_ID
from puzzlegen_figures.generate import readable
from puzzlegen_figures.trio import ATTRIBUTES, VALUES_PER_ATTRIBUTE, check_space

NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
DAY = "2026-10-01"


class TestTheQuery:
    def test_it_asks_for_the_members_of_the_taxonomys_root(self):
        query = figure_query(locale="en")
        assert query.operation is Operation.FIND_CATEGORY_MEMBERS
        assert query.category == ROOT
        assert query.taxonomy == TAXONOMY

    def test_it_takes_the_whole_subtree(self):
        """The provider puts each figure two levels below the root, so a depth
        of one would return the attribute categories' members and no figures.
        """
        assert figure_query(locale="en").taxonomy_depth is None

    def test_it_applies_no_similarity_gate(self):
        """A board never asks the player to see that the tiles resemble each
        other; it asks them to compare four attributes."""
        query = figure_query(locale="en")
        assert query.minimum_similarity is None
        assert query.maximum_similarity is None
        assert query.minimum_centroid_similarity is None
        assert query.similarity_strategy is None

    def test_it_applies_no_frequency_gate(self):
        """Nobody writes "two solid red triangles" often enough for a
        frequency table to hold it, and the number that came back would be
        about the English phrase rather than about the figure."""
        query = figure_query(locale="en")
        assert query.frequency_band is None
        assert query.difficulty_band is None
        assert query.maximum_frequency_spread is None

    def test_it_asks_for_no_second_axis(self):
        """This game has one axis: a tile is a figure and means nothing else."""
        query = figure_query(locale="en")
        assert query.intersects_taxonomy is None
        assert query.covering_taxonomy is None
        assert query.secondary_category is None

    def test_it_asks_for_the_whole_space(self):
        """The generator computes the figure that completes a trio, so a board
        seeded from a partial space would ask for figures the day never saw."""
        assert POOL >= VALUES_PER_ATTRIBUTE ** len(ATTRIBUTES)

    def test_the_confidence_floor_is_above_the_activation_policys(self):
        """Activation asks whether content may be used; this asks whether it
        should be shown to a player today."""
        assert MINIMUM_CONFIDENCE > 0.75
        assert figure_query(locale="en").minimum_confidence == MINIMUM_CONFIDENCE

    def test_it_asks_only_for_fresh_content(self):
        assert figure_query(locale="en").fresh_only


class TestTheRequirements:
    def test_there_is_one_and_it_is_required(self):
        """Game two declares one per board size because its content can serve
        one size and not another. This game's content is the same space
        whatever the size, so asking twice would be asking the same question.
        """
        requirements = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert len(requirements) == 1
        assert requirements[0].name == CANDIDATES
        assert not requirements[0].optional

    def test_the_minimum_is_a_real_floor(self):
        """Twenty-seven is the space with one attribute held constant. Below
        it the construction has too little room, and the requirement failing
        is a clearer signal than a day of exhausted attempts."""
        requirements = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert requirements[0].minimum == MINIMUM_FIGURES
        assert MINIMUM_FIGURES > max(BOARD_SIZES)

    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_band_does_not_change_the_request(self, band):
        """The band decides which board is built from the answer. A
        requirement that varied by band would make a day's content depend on
        something no later audit could reconstruct from the board."""
        for other in (DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD):
            mine = content_requirements(
                difficulty_target=band, locale="en", day_key=DAY
            )
            theirs = content_requirements(
                difficulty_target=other, locale="en", day_key=DAY
            )
            assert mine[0].query == theirs[0].query

    def test_the_day_does_not_change_the_request(self):
        first = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key="2026-10-01"
        )
        second = content_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key="2026-11-11"
        )
        assert first[0].query == second[0].query

    def test_the_summary_names_what_a_curator_needs(self):
        summary = describe_requirements(
            difficulty_target=DifficultyBand.HARD, locale="en", day_key=DAY
        )
        assert summary["game_id"] == GAME_ID
        assert summary["taxonomy"] == TAXONOMY
        assert summary["board_size"] in BOARD_SIZES
        assert sorted(summary["board_sizes_tried"]) == sorted(BOARD_SIZES)
        assert len(summary["band_target"]) == 2


class TestAgainstAGeneratedSnapshot:
    """The request, run against content a provider actually built.

    Built here rather than read from a committed snapshot, because the figure
    provider is generated: the whole space is a function of a declaration, so
    a snapshot of it is cheap to build in a test and a committed one would be
    an artifact that could drift.
    """

    @pytest.fixture
    def port(self, repos):
        from puzzlegen.providers.generated import FigureProvider

        meta, _ = SnapshotBuilder(repos, now=NOW).build(
            "figures-test",
            providers=[
                (
                    FigureProvider(name=TAXONOMY, now=NOW),
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

    def test_the_requirement_is_met(self, port):
        results = port.satisfy(
            content_requirements(
                difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
            )
        )
        result = results[CANDIDATES]
        assert result.satisfied, result.detail
        assert len(result.entities) == 81

    def test_every_figure_comes_back_readable_with_a_glyph(self, port):
        result = port.satisfy(
            content_requirements(
                difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
            )
        )[CANDIDATES]
        pool, figures, index = readable(result)
        assert len(pool) == len(result.entities)
        assert all(index.form(view.entity_id).playable for view in pool)

    def test_the_space_the_content_supplies_is_three_valued(self, port):
        """The property the rule needs, checked against the real content
        rather than against the game's own constant."""
        result = port.satisfy(
            content_requirements(
                difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
            )
        )[CANDIDATES]
        _, figures, _ = readable(result)
        space = check_space(list(figures.values()))
        assert {len(values) for values in space.values()} == {VALUES_PER_ATTRIBUTE}

    def test_the_port_issued_every_record_the_game_will_report(self, port):
        """A reference the port never issued is a fabricated dependency, which
        the engine treats as a protocol violation."""
        result = port.satisfy(
            content_requirements(
                difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
            )
        )[CANDIDATES]
        assert set(result.dependency_ids()) <= port.issued_ids()
