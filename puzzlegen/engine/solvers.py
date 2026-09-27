"""Reusable verification shapes.

Three different kinds of proof, provided by the engine so that each game does
not reimplement exhaustive search and get the completeness reporting subtly
wrong. A game supplies the predicates that make its own rules; the search,
the budget and the honest completeness flag come from here.

* Partition coverage, for grouping games. Prove that exactly one way exists to
  split the items into valid groups.
* Path enumeration with optimality, for chain games. Prove that exactly one
  shortest route exists, and count the longer ones, because a shortest-path
  puzzle with no longer alternative asks nothing.
* Perfect matching counting, for pairing games. Count one-to-one assignments
  exactly, with pruning rather than by generating permutations.

Every search reports ``states_examined`` and a completeness flag. A search
that hits its budget returns SOUND_INCOMPLETE and a lower bound, never a count
dressed up as exact: the branch it did not reach is precisely where a second
solution would hide.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, TypeVar

from ..core.types import VerificationCompleteness

T = TypeVar("T")

#: Default ceiling on states any one search will examine. Generous enough for
#: the state spaces the reference games actually occupy, small enough that a
#: mistaken predicate fails fast instead of hanging a generation run.
DEFAULT_STATE_BUDGET = 2_000_000


@dataclass
class SearchResult:
    """What one exhaustive search found."""

    solutions: list[Any] = field(default_factory=list)
    states_examined: int = 0
    exhausted: bool = True
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.solutions)

    @property
    def completeness(self) -> VerificationCompleteness:
        return (
            VerificationCompleteness.COMPLETE
            if self.exhausted
            else VerificationCompleteness.SOUND_INCOMPLETE
        )

    def first(self) -> Any | None:
        return self.solutions[0] if self.solutions else None


class _Budget:
    """Counts states and reports exhaustion without raising through search."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("state budget must be positive")
        self.limit = limit
        self.used = 0
        self.exceeded = False

    def spend(self, amount: int = 1) -> bool:
        self.used += amount
        if self.used > self.limit:
            self.exceeded = True
        return not self.exceeded


def enumerate_partitions(
    items: Sequence[T],
    *,
    group_size: int,
    group_count: int,
    is_valid_group: Callable[[tuple[T, ...]], bool],
    is_valid_partition: Callable[[tuple[tuple[T, ...], ...]], bool] | None = None,
    max_solutions: int | None = None,
    state_budget: int = DEFAULT_STATE_BUDGET,
) -> SearchResult:
    """Every way to split ``items`` into ``group_count`` valid groups.

    The search anchors each group on the lowest remaining item, which is what
    makes it enumerate each partition once rather than once per ordering of
    its groups. Without that anchoring a sixteen-item partition into four
    fours would be counted twenty-four times over and "exactly one solution"
    would never be provable.

    ``is_valid_partition`` runs on complete partitions only. It is where a
    game expresses constraints that cannot be judged group by group, which is
    exactly the combination-level fairness the design requires.
    """
    if group_size < 1 or group_count < 1:
        raise ValueError("group size and count must be positive")
    needed = group_size * group_count
    if len(items) < needed:
        return SearchResult(states_examined=0)

    ordered = list(items)
    budget = _Budget(state_budget)
    result = SearchResult()
    valid_group_cache: dict[tuple[int, ...], bool] = {}

    def group_ok(indices: tuple[int, ...]) -> bool:
        cached = valid_group_cache.get(indices)
        if cached is None:
            cached = is_valid_group(tuple(ordered[i] for i in indices))
            valid_group_cache[indices] = cached
        return cached

    def recurse(remaining: tuple[int, ...], built: list[tuple[int, ...]]) -> None:
        if result.exhausted is False:
            return
        if len(built) == group_count:
            budget.spend()
            partition = tuple(
                tuple(ordered[i] for i in group) for group in built
            )
            if is_valid_partition is None or is_valid_partition(partition):
                result.solutions.append(partition)
            return

        # Anchoring on the first remaining index is the canonical-form trick
        # that collapses permutations of the same partition into one.
        anchor, rest = remaining[0], remaining[1:]
        for companions in combinations(rest, group_size - 1):
            if not budget.spend():
                return
            candidate = (anchor, *companions)
            if not group_ok(candidate):
                continue
            leftover = tuple(i for i in rest if i not in companions)
            if len(leftover) < group_size * (group_count - len(built) - 1):
                continue
            recurse(leftover, [*built, candidate])
            if max_solutions is not None and result.count >= max_solutions:
                return

    recurse(tuple(range(len(ordered))), [])
    result.states_examined = budget.used
    result.exhausted = not budget.exceeded and (
        max_solutions is None or result.count < max_solutions
    )
    return result


