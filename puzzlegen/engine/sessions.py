"""Records for players, play sessions, scores and streaks.

These are engine-owned records like puzzles and manifests, but with a third
lifecycle again: a puzzle is generated once and frozen, a graph record changes
as the world does, and a session accumulates moves while a person plays and
then freezes at a terminal state.

Two rules shape every model here.

First, the tier 1 player id is a primary key and the tier 2 return key is a
bearer credential, and they are never the same string. A primary key ends up
in records, indexes and logs; a credential that did any of that would hand out
account takeover with every debug dump. Only a fingerprint of the return key
is ever stored.

Second, telemetry is derived from the move ledger, never submitted. Every
counter a score or a share can read is a function of moves the engine itself
timestamped, so a modified client can misreport nothing that matters.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core import ids
from ..core.hashing import canonical_json, stable_hash

#: Hard cap on ledger length. A session document holds its own moves, so the
#: cap is what keeps one document bounded; it is far above any real puzzle.
MAX_MOVES = 500

#: Cap on one move's payload, so a client cannot use the ledger as storage.
MAX_PAYLOAD_KEYS = 32
MAX_PAYLOAD_CHARS = 4096


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(dt.timezone.utc)


class SessionState(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    #: The player gave up, or the client reported a deliberate exit.
    ABANDONED = "ABANDONED"
    #: The day window closed while the session was still open.
    EXPIRED = "EXPIRED"


TERMINAL_STATES = frozenset(
    {SessionState.COMPLETED, SessionState.ABANDONED, SessionState.EXPIRED}
)


class SessionKind(StrEnum):
    #: Played on its own day. Scores, streaks and shares come from these only.
    LIVE = "LIVE"
    #: A back-catalogue replay. Recorded in full, excluded from every ranking.
    PRACTICE = "PRACTICE"


class MoveKind(StrEnum):
    SUBMIT = "SUBMIT"
    HINT = "HINT"
    GIVE_UP = "GIVE_UP"


class MoveOutcome(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    #: Recorded but neither right nor wrong: a hint, or a give-up.
    NEUTRAL = "NEUTRAL"


class ColourVision(StrEnum):
    FULL = "FULL"
    PROTAN = "PROTAN"
    DEUTAN = "DEUTAN"
    TRITAN = "TRITAN"
    MONOCHROME = "MONOCHROME"


class IdentityProvider(StrEnum):
    GOOGLE = "GOOGLE"
    APPLE = "APPLE"
    FACEBOOK = "FACEBOOK"


# -- injected policies -------------------------------------------------------


@runtime_checkable
class Clock(Protocol):
    """Time, injected. No module in this package calls ``datetime.now``.

    Every session assertion is about ordering and elapsed time, so a test that
    could not control the clock would either be slow or be a guess.
    """

    def now(self) -> dt.datetime: ...


class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)


@runtime_checkable
class DayWindow(Protocol):
    """Where one puzzle day starts and stops.

    The engine stores UTC instants and an opaque ``day_key`` string and holds
    no timezone opinion of its own; whoever operates the product decides when
    the day turns over and supplies it here.
    """

    def day_key_for(self, instant: dt.datetime) -> str: ...

    def opens_at(self, day_key: str) -> dt.datetime: ...

    def expires_at(self, day_key: str) -> dt.datetime: ...


class UtcDayWindow:
    """Days run midnight to midnight UTC, with an optional grace period.

    Grace exists because a session started at 23:59 and expired sixty seconds
    later is a worse product than one that expires slightly into the next day,
    and because expiry is evaluated lazily on read rather than by a sweep.
    """

    def __init__(self, grace: dt.timedelta = dt.timedelta(0)) -> None:
        if grace < dt.timedelta(0):
            raise ValueError("grace must not be negative")
        self._grace = grace

    def day_key_for(self, instant: dt.datetime) -> str:
        return _utc(instant).date().isoformat()

    def opens_at(self, day_key: str) -> dt.datetime:
        day = dt.date.fromisoformat(day_key)
        return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)

    def expires_at(self, day_key: str) -> dt.datetime:
        return self.opens_at(day_key) + dt.timedelta(days=1) + self._grace


class OffsetDayWindow(UtcDayWindow):
    """Days turn over at a fixed offset from UTC.

    A fixed offset rather than a named zone, deliberately: a named zone makes
    the day boundary move twice a year, which would mean a day key covering
    twenty-three or twenty-five hours and a streak rule that is sometimes
    wrong. If a product later wants local midnight, it supplies its own policy.
    """

    def __init__(
        self, offset: dt.timedelta, grace: dt.timedelta = dt.timedelta(0)
    ) -> None:
        super().__init__(grace)
        if abs(offset) >= dt.timedelta(hours=24):
            raise ValueError("offset must be less than a full day")
        self._offset = offset

    def day_key_for(self, instant: dt.datetime) -> str:
        return (_utc(instant) + self._offset).date().isoformat()

    def opens_at(self, day_key: str) -> dt.datetime:
        return super().opens_at(day_key) - self._offset


# -- accessibility -----------------------------------------------------------


class AccessibilityProfile(_Frozen):
    """How this player needs the interface to behave.

    Held against the player rather than the device, so it follows a returning
    player, and copied onto each session at start, so an old rendering stays
    reproducible after the player later changes a setting.
    """

    reduced_motion: bool = False
    high_contrast: bool = False
    screen_reader: bool = False
    colour_vision: ColourVision = ColourVision.FULL
    text_scale: float = 1.0

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not 1.0 <= self.text_scale <= 3.0:
            raise ValueError("text_scale must be between 1.0 and 3.0")
        return self

    @property
    def needs_symbolic_state(self) -> bool:
        """True when colour alone cannot carry state for this player."""
        return self.colour_vision is not ColourVision.FULL or self.high_contrast


DEFAULT_ACCESSIBILITY = AccessibilityProfile()


# -- identity ----------------------------------------------------------------


class PlayerRecord(_Frozen):
    """A tier 1 anonymous identity.

    Carries no name, address, email or provider subject. The provider subject
    lives on ``IdentityLink`` and nowhere else, so a dump of this collection
    identifies nobody.
    """

    id: str
    created_at: dt.datetime
    last_seen_at: dt.datetime
    locale: str = "en"
    accessibility: AccessibilityProfile = DEFAULT_ACCESSIBILITY
    #: Denormalised so "can this player recover their history" is answerable
    #: without querying links; the links themselves remain the source of truth.
    linked_providers: tuple[IdentityProvider, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.PLAYER)
        object.__setattr__(self, "created_at", _utc(self.created_at))
        object.__setattr__(self, "last_seen_at", _utc(self.last_seen_at))
        if self.last_seen_at < self.created_at:
            raise ValueError("last_seen_at precedes created_at")
        if not self.locale:
            raise ValueError("locale must not be empty")
        if len(set(self.linked_providers)) != len(self.linked_providers):
            raise ValueError("linked_providers must not repeat a provider")
        return self


class ReturnKeyRecord(_Frozen):
    """Proof that a client already holds a tier 1 id.

    Stores ``fingerprint``, an HMAC of the key under a server pepper, never
    the key itself. Several may be live at once, one per device, each
    revocable, because rotating on every use would strand the second device
    and any client whose reply was lost in transit.
    """

    id: str
    player_id: str
    fingerprint: str
    label: str = "default"
    created_at: dt.datetime
    last_seen_at: dt.datetime
    revoked_at: dt.datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.KEY)
        ids.require(self.player_id, ids.PLAYER)
        if len(self.fingerprint) != 64:
            raise ValueError("fingerprint must be a sha256 hex digest")
        if self.id != ids.for_return_key(self.fingerprint):
            raise ValueError("return key id must be minted from its fingerprint")
        object.__setattr__(self, "created_at", _utc(self.created_at))
        object.__setattr__(self, "last_seen_at", _utc(self.last_seen_at))
        if self.revoked_at is not None:
            object.__setattr__(self, "revoked_at", _utc(self.revoked_at))
            if self.revoked_at < self.created_at:
                raise ValueError("revoked_at precedes created_at")
        return self

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None


class IdentityLink(_Frozen):
    """A tier 3 recovery method attached to an existing tier 1 id.

    Holds the provider's subject and nothing else: no token, no refresh token,
    no email, no display name. Verification happened in front of the engine.
    """

    id: str
    player_id: str
    provider: IdentityProvider
    subject: str
    audience: str
    verified_at: dt.datetime
    linked_at: dt.datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.id, ids.LINK)
        ids.require(self.player_id, ids.PLAYER)
        if not self.subject or not self.audience:
            raise ValueError("subject and audience must not be empty")
        if self.id != ids.for_identity_link(self.provider.value, self.subject):
            raise ValueError("link id must be minted from provider and subject")
        object.__setattr__(self, "verified_at", _utc(self.verified_at))
        object.__setattr__(self, "linked_at", _utc(self.linked_at))
        return self


# -- play --------------------------------------------------------------------


class MoveRecord(_Frozen):
    """One entry in an append-only ledger.

    ``server_at`` is the engine's own timestamp and is the only time that
    feeds a score or a share. ``client_at`` is kept because it is useful when
    diagnosing a laggy client, and is never read by anything that ranks.
    """

    sequence: int
    kind: MoveKind
    outcome: MoveOutcome
    server_at: dt.datetime
    payload: Mapping[str, Any] = Field(default_factory=dict)
    client_at: dt.datetime | None = None
    #: Free-text reason a game attached to an incorrect move, for the player.
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.sequence < 1:
            raise ValueError("sequence starts at 1")
        if len(self.payload) > MAX_PAYLOAD_KEYS:
            raise ValueError(f"move payload exceeds {MAX_PAYLOAD_KEYS} keys")
        if len(canonical_json(dict(self.payload))) > MAX_PAYLOAD_CHARS:
            raise ValueError(f"move payload exceeds {MAX_PAYLOAD_CHARS} characters")
        if self.kind is not MoveKind.SUBMIT and self.outcome is not MoveOutcome.NEUTRAL:
            raise ValueError("only a SUBMIT move can be correct or incorrect")
        object.__setattr__(self, "server_at", _utc(self.server_at))
        if self.client_at is not None:
            object.__setattr__(self, "client_at", _utc(self.client_at))
        return self

    def payload_hash(self) -> str:
        """Identity of a move's content, for detecting a changed retry."""
        return stable_hash([self.kind.value, dict(self.payload)])


