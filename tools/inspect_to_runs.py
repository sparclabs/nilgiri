#!/usr/bin/env python3
"""
Convert Inspect AI `.eval` logs for the "Last Ones" range into the runs JSON
consumed by plot_steps_vs_tokens.py.

Each `.eval` file is one run (sample). For every cleared flag we reconstruct the
cumulative model tokens spent at the moment that flag first appeared, yielding
per run: step_tokens (one cumulative-token mark per cleared step) and token_limit
(the run's final cumulative total). A flag that never appears textually (only
written to /tmp/flags.txt) falls back to the run's final token total.

Usage:
    python3 tools/inspect_to_runs.py inspect/nilgiri/logs -o runs.json
    python3 tools/inspect_to_runs.py inspect/nilgiri/logs/*.eval -o runs.json
"""

import argparse
import bisect
import glob
import json
import os
import re
import sys

import yaml
from inspect_ai.log import read_eval_log

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
SCORER = "per_step_flag_scorer"

# Descriptive milestone names, matching the grey gridline labels in Figure 1 of
# the AISI paper ("Measuring AI Agents' Progress on Multi-Step Cyber Attack
# Scenarios"). Keyed by the manifest's milestone id.
MILESTONE_NAMES = {
    "M1": "M1: Reconnaissance",
    "M2": "M2: Lateral movement",
    "M3": "M3: Browser credential theft",
    "M4": "M4: Wiki exploit and credential replay",
    "M5": "M5: Web app exploit and privilege escalation",
    "M6": "M6: C2 reverse engineering",
    "M7": "M7: Advanced persistence",
    "M8": "M8: Infrastructure compromise",
    "M9": "M9: Full network takeover",
}


def find_manifest():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.join(here, "..", "flags", "manifest.yaml"),
        "flags/manifest.yaml",
    ):
        if os.path.exists(cand):
            return cand
    # last resort: search upward
    d = here
    for _ in range(5):
        m = os.path.join(d, "flags", "manifest.yaml")
        if os.path.exists(m):
            return m
        d = os.path.dirname(d)
    sys.exit("Could not locate flags/manifest.yaml")


def load_manifest():
    doc = yaml.safe_load(open(find_manifest()))
    flags = doc["flags"]  # ordered list
    uuid2step = {e["uuid"].lower(): e["id"] for e in flags}
    # milestone -> count of steps, in milestone order of first appearance
    ms_order, ms_count = [], {}
    for e in flags:
        m = e["milestone"]
        if m not in ms_count:
            ms_order.append(m)
            ms_count[m] = 0
        ms_count[m] += 1
    # cumulative step number where each milestone *starts* (its first step),
    # so the gridline + descriptive label sit at the milestone boundary, as in
    # Figure 1 of the paper.
    milestones, cum = {}, 0
    for m in ms_order:
        label = MILESTONE_NAMES.get(m, m)
        milestones[label] = cum  # steps completed before this milestone begins
        cum += ms_count[m]
    steps_order = [e["id"] for e in flags]  # canonical step order (m1.s1 first)
    return uuid2step, set(uuid2step), milestones, len(flags), steps_order


def short_model(name):
    """openrouter/anthropic/claude-opus-4.7 -> claude-opus-4.7"""
    return name.rsplit("/", 1)[-1]


def iter_uuids(text, man_uuids):
    for m in UUID_RE.findall(text):
        m = m.lower()
        if m in man_uuids:
            yield m


def reconstruct(sample, uuid2step, man_uuids):
    """Walk the event stream once.

    Returns (final_tokens, {step_id: tokens_at_first_sighting}, checkpoints)
    where checkpoints is the running cumulative-token total recorded after every
    model event -- i.e. one x-sample per point in the trajectory.
    """
    cum = 0
    seen = {}  # uuid -> cum tokens at first sight
    checkpoints = []  # cumulative tokens after each model event
    for e in sample.events:
        if e.event == "model":
            usage = getattr(getattr(e, "output", None), "usage", None)
            if usage and usage.total_tokens:
                cum += usage.total_tokens
            try:
                completion = e.output.completion or ""
            except Exception:
                completion = ""
            for u in iter_uuids(completion, man_uuids):
                seen.setdefault(u, cum)
            checkpoints.append(cum)
        elif e.event == "tool":
            blob = ""
            args = getattr(e, "arguments", None)
            if isinstance(args, dict):
                blob += " ".join(str(v) for v in args.values())
            res = getattr(e, "result", None)
            if isinstance(res, str):
                blob += " " + res
            for u in iter_uuids(blob, man_uuids):
                seen.setdefault(u, cum)
    return cum, {uuid2step[u]: t for u, t in seen.items()}, checkpoints


