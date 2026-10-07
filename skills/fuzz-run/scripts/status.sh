#!/bin/bash
# Fuzzing status: totals over all instances + deduplicated crash signatures. Ships with the fuzz-run skill.
# Usage: status.sh <project>/<version>/<campaign>   (paths relative to the workspace: $FUZZ_ROOT or the current directory)
CID="${1:?usage: status.sh <project>/<version>/<campaign>}"; ROOT="${FUZZ_ROOT:-$PWD}"
P=${CID%%/*}; R=${CID#*/}; VER=${R%%/*}; C=${R#*/}; V="$ROOT/$P/$VER"
O="$V/$C/out"; H="$V/src/fuzz/$C/harness"
R="$O/.remote.json"; I=$(pgrep -c afl-fuzz)   # fuzzers on a server (remote-fuzz.sh): take the count from the last pull, if under 5 min old
[ -f "$R" ] && [ $(( $(date +%s) - $(sed 's/.*"synced": \([0-9]*\).*/\1/' "$R") )) -lt 300 ] && I="$(sed 's/.*"running": \([0-9]*\).*/\1/' "$R") (remote)"
echo "== $(date +%H:%M) instances=$I $(cat "$O"/*/fuzzer_stats 2>/dev/null | awk -F' *: *' '/^execs_done/{e+=$2} /^saved_crashes/{c+=$2} /^saved_hangs/{h+=$2} /^corpus_count/{q+=$2} /^execs_per_sec/{s+=$2} END{printf "execs=%d (%d/s) corpus(sum)=%d crashes=%d hangs=%d",e,s,q,c,h}')"
for f in "$O"/*/crashes*/id:*; do [ -f "$f" ] || continue
  r=$(ASAN_OPTIONS=detect_leaks=0:symbolize=1 timeout 20 "$H" < "$f" 2>&1 | grep -m1 "SUMMARY: AddressSanitizer" | sed 's/SUMMARY: AddressSanitizer: //')
  echo "${r:-no-asan-report(rc/timeout)} | $f"; done | sort | awk -F' \\| ' '{n[$1]++; ex[$1]=$2} END{for(k in n) printf "  %dx %s\n     e.g. %s\n",n[k],k,ex[k]}'