class SessionRecord(_Frozen):
    """One player's play of one published puzzle.

    The move ledger lives inline rather than in its own collection so that a
    move and the counters derived from it can never be half written. The cap
    on ledger length is what keeps that document bounded.
    """

    id: str
    player_id: str
    manifest_id: str
    puzzle_id: str
    game_id: str
    day_key: str
    kind: SessionKind = SessionKind.LIVE
    state: SessionState = SessionState.IN_PROGRESS
    started_at: dt.datetime
    last_activity_at: dt.datetime
    expires_at: dt.datetime
    ended_at: dt.datetime | None = None
    locale: str = "en"
    #: The profile as it was when play started, not as it is now.
    accessibility: AccessibilityProfile = DEFAULT_ACCESSIBILITY
    moves: tuple[MoveRecord, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.player_id, ids.PLAYER)
        ids.require(self.manifest_id, ids.MANIFEST)
        ids.require(self.puzzle_id, ids.PUZZLE)
        if self.id != ids.for_session(
            self.player_id, self.manifest_id, self.kind.value
        ):
            raise ValueError(
                "session id must be minted from player, manifest and kind, so a "
                "repeated start is idempotent rather than a second history"
            )
        for field_name in ("started_at", "last_activity_at", "expires_at"):
            object.__setattr__(self, field_name, _utc(getattr(self, field_name)))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", _utc(self.ended_at))
        if self.last_activity_at < self.started_at:
            raise ValueError("last_activity_at precedes started_at")
        if (self.state in TERMINAL_STATES) != (self.ended_at is not None):
            raise ValueError("a terminal session has an end time and only then")
        if len(self.moves) > MAX_MOVES:
            raise ValueError(f"a session may hold at most {MAX_MOVES} moves")
        expected = 1
        previous = self.started_at
        for move in self.moves:
            if move.sequence != expected:
                raise ValueError(
                    f"move sequence {move.sequence} breaks the ledger; "
                    f"expected {expected}"
                )
            if move.server_at < previous:
                raise ValueError("move timestamps must not go backwards")
            expected += 1
            previous = move.server_at
        if self.moves and self.last_activity_at < self.moves[-1].server_at:
            raise ValueError(
                "last_activity_at precedes the last move; a session whose "
                "activity clock disagrees with its own ledger would report a "
                "shorter elapsed time than it actually took"
            )
        return self

    # -- derived counters (the only inputs any score may read) ---------------

    @property
    def attempts(self) -> int:
        return sum(1 for m in self.moves if m.kind is MoveKind.SUBMIT)

    @property
    def mistakes(self) -> int:
        return sum(1 for m in self.moves if m.outcome is MoveOutcome.INCORRECT)

    @property
    def hints_used(self) -> int:
        return sum(1 for m in self.moves if m.kind is MoveKind.HINT)

    @property
    def gave_up(self) -> bool:
        return any(m.kind is MoveKind.GIVE_UP for m in self.moves)

    @property
    def elapsed_ms(self) -> int:
        """Server-measured play time: start to last activity.

        Not start to now, because an abandoned tab would otherwise accumulate
        hours of "play", and not a client-reported duration, because that is
        the number a modified client would most want to choose.
        """
        delta = self.last_activity_at - self.started_at
        return int(delta.total_seconds() * 1000)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def next_sequence(self) -> int:
        return len(self.moves) + 1

    @property
    def ranked(self) -> bool:
        """Whether this session may produce a score, streak or share."""
        return self.kind is SessionKind.LIVE

    def move_at(self, sequence: int) -> MoveRecord | None:
        if 1 <= sequence <= len(self.moves):
            return self.moves[sequence - 1]
        return None

    def ledger_hash(self) -> str:
        """Identity of the ledger, so a stored score can be tied to the exact
        moves it was computed from and a later recompute can prove it."""
        return stable_hash(
            [
                [m.sequence, m.kind.value, m.outcome.value, dict(m.payload)]
                for m in self.moves
            ]
        )

    # -- pure transitions ----------------------------------------------------

    def with_move(self, move: MoveRecord) -> "SessionRecord":
        """Append one move. Sequence and terminality are validated here."""
        if self.is_terminal:
            raise ValueError(f"session {self.id} is {self.state} and takes no moves")
        if move.sequence != self.next_sequence:
            raise ValueError(
                f"expected sequence {self.next_sequence}, got {move.sequence}"
            )
        return self.model_copy(
            update={
                "moves": (*self.moves, move),
                "last_activity_at": max(self.last_activity_at, move.server_at),
            }
        )

    def terminated(self, state: SessionState, at: dt.datetime) -> "SessionRecord":
        if state not in TERMINAL_STATES:
            raise ValueError(f"{state} is not a terminal state")
        if self.is_terminal:
            raise ValueError(f"session {self.id} already ended as {self.state}")
        ended = _utc(at)
        return self.model_copy(
            update={
                "state": state,
                "ended_at": ended,
                "last_activity_at": max(self.last_activity_at, ended),
            }
        )


