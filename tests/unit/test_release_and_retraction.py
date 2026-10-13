"""The two things a published day needs beyond a recorded recipe.

The release check turns "reproducible" from a claim in a document into a check
that ran. The retraction turns "this fact was withdrawn" from an announcement
into something the player who played the board can verify for themselves.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.core import ids
from puzzlegen.core.types import DependencyRefKind
from puzzlegen.engine.commitment import verify_membership
from puzzlegen.engine.records import PuzzleStatus
from puzzlegen.engine.release import check_release
from puzzlegen.engine.retraction import RetractionService, retract_many

from conftest import DAY, PLAY_START, build_published_day

LATER_DAY = "2026-09-28"


@pytest.fixture
def dependencies(repos):
    return repos.dependencies


@pytest.fixture
def retraction(engine_repos, dependencies) -> RetractionService:
    return RetractionService(engine_repos, dependencies)


def publish_and_record(engine_repos, dependencies, *, day_key=DAY, game_id="oddoneout"):
    """A published day with its dependency edges written, as publish() does."""
    manifest = build_published_day(
        engine_repos, day_key=day_key, game_id=game_id
    )
    dependencies.record_refs(
        manifest_id=manifest.id,
        puzzle_id=manifest.puzzle_id,
        game_id=manifest.game_id,
        day_key=manifest.day_key,
        refs=[(DependencyRefKind.FACT, ref) for ref in manifest.fact_refs],
        created_at=PLAY_START,
    )
    return manifest


class TestTheCommitmentOnAManifest:
    def test_a_published_day_commits_to_its_dependency_set(
        self, engine_repos
    ):
        manifest = build_published_day(engine_repos)
        salt = engine_repos.dependency_salts.require(manifest.dependency_salt_id)
        assert manifest.commits_to(manifest.fact_refs, salt.salt)

    def test_the_public_view_shows_the_root_and_no_ref(self, engine_repos):
        manifest = build_published_day(engine_repos)
        view = manifest.public_view()
        assert view["dependency_root"] == manifest.dependency_root
        text = repr(view)
        for ref in manifest.fact_refs:
            assert ref not in text
        assert "salt" not in text

    def test_the_salt_is_not_released_when_the_day_is_published(
        self, engine_repos
    ):
        """During play the root commits; it does not reveal."""
        manifest = build_published_day(engine_repos)
        salt = engine_repos.dependency_salts.require(manifest.dependency_salt_id)
        assert salt.is_released is False

    def test_releasing_a_salt_is_idempotent_and_one_way(self, engine_repos):
        manifest = build_published_day(engine_repos)
        released = engine_repos.dependency_salts.release(manifest.id, PLAY_START)
        again = engine_repos.dependency_salts.release(
            manifest.id, PLAY_START + dt.timedelta(hours=1)
        )
        assert released.released_at == again.released_at
        with pytest.raises(Exception, match="cannot be withdrawn"):
            engine_repos.dependency_salts.put(
                released.model_copy(update={"released_at": None})
            )

    def test_a_manifest_is_unsealed_and_sealing_does_not_change_its_hash(
        self, engine_repos
    ):
        """The seal signs the hash, so it cannot be inside it."""
        manifest = build_published_day(engine_repos)
        assert manifest.is_sealed is False
        sealed = manifest.model_copy(update={"seal": "signature-goes-here"})
        assert sealed.is_sealed is True
        assert sealed.manifest_hash() == manifest.manifest_hash()


class TestTheReleaseCheck:
    def test_an_identical_rebuild_reproduces(self, engine_repos):
        manifest = build_published_day(engine_repos)
        record = engine_repos.puzzles.require(manifest.puzzle_id)
        certificate = check_release(manifest, lambda _inputs: record)
        assert certificate.reproduced
        assert certificate.conclusive
        assert not certificate.refuses_publication

    def test_a_changed_rebuild_is_refused_and_names_both_hashes(
        self, engine_repos
    ):
        manifest = build_published_day(engine_repos)
        record = engine_repos.puzzles.require(manifest.puzzle_id)
        perturbed = record.model_copy(
            update={"solution": {"answer": "opt_a"}}
        )
        certificate = check_release(manifest, lambda _inputs: perturbed)
        assert certificate.refuses_publication
        assert certificate.conclusive
        assert certificate.published_content_hash != (
            certificate.regenerated_content_hash
        )
        assert "disagreed" in certificate.describe()

    def test_a_rebuild_that_cannot_run_is_inconclusive_not_passing(
        self, engine_repos
    ):
        """An errored check refuses, or the gate is decorative."""
        manifest = build_published_day(engine_repos)

        def regenerate(_inputs):
            raise LookupError("snapshot snapshot:gone is not stored")

        certificate = check_release(manifest, regenerate)
        assert certificate.conclusive is False
        assert certificate.refuses_publication is True
        assert "snapshot:gone" in certificate.error

    def test_the_check_reads_the_manifests_own_recipe(self, engine_repos):
        """Not the caller's idea of the inputs."""
        manifest = build_published_day(engine_repos)
        record = engine_repos.puzzles.require(manifest.puzzle_id)
        seen = []
        check_release(manifest, lambda inputs: seen.append(inputs) or record)
        assert seen == [manifest.inputs]


