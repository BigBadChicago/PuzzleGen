"""Storage for session-layer records.

Same document store, same two backends, same parametrized test fixture as
everything else. Three guarantees are enforced here rather than in the service
layer, for the reason manifest immutability is enforced in its repository:
a rule that lives in a service is a rule that the next caller can bypass.

- A terminal session is immutable. An identical rewrite is idempotent; a
  changed one raises.
- A score is written once. A recomputation that disagrees is a conflict, not
  a silent correction of a number a player already saw.
- One provider subject belongs to one player forever. Repointing it would move
  a person's history to a different account without either of them noticing.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.errors import ConflictError
from ..graph.repositories import DocumentRepository
from ..graph.store import DocumentStore, Query
from .sessions import (
    IdentityLink,
    PlayerRecord,
    ReturnKeyRecord,
    ScoreRecord,
    SessionKind,
    SessionRecord,
    SessionState,
    StreakRecord,
)


class PlayerRepository(DocumentRepository[PlayerRecord]):
    collection = "players"
    model = PlayerRecord
    indexed_fields = ()


class ReturnKeyRepository(DocumentRepository[ReturnKeyRecord]):
    collection = "return_keys"
    model = ReturnKeyRecord
    indexed_fields = ("player_id", "fingerprint")

    def _index_values(self, record: ReturnKeyRecord) -> dict[str, list[str]]:
        return {
            "player_id": [record.player_id],
            "fingerprint": [record.fingerprint],
        }

    def by_fingerprint(self, fingerprint: str) -> ReturnKeyRecord | None:
        """Lookup is by fingerprint rather than by scanning, so verification
        costs one indexed read whatever the number of players."""
        found = self.find(fingerprint=fingerprint, limit=2)
        if len(found) > 1:
            raise ConflictError(
                f"fingerprint {fingerprint[:12]}... resolves to several keys"
            )
        return found[0] if found else None

    def for_player(self, player_id: str, *, live_only: bool = True) -> list[ReturnKeyRecord]:
        keys = self.find(player_id=player_id)
        if live_only:
            keys = [key for key in keys if key.is_live]
        return keys


class IdentityLinkRepository(DocumentRepository[IdentityLink]):
    collection = "identity_links"
    model = IdentityLink
    indexed_fields = ("player_id", "provider")

    def _index_values(self, record: IdentityLink) -> dict[str, list[str]]:
        return {
            "player_id": [record.player_id],
            "provider": [record.provider.value],
        }

    def put(self, record: IdentityLink) -> None:
        existing = self._store.get(self.collection, record.id)
        if existing is not None:
            current = self._load(existing)
            if current.player_id != record.player_id:
                raise ConflictError(
                    f"{record.provider} subject is already linked to "
                    f"{current.player_id}; relinking would move one person's "
                    "history onto another account"
                )
            return  # Idempotent relink of the same pairing.
        super().put(record)

    def for_player(self, player_id: str) -> list[IdentityLink]:
        return self.find(player_id=player_id)


class SessionRepository(DocumentRepository[SessionRecord]):
    collection = "sessions"
    model = SessionRecord
    indexed_fields = (
        "player_id",
        "manifest_id",
        "game_id",
        "day_key",
        "state",
        "kind",
        "player_day",
        "player_game",
    )

    def _index_values(self, record: SessionRecord) -> dict[str, list[str]]:
        return {
            "player_id": [record.player_id],
            "manifest_id": [record.manifest_id],
            "game_id": [record.game_id],
            "day_key": [record.day_key],
            "state": [record.state.value],
            "kind": [record.kind.value],
            "player_day": [f"{record.player_id}|{record.day_key}"],
            "player_game": [f"{record.player_id}|{record.game_id}"],
        }

    def put(self, record: SessionRecord) -> None:
        """Write a session. A terminal session is immutable from here on.

        Comparing the stored dump rather than only the state means a late
        move arriving after completion is a conflict rather than a quiet
        rewrite of a finished game.
        """
        existing = self._store.get(self.collection, record.id)
        if existing is not None:
            current = self._load(existing)
            if current.is_terminal and self._dump(current) != self._dump(record):
                raise ConflictError(
                    f"session {record.id} ended as {current.state} and is "
                    "immutable; start a practice session instead"
                )
        super().put(record)

    def for_player(
        self,
        player_id: str,
        *,
        game_id: str | None = None,
        limit: int | None = None,
    ) -> list[SessionRecord]:
        if game_id is None:
            sessions = self.find(player_id=player_id)
        else:
            sessions = self.find(player_game=f"{player_id}|{game_id}")
        sessions.sort(key=lambda s: (s.day_key, s.id))
        return sessions[-limit:] if limit else sessions

    def for_manifest(
        self, player_id: str, manifest_id: str
    ) -> list[SessionRecord]:
        return self.query(
            Query(equals={"player_id": player_id, "manifest_id": manifest_id})
        )

    def on_day(self, player_id: str, day_key: str) -> list[SessionRecord]:
        return self.find(player_day=f"{player_id}|{day_key}")

    def open_before(self, cutoff_day_key: str) -> list[SessionRecord]:
        """Sessions still open on a day already past.

        Expiry is applied lazily when a session is read, so this exists for
        an operator sweep and for tests, never as a correctness dependency.
        """
        open_sessions = self.find(state=SessionState.IN_PROGRESS.value)
        return [s for s in open_sessions if s.day_key < cutoff_day_key]

    def completed_days(self, player_id: str, game_id: str) -> list[str]:
        """Day keys this player completed for this game, ascending.

        Practice sessions are filtered out here, which is the one place that
        filter has to be right: every streak answer is built on this list.
        """
        sessions = self.find(player_game=f"{player_id}|{game_id}")
        return sorted(
            s.day_key
            for s in sessions
            if s.kind is SessionKind.LIVE and s.state is SessionState.COMPLETED
        )


class ScoreRepository(DocumentRepository[ScoreRecord]):
    collection = "scores"
    model = ScoreRecord
    indexed_fields = ("player_id", "game_id", "day_key", "session_id", "day_game")

    def _index_values(self, record: ScoreRecord) -> dict[str, list[str]]:
        return {
            "player_id": [record.player_id],
            "game_id": [record.game_id],
            "day_key": [record.day_key],
            "session_id": [record.session_id],
            "day_game": [f"{record.day_key}|{record.game_id}"],
        }

    def put(self, record: ScoreRecord) -> None:
        existing = self._store.get(self.collection, record.id)
        if existing is not None:
            current = self._load(existing)
            if self._dump(current) != self._dump(record):
                raise ConflictError(
                    f"score {record.id} is already final; a disagreeing "
                    "recomputation is a bug, not a correction"
                )
            return
        super().put(record)

    def for_session(self, session_id: str) -> ScoreRecord | None:
        found = self.find(session_id=session_id)
        return found[0] if found else None

    def for_day(self, day_key: str, game_id: str) -> list[ScoreRecord]:
        """One day of one game, already in ranking order."""
        scores = self.find(day_game=f"{day_key}|{game_id}")
        scores.sort(key=lambda s: (s.ranking_key(), s.id))
        return scores

    def for_player(self, player_id: str, game_id: str | None = None) -> list[ScoreRecord]:
        scores = (
            self.find(player_id=player_id)
            if game_id is None
            else self.query(
                Query(equals={"player_id": player_id, "game_id": game_id})
            )
        )
        scores.sort(key=lambda s: (s.day_key, s.game_id))
        return scores

    def rank_of(self, record: ScoreRecord) -> tuple[int, int]:
        """This score's place on its day, and how many played that day.

        Computed on read rather than stored: a rank is a fact about a set that
        keeps changing all day, and a stored rank would be wrong minutes later.
        """
        day = self.for_day(record.day_key, record.game_id)
        for position, score in enumerate(day, start=1):
            if score.id == record.id:
                return position, len(day)
        return 0, len(day)


class StreakRepository(DocumentRepository[StreakRecord]):
    collection = "streaks"
    model = StreakRecord
    indexed_fields = ("player_id", "game_id")

    def _index_values(self, record: StreakRecord) -> dict[str, list[str]]:
        return {
            "player_id": [record.player_id],
            "game_id": [record.game_id],
        }

    def for_player(self, player_id: str) -> list[StreakRecord]:
        streaks = self.find(player_id=player_id)
        streaks.sort(key=lambda s: s.game_id)
        return streaks


class SessionRepositories:
    """Every session-layer repository bound to one store."""

    def __init__(self, store: DocumentStore) -> None:
        self._store = store
        self.players = PlayerRepository(store)
        self.return_keys = ReturnKeyRepository(store)
        self.identity_links = IdentityLinkRepository(store)
        self.sessions = SessionRepository(store)
        self.scores = ScoreRepository(store)
        self.streaks = StreakRepository(store)

    def transaction(self):
        return self._store.transaction()

    def record_counts(self) -> dict[str, int]:
        return {
            repo.collection: repo.count()
            for repo in (
                self.players,
                self.return_keys,
                self.identity_links,
                self.sessions,
                self.scores,
                self.streaks,
            )
        }

    def forget_player(self, player_id: str) -> dict[str, int]:
        """Erase one player's identity and history.

        Present because a product that stores play history needs an erasure
        path that is not "delete the row you remember". Sessions and scores go
        with the player: they are keyed on the tier 1 id and are meaningless
        without it. Published manifests are untouched, because they belong to
        the day rather than to any player.
        """
        removed: dict[str, int] = {}
        with self.transaction():
            for key in self.return_keys.for_player(player_id, live_only=False):
                self.return_keys.delete(key.id)
                removed["return_keys"] = removed.get("return_keys", 0) + 1
            for link in self.identity_links.for_player(player_id):
                self.identity_links.delete(link.id)
                removed["identity_links"] = removed.get("identity_links", 0) + 1
            for session in self.sessions.for_player(player_id):
                self.sessions.delete(session.id)
                removed["sessions"] = removed.get("sessions", 0) + 1
            for score in self.scores.for_player(player_id):
                self.scores.delete(score.id)
                removed["scores"] = removed.get("scores", 0) + 1
            for streak in self.streaks.for_player(player_id):
                self.streaks.delete(streak.id)
                removed["streaks"] = removed.get("streaks", 0) + 1
            if self.players.delete(player_id):
                removed["players"] = 1
        return removed

    def declared_collections(self) -> Sequence[str]:
        return (
            self.players.collection,
            self.return_keys.collection,
            self.identity_links.collection,
            self.sessions.collection,
            self.scores.collection,
            self.streaks.collection,
        )