# -- results -----------------------------------------------------------------


class ScoreRecord(_Frozen):
    """A frozen score, written once when a session reaches a terminal state.

    Carries the telemetry it was computed from and the hash of the ledger that
    produced that telemetry, so a recomputation years later either reproduces
    the number or proves that something changed underneath it.
    """

    id: str
    session_id: str
    player_id: str
    game_id: str
    day_key: str
    points: int
    breakdown: Mapping[str, int] = Field(default_factory=dict)
    detail: str = ""
    #: Ordered comparison keys after points, lower being better in each.
    tiebreakers: tuple[int, ...] = ()
    completed: bool
    attempts: int
    mistakes: int
    hints_used: int
    elapsed_ms: int
    efficiency: float
    difficulty_score: float
    ledger_hash: str
    engine_version: str
    scored_at: dt.datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.session_id, ids.SESSION)
        ids.require(self.player_id, ids.PLAYER)
        if self.id != ids.for_score(self.session_id):
            raise ValueError("score id must be minted from its session id")
        if self.points < 0:
            raise ValueError("points must not be negative")
        if any(value < 0 for value in self.tiebreakers):
            raise ValueError("tiebreakers must not be negative")
        for name in ("attempts", "mistakes", "hints_used", "elapsed_ms"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if not 0.0 <= self.efficiency <= 1.0:
            raise ValueError("efficiency must lie on [0, 1]")
        object.__setattr__(self, "scored_at", _utc(self.scored_at))
        return self

    def ranking_key(self) -> tuple[int, ...]:
        """Higher points first, then each tiebreaker ascending.

        Negated points rather than a reverse sort so one tuple orders the whole
        comparison, and no caller has to remember which half sorts which way.
        """
        return (-self.points, *self.tiebreakers)


class StreakRecord(_Frozen):
    """Consecutive completed days for one player in one game.

    No freezes and no grace days: those are product decisions with revenue
    attached, and a streak rule that quietly forgives a gap cannot later be
    tightened without rewriting history.
    """

    id: str
    player_id: str
    game_id: str
    current: int = 0
    longest: int = 0
    last_day_key: str | None = None
    updated_at: dt.datetime

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids.require(self.player_id, ids.PLAYER)
        if self.id != ids.for_streak(self.player_id, self.game_id):
            raise ValueError("streak id must be minted from player and game")
        if self.current < 0 or self.longest < 0:
            raise ValueError("streak lengths must not be negative")
        if self.longest < self.current:
            raise ValueError("longest streak cannot be shorter than the current one")
        if self.current > 0 and self.last_day_key is None:
            raise ValueError("a live streak must name the day it last advanced")
        object.__setattr__(self, "updated_at", _utc(self.updated_at))
        return self
