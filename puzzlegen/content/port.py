"""The only content capability a game ever holds.

A plugin is handed one :class:`ContentPort` and nothing else. It has no
repository, no provider, no store, no credentials and no filesystem. Every
question it can ask is a method here, and every answer is a frozen view with
provenance and provider identity already stripped.

The port also keeps a ledger of the record ids it has issued. When a plugin
later reports which facts its puzzle depends on, the engine checks that list
against the ledger: a plugin cannot report a dependency it was never shown,
and cannot omit one either without the omission being visible. That check is
what turns "plugins must report fact_refs" from a documented obligation into
an enforced one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from ..core.errors import BoundaryViolationError
from .query import (
    ContentQuery,
    ContentRequirement,
    ContentResult,
    Operation,
)
from .service import ContentService


@dataclass(frozen=True, slots=True)
class PortBudget:
    """Limits on what one plugin may consume in one generation run.

    A plugin is untrusted code. Without a budget, an accidental loop in a
    third-party game would exhaust the generation run rather than failing its
    own puzzle, taking the day's other games down with it.
    """

    max_queries: int = 64
    max_results_per_query: int = 200
    max_total_results: int = 4000


@dataclass
class PortUsage:
    """What a plugin actually consumed. Recorded in generation telemetry."""

    queries: int = 0
    results: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    operations: dict[str, int] = field(default_factory=dict)

    def record(self, result: ContentResult) -> None:
        self.queries += 1
        self.results += len(result)
        key = str(result.operation)
        self.operations[key] = self.operations.get(key, 0) + 1
        for reason, count in result.rejected.items():
            self.rejected[reason] = self.rejected.get(reason, 0) + count


class ContentPort:
    """A game's entire view of the knowledge graph."""

    def __init__(
        self,
        service: ContentService,
        *,
        game_id: str,
        budget: PortBudget | None = None,
    ) -> None:
        self._service = service
        self._game_id = game_id
        self._budget = budget or PortBudget()
        self._usage = PortUsage()
        self._issued: set[str] = set()

    @property
    def game_id(self) -> str:
        return self._game_id

    @property
    def usage(self) -> PortUsage:
        return self._usage

    def issued_ids(self) -> frozenset[str]:
        """Every graph record id this port has shown the plugin."""
        return frozenset(self._issued)

    def verify_references(self, reported: Sequence[str]) -> tuple[str, ...]:
        """Ids the plugin reported that it was never shown.

        A non-empty result means the plugin fabricated a dependency, which the
        engine treats as a protocol violation rather than a content problem:
        dependency tracking that can be invented is not dependency tracking.
        """
        return tuple(sorted(set(reported) - self._issued))

    # -- the operations ---------------------------------------------------

    def find_entities(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_ENTITIES, constraints)

    def find_category_members(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_CATEGORY_MEMBERS, constraints)

    def find_siblings(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_SIBLINGS, constraints)

    def find_related_entities(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_RELATED_ENTITIES, constraints)

    def find_pairs(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_PAIRS, constraints)

    def find_groups(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_GROUPS, constraints)

    def find_intersecting_groups(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_INTERSECTING_GROUPS, constraints)

    def find_relationship_patterns(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_RELATIONSHIP_PATTERNS, constraints)

    def find_entities_by_frequency(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_ENTITIES_BY_FREQUENCY, constraints)

    def find_entities_by_difficulty(self, **constraints) -> ContentResult:
        return self._run(Operation.FIND_ENTITIES_BY_DIFFICULTY, constraints)

    def request(self, query: ContentQuery) -> ContentResult:
        """Run a pre-built query, for a game that assembled one itself."""
        return self._execute(query)

    def satisfy(
        self, requirements: Sequence[ContentRequirement]
    ) -> dict[str, ContentResult]:
        """Run a game's declared requirements in one batch.

        Batching is why a plugin can live behind a process boundary without
        paying a round trip per lookup: it declares what it needs up front and
        receives everything at once.
        """
        results: dict[str, ContentResult] = {}
        for requirement in requirements:
            result = self._execute(requirement.query)
            results[requirement.name] = result
            if not requirement.is_met(result):
                results[requirement.name] = replace(
                    result,
                    satisfied=False,
                    detail=(
                        f"requirement {requirement.name!r} needs "
                        f"{requirement.minimum}, got {len(result)}"
                    ),
                )
        return results

    # -- plumbing ---------------------------------------------------------

    def _run(self, operation: Operation, constraints: Mapping) -> ContentResult:
        unknown = set(constraints) - set(ContentQuery.__dataclass_fields__)
        if unknown:
            raise BoundaryViolationError(
                f"unknown content constraint(s): {sorted(unknown)}"
            )
        query = ContentQuery(operation=operation, **constraints)
        return self._execute(query)

    def _execute(self, query: ContentQuery) -> ContentResult:
        if self._usage.queries >= self._budget.max_queries:
            raise BoundaryViolationError(
                f"game {self._game_id!r} exceeded its query budget "
                f"({self._budget.max_queries})"
            )
        if query.limit > self._budget.max_results_per_query:
            query = replace(query, limit=self._budget.max_results_per_query)

        result = self._service.execute(query, game_id=self._game_id)
        self._usage.record(result)

        if self._usage.results > self._budget.max_total_results:
            raise BoundaryViolationError(
                f"game {self._game_id!r} exceeded its total result budget "
                f"({self._budget.max_total_results})"
            )

        self._issued.update(result.dependency_ids())
        return result
