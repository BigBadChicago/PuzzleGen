"""The static contract, and the promises the engine reads off it."""

from __future__ import annotations

from math import perm

import pytest

from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.core.versions import PLUGIN_PROTOCOL_VERSION
from puzzlegen.engine.accessibility import validate_game
from puzzlegen.engine.uniqueness import SINGLE_SOLUTION_CONTRACTS
from puzzlegen_digits.descriptor import (
    ACCESSIBILITY,
    CODE_LENGTHS,
    DESCRIPTOR,
    DIFFICULTY_THRESHOLDS,
    GAME_ID,
    GAME_VERSION,
    MAX_CLUES,
    PROPERTIES_BY_BAND,
    PROPERTY_COUNT,
    SEARCH_BOUND,
    SHARE_TOKENS,
    STATE_SYMBOLS,
    SUPPORTED_BANDS,
    build_descriptor,
    code_length_for,
    code_lengths_for,
    length_rng,
    properties_for,
)
from puzzlegen_digits.lock import BASE, PROPERTY_KINDS


class TestTheIdentity:
    def test_the_id_is_a_lowercase_slug(self):
        assert GAME_ID == GAME_ID.lower()
        assert GAME_ID.replace("_", "").isalnum()

    def test_the_descriptor_is_built_once_and_matches_a_fresh_build(self):
        assert DESCRIPTOR == build_descriptor()

    def test_it_declares_the_protocol_version_the_platform_is_on(self):
        assert DESCRIPTOR.protocol_version == PLUGIN_PROTOCOL_VERSION

    def test_the_version_is_the_games_own(self):
        assert DESCRIPTOR.game_version == GAME_VERSION


class TestThePromises:
    def test_the_contract_is_unique_deduction(self):
        """Stronger than one answer: the clues must entail it, so a solver who
        reasons correctly is left holding the code rather than choosing."""
        assert DESCRIPTOR.uniqueness_contract is UniquenessContract.UNIQUE_DEDUCTION

    def test_the_engine_treats_it_as_a_single_solution_contract(self):
        """Which is why the verifier's count is what the gate reads."""
        assert DESCRIPTOR.uniqueness_contract in SINGLE_SOLUTION_CONTRACTS

    def test_the_verification_is_complete(self):
        assert (
            DESCRIPTOR.verification_completeness is VerificationCompleteness.COMPLETE
        )

    def test_the_bound_covers_the_largest_space(self):
        """A bound below the space a supported length holds would refuse its
        own boards; one far above it would not be a bound."""
        largest = perm(BASE, max(CODE_LENGTHS))
        assert largest < SEARCH_BOUND
        assert SEARCH_BOUND < 3 * largest


class TestTheBoardShape:
    def test_the_lengths_are_small_enough_to_enumerate(self):
        for length in CODE_LENGTHS:
            assert perm(BASE, length) <= SEARCH_BOUND

    def test_the_shortest_length_is_not_trivial(self):
        """Below three the space is seventy-two codes and two clues exhaust
        it."""
        assert min(CODE_LENGTHS) >= 3

    def test_the_days_length_comes_from_the_day_alone(self):
        assert code_length_for("2026-10-01") == code_length_for("2026-10-01")
        assert code_length_for("2026-10-01") in CODE_LENGTHS

    def test_a_salt_moves_the_draw(self):
        days = [f"2026-10-{day:02d}" for day in range(1, 29)]
        assert [code_length_for(d) for d in days] != [
            code_length_for(d, "staging") for d in days
        ]

    def test_the_length_order_holds_every_length_once(self):
        order = code_lengths_for("2026-10-01")
        assert sorted(order) == sorted(CODE_LENGTHS)
        assert order[0] == code_length_for("2026-10-01")

    def test_both_lengths_are_drawn_over_a_month(self):
        drawn = {code_length_for(f"2026-10-{day:02d}") for day in range(1, 29)}
        assert drawn == set(CODE_LENGTHS)

    def test_the_length_stream_is_separate_from_generation(self):
        assert length_rng("2026-10-01").label != "candidates"

    def test_the_clue_ceiling_is_above_what_boards_need(self):
        """Measured at a median of five clues at three digits and six at four,
        with a maximum of eight. A ceiling below that would refuse the boards
        at the top of the range rather than the ones nobody wants."""
        assert MAX_CLUES >= 8


