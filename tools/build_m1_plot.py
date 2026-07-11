#!/usr/bin/env python3
"""Build the M1-start (full-range) steps-vs-tokens plot data, full-range:
y starts at 0, no baseline offset.

Usage: build_m1_plot.py [BUDGET_TOKENS]   # default 50000000 (also use 100000000)
Writes /tmp/m1_runs_<N>M.json for plot_steps_vs_tokens.py.

Run filter: keep only runs that ran >=75% of the budget AND cleared >=1 flag.
"""
import glob, os, sys, json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from inspect_to_runs import convert
from inspect_ai.log import read_eval_log

LOGDIR = "inspect/nilgiri/logs"
BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 50_000_000.0
OUT_JSON = f"/tmp/m1_runs_{int(BUDGET//1_000_000)}M.json"
BEST_FOR = {"gpt-5.6-sol"}  # only this model shows a mean->best band


def cleared_of(h):
    try:
        for s in (h.reductions or []):
            for samp in s.samples:
                md = samp.metadata or {}
                if "total_cleared" in md:
                    return md["total_cleared"]
    except Exception:
        pass
    return 0


# 1) select M1-start (full-range), success, full-budget real runs
files = []
for f in sorted(glob.glob(os.path.join(LOGDIR, "*.eval"))):
    try:
        h = read_eval_log(f, header_only=True)
    except Exception:
        continue
    if h.status != "success":
        continue
    sm = str((h.eval.task_args or {}).get("start_milestone") or "M1").upper()
    if sm != "M1":
        continue
    if getattr(h.eval.config, "token_limit", None) != BUDGET:
        continue
    mu = getattr(h.stats, "model_usage", {}) or {}
    used = sum((getattr(v, "total_tokens", 0) or 0) for v in mu.values())
    if used < 0.75 * BUDGET or cleared_of(h) < 1:
        continue
    files.append(f)
print(f"selected {len(files)} M1-start {int(BUDGET//1e6)}M real-budget runs", file=sys.stderr)

# 2) convert (full parse of selected files only)
data = convert(files, title="", config_limit=int(BUDGET))

# 3) normalize hyphen<->dot opus slugs
def norm(m):
    if m.startswith("claude-opus-4-"):
        m = "claude-opus-4." + m[len("claude-opus-4-"):]
    return m
for r in data["runs"]:
    r["model"] = norm(r["model"])

# 4) latest 3 runs per model
by = defaultdict(list)
for r in data["runs"]:
    by[r["model"]].append(r)
kept = []
for m, rs in by.items():
    rs.sort(key=lambda r: r.get("_started", ""), reverse=True)
    kept.extend(rs[:3])
runs = sorted(kept, key=lambda r: (r["model"], r.get("_started", "")))

# 5) clamp endpoints to the common budget (no baseline offset -- full range)
for r in runs:
    out, ylast = [], 0
    for t, y in (r.get("trace") or []):
        if t <= BUDGET:
            out.append([float(t), y]); ylast = y
        else:
            break
    if not out or out[-1][0] < BUDGET:
        out.append([BUDGET, ylast])
    r["trace"] = out
    r["token_limit"] = BUDGET

# 6) for each band model, add a "<model> (best)" series (label only, no line)
# marking its best run at the band's upper edge.
import copy
for m in BEST_FOR:
    m_runs = [r for r in runs if r["model"] == m]
    if not m_runs:
        continue
    best = max(m_runs, key=lambda r: r.get("_cleared", 0))
    br = copy.deepcopy(best)
    br["model"] = f"{m} (best)"
    runs.append(br)

data["runs"] = runs
data["band_models"] = sorted(BEST_FOR)
json.dump(data, open(OUT_JSON, "w"), indent=2)

print(f"wrote {OUT_JSON}")
agg = defaultdict(lambda: [0, 0])
for r in runs:
    agg[r["model"]][0] += 1
    agg[r["model"]][1] = max(agg[r["model"]][1], r.get("_cleared", 0))
for m, (n, b) in sorted(agg.items()):
    print(f"  {m:24} runs={n}  best_cleared={b}")
