"""Architectural invariants, enforced mechanically.

The design document says games must never reach the graph. A comment saying so
is not enforcement. These tests parse every module and fail the build when a
forbidden import appears, so the boundary cannot erode through an ordinary
refactor by someone who has not read the design document.

Two separate mechanisms enforce the same rule. This test covers in-tree games;
third-party games run as sandboxed subprocesses that cannot import the package
at all. Neither mechanism is sufficient alone: the sandbox does not constrain
in-tree code, and this test does not constrain code that never enters the
repository.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "puzzlegen"

#: Modules a game plugin may never import. Storage and providers would let it
#: read the graph directly; the rest would let it reach the world outside the
#: capability it was granted.
FORBIDDEN_FOR_GAMES = {
    "puzzlegen.graph",
    "puzzlegen.providers",
    "puzzlegen.content.service",
    "puzzlegen.content.snapshots",
    "puzzlegen.content.normalizer",
    "puzzlegen.content.policy",
    "puzzlegen.content.review",
    "puzzlegen.ops",
    "sqlite3",
    "requests",
    "httpx",
    "urllib",
    "urllib3",
    "socket",
    "subprocess",
    "shutil",
    "wn",
    "wordfreq",
    "sentence_transformers",
    "pathlib",
    "os",
    "sys",
    "importlib",
    "ctypes",
    "pickle",
}

#: What a game may import: its own capability types and the shared vocabulary.
ALLOWED_FOR_GAMES = {
    "puzzlegen.content.port",
    "puzzlegen.content.query",
    "puzzlegen.core",
    "puzzlegen.engine",
    "puzzlegen.games",
}

#: Layer order. A module may import from its own layer and any layer below it,
#: never above. This is what keeps the dependency direction from inverting.
LAYERS = ["core", "graph", "providers", "content", "engine", "games", "ops", "cli"]


def python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def imported_modules(path: pathlib.Path) -> set[str]:
    """Every module name a file imports, including relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    try:
        package_parts = path.relative_to(ROOT).with_suffix("").parts
    except ValueError:
        # A file outside the repository has no package context, so relative
        # imports in it cannot be resolved. Only the checker's own tests do
        # this, and they plant absolute imports.
        package_parts = ()
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package_parts[: len(package_parts) - node.level])
                module = ".".join([*base, node.module]) if node.module else ".".join(base)
            else:
                module = node.module or ""
            if module:
                found.add(module)
    return found


def is_forbidden(module: str, forbidden: set[str]) -> str | None:
    for banned in forbidden:
        if module == banned or module.startswith(banned + "."):
            return banned
    return None


def layer_of(path: pathlib.Path) -> str | None:
    parts = path.relative_to(PACKAGE).parts
    return parts[0] if len(parts) > 1 else None


