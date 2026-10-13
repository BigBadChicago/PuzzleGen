#!/usr/bin/env bash
# The one entry point every workflow runs, so a session dispatches a job and
# reads its summary instead of building in its own container.
#
#   scripts/ci.sh install [snapshot]   platform, every game, pinned wordfreq
#   scripts/ci.sh tests                platform alone, then every suite
#   scripts/ci.sh coverage             rebuild A2, measure, compare to committed
#   scripts/ci.sh snapshot             A2 with real vectors, then generate days
#   scripts/ci.sh symbolic             rebuild the symbolic snapshot and its days
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
# Same value as docs/build-symbolic-snapshot.sh, which builds the snapshot the
# symbolic task reads. Separate from NOW above so the two builds can move
# independently; they happen to agree today.
SYMBOLIC_NOW=2026-10-10T00:00:00+00:00

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

# The three games the symbolic snapshot serves, and where each writes its run.
#
# One line per game rather than a glob, because the day driver's flags and the
# summary renderer are the game's own: a glob would have to guess both. Adding
# a fourth symbolic game is a line here, which is the right size of edit for
# a claim this file is making on that game's behalf.
SYMBOLIC_GAMES="figures:shape-trio numbers:odd-number-out digits:digit-lock"

task_symbolic() {
  # The shipping claim for the symbolic games, rebuilt rather than trusted.
  #
  # Affordable in a way the lexical snapshot is not: the content is three
  # declarations, so there is no model, no network and no lexicon, and the
  # whole build takes seconds. That makes these the day runs this repository
  # can regenerate on every change and compare to what is committed, which is
  # the same mechanism coverage.yml applies to the coverage artifacts and for
  # the same reason: a committed artifact that no longer matches the code is
  # this project's recurring failure.
  local days="${DAYS:-30}" start="${START:-2026-10-01}" difficulty="${DIFFICULTY:-medium}"
  local status=0 changed=no
  if ! bash docs/build-symbolic-snapshot.sh; then
    printf '### Symbolic\n\n**build failed**: see the log above this summary\n' > "$SUMMARY"
    publish
    return 1
  fi

  : > "$OUT/symbolic.md"
  for entry in $SYMBOLIC_GAMES; do
    local game="${entry%%:*}" slug="${entry##*:}" json profile
    json="build/$slug-days-$days.json"
    profile="build/$slug-capabilities.json"
    # Written to the committed path so a diff is the comparison, pinned to the
    # snapshot's own time because content past its review date is refused
    # stale.
    python3 "games/$game/tools/generate_days.py" --db build/graph-symbolic.sqlite \
      --start "$start" --days "$days" --difficulty "$difficulty" \
      --now "$SYMBOLIC_NOW" --json "$json" \
      --capability-profile "$profile" || status=$?
    if [ ! -s "$json" ]; then
      printf '### Symbolic\n\n**%s: day generation failed**: see the log above\n' \
        "$game" > "$SUMMARY"
      publish
      return 1
    fi
    # Two checks, because build/ is gitignored and one of them alone is
    # blind. `git status --porcelain` reports a modified tracked file and says
    # nothing at all about an ignored untracked one, so a brand new game's
    # run would be reported as matching a committed run that does not exist.
    # The ls-files check is what catches that.
    #
    # The capability profile goes through the same two checks. It is observed
    # from the run's own port usage rather than declared, so a game that
    # starts reaching into a taxonomy nobody approved shows up here as a
    # changed file a reviewer reads, not as a discovery later.
    for artifact in "$json" "$profile"; do
      if ! git ls-files --error-unmatch "$artifact" >/dev/null 2>&1; then
        changed=yes
        status=1
      elif [ -n "$(git status --porcelain -- "$artifact")" ]; then
        changed=yes
        status=1
      fi
    done
    python3 scripts/ci_summary.py "$slug-days" "$json" >> "$OUT/symbolic.md"
    printf '\n' >> "$OUT/symbolic.md"
  done

  # Cross-board information flow, over the same days. A report rather than a
  # gate: nothing has declared what a daily set's coupling should be, and a
  # gate against an undeclared target would assert a product decision.
  # --require-independent turns this same run into the gate once it is
  # declared. The artifact goes through the same staleness checks as the rest.
  local coupling="build/symbolic-coupling-$days.json"
  # Restricted to the symbolic games, because a day has four active slots and
  # five games are installed: without this the three this snapshot can serve
  # would compete with two it cannot, and which three got measured would be
  # decided by discovery order.
  # The slug is the artifact's filename stem and uses hyphens; a game id uses
  # underscores. One translation, here, rather than a second list to keep in
  # step with the first.
  local coupling_games=""
  for entry in $SYMBOLIC_GAMES; do
    local slug="${entry##*:}"
    coupling_games="$coupling_games --game ${slug//-/_}"
  done
  # shellcheck disable=SC2086
  python3 tools/measure_coupling.py --db build/graph-symbolic.sqlite \
    --start "$start" --days "$days" --difficulty "$difficulty" \
    --now "$SYMBOLIC_NOW" --json "$coupling" $coupling_games \
    > "$OUT/coupling.txt" || status=$?
  if ! git ls-files --error-unmatch "$coupling" >/dev/null 2>&1; then
    changed=yes
    status=1
  elif [ -n "$(git status --porcelain -- "$coupling")" ]; then
    changed=yes
    status=1
  fi

  {
    printf '### Symbolic snapshot\n\n- days from %s at %s\n- committed runs changed: %s\n\n' \
      "$start" "$difficulty" "$changed"
    cat "$OUT/symbolic.md"
    printf '\n#### Cross-board information flow\n\n```\n'
    cat "$OUT/coupling.txt"
    printf '```\n'
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
  symbolic) task_symbolic ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
