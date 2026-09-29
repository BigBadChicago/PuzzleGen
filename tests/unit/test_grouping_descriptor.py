"""Game 1's contract and its declared needs.

Nothing here generates a puzzle. These are the two modules the engine reads
before any content exists: what the game promises, and what it asks for.
"""

from __future__ import annotations

import collections

import pytest

from puzzlegen.content.query import ContentRequirement, Operation, resolve_queries
from puzzlegen.core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from puzzlegen.core.versions import PLUGIN_PROTOCOL_VERSION
from puzzlegen.games.grouping import content as content_module
from puzzlegen.games.grouping import descriptor as descriptor_module
from puzzlegen.games.grouping.descriptor import (
    DESCRIPTOR,
    GROUP_SIZES,
    MAX_GROUP_SIZE,
    MIN_GROUP_SIZE,
    SEARCH_BOUND,
    VISIBLE_GROUPS,
    board_size_for,
    build_descriptor,
    group_size_for,
)

DAYS = [f"2026-09-{day:02d}" for day in range(1, 29)]


class TestIdentity:
    def test_the_game_id_is_a_lowercase_slug(self):
        assert DESCRIPTOR.game_id == "grouping"

    def test_it_declares_the_current_protocol(self):
        assert DESCRIPTOR.protocol_version == PLUGIN_PROTOCOL_VERSION

    def test_it_supports_three_ascending_bands(self):
        assert DESCRIPTOR.supported_bands == (
            DifficultyBand.EASY,
            DifficultyBand.MEDIUM,
            DifficultyBand.HARD,
        )

    def test_building_it_twice_gives_the_same_contract(self):
        assert build_descriptor() == build_descriptor()

    def test_the_module_level_descriptor_is_that_contract(self):
        assert DESCRIPTOR == build_descriptor()


class TestTheUniquenessPromise:
    def test_it_claims_uniqueness_only_up_to_tolerance(self):
        """A grouping board has genuinely equivalent solutions.

        Swap two tiles that each belong to both of their groups and the
        partition differs while the puzzle does not. EXACTLY_ONE would be a
        false promise; the tolerance contract is the one that permits
        collapsing equivalents and obliges the verifier to say how many it
        collapsed.
        """
        assert DESCRIPTOR.uniqueness_contract is (
            UniquenessContract.UNIQUE_UP_TO_TOLERANCE
        )

    def test_it_does_not_declare_a_fixed_solution_count(self):
        assert DESCRIPTOR.expected_solution_count is None

    def test_verification_is_declared_incomplete_with_a_bound(self):
        """The honest promise for a search that can legitimately overrun.

        Declaring COMPLETE and returning a partial enumeration is a protocol
        violation. Declaring the weaker promise lets a puzzle report COMPLETE
        whenever the search actually finished, and a day that overruns is
        refused publication by the uniqueness gate rather than published on a
        partial search.
        """
        assert DESCRIPTOR.verification_completeness is (
            VerificationCompleteness.SOUND_INCOMPLETE
        )
        assert DESCRIPTOR.search_bound == SEARCH_BOUND

    def test_the_bound_is_sized_for_the_widest_board(self):
        """36 tiles into four groups of 9, not the average day."""
        assert SEARCH_BOUND >= 10 ** 6


