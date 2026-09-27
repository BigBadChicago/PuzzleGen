"""The only source of randomness permitted anywhere in the engine.

Python's ``random`` module is process-global and reseedable from anywhere, and
its output is not contractually stable across interpreter versions. Neither
property is acceptable when a puzzle must regenerate byte-identically years
later, so the engine uses an HMAC-SHA256 counter stream instead.

Substreams are derived by key, not by consuming the parent's counter. A child
stream therefore produces the same values regardless of how much the parent
consumed before deriving it, which means adding a call site in one part of
generation cannot silently change the output of an unrelated part.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")

_BLOCK = 32
_U64_MAX = (1 << 64) - 1

SEED_NAMESPACE = "puzzlegen/seed/v1"


def derive_seed(day_key: str, game_id: str, salt: str = "") -> bytes:
    """Seed for one game on one day.

    Public and reproducible by design: anyone holding the day key, the game id
    and the salt recorded in the manifest can regenerate the puzzle. The salt
    exists so a test or a staging environment can produce a disjoint puzzle
    stream from production without changing any other input.
    """
    material = "|".join([SEED_NAMESPACE, day_key, game_id, salt])
    return hashlib.sha256(material.encode("utf-8")).digest()


class DeterministicRng:
    """A reproducible stream of pseudo-random values."""

    __slots__ = ("_key", "_counter", "_label")

    def __init__(self, seed: bytes, label: str = "root") -> None:
        if not isinstance(seed, (bytes, bytearray)):
            raise TypeError("seed must be bytes")
        if len(seed) < 16:
            raise ValueError("seed must be at least 16 bytes")
        self._key = hmac.new(bytes(seed), label.encode("utf-8"), hashlib.sha256).digest()
        self._counter = 0
        self._label = label

    @property
    def label(self) -> str:
        return self._label

    @property
    def consumed(self) -> int:
        """Blocks drawn so far. Recorded in telemetry to detect drift."""
        return self._counter

    def derive(self, label: str) -> "DeterministicRng":
        """An independent substream, keyed by ``label``."""
        if not label:
            raise ValueError("substream label must be non-empty")
        child_seed = hmac.new(
            self._key, f"derive:{label}".encode("utf-8"), hashlib.sha256
        ).digest()
        return DeterministicRng(child_seed, label=f"{self._label}/{label}")

    def _block(self) -> bytes:
        block = hmac.new(
            self._key, self._counter.to_bytes(8, "big"), hashlib.sha256
        ).digest()
        self._counter += 1
        return block

    def next_u64(self) -> int:
        return int.from_bytes(self._block()[:8], "big")

    def randbelow(self, n: int) -> int:
        """Uniform integer in [0, n).

        Rejection sampling rather than modulo: modulo bias is small but it is
        systematic, and a systematically biased draw over a candidate pool
        distorts puzzle variety in a way that is very hard to notice later.
        """
        if n <= 0:
            raise ValueError("n must be positive")
        if n == 1:
            return 0
        limit = _U64_MAX - (_U64_MAX % n)
        while True:
            value = self.next_u64()
            if value <= limit:
                return value % n

    def random(self) -> float:
        """Uniform float in [0, 1) with 53 bits of precision."""
        return (self.next_u64() >> 11) / float(1 << 53)

    def choice(self, seq: Sequence[T]) -> T:
        if len(seq) == 0:
            raise ValueError("cannot choose from an empty sequence")
        return seq[self.randbelow(len(seq))]

    def shuffled(self, seq: Sequence[T]) -> list[T]:
        """Fisher-Yates over a copy. The input is never mutated."""
        items = list(seq)
        for i in range(len(items) - 1, 0, -1):
            j = self.randbelow(i + 1)
            items[i], items[j] = items[j], items[i]
        return items

    def sample(self, seq: Sequence[T], k: int) -> list[T]:
        """``k`` distinct members, in draw order."""
        if k < 0:
            raise ValueError("k must be non-negative")
        if k > len(seq):
            raise ValueError(f"cannot sample {k} from {len(seq)} items")
        return self.shuffled(seq)[:k]

    def weighted_choice(self, items: Sequence[tuple[T, float]]) -> T:
        """Choose by non-negative weight.

        Accumulates in input order and compares against a scaled draw, so the
        result depends only on the sequence given and never on float summation
        order elsewhere.
        """
        if not items:
            raise ValueError("cannot choose from an empty sequence")
        total = 0.0
        for _, weight in items:
            if weight < 0:
                raise ValueError("weights must be non-negative")
            total += weight
        if total <= 0:
            raise ValueError("total weight must be positive")
        target = self.random() * total
        cumulative = 0.0
        for item, weight in items:
            cumulative += weight
            if target < cumulative:
                return item
        return items[-1][0]
