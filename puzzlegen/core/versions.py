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
#:
#: 2 added the dependency commitment to a manifest: ``dependency_root``,
#: ``dependency_salt_id`` and the unset ``seal``. A v1 manifest committed to
#: nothing publishable about its dependencies, so it is not readable as a v2
#: document and is rejected rather than defaulted.
#: 3 added composite puzzles and multi-axis difficulty: a record carries its
#: ``parts``, a verification summary carries one entry per part, and a
#: difficulty summary carries the axes the score was reduced from. A v2
#: document has no parts and no axes, which is not the same fact as a v3
#: document recording none, so it is rejected rather than defaulted.
PUZZLE_FORMAT_VERSION = 3

#: Rules that turn a move ledger into a grade. Bumped when telemetry
#: derivation, the scoring contract or a shipped game's scoring function
#: changes in a way that can alter the number a finished session produces.
#:
#: Separate from :data:`ENGINE_VERSION` because grading can change without
#: generation changing, and a transcript recorded under one grading rule must
#: not be silently graded under another.
SCORING_VERSION = "1.0.0"

#: Shape of a session document, including its inline move ledger. Recorded on
#: the session so a replay can refuse a transcript whose ledger it would have
#: to reinterpret rather than read.
SESSION_SCHEMA_VERSION = 1

#: Shape of a share artifact. Split from the puzzle format because share text
#: is pasted into third-party surfaces and changes on a different cadence.
SHARE_FORMAT_VERSION = 1

#: Wire protocol spoken between the engine and a sandboxed game plugin.
#: Plugins declare the protocol version they implement; the engine refuses to
#: load a plugin whose major version differs.
#:
#: 1.1.0 added ``grade_move`` and ``get_hint``. Both are additive within the
#: major version, so a 1.0 plugin still loads and still generates; what it
#: cannot do is have a session started against it, which the session service
#: reports by name rather than by an attribute error mid-play.
#:
#: 1.2.0 added ``witness``, and added it as a declared capability rather than
#: as a required method: a game announces it in
#: ``GameDescriptor.supplies_witness`` and the engine calls it only where that
#: is set. Additive again, so every 1.1 game loads unchanged. The declaration
#: is what makes the absence of a witness readable -- a game that cannot emit
#: its own deduction says so, instead of the engine discovering it by calling
#: a method that is not there.
#: 1.3.0 raised the complexity ceiling, and did it by adding three declared
#: capabilities rather than by adding required methods:
#:
#: * ``content_rounds`` above 1, with ``refine_content_requirements``. A game
#:   may ask a second question that depends on what the first round returned,
#:   which is what a chain, a path or a lattice needs and what one blind round
#:   cannot express. The game still never holds the port: every round is
#:   satisfied by the engine, through the same gates and the same budget.
#: * ``staged``, with ``verify_part``. A puzzle may carry ``parts``, each with
#:   its own answer, its own uniqueness contract and the parts it depends on.
#:   The engine verifies each part twice, so "this stage is unlocked by that
#:   one" becomes a checked claim instead of a comment.
#: * ``difficulty_reduction`` above ``NONE``, with
#:   ``DifficultyMeasurement.axes``. A board with several kinds of work
#:   reports them separately and declares how they reduce to the one number
#:   its bands are read against.
#:
#: Additive, so every 1.2 game loads and runs unchanged: a game declaring none
#: of the three is a single-part, single-round, single-axis game, which is
#: exactly what it was before.
PLUGIN_PROTOCOL_VERSION = "1.3.0"


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
