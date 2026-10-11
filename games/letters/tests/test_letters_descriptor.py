"""Game 2's static contract, and the promises in it.

Nothing here runs a puzzle. What is pinned is the shape the engine reads
before any content is fetched: the board sizes, the blank table, the states
the interface can show, the share vocabulary, and the two declarations that
decide whether a board can ever be published -- the uniqueness contract and
the completeness promise with its bound.

The board-size draw gets more tests than its three lines suggest, because the
content layer, the generator and any later audit all derive it independently
from the day key. A draw that was not a pure function of public inputs would
let them disagree, and the disagreement would look like missing content.
"""

from __future__ import annotations

import pytest

from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.engine.accessibility import (
    ANNOUNCEMENT_EVENTS,
    ANNOUNCEMENT_PLACEHOLDERS,
    KEYBOARD_MODELS,
    validate_declaration,
)
from puzzlegen_letters.descriptor import (
    ACCESSIBILITY,
    BLANKS_BY_BAND,
    BOARD_SIZES,
    DESCRIPTOR,
    DIFFICULTY_THRESHOLDS,
    GAME_ID,
    MINIMUM_BLANKS,
    SEARCH_BOUND,
    SHARE_TOKENS,
    STATE_SYMBOLS,
    SUPPORTED_BANDS,
    blanks_for,
    board_size_for,
    board_sizes_for,
    build_descriptor,
)

DAYS = [f"2026-10-{day:02d}" for day in range(1, 32)]


class TestTheIdentity:
    def test_the_game_id_is_the_name_the_requirements_settled(self):
        assert GAME_ID == "missing_letters"

    def test_the_descriptor_is_built_rather_than_copied(self):
        assert build_descriptor() == DESCRIPTOR

    def test_it_supports_a_band_for_every_blank_count_it_declares(self):
        assert set(BLANKS_BY_BAND) == set(SUPPORTED_BANDS)

    def test_the_thresholds_match_the_bands(self):
        """A positional misreading of the cutoffs would band every board."""
        assert len(DIFFICULTY_THRESHOLDS.cutoffs) == len(SUPPORTED_BANDS) - 1


class TestTheBoardShape:
    def test_every_size_is_one_the_coverage_tool_measures(self):
        assert BOARD_SIZES == (6, 8, 10, 12)

    def test_the_day_decides_the_size_from_the_day_key_alone(self):
        assert board_size_for("2026-10-01") == board_size_for("2026-10-01")
        assert board_size_for("2026-10-01") in BOARD_SIZES

    def test_a_salt_gives_a_disjoint_stream(self):
        """Staging must be able to serve a different board without changing
        any other input."""
        plain = [board_size_for(day) for day in DAYS]
        salted = [board_size_for(day, "staging") for day in DAYS]
        assert plain != salted

    def test_the_fallback_order_is_every_size_once(self):
        for day in DAYS:
            order = board_sizes_for(day)
            assert sorted(order) == sorted(BOARD_SIZES)
            assert order[0] == board_size_for(day)

    def test_the_fallbacks_ascend_so_the_likeliest_size_comes_first(self):
        """Smaller boards are likelier to be servable: thirty categories can
        fill six tiles and sixteen can fill twelve."""
        for day in DAYS:
            rest = board_sizes_for(day)[1:]
            assert list(rest) == sorted(rest)

    def test_the_draw_uses_every_size_over_a_month(self):
        """A draw that favoured one size would hide the content thinness at
        the others rather than measuring it."""
        drawn = {board_size_for(day) for day in DAYS}
        assert drawn == set(BOARD_SIZES)


class TestTheBlankTable:
    def test_harder_means_more_blanks(self):
        counts = [blanks_for(band) for band in SUPPORTED_BANDS]
        assert counts == sorted(counts)
        assert len(set(counts)) == len(counts)

    def test_no_band_is_below_the_floor(self):
        """The requirements' floor, which the ``fish`` example is called out
        for breaking."""
        assert all(blanks_for(band) >= MINIMUM_BLANKS for band in SUPPORTED_BANDS)

    def test_an_unsupported_band_is_refused_rather_than_defaulted(self):
        with pytest.raises(ValueError):
            blanks_for(DifficultyBand.EXPERT)


class TestWhatItPromisesTheEngine:
    def test_the_contract_is_about_readings_rather_than_intent(self):
        """The answer is one word, so a second solution is a second reading of
        the same tiles."""
        assert (
            DESCRIPTOR.uniqueness_contract
            is UniquenessContract.NO_ALTERNATE_INTERPRETATION
        )

    def test_verification_is_declared_incomplete_with_a_bound(self):
        """Section 4.1. The verifier is exhaustive over a pinned vocabulary and
        silent about every word outside it, and the weaker promise is what
        makes that honest."""
        assert (
            DESCRIPTOR.verification_completeness
            is VerificationCompleteness.SOUND_INCOMPLETE
        )
        assert DESCRIPTOR.search_bound == SEARCH_BOUND
        assert SEARCH_BOUND > 0

    def test_it_declares_one_locale_and_means_it(self):
        assert DESCRIPTOR.supported_locales == ("en",)


class TestItCanBePlayedByAnyone:
    def test_the_declaration_passes_the_session_gate(self):
        validate_declaration(ACCESSIBILITY, GAME_ID)

    def test_the_declaration_is_complete(self):
        complete, reason = ACCESSIBILITY.is_complete()
        assert complete, reason

    def test_every_state_carries_a_glyph_and_a_spoken_label(self):
        """A board that distinguishes a marked tile from an unmarked one by
        colour alone is unplayable for a substantial minority of players, and
        the mark is the puzzle."""
        for symbol in STATE_SYMBOLS:
            assert symbol.symbol and symbol.label

    def test_a_marked_and_an_unmarked_tile_are_distinct_without_colour(self):
        marked = next(s for s in STATE_SYMBOLS if s.name == "marked")
        unmarked = next(s for s in STATE_SYMBOLS if s.name == "unmarked")
        assert marked.symbol != unmarked.symbol
        assert marked.label != unmarked.label

    def test_every_announcement_is_an_event_the_engine_raises(self):
        assert set(ACCESSIBILITY.announcements) <= ANNOUNCEMENT_EVENTS

    def test_every_placeholder_is_one_the_engine_supplies(self):
        import re

        used = {
            name
            for template in ACCESSIBILITY.announcements.values()
            for name in re.findall(r"\{([a-z_]+)\}", template)
        }
        assert used <= ANNOUNCEMENT_PLACEHOLDERS

    def test_the_keyboard_model_is_one_the_shell_installs(self):
        assert ACCESSIBILITY.keyboard_model in KEYBOARD_MODELS


class TestTheShareVocabulary:
    def test_every_token_has_three_renderings(self):
        for token in SHARE_TOKENS:
            assert token.glyph and token.plain and token.label
            assert len(token.plain) == 1 and token.plain.isascii()

    def test_there_is_no_token_for_how_close_a_guess_was(self):
        """A share is built from the ledger with payloads stripped, so it can
        see that a guess was wrong but never which word it was. A token the
        share could never emit would be a word no artifact can carry."""
        assert {token.name for token in SHARE_TOKENS} == {"solved", "miss", "hint"}

    def test_the_descriptor_can_resolve_each_token_by_name(self):
        for token in SHARE_TOKENS:
            assert DESCRIPTOR.share_token(token.name) is token