def enumerate_paths(
    start: T,
    goal: T,
    neighbours: Callable[[T], Iterable[tuple[T, Any]]],
    *,
    max_depth: int = 8,
    max_solutions: int | None = None,
    state_budget: int = DEFAULT_STATE_BUDGET,
) -> SearchResult:
    """Every simple path from ``start`` to ``goal`` within ``max_depth``.

    Breadth-first, so shortest paths are found first and the minimal length is
    known as soon as the first solution appears. Reports two metrics a
    minimal-path contract needs: how many solutions sit at that minimal
    length, and how many longer alternatives exist.
    """
    if max_depth < 1:
        raise ValueError("max_depth must be positive")

    budget = _Budget(state_budget)
    result = SearchResult()
    frontier: list[tuple[list[T], list[Any]]] = [([start], [])]

    while frontier:
        nodes, edges = frontier.pop(0)
        if len(edges) >= max_depth:
            continue
        for neighbour, edge in sorted(
            neighbours(nodes[-1]), key=lambda pair: repr(pair[0])
        ):
            if not budget.spend():
                frontier.clear()
                break
            if neighbour in nodes:
                continue
            path_nodes = [*nodes, neighbour]
            path_edges = [*edges, edge]
            if neighbour == goal:
                result.solutions.append((tuple(path_nodes), tuple(path_edges)))
                if max_solutions is not None and result.count >= max_solutions:
                    frontier.clear()
                    break
                continue
            frontier.append((path_nodes, path_edges))

    result.states_examined = budget.used
    result.exhausted = not budget.exceeded and (
        max_solutions is None or result.count < max_solutions
    )

    if result.solutions:
        lengths = [len(edges) for _, edges in result.solutions]
        minimal = min(lengths)
        result.metrics["minimal_length"] = float(minimal)
        result.metrics["minimal_count"] = float(
            sum(1 for length in lengths if length == minimal)
        )
        result.metrics["longer_alternatives"] = float(
            sum(1 for length in lengths if length > minimal)
        )
        result.metrics["branching_factor"] = (
            float(budget.used) / float(max(len(lengths), 1))
        )
    return result


def minimal_paths(result: SearchResult) -> list[Any]:
    """The shortest paths from a path search, for a minimal-path contract."""
    if not result.solutions:
        return []
    minimal = int(result.metrics.get("minimal_length", 0))
    return [
        solution for solution in result.solutions if len(solution[1]) == minimal
    ]


def count_perfect_matchings(
    left: Sequence[T],
    right: Sequence[T],
    compatible: Callable[[T, T], bool],
    *,
    max_solutions: int | None = None,
    state_budget: int = DEFAULT_STATE_BUDGET,
) -> SearchResult:
    """Every one-to-one assignment of ``left`` onto ``right``.

    Backtracking with a most-constrained-first ordering rather than generating
    permutations: for twelve pairs the permutation space is 479 million while
    the constrained search visits a few thousand states. That difference is
    the whole reason a matching game can be proved rather than sampled.
    """
    if len(left) != len(right):
        return SearchResult(states_examined=0)

    budget = _Budget(state_budget)
    result = SearchResult()

    options: dict[int, list[int]] = {
        i: [j for j, r in enumerate(right) if compatible(left[i], r)]
        for i in range(len(left))
    }
    if any(not choices for choices in options.values()):
        result.metrics["dead_ends"] = float(
            sum(1 for choices in options.values() if not choices)
        )
        return result

    used: set[int] = set()
    assignment: dict[int, int] = {}

    def recurse() -> None:
        if len(assignment) == len(left):
            budget.spend()
            result.solutions.append(
                tuple((left[i], right[assignment[i]]) for i in sorted(assignment))
            )
            return
        # Most constrained first: the row with fewest remaining options prunes
        # the largest subtree when it fails.
        pending = [i for i in options if i not in assignment]
        row = min(
            pending,
            key=lambda i: (len([j for j in options[i] if j not in used]), i),
        )
        for column in options[row]:
            if column in used:
                continue
            if not budget.spend():
                return
            used.add(column)
            assignment[row] = column
            recurse()
            del assignment[row]
            used.discard(column)
            if max_solutions is not None and result.count >= max_solutions:
                return

    recurse()
    result.states_examined = budget.used
    result.exhausted = not budget.exceeded and (
        max_solutions is None or result.count < max_solutions
    )
    result.metrics["mean_options"] = (
        sum(len(v) for v in options.values()) / len(options) if options else 0.0
    )
    return result


def collapse_equivalent(
    solutions: Sequence[Any], key: Callable[[Any], Any]
) -> tuple[list[Any], int]:
    """Collapse solutions that a tolerance rule treats as the same.

    Returns the representatives and how many were absorbed, because a
    tolerance contract has to report that collapsing actually happened rather
    than being assumed.
    """
    seen: dict[Any, Any] = {}
    collapsed = 0
    for solution in solutions:
        signature = key(solution)
        if signature in seen:
            collapsed += 1
            continue
        seen[signature] = solution
    return list(seen.values()), collapsed
