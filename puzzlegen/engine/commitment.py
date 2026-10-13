"""The dependency commitment a published day carries.

A manifest holds ``fact_refs``, and a dependency list can betray an answer:
refs naming one seabird among three felines have told the reader which tile is
the odd one out. So the list cannot be published, and yet a day that publishes
nothing about its dependencies can retract nothing a player can check. The
commitment is the resolution: one hash a board can show while it is being
played, over leaves nobody can enumerate, with a membership proof available
afterwards for any single ref.

Two properties are load bearing.

**Leaves are salted.** The symbolic games draw from a few hundred entities; a
dictionary attack over unsalted leaves is a loop, not a cryptanalysis. The
salt is generated per day per game and released only when the day closes, so
during play the root commits to the dependency set without revealing it. This
is the one part of the scheme that cannot be retrofitted: a root published
over unsalted leaves is already readable and cannot be withdrawn.

**Leaf and node hashing are domain separated.** Without separation an internal
node's hash is a value an attacker can also produce as a leaf, which is the
standard second-preimage attack on an unannotated Merkle tree: a proof for a
ref that was never in the set can be assembled out of interior nodes.

An odd node at any level is promoted unchanged rather than paired with itself.
Promotion is safe here only because of the domain separation above; the two
decisions are one decision.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..core.hashing import stable_hash

#: Domain tags. Versioned in the string, so a future change to the tree's
#: shape is a different domain rather than a silently different root under the
#: same field name.
LEAF_DOMAIN = "puzzlegen/dependency-leaf/v1"
NODE_DOMAIN = "puzzlegen/dependency-node/v1"

#: Bytes of salt per commitment. Thirty-two, because the salt's only job is to
#: make leaf preimages unguessable and the cost of a longer one is nil.
SALT_BYTES = 32


class CommitmentError(ValueError):
    """A commitment could not be built or a proof could not be checked."""


def new_salt() -> str:
    """Fresh salt for one commitment, as lowercase hex.

    From :mod:`secrets`, not from the generation RNG. The rest of the engine
    is deterministic on purpose and this value must not be: a salt derived
    from the day's seed is a salt anybody holding the seed can recompute,
    which is every salt this scheme exists to prevent.
    """
    return secrets.token_hex(SALT_BYTES)


def leaf_hash(salt: str, ref: str) -> str:
    """The salted, domain-separated hash of one dependency ref."""
    if not salt:
        raise CommitmentError("a commitment leaf must be salted")
    return stable_hash([LEAF_DOMAIN, salt, ref])


def _node_hash(left: str, right: str) -> str:
    return stable_hash([NODE_DOMAIN, left, right])


def _leaves(refs: Sequence[str], salt: str) -> list[str]:
    if not refs:
        raise CommitmentError("a commitment must cover at least one dependency")
    unique = sorted(set(refs))
    if len(unique) != len(refs):
        raise CommitmentError("dependency refs must be distinct")
    return [leaf_hash(salt, ref) for ref in unique]


def _levels(leaves: Sequence[str]) -> list[list[str]]:
    """Every level of the tree, leaves first, root last."""
    levels = [list(leaves)]
    while len(levels[-1]) > 1:
        current = levels[-1]
        nxt: list[str] = []
        for index in range(0, len(current) - 1, 2):
            nxt.append(_node_hash(current[index], current[index + 1]))
        if len(current) % 2:
            nxt.append(current[-1])
        levels.append(nxt)
    return levels


def dependency_root(refs: Sequence[str], salt: str) -> str:
    """The commitment over ``refs`` under ``salt``.

    Deterministic in both arguments and independent of the order ``refs``
    arrive in, so two manifests built from the same dependency set and the
    same salt commit to the same root.
    """
    return _levels(_leaves(refs, salt))[-1][0]


@dataclass(frozen=True, slots=True)
class ProofStep:
    """One sibling on the path from a leaf to the root.

    ``side`` is the sibling's side, not the node's, because that is what a
    verifier needs to know to recombine them in the right order.
    """

    side: Literal["left", "right"]
    hash: str


def membership_proof(
    refs: Sequence[str], salt: str, ref: str
) -> tuple[ProofStep, ...]:
    """The sibling path proving ``ref`` is in the committed set.

    Pure, and takes the whole set because only the holder of the set can build
    a proof. That asymmetry is the scheme: the engine can prove membership of
    any one ref without ever publishing the rest.
    """
    if ref not in set(refs):
        raise CommitmentError(f"{ref!r} is not in the committed set")
    leaves = _leaves(refs, salt)
    index = leaves.index(leaf_hash(salt, ref))
    steps: list[ProofStep] = []
    for level in _levels(leaves)[:-1]:
        if index % 2:
            steps.append(ProofStep(side="left", hash=level[index - 1]))
            index //= 2
        elif index + 1 < len(level):
            steps.append(ProofStep(side="right", hash=level[index + 1]))
            index //= 2
        else:
            # A promoted odd node has no sibling at this level and no step to
            # record; its index on the level above is its own position there.
            index //= 2
    return tuple(steps)


def verify_membership(
    root: str, salt: str, ref: str, proof: Sequence[ProofStep]
) -> bool:
    """Whether ``proof`` carries ``ref`` to ``root`` under ``salt``.

    Takes no dependency set: a player checking a retraction has the root from
    the board they played, the salt from the closed day, the ref the
    retraction names and nothing else, which is exactly these four arguments.
    """
    try:
        running = leaf_hash(salt, ref)
    except CommitmentError:
        return False
    for step in proof:
        if step.side == "left":
            running = _node_hash(step.hash, running)
        elif step.side == "right":
            running = _node_hash(running, step.hash)
        else:
            return False
    return running == root


__all__ = [
    "LEAF_DOMAIN",
    "NODE_DOMAIN",
    "SALT_BYTES",
    "CommitmentError",
    "ProofStep",
    "dependency_root",
    "leaf_hash",
    "membership_proof",
    "new_salt",
    "verify_membership",
]
