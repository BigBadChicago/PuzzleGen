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
from ..graph.repositories import DocumentRepository
from ..graph.store import DocumentStore
from .records import (
    GenerationTrace,
    PuzzleManifest,
    PuzzleRecord,
    PuzzleStatus,
)


class PuzzleRepository(DocumentRepository[PuzzleRecord]):
    collection = "puzzles"
    model = PuzzleRecord
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

    def published(self, day_key: str) -> list[PuzzleRecord]:
        return [
            p
            for p in self.for_day(day_key)
            if p.status is PuzzleStatus.PUBLISHED
        ]

    def set_status(self, puzzle_id: str, status: PuzzleStatus) -> PuzzleRecord:
        record = self.require(puzzle_id)
        updated = record.model_copy(update={"status": status})
        # Bypasses the immutability rule on manifests deliberately: a puzzle's
        # status must remain writable so a retracted fact can mark it
        # invalidated. Its payload and solution never change, because
        # model_copy on a frozen model produces a new record and the content
        # hash is computed from payload and solution only.
        super().put(updated)
        return updated


class ManifestRepository(DocumentRepository[PuzzleManifest]):
    collection = "manifests"
    model = PuzzleManifest
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


class TraceRepository(DocumentRepository[GenerationTrace]):
    collection = "traces"
    model = GenerationTrace
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

    def transaction(self):
        return self._store.transaction()

    def record_counts(self) -> dict[str, int]:
        return {
            repo.collection: repo.count()
            for repo in (self.puzzles, self.manifests, self.traces)
        }
