"""Accessibility as a data contract.

None of this is styling. The engine supplies focus management, announcements
and keyboard routing, but only a game knows what its elements mean, and a game
that cannot describe its own states cannot be made accessible by any amount of
generic infrastructure downstream. So the description is required, checked
here, and a game that fails the check cannot have a session started against it.

Three things are enforced. A game claiming colour independence has to back the
claim with distinct state symbols, because the claim is otherwise a boolean
nobody verified. Announcement templates are validated at registration against
the placeholders the engine will actually supply, so an unknown placeholder
fails then rather than in the middle of somebody's play. And a player's stored
profile travels with them, so a preference set on a phone holds on a laptop.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..core.errors import ConfigurationError
from .plugin import AccessibilityDeclaration, GameDescriptor, StateSymbol
from .sessions import AccessibilityProfile, ColourVision, SessionRecord

#: Keyboard interaction models the shell knows how to install. A game naming
#: anything else would be routed by nothing, so the name is checked rather
#: than passed through.
KEYBOARD_MODELS = frozenset({"grid", "list", "graph", "canvas", "text"})

#: Placeholders the engine supplies when rendering an announcement. A template
#: may use any subset and nothing outside it.
ANNOUNCEMENT_PLACEHOLDERS = frozenset(
    {
        "label",
        "state",
        "position",
        "total",
        "remaining",
        "attempts",
        "mistakes",
        "hints",
        "day",
        "game",
    }
)

#: Events the engine will announce if a game supplies wording for them.
ANNOUNCEMENT_EVENTS = frozenset(
    {
        "selected",
        "deselected",
        "correct",
        "incorrect",
        "hint",
        "complete",
        "failed",
        "expired",
    }
)

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

#: Minimum interactive target, in CSS pixels. Below this the control is hard
#: to hit with a tremor, a thumb, or a trackpad on a moving train.
MIN_TARGET_PX = 44


class AccessibilityViolation(ConfigurationError):
    """A game's accessibility contract does not hold."""


@dataclass(frozen=True, slots=True)
class RenderedAnnouncement:
    """One thing to say, with the event it came from.

    Politeness is carried because it decides whether a screen reader
    interrupts: a wrong guess should interrupt, a hover should not.
    """

    event: str
    text: str
    politeness: str = "polite"


#: Events that interrupt whatever is being read. Deliberately short: a reader
#: that interrupts constantly is one that gets turned off.
_ASSERTIVE = frozenset({"correct", "incorrect", "complete", "failed", "expired"})


def validate_declaration(declaration: AccessibilityDeclaration, game_id: str) -> None:
    """Check one game's declaration. Raises with the reason, never a boolean.

    Stricter than ``AccessibilityDeclaration.is_complete``, which is the
    game's own self-description from phase 4 and stays as it was. This is the
    session layer's gate, and it is where the claims get evidence attached.
    """
    complete, reason = declaration.is_complete()
    if not complete:
        raise AccessibilityViolation(f"game {game_id} cannot be played: {reason}")
    if declaration.keyboard_model not in KEYBOARD_MODELS:
        raise AccessibilityViolation(
            f"game {game_id} asks for keyboard model "
            f"{declaration.keyboard_model!r}, which the shell cannot install; "
            f"choose one of {', '.join(sorted(KEYBOARD_MODELS))}"
        )
    if declaration.minimum_target_px < MIN_TARGET_PX:
        raise AccessibilityViolation(
            f"game {game_id} declares {declaration.minimum_target_px}px "
            f"targets, below the {MIN_TARGET_PX}px minimum"
        )
    if not declaration.state_symbols:
        raise AccessibilityViolation(
            f"game {game_id} claims colour independence but declares no state "
            "symbols; the claim needs a shape and a word behind it, or it is "
            "a boolean nobody checked"
        )
    for event, template in declaration.announcements.items():
        if event not in ANNOUNCEMENT_EVENTS:
            raise AccessibilityViolation(
                f"game {game_id} supplies wording for {event!r}, which the "
                f"engine never announces; known events are "
                f"{', '.join(sorted(ANNOUNCEMENT_EVENTS))}"
            )
        unknown = set(_PLACEHOLDER.findall(template)) - ANNOUNCEMENT_PLACEHOLDERS
        if unknown:
            raise AccessibilityViolation(
                f"game {game_id}'s {event!r} announcement uses "
                f"{', '.join(sorted(unknown))}, which the engine does not "
                "supply; a template that fails must fail at registration"
            )


