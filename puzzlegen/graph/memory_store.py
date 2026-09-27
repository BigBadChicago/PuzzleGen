"""In-memory :class:`DocumentStore`, used by tests and by snapshot building.

Kept behaviourally identical to the SQLite backend rather than merely similar:
the same conformance suite runs against both, because a fast test backend that
diverges from the production one converts real bugs into passing tests.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from ..core.errors import StorageError
from .store import IndexValues, Query


class InMemoryDocumentStore:
    """Dictionaries plus a copy-on-enter transaction."""

    def __init__(self) -> None:
        self._docs: dict[str, dict[str, dict[str, Any]]] = {}
        self._indexes: dict[str, frozenset[str]] = {}
        # collection -> field -> value -> set of doc ids
        self._index_data: dict[str, dict[str, dict[str, set[str]]]] = {}
        self._txn_depth = 0

    # -- schema -----------------------------------------------------------

    def declare_collection(self, collection: str, indexed_fields: Sequence[str]) -> None:
        existing = self._indexes.get(collection)
        fields = frozenset(indexed_fields)
        if existing is not None and existing != fields:
            raise StorageError(
                f"collection {collection!r} already declared with different indexes"
            )
        self._indexes[collection] = fields
        self._docs.setdefault(collection, {})
        self._index_data.setdefault(collection, {f: {} for f in fields})

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
        self._unindex(collection, doc_id)
        self._docs[collection][doc_id] = copy.deepcopy(dict(document))
        for name, values in index_values.items():
            bucket = self._index_data[collection].setdefault(name, {})
            for value in values:
                bucket.setdefault(str(value), set()).add(doc_id)

    def delete(self, collection: str, doc_id: str) -> bool:
        self._require(collection)
        existed = self._docs[collection].pop(doc_id, None) is not None
        if existed:
            self._unindex(collection, doc_id)
        return existed

    def _unindex(self, collection: str, doc_id: str) -> None:
        for bucket in self._index_data[collection].values():
            empty = [value for value, ids_ in bucket.items() if doc_id in ids_]
            for value in empty:
                bucket[value].discard(doc_id)
                if not bucket[value]:
                    del bucket[value]

    # -- reads ------------------------------------------------------------

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        self._require(collection)
        found = self._docs[collection].get(doc_id)
        return copy.deepcopy(found) if found is not None else None

    def get_many(
        self, collection: str, doc_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        self._require(collection)
        source = self._docs[collection]
        return {
            doc_id: copy.deepcopy(source[doc_id])
            for doc_id in sorted(set(doc_ids))
            if doc_id in source
        }

    def _matching_ids(self, collection: str, query: Query) -> list[str]:
        if query.is_empty():
            return sorted(self._docs[collection])

        unknown = set(query.indexed_fields()) - self._indexes[collection]
        if unknown:
            raise StorageError(
                f"query on undeclared index fields of {collection!r}: {sorted(unknown)}"
            )

        matched: set[str] | None = None
        clauses: list[tuple[str, tuple[str, ...]]] = [
            (name, (value,)) for name, value in query.equals.items()
        ]
        clauses += [(name, tuple(values)) for name, values in query.any_of.items()]

        for name, values in clauses:
            bucket = self._index_data[collection].get(name, {})
            hits: set[str] = set()
            for value in values:
                hits |= bucket.get(str(value), set())
            matched = hits if matched is None else matched & hits
            if not matched:
                return []
        return sorted(matched or set())

    def query(self, collection: str, query: Query) -> list[dict[str, Any]]:
        self._require(collection)
        ids_ = self._matching_ids(collection, query)
        window = ids_[query.offset :]
        if query.limit is not None:
            window = window[: query.limit]
        return [copy.deepcopy(self._docs[collection][i]) for i in window]

    def iter_all(self, collection: str) -> Iterator[dict[str, Any]]:
        self._require(collection)
        for doc_id in sorted(self._docs[collection]):
            yield copy.deepcopy(self._docs[collection][doc_id])

    def count(self, collection: str, query: Query | None = None) -> int:
        self._require(collection)
        if query is None or query.is_empty():
            return len(self._docs[collection])
        return len(self._matching_ids(collection, query))

    # -- transactions -----------------------------------------------------

    @contextmanager
    def transaction(self):
        if self._txn_depth > 0:
            # Nested scopes join the outer one: a partial rollback of an inner
            # scope would leave the outer scope's invariants half-applied.
            self._txn_depth += 1
            try:
                yield
            finally:
                self._txn_depth -= 1
            return

        saved_docs = copy.deepcopy(self._docs)
        saved_index = copy.deepcopy(self._index_data)
        self._txn_depth = 1
        try:
            yield
        except BaseException:
            self._docs = saved_docs
            self._index_data = saved_index
            raise
        finally:
            self._txn_depth = 0

    def close(self) -> None:
        return None
