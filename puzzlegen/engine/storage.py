"""Storage for the engine's own records.

Built on the same document store as the graph, and deliberately not on the
same repositories: the engine may store its puzzles, but it may not hold a
repository over governed content, because every gate in the content service
sits between graph records and a puzzle.

Manifest immutability is enforced here rather than by convention. A published
manifest is the reproducibility contract for a day that players have already
played; rewriting it would silently change history.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from ..core.errors import ConflictError
from ..core.versions import PUZZLE_FORMAT_VERSION
from ..graph.repositories import DocumentRepository
from ..graph.store import DocumentStore
from .records import (
    DependencySalt,
    GenerationTrace,
    PuzzleManifest,
    PuzzleRecord,
    PuzzleStatus,
)


class PuzzleRepository(DocumentRepository[PuzzleRecord]):
    collection = "puzzles"
    model = PuzzleRecord
    schema_version = PUZZLE_FORMAT_VERSION
    indexed_fields = ("game_id", "day_key", "status", "day_game")

    def _index_values(self, record: PuzzleRecord) -> dict[str, list[str]]:
        return {
            "game_id": [record.game_id],
            "day_key": [record.day_key],
            "status": [str(record.status)],
            "day_game": [f"{record.day_key}|{record.game_id}"],
        }

    def for_day(self, day_key: str, game_id: str | None = None) -> list[PuzzleRecord]:
        if game_id is None:
            return self.find(day_key=day_key)
        return self.find(day_game=f"{day_key}|{game_id}")

    def invalidated(self, game_id: str | None = None) -> list[PuzzleRecord]:
        """Every withdrawn puzzle, for one game or across all of them.

        The list a streak restoration is built from: a day a retraction
        withdrew is a day the platform may not count against a player, and
        the status is where that is written down. Read from the status index
        rather than from a retraction document, because retractions are
        published artifacts and the status is the stored state -- a second
        day withdrawn by a second retraction has to show up here too.
        """
        found = self.find(status=str(PuzzleStatus.INVALIDATED))
        if game_id is not None:
            found = [p for p in found if p.game_id == game_id]
        return sorted(found, key=lambda p: (p.day_key, p.game_id))

    def published(self, day_key: str) -> list[PuzzleRecord]:
        return [
            p
            for p in self.for_day(day_key)
            if p.status is PuzzleStatus.PUBLISHED
        ]

    def put(self, record: PuzzleRecord) -> None:
        """Write a puzzle, refusing to overwrite an invalidated one.

        Generation is deterministic, so regenerating a retracted day produces
        the same record and would otherwise write it straight back over the
        retraction. That is the one way a board whose salt has already been
        released could become playable again, and it needs no bad input to
        happen -- only a rerun of the pipeline that built the day originally.
        """
        existing = self.get(record.id)
        if existing is not None and existing.status is PuzzleStatus.INVALIDATED:
            raise ConflictError(
                f"puzzle {record.id} was invalidated and cannot be rewritten; "
                "a retracted board stays retracted, and republishing it would "
                "serve a day whose dependency salt is already public"
            )
        super().put(record)

    def set_status(self, puzzle_id: str, status: PuzzleStatus) -> PuzzleRecord:
        """Move a puzzle's status. ``INVALIDATED`` is terminal.

        Terminal here rather than on the record, because a status transition
        is a fact about a stored puzzle and the record cannot see the one it
        is replacing. Retraction is the engine's only irreversible act: it
        publishes a membership proof and releases the day's salt, neither of
        which can be taken back, so a later status that contradicts them
        would make the published evidence a lie.
        """
        record = self.require(puzzle_id)
        if (
            record.status is PuzzleStatus.INVALIDATED
            and status is not PuzzleStatus.INVALIDATED
        ):
            raise ConflictError(
                f"puzzle {puzzle_id} is invalidated, which is terminal; "
                f"it cannot become {status}"
            )
        updated = record.model_copy(update={"status": status})
        # Bypasses the immutability rule on manifests deliberately: a puzzle's
        # status must remain writable so a retracted fact can mark it
        # invalidated. Its payload and solution never change, because
        # model_copy on a frozen model produces a new record and the content
        # hash is computed from payload and solution only.
        #
        # Straight to the base write, past this class's own overwrite guard:
        # the transition check above is the authoritative one here, and the
        # guard would otherwise refuse a repeated retraction of the same day.
        DocumentRepository.put(self, updated)
        return updated


class ManifestRepository(DocumentRepository[PuzzleManifest]):
    collection = "manifests"
    model = PuzzleManifest
    schema_version = PUZZLE_FORMAT_VERSION
    indexed_fields = ("game_id", "day_key", "puzzle_id", "day_game")

    def _index_values(self, record: PuzzleManifest) -> dict[str, list[str]]:
        return {
            "game_id": [record.game_id],
            "day_key": [record.day_key],
            "puzzle_id": [record.puzzle_id],
            "day_game": [f"{record.day_key}|{record.game_id}"],
        }

    def put(self, record: PuzzleManifest) -> None:
        """Publish a manifest. Refuses to overwrite an existing one.

        The immutability guarantee lives here because it is the only place
        that can enforce it. A caller who wants to correct a published day
        must publish a new manifest for a new puzzle and invalidate the old
        one, which leaves both visible.
        """
        existing = self._store.get(self.collection, record.id)
        if existing is not None:
            current = self._load(existing)
            if current.manifest_hash() != record.manifest_hash():
                raise ConflictError(
                    f"manifest {record.id} is published and immutable; "
                    "publish a new puzzle and invalidate this one instead"
                )
            return  # Idempotent republish of identical content.
        super().put(record)

    def for_day(self, day_key: str, game_id: str | None = None) -> list[PuzzleManifest]:
        if game_id is None:
            return self.find(day_key=day_key)
        return self.find(day_game=f"{day_key}|{game_id}")

    def for_puzzle(self, puzzle_id: str) -> PuzzleManifest | None:
        found = self.find(puzzle_id=puzzle_id)
        return found[0] if found else None

    def history(self, game_id: str, limit: int | None = None) -> list[PuzzleManifest]:
        manifests = self.find(game_id=game_id)
        manifests.sort(key=lambda m: m.day_key)
        return manifests[-limit:] if limit else manifests


class DependencySaltRepository(DocumentRepository[DependencySalt]):
    """Salts for dependency commitments, and the release that opens them.

    A salt is written with the manifest and released separately. Release is a
    one-way door enforced here rather than by convention: a caller that could
    un-release a salt could make a published day unverifiable again, and the
    whole point of the commitment is that a player can check a retraction
    against the board they played.
    """

    collection = "dependency_salts"
    model = DependencySalt
    schema_version = PUZZLE_FORMAT_VERSION
    indexed_fields = ("manifest_id", "game_id", "day_key", "released")

    def _index_values(self, record: DependencySalt) -> dict[str, list[str]]:
        return {
            "manifest_id": [record.manifest_id],
            "game_id": [record.game_id],
            "day_key": [record.day_key],
            "released": [str(record.is_released).lower()],
        }

    def put(self, record: DependencySalt) -> None:
        existing = self._store.get(self.collection, record.id)
        if existing is not None:
            current = self._load(existing)
            if current.salt != record.salt:
                raise ConflictError(
                    f"salt {record.id} is already written; a day's commitment "
                    "cannot be rebuilt under a different salt without "
                    "invalidating the root players already hold"
                )
            if current.is_released and not record.is_released:
                raise ConflictError(
                    f"salt {record.id} is released; a published salt cannot "
                    "be withdrawn"
                )
        super().put(record)

    def for_manifest(self, manifest_id: str) -> DependencySalt | None:
        found = self.find(manifest_id=manifest_id)
        return found[0] if found else None

    def for_day(self, day_key: str) -> list[DependencySalt]:
        return self.find(day_key=day_key)

    def release(self, manifest_id: str, at: dt.datetime) -> DependencySalt:
        """Publish one day's salt. Idempotent."""
        from ..core import ids

        record = self.require(ids.for_dependency_salt(manifest_id))
        released = record.released(at)
        if released is not record:
            super().put(released)
        return released

    def release_day(self, day_key: str, at: dt.datetime) -> list[DependencySalt]:
        """Publish every salt for a closed day, in one transaction."""
        with self._store.transaction():
            return [
                self.release(record.manifest_id, at)
                for record in self.for_day(day_key)
            ]


