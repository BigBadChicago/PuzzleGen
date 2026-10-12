"""Value windows and value ordering, over content that has a scale.

The query layer could already ask for an exact value of a predicate. It could
not ask for a range, and it could not ask for an order, and the second gap is
the one that bites: a query has a limit, the service applies it, and a caller
sorting what it received has sorted an arbitrary slice. Both gaps are what a
game over numbers needs and no lexical game ever asked for, which is why they
arrived with this content rather than before it.

Built against a real generated snapshot rather than hand-built records. The
providers are the thing that makes numbers available at all, and a test of the
query over fixtures nothing produces would pass while the pair was broken.
"""

from __future__ import annotations

import datetime as dt

import pytest

from puzzlegen.content.policy import ContentPolicy, PolicyService
from puzzlegen.content.port import ContentPort, PortBudget
from puzzlegen.content.query import (
    VALUE_OPERATIONS,
    ContentQuery,
    Operation,
    ResultOrder,
)
from puzzlegen.content.service import ContentService
from puzzlegen.content.snapshots import SnapshotBuilder
from puzzlegen.core.errors import RejectionReason
from puzzlegen.graph.memory_store import InMemoryDocumentStore
from puzzlegen.graph.repositories import GraphRepositories
from puzzlegen.providers.generated import NumberRangeProvider

from ..conftest import NOW

NUMBERS = "numbers"


def build_service(repos, providers):
    meta, _ = SnapshotBuilder(repos, now=NOW).build("numbers", providers=providers)
    return ContentService(
        repos,
        policy=PolicyService(ContentPolicy(name="platform", minimum_confidence=0.7)),
        snapshot=meta,
        now=NOW,
    )


def number_providers(*ranges):
    return [
        (provider, {"taxonomy": taxonomy, "entity_identity": "lemma"})
        for provider, taxonomy in ranges
    ]


@pytest.fixture(scope="module")
def service():
    """One snapshot for the whole module, over the in-memory store.

    Not the parametrized ``repos`` fixture, and deliberately. Every test here
    is about what a query returns, which is backend-independent by
    construction: the storage suite is where the two backends are held to the
    same behaviour, and building a hundred numbers twice per test to restate
    that would be forty-odd snapshot builds for one claim nothing here makes.
    """
    store = InMemoryDocumentStore()
    try:
        yield build_service(
            GraphRepositories(store),
            number_providers((NumberRangeProvider(0, 99, now=NOW), NUMBERS)),
        )
    finally:
        store.close()


@pytest.fixture(scope="module")
def port(service):
    return ContentPort(service, game_id="numbers", budget=PortBudget(max_queries=256))


def values(result) -> list[int]:
    return [int(view.properties["value"]) for view in result.entities]


class TestTheSnapshotItself:
    """The providers and the graph, meeting for the first time.

    A number is an entity like any other once it is through the normaliser,
    which is the property that lets every query below be an ordinary query.
    """

    def test_a_snapshot_can_be_built_from_a_generated_provider_alone(self, service):
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, taxonomy=NUMBERS, limit=500)
        )
        assert len(result.entities) == 100

    def test_a_number_carries_its_properties_as_facts(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                predicate_value=17,
            )
        )
        (view,) = result.entities
        assert view.name == "17"
        assert view.properties["spoken"] == "seventeen"
        assert view.properties["is_prime"] is True
        assert view.properties["digit_sum"] == 8

    def test_a_number_carries_its_categories(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_CATEGORY_MEMBERS,
                category="prime number",
                taxonomy=NUMBERS,
                limit=100,
            )
        )
        found = sorted(values(result))
        assert found[:5] == [2, 3, 5, 7, 11]
        assert 91 not in found

    def test_the_same_number_in_two_taxonomies_is_one_entity(self, repos):
        """``seven`` is one entity with two memberships, not two sevens.

        This is the shared-graph property the whole architecture rests on,
        working on content that is not words: a digits taxonomy and a numbers
        taxonomy overlap, and where they do they name the same record. Run
        over both backends, because it is a claim about what was written.
        """
        service = build_service(
            repos,
            number_providers(
                (NumberRangeProvider(0, 19, now=NOW), NUMBERS),
                (
                    NumberRangeProvider(
                        0, 9, name="digits", kind="DIGIT", root_name="digit", now=NOW
                    ),
                    "digits",
                ),
            ),
        )
        result = service.execute(
            ContentQuery(operation=Operation.FIND_ENTITIES, limit=500)
        )
        # Twenty numbers, not thirty: the ten digits are the same entities the
        # number range already supplied.
        assert len(result.entities) == 20

        # ``types`` is read in the taxonomy the query names, so the two
        # memberships are two questions rather than one answer.
        def types_of(taxonomy: str) -> tuple[str, ...]:
            found = service.execute(
                ContentQuery(
                    operation=Operation.FIND_ENTITIES,
                    taxonomy=taxonomy,
                    predicate="value",
                    predicate_value=7,
                )
            )
            return found.entities[0].types

        assert "number" in types_of(NUMBERS)
        assert "digit" in types_of("digits")


