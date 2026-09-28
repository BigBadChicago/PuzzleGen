"""Internal identifiers.

Provider-native identifiers (a Wikidata QID, a WordNet synset key) are recorded
in provenance and never used as primary keys. If they were, replacing a
provider would rewrite every id in the store and invalidate every published
manifest, which is precisely the coupling the architecture forbids.

Ids are derived deterministically from a natural key so that rebuilding a
snapshot from the same inputs produces the same ids. Where the natural key is
already a clean slug it is used verbatim, because debuggable ids are worth
more than uniform ones and both forms are equally stable.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from .hashing import short_hash

ENTITY: Final = "entity"
FACT: Final = "fact"
RELATIONSHIP: Final = "rel"
CATEGORY: Final = "category"
SOURCE: Final = "source"
SNAPSHOT: Final = "snapshot"
PUZZLE: Final = "puzzle"
MANIFEST: Final = "manifest"
SESSION: Final = "session"
DEPENDENCY: Final = "dep"
REVIEW: Final = "review"
GENERATION: Final = "gen"
PLAYER: Final = "player"
KEY: Final = "key"
LINK: Final = "link"
SCORE: Final = "score"
STREAK: Final = "streak"
SHARE: Final = "share"

_KINDS: Final = frozenset(
    {
        ENTITY,
        FACT,
        RELATIONSHIP,
        CATEGORY,
        SOURCE,
        SNAPSHOT,
        PUZZLE,
        MANIFEST,
        SESSION,
        DEPENDENCY,
        REVIEW,
        GENERATION,
        PLAYER,
        KEY,
        LINK,
        SCORE,
        STREAK,
        SHARE,
    }
)

_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ID_OK = re.compile(r"^[a-z]+:[a-z0-9][a-z0-9_.-]{0,95}$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase ASCII slug. Returns "" when nothing usable survives."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SLUG_STRIP.sub("-", folded.lower()).strip("-")


def mint(kind: str, *natural_key: str) -> str:
    """Derive a stable id of ``kind`` from its natural key parts.

    The natural key must be the full set of fields that make the record
    distinct. Passing a partial key silently collides two records, so callers
    use the ``for_*`` helpers below rather than calling this directly.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown id kind: {kind!r}")
    if not natural_key or any(part == "" for part in natural_key):
        raise ValueError("natural key parts must be non-empty")

    if len(natural_key) == 1:
        slug = slugify(natural_key[0])
        if _SLUG_OK.match(slug):
            return f"{kind}:{slug}"

    return f"{kind}:{short_hash(list(natural_key), 20)}"


def is_id(value: str, kind: str | None = None) -> bool:
    if not _ID_OK.match(value):
        return False
    return kind is None or value.startswith(f"{kind}:")


def kind_of(value: str) -> str:
    if not _ID_OK.match(value):
        raise ValueError(f"not an identifier: {value!r}")
    return value.split(":", 1)[0]


def require(value: str, kind: str) -> str:
    """Validate and return an id, for use in model validators."""
    if not is_id(value, kind):
        raise ValueError(f"expected a {kind} id, got {value!r}")
    return value


def for_entity(canonical_name: str, lang: str) -> str:
    return mint(ENTITY, f"{canonical_name}|{lang}") if lang != "en" else mint(ENTITY, canonical_name)


def for_category(
    canonical_name: str, lang: str = "en", taxonomy: str = "default"
) -> str:
    """Taxonomy is part of the key: a WordNet synset named "feline" and a
    curated category named "feline" are different nodes with different
    parents, and collapsing them would merge two unrelated hierarchies."""
    if taxonomy == "default" and lang == "en":
        return mint(CATEGORY, canonical_name)
    return mint(CATEGORY, taxonomy, canonical_name, lang)


def for_source(name: str, version: str) -> str:
    return mint(SOURCE, name, version)


def for_fact(subject_id: str, predicate: str, value_repr: str) -> str:
    """Facts are keyed by subject, predicate and value.

    Including the value means a corrected value yields a new fact id rather
    than mutating an existing one in place. Dependency tracking then shows
    precisely which puzzles used the superseded assertion.
    """
    return mint(FACT, subject_id, predicate, value_repr)


def for_relationship(subject_id: str, predicate: str, object_id: str) -> str:
    return mint(RELATIONSHIP, subject_id, predicate, object_id)


def for_snapshot(label: str) -> str:
    return mint(SNAPSHOT, label)


def for_generation(day_key: str, game_id: str, attempt: int) -> str:
    return mint(GENERATION, day_key, game_id, str(attempt))


def for_puzzle(day_key: str, game_id: str, content_hash: str) -> str:
    return mint(PUZZLE, day_key, game_id, content_hash)


def for_manifest(puzzle_id: str) -> str:
    return mint(MANIFEST, puzzle_id)


def for_dependency(manifest_id: str, ref_kind: str, ref_id: str) -> str:
    return mint(DEPENDENCY, manifest_id, ref_kind, ref_id)


def for_review(subject_ref: str, opened_at: str) -> str:
    return mint(REVIEW, subject_ref, opened_at)


def for_player(mint_token: str) -> str:
    """Derive a player id from a freshly minted random token.

    Two parts are always passed so ``mint`` takes its hashing branch: a token
    that happened to slugify cleanly would otherwise become a readable id
    carrying the token's own characters, and the token is secret material.
    """
    return mint(PLAYER, "player", mint_token)


def for_return_key(fingerprint: str) -> str:
    return mint(KEY, "key", fingerprint)


def for_identity_link(provider: str, subject: str) -> str:
    """One id per provider subject, which is what makes double linking a
    primary-key conflict rather than something a query has to notice."""
    return mint(LINK, provider, subject)


def for_session(player_id: str, manifest_id: str, kind: str) -> str:
    return mint(SESSION, player_id, manifest_id, kind)


def for_score(session_id: str) -> str:
    return mint(SCORE, session_id)


def for_streak(player_id: str, game_id: str) -> str:
    return mint(STREAK, player_id, game_id)


def for_share(session_id: str, share_format_version: int) -> str:
    return mint(SHARE, session_id, str(share_format_version))
