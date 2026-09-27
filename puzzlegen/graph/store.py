"""The one persistence primitive the rest of the system is allowed to know.

Nine hand-written repository implementations per backend would mean nine
chances per backend to disagree about ordering, transactions or absence
semantics. Instead every repository is a typed wrapper over a single
:class:`DocumentStore`, so porting to PostgreSQL or a graph database means
writing one class rather than nine.

Two rules the interface enforces on every backend:

* Results are ordered by document id, always. Unordered iteration is the most
  common way determinism is lost, and a backend that returns insertion order
  would make generation depend on import order.
* Absence is ``None`` from :meth:`get` and never an exception. Repositories
  decide whether a miss is an error, because only they know the collection.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

#: A document's indexable values: field name to one or more string values.
#: Multi-valued fields exist because an entity has several aliases and a
#: dependency query needs to match any of them.
IndexValues = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class Query:
    """Equality and set-membership filters over indexed fields.

    Deliberately small. Range queries, text search and joins are not offered:
    every one of them would have to be reimplemented identically on each
    backend, and the content service builds its richer operations by combining
    indexed lookups with in-process filtering instead.
    """

    equals: Mapping[str, str] = field(default_factory=dict)
    any_of: Mapping[str, Sequence[str]] = field(default_factory=dict)
    limit: int | None = None
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("limit must be non-negative")
        if self.offset < 0:
            raise ValueError("offset must be non-negative")
        for name, values in self.any_of.items():
            if not values:
                raise ValueError(f"any_of[{name!r}] must not be empty")

    def indexed_fields(self) -> tuple[str, ...]:
        return tuple(sorted({*self.equals, *self.any_of}))

    def is_empty(self) -> bool:
        return not self.equals and not self.any_of


@runtime_checkable
class DocumentStore(Protocol):
    """A keyed store of JSON documents with equality indexes."""

    def declare_collection(self, collection: str, indexed_fields: Sequence[str]) -> None:
        """Register a collection and the fields that may be queried on it.

        Querying an undeclared field raises rather than silently scanning, so
        a missing index is a loud failure in development instead of a slow
        query in production.
        """

    def put(
        self,
        collection: str,
        doc_id: str,
        document: Mapping[str, Any],
        index_values: IndexValues,
    ) -> None:
        """Insert or replace a document and its index entries."""

    def get(self, collection: str, doc_id: str) -> dict[str, Any] | None: ...

    def get_many(
        self, collection: str, doc_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Fetch several documents. Missing ids are simply absent from the result."""

    def delete(self, collection: str, doc_id: str) -> bool:
        """Remove a document. Returns whether it existed."""

    def query(self, collection: str, query: Query) -> list[dict[str, Any]]:
        """Documents matching every filter, ordered by id."""

    def iter_all(self, collection: str) -> Iterator[dict[str, Any]]:
        """Every document in the collection, ordered by id."""

    def count(self, collection: str, query: Query | None = None) -> int: ...

    def transaction(self) -> AbstractContextManager[None]:
        """All-or-nothing write scope. Nesting joins the outer scope."""

    def close(self) -> None: ...
