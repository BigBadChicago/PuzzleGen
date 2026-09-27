"""Canonical serialisation and content hashing.

Reproducibility rests on one thing: the same logical value must always produce
the same bytes. Everything that is hashed, stored as a document, or compared
across runs goes through :func:`canonical_json`.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

_MAX_DEPTH = 64


def _normalise(value: Any, depth: int = 0) -> Any:
    """Reduce a value to JSON primitives with a deterministic representation."""
    if depth > _MAX_DEPTH:
        raise ValueError("value nested too deeply to canonicalise")

    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Enum):
        # StrEnum members are str subclasses; take .value so the emitted text
        # is the declared wire value rather than a subclass repr.
        return _normalise(value.value, depth + 1)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"non-finite float cannot be canonicalised: {value!r}")
        # Python's float repr is the shortest string that round-trips, and is
        # stable across CPython versions on IEEE-754 doubles.
        return value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be canonicalised; use UTC")
        return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, BaseModel):
        return _normalise(value.model_dump(mode="python"), depth + 1)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string mapping key: {key!r}")
            out[key] = _normalise(item, depth + 1)
        return dict(sorted(out.items()))
    if isinstance(value, (set, frozenset)):
        # Sets have no inherent order, so canonical form is a sorted list of
        # canonicalised members, compared by their serialised text.
        members = [_normalise(v, depth + 1) for v in value]
        return sorted(members, key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_normalise(v, depth + 1) for v in value]

    raise TypeError(f"cannot canonicalise {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialise to the one text form this system considers canonical."""
    return json.dumps(
        _normalise(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    """Content hash of any canonicalisable value, as lowercase hex sha256."""
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def short_hash(value: Any, length: int = 12) -> str:
    """Truncated :func:`stable_hash`, for ids and human-facing identifiers."""
    if not 4 <= length <= 64:
        raise ValueError("short hash length must be between 4 and 64")
    return stable_hash(value)[:length]
