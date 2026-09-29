"""The seed overlay: the file, and what it does when imported.

Two kinds of test. The first reads the file as data and checks the shape a
board needs, which is what catches an editing mistake in a pull request. The
second imports and activates it, which is what proves it takes the normal path
rather than the ten-accept one.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
from pathlib import Path

import pytest
from conftest import NOW

from puzzlegen.content.review import ReviewRepository, ReviewService
from puzzlegen.content.snapshots import ActivationPolicy, SnapshotBuilder
from puzzlegen.core import ids
from puzzlegen.core.types import ProvenanceClass, ReviewStatus, SourceKind
from puzzlegen.providers.curated import CuratedJSONProvider

SEED = Path(__file__).resolve().parents[2] / "content" / "seeds" / "overlay.curated.json"

OVERLAY_TAXONOMY = "overlay"
MEMBERSHIP_PREDICATE = "is_a"

#: The largest group size the day RNG can draw. A category with fewer members
#: than this is invisible to the generator on its widest days.
MAX_GROUP_SIZE = 9

EXPECTED_CATEGORIES = 15
EXPECTED_MEMBERS_EACH = 10


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(SEED.read_text(encoding="utf-8"))


@pytest.fixture
def imported(repos, document):
    """The seed imported and activated, exactly as a snapshot build does it."""
    builder = SnapshotBuilder(repos, now=NOW)
    builder.import_provider(
        CuratedJSONProvider(SEED, name="overlay-seed", now=NOW),
        taxonomy=OVERLAY_TAXONOMY,
    )
    builder.activate(ActivationPolicy())
    return repos


def members_of(document: dict) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {c["key"]: [] for c in document["categories"]}
    for entity in document["entities"]:
        for key in entity["categories"]:
            grouped[key].append(entity["name"])
    return grouped


class TestTheFile:
    def test_it_is_valid_json_at_the_supported_schema(self, document):
        assert document["curated_schema"] == 1

    def test_it_declares_a_version_and_an_update_date(self, document):
        assert document["version"]
        dt.date.fromisoformat(document["updated"])

    def test_it_carries_fifteen_categories(self, document):
        assert len(document["categories"]) == EXPECTED_CATEGORIES

    def test_every_category_has_a_gloss(self, document):
        assert all(c["gloss"] for c in document["categories"])

    def test_category_keys_are_unique(self, document):
        keys = [c["key"] for c in document["categories"]]
        assert len(set(keys)) == len(keys)

    def test_entity_keys_are_unique(self, document):
        keys = [e["key"] for e in document["entities"]]
        assert len(set(keys)) == len(keys)

    def test_every_category_has_exactly_ten_members(self, document):
        sizes = {key: len(names) for key, names in members_of(document).items()}
        assert set(sizes.values()) == {EXPECTED_MEMBERS_EACH}

    def test_every_category_can_serve_the_widest_board(self, document):
        """The hidden group needs as many members as the day's group size, and
        the day RNG can draw nine."""
        for key, names in members_of(document).items():
            assert len(names) >= MAX_GROUP_SIZE, key

    def test_no_category_repeats_a_member(self, document):
        for key, names in members_of(document).items():
            assert len(set(names)) == len(names), key

    def test_every_membership_names_a_declared_category(self, document):
        declared = {c["key"] for c in document["categories"]}
        for entity in document["entities"]:
            assert set(entity["categories"]) <= declared, entity["key"]

    def test_no_entity_is_named_after_a_category(self, document):
        """A member whose name is the category name gives the answer away."""
        names = {c["name"] for c in document["categories"]}
        assert not names & {e["name"] for e in document["entities"]}

    def test_some_members_sit_in_two_overlay_categories(self, document):
        """The overlay is a second axis, not a second taxonomy. A word that is
        both a color and a sharp thing is the whole point."""
        shared = [e for e in document["entities"] if len(e["categories"]) > 1]
        assert shared

    def test_it_mixes_shared_part_and_shared_word_axes(self, document):
        """Two kinds of second axis, because they fail differently.

        A shared part (teeth, wings) is a fact about the thing. A shared word
        form (a color, a name) is a fact about the label. A board built only
        from one kind reads the same every day.
        """
        names = {c["name"] for c in document["categories"]}
        assert {"thing with teeth", "thing with wings", "thing with a blade"} <= names
        assert {
            "word that is also a color",
            "word that is also a verb",
            "word that is also a person's name",
        } <= names

    def test_most_categories_draw_on_more_than_one_lexical_domain(self, document):
        """The point of the overlay is a group the taxonomy cannot make.

        A category whose members all come from one branch of WordNet is a
        visible group wearing a second name. Eleven of fifteen here cross two
        or more of animal, plant, tool, vehicle and instrument; the four that
        do not (wheels, floats, names, blown) are kept because their word play
        carries them.
        """
        single_domain = {
            "thing with wheels",
            "thing that floats",
            "word that is also a person's name",
            "thing that is blown",
        }
        names = {c["name"] for c in document["categories"]}
        assert len(names - single_domain) >= 11

    def test_it_declares_no_definitions(self, document):
        """Definitions belong to the lexical import.

        The overlay asserts membership and nothing else. Writing a gloss here
        would put a second definition in front of the merge, and whichever won
        would change what a vocabulary game says about a word the overlay only
        meant to categorise.
        """
        assert all("definition" not in e for e in document["entities"])

    def test_it_declares_no_facts_or_explicit_relationships(self, document):
        assert not document.get("facts")
        assert not document.get("relationships")


class TestWhatItImports:
    def test_the_source_is_curated_internal(self, imported):
        source = imported.sources.find(name="overlay-seed")[0]
        assert source.kind is SourceKind.CURATED_INTERNAL

    def test_the_categories_land_in_the_overlay_taxonomy(self, imported):
        overlay = imported.categories.live(OVERLAY_TAXONOMY)
        assert len(overlay) == EXPECTED_CATEGORIES

    def test_the_default_taxonomy_is_untouched(self, imported):
        assert imported.categories.live("default") == []

    def test_every_category_activates(self, imported):
        overlay = imported.categories.live(OVERLAY_TAXONOMY)
        assert all(c.status is ReviewStatus.ACTIVE for c in overlay)

    def test_every_membership_activates(self, imported):
        memberships = imported.relationships.find(predicate=MEMBERSHIP_PREDICATE)
        assert len(memberships) == EXPECTED_CATEGORIES * EXPECTED_MEMBERS_EACH
        assert all(r.status is ReviewStatus.ACTIVE for r in memberships)

    def test_membership_provenance_is_sourced_not_judged(self, imported):
        """The distinction the lifecycle already draws.

        A person asserted this; no machine inferred it. That is why it takes
        the normal activation path and not the ten-accept one, and it is not a
        special case for getting started.
        """
        for relationship in imported.relationships.find(
            predicate=MEMBERSHIP_PREDICATE
        ):
            assert all(
                p.provenance_class is ProvenanceClass.SOURCED
                for p in relationship.provenance
            )

    def test_every_category_has_ten_active_members_in_the_graph(self, imported):
        counts = collections.Counter(
            r.object_id
            for r in imported.relationships.find(predicate=MEMBERSHIP_PREDICATE)
            if r.status is ReviewStatus.ACTIVE
        )
        assert len(counts) == EXPECTED_CATEGORIES
        assert set(counts.values()) == {EXPECTED_MEMBERS_EACH}

    def test_entity_ids_are_lemma_derived_so_a_lexical_import_merges(
        self, imported, document
    ):
        """The overlay names the same entities WordNet will.

        Membership is asserted against ``entity:<hash of lemma>``, so importing
        a real lexicon later adds definitions and frequencies to the very
        records the overlay already points at, rather than creating a parallel
        set the generator cannot join.
        """
        for entity in document["entities"][:20]:
            assert imported.entities.get(ids.for_entity(entity["name"], "en"))

    def test_the_review_queue_stays_empty(self, imported):
        """Nothing here waits on a curator, because a curator wrote it."""
        service = ReviewService(imported, imported.attach(ReviewRepository))
        assert service.queue() == []

    def test_nothing_imported_is_pending_review(self, imported):
        for repo in (imported.entities, imported.categories, imported.relationships):
            assert repo.by_status(ReviewStatus.PENDING_REVIEW) == []

    def test_the_overlay_is_immediately_usable(self, imported):
        color = imported.categories.by_name("word that is also a color")[0]
        members = [
            r
            for r in imported.relationships.by_object(color.id, MEMBERSHIP_PREDICATE)
            if r.status is ReviewStatus.ACTIVE
        ]
        assert len(members) == EXPECTED_MEMBERS_EACH
        assert all(imported.entities.require(r.subject_id).is_usable() for r in members)


class TestItIsTheProposersInputFormat:
    def test_the_proposer_finds_the_seed_categories(self, imported):
        """The seed is designed as the proposer's input, not retrofitted to it.

        The proposer queries live overlay categories and their active members;
        after this import it finds fifteen of them, each with a member set big
        enough to build a centroid from once embeddings exist.
        """
        import importlib.util
        import sys

        tools = Path(__file__).resolve().parents[2] / "tools"
        spec = importlib.util.spec_from_file_location(
            "tool_propose_overlay_seed", tools / "propose_overlay.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        categories = [
            c
            for c in imported.categories.live(module.OVERLAY_TAXONOMY)
            if c.status is ReviewStatus.ACTIVE
        ]
        assert len(categories) == EXPECTED_CATEGORIES
        for category in categories:
            assert (
                len(module.existing_members(imported, category.id))
                == EXPECTED_MEMBERS_EACH
            )

    def test_the_proposer_proposes_nothing_from_the_seed_alone(self, imported):
        """No embeddings and no definitions yet, so neither signal can fire.

        Worth asserting: a proposer that invented candidates out of an empty
        vector store would fill the queue with noise on day one.
        """
        import importlib.util
        import sys

        tools = Path(__file__).resolve().parents[2] / "tools"
        spec = importlib.util.spec_from_file_location(
            "tool_propose_overlay_seed2", tools / "propose_overlay.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        reviews = imported.attach(ReviewRepository)
        assert module.propose(imported, reviews) == []


#: The five real exports the runbook produces. Named rather than globbed,
#: because ``wordnet-mini.lexicon.json`` is an 8 synset test fixture and a glob
#: would quietly run these tests against it and fail for the wrong reason.
LEXICON_ROOTS = (
    "carnivore",
    "bird",
    "fruit",
    "instrument",
    "vehicle",
    "garment",
    "tool",
)
LEXICONS = [
    SEED.parent / f"wordnet-{root}.lexicon.json" for root in LEXICON_ROOTS
]


@pytest.fixture(scope="module")
def lexicon_senses() -> dict[str, str]:
    """Every lemma the five real exports supply, with the gloss it would get.

    Read from the files rather than imported. A full import of 6,800 synsets
    takes 105 seconds, which is not a price worth paying on every run to
    re-prove a property that the files already settle: a lemma present as a
    sense becomes an entity with that synset's definition and that synset's
    category, and the import path for exactly that is already covered by
    ``TestWhatItImports`` and by the content service tests.
    """
    absent = [path.name for path in LEXICONS if not path.exists()]
    if absent:
        pytest.skip(f"lexical exports not committed: {', '.join(absent)}")

    senses: dict[str, str] = {}
    for path in LEXICONS:
        document = json.loads(path.read_text(encoding="utf-8"))
        glosses = {s["id"]: s.get("definition", "") for s in document["synsets"]}
        for sense in document["senses"]:
            senses.setdefault(
                sense["lemma"], sense.get("definition") or glosses.get(sense["synset"], "")
            )
    return senses


class TestTheSeedMeetsTheLexicon:
    """The property the whole re-authoring was for.

    A member the lexicon does not supply becomes an entity with no definition
    and no taxonomic parent, so it can only ever be a hidden group member. A
    member the lexicon does supply can sit in a visible group and be regrouped
    by the second axis, which is the mechanic the engine exists for. The first
    seed managed this for 10 of its 144 words, because it was written before
    the lexicon existed. Every member of this one was chosen from the lexicon.
    """

    def test_every_member_is_supplied_by_the_lexicon(self, document, lexicon_senses):
        absent = [
            entity["name"]
            for entity in document["entities"]
            if entity["name"] not in lexicon_senses
        ]
        assert absent == []

    def test_every_member_gets_a_definition(self, document, lexicon_senses):
        """Which is what the embedding is computed over."""
        for entity in document["entities"]:
            assert lexicon_senses[entity["name"]].strip()

    def test_the_members_are_single_words(self, document):
        """Multiword members would be rendered on a tile and read as two."""
        for entity in document["entities"]:
            assert " " not in entity["name"]
            assert "-" not in entity["name"]

    def test_no_member_is_a_lemma_the_lexicon_only_has_as_a_phrase(
        self, document, lexicon_senses
    ):
        for entity in document["entities"]:
            assert lexicon_senses[entity["name"]] is not None