class TestPluginBoundary:
    """Games are lenses. They may not reach the knowledge they look through."""

    def test_the_games_package_exists(self):
        assert (PACKAGE / "games").is_dir(), (
            "the games package must exist for the boundary test to be meaningful; "
            "an absent directory would make this suite vacuously pass"
        )

    def test_no_game_imports_storage_providers_or_the_outside_world(self):
        violations: list[str] = []
        for path in python_files(PACKAGE / "games"):
            for module in imported_modules(path):
                banned = is_forbidden(module, FORBIDDEN_FOR_GAMES)
                if banned:
                    violations.append(
                        f"{path.relative_to(ROOT)} imports {module!r} "
                        f"(forbidden: {banned})"
                    )
        assert not violations, "game plugins must reach content only through a port:\n" + "\n".join(
            violations
        )

    def test_games_import_only_from_the_allowed_surface(self):
        violations: list[str] = []
        for path in python_files(PACKAGE / "games"):
            for module in imported_modules(path):
                if not module.startswith("puzzlegen"):
                    continue
                if not any(
                    module == allowed or module.startswith(allowed + ".")
                    for allowed in ALLOWED_FOR_GAMES
                ):
                    violations.append(f"{path.relative_to(ROOT)} imports {module!r}")
        assert not violations, (
            "a game may import only its capability types and shared vocabulary:\n"
            + "\n".join(violations)
        )

    def test_no_game_opens_a_file_or_a_socket(self):
        """Even without an import, a builtin call would breach the sandbox."""
        banned_calls = {"open", "eval", "exec", "compile", "__import__"}
        violations: list[str] = []
        for path in python_files(PACKAGE / "games"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in banned_calls:
                        violations.append(
                            f"{path.relative_to(ROOT)} calls {node.func.id}()"
                        )
        assert not violations, "game plugins must not touch the host:\n" + "\n".join(
            violations
        )


class TestContentPortSurface:
    """The port is the whole capability, so its surface is part of the design."""

    def test_the_port_exposes_no_repository_or_store(self):
        from puzzlegen.content.port import ContentPort

        public = {n for n in dir(ContentPort) if not n.startswith("_")}
        leaked = public & {"repos", "store", "service", "session", "connection"}
        assert not leaked, f"ContentPort leaks {sorted(leaked)}"

    def test_every_public_port_method_is_a_declared_operation(self):
        from puzzlegen.content.port import ContentPort
        from puzzlegen.content.query import Operation

        plumbing = {
            "game_id",
            "usage",
            "issued_ids",
            "verify_references",
            "request",
            "satisfy",
        }
        operations = {str(op) for op in Operation}
        public = {n for n in dir(ContentPort) if not n.startswith("_")}
        unexpected = public - plumbing - operations
        assert not unexpected, (
            "the port must expose only semantic operations and its own plumbing; "
            f"found {sorted(unexpected)}"
        )

    def test_entity_views_carry_no_provenance(self):
        from puzzlegen.content.query import EntityView

        fields = set(EntityView.__dataclass_fields__)
        leaked = fields & {
            "provenance",
            "source_id",
            "source_ref",
            "confidence",
            "status",
            "provider",
        }
        assert not leaked, f"EntityView leaks {sorted(leaked)}"


class TestLayerDirection:
    """Dependencies point downward. An upward import inverts the architecture."""

    def test_no_layer_imports_from_a_layer_above_it(self):
        violations: list[str] = []
        for path in python_files(PACKAGE):
            layer = layer_of(path)
            if layer is None or layer not in LAYERS:
                continue
            ceiling = LAYERS.index(layer)
            for module in imported_modules(path):
                if not module.startswith("puzzlegen."):
                    continue
                parts = module.split(".")
                if len(parts) < 2 or parts[1] not in LAYERS:
                    continue
                if LAYERS.index(parts[1]) > ceiling:
                    violations.append(
                        f"{path.relative_to(ROOT)} ({layer}) imports {module!r}"
                    )
        assert not violations, "dependencies must point downward:\n" + "\n".join(
            violations
        )

    def test_core_depends_on_nothing_else_in_the_package(self):
        violations: list[str] = []
        for path in python_files(PACKAGE / "core"):
            for module in imported_modules(path):
                if module.startswith("puzzlegen.") and ".core" not in module:
                    violations.append(f"{path.relative_to(ROOT)} imports {module!r}")
        assert not violations, "core must stay dependency-free:\n" + "\n".join(
            violations
        )

    def test_no_layer_above_content_holds_a_graph_repository(self):
        """Governed content reaches the system only through the content service.

        The engine does own storage: puzzles, manifests, traces and sessions
        are its records, and it holds repositories for them. What it must
        never hold is a repository over *graph* records, because every gate in
        the content service sits between those records and a puzzle. The rule
        is therefore about which repositories, not about repositories at all.
        """
        forbidden_names = {
            "GraphRepositories",
            "EntityRepository",
            "FactRepository",
            "RelationshipRepository",
            "CategoryRepository",
            "SourceRepository",
            "EmbeddingRepository",
            "FrequencyRepository",
        }
        violations: list[str] = []
        for layer in ("engine", "games"):
            directory = PACKAGE / layer
            if not directory.is_dir():
                continue
            for path in python_files(directory):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if "graph.repositories" not in node.module:
                            continue
                        for alias in node.names:
                            if alias.name in forbidden_names:
                                violations.append(
                                    f"{path.relative_to(ROOT)} imports {alias.name}"
                                )
        assert not violations, (
            "graph repositories belong to the content layer:\n" + "\n".join(violations)
        )

    def test_games_hold_no_repository_of_any_kind(self):
        """A game owns no storage at all, not even its own."""
        violations: list[str] = []
        for path in python_files(PACKAGE / "games"):
            for module in imported_modules(path):
                if "repositories" in module or "store" in module:
                    violations.append(f"{path.relative_to(ROOT)} imports {module!r}")
        assert not violations, "games must own no storage:\n" + "\n".join(violations)


class TestProviderIsolation:
    """Heavy or replaceable dependencies stay out of the engine."""

    @pytest.mark.parametrize(
        "package", ["wn", "wordfreq", "sentence_transformers", "torch", "numpy"]
    )
    def test_the_engine_never_imports_a_snapshot_build_dependency(self, package):
        violations: list[str] = []
        for path in python_files(PACKAGE):
            for module in imported_modules(path):
                if module == package or module.startswith(package + "."):
                    violations.append(f"{path.relative_to(ROOT)} imports {module!r}")
        assert not violations, (
            f"{package} belongs in tools/, not the engine:\n" + "\n".join(violations)
        )

    def test_no_module_outside_providers_reads_a_provider_file_format(self):
        """Provider file parsing lives in providers, so formats stay swappable."""
        violations: list[str] = []
        for path in python_files(PACKAGE):
            if path.relative_to(PACKAGE).parts[0] in ("providers", "cli"):
                continue
            source = path.read_text(encoding="utf-8")
            if "lexicon_schema" in source or "curated_schema" in source:
                violations.append(str(path.relative_to(ROOT)))
        assert not violations, (
            "provider formats must not leak out of the providers package:\n"
            + "\n".join(violations)
        )


class TestTheCheckerItself:
    """A boundary test that cannot fail is worse than no boundary test.

    These plant known violations and assert the analysis catches them, so the
    suite above is proved non-vacuous rather than assumed to be.
    """

    def test_a_planted_storage_import_is_detected(self, tmp_path):
        planted = tmp_path / "rogue_game.py"
        planted.write_text(
            "from puzzlegen.graph.repositories import GraphRepositories\n"
            "import sqlite3\n",
            encoding="utf-8",
        )
        modules = imported_modules(planted)
        caught = [m for m in modules if is_forbidden(m, FORBIDDEN_FOR_GAMES)]
        assert set(caught) == {"puzzlegen.graph.repositories", "sqlite3"}

    def test_a_planted_relative_import_is_resolved(self):
        """Relative imports must resolve, or a game could evade the check."""
        path = PACKAGE / "content" / "port.py"
        modules = imported_modules(path)
        assert "puzzlegen.content.query" in modules
        assert "puzzlegen.core.errors" in modules

    def test_a_planted_builtin_call_is_detected(self, tmp_path):
        planted = tmp_path / "rogue_game.py"
        planted.write_text("data = open('/etc/passwd').read()\n", encoding="utf-8")
        tree = ast.parse(planted.read_text(encoding="utf-8"))
        calls = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        assert "open" in calls

    def test_a_planted_upward_import_would_be_detected(self, tmp_path):
        planted = tmp_path / "rogue_core.py"
        planted.write_text("from puzzlegen.engine import pipeline\n", encoding="utf-8")
        modules = imported_modules(planted)
        parts = next(iter(modules)).split(".")
        assert parts[1] == "engine" and LAYERS.index("engine") > LAYERS.index("core")


#: Modules that make up the session layer. Each has a rule above and beyond
#: the layer direction, and each rule protects something that fails silently.
SESSION_MODULES = (
    "sessions.py",
    "session_storage.py",
    "session_service.py",
    "scoring.py",
    "sharing.py",
    "accessibility.py",
    "identity.py",
)

#: Names that would mean a credential crossed into the engine. The design says
#: OAuth verification happens in front of the engine; this is what keeps that
#: from being a sentence in a document only.
CREDENTIAL_NAMES = (
    "access_token",
    "id_token",
    "refresh_token",
    "client_secret",
    "authorization_code",
    "password",
)


def session_module_paths() -> list[pathlib.Path]:
    return [PACKAGE / "engine" / name for name in SESSION_MODULES]


def attribute_calls(path: pathlib.Path) -> set[str]:
    """Every ``a.b()`` call in a file, as a dotted string."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            parts = [node.func.attr]
            inner = node.func.value
            while isinstance(inner, ast.Attribute):
                parts.append(inner.attr)
                inner = inner.value
            if isinstance(inner, ast.Name):
                parts.append(inner.id)
            found.add(".".join(reversed(parts)))
    return found


def clock_calls_inside(path: pathlib.Path, class_name: str) -> set[str]:
    """Attribute calls made within one class body, so a single sanctioned
    reader of the real clock can be exempted by name rather than by file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            found: set[str] = set()
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    parts = [inner.func.attr]
                    target = inner.func.value
                    while isinstance(target, ast.Attribute):
                        parts.append(target.attr)
                        target = target.value
                    if isinstance(target, ast.Name):
                        parts.append(target.id)
                    found.add(".".join(reversed(parts)))
            return found
    return set()


class TestSessionLayer:
    """Rules the session layer adds on top of the layer direction."""

    def test_every_session_module_exists_where_it_is_expected(self):
        for path in session_module_paths():
            assert path.exists(), f"{path.name} is not in puzzlegen/engine"

    def test_identity_never_imports_the_deterministic_generator(self):
        """A credential minted from a reproducible stream is not a credential.

        ``DeterministicRng`` exists so a puzzle can be regenerated years
        later, which is exactly the property that would let somebody
        regenerate another player's return key.
        """
        modules = imported_modules(PACKAGE / "engine" / "identity.py")
        assert "puzzlegen.core.rng" not in modules
        assert not any(module.endswith(".rng") for module in modules)

    def test_no_session_module_reads_the_clock_directly(self):
        """Time is injected. A module calling ``datetime.now`` would make its
        own behaviour untestable and its sessions unreproducible.

        One exemption, and it is the reason the rule works: ``SystemClock`` is
        the single place the real clock is read, which is what leaves every
        other module injectable.
        """
        offenders = {}
        for path in session_module_paths():
            calls = attribute_calls(path) - clock_calls_inside(path, "SystemClock")
            direct = {
                call
                for call in calls
                if call.endswith("datetime.now")
                or call.endswith("dt.datetime.now")
                or call == "time.time"
            }
            if direct:
                offenders[path.name] = sorted(direct)
        assert not offenders, f"clock read directly in {offenders}"

    def test_no_session_module_reads_the_environment(self):
        forbidden = {"os", "os.path", "dotenv"}
        for path in session_module_paths():
            modules = imported_modules(path)
            caught = [m for m in modules if is_forbidden(m, forbidden)]
            assert not caught, f"{path.name} imports {caught}"

    def test_no_credential_name_appears_in_the_package(self):
        offenders = {}
        for path in python_files(PACKAGE):
            text = path.read_text(encoding="utf-8").lower()
            hits = [name for name in CREDENTIAL_NAMES if name in text]
            if hits:
                offenders[str(path.relative_to(PACKAGE))] = hits
        assert not offenders, f"credential-shaped names found: {offenders}"

    def test_only_the_session_layer_holds_session_repositories(self):
        """Session storage is engine-owned, like puzzle storage.

        The rule that matters is the same one phase 3 established for graph
        records: no layer above the one that owns a record may hold a
        repository over it.
        """
        for path in python_files(PACKAGE):
            layer = layer_of(path)
            if layer in (None, "engine"):
                continue
            modules = imported_modules(path)
            assert "puzzlegen.engine.session_storage" not in modules, (
                f"{path.relative_to(PACKAGE)} reaches into session storage"
            )

    def test_games_cannot_import_the_session_layer(self):
        """A game grades a move and scores telemetry. It never sees a player.

        Reaching the session layer would let a game read who is playing, which
        is precisely the identity the anonymous id exists to withhold.
        """
        forbidden = {
            "puzzlegen.engine.identity",
            "puzzlegen.engine.sessions",
            "puzzlegen.engine.session_storage",
            "puzzlegen.engine.session_service",
        }
        for path in python_files(PACKAGE / "games"):
            modules = imported_modules(path)
            caught = [m for m in modules if is_forbidden(m, forbidden)]
            assert not caught, f"{path.relative_to(PACKAGE)} imports {caught}"

    def test_the_share_layer_holds_a_redactor(self):
        """The share path must pass through redaction, not merely intend to."""
        text = (PACKAGE / "engine" / "sharing.py").read_text(encoding="utf-8")
        assert "class ShareRedactor" in text
        assert "_redactor.check(" in text


class TestSessionCheckerItself:
    """The session rules above, proved non-vacuous."""

    def test_a_planted_rng_import_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_identity.py"
        planted.write_text(
            "from puzzlegen.core.rng import DeterministicRng\n", encoding="utf-8"
        )
        modules = imported_modules(planted)
        assert any(module.endswith(".rng") for module in modules)

    def test_the_exemption_covers_one_class_only(self):
        """The sanctioned reader is exempted by name, so a second reader added
        to the same file is still caught."""
        path = PACKAGE / "engine" / "sessions.py"
        exempt = clock_calls_inside(path, "SystemClock")
        assert any(call.endswith("datetime.now") for call in exempt)
        # Any other class in the same file is outside the exemption, so a
        # second clock reader added beside it would still be caught.
        others = clock_calls_inside(path, "UtcDayWindow")
        assert not any(call.endswith("datetime.now") for call in others)

    def test_a_planted_clock_read_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_clock.py"
        planted.write_text(
            "import datetime as dt\nnow = dt.datetime.now()\n", encoding="utf-8"
        )
        calls = attribute_calls(planted)
        assert any(call.endswith("datetime.now") for call in calls)

    def test_a_planted_credential_name_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_identity.py"
        planted.write_text("access_token = 'abc'\n", encoding="utf-8")
        text = planted.read_text(encoding="utf-8").lower()
        assert any(name in text for name in CREDENTIAL_NAMES)

    def test_a_planted_session_storage_import_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_game.py"
        planted.write_text(
            "from puzzlegen.engine.session_storage import SessionRepositories\n",
            encoding="utf-8",
        )
        modules = imported_modules(planted)
        assert is_forbidden(
            next(iter(modules)), {"puzzlegen.engine.session_storage"}
        )


# -- review authority --------------------------------------------------------
#
# Two gates protect published content: repeated acceptance, and explicit
# activation. They only count as two if two different modules own them, so the
# rules below are about which file is allowed to write which status.

TOOLS = ROOT / "tools"

#: Statuses no module may assign except the one that owns the gate.
GUARDED_STATUS = {"APPROVED", "ACTIVE"}

#: The owner of each gate, by path relative to the package.
STATUS_OWNER = {
    "APPROVED": pathlib.Path("content/review.py"),
    "ACTIVE": pathlib.Path("content/snapshots.py"),
}


def _guarded_status_attribute(node: ast.AST) -> str | None:
    """``ReviewStatus.APPROVED`` or ``ReviewStatus.ACTIVE``, exactly.

    Exactly, and not wrapped in anything: ``str(ReviewStatus.ACTIVE)`` inside a
    query filter is a read, and flagging reads would make the rule so noisy it
    would be suppressed rather than obeyed.
    """
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ReviewStatus"
        and node.attr in GUARDED_STATUS
    ):
        return node.attr
    return None


