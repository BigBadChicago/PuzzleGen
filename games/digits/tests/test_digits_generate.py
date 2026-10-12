"""Choosing a code and the clues that pin it down.

The construction's claim is that exactly one code survives every clue, and the
tests below check it by rerunning the elimination independently of the walk
that produced it. The other half of the claim is the one the contract rests
on: the surviving code is the intended one, so a solver who reasons correctly
arrives at the answer rather than at a choice.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.rng import DeterministicRng
from puzzlegen.core.types import DifficultyBand
from puzzlegen_digits.descriptor import (
    CODE_LENGTHS,
    MAX_CLUES,
    code_lengths_for,
    properties_for,
)
from puzzlegen_digits.generate import (
    ATTEMPTS_PER_LENGTH,
    CLUES_EXHAUSTED,
    FACTS_MISSING,
    GUESS_POOL,
    TOO_FEW_DIGITS,
    BoardPlan,
    facts_of,
    generate_candidates,
    last_rejections,
    plan_for,
    ranked,
    rationale_for,
    readable,
    unusable_reason,
)
from puzzlegen_digits.lock import PROPERTY_KINDS, space_for, survivors

from boards import context, digits, entity, facts, result


@pytest.fixture
def prepared():
    chosen, known, entity_ids, index = readable(result(digits()))
    return chosen, known, entity_ids, index


def build(prepared, *, code_length=3, properties=(), seed=b"digits" * 5, attempt=0):
    chosen, known, entity_ids, index = prepared
    return plan_for(
        digits=chosen,
        facts=known,
        entity_ids=entity_ids,
        index=index,
        code_length=code_length,
        properties=properties,
        rng=DeterministicRng(seed).derive(str(attempt)),
    )


class TestReadingTheContent:
    def test_every_digit_is_readable(self, prepared):
        chosen, known, entity_ids, _ = prepared
        assert chosen == list(range(10))
        assert len(known) == 10
        assert entity_ids[7] == "entity:7"

    def test_the_facts_come_from_the_content(self):
        assert facts_of(entity(7)).prime is True
        assert facts_of(entity(4)).even is True

    @pytest.mark.parametrize("missing", ["value", "is_prime", "parity"])
    def test_a_digit_without_a_fact_is_not_a_digit(self, missing):
        """A board built over nine digits is a different game, and the
        requirement rather than the generator is where that should surface."""
        view = entity(7)
        stripped = type(view)(
            entity_id=view.entity_id,
            name=view.name,
            properties={k: v for k, v in view.properties.items() if k != missing},
            types=view.types,
        )
        assert facts_of(stripped) is None

    def test_a_digit_without_facts_is_dropped_and_counted(self):
        views = digits()
        view = views[0]
        stripped = type(view)(
            entity_id=view.entity_id,
            name=view.name,
            properties={k: v for k, v in view.properties.items() if k != "parity"},
            types=view.types,
        )
        chosen, _, _, _ = readable(result([stripped, *views[1:]]))
        assert len(chosen) == 9
        generate_candidates(context([stripped, *views[1:]]))
        assert last_rejections()[FACTS_MISSING] == 1

    def test_a_boolean_value_is_a_flag_rather_than_a_digit(self):
        view = entity(7)
        flagged = type(view)(
            entity_id=view.entity_id,
            name=view.name,
            properties={**view.properties, "value": True},
            types=view.types,
        )
        assert facts_of(flagged) is None


class TestTheConstruction:
    @pytest.mark.parametrize("code_length", CODE_LENGTHS)
    @pytest.mark.parametrize("properties", [(), ("primes",), PROPERTY_KINDS])
    def test_exactly_one_code_survives_every_board(
        self, prepared, code_length, properties
    ):
        """The construction's whole claim, rerun independently of the walk.

        The walk eliminates incrementally; this reruns the elimination from the
        whole space against the finished clue set, which is what the verifier
        does. A board where the two disagreed would be one the generator
        thought it had pinned down and the verifier did not.
        """
        chosen, known, _, _ = prepared
        built = 0
        for attempt in range(12):
            plan, reason = build(
                prepared,
                code_length=code_length,
                properties=properties,
                attempt=attempt,
            )
            if plan is None:
                assert reason in (CLUES_EXHAUSTED, TOO_FEW_DIGITS)
                continue
            built += 1
            space = space_for(code_length, chosen)
            alive = survivors(space, plan.guesses, plan.properties, known)
            assert alive == [plan.code]
        assert built, f"no board at length {code_length} with {properties}"

    @pytest.mark.parametrize("code_length", CODE_LENGTHS)
    def test_no_clue_is_the_code(self, prepared, code_length):
        """A clue reporting every digit exact is the answer printed on the
        board, which is the one thing it may never do."""
        for attempt in range(12):
            plan, _ = build(prepared, code_length=code_length, attempt=attempt)
            if plan is None:
                continue
            for clue in plan.guesses:
                assert not clue.solves()
                assert clue.guess != plan.code

    def test_every_clue_is_true_of_the_code(self, prepared):
        from puzzlegen_digits.lock import verdict

        plan, _ = build(prepared)
        for clue in plan.guesses:
            assert verdict(clue.guess, plan.code) == (clue.exact, clue.misplaced)

    def test_every_stated_fact_is_true_of_the_code(self, prepared):
        _, known, _, _ = prepared
        plan, _ = build(prepared, properties=PROPERTY_KINDS)
        assert plan.properties
        for clue in plan.properties:
            assert clue.admits(plan.code, known)

    def test_no_clue_removes_nothing(self, prepared):
        """A redundant clue is noise, and the stopping rule counts clues, so a
        padded board would measure as harder than it is."""
        chosen, known, _, _ = prepared
        plan, _ = build(prepared)
        space = space_for(plan.code_length, chosen)
        for index in range(len(plan.guesses)):
            without = [c for i, c in enumerate(plan.guesses) if i != index]
            assert len(
                survivors(space, without, plan.properties, known)
            ) > 1

    def test_the_clue_count_stays_inside_the_ceiling(self, prepared):
        for attempt in range(12):
            plan, _ = build(prepared, code_length=4, attempt=attempt)
            if plan is not None:
                assert plan.clue_count() <= MAX_CLUES

    def test_stating_facts_needs_fewer_guesses(self, prepared):
        """The band's lever, measured. Over twelve draws, boards with all
        three facts stated need fewer guess clues than boards with none."""
        def clues(properties):
            counts = []
            for attempt in range(12):
                plan, _ = build(
                    prepared, properties=properties, attempt=attempt
                )
                if plan is not None:
                    counts.append(plan.clue_count())
            return sum(counts) / len(counts)

        assert clues(PROPERTY_KINDS) < clues(())

    def test_the_same_draw_builds_the_same_board(self, prepared):
        first, _ = build(prepared)
        second, _ = build(prepared)
        assert first.candidate_id() == second.candidate_id()

    def test_too_few_digits_for_the_length_is_refused(self, prepared):
        chosen, known, entity_ids, index = prepared
        plan, reason = plan_for(
            digits=chosen[:2],
            facts=known,
            entity_ids=entity_ids,
            index=index,
            code_length=4,
            properties=(),
            rng=DeterministicRng(b"short" * 8),
        )
        assert plan is None and reason == TOO_FEW_DIGITS

    def test_a_board_the_clue_ceiling_cannot_pin_down_is_refused(self, prepared):
        """What ``MAX_CLUES`` protects. With the ceiling at two, most draws
        cannot reach one survivor and say so."""
        chosen, known, entity_ids, index = prepared
        import puzzlegen_digits.generate as module

        original = module.MAX_CLUES
        module.MAX_CLUES = 2
        try:
            reasons = set()
            for attempt in range(20):
                plan, reason = plan_for(
                    digits=chosen,
                    facts=known,
                    entity_ids=entity_ids,
                    index=index,
                    code_length=4,
                    properties=(),
                    rng=DeterministicRng(b"cap" * 10).derive(str(attempt)),
                )
                if plan is None:
                    reasons.add(reason)
            assert CLUES_EXHAUSTED in reasons
        finally:
            module.MAX_CLUES = original


class TestThePlan:
    def test_a_plan_measures_what_the_difficulty_model_reads(self, prepared):
        plan, reason = build(prepared, properties=("primes",))
        assert reason == ""
        assert isinstance(plan, BoardPlan)
        assert plan.space_size == 720
        assert plan.after_properties <= plan.space_size
        assert plan.before_last >= 1
        assert plan.clue_count() == len(plan.guesses)

    def test_a_plan_declares_every_digit_the_board_could_name(self, prepared):
        """The solver rules out every digit, so every digit is load bearing --
        not only the ones in the code."""
        plan, _ = build(prepared)
        assert len(plan.dependency_ids()) == 10

    def test_the_rationale_names_the_shape_of_the_search(self, prepared):
        plan, _ = build(prepared, properties=("primes",))
        text = rationale_for(plan)
        assert "clue" in text and str(plan.space_size) in text


class TestTheRanking:
    def test_the_most_demanding_boards_come_first(self, prepared):
        plans = []
        for attempt in range(16):
            plan, _ = build(prepared, attempt=attempt)
            if plan is not None:
                plans.append(plan)
        ordered = ranked(plans)
        counts = [-len(p.guesses) for p in ordered]
        assert counts == sorted(counts)

    def test_the_order_is_a_pure_function_of_the_plans(self, prepared):
        plans = []
        for attempt in range(10):
            plan, _ = build(prepared, attempt=attempt)
            if plan is not None:
                plans.append(plan)
        first = [p.candidate_id() for p in ranked(plans)]
        second = [p.candidate_id() for p in ranked(list(reversed(plans)))]
        assert first == second


class TestGeneratingADay:
    def test_a_day_offers_boards(self):
        candidates = generate_candidates(context(budget=256))
        assert len(candidates) >= ATTEMPTS_PER_LENGTH
        assert len({c.candidate_id for c in candidates}) == len(candidates)

    def test_every_candidate_declares_the_records_it_used(self):
        for candidate in generate_candidates(context()):
            assert len(candidate.fact_refs) == 10

    def test_the_days_own_length_leads(self):
        day = "2026-10-01"
        candidates = generate_candidates(context(day_key=day))
        assert candidates[0].payload["code_length"] == code_lengths_for(day)[0]

    def test_both_lengths_are_offered(self):
        candidates = generate_candidates(context(budget=256))
        assert {c.payload["code_length"] for c in candidates} == set(CODE_LENGTHS)

    @pytest.mark.parametrize(
        "band", [DifficultyBand.EASY, DifficultyBand.MEDIUM, DifficultyBand.HARD]
    )
    def test_the_board_states_the_facts_its_band_allows(self, band):
        candidates = generate_candidates(context(band=band))
        expected = len(properties_for(band))
        assert len(candidates[0].payload["properties"]) == expected

    def test_the_same_day_generates_the_same_candidates(self):
        first = generate_candidates(context())
        second = generate_candidates(context())
        assert [c.candidate_id for c in first] == [c.candidate_id for c in second]

    def test_two_days_generate_different_candidates(self):
        first = generate_candidates(context(day_key="2026-10-01"))
        second = generate_candidates(context(day_key="2026-10-02"))
        assert [c.candidate_id for c in first] != [c.candidate_id for c in second]

    def test_a_salt_moves_the_whole_stream(self):
        plain = generate_candidates(context(), salt="")
        salted = generate_candidates(context(salt="staging"), salt="staging")
        assert [c.candidate_id for c in plain] != [c.candidate_id for c in salted]

    def test_the_budget_is_respected(self):
        assert len(generate_candidates(context(budget=4))) == 4

    def test_a_day_with_no_digits_says_so(self):
        assert generate_candidates(context([])) == ()
        assert "no digits" in unusable_reason()

    def test_the_rejections_are_cleared_between_runs(self):
        generate_candidates(context([]))
        assert last_rejections()
        generate_candidates(context())
        assert "no digits" not in last_rejections()
