"""Independently advancing version numbers.

Every component that can change the bytes of a published puzzle carries its own
version, and every manifest records all of them. Without this a regenerated
puzzle that differs from its original is indistinguishable from a bug.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The engine's own release. Bumped when generation, verification, difficulty
#: measurement or scoring behaviour changes in a way that can alter output.
ENGINE_VERSION = "0.1.0"

#: Shape of the knowledge graph records. Bumped on any incompatible change to
#: the models in ``puzzlegen.graph.models``; snapshots record it so an old
#: snapshot can be rejected rather than silently misread.
CONTENT_SCHEMA_VERSION = 1

#: Shape of a published puzzle document.
PUZZLE_FORMAT_VERSION = 1

#: Shape of a share artifact. Split from the puzzle format because share text
#: is pasted into third-party surfaces and changes on a different cadence.
SHARE_FORMAT_VERSION = 1

#: Wire protocol spoken between the engine and a sandboxed game plugin.
#: Plugins declare the protocol version they implement; the engine refuses to
#: load a plugin whose major version differs.
PLUGIN_PROTOCOL_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class SemVer:
    """A minimal semantic version, sufficient for plugin compatibility checks.

    A full semver library is avoided: prerelease and build metadata have no
    meaning at this boundary, and accepting them would imply a comparison
    order the engine does not actually honour.
    """

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> "SemVer":
        parts = text.strip().split(".")
        if len(parts) != 3:
            raise ValueError(f"not a MAJOR.MINOR.PATCH version: {text!r}")
        try:
            major, minor, patch = (int(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"not a MAJOR.MINOR.PATCH version: {text!r}") from exc
        if major < 0 or minor < 0 or patch < 0:
            raise ValueError(f"version components must be non-negative: {text!r}")
        return cls(major, minor, patch)

    def is_compatible_with(self, other: "SemVer") -> bool:
        """Major version must match exactly; minor may be newer on either side.

        Deliberately symmetric in minor: the engine may be newer than a plugin
        or the reverse, and both are tolerable within a major version because
        the protocol only ever adds optional fields within a major.
        """
        return self.major == other.major

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"
