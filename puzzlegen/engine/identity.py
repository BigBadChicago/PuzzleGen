"""Three-tier session identity.

Tier 1 is a server-issued anonymous id, minted the moment a session starts and
used as the primary key everywhere. Tier 2 is a client-held return key, which
is only proof that the client already holds a tier 1 id. Tier 3 is an optional
provider link that attaches a recovery method to an existing tier 1 id and
never creates a new identity.

Three rules make that safe rather than merely described.

``DeterministicRng`` is never used here. Reproducibility is its entire purpose
and is precisely the property a credential must not have; an invariant test
asserts this module does not import it.

Return keys are stored as an HMAC fingerprint under a server pepper, never in
the clear, so a dump of the store cannot impersonate anybody. The plaintext
key exists exactly once, in the return value of the call that mints it.

No token, code, credential or refresh type appears anywhere in the package.
The layer in front of the engine verifies a provider's response and hands over
a ``VerifiedProviderAssertion``, which is named for what the caller is
promising, so nobody can mistake an unverified token for sufficient input.
"""

from __future__ import annotations

import datetime as dt
import hmac
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, runtime_checkable

from ..core import ids
from ..core.errors import ConflictError, NotFoundError
from .session_storage import SessionRepositories
from .sessions import (
    AccessibilityProfile,
    Clock,
    IdentityLink,
    IdentityProvider,
    PlayerRecord,
    ReturnKeyRecord,
)

#: Bytes of entropy in a minted secret. 32 bytes is 256 bits, which is far
#: past any brute-force horizon and costs nothing to carry.
TOKEN_BYTES = 32

#: Shortest acceptable pepper. A short pepper would make the stored
#: fingerprints attackable offline, which is the one thing hashing them buys.
MIN_PEPPER_BYTES = 32

#: A player may hold this many live return keys at once, one per device.
MAX_LIVE_KEYS = 10


class MergeRequired(ConflictError):
    """Two separate play histories would have to become one.

    Raised rather than resolved. Picking a winner discards somebody's real
    history silently, and merging is a product decision with rules about
    scores and streaks that nobody has made yet.
    """

    def __init__(self, existing_player_id: str, requested_player_id: str) -> None:
        super().__init__(
            f"provider identity already belongs to {existing_player_id}; "
            f"cannot attach it to {requested_player_id} without a merge"
        )
        self.existing_player_id = existing_player_id
        self.requested_player_id = requested_player_id


@runtime_checkable
class SecretSource(Protocol):
    """Where credential material comes from, injected for testability.

    A protocol rather than a direct call to ``secrets`` so a test can make key
    minting deterministic without any module under test ever holding a
    reproducible generator of its own.
    """

    def token(self, nbytes: int = TOKEN_BYTES) -> str: ...


class SystemSecretSource:
    def token(self, nbytes: int = TOKEN_BYTES) -> str:
        if nbytes < 16:
            raise ValueError("refusing to mint a credential below 128 bits")
        return secrets.token_urlsafe(nbytes)


@dataclass(frozen=True, slots=True)
class SessionSecrets:
    """Server-side pepper for return key fingerprints.

    Supplied by the caller rather than read from the environment: no module in
    this package reads process environment, which is what keeps the engine
    embeddable and keeps the invariant test's rule about environment access
    honest for everything above the graph too.
    """

    pepper: bytes

    def __post_init__(self) -> None:
        if len(self.pepper) < MIN_PEPPER_BYTES:
            raise ValueError(
                f"pepper must be at least {MIN_PEPPER_BYTES} bytes"
            )

    @classmethod
    def from_hex(cls, value: str) -> "SessionSecrets":
        return cls(bytes.fromhex(value))

    def fingerprint(self, return_key: str) -> str:
        """HMAC rather than a bare hash, so the stored digest is useless to an
        attacker who has the database but not the pepper."""
        return hmac.new(self.pepper, return_key.encode("utf-8"), sha256).hexdigest()

    def matches(self, return_key: str, fingerprint: str) -> bool:
        return hmac.compare_digest(self.fingerprint(return_key), fingerprint)


