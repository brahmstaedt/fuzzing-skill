#!/usr/bin/env python3
"""Record campaign statistics that AFL++ loses across restarts (execs_done resets, run_time includes downtime, CPU time is never kept).
Usage: session-snapshot.py <afl-out-dir> <campaign-dir> [checkpoint|final] [reason]
Writes <afl-out-dir>/session.json:  segments[] (one per afl-fuzz session, finalised at stop/restart)  +  current (latest checkpoint)  +  cumulative.
Run it BEFORE stopping the fuzzers (CPU time is read from /proc). 'checkpoint' (e.g. every minute) survives a crash; 'final' closes the segment."""
import glob, hashlib, json, os, subprocess, sys, time

OUT = os.path.abspath(sys.argv[1]); CAMP = os.path.abspath(sys.argv[2]); MODE = sys.argv[3] if len(sys.argv) > 3 else "checkpoint"
REASON = sys.argv[4] if len(sys.argv) > 4 else ""
PATH = os.path.join(OUT, "session.json"); TICK = os.sysconf("SC_CLK_TCK"); NOW = int(time.time())
KEYS = ["run_time", "execs_done", "corpus_count", "edges_found", "saved_crashes", "saved_hangs", "cycles_done", "stability",
        "execs_per_sec", "last_find", "pending_total", "max_depth"]

def stats(path):
    d = {}
    try:
        for l in open(path):
            k, _, v = l.partition(":"); d[k.strip()] = v.strip()
    except OSError: pass
    return d

def procs(names):
    """pid -> (comm, cpu_seconds incl. reaped children, elapsed_seconds, nice)."""
    out = {}
    up = float(open("/proc/uptime").read().split()[0])
    for p in glob.glob("/proc/[0-9]*"):
        try:
            raw = open(p + "/stat").read(); comm = raw[raw.index("(") + 1:raw.rindex(")")]
            if comm not in names: continue
            f = raw[raw.rindex(")") + 2:].split()                 # fields from state on: utime=f[11] stime=f[12] cutime=f[13] cstime=f[14] nice=f[16] start=f[19]
            cpu = sum(int(x) for x in f[11:15]) / TICK
            out[int(os.path.basename(p))] = (comm, cpu, up - int(f[19]) / TICK, int(f[16]))
        except (OSError, ValueError, IndexError): pass
    return out

def sh(cmd):
    try: return subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=CAMP).stdout.strip()
    except Exception: return ""

def collect():
    pr = procs({"afl-fuzz", "harness"})
    afl = {p: v for p, v in pr.items() if v[0] == "afl-fuzz"}
    inst = {}
    for d in sorted(glob.glob(os.path.join(OUT, "*", "fuzzer_stats"))):
        s = stats(d); inst[os.path.basename(os.path.dirname(d))] = {k: s.get(k) for k in KEYS}
    num = lambda k: sum(float(v[k]) for v in inst.values() if v.get(k) not in (None, "") and str(v[k]).replace(".", "", 1).isdigit())
    harness = os.path.join(CAMP, "harness")
    try: md5 = hashlib.md5(open(harness, "rb").read()).hexdigest()
    except OSError: md5 = None
    unique = None
    try: unique = sorted({v["summary"] for v in json.load(open(os.path.join(OUT, ".triage.json"))).values()})
    except Exception: pass
    first = next(iter(inst.values()), {})
    cmd = stats(os.path.join(OUT, "main", "fuzzer_stats")).get("command_line", "")
    seg = {
        "start_epoch": NOW - int(max((v[2] for v in afl.values()), default=0)),
        "end_epoch": NOW, "mode": MODE, "reason": REASON,
        "instances": len(afl), "instance_wall_hours": round(sum(v[2] for v in afl.values()) / 3600, 3),
        "cpu_hours": round(sum(v[1] for v in pr.values()) / 3600, 3),      # afl-fuzz + harness processes, incl. reaped children
        "execs": int(num("execs_done")), "saved_crashes": int(num("saved_crashes")), "saved_hangs": int(num("saved_hangs")),
        "crash_files_all_sessions": len(glob.glob(os.path.join(OUT, "*", "crashes*", "id:*"))), "hang_files_all_sessions": len(glob.glob(os.path.join(OUT, "*", "hangs*", "id:*"))),
        "corpus_sum": int(num("corpus_count")), "max_edges": int(max([float(v["edges_found"]) for v in inst.values() if v.get("edges_found")] or [0])),
        "nice": sorted({v[3] for v in afl.values()}), "unique_crash_signatures": unique,
        "harness_md5": md5, "harness_git": sh(["git", "log", "-1", "--format=%h %s", "--", "harness.cc"]),
        "afl_command_line": cmd, "avail_ram_mb": int(sh(["sh", "-c", "free -m | awk 'NR==2{print $7}'"]) or 0),
        "loadavg": os.getloadavg(), "per_instance": inst,
    }
    seg["start_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(seg["start_epoch"])); seg["end_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW))
    return seg

def cumulative(segs):
    return {"segments": len(segs), "instance_wall_hours": round(sum(s["instance_wall_hours"] for s in segs), 3),
            "cpu_hours": round(sum(s["cpu_hours"] for s in segs), 3), "execs": sum(s["execs"] for s in segs)}

def main():
    try: data = json.load(open(PATH))
    except (OSError, ValueError): data = {"campaign": os.path.basename(CAMP), "created": NOW, "segments": [], "unrecorded": []}
    seg = collect()
    if seg["instances"] == 0 and MODE == "checkpoint":                 # nothing running: keep the last checkpoint untouched
        return
    cur = data.get("current")
    if cur and abs(cur["start_epoch"] - seg["start_epoch"]) > 120:     # previous session ended without a final snapshot: close it from its last checkpoint
        cur["mode"] = "closed-from-last-checkpoint"; data["segments"].append(cur); cur = None
    if MODE == "final":
        seg["start_epoch"] = cur["start_epoch"] if cur else seg["start_epoch"]
        prev = data["segments"][-1] if data["segments"] else None
        if prev and abs(prev["start_epoch"] - seg["start_epoch"]) <= 120:   # same session already closed (manual final + the entrypoint's stop trap): replace, never count twice
            data["segments"][-1] = seg
        else:
            data["segments"].append(seg)
        data["current"] = None
    else:
        data["current"] = seg
    allsegs = data["segments"] + ([data["current"]] if data.get("current") else [])
    data["cumulative"] = cumulative(allsegs); data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = PATH + ".tmp"; json.dump(data, open(tmp, "w"), indent=1); os.replace(tmp, PATH)
    print(f"session.json: {MODE} cpu_h={seg['cpu_hours']} instance_h={seg['instance_wall_hours']} execs={seg['execs']} | cumulative {data['cumulative']}")

if __name__ == "__main__":
    main()
