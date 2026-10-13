"""The release check: did this day actually regenerate?

A manifest records the recipe for rebuilding a puzzle, and
``is_reproducible_from`` compares two recipes. Neither is evidence. The
manifest's reproducibility claim is only as good as the last time somebody
rebuilt the puzzle from it, and nothing in the publish path does: generation
assembles a board, verification proves it, and publication writes down the
evaluation the pipeline already accepted -- which is right, because
re-verifying at publication time would open a window in which the puzzle
verified once and shipped on the strength of a second, different run.

So the honest version of "reproducible" is not a field. It is a check that
ran, with a result recorded: this module regenerates from the manifest's own
``inputs`` and compares the content hash.

The pipeline runs it as a gate, one moment before the manifest exists: a day
that will not rebuild from its own recipe is refused rather than published
with a reproducibility claim it is the one day unable to honour. That costs
one extra generation per published day, which is what a check that actually
ran costs.

The regeneration itself is a callable the caller supplies. The check must not
own a pipeline, a snapshot or a registry: doing so would make it a second
generation path, and a second generation path is the thing whose disagreement
with the first this check exists to detect.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from .records import GenerationInputs, PuzzleManifest, PuzzleRecord

#: Rebuilds one puzzle from a recipe. Raising is a legitimate outcome: a
#: snapshot that no longer exists is a failed check, not a crash.
Regenerator = Callable[[GenerationInputs], PuzzleRecord]


@dataclass(frozen=True, slots=True)
class ReleaseCertificate:
    """What a regeneration attempt established about one published day.

    Carries the two hashes rather than a bare verdict, because the useful
    question after a disagreement is *how* the rebuild differed, and a caller
    holding only ``reproduced`` has to run the check again to find out.
    """

    manifest_id: str
    day_key: str
    game_id: str
    reproduced: bool
    published_content_hash: str
    regenerated_content_hash: str = ""
    #: Why the check could not complete, if it could not. Distinct from a
    #: disagreement: a missing snapshot and a changed puzzle are different
    #: findings and must not share a field.
    error: str = ""
    checked_at: dt.datetime | None = None

    @property
    def conclusive(self) -> bool:
        """Whether the check reached a verdict at all."""
        return not self.error

    @property
    def refuses_publication(self) -> bool:
        """Whether a gating caller should hold the day.

        An inconclusive check refuses too. A day that could not be rebuilt is
        a day whose reproducibility claim is unproven, and publishing it on
        the grounds that the check errored would make the gate decorative.
        """
        return not (self.reproduced and self.conclusive)

    def describe(self) -> str:
        if self.error:
            return (
                f"{self.day_key} {self.game_id}: regeneration could not be "
                f"completed ({self.error})"
            )
        if self.reproduced:
            return f"{self.day_key} {self.game_id}: regenerated identically"
        return (
            f"{self.day_key} {self.game_id}: regeneration disagreed; published "
            f"{self.published_content_hash[:12]}, rebuilt "
            f"{self.regenerated_content_hash[:12]}"
        )


def check_release(
    manifest: PuzzleManifest,
    regenerate: Regenerator,
    *,
    checked_at: dt.datetime | None = None,
) -> ReleaseCertificate:
    """Rebuild the puzzle this manifest published and compare it."""
    return check_rebuild(
        manifest.inputs,
        manifest.puzzle_content_hash,
        regenerate,
        manifest_id=manifest.id,
        checked_at=checked_at,
    )


def check_rebuild(
    inputs: GenerationInputs,
    published_content_hash: str,
    regenerate: Regenerator,
    *,
    manifest_id: str = "",
    checked_at: dt.datetime | None = None,
) -> ReleaseCertificate:
    """Rebuild from a recipe and compare the result against a known hash.

    The primitive, taking the recipe rather than the manifest, because the
    check is most useful one moment *before* there is a manifest: a day that
    will not rebuild should not be published, and a gate that could only run
    on a published manifest would be reporting on a day already served.
    ``manifest_id`` is then empty, which is honest -- there is not one yet.

    Compares the content hash rather than the records, because a puzzle's
    content hash is defined over exactly the fields that make it the same
    puzzle: payload, solution and dependency refs. A record comparison would
    fail on the created-at timestamp of a rebuild, which is a difference that
    means nothing.
    """
    base = {
        "manifest_id": manifest_id,
        "day_key": inputs.day_key,
        "game_id": inputs.game_id,
        "published_content_hash": published_content_hash,
        "checked_at": checked_at,
    }
    try:
        rebuilt = regenerate(inputs)
    except Exception as exc:  # noqa: BLE001 - any failure is one finding
        return ReleaseCertificate(
            **base,
            reproduced=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    rebuilt_hash = rebuilt.content_hash()
    return ReleaseCertificate(
        **base,
        reproduced=rebuilt_hash == published_content_hash,
        regenerated_content_hash=rebuilt_hash,
    )


__all__ = [
    "Regenerator",
    "ReleaseCertificate",
    "check_rebuild",
    "check_release",
]
