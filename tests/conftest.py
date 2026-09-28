"""Fixtures shared by every test module.

The store fixture is parametrized over both backends. Every storage test
therefore runs twice, which is the mechanism that keeps the in-memory backend
from drifting away from SQLite.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

# ``tests`` on the path so test modules can import the shared builders in this
# file and the fixture games beside it without a package install.
sys.path.insert(0, str(Path(__file__).parent))

from puzzlegen.core import ids
from puzzlegen.core.types import (
    FreshnessClass,
    FrequencyBand,
    ProvenanceClass,
    ReviewStatus,
    SourceKind,
)
from puzzlegen.graph.memory_store import InMemoryDocumentStore
from puzzlegen.graph.models import (
    Category,
    Entity,
    Fact,
    FactValue,
    Provenance,
    Relationship,
    Source,
)
from puzzlegen.graph.repositories import GraphRepositories
from puzzlegen.graph.sqlite_store import SqliteDocumentStore

NOW = dt.datetime(2026, 9, 26, 12, 0, 0, tzinfo=dt.timezone.utc)
LATER = NOW + dt.timedelta(days=365)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        backend = InMemoryDocumentStore()
    else:
        backend = SqliteDocumentStore(tmp_path / "graph.sqlite3")
    yield backend
    backend.close()


@pytest.fixture
def repos(store) -> GraphRepositories:
    return GraphRepositories(store)


@pytest.fixture
def curated_source() -> Source:
    return Source(
        id=ids.for_source("curated", "2026.09"),
        name="curated",
        kind=SourceKind.CURATED_INTERNAL,
        version="2026.09",
        retrieved_at=NOW,
    )


def sourced_provenance(source: Source, confidence: float = 0.95) -> Provenance:
    return Provenance(
        source_id=source.id,
        provenance_class=ProvenanceClass.SOURCED,
        source_ref="curated/tiger",
        retrieval_date=NOW,
        verification_date=NOW,
        verification_method="curator import",
        confidence=confidence,
    )


def make_entity(
    name: str,
    source: Source,
    *,
    status: ReviewStatus = ReviewStatus.ACTIVE,
    band: FrequencyBand = FrequencyBand.COMMON,
    aliases: tuple[str, ...] = (),
    freshness: FreshnessClass = FreshnessClass.STATIC,
) -> Entity:
    return Entity(
        id=ids.for_entity(name, "en"),
        canonical_name=name,
        aliases=aliases,
        status=status,
        freshness_class=freshness,
        frequency_band=band,
        confidence=0.95,
        provenance=(sourced_provenance(source),),
        created_at=NOW,
        updated_at=NOW,
        verified_at=NOW,
        next_review_at=LATER,
    )


def make_category(
    name: str,
    source: Source,
    *,
    parent: Category | None = None,
    parents: tuple[Category, ...] = (),
) -> Category:
    chosen = parents or ((parent,) if parent else ())
    return Category.build(
        canonical_name=name,
        parents=chosen,
        created_at=NOW,
        status=ReviewStatus.ACTIVE,
        confidence=0.95,
        provenance=(sourced_provenance(source),),
    )


def make_fact(
    entity: Entity,
    predicate: str,
    value,
    source: Source,
    *,
    freshness: FreshnessClass = FreshnessClass.STATIC,
    status: ReviewStatus = ReviewStatus.ACTIVE,
) -> Fact:
    non_static = freshness is not FreshnessClass.STATIC
    return Fact.build(
        subject_id=entity.id,
        predicate=predicate,
        value=FactValue.of(value),
        created_at=NOW,
        status=status,
        freshness_class=freshness,
        confidence=0.9,
        provenance=(sourced_provenance(source),),
        verified_at=NOW if non_static else None,
        next_review_at=LATER if non_static else None,
    )


def make_relationship(
    subject: Entity,
    predicate: str,
    obj,
    source: Source,
) -> Relationship:
    return Relationship.build(
        subject_id=subject.id,
        predicate=predicate,
        object_id=obj.id,
        created_at=NOW,
        status=ReviewStatus.ACTIVE,
        confidence=0.9,
        provenance=(sourced_provenance(source),),
    )


# -- session-layer fixtures --------------------------------------------------
#
# The clock is mutable and injected everywhere, because every assertion about a
# session is about ordering or elapsed time and a test that could not control
# time would either be slow or be a guess.

from puzzlegen.core import ids as _ids  # noqa: E402
from puzzlegen.core.types import DifficultyBand, UniquenessContract  # noqa: E402
from puzzlegen.core.types import VerificationCompleteness  # noqa: E402
from puzzlegen.core.versions import PLUGIN_PROTOCOL_VERSION  # noqa: E402
from puzzlegen.engine.identity import (  # noqa: E402
    IdentityService,
    SessionSecrets,
)
from puzzlegen.engine.records import (  # noqa: E402
    DifficultySummary,
    GenerationInputs,
    PuzzleManifest,
    PuzzleRecord,
    PuzzleStatus,
    VerificationSummary,
    VersionSet,
)
from puzzlegen.engine.registry import GameRegistry  # noqa: E402
from puzzlegen.engine.scoring import ScoringService  # noqa: E402
from puzzlegen.engine.session_service import SessionService  # noqa: E402
from puzzlegen.engine.session_storage import SessionRepositories  # noqa: E402
from puzzlegen.engine.sessions import UtcDayWindow  # noqa: E402
from puzzlegen.engine.sharing import ShareService  # noqa: E402
from puzzlegen.engine.storage import EngineRepositories  # noqa: E402

DAY = "2026-09-27"
PLAY_START = dt.datetime(2026, 9, 27, 12, 0, 0, tzinfo=dt.timezone.utc)


class MutableClock:
    """A clock a test moves by hand."""

    def __init__(self, start: dt.datetime = PLAY_START) -> None:
        self.instant = start

    def now(self) -> dt.datetime:
        return self.instant

    def advance(self, **kwargs) -> dt.datetime:
        self.instant += dt.timedelta(**kwargs)
        return self.instant


class SequenceSecretSource:
    """Deterministic credential material, for tests only.

    Never used outside tests: the production source is unpredictable by
    design, and an invariant test keeps the engine from importing anything
    reproducible into the identity module.
    """

    def __init__(self, prefix: str = "tok") -> None:
        self._prefix = prefix
        self._next = 0

    def token(self, nbytes: int = 32) -> str:
        self._next += 1
        return f"{self._prefix}-{self._next:04d}-{'x' * 20}"


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def day_window() -> UtcDayWindow:
    return UtcDayWindow()


@pytest.fixture
def session_repos(store) -> SessionRepositories:
    return SessionRepositories(store)


@pytest.fixture
def engine_repos(store) -> EngineRepositories:
    return EngineRepositories(store)


@pytest.fixture
def secrets_config() -> SessionSecrets:
    return SessionSecrets(b"pepper-for-tests" * 2)


@pytest.fixture
def identity(session_repos, secrets_config, clock) -> IdentityService:
    return IdentityService(
        session_repos,
        secrets_config=secrets_config,
        clock=clock,
        secret_source=SequenceSecretSource(),
    )


@pytest.fixture
def scoring(session_repos, clock) -> ScoringService:
    return ScoringService(session_repos, clock=clock)


@pytest.fixture
def game():
    from support.games import OddOneOutGame

    return OddOneOutGame()


@pytest.fixture
def registry(game) -> GameRegistry:
    registry = GameRegistry()
    registry.register(game)
    registry.activate(game.describe().game_id, day_key=DAY)
    return registry


def build_published_day(
    engine_repos: EngineRepositories,
    *,
    game_id: str = "oddoneout",
    day_key: str = DAY,
    created_at: dt.datetime = PLAY_START,
) -> PuzzleManifest:
    """A published puzzle and manifest, ready to be played.

    Built directly rather than through the generation pipeline: these tests
    are about what happens after publication, and routing every one of them
    through generation would make a session test fail for a generation reason.
    """
    inputs = GenerationInputs(
        day_key=day_key,
        game_id=game_id,
        seed_hex="ab" * 16,
        snapshot_id=_ids.for_snapshot("test-snapshot"),
        snapshot_content_hash="snapshot-hash",
        difficulty_target=DifficultyBand.MEDIUM,
    )
    versions = VersionSet(game="1.0.0", protocol=PLUGIN_PROTOCOL_VERSION)
    puzzle = PuzzleRecord(
        id=_ids.for_puzzle(day_key, game_id, "content-hash"),
        game_id=game_id,
        day_key=day_key,
        status=PuzzleStatus.PUBLISHED,
        payload={
            "options": ["opt_a", "opt_b", "opt_c", "opt_d"],
            "labels": {
                "opt_a": "tiger",
                "opt_b": "lion",
                "opt_c": "puma",
                "opt_d": "eagle",
            },
            "prompt": "Three share a category",
        },
        solution={"answer": "opt_d"},
        presentation={"optimal_attempts": 1},
        fact_refs=("fact:one", "fact:two"),
        inputs=inputs,
        versions=versions,
        created_at=created_at,
    )
    engine_repos.puzzles.put(puzzle)
    manifest = PuzzleManifest(
        id=_ids.for_manifest(puzzle.id),
        puzzle_id=puzzle.id,
        game_id=game_id,
        day_key=day_key,
        generation_id=_ids.for_generation(day_key, game_id, 1),
        inputs=inputs,
        versions=versions,
        verification=VerificationSummary(
            solvable=True,
            solution_count=1,
            completeness=VerificationCompleteness.COMPLETE,
            uniqueness_contract=UniquenessContract.EXACTLY_ONE,
            contract_satisfied=True,
        ),
        difficulty=DifficultySummary(
            target=DifficultyBand.MEDIUM,
            measured_band=DifficultyBand.MEDIUM,
            score=0.4,
            confidence=0.8,
        ),
        fact_refs=puzzle.fact_refs,
        puzzle_content_hash=puzzle.content_hash(),
        published_at=created_at,
    )
    engine_repos.manifests.put(manifest)
    return manifest


@pytest.fixture
def published(engine_repos) -> PuzzleManifest:
    return build_published_day(engine_repos)


@pytest.fixture
def sessions(
    session_repos, engine_repos, registry, clock, day_window, scoring, identity
) -> SessionService:
    return SessionService(
        session_repos,
        engine_repos,
        registry,
        clock=clock,
        day_window=day_window,
        scoring=scoring,
        identity=identity,
    )


@pytest.fixture
def share_service() -> ShareService:
    return ShareService()


@pytest.fixture
def player(identity):
    return identity.create_player().player
