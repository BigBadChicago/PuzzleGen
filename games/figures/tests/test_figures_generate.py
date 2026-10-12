"""Choosing a board with exactly one trio in it.

The construction's claim is strong and cheap to check: every board it builds
holds exactly one trio, by an enumeration this module runs independently of
the shortcut the construction uses. Most of what follows is that claim at
different sizes, under different draws, against the whole figure space and
against a space too small to work in.
"""

from __future__ import annotations

import pytest

from puzzlegen.content.query import ContentResult, Operation
from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import DifficultyBand
from puzzlegen_figures import content as content_module
from puzzlegen_figures.descriptor import BOARD_SIZES, band_target, board_sizes_for
from puzzlegen_figures.generate import (
    ATTEMPTS_PER_SIZE,
    BOARD_NOT_FILLABLE,
    COMPLETION_ABSENT,
    GLYPH_MISSING,
    TOO_FEW_FIGURES,
    BoardPlan,
    build_board,
    figure_of,
    generate_candidates,
    last_rejections,
    plan_for,
    ranked,
    readable,
    unusable_reason,
)
from puzzlegen_figures.trio import ATTRIBUTES, check_space, trios_in

from boards import SPACE, context, entity, figure, result, whole_space


@pytest.fixture
def space_views():
    return whole_space()


@pytest.fixture
def prepared(space_views):
    pool, figures, index = readable(result(space_views))
    return pool, figures, index, check_space([figures[v.entity_id] for v in pool])


class TestReadingTheContent:
    def test_every_figure_in_the_space_is_readable(self, space_views):
        pool, figures, index = readable(result(space_views))
        assert len(pool) == len(space_views) == 81
        assert all(len(figures[view.entity_id]) == len(ATTRIBUTES) for view in pool)

    def test_an_entity_missing_an_attribute_is_not_a_figure(self):
        broken = entity(figure("triangle", "solid", "one", "red"))
        stripped = type(broken)(
            entity_id=broken.entity_id,
            name=broken.name,
            properties={k: v for k, v in broken.properties.items() if k != "fill"},
        )
        assert figure_of(stripped) is None

    def test_an_entity_with_no_glyph_is_dropped_and_counted(self, space_views):
        """A figure the content gave no glyph is one this game cannot draw.

        Dropped rather than drawn from its name: a name is a phrase and a tile
        is one glyph wide, so the fallback would be a board of sentences.
        """
        first = space_views[0]
        glyphless = type(first)(
            entity_id=first.entity_id,
            name=first.name,
            properties={k: v for k, v in first.properties.items() if k != "glyph"},
        )
        pool, _, _ = readable(result([glyphless, *space_views[1:]]))
        assert len(pool) == len(space_views) - 1

        candidates = generate_candidates(context([glyphless, *space_views[1:]]))
        assert candidates
        assert last_rejections()[GLYPH_MISSING] == 1

    def test_the_attribute_names_come_from_the_facts_not_the_categories(self):
        """Read from facts, so a category named for one thing and holding
        another cannot silently change what a board is about."""
        view = entity(figure("triangle", "solid", "one", "red"))
        lying = type(view)(
            entity_id=view.entity_id,
            name=view.name,
            properties=dict(view.properties),
            types=("square shape",),
            type_ids=("category:square-shape",),
        )
        assert figure_of(lying)["shape"] == "triangle"


