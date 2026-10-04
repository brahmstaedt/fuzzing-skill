#!/usr/bin/env bash
# Run on the HOST before the talk. Prepares the machine and removes every earlier result so
# that /fuzz-campaign has to build everything live, from nothing.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n== %s\n' "$*"; }

say "core_pattern (AFL++ refuses to start otherwise)"
sudo sysctl -w kernel.core_pattern=core
echo kernel.core_pattern=core | sudo tee /etc/sysctl.d/99-afl.conf >/dev/null
echo "now: $(cat /proc/sys/kernel/core_pattern)"

say "CPU governor (afl-fuzz aborts on on-demand frequency scaling)"
echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null
echo "now: $(sort -u /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor | tr '\n' ' ')  (resets on reboot)"

say "toolchain"
if ! command -v afl-fuzz >/dev/null 2>&1 || ! command -v gdb >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq afl++ gdb
fi
afl-fuzz 2>&1 | head -1 || true

say "removing earlier results so the skill starts from nothing"
pkill -x afl-fuzz 2>/dev/null || true
rm -rf campaigns out work/src
rm -f core.*
mkdir -p campaigns
echo "removed: campaigns/  out/  work/src/"
echo "kept:    work/cache/  (downloaded source tarballs — not part of the demo)"

say "checks"
if [ -d archive ]; then
  echo "note: archive/ still has an old harness lying around; Claude should not read it, but delete it if unsure"
fi
echo "disk free: $(df -h --output=avail . | tail -1 | tr -d ' ')   cores: $(nproc)"
echo "terminal size: $(stty size 2>/dev/null | awk '{print $2"x"$1}')  (needs at least 80x24 in the demo font)"
echo "manual: turn off sleep, lid-close suspend, screensaver, notifications; stay on AC power"

say "ready"
echo "In Claude Code:  /fuzz-campaign dcmtk 3.6.3 fuzz the DICOM input parsing"
