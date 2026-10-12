"""The static contract, and the promises the engine reads off it.

Nothing here runs a puzzle. What is tested is that the declaration is
self-consistent and that the engine's own validators accept it, because a
descriptor the accessibility gate refuses is a game that cannot publish a
board however good the board is.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.core.versions import PLUGIN_PROTOCOL_VERSION
from puzzlegen.engine.accessibility import validate_game
from puzzlegen_figures.descriptor import (
    ACCESSIBILITY,
    BAND_TARGETS,
    BOARD_SIZES,
    DESCRIPTOR,
    DIFFICULTY_THRESHOLDS,
    GAME_ID,
    GAME_VERSION,
    SEARCH_BOUND,
    SHARE_TOKENS,
    STATE_SYMBOLS,
    SUPPORTED_BANDS,
    TRIO,
    band_target,
    board_rng,
    board_size_for,
    board_sizes_for,
    build_descriptor,
)
from puzzlegen_figures.trio import ATTRIBUTES, VALUES_PER_ATTRIBUTE


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
    def test_the_contract_is_exactly_one(self):
        """The strong promise, which this game can make because its solution
        space is the triples of its own tiles."""
        assert DESCRIPTOR.uniqueness_contract is UniquenessContract.EXACTLY_ONE

    def test_the_verification_is_complete(self):
        assert (
            DESCRIPTOR.verification_completeness is VerificationCompleteness.COMPLETE
        )

    def test_the_bound_covers_the_largest_board(self):
        """A bound below the triples a supported board holds would refuse its
        own boards; one far above it would not be a bound."""
        largest = max(BOARD_SIZES)
        triples = largest * (largest - 1) * (largest - 2) // 6
        assert triples < SEARCH_BOUND
        assert SEARCH_BOUND < 10 * triples

    def test_a_trio_is_three(self):
        assert TRIO == VALUES_PER_ATTRIBUTE == 3


class TestTheBoardShape:
    def test_every_size_lays_out_three_wide(self):
        assert all(size % 3 == 0 for size in BOARD_SIZES)

    def test_a_board_holds_fewer_figures_than_the_space(self):
        """The construction needs room to place fillers that complete
        nothing, so a board as large as the space would be unfillable."""
        space = VALUES_PER_ATTRIBUTE ** len(ATTRIBUTES)
        assert max(BOARD_SIZES) < space

    def test_the_days_size_comes_from_the_day_alone(self):
        assert board_size_for("2026-10-01") == board_size_for("2026-10-01")
        assert board_size_for("2026-10-01") in BOARD_SIZES

    def test_a_salt_moves_the_draw(self):
        days = [f"2026-10-{day:02d}" for day in range(1, 29)]
        plain = [board_size_for(day) for day in days]
        salted = [board_size_for(day, "staging") for day in days]
        assert plain != salted

    def test_the_size_order_holds_every_size_once_with_the_days_own_first(self):
        order = board_sizes_for("2026-10-01")
        assert sorted(order) == sorted(BOARD_SIZES)
        assert order[0] == board_size_for("2026-10-01")

    def test_the_fallback_sizes_ascend(self):
        order = board_sizes_for("2026-10-01")
        assert list(order[1:]) == sorted(order[1:])

    def test_both_sizes_are_drawn_over_a_month(self):
        drawn = {board_size_for(f"2026-10-{day:02d}") for day in range(1, 29)}
        assert drawn == set(BOARD_SIZES)

    def test_the_board_stream_is_separate_from_generation(self):
        """The size has to be known before any content is requested, which
        happens before the engine builds a generation context."""
        assert board_rng("2026-10-01").label != "candidates"


class TestTheBandTargets:
    def test_every_supported_band_has_a_target(self):
        assert set(BAND_TARGETS) == set(SUPPORTED_BANDS)

    def test_the_targets_ascend_on_both_levers(self):
        varying = [BAND_TARGETS[band][0] for band in SUPPORTED_BANDS]
        decoys = [BAND_TARGETS[band][1] for band in SUPPORTED_BANDS]
        assert varying == sorted(varying)
        assert decoys == sorted(decoys)

    def test_the_varying_targets_are_reachable(self):
        """One to four, never zero: a trio all-same on every attribute would
        be three copies of one figure."""
        for band in SUPPORTED_BANDS:
            assert 1 <= BAND_TARGETS[band][0] <= len(ATTRIBUTES)

    def test_the_decoy_targets_sit_inside_the_measured_range(self):
        """0.024 to 0.202 at nine tiles and 0.045 to 0.150 at twelve, as the
        descriptor records. A target outside that would rank by an
        unreachable number and effectively shuffle."""
        for band in SUPPORTED_BANDS:
            assert 0.045 <= BAND_TARGETS[band][1] <= 0.150

    def test_an_unsupported_band_is_refused(self):
        with pytest.raises(ValueError, match="not a band"):
            band_target(DifficultyBand.EXPERT)


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

    @pytest.mark.parametrize(
        "score,band",
        [
            (0.0, DifficultyBand.EASY),
            (0.5, DifficultyBand.EASY),
            (0.6, DifficultyBand.MEDIUM),
            (0.9, DifficultyBand.HARD),
            (1.0, DifficultyBand.HARD),
        ],
    )
    def test_a_score_lands_in_the_band_the_cutoffs_say(self, score, band):
        assert DESCRIPTOR.band_for(score) is band


class TestAccessibility:
    def test_the_engine_accepts_the_declaration(self):
        """A descriptor the gate refuses is a game that cannot publish a
        board however good the board is."""
        # Raises on a declaration it refuses, so returning is the pass.
        assert validate_game(DESCRIPTOR) is None
        ok, why = DESCRIPTOR.accessibility.is_complete()
        assert ok, why

    def test_every_state_carries_a_glyph_and_a_spoken_label(self):
        for symbol in STATE_SYMBOLS:
            assert symbol.symbol and symbol.label

    def test_no_two_states_look_or_sound_alike(self):
        assert len({s.symbol for s in STATE_SYMBOLS}) == len(STATE_SYMBOLS)
        assert len({s.label.lower() for s in STATE_SYMBOLS}) == len(STATE_SYMBOLS)

    def test_colour_is_never_the_only_channel(self):
        """The promise this game is most exposed to, since two of its figures
        can differ only in colour."""
        assert ACCESSIBILITY.colour_independent
        assert all(symbol.symbol for symbol in STATE_SYMBOLS)

    def test_the_board_is_operable_by_keyboard(self):
        assert ACCESSIBILITY.keyboard_complete
        assert ACCESSIBILITY.minimum_target_px >= 44

    def test_the_element_kinds_name_what_the_board_shows(self):
        assert "tile" in ACCESSIBILITY.element_kinds


class TestTheShareVocabulary:
    def test_every_token_has_three_renderings(self):
        for token in SHARE_TOKENS:
            assert token.glyph and token.plain and token.label
            assert len(token.plain) == 1 and token.plain.isascii()

    def test_no_two_tokens_collide(self):
        for field in ("name", "glyph", "plain"):
            values = [getattr(token, field) for token in SHARE_TOKENS]
            assert len(set(values)) == len(values)

    def test_the_descriptor_can_look_a_token_up_by_name(self):
        assert DESCRIPTOR.share_token("solved") is not None
        assert DESCRIPTOR.share_token("undeclared") is None