class TestTheConstruction:
    @pytest.mark.parametrize("board_size", BOARD_SIZES)
    def test_every_board_holds_exactly_one_trio(self, prepared, board_size):
        """The construction's whole claim, over a hundred draws per size.

        Checked by enumerating every triple, which is not how the board was
        built: the construction uses the fact that two figures determine a
        third, and a check using the same shortcut would be the same mistake
        twice.
        """
        pool, figures, _, space = prepared
        root = DeterministicRng(b"construction" * 4)
        for attempt in range(100):
            built = build_board(
                pool=pool,
                figures=figures,
                space=space,
                board_size=board_size,
                rng=root.derive(str(attempt)),
            )
            assert not isinstance(built, str), built
            board, answer = built
            assert len(board) == board_size
            assert len({view.entity_id for view in board}) == board_size
            readings = [figures[view.entity_id] for view in board]
            found = trios_in(readings)
            assert len(found) == 1
            ids = [view.entity_id for view in board]
            assert sorted(ids[index] for index in found[0]) == sorted(answer)

    def test_the_same_draw_builds_the_same_board(self, prepared):
        pool, figures, _, space = prepared
        rng = lambda: DeterministicRng(b"same" * 8).derive("one")
        first = build_board(pool=pool, figures=figures, space=space, board_size=9, rng=rng())
        second = build_board(pool=pool, figures=figures, space=space, board_size=9, rng=rng())
        assert [v.entity_id for v in first[0]] == [v.entity_id for v in second[0]]
        assert first[1] == second[1]

    def test_a_pool_smaller_than_the_board_is_refused(self, prepared):
        pool, figures, _, space = prepared
        built = build_board(
            pool=pool[:5],
            figures=figures,
            space=space,
            board_size=9,
            rng=DeterministicRng(b"small" * 8),
        )
        assert built == TOO_FEW_FIGURES

    def test_a_pool_missing_the_completion_is_refused(self, prepared):
        """On a complete space this cannot happen, so the refusal is a
        statement about the content rather than about the draw."""
        pool, figures, _, space = prepared
        reds = [v for v in pool if figures[v.entity_id]["colour"] == "red"]
        thinned = [*reds[:2], *[v for v in pool if figures[v.entity_id]["colour"] != "red"]]
        seen = set()
        for attempt in range(60):
            built = build_board(
                pool=thinned,
                figures=figures,
                space=space,
                board_size=9,
                rng=DeterministicRng(b"thin" * 8).derive(str(attempt)),
            )
            if isinstance(built, str):
                seen.add(built)
        assert COMPLETION_ABSENT in seen

    def test_a_board_that_cannot_be_filled_is_refused(self, prepared):
        """Twelve tiles from a pool of fourteen leaves the filler no room."""
        pool, figures, _, space = prepared
        refusals = set()
        for attempt in range(40):
            built = build_board(
                pool=pool[:14],
                figures=figures,
                space=space,
                board_size=12,
                rng=DeterministicRng(b"tight" * 8).derive(str(attempt)),
            )
            if isinstance(built, str):
                refusals.add(built)
        assert refusals <= {BOARD_NOT_FILLABLE, COMPLETION_ABSENT}
        assert refusals


class TestThePlan:
    def test_a_plan_measures_what_the_difficulty_model_reads(self, prepared):
        pool, figures, index, space = prepared
        plan, reason = plan_for(
            pool=pool,
            figures=figures,
            index=index,
            space=space,
            board_size=9,
            rng=DeterministicRng(b"plan" * 8),
        )
        assert reason == ""
        assert isinstance(plan, BoardPlan)
        assert plan.board_size == 9
        assert len(plan.answer) == 3
        assert 0.0 <= plan.decoy_share <= 1.0
        assert plan.near_misses == int(round(plan.decoy_share * 84))
        assert set(plan.varying) <= set(ATTRIBUTES)

    def test_a_plan_declares_every_tile_it_rests_on(self, prepared):
        pool, figures, index, space = prepared
        plan, _ = plan_for(
            pool=pool, figures=figures, index=index, space=space,
            board_size=9, rng=DeterministicRng(b"deps" * 8),
        )
        assert set(plan.dependency_ids()) >= {view.entity_id for view in plan.tiles}

    def test_two_plans_with_the_same_tiles_and_trio_have_the_same_id(self, prepared):
        pool, figures, index, space = prepared
        made = [
            plan_for(pool=pool, figures=figures, index=index, space=space,
                     board_size=9, rng=DeterministicRng(b"id" * 16).derive(str(i)))[0]
            for i in range(2)
        ]
        assert (made[0].candidate_id() == made[1].candidate_id()) is (
            sorted(t.entity_id for t in made[0].tiles)
            == sorted(t.entity_id for t in made[1].tiles)
            and sorted(made[0].answer) == sorted(made[1].answer)
        )


