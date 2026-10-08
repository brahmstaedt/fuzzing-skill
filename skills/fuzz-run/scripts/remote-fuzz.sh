#!/bin/bash
# Runs a prepared campaign on a remote server instead of locally, and keeps the local <project>/<version>/<campaign>/out/ in sync with it.
# Run from the workspace ($FUZZ_ROOT or the current directory, holds <project>/<version>/). Ships with the fuzz-run skill.
#
# Usage: remote-fuzz.sh <command> <project>/<version>/<campaign> [user@]host
#   start   prepare the host (idempotent), push campaign + local <project>/<version>/<campaign>/out/, start the fuzzers, smoke-test, start the local sync loop
#   sync    pull the server's out/ into <project>/<version>/<campaign>/out/ once (add --loop to repeat every FUZZ_SYNC_SECONDS, default 60)
#   status  container state, instance count, afl-whatsup summary
#   stop    stop the fuzzers (the entrypoint writes the final session snapshot), final pull, stop the local sync loop
#
# The fuzzers run in an Ubuntu 24.04 container with the distro's AFL++, the same toolchain the harness is built with here,
# so the locally built harness runs unchanged whatever the server's OS. The campaign dir and out dir are bind-mounted.
# Sync direction: the server is the source of truth while it runs; local -> server only at start (and only after first
# pulling whatever the server already has, so a missed sync never loses remote results).
# Env: FUZZ_RESERVE_CORES (default 2) cores left free · FUZZ_REMOTE_DIR (default /srv/fuzz) · FUZZ_APT_PKGS extra
#      runtime packages the harness needs in the image · DICT dictionary path relative to the campaign dir
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"            # the skill's scripts/ dir
ROOT="${FUZZ_ROOT:-$PWD}"; cd "$ROOT"
CMD="${1:-}"; CID="${2:-}"; HOST="${3:-}"; LOOP="${4:-}"   # CID = <project>/<version>/<campaign>
[ -n "$CMD" ] && [ -n "$CID" ] && [ -n "$HOST" ] || { sed -n '5,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
command -v rsync >/dev/null || { echo "rsync missing locally (sudo apt-get install rsync)" >&2; exit 1; }
P=${CID%%/*}; R=${CID#*/}; VER=${R%%/*}; C=${R#*/}; V="$P/$VER"
CAMP="$V/src/fuzz/$C"; NAME="$P-$VER-$C"   # campaign sources + built harness in the fork worktree; NAME = flat id for server dir/container
[ -d "$CAMP" ] || { echo "no $CAMP (run from the workspace)" >&2; exit 1; }

RDIR="${FUZZ_REMOTE_DIR:-/srv/fuzz}/$NAME"; CTR="fuzz-$NAME"; IMAGE="fuzz-afl:ubuntu24.04"
RESERVE="${FUZZ_RESERVE_CORES:-2}"; EVERY="${FUZZ_SYNC_SECONDS:-60}"
OUT="$V/$C/out"; mkdir -p "$V/work"; PIDFILE="$V/work/remote-$C.sync.pid"; LOG="$V/work/remote-$C.sync.log"
# UpdateHostKeys=no: a read-only known_hosts (session container) makes ssh print errors on every connect otherwise.
SSH=(ssh -o BatchMode=yes -o UpdateHostKeys=no -o ServerAliveInterval=30 -o ConnectTimeout=15)
RSH="${SSH[*]}"
LOCAL_ONLY=(dashboard.html dashboard.html.tmp .triage.json .remote.json)   # written here, never by the server
r() { "${SSH[@]}" "$HOST" "$@"; }

running_remote() { r "docker inspect -f '{{.State.Running}}' $CTR 2>/dev/null" 2>/dev/null | grep -qx true; }
instances() { local n; n=$(r "docker exec $CTR pgrep -c -x afl-fuzz 2>/dev/null" 2>/dev/null | head -n1); echo "${n:-0}"; }   # pgrep -c prints 0 and exits 1 when none
sync_loop_pid() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && cat "$PIDFILE"; }

