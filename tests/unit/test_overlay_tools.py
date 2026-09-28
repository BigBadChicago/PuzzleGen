"""The overlay proposer and the review CLI.

Both are loaded by path rather than imported, because ``tools/`` is not a
package and deliberately is not one: nothing in the engine may import it.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import NOW, make_entity, sourced_provenance

from puzzlegen.content.review import (
    ACCEPT_THRESHOLD,
    ReviewRepository,
    ReviewService,
)
from puzzlegen.core import ids
from puzzlegen.core.types import (
    FreshnessClass,
    ProvenanceClass,
    ReviewStatus,
)
from puzzlegen.graph import (
    Category,
    EmbeddingRecord,
    GraphRepositories,
    Provenance,
    Relationship,
    SqliteDocumentStore,
)

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"tool_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


propose_overlay = load_tool("propose_overlay")
review_tool = load_tool("review")

MODEL = ("test-model", "v1")


class FixedClock:
    def __init__(self, instant: dt.datetime = NOW) -> None:
        self.instant = instant

    def now(self) -> dt.datetime:
        return self.instant


def overlay_category(name: str, source, *, status=ReviewStatus.ACTIVE) -> Category:
    return Category.build(
        canonical_name=name,
        parents=(),
        created_at=NOW,
        taxonomy=propose_overlay.OVERLAY_TAXONOMY,
        status=status,
        freshness_class=FreshnessClass.STATIC,
        confidence=0.95,
        provenance=(sourced_provenance(source),),
    )


def defined_entity(name: str, source, *, definition=None, aliases=()):
    """``make_entity`` with a gloss, which the lexical signal reads."""
    entity = make_entity(name, source, aliases=aliases)
    return entity.model_copy(update={"definition": definition})


def embed(entity_id: str, vector: tuple[float, ...]) -> EmbeddingRecord:
    return EmbeddingRecord(
        entity_id=entity_id,
        model_name=MODEL[0],
        model_version=MODEL[1],
        dimensions=len(vector),
        vector=vector,
        computed_at=NOW,
    )


def membership(
    entity_id: str,
    category_id: str,
    source,
    *,
    status=ReviewStatus.ACTIVE,
) -> Relationship:
    return Relationship.build(
        subject_id=entity_id,
        predicate=propose_overlay.MEMBERSHIP_PREDICATE,
        object_id=category_id,
        created_at=NOW,
        status=status,
        freshness_class=FreshnessClass.STATIC,
        provenance=(sourced_provenance(source),),
    )


@pytest.fixture
def world(repos, curated_source):
    """An overlay category with three embedded active members, plus two
    unattached entities: one that matches lexically, one that matches only by
    vector, and one that matches neither."""
    repos.sources.put(curated_source)
    colour = overlay_category("colour", curated_source)
    repos.categories.put(colour)

    members = {}
    for index, name in enumerate(("crimson", "azure", "amber")):
        entity = defined_entity(name, curated_source, definition=f"a {name} colour")
        repos.entities.put(entity)
        repos.embeddings.put(embed(entity.id, (1.0, 0.1 * index, 0.0)))
        repos.relationships.put(membership(entity.id, colour.id, curated_source))
        members[name] = entity

    salmon = defined_entity(
        "salmon", curated_source, definition="a fish, and a pale pinkish colour"
    )
    repos.entities.put(salmon)
    repos.embeddings.put(embed(salmon.id, (0.2, 0.1, 0.9)))

    rose = defined_entity("rose", curated_source, definition="a thorned garden flower")
    repos.entities.put(rose)
    repos.embeddings.put(embed(rose.id, (0.98, 0.15, 0.02)))

    hammer = defined_entity(
        "hammer", curated_source, definition="a tool for driving nails"
    )
    repos.entities.put(hammer)
    repos.embeddings.put(embed(hammer.id, (0.0, 0.0, 1.0)))

    return {
        "category": colour,
        "members": members,
        "salmon": salmon,
        "rose": rose,
        "hammer": hammer,
    }


@pytest.fixture
def reviews(repos) -> ReviewRepository:
    return repos.attach(ReviewRepository)


def ref_for(entity, category) -> str:
    return ids.for_relationship(
        entity.id, propose_overlay.MEMBERSHIP_PREDICATE, category.id
    )


class TestSignals:
    def test_a_category_name_in_the_definition_fires_lexically(self, world):
        signal = propose_overlay.lexical_signal(world["salmon"], world["category"])
        assert signal is not None
        assert signal.name == "lexical"
        assert "colour" in signal.evidence

    def test_an_unrelated_definition_does_not_fire(self, world):
        assert propose_overlay.lexical_signal(world["hammer"], world["category"]) is None

    def test_a_plural_in_the_definition_still_fires(self, world, curated_source):
        entity = defined_entity(
            "ochre", curated_source, definition="one of the earth colours"
        )
        assert propose_overlay.lexical_signal(entity, world["category"]) is not None

    def test_an_alias_can_carry_the_lexical_hit(self, world, curated_source):
        entity = defined_entity(
            "teal", curated_source, aliases=("a colour of the sea",)
        )
        signal = propose_overlay.lexical_signal(entity, world["category"])
        assert signal is not None
        assert "aliases" in signal.evidence

    def test_cosine_of_identical_vectors_is_one(self):
        assert propose_overlay.cosine((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)

    def test_cosine_of_orthogonal_vectors_is_zero(self):
        assert propose_overlay.cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)

    def test_a_zero_vector_has_no_direction_rather_than_an_error(self):
        assert propose_overlay.cosine((0.0, 0.0), (1.0, 0.0)) == 0.0

    def test_mismatched_dimensions_are_refused(self):
        with pytest.raises(ValueError, match="dimensions"):
            propose_overlay.cosine((1.0,), (1.0, 0.0))

    def test_a_centroid_averages_componentwise(self):
        assert propose_overlay.centroid([(0.0, 2.0), (2.0, 0.0)]) == (1.0, 1.0)

    def test_an_empty_centroid_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            propose_overlay.centroid([])

    def test_the_embedding_signal_respects_its_threshold(self):
        near = propose_overlay.embedding_signal((1.0, 0.0), (1.0, 0.0), 5, 0.55)
        far = propose_overlay.embedding_signal((0.0, 1.0), (1.0, 0.0), 5, 0.55)
        assert near is not None and far is None
        assert "5 existing members" in near.evidence


class TestScoring:
    def test_no_signal_scores_nothing(self):
        assert propose_overlay.score([], 0.55) == 0.0

    def test_both_signals_beat_either_alone(self):
        lexical = [propose_overlay.Signal("lexical", "", 1.0)]
        embedding = [propose_overlay.Signal("embedding", "", 0.9)]
        both = lexical + embedding
        assert propose_overlay.score(both, 0.55) > propose_overlay.score(lexical, 0.55)
        assert propose_overlay.score(both, 0.55) > propose_overlay.score(embedding, 0.55)

    def test_a_stronger_embedding_scores_higher(self):
        weak = [propose_overlay.Signal("embedding", "", 0.6)]
        strong = [propose_overlay.Signal("embedding", "", 0.95)]
        assert propose_overlay.score(strong, 0.55) > propose_overlay.score(weak, 0.55)

    def test_confidence_never_reaches_certainty(self):
        both = [
            propose_overlay.Signal("lexical", "", 1.0),
            propose_overlay.Signal("embedding", "", 1.0),
        ]
        assert propose_overlay.score(both, 0.55) <= 0.95


class TestProposing:
    def test_a_lexical_match_is_proposed(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        refs = {c.subject_ref for c in found}
        assert ref_for(world["salmon"], world["category"]) in refs

    def test_a_vector_match_without_a_lexical_hit_is_proposed(
        self, repos, reviews, world
    ):
        found = propose_overlay.propose(repos, reviews)
        rose = next(
            c for c in found if c.subject_ref == ref_for(world["rose"], world["category"])
        )
        assert rose.signal_names() == ["embedding"]

    def test_an_entity_matching_neither_signal_is_not_proposed(
        self, repos, reviews, world
    ):
        found = propose_overlay.propose(repos, reviews)
        refs = {c.subject_ref for c in found}
        assert ref_for(world["hammer"], world["category"]) not in refs

    def test_existing_members_are_not_re_proposed(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        member = world["members"]["crimson"]
        refs = {c.subject_ref for c in found}
        assert ref_for(member, world["category"]) not in refs

    def test_something_already_in_the_queue_is_not_proposed_twice(
        self, repos, reviews, world, curated_source
    ):
        repos.relationships.put(
            membership(
                world["salmon"].id,
                world["category"].id,
                curated_source,
                status=ReviewStatus.PENDING_REVIEW,
            )
        )
        found = propose_overlay.propose(repos, reviews)
        refs = [c.subject_ref for c in found]
        assert ref_for(world["salmon"], world["category"]) not in refs

    def test_too_few_member_vectors_disables_the_embedding_signal(
        self, repos, reviews, world
    ):
        found = propose_overlay.propose(repos, reviews, min_members=99)
        assert all("embedding" not in c.signal_names() for c in found)
        assert any("lexical" in c.signal_names() for c in found)

    def test_results_are_ordered_by_confidence(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        assert [c.confidence for c in found] == sorted(
            (c.confidence for c in found), reverse=True
        )

    def test_a_confidence_floor_filters(self, repos, reviews, world):
        assert propose_overlay.propose(repos, reviews, min_confidence=0.99) == []

    def test_a_retired_or_inactive_category_is_skipped(self, repos, reviews, world):
        repos.categories.put(
            world["category"].model_copy(update={"status": ReviewStatus.PENDING_REVIEW})
        )
        assert propose_overlay.propose(repos, reviews) == []

    def test_proposals_are_deterministic(self, repos, reviews, world):
        first = propose_overlay.propose(repos, reviews)
        second = propose_overlay.propose(repos, reviews)
        assert [c.subject_ref for c in first] == [c.subject_ref for c in second]


class TestRejectionMemory:
    def reject(self, repos, reviews, subject_ref: str) -> None:
        service = ReviewService(repos, reviews, clock=FixedClock())
        service.reject(subject_ref, reviewer="ada", proposal_batch="b0", at=NOW)

    def test_a_rejected_candidate_is_not_proposed_again(
        self, repos, reviews, world, curated_source
    ):
        ref = ref_for(world["salmon"], world["category"])
        repos.relationships.put(
            membership(
                world["salmon"].id,
                world["category"].id,
                curated_source,
                status=ReviewStatus.PENDING_REVIEW,
            )
        )
        self.reject(repos, reviews, ref)
        repos.relationships.delete(ref)
        assert all(c.subject_ref != ref for c in propose_overlay.propose(repos, reviews))

    def test_include_rejected_surfaces_it_with_the_rejection_attached(
        self, repos, reviews, world, curated_source
    ):
        ref = ref_for(world["salmon"], world["category"])
        repos.relationships.put(
            membership(
                world["salmon"].id,
                world["category"].id,
                curated_source,
                status=ReviewStatus.PENDING_REVIEW,
            )
        )
        self.reject(repos, reviews, ref)
        repos.relationships.delete(ref)
        found = propose_overlay.propose(repos, reviews, include_rejected=True)
        candidate = next(c for c in found if c.subject_ref == ref)
        assert candidate.previously_rejected
        assert candidate.rejected_by == "ada"
        assert candidate.rejected_at is not None


class TestWriting:
    def test_candidates_are_written_pending_and_judged(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        written = propose_overlay.write_candidates(
            repos, found, batch="b1", now=NOW
        )
        assert written == len(found)
        for candidate in found:
            stored = repos.relationships.require(candidate.subject_ref)
            assert stored.status is ReviewStatus.PENDING_REVIEW
            assert all(
                p.provenance_class is ProvenanceClass.JUDGED for p in stored.provenance
            )

    def test_the_placeholder_reviewer_is_the_tool_itself(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        propose_overlay.write_candidates(repos, found, batch="b1", now=NOW)
        stored = repos.relationships.require(found[0].subject_ref)
        assert stored.provenance[0].reviewer == "tools/propose_overlay.py"

    def test_the_firing_signals_are_recorded_on_the_record(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        propose_overlay.write_candidates(repos, found, batch="b1", now=NOW)
        salmon_ref = ref_for(world["salmon"], world["category"])
        stored = repos.relationships.require(salmon_ref)
        assert "lexical" in stored.provenance[0].verification_method

    def test_the_batch_is_recorded_on_the_record(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        propose_overlay.write_candidates(repos, found, batch="batch-xyz", now=NOW)
        stored = repos.relationships.require(found[0].subject_ref)
        assert stored.provenance[0].source_ref.startswith("batch-xyz/")

    def test_nothing_written_is_usable(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        propose_overlay.write_candidates(repos, found, batch="b1", now=NOW)
        for candidate in found:
            assert not repos.relationships.require(candidate.subject_ref).is_usable()

    def test_the_proposer_source_is_recorded(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        propose_overlay.write_candidates(repos, found, batch="b1", now=NOW)
        source = repos.sources.require(
            ids.for_source("overlay-proposer", propose_overlay.PROPOSER_VERSION)
        )
        assert source.name == "overlay-proposer"


class TestManifest:
    def test_the_manifest_counts_by_signal(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        document = propose_overlay.manifest_document(
            found, batch="b1", now=NOW, parameters={}
        )
        assert document["counts"]["candidates"] == len(found)
        assert document["counts"]["by_signal"]["lexical"] >= 1
        assert document["counts"]["by_signal"]["embedding"] >= 1

    def test_every_candidate_carries_its_evidence(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        document = propose_overlay.manifest_document(
            found, batch="b1", now=NOW, parameters={}
        )
        for candidate in document["candidates"]:
            assert candidate["signals"]
            assert all(s["evidence"] for s in candidate["signals"])

    def test_the_manifest_is_json_serialisable(self, repos, reviews, world):
        found = propose_overlay.propose(repos, reviews)
        document = propose_overlay.manifest_document(
            found, batch="b1", now=NOW, parameters={"min_similarity": 0.55}
        )
        assert json.loads(json.dumps(document))["batch"] == "b1"


@pytest.fixture
def db(tmp_path, curated_source) -> Path:
    """A real sqlite file, seeded the way the world fixture seeds memory."""
    path = tmp_path / "graph.sqlite"
    repos = GraphRepositories(SqliteDocumentStore(path))
    repos.sources.put(curated_source)
    colour = overlay_category("colour", curated_source)
    repos.categories.put(colour)
    for index, name in enumerate(("crimson", "azure", "amber")):
        entity = defined_entity(name, curated_source, definition=f"a {name} colour")
        repos.entities.put(entity)
        repos.embeddings.put(embed(entity.id, (1.0, 0.1 * index, 0.0)))
        repos.relationships.put(membership(entity.id, colour.id, curated_source))
    salmon = defined_entity(
        "salmon", curated_source, definition="a fish, and a pale pinkish colour"
    )
    repos.entities.put(salmon)
    repos.embeddings.put(embed(salmon.id, (0.2, 0.1, 0.9)))
    return path


def salmon_ref(db: Path) -> str:
    repos = GraphRepositories(SqliteDocumentStore(db))
    salmon = repos.entities.by_name("salmon")[0]
    colour = repos.categories.by_name("colour")[0]
    return ids.for_relationship(
        salmon.id, propose_overlay.MEMBERSHIP_PREDICATE, colour.id
    )


class TestProposerCommandLine:
    def test_a_run_writes_records_and_a_manifest(self, db, tmp_path, capsys):
        manifest = tmp_path / "proposals" / "b1.json"
        code = propose_overlay.main(
            ["--db", str(db), "--batch", "b1", "--manifest", str(manifest)]
        )
        assert code == 0
        assert manifest.exists()
        repos = GraphRepositories(SqliteDocumentStore(db))
        assert repos.relationships.get(salmon_ref(db)) is not None
        assert "candidates" in capsys.readouterr().out

    def test_a_dry_run_writes_the_manifest_and_nothing_else(self, db, tmp_path):
        manifest = tmp_path / "b1.json"
        propose_overlay.main(
            [
                "--db",
                str(db),
                "--batch",
                "b1",
                "--manifest",
                str(manifest),
                "--dry-run",
            ]
        )
        assert json.loads(manifest.read_text())["candidates"]
        repos = GraphRepositories(SqliteDocumentStore(db))
        assert repos.relationships.get(salmon_ref(db)) is None


class TestReviewCommandLine:
    def accept_args(self, db, ref, *, reviewer="ada", batch="b1", extra=()):
        return [
            "--db",
            str(db),
            "accept",
            ref,
            "--reviewer",
            reviewer,
            "--batch",
            batch,
            *extra,
        ]

    def seed_candidate(self, db, tmp_path) -> str:
        propose_overlay.main(
            [
                "--db",
                str(db),
                "--batch",
                "b1",
                "--manifest",
                str(tmp_path / "b1.json"),
            ]
        )
        return salmon_ref(db)

    def test_the_queue_lists_a_pending_candidate(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        assert review_tool.main(["--db", str(db), "queue"]) == 0
        assert ref in capsys.readouterr().out

    def test_the_queue_shows_manifest_evidence(self, db, tmp_path, capsys):
        self.seed_candidate(db, tmp_path)
        review_tool.main(
            ["--db", str(db), "--manifest", str(tmp_path / "b1.json"), "queue"]
        )
        assert "lexical" in capsys.readouterr().out

    def test_an_accept_is_recorded_and_reported(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        assert review_tool.main(self.accept_args(db, ref)) == 0
        out = capsys.readouterr().out
        assert "PENDING_REVIEW" in out
        assert f"1/{ACCEPT_THRESHOLD}" in out

    def test_a_repeated_accept_in_one_batch_is_recorded_but_not_credited(
        self, db, tmp_path, capsys
    ):
        """The ledger keeps it; the fold ignores it; the curator is told."""
        ref = self.seed_candidate(db, tmp_path)
        review_tool.main(self.accept_args(db, ref))
        assert review_tool.main(self.accept_args(db, ref)) == 0
        out = capsys.readouterr().out
        assert "not credited" in out
        assert out.count(f"1/{ACCEPT_THRESHOLD}") == 2
        capsys.readouterr()
        review_tool.main(["--db", str(db), "history", ref])
        assert capsys.readouterr().out.count("ACCEPT") == 2

    def test_a_reject_closes_the_item(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        code = review_tool.main(
            ["--db", str(db), "reject", ref, "--reviewer", "ada", "--batch", "b1"]
        )
        assert code == 0
        assert "REJECTED" in capsys.readouterr().out

    def test_a_lowered_threshold_approves_through_the_cli(self, db, tmp_path):
        ref = self.seed_candidate(db, tmp_path)
        review_tool.main(
            ["--db", str(db), "--threshold", "1", *self.accept_args(db, ref)[2:]]
        )
        repos = GraphRepositories(SqliteDocumentStore(db))
        assert repos.relationships.require(ref).status is ReviewStatus.APPROVED

    def test_the_batch_can_come_from_the_manifest(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        code = review_tool.main(
            [
                "--db",
                str(db),
                "--manifest",
                str(tmp_path / "b1.json"),
                "accept",
                ref,
                "--reviewer",
                "ada",
            ]
        )
        assert code == 0
        assert "1/" in capsys.readouterr().out

    def test_a_missing_batch_is_refused(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        code = review_tool.main(
            ["--db", str(db), "accept", ref, "--reviewer", "ada"]
        )
        assert code == 2
        assert "proposal batch is required" in capsys.readouterr().err

    def test_a_missing_reviewer_is_refused(self, db, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv(review_tool.REVIEWER_ENV, raising=False)
        ref = self.seed_candidate(db, tmp_path)
        code = review_tool.main(["--db", str(db), "accept", ref, "--batch", "b1"])
        assert code == 2
        assert "reviewer is required" in capsys.readouterr().err

    def test_the_reviewer_may_come_from_the_environment(
        self, db, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv(review_tool.REVIEWER_ENV, "ada")
        ref = self.seed_candidate(db, tmp_path)
        code = review_tool.main(["--db", str(db), "accept", ref, "--batch", "b1"])
        assert code == 0
        assert "1/" in capsys.readouterr().out

    def test_an_unknown_subject_reports_and_fails(self, db, tmp_path, capsys):
        self.seed_candidate(db, tmp_path)
        missing = ids.for_relationship(
            ids.for_entity("nothing", "en"),
            "is_a",
            ids.for_category("colour", "en", "overlay"),
        )
        assert review_tool.main(self.accept_args(db, missing)) == 1
        assert "no such review subject" in capsys.readouterr().err

    def test_history_prints_every_decision(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        review_tool.main(self.accept_args(db, ref, extra=("--note", "looks right")))
        capsys.readouterr()
        review_tool.main(["--db", str(db), "history", ref])
        out = capsys.readouterr().out
        assert "ACCEPT" in out and "looks right" in out

    def test_history_of_an_untouched_item_says_so(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        review_tool.main(["--db", str(db), "history", ref])
        assert "no decisions" in capsys.readouterr().out

    def test_show_reports_the_derived_state(self, db, tmp_path, capsys):
        ref = self.seed_candidate(db, tmp_path)
        review_tool.main(["--db", str(db), "show", ref])
        out = capsys.readouterr().out
        assert "derived status" in out and "to go" in out

    def test_an_empty_queue_says_so(self, db, capsys):
        review_tool.main(["--db", str(db), "queue"])
        assert "queue is empty" in capsys.readouterr().out

    def test_several_refs_are_decided_in_one_call(self, db, tmp_path, capsys):
        propose_overlay.main(
            ["--db", str(db), "--batch", "b1", "--manifest", str(tmp_path / "b1.json")]
        )
        refs = json.loads((tmp_path / "b1.json").read_text())["candidates"]
        subject_refs = [c["subject_ref"] for c in refs]
        code = review_tool.main(
            [
                "--db",
                str(db),
                "accept",
                *subject_refs,
                "--reviewer",
                "ada",
                "--batch",
                "b1",
            ]
        )
        assert code == 0
        assert capsys.readouterr().out.count("credited") == len(subject_refs)


class TestToolsCannotPromote:
    """The property the invariant test asserts statically, exercised live."""

    def test_proposing_and_accepting_nine_times_leaves_it_unusable(
        self, db, tmp_path
    ):
        propose_overlay.main(
            ["--db", str(db), "--batch", "b1", "--manifest", str(tmp_path / "b1.json")]
        )
        ref = salmon_ref(db)
        for day in range(1, ACCEPT_THRESHOLD):
            review_tool.main(
                [
                    "--db",
                    str(db),
                    "accept",
                    ref,
                    "--reviewer",
                    "ada",
                    "--batch",
                    f"b{day}",
                ]
            )
        repos = GraphRepositories(SqliteDocumentStore(db))
        stored = repos.relationships.require(ref)
        assert stored.status is ReviewStatus.PENDING_REVIEW
        assert not stored.is_usable()

    def test_repeated_accepts_in_one_day_never_cross_the_threshold(
        self, db, tmp_path
    ):
        """The credit rule holds through the CLI, which is where a curator
        would otherwise be able to click ten times in a minute."""
        propose_overlay.main(
            ["--db", str(db), "--batch", "b1", "--manifest", str(tmp_path / "b1.json")]
        )
        ref = salmon_ref(db)
        for index in range(ACCEPT_THRESHOLD + 5):
            review_tool.main(
                [
                    "--db",
                    str(db),
                    "accept",
                    ref,
                    "--reviewer",
                    f"curator-{index}",
                    "--batch",
                    f"batch-{index}",
                ]
            )
        repos = GraphRepositories(SqliteDocumentStore(db))
        assert repos.relationships.require(ref).status is ReviewStatus.PENDING_REVIEW