@dataclass(frozen=True, slots=True)
class VerifiedProviderAssertion:
    """What the layer in front of the engine promises it already checked.

    ``audience`` is carried because a provider subject only means anything
    relative to the client id it was issued to; recording it lets an operator
    tell later which application a link was made through.
    """

    provider: IdentityProvider
    subject: str
    audience: str
    verified_at: dt.datetime

    def __post_init__(self) -> None:
        if not self.subject:
            raise ValueError("assertion subject must not be empty")
        if not self.audience:
            raise ValueError("assertion audience must not be empty")
        if self.verified_at.tzinfo is None:
            raise ValueError("verified_at must be timezone-aware")

    @property
    def link_id(self) -> str:
        return ids.for_identity_link(self.provider.value, self.subject)


@dataclass(frozen=True, slots=True)
class MintedPlayer:
    """A new player and the only copy of its return key that will ever exist.

    Returned as a pair, rather than stored, so that the caller is forced to
    deal with the key at the moment it is created. Losing it later has no
    recovery path, which is exactly what an anonymous identity means.
    """

    player: PlayerRecord
    return_key: str
    key_record: ReturnKeyRecord


class IdentityService:
    """Mints, verifies and links identities. Holds no opinion about play."""

    def __init__(
        self,
        repositories: SessionRepositories,
        *,
        secrets_config: SessionSecrets,
        clock: Clock,
        secret_source: SecretSource | None = None,
    ) -> None:
        self._repos = repositories
        self._secrets = secrets_config
        self._clock = clock
        self._source = secret_source or SystemSecretSource()

    # -- tier 1 and tier 2 ---------------------------------------------------

    def create_player(
        self,
        *,
        locale: str = "en",
        accessibility: AccessibilityProfile | None = None,
        key_label: str = "default",
    ) -> MintedPlayer:
        now = self._clock.now()
        player_id = ids.for_player(self._source.token())
        player = PlayerRecord(
            id=player_id,
            created_at=now,
            last_seen_at=now,
            locale=locale,
            accessibility=accessibility or AccessibilityProfile(),
        )
        with self._repos.transaction():
            # insert rather than put: a minted id colliding with an existing
            # player would mean the secret source is repeating itself, which
            # must fail loudly rather than overwrite somebody.
            self._repos.players.insert(player)
            return_key, key_record = self._mint_return_key(player_id, key_label, now)
        return MintedPlayer(
            player=player, return_key=return_key, key_record=key_record
        )

    def issue_return_key(self, player_id: str, *, label: str) -> tuple[str, ReturnKeyRecord]:
        """Add a device. The plaintext key is returned once and never stored."""
        player = self._repos.players.require(player_id)
        live = self._repos.return_keys.for_player(player.id)
        if len(live) >= MAX_LIVE_KEYS:
            raise ConflictError(
                f"player {player_id} already holds {len(live)} live return "
                "keys; revoke one before issuing another"
            )
        with self._repos.transaction():
            return self._mint_return_key(player.id, label, self._clock.now())

    def _mint_return_key(
        self, player_id: str, label: str, now: dt.datetime
    ) -> tuple[str, ReturnKeyRecord]:
        return_key = self._source.token()
        fingerprint = self._secrets.fingerprint(return_key)
        record = ReturnKeyRecord(
            id=ids.for_return_key(fingerprint),
            player_id=player_id,
            fingerprint=fingerprint,
            label=label,
            created_at=now,
            last_seen_at=now,
        )
        self._repos.return_keys.insert(record)
        return return_key, record

    def resolve(self, return_key: str) -> PlayerRecord | None:
        """Exchange a client-held key for its player, or nothing.

        Returns ``None`` for an unknown or revoked key rather than raising:
        an unrecognised key is the ordinary case of a new or cleared browser,
        not an error condition, and the caller's next step is to mint a player.
        """
        fingerprint = self._secrets.fingerprint(return_key)
        record = self._repos.return_keys.by_fingerprint(fingerprint)
        if record is None or not record.is_live:
            return None
        if not self._secrets.matches(return_key, record.fingerprint):
            return None  # Unreachable through the index; checked anyway.
        player = self._repos.players.get(record.player_id)
        if player is None:
            return None
        now = self._clock.now()
        with self._repos.transaction():
            self._repos.return_keys.put(record.model_copy(update={"last_seen_at": now}))
            player = player.model_copy(update={"last_seen_at": now})
            self._repos.players.put(player)
        return player

    def revoke_return_key(self, key_id: str) -> ReturnKeyRecord:
        """Revoke one device's key. Never deletes the record.

        A deleted key leaves no evidence that a device was ever trusted, and
        the question after a suspected theft is which devices existed.
        """
        record = self._repos.return_keys.get(key_id)
        if record is None:
            raise NotFoundError("return_keys", key_id)
        if not record.is_live:
            return record
        revoked = record.model_copy(update={"revoked_at": self._clock.now()})
        self._repos.return_keys.put(revoked)
        return revoked

    def revoke_all_keys(self, player_id: str) -> int:
        """Sign every device out, for a player who thinks a key leaked."""
        count = 0
        with self._repos.transaction():
            for key in self._repos.return_keys.for_player(player_id):
                self.revoke_return_key(key.id)
                count += 1
        return count

    # -- tier 3 --------------------------------------------------------------

    def link_identity(
        self, player_id: str, assertion: VerifiedProviderAssertion
    ) -> IdentityLink:
        """Attach a recovery method to an existing player.

        Never creates a player. A subject already bound elsewhere raises
        ``MergeRequired`` rather than moving, because the common cause is one
        person who played anonymously on two devices, and both histories are
        real.
        """
        player = self._repos.players.require(player_id)
        existing = self._repos.identity_links.get(assertion.link_id)
        if existing is not None and existing.player_id != player.id:
            raise MergeRequired(existing.player_id, player.id)
        now = self._clock.now()
        link = IdentityLink(
            id=assertion.link_id,
            player_id=player.id,
            provider=assertion.provider,
            subject=assertion.subject,
            audience=assertion.audience,
            verified_at=assertion.verified_at,
            linked_at=existing.linked_at if existing is not None else now,
        )
        with self._repos.transaction():
            self._repos.identity_links.put(link)
            providers = tuple(
                sorted({*player.linked_providers, assertion.provider})
            )
            self._repos.players.put(
                player.model_copy(
                    update={"linked_providers": providers, "last_seen_at": now}
                )
            )
        return link

    def unlink_identity(self, player_id: str, provider: IdentityProvider) -> int:
        """Remove a recovery method. Play history is untouched."""
        player = self._repos.players.require(player_id)
        removed = 0
        with self._repos.transaction():
            for link in self._repos.identity_links.for_player(player.id):
                if link.provider is provider:
                    self._repos.identity_links.delete(link.id)
                    removed += 1
            if removed:
                providers = tuple(
                    p for p in player.linked_providers if p is not provider
                )
                self._repos.players.put(
                    player.model_copy(update={"linked_providers": providers})
                )
        return removed

    def player_for(self, assertion: VerifiedProviderAssertion) -> PlayerRecord | None:
        link = self._repos.identity_links.get(assertion.link_id)
        if link is None:
            return None
        return self._repos.players.get(link.player_id)

    def recover(
        self, assertion: VerifiedProviderAssertion, *, label: str = "recovered"
    ) -> MintedPlayer | None:
        """Sign in on a new device with a linked provider.

        Issues an additional return key rather than replacing the existing
        ones, so recovering on a phone does not sign the tablet out.
        """
        player = self.player_for(assertion)
        if player is None:
            return None
        now = self._clock.now()
        with self._repos.transaction():
            return_key, record = self._mint_return_key(player.id, label, now)
            player = player.model_copy(update={"last_seen_at": now})
            self._repos.players.put(player)
        return MintedPlayer(player=player, return_key=return_key, key_record=record)

    # -- player settings -----------------------------------------------------

    def update_accessibility(
        self, player_id: str, profile: AccessibilityProfile
    ) -> PlayerRecord:
        """Change the stored profile. Sessions already started keep theirs."""
        player = self._repos.players.require(player_id)
        updated = player.model_copy(
            update={"accessibility": profile, "last_seen_at": self._clock.now()}
        )
        self._repos.players.put(updated)
        return updated

    def update_locale(self, player_id: str, locale: str) -> PlayerRecord:
        if not locale:
            raise ValueError("locale must not be empty")
        player = self._repos.players.require(player_id)
        updated = player.model_copy(
            update={"locale": locale, "last_seen_at": self._clock.now()}
        )
        self._repos.players.put(updated)
        return updated

    def touch(self, player_id: str) -> PlayerRecord:
        player = self._repos.players.require(player_id)
        updated = player.model_copy(update={"last_seen_at": self._clock.now()})
        self._repos.players.put(updated)
        return updated

    def forget(self, player_id: str) -> dict[str, int]:
        """Erase this player entirely, including every session and score."""
        return self._repos.forget_player(player_id)