setup_host() {   # idempotent: packages, kernel settings AFL++/ASan need (persisted), the runtime image
  echo "== preparing $HOST"
  r 'bash -s' <<EOF
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1
command -v docker >/dev/null && command -v rsync >/dev/null || { apt-get update -qq; apt-get install -y -qq docker.io rsync >/tmp/fuzz-apt.log 2>&1 || { tail -20 /tmp/fuzz-apt.log; exit 1; }; }
systemctl enable --now docker >/dev/null 2>&1
# core_pattern must not be a pipe (AFL++ refuses); apport would rewrite it on boot.
systemctl disable --now apport >/dev/null 2>&1 || true
# vm.mmap_rnd_bits=28: ASan from clang < 18 (the 24.04 toolchain) dies at startup with the 32 bits that newer kernels default to.
printf 'kernel.core_pattern=core\nvm.mmap_rnd_bits=28\n' > /etc/sysctl.d/60-fuzz.conf
sysctl -q -p /etc/sysctl.d/60-fuzz.conf
for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do [ -w "\$g" ] && echo performance > "\$g"; done 2>/dev/null || true
mkdir -p /root/fuzz-image
cat > /root/fuzz-image/Dockerfile <<'DF'
FROM ubuntu:24.04
ARG EXTRA=""
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends afl++ python3 procps libstdc++6 \$EXTRA && rm -rf /var/lib/apt/lists/*
DF
DOCKER_CLI_HINTS=false docker build -q --build-arg EXTRA="${FUZZ_APT_PKGS:-}" -t $IMAGE /root/fuzz-image 2>&1 >/dev/null | { grep -v -e DEPRECATED -e 'legacy builder' -e buildx -e '^ *$' >&2 || true; }   # filter the legacy-builder notice; a build failure still fails (pipefail)
EOF
  local lv rv
  lv=$(dpkg-query -W -f '${Version}' afl++ 2>/dev/null || echo "?")
  rv=$(r "docker run --rm $IMAGE dpkg-query -W -f '\${Version}' afl++")
  echo "   AFL++ local $lv, server image $rv"
  [ "$lv" = "$rv" ] || echo "   WARNING: AFL++ versions differ; the harness was instrumented with the local one" >&2
}

pull() {   # server -> local. --delete so AFL's crashes/ -> crashes.<date> renames on resume don't leave stale copies; local-only files protected.
  mkdir -p "$OUT"
  local f=(--exclude='.cur_input' --exclude='*.tmp')
  for p in "${LOCAL_ONLY[@]}"; do f+=(--filter="P /$p"); done
  rsync -a --delete "${f[@]}" -e "$RSH" "$HOST:$RDIR/out/" "$OUT/"
  printf '{"host": "%s", "container": "%s", "running": %d, "synced": %d}\n' "$HOST" "$CTR" "$(instances)" "$(date +%s)" > "$OUT/.remote.json"
}

push() {   # local -> server: campaign (harness, seeds, ...), the skill's scripts, <project>/<version>/<campaign>/out/
  local x=(); for p in "${LOCAL_ONLY[@]}"; do x+=(--exclude="/$p"); done
  r "mkdir -p $RDIR/campaign $RDIR/tools $RDIR/out"
  rsync -a --delete -e "$RSH" "$CAMP/" "$HOST:$RDIR/campaign/"
  rsync -a -e "$RSH" "$HERE/fuzz-entrypoint.sh" "$HERE/session-snapshot.py" "$HOST:$RDIR/tools/"
  [ -d "$OUT" ] && rsync -a --delete "${x[@]}" -e "$RSH" "$OUT/" "$HOST:$RDIR/out/"
  r "chown -R 1000:1000 $RDIR/out"
}

start_sync_loop() {
  [ -n "$(sync_loop_pid)" ] && { echo "   local sync loop already running (pid $(sync_loop_pid))"; return; }
  mkdir -p work
  setsid nohup "$0" sync "$CID" "$HOST" --loop >>"$LOG" 2>&1 < /dev/null &
  echo $! > "$PIDFILE"; echo "   local sync loop pid $! every ${EVERY}s -> $OUT/ (log $LOG)"
}

case "$CMD" in
start)
  if running_remote; then echo "$CTR already runs on $HOST"; start_sync_loop; exit 0; fi
  if pgrep -x afl-fuzz >/dev/null; then echo "afl-fuzz runs locally; stop it first, it would write into $OUT while the server owns it" >&2; exit 1; fi
  [ -f "$CAMP/harness" ] || { echo "$CAMP/harness missing; build the campaign first" >&2; exit 1; }
  setup_host
  if r "test -n \"\$(ls -A $RDIR/out 2>/dev/null)\""; then echo "== pulling the server's existing results first"; pull; fi
  echo "== pushing campaign + $OUT"; push
  echo "== smoke test: harness on every seed inside the image"
  r "docker run --rm --user 1000:1000 -v $RDIR/campaign:/campaign:ro $IMAGE bash -c '
       ldd /campaign/harness | grep \"not found\" && exit 1
       for s in /campaign/seeds/*; do ASAN_OPTIONS=detect_leaks=0 timeout 20 /campaign/harness < \"\$s\" >/dev/null 2>/tmp/err || { echo \"seed \$s: rc \$?\"; head -5 /tmp/err; exit 1; }; done
       echo \"   \$(ls /campaign/seeds | wc -l) seeds replay cleanly\"'"
  CORES=$(r nproc); N=$(( CORES - RESERVE )); [ "$N" -lt 1 ] && N=1
  MEM=$(r "awk '/MemTotal/{print int(\$2*0.9/1024)}' /proc/meminfo")   # cap at 90% RAM so an ASan blow-up hits the container, not sshd
  echo "== starting $N instances on $CORES cores (container memory cap ${MEM} MB)"
  [ $(( N * 1500 )) -gt "$MEM" ] && echo "   WARNING: $N instances x ~1.5 GB exceeds the cap; see fuzz-run §1 (memory)" >&2
  r "docker rm -f $CTR >/dev/null 2>&1; docker run -d --name $CTR --init --restart unless-stopped --stop-timeout 120 \
       --user 1000:1000 --memory ${MEM}m \
       -v $RDIR/campaign:/campaign:ro -v $RDIR/out:/out -v $RDIR/tools:/tools:ro \
       -e CAMPAIGN=/campaign -e OUT=/out -e FUZZ_CORES=$N -e FUZZ_SNAPSHOT=/tools/session-snapshot.py -e FUZZ_CHECKPOINT=1 \
       ${DICT:+-e DICT=/campaign/$DICT} \
       $IMAGE /tools/fuzz-entrypoint.sh >/dev/null"
  echo "== waiting 45 s, then verifying"; sleep 45
  n=$(instances)
  [ "$n" = "$N" ] || { echo "only $n of $N afl-fuzz instances running; last log lines:" >&2; r "tail -n 20 $RDIR/out/main.log" >&2; exit 1; }
  echo "   $n afl-fuzz instances running"
  pull; start_sync_loop
  echo "dashboard: $HERE/dashboard-loop.sh $CID (reads the synced copy)"
  ;;
sync)
  if [ "$LOOP" = "--loop" ]; then
    while true; do s=$(date +%s); pull || echo "$(date '+%F %T') pull failed" >&2
      sleep $(( EVERY - ($(date +%s) - s) % EVERY )); done
  fi
  pull; echo "synced $HOST:$RDIR/out -> $OUT ($(sed 's/.*"running": \([0-9]*\).*/\1/' "$OUT/.remote.json") instances running)"
  ;;
status)
  echo "container: $(r "docker inspect -f '{{.State.Status}} since {{.State.StartedAt}}' $CTR 2>/dev/null" || echo absent)"
  echo "instances: $(instances) · local sync loop: $(sync_loop_pid || echo none)"
  [ -f "$OUT/.remote.json" ] && echo "last sync: $(( $(date +%s) - $(sed 's/.*"synced": \([0-9]*\).*/\1/' "$OUT/.remote.json") )) s ago"
  r "docker exec $CTR afl-whatsup -s /out 2>/dev/null | tail -n 12" || true
  ;;
stop)
  p=$(sync_loop_pid || true); [ -n "$p" ] && kill "$p" && rm -f "$PIDFILE" && echo "stopped local sync loop $p"
  if running_remote; then echo "== stopping $CTR (final snapshot runs in the container)"; r "docker stop $CTR >/dev/null"; fi
  echo "== final pull"; pull
  echo "done; server keeps $RDIR (resume with: $0 start $CID $HOST)"
  ;;
*) echo "unknown command $CMD" >&2; exit 2 ;;
esac