class TestTheValueWindow:
    def test_a_window_selects_a_range_of_values(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                predicate_at_least=10,
                predicate_at_most=19,
                limit=100,
            )
        )
        assert sorted(values(result)) == list(range(10, 20))

    def test_both_ends_are_inclusive(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                predicate_at_least=5,
                predicate_at_most=5,
            )
        )
        assert values(result) == [5]

    def test_one_open_end_is_legal(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                predicate_at_least=95,
                limit=100,
            )
        )
        assert sorted(values(result)) == [95, 96, 97, 98, 99]

    def test_a_window_composes_with_a_category(self, service):
        """The two constraints are independent, which is the whole point of
        asking the graph rather than filtering locally."""
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_CATEGORY_MEMBERS,
                category="square number",
                taxonomy=NUMBERS,
                predicate="value",
                predicate_at_least=10,
                predicate_at_most=99,
                limit=100,
            )
        )
        assert sorted(values(result)) == [16, 25, 36, 49, 64, 81]

    def test_a_window_over_a_derived_predicate_works_the_same(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="digit_sum",
                predicate_at_least=17,
                limit=100,
            )
        )
        assert sorted(values(result)) == [89, 98, 99]

    def test_a_value_outside_the_window_is_counted_as_a_rejection(self, service):
        """A reason rather than a silent absence.

        A caller that asked for the two-digit squares and got nothing needs to
        know whether the predicate was missing or every value fell outside:
        the first wants a different predicate, the second a wider window.
        """
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                predicate_at_least=0,
                predicate_at_most=9,
                limit=100,
            )
        )
        assert result.rejected[RejectionReason.VALUE_OUT_OF_RANGE.value] == 90

    def test_a_non_numeric_value_is_outside_every_window(self, service):
        """``parity`` is "even" or "odd", and neither is at least zero.

        The alternative is a string passing a bound it was never compared
        against, which is exactly what the query layer refuses an unsupported
        operation for.
        """
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="parity",
                predicate_at_least=0,
                limit=100,
            )
        )
        assert result.entities == ()


