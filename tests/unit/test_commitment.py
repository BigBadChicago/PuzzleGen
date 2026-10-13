"""The dependency commitment, its proofs, and what it must not leak.

The tests that matter here are the negative ones. A Merkle tree that returns
the right root for the right input is easy; one that cannot be made to prove
membership of a ref it never committed to is the property the scheme is for.
"""

from __future__ import annotations

import pytest

from puzzlegen.engine.commitment import (
    LEAF_DOMAIN,
    NODE_DOMAIN,
    CommitmentError,
    ProofStep,
    dependency_root,
    leaf_hash,
    membership_proof,
    new_salt,
    verify_membership,
)

SALT = "a" * 64
OTHER = "b" * 64


def refs(count: int) -> list[str]:
    return [f"fact:ref{index:03d}" for index in range(count)]


class TestTheRoot:
    def test_the_same_refs_and_salt_commit_to_the_same_root(self):
        assert dependency_root(refs(7), SALT) == dependency_root(refs(7), SALT)

    def test_order_does_not_change_the_root(self):
        """Leaves are sorted, so a caller's ordering is not a commitment."""
        forward = refs(9)
        assert dependency_root(forward, SALT) == dependency_root(
            list(reversed(forward)), SALT
        )

    def test_a_different_salt_over_the_same_refs_is_a_different_root(self):
        """The property that makes a live board's root uninformative."""
        assert dependency_root(refs(5), SALT) != dependency_root(refs(5), OTHER)

    def test_one_changed_ref_changes_the_root(self):
        before = refs(6)
        after = [*before[:-1], "fact:substituted"]
        assert dependency_root(before, SALT) != dependency_root(after, SALT)

    @pytest.mark.parametrize("count", range(1, 18))
    def test_every_size_up_to_a_ragged_tree_builds(self, count):
        assert len(dependency_root(refs(count), SALT)) == 64

    def test_an_empty_set_is_refused(self):
        with pytest.raises(CommitmentError, match="at least one"):
            dependency_root([], SALT)

    def test_a_repeated_ref_is_refused(self):
        """Not deduplicated. A caller whose refs repeat has a bug upstream."""
        with pytest.raises(CommitmentError, match="distinct"):
            dependency_root(["fact:a", "fact:a"], SALT)

    def test_an_unsalted_leaf_is_refused(self):
        """The one thing that cannot be retrofitted once a root is published."""
        with pytest.raises(CommitmentError, match="salted"):
            dependency_root(refs(4), "")


class TestProofs:
    @pytest.mark.parametrize("count", range(1, 18))
    def test_every_ref_proves_its_own_membership(self, count):
        members = refs(count)
        root = dependency_root(members, SALT)
        for ref in members:
            proof = membership_proof(members, SALT, ref)
            assert verify_membership(root, SALT, ref, proof)

    def test_a_tampered_ref_fails_verification(self):
        members = refs(8)
        root = dependency_root(members, SALT)
        proof = membership_proof(members, SALT, members[3])
        assert not verify_membership(root, SALT, "fact:not-in-the-set", proof)

    def test_a_tampered_proof_step_fails_verification(self):
        members = refs(8)
        root = dependency_root(members, SALT)
        proof = list(membership_proof(members, SALT, members[3]))
        proof[0] = ProofStep(side=proof[0].side, hash="f" * 64)
        assert not verify_membership(root, SALT, members[3], proof)

    def test_a_flipped_side_fails_verification(self):
        """Sibling order is part of the proof, not a presentation detail."""
        members = refs(8)
        root = dependency_root(members, SALT)
        proof = [
            ProofStep(
                side="left" if step.side == "right" else "right", hash=step.hash
            )
            for step in membership_proof(members, SALT, members[3])
        ]
        assert not verify_membership(root, SALT, members[3], proof)

    def test_the_wrong_salt_fails_verification(self):
        members = refs(8)
        root = dependency_root(members, SALT)
        proof = membership_proof(members, SALT, members[3])
        assert not verify_membership(root, OTHER, members[3], proof)

    def test_a_ref_outside_the_set_cannot_be_proved(self):
        with pytest.raises(CommitmentError, match="not in the committed set"):
            membership_proof(refs(4), SALT, "fact:elsewhere")

    def test_an_interior_node_cannot_be_passed_off_as_a_leaf(self):
        """The second-preimage attack domain separation exists to stop.

        Without separate leaf and node domains, an internal node's hash is a
        value an attacker can also produce as a leaf, and a proof can be
        assembled for a ref that was never committed to.
        """
        assert LEAF_DOMAIN != NODE_DOMAIN
        members = refs(4)
        # The left subtree's root, which an attacker holding the published
        # tree would know, offered as if it were a leaf.
        left_pair = sorted(set(members))[:2]
        interior = dependency_root(left_pair, SALT)
        assert interior != leaf_hash(SALT, left_pair[0])


class TestSalts:
    def test_two_salts_differ(self):
        assert new_salt() != new_salt()

    def test_a_salt_is_long_enough_to_be_unguessable(self):
        assert len(new_salt()) == 64