class TestBoardShape:
    def test_group_sizes_run_from_five_to_nine(self):
        assert GROUP_SIZES == (5, 6, 7, 8, 9)
        assert MIN_GROUP_SIZE == 5 and MAX_GROUP_SIZE == 9

    def test_a_board_is_twenty_to_thirty_six_tiles(self):
        sizes = {board_size_for(day) for day in DAYS}
        assert min(sizes) >= MIN_GROUP_SIZE * VISIBLE_GROUPS
        assert max(sizes) <= MAX_GROUP_SIZE * VISIBLE_GROUPS

    def test_the_same_day_always_gives_the_same_size(self):
        assert group_size_for("2026-09-28") == group_size_for("2026-09-28")

    def test_different_days_do_not_all_agree(self):
        """A constant board size would be a broken draw, not a stable one."""
        assert len({group_size_for(day) for day in DAYS}) > 1

    def test_every_size_is_in_range(self):
        assert all(group_size_for(day) in GROUP_SIZES for day in DAYS)

    def test_the_draw_uses_the_whole_range_over_a_month(self):
        drawn = collections.Counter(group_size_for(day) for day in DAYS)
        assert set(drawn) == set(GROUP_SIZES)

    def test_a_salt_produces_a_disjoint_stream(self):
        """Staging must be able to differ from production without changing
        any other input."""
        plain = [group_size_for(day) for day in DAYS]
        salted = [group_size_for(day, salt="staging") for day in DAYS]
        assert plain != salted

    def test_the_board_size_stream_is_named_not_inlined(self):
        """Two callers derive this; a mistyped label would give two answers."""
        assert descriptor_module.BOARD_SIZE_STREAM
        one = descriptor_module.board_rng("2026-09-28")
        two = descriptor_module.board_rng("2026-09-28")
        assert one.next_u64() == two.next_u64()


class TestStateSymbols:
    def test_every_state_carries_a_glyph_and_a_label(self):
        for symbol in DESCRIPTOR.accessibility.state_symbols:
            assert symbol.symbol and symbol.label

    def test_no_two_states_share_a_glyph_or_a_label(self):
        """Enforced by the type, asserted here because it is the point.

        Two states sharing either are indistinguishable to somebody reading
        shapes or hearing text.
        """
        symbols = DESCRIPTOR.accessibility.state_symbols
        assert len({s.symbol for s in symbols}) == len(symbols)
        assert len({s.label.lower() for s in symbols}) == len(symbols)
        assert len({s.name for s in symbols}) == len(symbols)

    def test_the_states_a_board_actually_shows_are_all_declared(self):
        names = {s.name for s in DESCRIPTOR.accessibility.state_symbols}
        assert {"idle", "selected", "solved", "near_miss", "rejected"} <= names

    def test_a_revealed_state_exists_for_the_end_of_a_lost_game(self):
        names = {s.name for s in DESCRIPTOR.accessibility.state_symbols}
        assert "revealed" in names

    def test_colour_is_never_the_only_channel(self):
        assert DESCRIPTOR.accessibility.colour_independent
        for symbol in DESCRIPTOR.accessibility.state_symbols:
            assert symbol.symbol != ""


