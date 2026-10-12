"""Choosing a board with exactly one odd number out.

The construction is a draw with one check that cannot be designed away:
whether some *other* tile is also readable is a fact about the whole board,
and the only way to know is to enumerate. So the tests below are mostly about
that enumeration being applied -- every board the generator offers has exactly
one reading -- and about the refusals being named when it is not.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import DifficultyBand
from puzzlegen_numbers.descriptor import BOARD_SIZES, board_sizes_for, tier_for
from puzzlegen_numbers.generate import (
    ATTEMPTS_PER_SIZE,
    NO_OUTSIDER,
    NO_PROPERTY_AT_TIER,
    SECOND_ANSWER,
    TOO_FEW_MEMBERS,
    VALUE_MISSING,
    BoardPlan,
    generate_candidates,
    last_rejections,
    plan_for,
    properties_at_tier,
    ranked,
    readable,
    rationale_for,
    unusable_reason,
    value_of,
)
from puzzlegen_numbers.properties import (
    MAX_TIER,
    MIN_TIER,
    readings_of,
    tier_of,
    vocabulary_of,
)

from boards import context, entity, result, window


@pytest.fixture
def prepared():
    pool, values, memberships, index = readable(result(window()))
    return pool, values, memberships, index


class TestReadingTheContent:
    def test_every_number_in_the_window_is_readable(self, prepared):
        pool, values, memberships, _ = prepared
        assert len(pool) == 90
        assert values[pool[0].entity_id] == 10
        assert all(memberships[view.entity_id] for view in pool)

    def test_the_pool_comes_back_ascending(self, prepared):
        pool, values, _, _ = prepared
        got = [values[view.entity_id] for view in pool]
        assert got == sorted(got)

    def test_the_value_is_read_from_the_fact_not_the_name(self):
        """``entity:7`` has a numeric id by accident of slugging, and the same
        number exported under a different name would parse to nothing."""
        view = entity(42)
        renamed = type(view)(
            entity_id="entity:forty-two",
            name="forty-two",
            properties=dict(view.properties),
            types=view.types,
            type_ids=view.type_ids,
        )
        assert value_of(renamed) == 42

    def test_a_number_with_no_value_is_dropped_and_counted(self):
        views = window()
        first = views[0]
        valueless = type(first)(
            entity_id=first.entity_id,
            name=first.name,
            properties={k: v for k, v in first.properties.items() if k != "value"},
            types=first.types,
            type_ids=first.type_ids,
        )
        pool, _, _, _ = readable(result([valueless, *views[1:]]))
        assert len(pool) == len(views) - 1

        generate_candidates(context([valueless, *views[1:]]))
        assert last_rejections()[VALUE_MISSING] == 1

    def test_a_boolean_value_is_a_flag_rather_than_a_number(self):
        view = entity(42)
        flagged = type(view)(
            entity_id=view.entity_id,
            name=view.name,
            properties={**view.properties, "value": True},
            types=view.types,
        )
        assert value_of(flagged) is None


class TestThePropertiesAvailable:
    @pytest.mark.parametrize("tier", [MIN_TIER, MIN_TIER + 1, MAX_TIER])
    def test_every_tier_has_properties_in_the_window(self, prepared, tier):
        """A tier with nothing in it is a band that cannot be served."""
        pool, _, memberships, _ = prepared
        found = properties_at_tier(pool, memberships, tier)
        assert found
        assert all(tier_of(name) == tier for name in found)

    def test_a_universal_property_is_not_offered(self, prepared):
        """Every number in the window is a two-digit number, so it has no
        outsider and is a reading of no board."""
        pool, _, memberships, _ = prepared
        for tier in (MIN_TIER, MIN_TIER + 1, MAX_TIER):
            assert "two-digit number" not in properties_at_tier(
                pool, memberships, tier
            )


class TestTheConstruction:
    @pytest.mark.parametrize("board_size", BOARD_SIZES)
    @pytest.mark.parametrize("tier", [MIN_TIER, MIN_TIER + 1, MAX_TIER])
    def test_every_board_has_exactly_one_reading(self, prepared, board_size, tier):
        """The construction's whole claim, over sixty draws per shape.

        Checked by recomputing the readings from the plan's own memberships,
        which is the same computation the generator used. That is a deliberate
        exception to the figure game's rule that the check be independent: the
        thing the generator needs to know here is exactly what the verifier
        computes, and a second implementation would be two things to keep in
        step for no gain. What the verifier adds is that it runs against the
        assembled board.
        """
        pool, values, memberships, index = prepared
        root = DeterministicRng(b"numbers" * 4)
        built = 0
        for attempt in range(60):
            plan, reason = plan_for(
                pool=pool,
                values=values,
                memberships=memberships,
                index=index,
                board_size=board_size,
                tier=tier,
                rng=root.derive(f"{board_size}:{tier}:{attempt}"),
            )
            if plan is None:
                assert reason in (TOO_FEW_MEMBERS, SECOND_ANSWER, NO_OUTSIDER)
                continue
            built += 1
            found = readings_of(plan.memberships, vocabulary_of(plan.memberships))
            assert list(found) == [plan.answer]
            assert tier_of(plan.property_name) == tier
            assert len(plan.tiles) == board_size
        assert built, f"no board at size {board_size} tier {tier}"

    def test_the_tiles_are_in_ascending_order(self, prepared):
        pool, values, memberships, index = prepared
        plan, _ = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=6, tier=MIN_TIER, rng=DeterministicRng(b"order" * 8),
        )
        got = [plan.values[view.entity_id] for view in plan.tiles]
        assert got == sorted(got)

    def test_the_answer_is_outside_the_shared_property(self, prepared):
        pool, values, memberships, index = prepared
        plan, _ = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=6, tier=MIN_TIER, rng=DeterministicRng(b"out" * 10),
        )
        assert plan.property_name not in plan.memberships[plan.answer]
        for tile, names in plan.memberships.items():
            if tile != plan.answer:
                assert plan.property_name in names

    def test_the_same_draw_builds_the_same_board(self, prepared):
        pool, values, memberships, index = prepared
        def build():
            return plan_for(
                pool=pool, values=values, memberships=memberships, index=index,
                board_size=6, tier=MIN_TIER, rng=DeterministicRng(b"same" * 8),
            )[0]
        assert build().candidate_id() == build().candidate_id()

    def test_a_tier_with_no_property_is_refused(self, prepared):
        pool, values, memberships, index = prepared
        plan, reason = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=6, tier=99, rng=DeterministicRng(b"tier" * 8),
        )
        assert plan is None and reason == NO_PROPERTY_AT_TIER

    def test_a_property_with_too_few_members_is_refused(self, prepared):
        """Five two-digit numbers are Fibonacci, so a seven-tile board needs
        six of the five there are."""
        pool, values, memberships, index = prepared
        fibonacci = [
            v for v in pool if "Fibonacci number" in memberships[v.entity_id]
        ]
        assert len(fibonacci) == 5
        thinned = [*fibonacci, *[v for v in pool if v not in fibonacci][:20]]
        reasons = set()
        for attempt in range(40):
            plan, reason = plan_for(
                pool=thinned, values=values, memberships=memberships, index=index,
                board_size=7, tier=MAX_TIER, rng=DeterministicRng(b"thin" * 8).derive(str(attempt)),
            )
            if plan is None:
                reasons.add(reason)
        assert TOO_FEW_MEMBERS in reasons

    def test_a_second_answer_is_refused(self, prepared):
        """The check that cannot be constructed away. Over enough draws some
        of them land on a board where another tile is also readable."""
        pool, values, memberships, index = prepared
        reasons = set()
        for attempt in range(200):
            plan, reason = plan_for(
                pool=pool, values=values, memberships=memberships, index=index,
                board_size=5, tier=MAX_TIER,
                rng=DeterministicRng(b"second" * 6).derive(str(attempt)),
            )
            if plan is None:
                reasons.add(reason)
        assert SECOND_ANSWER in reasons


class TestThePlan:
    def test_a_plan_measures_what_the_difficulty_model_reads(self, prepared):
        pool, values, memberships, index = prepared
        plan, reason = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=6, tier=MIN_TIER + 1, rng=DeterministicRng(b"plan" * 8),
        )
        assert reason == ""
        assert isinstance(plan, BoardPlan)
        assert plan.reasons
        assert plan.vocabulary
        assert plan.spread() > 0
        assert "number" in plan.universal

    def test_a_plan_declares_every_tile_it_rests_on(self, prepared):
        pool, values, memberships, index = prepared
        plan, _ = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=6, tier=MIN_TIER, rng=DeterministicRng(b"deps" * 8),
        )
        assert set(plan.dependency_ids()) >= {v.entity_id for v in plan.tiles}

    def test_the_id_does_not_depend_on_the_property(self, prepared):
        """A board with the same tiles and the same odd one out is the same
        board however many properties pick it out."""
        pool, values, memberships, index = prepared
        plan, _ = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=6, tier=MIN_TIER, rng=DeterministicRng(b"id" * 16),
        )
        from dataclasses import replace

        renamed = replace(plan, property_name="something else")
        assert renamed.candidate_id() == plan.candidate_id()

    def test_the_rationale_names_the_property_and_its_tier(self, prepared):
        pool, values, memberships, index = prepared
        plan, _ = plan_for(
            pool=pool, values=values, memberships=memberships, index=index,
            board_size=6, tier=MIN_TIER, rng=DeterministicRng(b"why" * 10),
        )
        text = rationale_for(plan)
        assert plan.property_name in text and "tier" in text


class TestTheRanking:
    def test_boards_at_the_bands_tier_come_first(self, prepared):
        pool, values, memberships, index = prepared
        plans = []
        for tier in (MIN_TIER, MIN_TIER + 1, MAX_TIER):
            for attempt in range(6):
                plan, _ = plan_for(
                    pool=pool, values=values, memberships=memberships, index=index,
                    board_size=5, tier=tier,
                    rng=DeterministicRng(b"rank" * 8).derive(f"{tier}:{attempt}"),
                )
                if plan is not None:
                    plans.append(plan)
        ordered = ranked(plans, MAX_TIER)
        distances = [abs(tier_of(p.property_name) - MAX_TIER) for p in ordered]
        assert distances == sorted(distances)

    def test_fewer_reasons_comes_before_more_at_the_same_tier(self, prepared):
        pool, values, memberships, index = prepared
        plans = []
        for attempt in range(40):
            plan, _ = plan_for(
                pool=pool, values=values, memberships=memberships, index=index,
                board_size=5, tier=MIN_TIER + 1,
                rng=DeterministicRng(b"reasons" * 5).derive(str(attempt)),
            )
            if plan is not None:
                plans.append(plan)
        ordered = ranked(plans, MIN_TIER + 1)
        counts = [len(p.reasons) for p in ordered]
        assert counts == sorted(counts)

    def test_the_order_is_a_pure_function_of_the_plans(self, prepared):
        pool, values, memberships, index = prepared
        plans = [
            plan_for(
                pool=pool, values=values, memberships=memberships, index=index,
                board_size=5, tier=MIN_TIER,
                rng=DeterministicRng(b"pure" * 8).derive(str(i)),
            )[0]
            for i in range(10)
        ]
        plans = [p for p in plans if p is not None]
        first = [p.candidate_id() for p in ranked(plans, MIN_TIER)]
        second = [p.candidate_id() for p in ranked(list(reversed(plans)), MIN_TIER)]
        assert first == second


class TestGeneratingADay:
    def test_a_day_offers_many_boards(self):
        candidates = generate_candidates(context(window(), budget=512))
        assert len(candidates) >= ATTEMPTS_PER_SIZE
        assert len({c.candidate_id for c in candidates}) == len(candidates)

    def test_every_candidate_declares_the_records_it_used(self):
        for candidate in generate_candidates(context(window())):
            assert candidate.fact_refs
            assert len(set(candidate.fact_refs)) == len(candidate.fact_refs)

    def test_the_days_own_size_leads(self):
        day = "2026-10-01"
        candidates = generate_candidates(context(window(), day_key=day))
        assert candidates[0].payload["board_size"] == board_sizes_for(day)[0]

    def test_every_size_is_offered(self):
        candidates = generate_candidates(context(window(), budget=512))
        assert {c.payload["board_size"] for c in candidates} == set(BOARD_SIZES)

    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_leading_board_is_at_its_bands_tier(self, band):
        candidates = generate_candidates(context(window(), band=band))
        assert tier_of(candidates[0].payload["property"]) == tier_for(band)

    def test_the_same_day_generates_the_same_candidates(self):
        first = generate_candidates(context(window()))
        second = generate_candidates(context(window()))
        assert [c.candidate_id for c in first] == [c.candidate_id for c in second]

    def test_two_days_generate_different_candidates(self):
        first = generate_candidates(context(window(), day_key="2026-10-01"))
        second = generate_candidates(context(window(), day_key="2026-10-02"))
        assert [c.candidate_id for c in first] != [c.candidate_id for c in second]

    def test_a_salt_moves_the_whole_stream(self):
        plain = generate_candidates(context(window()), salt="")
        salted = generate_candidates(
            context(window(), salt="staging"), salt="staging"
        )
        assert [c.candidate_id for c in plain] != [c.candidate_id for c in salted]

    def test_the_budget_is_respected(self):
        assert len(generate_candidates(context(window(), budget=5))) == 5

    def test_a_day_with_no_numbers_says_so(self):
        assert generate_candidates(context([])) == ()
        assert "no numbers" in unusable_reason()

    def test_the_rejections_are_cleared_between_runs(self):
        generate_candidates(context([]))
        assert last_rejections()
        generate_candidates(context(window()))
        assert "no numbers" not in last_rejections()
