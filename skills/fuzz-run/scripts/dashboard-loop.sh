#!/bin/bash
# Regenerates the dashboard once a minute and checkpoints session stats, as long as it runs. Ships with the fuzz-run skill.
# Usage: dashboard-loop.sh <project>/<version>/<campaign>   (paths relative to the workspace: $FUZZ_ROOT or the current directory)
HERE="$(cd "$(dirname "$0")" && pwd)"            # the skill's scripts/ dir
ROOT="${FUZZ_ROOT:-$PWD}"; cd "$ROOT" || exit 1  # workspace
CID="${1:?usage: dashboard-loop.sh <project>/<version>/<campaign>}"
P=${CID%%/*}; R=${CID#*/}; VER=${R%%/*}; C=${R#*/}; V="$P/$VER"
mkdir -p "$V/work"; ERR="$V/work/dashboard.err"
while true; do s=$(date +%s)
  python3 "$HERE/dashboard.py" "$CID" 2>>"$ERR"
  python3 "$HERE/session-snapshot.py" "$V/$C/out" "$V/src/fuzz/$C" checkpoint minute >/dev/null 2>>"$ERR"
  sleep $(( 60 - ($(date +%s) - s) % 60 )); done
