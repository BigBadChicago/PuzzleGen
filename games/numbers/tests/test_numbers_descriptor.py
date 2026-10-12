"""The static contract, and the promises the engine reads off it."""

from __future__ import annotations

import pytest

from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.core.versions import PLUGIN_PROTOCOL_VERSION
from puzzlegen.engine.accessibility import validate_game
from puzzlegen_numbers.descriptor import (
    ACCESSIBILITY,
    BOARD_SIZES,
    DESCRIPTOR,
    DIFFICULTY_THRESHOLDS,
    GAME_ID,
    GAME_VERSION,
    SEARCH_BOUND,
    SHARE_TOKENS,
    STATE_SYMBOLS,
    SUPPORTED_BANDS,
    TIER_BY_BAND,
    WINDOW,
    board_rng,
    board_size_for,
    board_sizes_for,
    build_descriptor,
    tier_for,
)
from puzzlegen_numbers.properties import MAX_TIER, MIN_TIER, TIERS, tier_of


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
    def test_the_contract_is_exactly_one_because_the_answer_is_a_tile(self):
        assert DESCRIPTOR.uniqueness_contract is UniquenessContract.EXACTLY_ONE

    def test_the_verification_promise_is_the_weaker_one(self):
        """The vocabulary is the properties the build holds, not arithmetic.
        A solver could defend a property no taxonomy has, and no enumeration
        here would have seen it."""
        assert (
            DESCRIPTOR.verification_completeness
            is VerificationCompleteness.SOUND_INCOMPLETE
        )
        assert DESCRIPTOR.search_bound == SEARCH_BOUND

    def test_the_bound_is_generous_against_the_boards_it_serves(self):
        assert max(BOARD_SIZES) * len(TIERS) * 4 < SEARCH_BOUND


class TestTheBoardShape:
    def test_the_smallest_board_still_has_a_majority(self):
        assert min(BOARD_SIZES) >= 4

    def test_the_window_is_one_magnitude(self):
        """So "two-digit number" is true of every tile and distinguishes
        nothing, instead of being the easiest reading on every board."""
        low, high = WINDOW
        assert len(str(low)) == len(str(high))

    def test_a_board_fits_inside_the_window(self):
        low, high = WINDOW
        assert max(BOARD_SIZES) < high - low + 1

    def test_the_days_size_comes_from_the_day_alone(self):
        assert board_size_for("2026-10-01") == board_size_for("2026-10-01")
        assert board_size_for("2026-10-01") in BOARD_SIZES

    def test_a_salt_moves_the_draw(self):
        days = [f"2026-10-{day:02d}" for day in range(1, 29)]
        assert [board_size_for(d) for d in days] != [
            board_size_for(d, "staging") for d in days
        ]

    def test_the_size_order_holds_every_size_once_with_the_days_own_first(self):
        order = board_sizes_for("2026-10-01")
        assert sorted(order) == sorted(BOARD_SIZES)
        assert order[0] == board_size_for("2026-10-01")

    def test_the_fallback_sizes_ascend(self):
        """A smaller board is likelier to be servable when the day's property
        is a thin one."""
        order = board_sizes_for("2026-10-01")
        assert list(order[1:]) == sorted(order[1:])

    def test_every_size_is_drawn_over_a_month(self):
        drawn = {board_size_for(f"2026-10-{day:02d}") for day in range(1, 29)}
        assert drawn == set(BOARD_SIZES)

    def test_the_board_stream_is_separate_from_generation(self):
        assert board_rng("2026-10-01").label != "candidates"


class TestTheTierTargets:
    def test_every_supported_band_has_a_tier(self):
        assert set(TIER_BY_BAND) == set(SUPPORTED_BANDS)

    def test_the_tiers_ascend_with_the_bands(self):
        tiers = [TIER_BY_BAND[band] for band in SUPPORTED_BANDS]
        assert tiers == sorted(tiers)

    def test_the_bands_span_the_whole_tier_range(self):
        """A band mapped to a tier nothing reaches would be a band whose
        boards are all the same puzzle."""
        assert min(TIER_BY_BAND.values()) == MIN_TIER
        assert max(TIER_BY_BAND.values()) == MAX_TIER

    def test_every_tier_a_band_asks_for_has_properties(self):
        for band in SUPPORTED_BANDS:
            tier = tier_for(band)
            assert [name for name in TIERS if tier_of(name) == tier]

    def test_an_unsupported_band_is_refused(self):
        with pytest.raises(ValueError, match="not a band"):
            tier_for(DifficultyBand.EXPERT)


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
        """The descriptor records three disjoint ranges: 0.250-0.399,
        0.450-0.638 and 0.650-0.828. A cutoff inside a range rather than a
        gap would misband boards the measurement says it should not."""
        low, high = DIFFICULTY_THRESHOLDS.cutoffs
        assert 0.399 < low < 0.450
        assert 0.638 < high < 0.650


class TestAccessibility:
    def test_the_engine_accepts_the_declaration(self):
        assert validate_game(DESCRIPTOR) is None
        ok, why = DESCRIPTOR.accessibility.is_complete()
        assert ok, why

    def test_the_keyboard_model_matches_the_layout(self):
        """One row of numbers is a list. A grid model would have the player
        pressing down arrows that do nothing."""
        assert ACCESSIBILITY.keyboard_model == "list"

    def test_every_state_carries_a_glyph_and_a_spoken_label(self):
        for symbol in STATE_SYMBOLS:
            assert symbol.symbol and symbol.label

    def test_no_two_states_look_or_sound_alike(self):
        assert len({s.symbol for s in STATE_SYMBOLS}) == len(STATE_SYMBOLS)
        assert len({s.label.lower() for s in STATE_SYMBOLS}) == len(STATE_SYMBOLS)

    def test_colour_is_never_the_only_channel(self):
        assert ACCESSIBILITY.colour_independent
        assert all(symbol.symbol for symbol in STATE_SYMBOLS)

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

    def test_there_is_no_token_for_the_property(self):
        """Naming it in a share would give the answer to a board the reader
        has not played."""
        assert {token.name for token in SHARE_TOKENS} == {"solved", "miss", "hint"}
