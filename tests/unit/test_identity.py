"""Identity: what a player id is, what a return key proves, and what linking
a provider may and may not move.

The security claims in this module are the ones worth testing hardest, because
each of them fails silently. A return key stored in the clear works perfectly
until the database leaks. A relink that repoints a subject works perfectly
until somebody's history lands in a stranger's account.
"""

from __future__ import annotations

import pytest

from puzzlegen.core import ids
from puzzlegen.core.errors import ConflictError, NotFoundError
from puzzlegen.engine.identity import (
    IdentityService,
    MergeRequired,
    SessionSecrets,
    SystemSecretSource,
    VerifiedProviderAssertion,
)
from puzzlegen.engine.sessions import (
    AccessibilityProfile,
    ColourVision,
    IdentityProvider,
)


class LeakySource:
    """A secret source whose tokens are recognisable, so a test can prove the
    minted player id does not carry the token that produced it."""

    def __init__(self, prefix: str = "leakme") -> None:
        self._prefix = prefix
        self._next = 0

    def token(self, nbytes: int = 32) -> str:
        self._next += 1
        return f"{self._prefix}-{self._next:04d}"


def assertion(
    clock, *, provider=IdentityProvider.GOOGLE, subject="google-sub-1"
) -> VerifiedProviderAssertion:
    return VerifiedProviderAssertion(
        provider=provider,
        subject=subject,
        audience="puzzlegen-web",
        verified_at=clock.now(),
    )


class TestSecrets:
    def test_pepper_must_be_long_enough(self):
        with pytest.raises(ValueError, match="at least"):
            SessionSecrets(b"short")

    def test_fingerprint_is_stable_and_key_specific(self, secrets_config):
        assert secrets_config.fingerprint("abc") == secrets_config.fingerprint("abc")
        assert secrets_config.fingerprint("abc") != secrets_config.fingerprint("abd")

    def test_fingerprint_depends_on_the_pepper(self):
        one = SessionSecrets(b"a" * 32)
        two = SessionSecrets(b"b" * 32)
        assert one.fingerprint("key") != two.fingerprint("key")

    def test_matches_accepts_only_the_original_key(self, secrets_config):
        digest = secrets_config.fingerprint("key")
        assert secrets_config.matches("key", digest)
        assert not secrets_config.matches("key ", digest)

    def test_system_source_refuses_weak_tokens(self):
        with pytest.raises(ValueError, match="128 bits"):
            SystemSecretSource().token(8)

    def test_system_source_does_not_repeat(self):
        source = SystemSecretSource()
        assert len({source.token() for _ in range(50)}) == 50


class TestPlayerMinting:
    def test_create_player_returns_a_key_exactly_once(self, identity):
        minted = identity.create_player()
        assert ids.is_id(minted.player.id, ids.PLAYER)
        assert minted.return_key
        assert minted.key_record.player_id == minted.player.id

    def test_the_key_is_never_stored_in_the_clear(
        self, identity, session_repos, secrets_config
    ):
        minted = identity.create_player()
        stored = session_repos.return_keys.require(minted.key_record.id)
        assert minted.return_key not in stored.model_dump_json()
        assert stored.fingerprint == secrets_config.fingerprint(minted.return_key)

    def test_the_player_id_does_not_contain_the_token(self, session_repos, clock, secrets_config):
        service = IdentityService(
            session_repos,
            secrets_config=secrets_config,
            clock=clock,
            secret_source=LeakySource(),
        )
        minted = service.create_player()
        assert "leakme" not in minted.player.id

    def test_player_record_carries_no_personal_data(self, identity):
        fields = set(identity.create_player().player.model_dump().keys())
        assert fields == {
            "id",
            "created_at",
            "last_seen_at",
            "locale",
            "accessibility",
            "linked_providers",
        }

    def test_accessibility_profile_is_kept(self, identity):
        profile = AccessibilityProfile(
            screen_reader=True, colour_vision=ColourVision.DEUTAN
        )
        minted = identity.create_player(accessibility=profile)
        assert minted.player.accessibility.screen_reader