class TestAccessibility:
    def test_the_declaration_is_complete(self):
        complete, reason = DESCRIPTOR.accessibility.is_complete()
        assert complete, reason

    def test_it_names_its_semantic_elements(self):
        assert DESCRIPTOR.accessibility.element_kinds == ("tile", "group", "board")

    def test_it_is_operable_without_a_pointer(self):
        assert DESCRIPTOR.accessibility.keyboard_complete
        assert DESCRIPTOR.accessibility.keyboard_model == "grid"

    def test_targets_meet_the_minimum_size(self):
        assert DESCRIPTOR.accessibility.minimum_target_px >= 44

    def test_every_event_a_player_can_cause_has_an_announcement(self):
        announced = set(DESCRIPTOR.accessibility.announcements)
        assert {"selected", "correct", "incorrect", "complete", "failed"} <= announced

    def test_it_only_uses_events_the_engine_announces(self):
        """The vocabulary is the engine's, not the game's.

        A template naming an event the engine never fires is wording that will
        never be spoken, and the player who needed it has no way to discover
        that.
        """
        from puzzlegen.engine.accessibility import ANNOUNCEMENT_EVENTS

        assert set(DESCRIPTOR.accessibility.announcements) <= ANNOUNCEMENT_EVENTS

    def test_it_only_uses_placeholders_the_engine_supplies(self):
        import re

        from puzzlegen.engine.accessibility import ANNOUNCEMENT_PLACEHOLDERS

        used: set[str] = set()
        for template in DESCRIPTOR.accessibility.announcements.values():
            used |= set(re.findall(r"\{([a-z_]+)\}", template))
        assert used <= ANNOUNCEMENT_PLACEHOLDERS

    def test_the_engine_gate_accepts_this_descriptor(self):
        """The check that actually runs at registration.

        Everything above tests a property; this tests the gate. A descriptor
        that satisfies each property separately and still fails here would be
        a game that cannot be registered at all.
        """
        from puzzlegen.engine.accessibility import validate_game

        validate_game(DESCRIPTOR)

    def test_the_engine_can_render_every_announcement(self):
        """Every template renders with the fields the engine has.

        A missing field raises rather than emitting a literal brace, so this
        is the test that proves the wording is speakable rather than merely
        declared.
        """
        from puzzlegen.engine.accessibility import AccessibilityService

        service = AccessibilityService(DESCRIPTOR)
        fields = {
            "label": "salmon",
            "state": "selected",
            "position": 3,
            "total": 5,
            "remaining": 12,
            "attempts": 4,
            "mistakes": 1,
            "hints": 2,
            "day": "2026-09-28",
            "game": "Grouping",
        }
        for event in DESCRIPTOR.accessibility.announcements:
            rendered = service.announce(event, **fields)
            assert rendered is not None
            assert "{" not in rendered.text

    def test_an_event_this_game_does_not_announce_renders_nothing(self):
        from puzzlegen.engine.accessibility import AccessibilityService

        service = AccessibilityService(DESCRIPTOR)
        unannounced = {"selected", "deselected", "correct", "incorrect", "hint",
                       "complete", "failed", "expired"} - set(
            DESCRIPTOR.accessibility.announcements
        )
        for event in unannounced:
            assert service.announce(event) is None

    def test_the_engine_can_resolve_every_state_symbol(self):
        from puzzlegen.engine.accessibility import AccessibilityService

        service = AccessibilityService(DESCRIPTOR)
        for symbol in DESCRIPTOR.accessibility.state_symbols:
            assert service.symbol_for(symbol.name) is not None

    def test_no_announcement_template_names_the_answer(self):
        for text in DESCRIPTOR.accessibility.announcements.values():
            assert "{solution}" not in text
            assert "{answer}" not in text


class TestShareTokens:
    def test_every_token_has_three_renderings(self):
        for token in DESCRIPTOR.share_tokens:
            assert token.glyph and token.plain and token.label

    def test_the_plain_rendering_is_one_ascii_character(self):
        for token in DESCRIPTOR.share_tokens:
            assert len(token.plain) == 1 and token.plain.isascii()

    def test_no_two_tokens_look_alike(self):
        tokens = DESCRIPTOR.share_tokens
        assert len({t.glyph for t in tokens}) == len(tokens)
        assert len({t.plain for t in tokens}) == len(tokens)
        assert len({t.name for t in tokens}) == len(tokens)

    def test_the_hidden_group_has_its_own_token(self):
        """Finding the group nobody mentioned is the thing worth showing off.

        Collapsing it into an ordinary solve would throw away the only part of
        a shared result that says which axis the player found.
        """
        names = {t.name for t in DESCRIPTOR.share_tokens}
        assert "hidden" in names
        assert DESCRIPTOR.share_token("hidden") != DESCRIPTOR.share_token("solved")

    def test_the_vocabulary_covers_every_outcome_a_row_can_have(self):
        names = {t.name for t in DESCRIPTOR.share_tokens}
        assert {"solved", "hidden", "miss", "hint"} == names

    def test_there_is_no_token_the_share_could_never_emit(self):
        """A share reads the move ledger with payloads stripped, so it knows
        an attempt was wrong but never how wrong.

        "One away" is a state a tile can be in and not an outcome a share can
        carry, so it is a state symbol and not a share token.
        """
        tokens = {t.name for t in DESCRIPTOR.share_tokens}
        symbols = {s.name for s in DESCRIPTOR.accessibility.state_symbols}
        assert "near_miss" in symbols
        assert "near_miss" not in tokens

    def test_the_axis_switch_is_announced(self):
        """A board that silently changes what it asks loses anybody not
        watching closely, and the player who most needs telling is the one who
        cannot see the tiles."""
        assert "axis_switch" in DESCRIPTOR.accessibility.announcements

    def test_an_undeclared_token_is_not_resolvable(self):
        assert DESCRIPTOR.share_token("jackpot") is None


