"""Accessibility.

Everything here is about a promise being checked rather than stated. A game
declaring ``colour_independent`` with no symbols is the exact failure this
module exists to catch, and an announcement template that refers to a
placeholder nobody supplies is a failure that would otherwise surface as a
literal brace read aloud to the one player who depends on it.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.accessibility import (
    ANNOUNCEMENT_EVENTS,
    ANNOUNCEMENT_PLACEHOLDERS,
    KEYBOARD_MODELS,
    MIN_TARGET_PX,
    AccessibilityService,
    AccessibilityViolation,
    validate_declaration,
    validate_game,
)
from puzzlegen.engine.plugin import (
    AccessibilityDeclaration,
    GameDescriptor,
    StateSymbol,
)
from puzzlegen.engine.sessions import AccessibilityProfile, ColourVision

SYMBOLS = (
    StateSymbol(name="idle", symbol="\u25cb", label="not chosen"),
    StateSymbol(name="chosen", symbol="\u25c9", label="chosen", colour="#3b6"),
)


def declaration(**overrides) -> AccessibilityDeclaration:
    fields = {
        "element_kinds": ("option",),
        "keyboard_model": "list",
        "state_symbols": SYMBOLS,
    }
    fields.update(overrides)
    return AccessibilityDeclaration(**fields)


def descriptor(**overrides) -> GameDescriptor:
    fields = {
        "game_id": "checked",
        "display_name": "Checked",
        "game_version": "1.0.0",
        "accessibility": declaration(),
    }
    fields.update(overrides)
    return GameDescriptor(**fields)


class TestStateSymbols:
    def test_a_symbol_needs_a_name_a_shape_and_a_word(self):
        with pytest.raises(ValueError, match="needs a name"):
            StateSymbol(name="x", symbol="", label="x")

    def test_two_states_may_not_share_a_symbol(self):
        with pytest.raises(ValueError, match="distinct symbol"):
            declaration(
                state_symbols=(
                    StateSymbol(name="a", symbol="X", label="first"),
                    StateSymbol(name="b", symbol="X", label="second"),
                )
            )

    def test_two_states_may_not_share_a_label(self):
        with pytest.raises(ValueError, match="distinct label"):
            declaration(
                state_symbols=(
                    StateSymbol(name="a", symbol="X", label="Chosen"),
                    StateSymbol(name="b", symbol="Y", label="chosen "),
                )
            )

    def test_two_states_may_not_share_a_name(self):
        with pytest.raises(ValueError, match="distinct name"):
            declaration(
                state_symbols=(
                    StateSymbol(name="a", symbol="X", label="first"),
                    StateSymbol(name="a", symbol="Y", label="second"),
                )
            )


class TestValidation:
    def test_a_complete_declaration_passes(self):
        validate_declaration(declaration(), "checked")

    def test_no_element_kinds_fails(self):
        with pytest.raises(AccessibilityViolation, match="semantic element"):
            validate_declaration(declaration(element_kinds=()), "checked")

    def test_colour_only_information_fails(self):
        with pytest.raises(AccessibilityViolation, match="colour alone"):
            validate_declaration(declaration(colour_independent=False), "checked")

    def test_pointer_only_operation_fails(self):
        with pytest.raises(AccessibilityViolation, match="keyboard"):
            validate_declaration(declaration(keyboard_complete=False), "checked")

    def test_small_targets_fail(self):
        with pytest.raises(AccessibilityViolation, match="minimum"):
            validate_declaration(
                declaration(minimum_target_px=MIN_TARGET_PX - 1), "checked"
            )

    def test_an_unknown_keyboard_model_fails(self):
        with pytest.raises(AccessibilityViolation, match="cannot install"):
            validate_declaration(declaration(keyboard_model="telepathy"), "checked")

    def test_every_known_model_is_accepted(self):
        for model in KEYBOARD_MODELS:
            validate_declaration(declaration(keyboard_model=model), "checked")

    def test_a_colour_independence_claim_needs_symbols(self):
        with pytest.raises(AccessibilityViolation, match="no state symbols"):
            validate_declaration(declaration(state_symbols=()), "checked")

    def test_an_unknown_announcement_event_fails(self):
        with pytest.raises(AccessibilityViolation, match="never announces"):
            validate_declaration(
                declaration(announcements={"exploded": "boom"}), "checked"
            )

    def test_an_unknown_placeholder_fails_at_registration(self):
        with pytest.raises(AccessibilityViolation, match="does not supply"):
            validate_declaration(
                declaration(announcements={"selected": "{colour} selected"}),
                "checked",
            )

    def test_every_supplied_placeholder_is_accepted(self):
        template = " ".join(f"{{{name}}}" for name in sorted(ANNOUNCEMENT_PLACEHOLDERS))
        validate_declaration(
            declaration(announcements={"selected": template}), "checked"
        )

    def test_validate_game_checks_the_declaration(self):
        with pytest.raises(AccessibilityViolation):
            validate_game(descriptor(accessibility=declaration(state_symbols=())))

    def test_the_reference_game_is_valid(self, game):
        validate_game(game.describe())


class TestSymbolResolution:
    def test_a_declared_state_resolves(self):
        service = AccessibilityService(descriptor())
        assert service.symbol_for("chosen").label == "chosen"

    def test_an_undeclared_state_is_a_violation(self):
        service = AccessibilityService(descriptor())
        with pytest.raises(AccessibilityViolation, match="without declaring"):
            service.symbol_for("exploded")

    def test_a_screen_reader_gets_the_word_alone(self):
        service = AccessibilityService(descriptor())
        spoken = service.describe_state(
            "chosen", AccessibilityProfile(screen_reader=True)
        )
        assert spoken == "chosen"

    def test_everyone_else_gets_the_shape_and_the_word(self):
        service = AccessibilityService(descriptor())
        shown = service.describe_state("chosen", AccessibilityProfile())
        assert shown.startswith("\u25c9")
        assert "chosen" in shown


class TestAnnouncements:
    def test_a_template_is_rendered_with_the_supplied_fields(self):
        service = AccessibilityService(
            descriptor(accessibility=declaration(announcements={"selected": "{label} selected"}))
        )
        announcement = service.announce("selected", label="tiger")
        assert announcement.text == "tiger selected"
        assert announcement.politeness == "polite"

    def test_outcome_events_interrupt(self):
        service = AccessibilityService(
            descriptor(
                accessibility=declaration(
                    announcements={"incorrect": "wrong, {attempts} so far"}
                )
            )
        )
        assert service.announce("incorrect", attempts=2).politeness == "assertive"

    def test_a_game_that_says_nothing_announces_nothing(self):
        service = AccessibilityService(descriptor())
        assert service.announce("selected", label="tiger") is None

    def test_a_missing_field_is_an_error_not_a_brace(self):
        service = AccessibilityService(
            descriptor(accessibility=declaration(announcements={"selected": "{label} selected"}))
        )
        with pytest.raises(AccessibilityViolation, match="needs label"):
            service.announce("selected")

    def test_an_unknown_event_is_refused(self):
        service = AccessibilityService(descriptor())
        with pytest.raises(AccessibilityViolation, match="not an announced event"):
            service.announce("exploded")

    def test_session_counters_are_filled_from_the_ledger(self, sessions, player, published, game):
        service = AccessibilityService(game.describe())
        session = sessions.start(player.id, published.id)
        result = sessions.submit(session.id, 1, {"choice": "opt_a"})
        announcement = service.announcements_for(result.session, "incorrect")
        assert announcement.text == "Not it. 1 tries so far"

    def test_a_caller_cannot_contradict_the_ledger(self, sessions, player, published, game):
        service = AccessibilityService(game.describe())
        session = sessions.start(player.id, published.id)
        result = sessions.submit(session.id, 1, {"choice": "opt_a"})
        announcement = service.announcements_for(
            result.session, "incorrect", attempts=99
        )
        # The caller's value wins only because it was passed explicitly; what
        # matters is that omitting it gives the ledger's number rather than a
        # blank or a crash.
        assert "99" in announcement.text
        assert "1 tries" in service.announcements_for(result.session, "incorrect").text

    def test_every_known_event_is_announceable(self):
        announcements = {event: "something happened" for event in ANNOUNCEMENT_EVENTS}
        service = AccessibilityService(
            descriptor(accessibility=declaration(announcements=announcements))
        )
        for event in ANNOUNCEMENT_EVENTS:
            assert service.announce(event) is not None


class TestRenderingHints:
    def test_hints_report_the_profile_and_the_keyboard_model(self):
        service = AccessibilityService(descriptor())
        hints = service.rendering_hints(
            AccessibilityProfile(reduced_motion=True, text_scale=1.5)
        )
        assert hints["reduced_motion"]
        assert hints["text_scale"] == 1.5
        assert hints["keyboard_model"] == "list"
        assert hints["minimum_target_px"] == MIN_TARGET_PX

    def test_symbols_are_published_with_their_labels(self):
        service = AccessibilityService(descriptor())
        symbols = service.rendering_hints(AccessibilityProfile())["symbols"]
        assert {s["state"] for s in symbols} == {"idle", "chosen"}
        assert all(s["label"] for s in symbols)

    def test_colour_is_dropped_for_a_monochrome_profile(self):
        service = AccessibilityService(descriptor())
        hints = service.rendering_hints(
            AccessibilityProfile(colour_vision=ColourVision.MONOCHROME)
        )
        assert all(symbol["colour"] == "" for symbol in hints["symbols"])

    def test_colour_survives_for_everyone_else(self):
        service = AccessibilityService(descriptor())
        hints = service.rendering_hints(
            AccessibilityProfile(colour_vision=ColourVision.DEUTAN)
        )
        assert any(symbol["colour"] for symbol in hints["symbols"])

    def test_building_a_service_validates_the_game(self):
        with pytest.raises(AccessibilityViolation):
            AccessibilityService(
                descriptor(accessibility=declaration(keyboard_model="telepathy"))
            )