def validate_game(descriptor: GameDescriptor) -> None:
    validate_declaration(descriptor.accessibility, descriptor.game_id)


class AccessibilityService:
    """Resolves symbols, renders announcements and reports rendering hints."""

    def __init__(self, descriptor: GameDescriptor) -> None:
        validate_game(descriptor)
        self._descriptor = descriptor
        self._symbols = {s.name: s for s in descriptor.accessibility.state_symbols}

    @property
    def symbols(self) -> tuple[StateSymbol, ...]:
        return self._descriptor.accessibility.state_symbols

    def symbol_for(self, state: str) -> StateSymbol:
        symbol = self._symbols.get(state)
        if symbol is None:
            raise AccessibilityViolation(
                f"game {self._descriptor.game_id} rendered state {state!r} "
                "without declaring a symbol for it"
            )
        return symbol

    def describe_state(self, state: str, profile: AccessibilityProfile) -> str:
        """How one state should be presented to this player.

        A player reading shapes gets the symbol and the label. A player using
        colour gets the same two things: the symbol channel is never removed,
        because a game rendered one way for some players and another way for
        others is a game whose accessible path is the one nobody tests.
        """
        symbol = self.symbol_for(state)
        if profile.screen_reader:
            return symbol.label
        return f"{symbol.symbol} {symbol.label}"

    def announce(
        self, event: str, **fields: Any
    ) -> RenderedAnnouncement | None:
        """Render one announcement, or nothing when the game supplies none.

        Rendered engine side from the game's template so wording stays
        consistent between games, and so a missing field is a clear error
        rather than a literal brace in a screen reader's output.
        """
        if event not in ANNOUNCEMENT_EVENTS:
            raise AccessibilityViolation(f"{event!r} is not an announced event")
        template = self._descriptor.accessibility.announcements.get(event)
        if template is None:
            return None
        needed = set(_PLACEHOLDER.findall(template))
        missing = needed - set(fields)
        if missing:
            raise AccessibilityViolation(
                f"announcement {event!r} needs {', '.join(sorted(missing))}"
            )
        text = template.format(**{k: v for k, v in fields.items() if k in needed})
        return RenderedAnnouncement(
            event=event,
            text=text,
            politeness="assertive" if event in _ASSERTIVE else "polite",
        )

    def announcements_for(
        self, session: SessionRecord, event: str, **extra: Any
    ) -> RenderedAnnouncement | None:
        """Announce an event with the session's own counters filled in.

        Counters come from the ledger rather than from the caller, so an
        announcement cannot tell a player something different from what the
        score will.
        """
        fields: dict[str, Any] = {
            "attempts": session.attempts,
            "mistakes": session.mistakes,
            "hints": session.hints_used,
            "day": session.day_key,
            "game": self._descriptor.display_name,
        }
        fields.update(extra)
        return self.announce(event, **fields)

    def rendering_hints(self, profile: AccessibilityProfile) -> Mapping[str, Any]:
        """What the shell needs to know, as data rather than as CSS.

        The engine renders nothing. It reports the player's stated
        preferences, the keyboard model the game asked for, and the symbol
        table, and the shell decides what any of that looks like.
        """
        return {
            "reduced_motion": profile.reduced_motion,
            "high_contrast": profile.high_contrast,
            "screen_reader": profile.screen_reader,
            "colour_vision": profile.colour_vision.value,
            "text_scale": profile.text_scale,
            "keyboard_model": self._descriptor.accessibility.keyboard_model,
            "minimum_target_px": self._descriptor.accessibility.minimum_target_px,
            "element_kinds": list(self._descriptor.accessibility.element_kinds),
            "symbols": [
                {
                    "state": symbol.name,
                    "symbol": symbol.symbol,
                    "label": symbol.label,
                    # Dropped for a player whose colour vision makes it
                    # meaningless or actively misleading, rather than sent and
                    # hopefully ignored.
                    "colour": (
                        ""
                        if profile.colour_vision is ColourVision.MONOCHROME
                        else symbol.colour
                    ),
                }
                for symbol in self.symbols
            ],
        }
