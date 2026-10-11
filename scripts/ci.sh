#!/usr/bin/env bash
# The one entry point every workflow runs, so a session dispatches a job and
# reads its summary instead of building in its own container.
#
#   scripts/ci.sh install [snapshot]   platform, every game, pinned wordfreq
#   scripts/ci.sh tests                platform alone, then every suite
#   scripts/ci.sh coverage             rebuild A2, measure, compare to committed
#   scripts/ci.sh snapshot             A2 with real vectors, then generate days
#
# Each task writes build/ci/summary.md, appends it to the job's step summary
# when there is one, and prints it last between markers, so the final lines of
# a job log are the result. Runs the same on a laptop as on a runner.
#
# snapshot reads: DAYS (30), START (2026-10-01), DIFFICULTY (medium),
# EMBEDDINGS (real|dev, default real), REEXPORT (1 to re-export every root).
set -uo pipefail

cd "$(dirname "$0")/.."
OUT=build/ci
mkdir -p "$OUT"
SUMMARY="$OUT/summary.md"

# Same values as docs/rebuild-with-batch2.sh, which builds the snapshot read here.
DB=build/graph-A2.sqlite
NOW=2026-10-10T00:00:00+00:00

publish() {
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] && cat "$SUMMARY" >> "$GITHUB_STEP_SUMMARY"
  echo
  echo "===== SUMMARY ====="
  cat "$SUMMARY"
  echo "===== END SUMMARY ====="
}

games() {
  # A game's content can exist before its code: games/letters carries game 2's
  # categories and no package yet. Such a directory is skipped, and the skip is
  # not this function's exit status. It was, while grouping was the only entry
  # and had a pyproject: a trailing skip made the last `[ -f ]` the function's
  # status, so `games` returned 1, and under task_install's `set -eE` the ERR
  # trap fired inside `$(games)` and its summary text became pip's argument
  # ("===== is not a valid editable requirement").
  for game in games/*/; do
    if [ -f "$game/pyproject.toml" ]; then echo "$game"; fi
  done
}

game_name() {
  python3 -c "import tomllib,sys;print(tomllib.load(open(sys.argv[1]+'/pyproject.toml','rb'))['project']['name'])" "$1"
}

task_install() {
  # A failed install skips every later step, so this is the only place its
  # summary can be written.
  trap 'printf "### Install\n\n**install failed**: see the log above this summary\n" > "$SUMMARY"; publish' ERR
  set -eE
  python3 -m pip install --upgrade pip
  if [ "${1:-}" = snapshot ]; then
    # CPU wheels: the default torch wheel carries CUDA, gigabytes a runner never uses.
    python3 -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    python3 -m pip install -e ".[dev,snapshot]"
  else
    python3 -m pip install -e ".[dev]"
  fi
  for game in $(games); do python3 -m pip install -e "$game"; done
  # Pinned. Frequency bands decide which entities can be tiles, so a wordfreq
  # release can move every coverage number; a drift should be a change here,
  # not upstream.
  python3 -m pip install "wordfreq==3.1.1"
}

task_tests() {
  local status=0 logs=()
  # Platform with no game installed: the only way the claim that the platform
  # depends on no game is tested.
  for game in $(games); do python3 -m pip uninstall -y -q "$(game_name "$game")"; done
  python3 -m pytest -q --no-header tests 2>&1 | tee "$OUT/platform-alone.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || status=1
  logs+=("$OUT/platform-alone.log")
  for game in $(games); do python3 -m pip install -q -e "$game"; done

  export PUZZLEGEN_REQUIRE_GAMES=1
  python3 -m pytest -q --no-header tests 2>&1 | tee "$OUT/platform.log"
  [ "${PIPESTATUS[0]}" -eq 0 ] || status=1
  logs+=("$OUT/platform.log")
  for game in $(games); do
    local name log
    name=$(basename "$game")
    log="$PWD/$OUT/$name.log"
    ( cd "$game" && python3 -m pytest -q --no-header tests ) 2>&1 | tee "$log"
    [ "${PIPESTATUS[0]}" -eq 0 ] || status=1
    logs+=("$log")
  done

  python3 scripts/ci_summary.py tests "${logs[@]}" > "$SUMMARY"
  publish
  return "$status"
}

task_coverage() {
  local status=0 changed=no
  local artifacts=(
    build/coverage-A2.json build/lemma-collisions-A2.json build/letters-A2.json
  )
  git show HEAD:build/coverage-A2.json > "$OUT/coverage-A2.committed.json" 2>/dev/null || : > "$OUT/coverage-A2.committed.json"

  if ! bash docs/rebuild-with-batch2.sh; then
    printf '### Coverage (build A2)\n\n**rebuild failed**: see the log above this summary\n' > "$SUMMARY"
    publish
    return 1
  fi
  if ! git diff --quiet -- "${artifacts[@]}"; then
    changed=yes
    status=1
    git diff --stat -- "${artifacts[@]}" > "$OUT/coverage.diffstat"
  fi
  python3 scripts/ci_summary.py coverage build/coverage-A2.json \
    --old "$OUT/coverage-A2.committed.json" --changed "$changed" > "$SUMMARY"
  publish
  return "$status"
}

task_snapshot() {
  local days="${DAYS:-30}" start="${START:-2026-10-01}" difficulty="${DIFFICULTY:-medium}"
  if [ "${EMBEDDINGS:-real}" = real ]; then
    export PUZZLEGEN_REAL_EMBEDDINGS=1
  else
    # EMBEDDINGS decides, not whatever the caller's shell already exported.
    unset PUZZLEGEN_REAL_EMBEDDINGS
  fi
  if [ "${REEXPORT:-}" = 1 ]; then
    export PUZZLEGEN_REEXPORT=1
    python3 -c 'import wn; wn.download("oewn:2024")' || return 1
  fi
  if ! bash docs/rebuild-with-batch2.sh; then
    printf '### Snapshot\n\n**build failed**: see the log above this summary\n' > "$SUMMARY"
    publish
    return 1
  fi
  local json="build/days-$days.json" status=0
  rm -f "$json"
  # Exits 1 when any day fails to generate, after writing every day's result:
  # those per-day reasons are the summary, so render them whenever they exist.
  python3 games/grouping/tools/generate_days.py --db "$DB" --start "$start" --days "$days" \
    --difficulty "$difficulty" --now "$NOW" --json "$json" || status=$?
  if [ ! -s "$json" ]; then
    printf '### Snapshot\n\n**day generation failed**: see the log above this summary\n' > "$SUMMARY"
    publish
    return 1
  fi
  {
    printf '### Snapshot\n\n- embeddings: %s\n- days from %s at %s\n\n' "${EMBEDDINGS:-real}" "$start" "$difficulty"
    python3 scripts/ci_summary.py days "$json"
  } > "$SUMMARY"
  publish
  return "$status"
}

task="${1:-}"
shift || true
case "$task" in
  install) task_install "$@" ;;
  tests) task_tests ;;
  coverage) task_coverage ;;
  snapshot) task_snapshot ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