class TestTheOrdering:
    def test_ascending_is_by_value_and_not_by_id(self, service):
        """Entity ids are slugs of the digits, so id order is lexicographic:
        1, 10, 100, 11. A number game needs the other one."""
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                order=ResultOrder.PREDICATE_ASCENDING,
                limit=12,
            )
        )
        assert values(result) == list(range(12))

    def test_descending_reverses_it(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                order=ResultOrder.PREDICATE_DESCENDING,
                limit=5,
            )
        )
        assert values(result) == [99, 98, 97, 96, 95]

    def test_the_order_is_applied_before_the_limit(self, service):
        """The reason ordering is part of the request at all.

        Truncating an id-ordered pool and sorting the remainder would hand a
        caller a sorted sample of an arbitrary slice, and the caller could not
        tell the difference from the result.
        """
        unordered = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES, taxonomy=NUMBERS, limit=10
            )
        )
        ordered = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                order=ResultOrder.PREDICATE_ASCENDING,
                limit=10,
            )
        )
        assert values(ordered) == list(range(10))
        assert sorted(values(unordered)) != list(range(10))

    def test_ordering_composes_with_a_window(self, service):
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="value",
                predicate_at_least=20,
                predicate_at_most=24,
                order=ResultOrder.PREDICATE_DESCENDING,
                limit=10,
            )
        )
        assert values(result) == [24, 23, 22, 21, 20]

    def test_id_order_is_the_default_and_is_still_reproducible(self, service):
        query = ContentQuery(
            operation=Operation.FIND_ENTITIES, taxonomy=NUMBERS, limit=20
        )
        assert query.order is ResultOrder.ID
        first = [v.entity_id for v in service.execute(query).entities]
        second = [v.entity_id for v in service.execute(query).entities]
        assert first == second == sorted(first)

    def test_an_entity_with_no_number_under_the_predicate_sorts_last(self, service):
        """Reaching the sort means the predicate is asserted; what is missing
        is a number. That tile goes to the end rather than losing the day."""
        result = service.execute(
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                taxonomy=NUMBERS,
                predicate="parity",
                order=ResultOrder.PREDICATE_ASCENDING,
                limit=100,
            )
        )
        # Every value is textual, so nothing has a position and the tie-break
        # on the id is what remains.
        ids = [v.entity_id for v in result.entities]
        assert ids == sorted(ids)


class TestTheRefusals:
    def test_a_window_on_an_operation_that_cannot_honour_it_is_refused(self):
        with pytest.raises(ValueError, match="not honoured by"):
            ContentQuery(
                operation=Operation.FIND_GROUPS,
                predicate="value",
                predicate_at_least=1,
            )

    def test_an_ordering_on_an_operation_that_cannot_honour_it_is_refused(self):
        with pytest.raises(ValueError, match="not honoured by"):
            ContentQuery(
                operation=Operation.FIND_SIBLINGS,
                predicate="value",
                order=ResultOrder.PREDICATE_ASCENDING,
            )

    def test_a_window_with_no_predicate_is_refused(self):
        with pytest.raises(ValueError, match="needs a predicate"):
            ContentQuery(operation=Operation.FIND_ENTITIES, predicate_at_most=9)

    def test_an_inverted_window_is_refused(self):
        with pytest.raises(ValueError, match="exceeds predicate_at_most"):
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                predicate="value",
                predicate_at_least=10,
                predicate_at_most=5,
            )

    def test_an_exact_value_and_a_window_cannot_both_be_asked(self):
        """Two different questions, so a caller asking both has one wrong."""
        with pytest.raises(ValueError, match="cannot both be set"):
            ContentQuery(
                operation=Operation.FIND_ENTITIES,
                predicate="value",
                predicate_value=5,
                predicate_at_least=1,
            )

    def test_the_supported_operations_are_the_ones_that_select_entities(self):
        assert VALUE_OPERATIONS == {
            Operation.FIND_ENTITIES,
            Operation.FIND_CATEGORY_MEMBERS,
            Operation.FIND_ENTITIES_BY_FREQUENCY,
            Operation.FIND_ENTITIES_BY_DIFFICULTY,
        }


class TestThePortCarriesThem:
    def test_a_game_can_ask_for_a_window_through_its_port(self, port):
        """The port forwards every ContentQuery field, so a new constraint
        reaches games with no change to the port at all."""
        result = port.find_entities(
            taxonomy=NUMBERS,
            predicate="value",
            predicate_at_least=90,
            order=ResultOrder.PREDICATE_ASCENDING,
            limit=20,
        )
        assert values(result) == list(range(90, 100))

    def test_the_issued_ledger_covers_what_a_window_returned(self, port):
        result = port.find_entities(
            taxonomy=NUMBERS, predicate="value", predicate_at_most=4, limit=20
        )
        assert set(result.dependency_ids()) <= port.issued_ids()
