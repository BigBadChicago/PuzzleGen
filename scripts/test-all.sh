#!/usr/bin/env bash
# Every suite in the repository: the platform, then each game distribution.
#
# Two suites, not one, because they are two distributions. The platform's suite
# must pass with no game installed, which is what proves the platform depends
# on no game; a single pytest run over both would hide that. Games are found by
# globbing the games directory rather than listed, so adding one needs no edit
# here either.
#
# Usage: scripts/test-all.sh [extra pytest args]
set -uo pipefail

cd "$(dirname "$0")/.."
status=0

# The boundary tests have nothing to inspect with no game installed, which is
# fine for the platform suite on its own and a broken configuration here. This
# turns their skip into a failure.
export PUZZLEGEN_REQUIRE_GAMES=1

echo "== platform"
python3 -m pytest -q --no-header "$@" tests || status=1

for game in games/*/; do
  [ -f "$game/pyproject.toml" ] || continue
  echo "== $(basename "$game")"
  ( cd "$game" && python3 -m pytest -q --no-header "$@" tests ) || status=1
done

exit "$status"