class TestTheRanking:
    def test_boards_nearest_the_band_target_come_first(self, prepared):
        pool, figures, index, space = prepared
        plans = []
        for attempt in range(40):
            plan, _ = plan_for(
                pool=pool, figures=figures, index=index, space=space,
                board_size=9, rng=DeterministicRng(b"rank" * 8).derive(str(attempt)),
            )
            plans.append(plan)
        want_varying, want_decoy = band_target(DifficultyBand.HARD)
        ordered = ranked(plans, (want_varying, want_decoy))
        distances = [abs(len(p.varying) - want_varying) for p in ordered]
        assert distances == sorted(distances)

    def test_the_order_is_a_pure_function_of_the_plans(self, prepared):
        pool, figures, index, space = prepared
        plans = [
            plan_for(pool=pool, figures=figures, index=index, space=space,
                     board_size=9, rng=DeterministicRng(b"pure" * 8).derive(str(i)))[0]
            for i in range(12)
        ]
        target = band_target(DifficultyBand.EASY)
        first = [p.candidate_id() for p in ranked(plans, target)]
        second = [p.candidate_id() for p in ranked(list(reversed(plans)), target)]
        assert first == second


class TestGeneratingADay:
    def test_a_day_offers_many_boards(self, space_views):
        """Both sizes' attempts, up to the engine's budget.

        The count asserted is the attempt budget rather than a round number:
        the construction refuses nothing on a complete space, so a day that
        offered materially fewer boards than it attempted would mean the
        construction had started failing.
        """
        candidates = generate_candidates(context(space_views, budget=512))
        assert len(candidates) == 2 * ATTEMPTS_PER_SIZE
        assert len({c.candidate_id for c in candidates}) == len(candidates)

    def test_every_candidate_declares_the_records_it_used(self, space_views):
        for candidate in generate_candidates(context(space_views)):
            assert candidate.fact_refs
            assert len(set(candidate.fact_refs)) == len(candidate.fact_refs)

    def test_the_days_own_size_leads(self, space_views):
        day = "2026-10-01"
        preferred = board_sizes_for(day)[0]
        candidates = generate_candidates(context(space_views, day_key=day))
        assert candidates[0].payload["board_size"] == preferred

    def test_both_sizes_are_offered(self, space_views):
        candidates = generate_candidates(context(space_views, budget=512))
        assert {c.payload["board_size"] for c in candidates} == set(BOARD_SIZES)

    def test_the_same_day_generates_the_same_candidates(self, space_views):
        first = generate_candidates(context(space_views))
        second = generate_candidates(context(space_views))
        assert [c.candidate_id for c in first] == [c.candidate_id for c in second]

    def test_two_days_generate_different_candidates(self, space_views):
        first = generate_candidates(context(space_views, day_key="2026-10-01"))
        second = generate_candidates(context(space_views, day_key="2026-10-02"))
        assert [c.candidate_id for c in first] != [c.candidate_id for c in second]

    def test_a_salt_moves_the_whole_stream(self, space_views):
        plain = generate_candidates(context(space_views), salt="")
        salted = generate_candidates(context(space_views, salt="staging"), salt="staging")
        assert [c.candidate_id for c in plain] != [c.candidate_id for c in salted]

    def test_the_budget_is_respected(self, space_views):
        assert len(generate_candidates(context(space_views, budget=5))) == 5

    def test_a_day_with_no_figures_says_so(self):
        assert generate_candidates(context([])) == ()
        assert "no figures" in unusable_reason()

    def test_a_day_whose_space_is_not_three_valued_says_which_attribute(self):
        """The refusal a build with a fourth colour would hit."""
        views = [v for v in whole_space() if v.properties["colour"] != "blue"]
        assert generate_candidates(context(views)) == ()
        assert "colour" in unusable_reason()

    def test_the_rejections_are_cleared_between_runs(self, space_views):
        generate_candidates(context([]))
        assert last_rejections()
        generate_candidates(context(space_views))
        assert "no figures" not in last_rejections()

    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_leading_board_is_near_its_bands_target(self, space_views, band):
        """The ranking is a preference the engine then verifies, so what is
        asserted is that the first board is at the target, not that every
        board is."""
        want_varying, _ = band_target(band)
        candidates = generate_candidates(context(space_views, band=band))
        assert len(candidates[0].payload["varying"]) == want_varying