class TestDifficultyThresholds:
    def test_two_cutoffs_for_three_bands(self):
        assert len(DESCRIPTOR.difficulty_thresholds.cutoffs) == 2

    def test_the_scale_is_cut_in_even_thirds(self):
        """Board size is divided out before a score reaches these.

        A nine-per-group day is mechanically larger than a five-per-group one,
        and banding the raw number would put every wide board in the hardest
        band for a reason unrelated to the puzzle. Once that is removed the
        scale carries only difficulty, so an asymmetric cut would be a second
        correction to something already corrected.
        """
        low, high = DESCRIPTOR.difficulty_thresholds.cutoffs
        assert low == pytest.approx(1 / 3)
        assert high == pytest.approx(2 / 3)

    @pytest.mark.parametrize(
        "score,band",
        [
            (0.0, DifficultyBand.EASY),
            (0.2, DifficultyBand.EASY),
            (1 / 3, DifficultyBand.EASY),
            (0.5, DifficultyBand.MEDIUM),
            (2 / 3, DifficultyBand.MEDIUM),
            (0.8, DifficultyBand.HARD),
            (1.0, DifficultyBand.HARD),
        ],
    )
    def test_scores_band_where_expected(self, score, band):
        assert DESCRIPTOR.band_for(score) is band

    def test_a_score_exactly_on_a_cutoff_lands_in_the_easier_band(self):
        """Ties break somewhere, and breaking easier is the kinder failure."""
        assert DESCRIPTOR.band_for(1 / 3) is DifficultyBand.EASY


class TestContentRequirements:
    def requirements(
        self, day_key: str = "2026-09-28", band: DifficultyBand = DifficultyBand.MEDIUM
    ) -> tuple[ContentRequirement, ...]:
        return tuple(
            content_module.content_requirements(
                difficulty_target=band, locale="en", day_key=day_key
            )
        )

    def named(self, day_key: str = "2026-09-28") -> dict[str, ContentRequirement]:
        return {r.name: r for r in self.requirements(day_key)}

    def test_three_requirements_are_declared(self):
        assert len(self.requirements()) == 3

    def test_their_names_are_unique_and_resolvable(self):
        assert set(resolve_queries(self.requirements())) == {
            content_module.VISIBLE,
            content_module.HIDDEN,
            content_module.HIDDEN_ENRICHED,
        }

    def test_the_visible_groups_come_from_the_lexical_taxonomy(self):
        query = self.named()[content_module.VISIBLE].query
        assert query.taxonomy == content_module.LEXICAL_TAXONOMY
        assert query.operation is Operation.FIND_GROUPS

    def test_the_hidden_group_comes_from_the_overlay(self):
        query = self.named()[content_module.HIDDEN].query
        assert query.taxonomy == content_module.OVERLAY_TAXONOMY

    def test_every_query_asks_for_the_days_group_size(self):
        size = group_size_for("2026-09-28")
        for requirement in self.requirements():
            assert requirement.query.group_size == size

    def test_the_group_size_follows_the_day(self):
        sizes = {
            day: self.named(day)[content_module.VISIBLE].query.group_size
            for day in DAYS
        }
        assert sizes == {day: group_size_for(day) for day in DAYS}

    def test_the_visible_pool_is_larger_than_a_board_needs(self):
        """A pool of exactly four cannot survive one rejection."""
        requirement = self.named()[content_module.VISIBLE]
        assert requirement.query.limit > VISIBLE_GROUPS
        assert requirement.minimum > VISIBLE_GROUPS

    def test_the_visible_requirement_is_not_optional(self):
        assert not self.named()[content_module.VISIBLE].optional

    def test_the_hidden_requirement_is_not_optional(self):
        """No hidden group is no game, only four piles."""
        assert not self.named()[content_module.HIDDEN].optional

    def test_the_enriched_hidden_requirement_is_optional(self):
        """It can only find overlay-to-overlay intersections, and most overlay
        groups have none: six of 144 seed members sit in two categories."""
        requirement = self.named()[content_module.HIDDEN_ENRICHED]
        assert requirement.optional
        assert requirement.query.operation is Operation.FIND_INTERSECTING_GROUPS

    def test_an_unmet_optional_requirement_still_counts_as_met(self):
        from puzzlegen.content.query import ContentResult

        requirement = self.named()[content_module.HIDDEN_ENRICHED]
        assert requirement.is_met(ContentResult(operation=requirement.query.operation))

    def test_an_unmet_required_requirement_does_not(self):
        from puzzlegen.content.query import ContentResult

        requirement = self.named()[content_module.VISIBLE]
        assert not requirement.is_met(
            ContentResult(operation=requirement.query.operation)
        )