class TestRetraction:
    def test_a_retraction_invalidates_every_affected_puzzle(
        self, engine_repos, dependencies, retraction
    ):
        first = publish_and_record(engine_repos, dependencies)
        second = publish_and_record(
            engine_repos, dependencies, day_key=LATER_DAY
        )
        document = retraction.retract(
            "fact:one", reason="the source withdrew the claim", at=PLAY_START
        )
        assert document.manifest_count == 2
        for manifest in (first, second):
            puzzle = engine_repos.puzzles.require(manifest.puzzle_id)
            assert puzzle.status is PuzzleStatus.INVALIDATED

    def test_an_invalidated_puzzle_is_still_readable(
        self, engine_repos, dependencies, retraction
    ):
        """A withdrawn day is marked, never deleted."""
        manifest = publish_and_record(engine_repos, dependencies)
        retraction.retract("fact:one", reason="withdrawn", at=PLAY_START)
        puzzle = engine_repos.puzzles.require(manifest.puzzle_id)
        assert puzzle.solution == {"answer": "opt_d"}
        assert engine_repos.manifests.get(manifest.id) is not None

    def test_a_player_can_verify_the_retraction_against_their_own_board(
        self, engine_repos, dependencies, retraction
    ):
        """The whole point: root from the board, salt and proof from the
        document, and nothing else."""
        manifest = publish_and_record(engine_repos, dependencies)
        document = retraction.retract(
            "fact:one", reason="withdrawn", at=PLAY_START
        )
        day = document.days[0]
        board_root = manifest.public_view()["dependency_root"]
        assert verify_membership(
            board_root, day.salt, document.ref_id, day.proof
        )
        assert retraction.verify(document) == ()

    def test_a_proof_does_not_carry_to_another_days_board(
        self, engine_repos, dependencies, retraction
    ):
        first = publish_and_record(engine_repos, dependencies)
        second = publish_and_record(
            engine_repos, dependencies, day_key=LATER_DAY
        )
        # Each day is salted separately, so two days that used the same facts
        # still publish different roots. That is what keeps one day's released
        # salt from opening another day's board.
        assert first.dependency_root != second.dependency_root
        document = retraction.retract(
            "fact:one", reason="withdrawn", at=PLAY_START
        )
        by_day = {day.day_key: day for day in document.days}
        assert not verify_membership(
            first.dependency_root,
            by_day[LATER_DAY].salt,
            document.ref_id,
            by_day[LATER_DAY].proof,
        )

    def test_retracting_releases_the_salt_of_each_affected_day(
        self, engine_repos, dependencies, retraction
    ):
        manifest = publish_and_record(engine_repos, dependencies)
        retraction.retract("fact:one", reason="withdrawn", at=PLAY_START)
        salt = engine_repos.dependency_salts.require(
            manifest.dependency_salt_id
        )
        assert salt.is_released

    def test_a_retraction_without_a_reason_is_refused(self, retraction):
        with pytest.raises(ValueError, match="must record why"):
            retraction.retract("fact:one", reason="", at=PLAY_START)

    def test_an_unused_ref_retracts_nothing_and_says_so(self, retraction):
        document = retraction.retract(
            "fact:never-used", reason="withdrawn", at=PLAY_START
        )
        assert document.days == ()
        assert document.blast_radius["manifest_count"] == 0

    def test_a_day_with_no_stored_salt_is_reported_not_dropped(
        self, engine_repos, dependencies, retraction
    ):
        manifest = publish_and_record(engine_repos, dependencies)
        engine_repos.dependency_salts.delete(manifest.dependency_salt_id)
        document = retraction.retract(
            "fact:one", reason="withdrawn", at=PLAY_START
        )
        assert document.days == ()
        assert document.unevidenced == (
            (manifest.id, "no commitment salt was stored"),
        )

    def test_the_document_is_serialisable_and_names_its_evidence(
        self, engine_repos, dependencies, retraction
    ):
        publish_and_record(engine_repos, dependencies)
        document = retraction.retract(
            "fact:one", reason="withdrawn", at=PLAY_START
        ).as_document()
        assert document["record"] == "retraction"
        day = document["days"][0]
        assert set(day) == {
            "manifest_id",
            "game_id",
            "day_key",
            "dependency_root",
            "salt",
            "proof",
        }

    def test_several_refs_retract_to_one_document_each(
        self, engine_repos, dependencies, retraction
    ):
        publish_and_record(engine_repos, dependencies)
        documents = retract_many(
            retraction,
            ["fact:one", "fact:two"],
            reason="the source withdrew both",
            at=PLAY_START,
        )
        assert [d.ref_id for d in documents] == ["fact:one", "fact:two"]
        assert all(retraction.verify(d) == () for d in documents)


class TestTheSaltRecord:
    def test_a_salt_id_must_be_minted_from_its_manifest(self, engine_repos):
        from puzzlegen.engine.records import DependencySalt

        manifest = build_published_day(engine_repos)
        with pytest.raises(ValueError, match="minted from its manifest"):
            DependencySalt(
                id=ids.for_dependency_salt(
                    ids.for_manifest(ids.for_puzzle(DAY, "other", "h"))
                ),
                manifest_id=manifest.id,
                game_id=manifest.game_id,
                day_key=manifest.day_key,
                salt="x" * 64,
                created_at=PLAY_START,
            )

    def test_a_second_salt_for_the_same_day_is_refused(self, engine_repos):
        manifest = build_published_day(engine_repos)
        stored = engine_repos.dependency_salts.require(
            manifest.dependency_salt_id
        )
        with pytest.raises(Exception, match="cannot be rebuilt"):
            engine_repos.dependency_salts.put(
                stored.model_copy(update={"salt": "y" * 64})
            )

    def test_a_whole_day_releases_at_once(self, engine_repos):
        build_published_day(engine_repos, game_id="oddoneout")
        build_published_day(engine_repos, game_id="another")
        released = engine_repos.dependency_salts.release_day(DAY, PLAY_START)
        assert len(released) == 2
        assert all(salt.is_released for salt in released)