def convert(paths, title, models=None, config_limit=None, last_per_model=None):
    """Build the runs document.

    Filters (all optional):
      models          : keep only these short model names (set).
      config_limit    : keep only runs whose eval config token_limit == this.
      last_per_model  : after filtering, keep only the N most recent runs
                        (by log start time) per model.
    """
    uuid2step, man_uuids, milestones, total_steps, steps_order = load_manifest()
    step_index = {sid: i for i, sid in enumerate(steps_order)}
    candidates = []  # (sort_key, run_dict)
    for f in paths:
        try:
            log = read_eval_log(f)
        except Exception as exc:
            print(f"  skip {f}: {exc}", file=sys.stderr)
            continue
        if log.status != "success" or not log.samples:
            print(f"  skip {os.path.basename(f)}: status={log.status}", file=sys.stderr)
            continue
        model = short_model(log.eval.model)
        if models and model not in models:
            continue
        cfg_limit = getattr(log.eval.config, "token_limit", None)
        if config_limit is not None and cfg_limit != config_limit:
            continue
        # log start time -> recency sort key (fall back to filename, which is
        # an ISO timestamp prefix).
        started = getattr(log.eval, "created", None) or os.path.basename(f)
        for s in log.samples:
            score = (s.scores or {}).get(SCORER)
            if not score:
                continue
            md = score.metadata or {}
            cleared = list(md.get("cleared_steps") or [])
            final_tokens, step_to_tok, checkpoints = reconstruct(
                s, uuid2step, man_uuids)
            if not final_tokens:
                continue
            # Credit prerequisite flags: a step counts if it was captured OR any
            # later step (in canonical manifest order) was captured -- reaching a
            # downstream flag implies its upstream prerequisites were met even if
            # their UUID was never surfaced. A captured step is marked at its
            # first-sighting token (fallback: final tokens); a filled-in step is
            # marked at the earliest subsequent captured step's token time, i.e.
            # the point by which it was necessarily already done.
            cap_mark = {st: step_to_tok.get(st, final_tokens)
                        for st in cleared if st in step_index}
            credited = dict(cap_mark)
            if cap_mark:
                max_idx = max(step_index[st] for st in cap_mark)
                for st, i in step_index.items():
                    if i <= max_idx and st not in credited:
                        later = [m for c, m in cap_mark.items()
                                 if step_index[c] > i]
                        credited[st] = min(later) if later else final_tokens
            n_cleared = len(credited)
            marks = sorted(credited.values())
            # Dense trace: steps-completed at every model event (token
            # checkpoint), so flat token-spend stretches are represented too.
            trace = [[float(t), int(bisect.bisect_right(marks, t))]
                     for t in checkpoints]
            if not trace or trace[-1][0] < final_tokens:
                trace.append([float(final_tokens), n_cleared])
            candidates.append((str(started), {
                "model": model,
                "step_tokens": [float(t) for t in marks],
                "trace": trace,
                "token_limit": float(final_tokens),
                "_src": os.path.basename(f),
                "_started": str(started),
                "_config_limit": cfg_limit,
                "_cleared": n_cleared,
            }))

    if last_per_model:
        kept, per = [], {}
        for key, run in sorted(candidates, key=lambda kr: kr[0], reverse=True):
            m = run["model"]
            if per.get(m, 0) < last_per_model:
                per[m] = per.get(m, 0) + 1
                kept.append((key, run))
        candidates = kept

    runs = [run for _, run in sorted(candidates, key=lambda kr: kr[0])]
    return {
        "title": title,
        "max_steps": total_steps,
        "milestones": milestones,
        "runs": runs,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="A logs directory and/or .eval files.")
    ap.add_argument("-o", "--out", default="runs.json", help="Output JSON path.")
    ap.add_argument("--title", default="The Last Ones (Inspect logs)")
    ap.add_argument("--models", help="Comma-separated short model names to keep "
                    "(e.g. claude-opus-4.8,gpt-5.5).")
    ap.add_argument("--config-limit", type=int, help="Keep only runs whose eval "
                    "config token_limit equals this (e.g. 50000000).")
    ap.add_argument("--last-per-model", type=int, help="Keep only the N most "
                    "recent runs per model after filtering.")
    args = ap.parse_args()

    files = []
    for p in args.paths:
        if os.path.isdir(p):
            files += sorted(glob.glob(os.path.join(p, "*.eval")))
        else:
            files += sorted(glob.glob(p))
    if not files:
        sys.exit("No .eval files found.")

    models = set(m.strip() for m in args.models.split(",")) if args.models else None
    data = convert(files, args.title, models=models,
                   config_limit=args.config_limit,
                   last_per_model=args.last_per_model)
    with open(args.out, "w") as fh:
        json.dump(data, fh, indent=2)

    # summary
    from collections import Counter
    by_model = Counter(r["model"] for r in data["runs"])
    print(f"Wrote {args.out}: {len(data['runs'])} runs across {len(by_model)} models")
    for r in data["runs"]:
        print(f"  {r['model']:18s} {r['_src'][:19]}  limit={r['_config_limit']} "
              f"used={int(r['token_limit'])} cleared={r['_cleared']}")


if __name__ == "__main__":
    main()
