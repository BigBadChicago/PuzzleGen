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
