"""What a game turned out to need, measured rather than declared.

A declared capability manifest is a promise a game makes about itself, and a
promise is exactly the wrong instrument here: the game is untrusted code, the
declaration is written by the same author as the code, and nothing checks one
against the other until something goes wrong. So there is no declaration. A
profile is built from what the port actually saw -- which taxonomies were
reached into, which operations were used, how many queries and results a run
consumed -- and committed as an artifact. CI regenerates it and diffs it, so a
game that starts touching a second taxonomy produces a line a reviewer reads
rather than a surprise in production.

**Ceilings are recorded, not enforced.** An observed maximum is a
measurement. Turning it into a limit is how an ordinary content change starts
failing generation for a reason nobody stated: the graph grew, a query now
returns 180 rows where it returned 150, and a game that did nothing wrong
stops producing a board. :class:`~puzzlegen.content.port.PortBudget` already
enforces a *declared* cap, which is a different thing with a different
failure: the author chose the number and the message names it.

Observe-only at runtime, for the same reason. This module reads usage; it
refuses nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..content.port import PortUsage
from .records import GenerationTrace


class CapabilityProfile(BaseModel):
    """One game's observed reach, as an artifact.

    Frozen and canonically ordered, because its whole purpose is to be
    compared byte for byte against a committed copy. A profile whose field
    order depended on observation order would diff against itself.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record: Literal["capability_profile"] = "capability_profile"
    game_id: str
    game_version: str
    #: Named rather than implied, so a profile observed against one snapshot
    #: is not silently compared with one observed against another.
    snapshot_id: str = ""
    days_observed: int = Field(default=0, ge=0)
    taxonomies: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    #: The largest single-run figures seen. Measurements, not limits.
    max_queries_in_a_run: int = Field(default=0, ge=0)
    max_results_in_a_run: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in ("taxonomies", "operations"):
            values = getattr(self, name)
            if list(values) != sorted(set(values)):
                raise ValueError(
                    f"{name} must be sorted and distinct, or two profiles "
                    "over the same observations would not compare equal"
                )
        return self

    def to_document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "CapabilityProfile":
        return cls.model_validate(document)


@dataclass
class CapabilityObserver:
    """Accumulates one game's usage across the days of an admission run."""

    game_id: str
    game_version: str
    snapshot_id: str = ""
    days_observed: int = 0
    taxonomies: set[str] = field(default_factory=set)
    operations: set[str] = field(default_factory=set)
    max_queries_in_a_run: int = 0
    max_results_in_a_run: int = 0

    def observe_usage(self, usage: PortUsage) -> None:
        """Fold one run's live port usage in."""
        self.days_observed += 1
        self.taxonomies.update(usage.taxonomies)
        self.operations.update(usage.operation_kinds)
        self.max_queries_in_a_run = max(self.max_queries_in_a_run, usage.queries)
        self.max_results_in_a_run = max(self.max_results_in_a_run, usage.results)

    def observe_trace(self, trace: GenerationTrace) -> None:
        """Fold one stored run in.

        A failed run counts. A game that reached into a taxonomy and then
        failed to assemble a board still reached into it, and a profile that
        only observed successes would understate the reach of exactly the
        games worth watching.
        """
        self.days_observed += 1
        self.taxonomies.update(trace.port_taxonomies)
        self.operations.update(trace.port_operations)
        self.max_queries_in_a_run = max(
            self.max_queries_in_a_run, trace.port_queries
        )
        self.max_results_in_a_run = max(
            self.max_results_in_a_run, trace.port_results
        )

    def profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            game_id=self.game_id,
            game_version=self.game_version,
            snapshot_id=self.snapshot_id,
            days_observed=self.days_observed,
            taxonomies=tuple(sorted(self.taxonomies)),
            operations=tuple(sorted(self.operations)),
            max_queries_in_a_run=self.max_queries_in_a_run,
            max_results_in_a_run=self.max_results_in_a_run,
        )


def profile_from_traces(
    traces: Iterable[GenerationTrace],
    *,
    game_id: str,
    game_version: str,
    snapshot_id: str = "",
) -> CapabilityProfile:
    """Build a profile from an admission run's stored traces."""
    observer = CapabilityObserver(
        game_id=game_id, game_version=game_version, snapshot_id=snapshot_id
    )
    for trace in traces:
        if trace.game_id != game_id:
            raise ValueError(
                f"trace {trace.id} is for game {trace.game_id!r}, not "
                f"{game_id!r}; a profile over mixed games describes nothing"
            )
        observer.observe_trace(trace)
    return observer.profile()