def _targets_status(target: ast.AST) -> bool:
    if isinstance(target, ast.Name):
        return target.id == "status"
    if isinstance(target, ast.Attribute):
        return target.attr == "status"
    if isinstance(target, ast.Subscript):
        return isinstance(target.slice, ast.Constant) and target.slice.value == "status"
    return False


def status_writes(path: pathlib.Path) -> set[str]:
    """Every guarded status this file assigns to a status field.

    Three forms are writes: a ``status=`` keyword argument, a ``"status"`` key
    in a dict literal (which is how ``model_copy(update=...)`` writes one), and
    an assignment whose target is named ``status``. Everything else is a
    comparison, a set membership test or an ordering table.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "status":
            name = _guarded_status_attribute(node.value)
            if name:
                found.add(name)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "status":
                    name = _guarded_status_attribute(value)
                    if name:
                        found.add(name)
        elif isinstance(node, ast.Assign):
            if any(_targets_status(t) for t in node.targets):
                name = _guarded_status_attribute(node.value)
                if name:
                    found.add(name)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _targets_status(node.target):
                name = _guarded_status_attribute(node.value)
                if name:
                    found.add(name)
    return found


class TestReviewAuthority:
    """Who may write APPROVED, who may write ACTIVE, and who may write neither."""

    def test_only_the_review_service_writes_approved(self):
        offenders = {}
        for path in python_files(PACKAGE):
            relative = path.relative_to(PACKAGE)
            if relative == STATUS_OWNER["APPROVED"]:
                continue
            if "APPROVED" in status_writes(path):
                offenders[str(relative)] = "APPROVED"
        assert not offenders, f"APPROVED assigned outside the review service: {offenders}"

    def test_only_the_snapshot_builder_writes_active(self):
        offenders = {}
        for path in python_files(PACKAGE):
            relative = path.relative_to(PACKAGE)
            if relative == STATUS_OWNER["ACTIVE"]:
                continue
            if "ACTIVE" in status_writes(path):
                offenders[str(relative)] = "ACTIVE"
        assert not offenders, f"ACTIVE assigned outside SnapshotBuilder: {offenders}"

    def test_the_review_service_never_writes_active(self):
        """Passing review makes a record eligible, not usable. The whole point
        of two gates is that the first cannot reach through the second."""
        assert "ACTIVE" not in status_writes(PACKAGE / "content" / "review.py")

    def test_the_snapshot_builder_never_writes_approved(self):
        assert "APPROVED" not in status_writes(PACKAGE / "content" / "snapshots.py")

    def test_no_tool_writes_either_status(self):
        """A proposer writes JUDGED candidates and nothing else.

        This is the rule that makes a permissive proposer safe: it may suggest
        as freely as it likes because nothing it writes can reach a puzzle on
        its own.
        """
        offenders = {}
        for path in python_files(TOOLS):
            written = status_writes(path)
            if written:
                offenders[path.name] = sorted(written)
        assert not offenders, f"tools assign a guarded status: {offenders}"

    def test_the_owners_actually_write_what_they_own(self):
        """A rule that no file writes APPROVED would pass trivially if nothing
        wrote it at all. These two assertions are what make the two above
        mean 'exactly one writer' rather than 'at most one'."""
        assert "APPROVED" in status_writes(PACKAGE / "content" / "review.py")
        assert "ACTIVE" in status_writes(PACKAGE / "content" / "snapshots.py")

    def test_games_cannot_import_the_review_module(self):
        forbidden = {"puzzlegen.content.review"}
        for path in python_files(PACKAGE / "games"):
            modules = imported_modules(path)
            caught = [m for m in modules if is_forbidden(m, forbidden)]
            assert not caught, f"{path.relative_to(PACKAGE)} imports {caught}"
        assert "puzzlegen.content.review" in FORBIDDEN_FOR_GAMES

    def test_only_the_review_tool_drives_the_review_service(self):
        """Reading the ledger is fine; deciding is not.

        The proposer legitimately reads the ledger, to avoid offering a
        curator the same rejected suggestion every week. What it must not do is
        hold the service that records decisions, because a proposer that can
        record an accept is a proposer that can approve its own work.
        """
        offenders = []
        review_tool = (TOOLS / "review.py").resolve()
        for path in python_files(TOOLS):
            # By path, not by name. A copy of any module that happens to be
            # called review.py, dropped anywhere under tools/, walked straight
            # past a name check, and three separate phase 7 incidents came
            # from files landing in the wrong directory.
            if path.resolve() == review_tool:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "ReviewService":
                    offenders.append(path.name)
                elif isinstance(node, ast.Attribute) and node.attr == "ReviewService":
                    offenders.append(path.name)
                elif isinstance(node, ast.ImportFrom):
                    if any(alias.name == "ReviewService" for alias in node.names):
                        offenders.append(path.name)
        assert not offenders, f"{sorted(set(offenders))} hold the review service"

    def test_the_review_module_holds_no_store(self):
        """The ledger reaches storage through ``GraphRepositories.attach``.

        Importing a store directly would give the content layer the raw
        backend that phase 1 spent a module boundary keeping away from it.
        """
        modules = imported_modules(PACKAGE / "content" / "review.py")
        for banned in (
            "puzzlegen.graph.store",
            "puzzlegen.graph.memory_store",
            "puzzlegen.graph.sqlite_store",
            "sqlite3",
        ):
            assert banned not in modules, f"review.py imports {banned}"


class TestReviewCheckerItself:
    """The review rules above, proved non-vacuous."""

    def test_a_planted_keyword_write_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_keyword.py"
        planted.write_text(
            "record = Thing(status=ReviewStatus.APPROVED)\n", encoding="utf-8"
        )
        assert status_writes(planted) == {"APPROVED"}

    def test_a_planted_model_copy_write_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_copy.py"
        planted.write_text(
            'x = r.model_copy(update={"status": ReviewStatus.ACTIVE})\n',
            encoding="utf-8",
        )
        assert status_writes(planted) == {"ACTIVE"}

    def test_a_planted_attribute_assignment_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_assign.py"
        planted.write_text(
            "draft.status = ReviewStatus.APPROVED\n", encoding="utf-8"
        )
        assert status_writes(planted) == {"APPROVED"}

    def test_a_planted_subscript_assignment_would_be_caught(self, tmp_path):
        planted = tmp_path / "rogue_subscript.py"
        planted.write_text(
            'updates["status"] = ReviewStatus.ACTIVE\n', encoding="utf-8"
        )
        assert status_writes(planted) == {"ACTIVE"}

    def test_a_read_is_not_a_write(self, tmp_path):
        """The forms the package already uses, none of which is a write."""
        planted = tmp_path / "reads.py"
        planted.write_text(
            "\n".join(
                [
                    "rows = repo.find(status=str(ReviewStatus.ACTIVE))",
                    'filters["status"] = str(ReviewStatus.ACTIVE)',
                    "USABLE = frozenset({ReviewStatus.ACTIVE})",
                    "order = [ReviewStatus.APPROVED, ReviewStatus.ACTIVE]",
                    "if record.status is ReviewStatus.ACTIVE:",
                    "    pass",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assert status_writes(planted) == set()

    def test_an_unguarded_status_is_not_flagged(self, tmp_path):
        planted = tmp_path / "pending.py"
        planted.write_text(
            "record = Thing(status=ReviewStatus.PENDING_REVIEW)\n", encoding="utf-8"
        )
        assert status_writes(planted) == set()


class TestModulesLiveWhereTheyBelong:
    """Where a file sits is part of what it means.

    ``puzzlegen/content/review.py`` shipped as three byte-identical copies:
    the real one, one at the repository root, and one in ``content/``, which
    is a data directory. None was imported, so nothing failed; the root copy
    was importable as ``review`` by every tool, since each tool puts the
    repository root on ``sys.path``. A duplicate that is only latently wrong
    is the kind that survives.
    """

    #: Directories that hold content artifacts, never code.
    DATA_DIRECTORIES = ("content", "docs")

    def test_no_module_sits_at_the_repository_root(self):
        strays = sorted(p.name for p in ROOT.glob("*.py"))
        assert not strays, f"modules at the repository root: {strays}"

    def test_data_directories_hold_no_modules(self):
        for name in self.DATA_DIRECTORIES:
            directory = ROOT / name
            if not directory.exists():
                continue
            strays = sorted(
                str(p.relative_to(ROOT)) for p in python_files(directory)
            )
            assert not strays, f"{name}/ holds modules: {strays}"

    def test_each_package_module_name_is_unique_outside_the_package(self):
        """No file outside puzzlegen/ shares a name with a module inside it.

        A tool that adds the repository root to ``sys.path`` and imports a
        bare name gets whichever copy the path finds first, which is decided
        by directory order rather than by intent.
        """
        package_names = {p.stem for p in python_files(PACKAGE)} - {"__init__"}
        collisions = []
        for path in python_files(ROOT):
            if PACKAGE in path.parents or path.stem == "__init__":
                continue
            if "tests" in path.relative_to(ROOT).parts:
                continue
            # tools/ is exempt: a script there is a top-level name by
            # design, and nothing in the package may import it.
            if path.stem in package_names and path.parent != TOOLS:
                collisions.append(str(path.relative_to(ROOT)))
        assert not collisions, f"shadow package module names: {collisions}"