class TestReturnKeys:
    def test_resolve_returns_the_owning_player(self, identity):
        minted = identity.create_player()
        assert identity.resolve(minted.return_key).id == minted.player.id

    def test_an_unknown_key_is_not_an_error(self, identity):
        identity.create_player()
        assert identity.resolve("not-a-key") is None

    def test_resolve_updates_last_seen(self, identity, clock):
        minted = identity.create_player()
        clock.advance(days=3)
        resolved = identity.resolve(minted.return_key)
        assert resolved.last_seen_at == clock.now()

    def test_a_second_device_gets_its_own_key(self, identity):
        minted = identity.create_player()
        second, record = identity.issue_return_key(minted.player.id, label="tablet")
        assert second != minted.return_key
        assert identity.resolve(second).id == minted.player.id
        assert identity.resolve(minted.return_key).id == minted.player.id
        assert record.label == "tablet"

    def test_keys_are_capped(self, identity):
        minted = identity.create_player()
        for index in range(9):
            identity.issue_return_key(minted.player.id, label=f"device-{index}")
        with pytest.raises(ConflictError, match="live return keys"):
            identity.issue_return_key(minted.player.id, label="one too many")

    def test_revocation_stops_a_key_without_deleting_it(
        self, identity, session_repos
    ):
        minted = identity.create_player()
        identity.revoke_return_key(minted.key_record.id)
        assert identity.resolve(minted.return_key) is None
        assert session_repos.return_keys.get(minted.key_record.id) is not None

    def test_revoking_twice_is_idempotent(self, identity):
        minted = identity.create_player()
        first = identity.revoke_return_key(minted.key_record.id)
        again = identity.revoke_return_key(minted.key_record.id)
        assert first.revoked_at == again.revoked_at

    def test_revoke_all_signs_every_device_out(self, identity):
        minted = identity.create_player()
        second, _ = identity.issue_return_key(minted.player.id, label="tablet")
        assert identity.revoke_all_keys(minted.player.id) == 2
        assert identity.resolve(minted.return_key) is None
        assert identity.resolve(second) is None

    def test_revoking_an_unknown_key_raises(self, identity):
        with pytest.raises(NotFoundError):
            identity.revoke_return_key(ids.for_return_key("f" * 64))

    def test_a_revoked_key_frees_a_slot(self, identity):
        minted = identity.create_player()
        for index in range(9):
            identity.issue_return_key(minted.player.id, label=f"d{index}")
        identity.revoke_return_key(minted.key_record.id)
        replacement, _ = identity.issue_return_key(
            minted.player.id, label="replacement"
        )
        assert identity.resolve(replacement) is not None