@dataclass(frozen=True, slots=True)
class CapabilityDiff:
    """What changed between a committed profile and a fresh observation.

    New reach and lost reach are separate fields rather than one symmetric
    difference, because they mean opposite things to a reviewer: a new
    taxonomy is a capability nobody approved, and a vanished one is usually a
    content change that quietly stopped a query from matching.
    """

    game_id: str
    taxonomies_added: tuple[str, ...] = ()
    taxonomies_removed: tuple[str, ...] = ()
    operations_added: tuple[str, ...] = ()
    operations_removed: tuple[str, ...] = ()
    #: Ceilings that moved, as ``(name, committed, observed)``. Reported in
    #: both directions and gating neither: the number is a measurement.
    ceilings_changed: tuple[tuple[str, int, int], ...] = ()
    version_changed: tuple[str, str] | None = None
    snapshot_changed: tuple[str, str] | None = None

    @property
    def is_empty(self) -> bool:
        return not (
            self.taxonomies_added
            or self.taxonomies_removed
            or self.operations_added
            or self.operations_removed
            or self.ceilings_changed
            or self.version_changed
            or self.snapshot_changed
        )

    @property
    def widens_reach(self) -> bool:
        """Whether the game now touches something it did not before.

        The one question a reviewer has to answer. Separate from
        :attr:`is_empty` because a raised ceiling and a new taxonomy are not
        the same finding, and only one of them is about capability.
        """
        return bool(self.taxonomies_added or self.operations_added)

    def describe(self) -> tuple[str, ...]:
        lines: list[str] = []
        if self.version_changed:
            lines.append(
                f"game version {self.version_changed[0]} -> "
                f"{self.version_changed[1]}"
            )
        if self.snapshot_changed:
            lines.append(
                f"snapshot {self.snapshot_changed[0] or '(unnamed)'} -> "
                f"{self.snapshot_changed[1] or '(unnamed)'}"
            )
        for name, values in (
            ("taxonomy now reached", self.taxonomies_added),
            ("taxonomy no longer reached", self.taxonomies_removed),
            ("operation now used", self.operations_added),
            ("operation no longer used", self.operations_removed),
        ):
            lines.extend(f"{name}: {value}" for value in values)
        for name, committed, observed in self.ceilings_changed:
            lines.append(f"{name}: {committed} -> {observed}")
        return tuple(lines)


def diff_profiles(
    committed: CapabilityProfile, observed: CapabilityProfile
) -> CapabilityDiff:
    """Compare a committed profile with a fresh observation."""
    if committed.game_id != observed.game_id:
        raise ValueError(
            f"cannot diff profiles for different games: "
            f"{committed.game_id!r} and {observed.game_id!r}"
        )
    ceilings = tuple(
        (name, getattr(committed, name), getattr(observed, name))
        for name in ("max_queries_in_a_run", "max_results_in_a_run")
        if getattr(committed, name) != getattr(observed, name)
    )
    return CapabilityDiff(
        game_id=committed.game_id,
        taxonomies_added=_missing(observed.taxonomies, committed.taxonomies),
        taxonomies_removed=_missing(committed.taxonomies, observed.taxonomies),
        operations_added=_missing(observed.operations, committed.operations),
        operations_removed=_missing(committed.operations, observed.operations),
        ceilings_changed=ceilings,
        version_changed=(
            (committed.game_version, observed.game_version)
            if committed.game_version != observed.game_version
            else None
        ),
        snapshot_changed=(
            (committed.snapshot_id, observed.snapshot_id)
            if committed.snapshot_id != observed.snapshot_id
            else None
        ),
    )


def _missing(present: Sequence[str], against: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(present) - set(against)))


def write_profile(path: Any, profile: CapabilityProfile) -> None:
    """Write a profile to a committed artifact path.

    Sorted keys and a trailing newline, so the file a run produces is the file
    a diff compares: an artifact whose formatting depended on the writer would
    report a change on every run.
    """
    import json
    import pathlib

    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(profile.to_document(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_profile(path: Any) -> CapabilityProfile | None:
    """Read a committed profile, or ``None`` if there is not one yet.

    ``None`` rather than an exception, because a game's first run has nothing
    to compare against and that is a legitimate state, not a failure.
    """
    import json
    import pathlib

    source = pathlib.Path(path)
    if not source.exists():
        return None
    return CapabilityProfile.from_document(
        json.loads(source.read_text(encoding="utf-8"))
    )


def check_profile(path: Any, observed: CapabilityProfile) -> CapabilityDiff | None:
    """Diff an observation against the committed profile at ``path``.

    ``None`` means there is nothing committed yet. An empty diff means the
    observation matches.
    """
    committed = read_profile(path)
    if committed is None:
        return None
    return diff_profiles(committed, observed)


__all__ = [
    "CapabilityDiff",
    "CapabilityObserver",
    "CapabilityProfile",
    "check_profile",
    "diff_profiles",
    "profile_from_traces",
    "read_profile",
    "write_profile",
]