class TestQueryConstraints:
    def visible(self, band: DifficultyBand = DifficultyBand.MEDIUM):
        return content_module.visible_group_query(
            group_size=7, difficulty_target=band, locale="en"
        )

    def hidden(self):
        return content_module.hidden_group_query(group_size=7, locale="en")

    def test_the_visible_query_asks_for_the_difficulty_band_not_a_frequency(self):
        """The engine owns the difficulty-to-frequency mapping.

        A game hard-coding its own would be second-guessing a decision that
        must stay consistent across games for a difficulty label to mean
        anything.
        """
        query = self.visible(DifficultyBand.HARD)
        assert query.difficulty_band is DifficultyBand.HARD
        assert query.frequency_band is None

    def test_the_visible_query_bounds_how_far_apart_members_may_sit(self):
        """A group with one obvious outlier is solved by spotting the outlier."""
        query = self.visible()
        assert query.maximum_similarity_spread is not None
        assert query.minimum_similarity is not None

    def test_the_visible_query_bounds_frequency_spread(self):
        """Mixing a very common word with an obscure one gives the obscure one
        away by elimination."""
        assert self.visible().maximum_frequency_spread == 1

    def test_the_hidden_query_has_no_similarity_floor(self):
        """A turtle and a walnut share a shell and nothing else.

        A similarity gate would reject exactly the groups worth hiding.
        """
        query = self.hidden()
        assert query.minimum_similarity is None
        assert query.maximum_similarity_spread is None

    def test_the_hidden_query_has_no_difficulty_band(self):
        """The hidden group is found by noticing a second meaning, not by
        knowing a rare word. Salmon is very common and a hard tile to place."""
        assert self.hidden().difficulty_band is None

    def test_every_query_demands_fresh_content(self):
        for query in (self.visible(), self.hidden()):
            assert query.fresh_only

    def test_every_query_sets_a_confidence_floor_above_activation(self):
        """Activation asks whether content may be used; this asks whether it
        should be shown to a player today."""
        for query in (self.visible(), self.hidden()):
            assert query.minimum_confidence is not None
            assert query.minimum_confidence > 0.75

    def test_every_query_stays_inside_the_engines_limit_cap(self):
        for query in (self.visible(), self.hidden()):
            assert 0 < query.limit <= 500

    def test_the_locale_reaches_the_query(self):
        query = content_module.hidden_group_query(group_size=5, locale="en")
        assert query.lang == "en"


class TestDiagnostics:
    def test_the_summary_reports_the_shape_without_a_solver(self):
        summary = content_module.describe_requirements(
            difficulty_target=DifficultyBand.EASY, locale="en", day_key="2026-09-28"
        )
        assert summary["group_size"] == group_size_for("2026-09-28")
        assert summary["board_size"] == board_size_for("2026-09-28")
        assert summary["hidden_group_size"] == summary["group_size"]

    def test_the_summary_lists_every_requirement(self):
        summary = content_module.describe_requirements(
            difficulty_target=DifficultyBand.EASY, locale="en", day_key="2026-09-28"
        )
        assert len(summary["requirements"]) == 3
        assert {r["name"] for r in summary["requirements"]} == {
            content_module.VISIBLE,
            content_module.HIDDEN,
            content_module.HIDDEN_ENRICHED,
        }

    def test_the_summary_is_json_shaped(self):
        import json

        summary = content_module.describe_requirements(
            difficulty_target=DifficultyBand.MEDIUM, locale="en", day_key="2026-09-28"
        )
        assert json.loads(json.dumps(summary))["game_id"] == "grouping"
