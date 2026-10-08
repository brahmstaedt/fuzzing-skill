#!/usr/bin/env python3
"""Static HTML dashboard for a running AFL++ campaign. Usage, from the workspace ($FUZZ_ROOT or cwd): dashboard.py <project>/<version>/<campaign>
Reads <project>/<version>/<campaign>/out/*/fuzzer_stats, plot_data, crashes/, hangs/; writes out/dashboard.html (auto-reloads every 60 s).
Crashes are replayed once (result cached in out/.triage.json, keyed by file content) to get the ASan summary and stack."""
import glob, hashlib, html, json, os, re, subprocess, sys, time

ROOT = os.path.abspath(os.environ.get("FUZZ_ROOT", os.getcwd()))   # workspace (holds <project>/<version>/); script lives in the skill
if len(sys.argv) < 2 or sys.argv[1].strip("/").count("/") != 2:
    sys.exit("usage: dashboard.py <project>/<version>/<campaign>")
NAME = sys.argv[1].strip("/")
PROJECT, VERSION, CAMPAIGN = NAME.split("/")
VDIR = os.path.join(ROOT, PROJECT, VERSION)
OUT = os.path.join(VDIR, CAMPAIGN, "out")
CDIR = os.path.join(VDIR, "src", "fuzz", CAMPAIGN)                  # campaign sources in the fork worktree
HARNESS = os.path.join(CDIR, "harness")
SEEDS = glob.glob(os.path.join(CDIR, "seeds", "*"))
REL = lambda p: os.path.relpath(p, ROOT)
CACHE = os.path.join(OUT, ".triage.json")
BUDGET = float(os.environ.get("DASHBOARD_TRIAGE_SECONDS", 40))   # replay time per render; the rest waits for the next run, so the page stays fresh
e = html.escape

def read_stats(path):
    d = {}
    try:
        for line in open(path):
            k, _, v = line.partition(":")
            d[k.strip()] = v.strip()
    except OSError:
        pass
    return d

def num(d, k):
    try: return float(d.get(k, 0))
    except ValueError: return 0.0

def fmt_dur(s):
    s = int(s); return f"{s//86400}d {s%86400//3600:02d}:{s%3600//60:02d}:{s%60:02d}"

def triage(path):
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:symbolize=1")
    try:
        p = subprocess.run([HARNESS], stdin=open(path, "rb"), capture_output=True, text=True, timeout=30, env=env)
        out, rc = p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        return {"summary": "timeout on replay (>30 s)", "stack": ""}
    except OSError:
        return None        # file vanished between listing and replay; not cached, retried next run
    m = re.search(r"SUMMARY: AddressSanitizer: (.*)", out)
    stack = re.findall(r"^\s+#\d+ .*$", out, re.M)[:8]
    return {"summary": m.group(1) if m else f"no ASan report (exit code {rc})", "stack": "\n".join(stack)}


def sha1(path):
    return hashlib.sha1(open(path, "rb").read()).hexdigest()

def save_json(obj, path):
    tmp = path + ".tmp"; json.dump(obj, open(tmp, "w")); os.replace(tmp, path)

def read_session():
    try: return json.load(open(os.path.join(OUT, "session.json")))
    except (OSError, ValueError): return None

def archived_rounds():
    """Earlier rounds moved to out/archive/roundN: last-known numbers from their fuzzer_stats (only the last AFL session of that round)."""
    rows = []
    for d in sorted(glob.glob(os.path.join(OUT, "archive", "round*"))):
        st = [read_stats(f) for f in glob.glob(os.path.join(d, "*", "fuzzer_stats"))]
        rows.append({"name": os.path.basename(d), "instances": len(st), "execs": sum(num(x, "execs_done") for x in st),
                     "crash_files": len(glob.glob(os.path.join(d, "*", "crashes", "id:*"))),
                     "hang_files": len(glob.glob(os.path.join(d, "*", "hangs", "id:*"))),
                     "edges": max((num(x, "edges_found") for x in st), default=0),
                     "run_time": max((num(x, "run_time") for x in st), default=0)})
    return rows

def findings_summary():
    base = os.path.join(VDIR, CAMPAIGN, "findings"); rows = []
    for d in sorted(glob.glob(os.path.join(base, "*"))):
        if os.path.isdir(d): rows.append((os.path.basename(d), len(glob.glob(os.path.join(d, "*")) ) - (1 if os.path.exists(os.path.join(d, "NOTES.md")) else 0)))
    return rows, os.path.exists(os.path.join(base, "README.md"))

