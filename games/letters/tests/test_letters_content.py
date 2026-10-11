"""What game 2 asks the graph for.

A requirement is a promise about what a day will be handed, so the tests here
are about the request rather than about any answer to it: which taxonomy, how
the groups are formed, what is deliberately not constrained, and that the
names the generator reads results back by are the names the requirements were
filed under.

The absences get as much attention as the constraints. A similarity floor or a
frequency spread ceiling added here would refuse the curated sets this game
exists to use, and nothing in the engine would report that as anything other
than missing content.
"""

from __future__ import annotations

from puzzlegen.content.query import ContentRequirement, GroupingMode, Operation
from puzzlegen.core.types import DifficultyBand
from puzzlegen_letters.content import (
    CANDIDATES,
    MINIMUM_CONFIDENCE,
    POOL,
    TAXONOMY,
    category_query,
    content_requirements,
    describe_requirements,
    sized,
)
from puzzlegen_letters.descriptor import BOARD_SIZES, board_sizes_for

DAY = "2026-10-01"


def requirements(band: DifficultyBand = DifficultyBand.MEDIUM, **kwargs):
    return list(
        content_requirements(
            difficulty_target=band, locale="en", day_key=DAY, **kwargs
        )
    )


class TestTheRequest:
    def test_it_asks_the_game_s_own_taxonomy(self):
        """Section 6: at medium frequency exactly one WordNet category in
        7,050 had six or more eligible members."""
        assert TAXONOMY == "letters"
        assert category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        ).taxonomy == TAXONOMY

    def test_it_groups_by_shared_category_rather_than_by_siblings(self):
        """A curated category's direct members are examples of it. Game 1
        needs siblings because its taxonomy is derived from WordNet, where
        direct members are synonyms of one meaning."""
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        )
        assert query.grouping is GroupingMode.SHARED_CATEGORY
        assert query.operation is Operation.FIND_GROUPS

    def test_it_asks_for_a_group_the_size_of_the_board(self):
        for size in BOARD_SIZES:
            query = category_query(
                board_size=size, difficulty_target=DifficultyBand.MEDIUM, locale="en"
            )
            assert query.group_size == size

    def test_it_passes_the_difficulty_target_rather_than_a_frequency_band(self):
        """The engine owns the mapping, so a difficulty label means the same
        thing across games."""
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.HARD, locale="en"
        )
        assert query.difficulty_band is DifficultyBand.HARD
        assert query.frequency_band is None

    def test_it_asks_for_a_pool_rather_than_one_group_per_size(self):
        """A category is playable with the subset holding its ``fig`` and
        refused with the subset that does not, so the day needs several
        subsets to choose between."""
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        )
        assert query.limit == POOL
        assert POOL > len(BOARD_SIZES)

    def test_it_applies_the_confidence_floor_and_asks_for_fresh_content(self):
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        )
        assert query.minimum_confidence == MINIMUM_CONFIDENCE
        assert query.fresh_only is True


class TestWhatIsDeliberatelyNotAsked:
    def test_no_similarity_gate(self):
        """``polo`` is in ``sport`` because ``sport`` needs a ``p``, and no
        embedding will like it."""
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        )
        assert query.minimum_similarity is None
        assert query.maximum_similarity is None
        assert query.maximum_similarity_spread is None
        assert query.minimum_centroid_similarity is None

    def test_no_frequency_spread_ceiling(self):
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        )
        assert query.maximum_frequency_spread is None

    def test_no_second_axis(self):
        """This game has one axis: a tile means the category and nothing
        else."""
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        )
        assert query.intersects_taxonomy is None
        assert query.covering_taxonomy is None
        assert query.secondary_category is None

    def test_the_letters_are_not_asked_for_at_all(self):
        """Section 2.3: the character-level constraint is computed in the game,
        and the content service grows no new operation for it."""
        query = category_query(
            board_size=6, difficulty_target=DifficultyBand.MEDIUM, locale="en"
        )
        assert query.predicate is None
        assert query.predicate_value is None


class TestTheRequirements:
    def test_one_per_board_size_in_the_day_s_own_order(self):
        names = [r.name for r in requirements()]
        assert names == [sized(CANDIDATES, size) for size in board_sizes_for(DAY)]

    def test_every_size_is_optional_including_the_day_s_own(self):
        """A size is a preference. Marking the preferred one required would
        fail a day that has a good board one size down."""
        assert all(r.optional for r in requirements())

    def test_each_requirement_is_resolvable_without_duplicates(self):
        from puzzlegen.content.query import resolve_queries

        resolved = resolve_queries(requirements())
        assert len(resolved) == len(BOARD_SIZES)

    def test_a_salt_changes_the_order_rather_than_the_set(self):
        plain = {r.name for r in requirements()}
        salted = {r.name for r in requirements(salt="staging")}
        assert plain == salted

    def test_every_requirement_is_the_declared_type(self):
        assert all(isinstance(r, ContentRequirement) for r in requirements())


class TestTheDiagnosticSummary:
    def test_it_says_what_the_day_asked_for_without_a_solver(self):
        summary = describe_requirements(
            difficulty_target=DifficultyBand.HARD, locale="en", day_key=DAY
        )
        assert summary["game_id"] == "missing_letters"
        assert summary["board_size"] in BOARD_SIZES
        assert summary["blanks"] == 4
        assert summary["taxonomy"] == TAXONOMY
        assert len(summary["requirements"]) == len(BOARD_SIZES)

    def test_it_names_the_sizes_the_day_will_try(self):
        summary = describe_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key=DAY
        )
        assert summary["board_sizes_tried"] == list(board_sizes_for(DAY))
