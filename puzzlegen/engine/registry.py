"""Which games exist, and which are live.

Two separate questions. Registration says a game is installed and conformant.
Activation says it occupies one of a limited number of daily slots. Keeping
them apart is what lets a game be swapped out without being uninstalled, and
what lets a deactivated game's published history stay playable.

Activation changes are recorded as dated events rather than as a mutable flag,
because the daily schedule must be reproducible. Asking "which games ran on
2026-09-26" a year later has to give the same answer as it did that day, and a
flag that was flipped since cannot answer it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from ..core.errors import ConfigurationError, ConflictError
from ..core.versions import PLUGIN_PROTOCOL_VERSION, SemVer
from .plugin import GameDescriptor, GamePlugin

#: Default cap on simultaneously active games. A cap exists because each
#: active game consumes a generation run, a review burden and a slot in the
#: player's daily attention, and unbounded growth degrades all three.
DEFAULT_MAX_ACTIVE = 4


@dataclass(frozen=True, slots=True)
class ActivationEvent:
    """One change to a game's live status, on a given day."""

    game_id: str
    day_key: str
    active: bool
    reason: str = ""

    def __post_init__(self) -> None:
        dt.date.fromisoformat(self.day_key)


@dataclass(frozen=True, slots=True)
class RegisteredGame:
    descriptor: GameDescriptor
    plugin: GamePlugin

    @property
    def game_id(self) -> str:
        return self.descriptor.game_id


class GameRegistry:
    """Holds installed games and the history of which were live when."""

    def __init__(self, max_active: int = DEFAULT_MAX_ACTIVE) -> None:
        if max_active < 1:
            raise ValueError("max_active must be at least one")
        self._max_active = max_active
        self._games: dict[str, RegisteredGame] = {}
        self._events: list[ActivationEvent] = []

    # -- registration -----------------------------------------------------

    def register(self, plugin: GamePlugin) -> RegisteredGame:
        descriptor = plugin.describe()
        self._check_protocol(descriptor)

        if descriptor.game_id in self._games:
            existing = self._games[descriptor.game_id].descriptor
            if existing.game_version == descriptor.game_version:
                raise ConflictError(
                    f"game {descriptor.game_id!r} version "
                    f"{descriptor.game_version} is already registered"
                )
        entry = RegisteredGame(descriptor=descriptor, plugin=plugin)
        self._games[descriptor.game_id] = entry
        return entry

    def _check_protocol(self, descriptor: GameDescriptor) -> None:
        try:
            declared = SemVer.parse(descriptor.protocol_version)
            engine = SemVer.parse(PLUGIN_PROTOCOL_VERSION)
        except ValueError as exc:
            raise ConfigurationError(
                f"game {descriptor.game_id!r} declares an unparseable protocol "
                f"version {descriptor.protocol_version!r}"
            ) from exc
        if not declared.is_compatible_with(engine):
            raise ConfigurationError(
                f"game {descriptor.game_id!r} speaks protocol "
                f"{descriptor.protocol_version}, engine speaks "
                f"{PLUGIN_PROTOCOL_VERSION}"
            )

    def unregister(self, game_id: str, *, day_key: str) -> None:
        """Remove a game entirely. Its published history is untouched."""
        if game_id not in self._games:
            raise KeyError(game_id)
        if self.is_active(game_id, day_key):
            self.deactivate(game_id, day_key=day_key, reason="uninstalled")
        del self._games[game_id]

    def get(self, game_id: str) -> RegisteredGame:
        try:
            return self._games[game_id]
        except KeyError as exc:
            raise KeyError(f"no game registered as {game_id!r}") from exc

    def __contains__(self, game_id: str) -> bool:
        return game_id in self._games

    def __iter__(self) -> Iterator[RegisteredGame]:
        return iter(sorted(self._games.values(), key=lambda g: g.game_id))

    def __len__(self) -> int:
        return len(self._games)

    def installed(self) -> tuple[str, ...]:
        return tuple(sorted(self._games))

    # -- activation -------------------------------------------------------

    @property
    def max_active(self) -> int:
        return self._max_active

    def set_max_active(self, value: int) -> None:
        if value < 1:
            raise ValueError("max_active must be at least one")
        if value < len(self.active_on(self._latest_day())):
            raise ConflictError(
                f"{len(self.active_on(self._latest_day()))} games are active; "
                f"deactivate some before lowering the cap to {value}"
            )
        self._max_active = value

    def activate(self, game_id: str, *, day_key: str, reason: str = "") -> None:
        if game_id not in self._games:
            raise KeyError(f"cannot activate unregistered game {game_id!r}")
        if self.is_active(game_id, day_key):
            return
        active = self.active_on(day_key)
        if len(active) >= self._max_active:
            raise ConflictError(
                f"{self._max_active} games are already active on {day_key} "
                f"({', '.join(active)}); deactivate one first"
            )
        self._events.append(
            ActivationEvent(game_id=game_id, day_key=day_key, active=True, reason=reason)
        )

    def deactivate(self, game_id: str, *, day_key: str, reason: str = "") -> None:
        """Remove a game from future scheduling only.

        Published manifests stay published and remain playable. A deactivation
        that erased history would make a day's results irreproducible, which
        is the one thing the manifest exists to prevent.
        """
        if game_id not in self._games:
            raise KeyError(f"cannot deactivate unregistered game {game_id!r}")
        if not self.is_active(game_id, day_key):
            return
        self._events.append(
            ActivationEvent(
                game_id=game_id, day_key=day_key, active=False, reason=reason
            )
        )

    def swap(self, *, out: str, into: str, day_key: str, reason: str = "") -> None:
        """Deactivate one game and activate another in the same slot."""
        self.deactivate(out, day_key=day_key, reason=reason or f"swapped for {into}")
        self.activate(into, day_key=day_key, reason=reason or f"swapped for {out}")

    def is_active(self, game_id: str, day_key: str) -> bool:
        state = False
        for event in self._events_up_to(day_key):
            if event.game_id == game_id:
                state = event.active
        return state

    def active_on(self, day_key: str) -> tuple[str, ...]:
        """Games live on a given day, as of the events recorded by then."""
        state: dict[str, bool] = {}
        for event in self._events_up_to(day_key):
            state[event.game_id] = event.active
        return tuple(
            sorted(game_id for game_id, active in state.items() if active)
        )

    def schedule_for(self, day_key: str) -> tuple[RegisteredGame, ...]:
        """The games to generate for one day, in a deterministic order."""
        return tuple(
            self._games[game_id]
            for game_id in self.active_on(day_key)
            if game_id in self._games
        )

    def history(self, game_id: str | None = None) -> tuple[ActivationEvent, ...]:
        events = tuple(self._events)
        if game_id is None:
            return events
        return tuple(e for e in events if e.game_id == game_id)

    def load_history(self, events: Sequence[ActivationEvent]) -> None:
        """Restore recorded activation history, e.g. on engine start."""
        self._events = sorted(events, key=lambda e: (e.day_key, e.game_id))

    def _events_up_to(self, day_key: str) -> list[ActivationEvent]:
        return sorted(
            (e for e in self._events if e.day_key <= day_key),
            key=lambda e: (e.day_key, e.game_id),
        )

    def _latest_day(self) -> str:
        return max((e.day_key for e in self._events), default="9999-12-31")