def svg_chart(series, title, ylabel):
    """series: list of (label, [(t, y)]) -> inline SVG polylines."""
    W, H, L, B = 760, 220, 55, 25
    pts = [p for _, s in series for p in s]
    if not pts: return f"<p class=muted>{e(title)}: no data yet</p>"
    tmax = max(t for t, _ in pts) or 1; ymax = max(y for _, y in pts) or 1
    sx = lambda t: L + (W - L - 10) * t / tmax
    sy = lambda y: H - B - (H - B - 15) * y / ymax
    g = [f'<text x="{L}" y="12" class=t>{e(title)} ({e(ylabel)}), max {ymax:,.0f}</text>',
         f'<line x1="{L}" y1="{H-B}" x2="{W-10}" y2="{H-B}" class=ax /><line x1="{L}" y1="15" x2="{L}" y2="{H-B}" class=ax />',
         f'<text x="{L}" y="{H-8}" class=t>0</text><text x="{W-60}" y="{H-8}" class=t>{fmt_dur(tmax)}</text>']
    for label, s in series:
        if len(s) < 2: continue
        step = max(1, len(s) // 400)
        d = " ".join(f"{sx(t):.1f},{sy(y):.1f}" for t, y in s[::step])
        g.append(f'<polyline points="{d}" class=ln><title>{e(label)}</title></polyline>')
    return f'<svg viewBox="0 0 {W} {H}" width="100%">{"".join(g)}</svg>'

def main():
    inst = sorted(d for d in glob.glob(os.path.join(OUT, "*")) if os.path.isfile(os.path.join(d, "fuzzer_stats")))
    stats = {os.path.basename(d): read_stats(os.path.join(d, "fuzzer_stats")) for d in inst}
    try: cache = json.load(open(CACHE))
    except (OSError, ValueError): cache = {}
    crashes = sorted(f for d in inst for f in glob.glob(os.path.join(d, "crashes*", "id:*")))
    # Keyed by content, not path: AFL++ renames crashes/ to crashes.<date> on every resume, which would re-replay everything.
    keys = {}
    for f in crashes:
        try: keys[os.path.relpath(f, OUT)] = sha1(f)
        except OSError: pass                                            # vanished (e.g. a sync renamed it); seen next run
    for k in [k for k in cache if "/" in k]:                            # migrate old path-keyed entries whose file is still there
        v = cache.pop(k)
        try: cache.setdefault(sha1(os.path.join(OUT, k)), v)
        except OSError: pass
    deadline = time.time() + BUDGET
    for k, ch in keys.items():
        if ch in cache: continue
        if time.time() > deadline: break
        r = triage(os.path.join(OUT, k))
        if r: cache[ch] = r; save_json(cache, CACHE)                    # saved per replay, so an interrupted run keeps its progress
    save_json(cache, CACHE)
    untriaged = sum(1 for ch in keys.values() if ch not in cache)
    hangs = sorted(f for d in inst for f in glob.glob(os.path.join(d, "hangs*", "id:*")))
    running = int(subprocess.run(["pgrep", "-c", "afl-fuzz"], capture_output=True, text=True).stdout.strip() or 0)
    try: remote = json.load(open(os.path.join(OUT, ".remote.json")))        # written by remote-fuzz.sh on every pull
    except (OSError, ValueError): remote = None
    if remote and time.time() - remote["synced"] < 300: running = remote["running"]   # fuzzers run on the server; fresh sync = trustworthy count
    now = time.time()
    tot = lambda k: sum(num(s, k) for s in stats.values())
    best = lambda k: max((num(s, k) for s in stats.values()), default=0)
    uniq = {}
    for k, ch in keys.items():
        if ch in cache: uniq.setdefault(cache[ch]["summary"], []).append(k)

    where = f" (on {remote['host'].split('@')[-1]}, synced {time.strftime('%H:%M', time.localtime(remote['synced']))})" if remote else ""
    cards = [("Instances running" + where, f"{running} / {len(stats)}"), ("Run time", fmt_dur(best("run_time"))),
             ("Total execs", f"{tot('execs_done'):,.0f}"), ("Execs/s (sum)", f"{tot('execs_per_sec'):,.0f}"),
             ("Unique crash signatures", len(uniq)), ("Crash files", f"{len(crashes)}" + (f" ({untriaged} not replayed yet)" if untriaged else "")), ("Hang files", len(hangs)),
             ("Max edges found", f"{best('edges_found'):,.0f}"), ("Best corpus (one inst.)", f"{best('corpus_count'):,.0f}"),
             ("Max cycles done", f"{best('cycles_done'):,.0f}"), ("Seeds", len(SEEDS))]
    h = ["<!doctype html><html><head><meta charset=utf-8><meta http-equiv=refresh content=60>",
         f"<title>Fuzz dashboard {e(NAME)}</title><style>",
         ":root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--card:#f3f4f6;--bd:#d6d8dc;--acc:#2563eb;--bad:#b91c1c}",
         "@media(prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e6e6e6;--mut:#999;--card:#1f2228;--bd:#343842;--acc:#6ea0ff;--bad:#ff7b72}}",
         "body{font:14px system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:16px 20px;max-width:1100px}",
         "h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}.muted{color:var(--mut)}",
         ".cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}",
         ".card{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:8px 10px}.card b{display:block;font-size:20px}",
         "table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--bd);padding:4px 8px;text-align:right}",
         "th:first-child,td:first-child{text-align:left}pre{background:var(--card);padding:8px;overflow:auto;font-size:12px;border-radius:6px}",
         ".bad{color:var(--bad)}.t{font-size:11px;fill:var(--mut)}.ax{stroke:var(--bd)}.ln{fill:none;stroke:var(--acc);stroke-width:1;opacity:.6}",
         "</style></head><body>",
         f"<h1>Fuzzing campaign {e(NAME)}</h1><div class=muted>Generated {time.strftime('%Y-%m-%d %H:%M:%S')} · reloads every 60 s · output: {e(OUT)}</div>",
         "<h2>Current run</h2><div class=cards>" + "".join(f"<div class=card>{e(str(a))}<b>{e(str(b))}</b></div>" for a, b in cards) + "</div>"]


    # ---- campaign totals across all AFL sessions (session.json) and earlier rounds -------------------------------------------------
    sess = read_session(); rounds = archived_rounds()
    h.append("<h2>Campaign total (all AFL sessions, not just the current run)</h2>")
    if sess:
        cum = sess.get("cumulative", {}); segs = sess.get("segments", []) + ([sess["current"]] if sess.get("current") else [])
        first = min((x["start_epoch"] for x in segs), default=0)
        cards2 = [("Sessions recorded", cum.get("segments", 0)), ("CPU hours (recorded)", f"{cum.get('cpu_hours', 0):.1f}"),
                  ("Instance-hours (recorded)", f"{cum.get('instance_wall_hours', 0):.1f}"), ("Execs (recorded)", f"{cum.get('execs', 0):,}"),
                  ("First recorded start", time.strftime('%m-%d %H:%M', time.localtime(first)) if first else "—"),
                  ("Earlier rounds archived", len(rounds))]
        h.append("<div class=cards>" + "".join(f"<div class=card>{e(str(a))}<b>{e(str(b))}</b></div>" for a, b in cards2) + "</div>")
        h.append("<p class=muted>Recorded = sessions captured by session-snapshot.py. AFL++ resets execs_done on every restart, so anything before the first snapshot is listed below as unrecorded, not guessed.</p>")
        h.append("<table><tr><th>session start<th>end<th>how it ended<th>inst.<th>CPU h<th>execs<th>crashes<th>hangs<th>max edges<th>harness</tr>")
        for x in segs:
            how = x.get("reason") or x.get("mode", "")
            if x is sess.get("current"): how = "running now"
            h.append(f"<tr><td>{e(x['start_iso'])}<td>{e(x['end_iso'])}<td>{e(str(how))}<td>{x['instances']}<td>{x['cpu_hours']:.2f}<td>{x['execs']:,}"
                     f"<td>{x['saved_crashes']}<td>{x['saved_hangs']}<td>{x['max_edges']:,}<td>{e((x.get('harness_git') or '')[:60])}</tr>")
        h.append("</table>")
        for u in sess.get("unrecorded", []): h.append(f"<p class=muted><b>Unrecorded:</b> {e(u['what'])} — {e(u['note'])}</p>")
    else:
        h.append("<p class=muted>No session.json yet (run session-snapshot.py).</p>")
    if rounds:
        h.append("<h3>Earlier rounds (archived)</h3><table><tr><th>round<th>instances<th>last-session execs<th>crash files<th>hang files<th>max edges<th>last-session run time</tr>")
        for r in rounds:
            h.append(f"<tr><td>{e(r['name'])}<td>{r['instances']}<td>{r['execs']:,.0f}<td>{r['crash_files']}<td>{r['hang_files']}<td>{r['edges']:,.0f}<td>{fmt_dur(r['run_time'])}</tr>")
        h.append("</table>")
    frows, has_readme = findings_summary()
    if frows:
        h.append(f"<h3>Findings collected for review ({e(REL(os.path.join(VDIR, CAMPAIGN, "findings")))}/)</h3><table><tr><th>folder<th>input files</tr>" + "".join(f"<tr><td>{e(n)}<td>{c}</tr>" for n, c in frows) + "</table>")
        if has_readme: h.append(f"<p class=muted>Summary and verification table: {e(REL(os.path.join(VDIR, CAMPAIGN, "findings", "README.md")))}</p>")

    h.append("<h2>Current run: crashes (unique by ASan summary)</h2>")
    if not uniq: h.append("<p class=muted>No crashes saved yet.</p>")
    for summ, files in sorted(uniq.items(), key=lambda x: -len(x[1])):
        h.append(f"<p class=bad><b>{len(files)}×</b> {e(summ)}</p><pre>{e(cache[keys[files[0]]]['stack'])}\n\nreplay: ASAN_OPTIONS=detect_leaks=0:symbolize=1 {e(REL(HARNESS))} &lt; {e(REL(os.path.join(OUT, files[0])))}</pre>")
        h.append("<details><summary class=muted>all files</summary><pre>" + e("\n".join(files)) + "</pre></details>")

    h.append("<h2>Hangs</h2>")
    h.append(f"<p>{len(hangs)} hang files (inputs exceeding the 1 s timeout; not replayed automatically — a replay can take &gt;10 s each). Newest:</p>")
    h.append("<pre>" + e("\n".join(os.path.relpath(f, OUT) for f in sorted(hangs, key=os.path.getmtime)[-10:])) + "</pre>")

    series_e, series_x = [], []
    for name in stats:
        rows = []
        try:
            for line in open(os.path.join(OUT, name, "plot_data")):
                if line.startswith("#"): continue
                p = [x.strip() for x in line.split(",")]
                rows.append((float(p[0]), float(p[12]), float(p[10]) if p[10].replace('.', '', 1).isdigit() else 0))
        except (OSError, ValueError, IndexError): pass
        series_e.append((name, [(t - (rows[0][0] if rows else 0), ed) for t, ed, _ in rows]))
        series_x.append((name, [(t - (rows[0][0] if rows else 0), x) for t, _, x in rows]))
    h.append("<h2>Coverage over time</h2>" + svg_chart(series_e, "edges found per instance", "edges"))
    h.append("<h2>Throughput over time</h2>" + svg_chart(series_x, "execs/s per instance", "execs/s"))

    h.append("<h2>Instances</h2><table><tr><th>instance<th>run time<th>execs<th>execs/s<th>corpus<th>pending<th>cycles<th>edges<th>stability<th>crashes<th>hangs<th>last find</tr>")
    for n, s in stats.items():
        lf = num(s, "last_find"); ago = fmt_dur(now - lf) + " ago" if lf else "—"
        h.append(f"<tr><td>{e(n)}<td>{fmt_dur(num(s,'run_time'))}<td>{num(s,'execs_done'):,.0f}<td>{num(s,'execs_per_sec'):,.0f}"
                 f"<td>{num(s,'corpus_count'):,.0f}<td>{num(s,'pending_total'):,.0f}<td>{num(s,'cycles_done'):,.0f}<td>{num(s,'edges_found'):,.0f}"
                 f"<td>{e(s.get('stability','?'))}<td>{num(s,'saved_crashes'):,.0f}<td>{num(s,'saved_hangs'):,.0f}<td>{e(ago)}</tr>")
    h.append("</table>")
    cmd = next((s.get("command_line", "") for s in stats.values()), "")
    h.append(f"<h2>Setup</h2><pre>{e(cmd)}\nAFL++ {e(next((s.get('afl_version','') for s in stats.values()), ''))}   target: {e(NAME)}   harness: {e(REL(HARNESS))}</pre>")
    h.append("</body></html>")
    tmp = os.path.join(OUT, "dashboard.html.tmp")
    open(tmp, "w").write("\n".join(h)); os.replace(tmp, os.path.join(OUT, "dashboard.html"))

if __name__ == "__main__":
    main()
