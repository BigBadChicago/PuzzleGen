"""Where this distribution's own files are, and where the platform's are.

Two roots, kept apart deliberately. ``GAME`` is this distribution: its tools,
its content, its tests. ``REPO`` is the repository that currently also holds
the platform, which only the lexicon seeds are reached through, because
lexicons are platform content a game reads rather than content a game owns.

A test that wants the repository root and finds nothing is a test running
against an installed game rather than a checkout, which is a real situation
once a game ships separately, so the lexicon-dependent tests say so and skip
rather than failing.
"""

from __future__ import annotations

from pathlib import Path

#: This game distribution's root: the directory holding its pyproject.
GAME = Path(__file__).resolve().parents[1]

#: This game's own tools and content.
TOOLS = GAME / "tools"
CONTENT = GAME / "content"
OVERLAY_SEED = CONTENT / "overlay.curated.json"

#: The repository root, when the game is being tested inside the monorepo.
REPO = GAME.parents[1]

#: Platform content a game reads but does not own.
LEXICON_SEEDS = REPO / "content" / "seeds"
PLATFORM_TOOLS = REPO / "tools"


def has_repository() -> bool:
    """Whether the platform's checkout is reachable from here."""
    return (REPO / "pyproject.toml").is_file() and LEXICON_SEEDS.is_dir()


__all__ = [
    "CONTENT",
    "GAME",
    "LEXICON_SEEDS",
    "OVERLAY_SEED",
    "PLATFORM_TOOLS",
    "REPO",
    "TOOLS",
    "has_repository",
]