class TestProviderLinks:
    def test_linking_attaches_to_an_existing_player(self, identity, clock):
        minted = identity.create_player()
        link = identity.link_identity(minted.player.id, assertion(clock))
        assert link.player_id == minted.player.id
        assert identity.player_for(assertion(clock)).id == minted.player.id

    def test_linking_records_the_provider_on_the_player(self, identity, clock):
        minted = identity.create_player()
        identity.link_identity(minted.player.id, assertion(clock))
        player = identity._repos.players.require(minted.player.id)
        assert player.linked_providers == (IdentityProvider.GOOGLE,)

    def test_relinking_the_same_pair_is_idempotent(self, identity, clock):
        minted = identity.create_player()
        first = identity.link_identity(minted.player.id, assertion(clock))
        clock.advance(days=1)
        again = identity.link_identity(minted.player.id, assertion(clock))
        assert again.linked_at == first.linked_at

    def test_a_subject_cannot_move_to_another_player(self, identity, clock):
        one = identity.create_player().player
        two = identity.create_player().player
        identity.link_identity(one.id, assertion(clock))
        with pytest.raises(MergeRequired) as caught:
            identity.link_identity(two.id, assertion(clock))
        assert caught.value.existing_player_id == one.id
        assert caught.value.requested_player_id == two.id

    def test_two_providers_can_point_at_one_player(self, identity, clock):
        minted = identity.create_player()
        identity.link_identity(minted.player.id, assertion(clock))
        identity.link_identity(
            minted.player.id,
            assertion(clock, provider=IdentityProvider.APPLE, subject="apple-1"),
        )
        player = identity._repos.players.require(minted.player.id)
        assert set(player.linked_providers) == {
            IdentityProvider.GOOGLE,
            IdentityProvider.APPLE,
        }

    def test_the_same_subject_string_on_two_providers_is_two_links(
        self, identity, clock
    ):
        one = identity.create_player().player
        two = identity.create_player().player
        identity.link_identity(one.id, assertion(clock, subject="shared"))
        identity.link_identity(
            two.id,
            assertion(
                clock, provider=IdentityProvider.APPLE, subject="shared"
            ),
        )
        assert identity.player_for(assertion(clock, subject="shared")).id == one.id

    def test_unlinking_leaves_the_player_and_its_keys(self, identity, clock):
        minted = identity.create_player()
        identity.link_identity(minted.player.id, assertion(clock))
        assert identity.unlink_identity(minted.player.id, IdentityProvider.GOOGLE) == 1
        assert identity.player_for(assertion(clock)) is None
        assert identity.resolve(minted.return_key).id == minted.player.id

    def test_linking_an_unknown_player_raises(self, identity, clock):
        with pytest.raises(NotFoundError):
            identity.link_identity(ids.for_player("ghost"), assertion(clock))

    def test_an_assertion_needs_a_subject_and_an_audience(self, clock):
        with pytest.raises(ValueError, match="subject"):
            VerifiedProviderAssertion(
                provider=IdentityProvider.GOOGLE,
                subject="",
                audience="web",
                verified_at=clock.now(),
            )
        with pytest.raises(ValueError, match="audience"):
            VerifiedProviderAssertion(
                provider=IdentityProvider.GOOGLE,
                subject="s",
                audience="",
                verified_at=clock.now(),
            )


class TestRecovery:
    def test_recovery_issues_a_new_key_and_keeps_the_old(self, identity, clock):
        minted = identity.create_player()
        identity.link_identity(minted.player.id, assertion(clock))
        recovered = identity.recover(assertion(clock))
        assert recovered.player.id == minted.player.id
        assert recovered.return_key != minted.return_key
        assert identity.resolve(minted.return_key) is not None
        assert identity.resolve(recovered.return_key) is not None

    def test_recovery_without_a_link_yields_nothing(self, identity, clock):
        identity.create_player()
        assert identity.recover(assertion(clock)) is None


class TestPlayerSettings:
    def test_accessibility_can_be_changed(self, identity):
        minted = identity.create_player()
        updated = identity.update_accessibility(
            minted.player.id, AccessibilityProfile(reduced_motion=True)
        )
        assert updated.accessibility.reduced_motion

    def test_locale_must_not_be_empty(self, identity):
        minted = identity.create_player()
        with pytest.raises(ValueError):
            identity.update_locale(minted.player.id, "")

    def test_touch_moves_last_seen_forward(self, identity, clock):
        minted = identity.create_player()
        clock.advance(hours=5)
        assert identity.touch(minted.player.id).last_seen_at == clock.now()


class TestErasure:
    def test_forget_removes_every_trace_of_one_player(
        self, identity, session_repos, clock
    ):
        keeper = identity.create_player().player
        doomed = identity.create_player()
        identity.link_identity(doomed.player.id, assertion(clock))
        removed = identity.forget(doomed.player.id)
        assert removed["players"] == 1
        assert removed["identity_links"] == 1
        assert session_repos.players.get(doomed.player.id) is None
        assert identity.resolve(doomed.return_key) is None
        assert session_repos.players.get(keeper.id) is not None