class TraceRepository(DocumentRepository[GenerationTrace]):
    collection = "traces"
    model = GenerationTrace
    schema_version = PUZZLE_FORMAT_VERSION
    indexed_fields = ("game_id", "day_key", "succeeded", "day_game")

    def _index_values(self, record: GenerationTrace) -> dict[str, list[str]]:
        return {
            "game_id": [record.game_id],
            "day_key": [record.day_key],
            "succeeded": [str(record.succeeded).lower()],
            "day_game": [f"{record.day_key}|{record.game_id}"],
        }

    def for_day(self, day_key: str, game_id: str | None = None) -> list[GenerationTrace]:
        if game_id is None:
            return self.find(day_key=day_key)
        return self.find(day_game=f"{day_key}|{game_id}")

    def failures(self, limit: int | None = None) -> list[GenerationTrace]:
        return self.find(succeeded="false", limit=limit)

    def rejection_totals(
        self, game_id: str | None = None
    ) -> dict[str, int]:
        """Aggregate rejection reasons across runs.

        The question a curator actually asks is not "why did this run fail"
        but "what keeps failing", and that needs counts across runs rather
        than one trace at a time.
        """
        traces = self.find(game_id=game_id) if game_id else list(self.iter_all())
        totals: dict[str, int] = {}
        for trace in traces:
            for reason, count in trace.rejection_summary().items():
                totals[reason] = totals.get(reason, 0) + count
        return dict(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])))


class EngineRepositories:
    """Every engine-owned repository bound to one store."""

    def __init__(self, store: DocumentStore) -> None:
        self._store = store
        self.puzzles = PuzzleRepository(store)
        self.manifests = ManifestRepository(store)
        self.traces = TraceRepository(store)
        self.dependency_salts = DependencySaltRepository(store)

    def transaction(self):
        return self._store.transaction()

    def record_counts(self) -> dict[str, int]:
        return {
            repo.collection: repo.count()
            for repo in (
                self.puzzles,
                self.manifests,
                self.traces,
                self.dependency_salts,
            )
        }
