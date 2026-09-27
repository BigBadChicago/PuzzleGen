"""SQLite :class:`DocumentStore`, the default durable backend.

Two generic tables rather than one table per collection. A per-collection
schema would mean a migration every time a model gains a field, and the
documents are already validated by pydantic on read, so the database's job is
storage and index lookup rather than type enforcement.

The document body is stored as canonical JSON, which makes a snapshot file
byte-comparable between two machines that built it from the same inputs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.errors import StorageError
from ..core.hashing import canonical_json
from .store import IndexValues, Query

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    id         TEXT NOT NULL,
    body       TEXT NOT NULL,
    PRIMARY KEY (collection, id)
);

CREATE TABLE IF NOT EXISTS document_index (
    collection TEXT NOT NULL,
    field      TEXT NOT NULL,
    value      TEXT NOT NULL,
    id         TEXT NOT NULL,
    PRIMARY KEY (collection, field, value, id)
);

CREATE INDEX IF NOT EXISTS document_index_by_doc
    ON document_index (collection, id);
"""


class SqliteDocumentStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL keeps a reader (the running engine) from blocking a writer (a
        # snapshot import) on the same file.
        if self._path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._indexes: dict[str, frozenset[str]] = {}
        self._txn_depth = 0

    # -- schema -----------------------------------------------------------

    def declare_collection(self, collection: str, indexed_fields: Sequence[str]) -> None:
        fields = frozenset(indexed_fields)
        existing = self._indexes.get(collection)
        if existing is not None and existing != fields:
            raise StorageError(
                f"collection {collection!r} already declared with different indexes"
            )
        self._indexes[collection] = fields

    def _require(self, collection: str) -> None:
        if collection not in self._indexes:
            raise StorageError(f"collection {collection!r} is not declared")

    # -- writes -----------------------------------------------------------

    def put(
        self,
        collection: str,
        doc_id: str,
        document: Mapping[str, Any],
        index_values: IndexValues,
    ) -> None:
        self._require(collection)
        unknown = set(index_values) - self._indexes[collection]
        if unknown:
            raise StorageError(
                f"undeclared index fields on {collection!r}: {sorted(unknown)}"
            )
        body = canonical_json(document)
        with self.transaction():
            self._conn.execute(
                "INSERT INTO documents (collection, id, body) VALUES (?, ?, ?) "
                "ON CONFLICT (collection, id) DO UPDATE SET body = excluded.body",
                (collection, doc_id, body),
            )
            self._conn.execute(
                "DELETE FROM document_index WHERE collection = ? AND id = ?",
                (collection, doc_id),
            )
            rows = [
                (collection, name, str(value), doc_id)
                for name, values in index_values.items()
                for value in values
            ]
            if rows:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO document_index "
                    "(collection, field, value, id) VALUES (?, ?, ?, ?)",
                    rows,
                )

    def delete(self, collection: str, doc_id: str) -> bool:
        self._require(collection)
        with self.transaction():
            cursor = self._conn.execute(
                "DELETE FROM documents WHERE collection = ? AND id = ?",
                (collection, doc_id),
            )
            self._conn.execute(
                "DELETE FROM document_index WHERE collection = ? AND id = ?",
                (collection, doc_id),
            )
            return cursor.rowcount > 0

    # -- reads ------------------------------------------------------------

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        import json

        return json.loads(row["body"])

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        self._require(collection)
        row = self._conn.execute(
            "SELECT body FROM documents WHERE collection = ? AND id = ?",
            (collection, doc_id),
        ).fetchone()
        return self._decode(row) if row is not None else None

    def get_many(
        self, collection: str, doc_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        self._require(collection)
        wanted = sorted(set(doc_ids))
        if not wanted:
            return {}
        out: dict[str, dict[str, Any]] = {}
        # Chunked to stay clear of SQLite's variable limit on large batches.
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT id, body FROM documents "
                f"WHERE collection = ? AND id IN ({placeholders}) ORDER BY id",
                (collection, *chunk),
            ).fetchall()
            for row in rows:
                out[row["id"]] = self._decode(row)
        return out

    def _id_query(self, collection: str, query: Query) -> tuple[str, list[Any]]:
        clauses: list[tuple[str, tuple[str, ...]]] = [
            (name, (value,)) for name, value in sorted(query.equals.items())
        ]
        clauses += [
            (name, tuple(values)) for name, values in sorted(query.any_of.items())
        ]
        parts: list[str] = []
        params: list[Any] = []
        for name, values in clauses:
            placeholders = ",".join("?" * len(values))
            parts.append(
                "SELECT id FROM document_index "
                f"WHERE collection = ? AND field = ? AND value IN ({placeholders})"
            )
            params.extend([collection, name, *[str(v) for v in values]])
        return " INTERSECT ".join(parts), params

    def query(self, collection: str, query: Query) -> list[dict[str, Any]]:
        self._require(collection)
        if query.is_empty():
            sql = "SELECT body FROM documents WHERE collection = ? ORDER BY id"
            params: list[Any] = [collection]
        else:
            unknown = set(query.indexed_fields()) - self._indexes[collection]
            if unknown:
                raise StorageError(
                    f"query on undeclared index fields of {collection!r}: "
                    f"{sorted(unknown)}"
                )
            inner, params = self._id_query(collection, query)
            sql = (
                "SELECT d.body AS body FROM documents d "
                f"JOIN ({inner}) m ON m.id = d.id "
                "WHERE d.collection = ? ORDER BY d.id"
            )
            params = [*params, collection]

        if query.limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = [*params, query.limit, query.offset]
        elif query.offset:
            sql += " LIMIT -1 OFFSET ?"
            params = [*params, query.offset]

        return [self._decode(row) for row in self._conn.execute(sql, params)]

    def iter_all(self, collection: str) -> Iterator[dict[str, Any]]:
        self._require(collection)
        cursor = self._conn.execute(
            "SELECT body FROM documents WHERE collection = ? ORDER BY id", (collection,)
        )
        for row in cursor:
            yield self._decode(row)

    def count(self, collection: str, query: Query | None = None) -> int:
        self._require(collection)
        if query is None or query.is_empty():
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE collection = ?",
                (collection,),
            ).fetchone()
            return int(row["n"])
        inner, params = self._id_query(collection, query)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM ({inner})", params
        ).fetchone()
        return int(row["n"])

    # -- transactions -----------------------------------------------------

    @contextmanager
    def transaction(self):
        if self._txn_depth > 0:
            self._txn_depth += 1
            try:
                yield
            finally:
                self._txn_depth -= 1
            return

        self._conn.execute("BEGIN")
        self._txn_depth = 1
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")
        finally:
            self._txn_depth = 0

    def close(self) -> None:
        self._conn.close()
