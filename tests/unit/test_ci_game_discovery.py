"""`games()` in scripts/ci.sh, which decides what CI installs.

Pinned because the shape of the bug it had is invisible in review. The
function echoes each game directory that carries a pyproject.toml, and the
install step consumes it as `$(games)` under `set -eE`. A directory without a
pyproject made the final `[ -f ]` the function's exit status, so the function
returned 1, the ERR trap fired inside the command substitution, and the
trap's own summary text ("===== SUMMARY =====") became pip's argument. Three
CI jobs failed on an install error naming nothing in the diff.

Game 2's content shipped before game 2's code, which is a legitimate state
and the one that triggered it, so the skip is tested directly rather than
left to the next content-only game to rediscover.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / "scripts" / "ci.sh"


def discover(root: Path) -> subprocess.CompletedProcess:
    """Run `games` from ci.sh against ``root`` as the working directory.

    The function is extracted by text rather than by sourcing the whole
    script, because sourcing it runs its argument dispatch.
    """
    return subprocess.run(
        ["bash", "-c", f"{_function_text()}\ngames"],
        cwd=root,
        capture_output=True,
        text=True,
    )


def _function_text() -> str:
    lines = CI.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("games()"))
    end = next(i for i, line in enumerate(lines[start:], start) if line == "}")
    return "\n".join(lines[start : end + 1])


def make_game(root: Path, name: str, *, packaged: bool) -> None:
    directory = root / "games" / name
    directory.mkdir(parents=True)
    if packaged:
        (directory / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    else:
        (directory / "content").mkdir()


class TestWhichGamesCiInstalls:
    def test_a_packaged_game_is_listed(self, tmp_path: Path):
        make_game(tmp_path, "grouping", packaged=True)
        result = discover(tmp_path)
        assert result.returncode == 0
        assert result.stdout.split() == ["games/grouping/"]

    def test_a_content_only_game_is_skipped(self, tmp_path: Path):
        make_game(tmp_path, "letters", packaged=False)
        result = discover(tmp_path)
        assert result.returncode == 0
        assert result.stdout.split() == []

    def test_a_content_only_game_sorting_last_does_not_fail_the_function(
        self, tmp_path: Path
    ):
        """The exact arrangement that broke CI: the skip came last, so the
        test's own failure was the function's exit status."""
        make_game(tmp_path, "grouping", packaged=True)
        make_game(tmp_path, "letters", packaged=False)
        result = discover(tmp_path)
        assert result.returncode == 0
        assert result.stdout.split() == ["games/grouping/"]

    def test_the_repository_itself_discovers_exactly_its_packaged_games(self):
        """Read off the tree rather than listed, so adding a game needs no
        edit here. This assertion used to name ``games/letters`` as the
        content-only case; that game now carries a pyproject of its own, and a
        test pinning which directories happen to be packaged today would have
        to be edited by every game that ships.
        """
        packaged = {
            f"games/{directory.name}/"
            for directory in (ROOT / "games").iterdir()
            if (directory / "pyproject.toml").is_file()
        }
        result = discover(ROOT)
        assert result.returncode == 0
        assert set(result.stdout.split()) == packaged
        assert "games/grouping/" in packaged
