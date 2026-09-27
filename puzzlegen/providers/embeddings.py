"""Embeddings as a snapshot-time input, never a runtime dependency.

Vectors are computed once when a snapshot is built and frozen inside it. The
engine therefore never loads a model, and upgrading models is a snapshot
rebuild rather than a silent change to already-published puzzles.

Two implementations ship. :class:`TableEmbeddingProvider` reads vectors a real
model produced (see ``tools/export_embeddings.py``) and is the production
path. :class:`DevHashEmbeddingProvider` produces deterministic vectors with no
dependencies for local development; it carries no semantic meaning at all, and
labels itself in snapshot metadata as ``dev-hash`` so that a snapshot built
with it is obviously not production content.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

#: Marker model name. Any snapshot whose embedding model is this value is a
#: development snapshot and its embedding-based gates carry no real signal.
DEV_MODEL_NAME = "dev-hash"


@dataclass(frozen=True, slots=True)
class EmbeddingDescriptor:
    model_name: str
    model_version: str
    dimensions: int
    computed_at: dt.datetime
    #: How similarity between two of these vectors should be measured. Stored
    #: because a future model may want a metric other than cosine.
    similarity_metric: str = "cosine"

    def __post_init__(self) -> None:
        if self.computed_at.tzinfo is None:
            raise ValueError("computed_at must be timezone-aware")
        if self.dimensions < 2:
            raise ValueError("dimensions must be at least 2")

    @property
    def is_development_only(self) -> bool:
        return self.model_name == DEV_MODEL_NAME


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into a vector."""

    def describe(self) -> EmbeddingDescriptor: ...

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        """One unit-length vector per input, in input order."""


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, rescaled from [-1, 1] onto [0, 1].

    Rescaled because every similarity in this system is on [0, 1], and a
    threshold configured against one metric must not silently mean something
    different when read against another.
    """
    if len(a) != len(b):
        raise ValueError("vectors must share a dimensionality")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        raise ValueError("cannot compare a zero vector")
    raw = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, (raw + 1.0) / 2.0))


def centroid(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Mean vector of a group. Used for outlier detection."""
    if not vectors:
        raise ValueError("cannot take the centroid of no vectors")
    width = len(vectors[0])
    if any(len(v) != width for v in vectors):
        raise ValueError("vectors must share a dimensionality")
    count = len(vectors)
    return tuple(sum(v[i] for v in vectors) / count for i in range(width))


def normalize(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        raise ValueError("cannot normalize a zero vector")
    return tuple(x / norm for x in vector)


class TableEmbeddingProvider:
    """Serves vectors a real model already produced."""

    def __init__(
        self,
        vectors: dict[str, Sequence[float]],
        *,
        model_name: str,
        model_version: str,
        computed_at: dt.datetime,
        similarity_metric: str = "cosine",
    ) -> None:
        if not vectors:
            raise ValueError("embedding table is empty")
        dimensions = len(next(iter(vectors.values())))
        if any(len(v) != dimensions for v in vectors.values()):
            raise ValueError("embedding table has inconsistent dimensionality")
        self._vectors = {k: normalize(v) for k, v in vectors.items()}
        self._descriptor = EmbeddingDescriptor(
            model_name=model_name,
            model_version=model_version,
            dimensions=dimensions,
            computed_at=computed_at,
            similarity_metric=similarity_metric,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "TableEmbeddingProvider":
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            vectors={k: [float(x) for x in v] for k, v in document["vectors"].items()},
            model_name=document["model_name"],
            model_version=str(document["model_version"]),
            computed_at=dt.datetime.fromisoformat(document["computed_at"]),
            similarity_metric=document.get("similarity_metric", "cosine"),
        )

    def describe(self) -> EmbeddingDescriptor:
        return self._descriptor

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        missing = sorted({t for t in texts if t not in self._vectors})
        if missing:
            raise KeyError(f"embedding table has no vector for: {missing[:5]}")
        return [self._vectors[t] for t in texts]

    def has(self, text: str) -> bool:
        return text in self._vectors


class DevHashEmbeddingProvider:
    """Deterministic pseudo-vectors for local development only.

    Produces a stable vector per string with no semantic content whatsoever.
    Its purpose is to let the embedding-shaped parts of the pipeline run and
    be tested offline; any gate relying on it will reject at chance. The
    ``dev-hash`` model name propagates into snapshot metadata so this can
    never be mistaken for real signal.
    """

    def __init__(self, dimensions: int = 32, now: dt.datetime | None = None) -> None:
        self._descriptor = EmbeddingDescriptor(
            model_name=DEV_MODEL_NAME,
            model_version="1",
            dimensions=dimensions,
            computed_at=now or dt.datetime.now(dt.timezone.utc),
        )

    def describe(self) -> EmbeddingDescriptor:
        return self._descriptor

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> tuple[float, ...]:
        dimensions = self._descriptor.dimensions
        raw: list[float] = []
        counter = 0
        while len(raw) < dimensions:
            digest = hashlib.sha256(
                f"{DEV_MODEL_NAME}|{text}|{counter}".encode("utf-8")
            ).digest()
            for offset in range(0, len(digest), 2):
                if len(raw) == dimensions:
                    break
                word = int.from_bytes(digest[offset : offset + 2], "big")
                raw.append((word / 65535.0) * 2.0 - 1.0)
            counter += 1
        return normalize(raw)