class TestTheBandLever:
    def test_every_supported_band_has_a_fact_set(self):
        assert set(PROPERTIES_BY_BAND) == set(SUPPORTED_BANDS)

    def test_the_bands_state_fewer_facts_as_they_get_harder(self):
        counts = [len(PROPERTIES_BY_BAND[band]) for band in SUPPORTED_BANDS]
        assert counts == sorted(counts, reverse=True)

    def test_the_easiest_band_states_everything_and_the_hardest_nothing(self):
        assert set(properties_for(DifficultyBand.EASY)) == set(PROPERTY_KINDS)
        assert properties_for(DifficultyBand.HARD) == ()

    def test_every_stated_kind_is_one_the_deduction_knows(self):
        for band in SUPPORTED_BANDS:
            assert set(properties_for(band)) <= set(PROPERTY_KINDS)

    def test_the_property_count_is_read_rather_than_written_down(self):
        """So a fourth kind added to the deduction makes the easy band state
        it and the difficulty model normalise against it with no edit."""
        assert PROPERTY_COUNT == len(PROPERTY_KINDS)

    def test_an_unsupported_band_is_refused(self):
        with pytest.raises(ValueError, match="not a band"):
            properties_for(DifficultyBand.EXPERT)


class TestTheDifficultyScale:
    def test_the_bands_ascend(self):
        assert list(SUPPORTED_BANDS) == [
            DifficultyBand.EASY,
            DifficultyBand.MEDIUM,
            DifficultyBand.HARD,
        ]

    def test_there_is_one_cutoff_fewer_than_there_are_bands(self):
        assert len(DIFFICULTY_THRESHOLDS.cutoffs) == len(SUPPORTED_BANDS) - 1

    def test_the_cutoffs_ascend_inside_the_unit_interval(self):
        cutoffs = DIFFICULTY_THRESHOLDS.cutoffs
        assert list(cutoffs) == sorted(cutoffs)
        assert all(0.0 < cutoff < 1.0 for cutoff in cutoffs)

    def test_the_cutoffs_sit_in_the_measured_gaps(self):
        """The descriptor records three disjoint ranges: 0.156-0.400,
        0.510-0.667 and 0.688-0.825."""
        low, high = DIFFICULTY_THRESHOLDS.cutoffs
        assert 0.400 < low < 0.510
        assert 0.667 < high < 0.688


class TestAccessibility:
    def test_the_engine_accepts_the_declaration(self):
        assert validate_game(DESCRIPTOR) is None
        ok, why = DESCRIPTOR.accessibility.is_complete()
        assert ok, why

    def test_the_keyboard_model_matches_the_layout(self):
        """Rows of digits with two counts beside them: the solver moves along
        a row and down the column of verdicts."""
        assert ACCESSIBILITY.keyboard_model == "grid"

    def test_every_state_carries_a_glyph_and_a_spoken_label(self):
        for symbol in STATE_SYMBOLS:
            assert symbol.symbol and symbol.label

    def test_no_two_states_look_or_sound_alike(self):
        assert len({s.symbol for s in STATE_SYMBOLS}) == len(STATE_SYMBOLS)
        assert len({s.label.lower() for s in STATE_SYMBOLS}) == len(STATE_SYMBOLS)

    def test_the_verdict_states_are_distinguishable_without_colour(self):
        """Exact, misplaced and absent are the three the board turns on, and a
        board that told them apart by hue would be unplayable for some."""
        names = {symbol.name: symbol for symbol in STATE_SYMBOLS}
        glyphs = {names[name].symbol for name in ("exact", "misplaced", "absent")}
        assert len(glyphs) == 3

    def test_the_board_is_operable_by_keyboard(self):
        assert ACCESSIBILITY.keyboard_complete
        assert ACCESSIBILITY.minimum_target_px >= 44


class TestTheShareVocabulary:
    def test_every_token_has_three_renderings(self):
        for token in SHARE_TOKENS:
            assert token.glyph and token.plain and token.label
            assert len(token.plain) == 1 and token.plain.isascii()

    def test_no_two_tokens_collide(self):
        for field in ("name", "glyph", "plain"):
            values = [getattr(token, field) for token in SHARE_TOKENS]
            assert len(set(values)) == len(values)

    def test_there_is_no_token_carrying_a_verdict(self):
        assert {token.name for token in SHARE_TOKENS} == {"solved", "miss", "hint"}
