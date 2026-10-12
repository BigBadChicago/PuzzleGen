"""Where this distribution's own files are, and where the platform's are.

Two roots, kept apart deliberately. ``GAME`` is this distribution: its tools,
its content, its tests. ``REPO`` is the repository that currently also holds
the platform, which only the built snapshot is reached through, because a
snapshot is platform content a game reads rather than content a game owns.

A test that wants the repository root and finds nothing is a test running
against an installed game rather than a checkout, which is a real situation
once a game ships separately, so the snapshot-dependent tests say so and skip
rather than failing.
"""

from __future__ import annotations

from pathlib import Path

#: This game distribution's root: the directory holding its pyproject.
GAME = Path(__file__).resolve().parents[1]

#: This game's own tools.
TOOLS = GAME / "tools"

#: The repository root, when the game is being tested inside the monorepo.
REPO = GAME.parents[1]

#: The snapshot ``docs/build-symbolic-snapshot.sh`` builds. Small enough to
#: rebuild in seconds and not committed, because it is a build output; a test
#: that needs it skips when it is absent.
SNAPSHOT = REPO / "build" / "graph-symbolic.sqlite"

#: The committed day run this game ships. A board a day for a month, generated
#: against the snapshot above and checked in, which is what makes the shipping
#: claim something a reader can audit rather than something a log once said.
SHIPPED_DAYS = REPO / "build" / "shape-trio-days-30.json"


def has_repository() -> bool:
    """Whether the platform's checkout is reachable from here."""
    return (REPO / "pyproject.toml").is_file()


def has_snapshot() -> bool:
    return SNAPSHOT.is_file()


def has_shipped_days() -> bool:
    return SHIPPED_DAYS.is_file()


__all__ = [
    "GAME",
    "REPO",
    "SHIPPED_DAYS",
    "SNAPSHOT",
    "TOOLS",
    "has_repository",
    "has_shipped_days",
    "has_snapshot",
]
