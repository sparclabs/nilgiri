#!/usr/bin/env python3
"""
Generate a "Figure 1"-style plot: average attack steps completed vs. cumulative
tokens (log x-axis), one line per model, with a shaded band across runs and
horizontal gridlines marking milestone step counts.

INPUT FORMAT (JSON) -- a single object:

{
  "title": "The Last Ones",                # optional, plot title
  "max_steps": 32,                          # optional, y-axis upper bound
  "milestones": {                           # optional, name -> step number
    "M1: Reconnaissance": 4,
    "M2: Lateral movement": 7,
    ...
  },
  "runs": [
    {
      "model": "Opus 4.6",
      "step_tokens": [12000, 90000, 210000, ...],  # cumulative tokens at which
                                                   # step 1, 2, 3, ... completed
      "token_limit": 100000000                     # optional; where the run ended
    },
    ...
  ]
}

A run only contributes to a token budget t if t <= its token_limit (or its last
completed step's token count) -- runs are not extrapolated past where they ran.

USAGE:
  python3 plot_steps_vs_tokens.py runs.json -o figure1.png
  python3 plot_steps_vs_tokens.py --demo -o demo.png     # built-in synthetic data
"""

import argparse
import json
import sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import (
    LogLocator, FuncFormatter, MaxNLocator, MultipleLocator)


def steps_completed(step_tokens, t):
    """Number of steps completed by cumulative-token budget t (step_tokens sorted)."""
    return int(np.searchsorted(np.asarray(step_tokens), t, side="right"))


def run_reach(run):
    """Largest token budget at which a run still has data."""
    if run.get("trace"):
        return float(run["trace"][-1][0])
    if run.get("token_limit") is not None:
        return float(run["token_limit"])
    st = run.get("step_tokens") or []
    return float(st[-1]) if st else 0.0


def run_curve(run):
    """Return (tokens, steps) arrays describing steps-completed over tokens.

    Prefers the dense per-event `trace`; falls back to the sparse per-step
    `step_tokens` (where steps go 1,2,3,... at each captured flag).
    """
    if run.get("trace"):
        tr = run["trace"]
        return np.array([p[0] for p in tr]), np.array([p[1] for p in tr])
    st = np.array(sorted(run.get("step_tokens") or []))
    return st, np.arange(1, len(st) + 1)


def steps_at(tokens, steps, t):
    """Steps completed by budget t given a (tokens, steps) step curve."""
    idx = int(np.searchsorted(tokens, t, side="right")) - 1
    return int(steps[idx]) if idx >= 0 else 0


def aggregate(runs, x_grid):
    """For each x in x_grid, return (mean, lo, hi) of steps across runs that reach x.

    Returns arrays aligned with x_grid; entries where no run reaches x are NaN.
    """
    mean = np.full(x_grid.shape, np.nan)
    lo = np.full(x_grid.shape, np.nan)
    hi = np.full(x_grid.shape, np.nan)
    curves = [run_curve(r) for r in runs]
    reaches = [run_reach(r) for r in runs]
    for i, t in enumerate(x_grid):
        vals = [
            steps_at(tok, stp, t)
            for (tok, stp), reach in zip(curves, reaches)
            if t <= reach
        ]
        if vals:
            mean[i] = np.mean(vals)
            lo[i] = np.min(vals)
            hi[i] = np.max(vals)
    return mean, lo, hi


def gaussian_smooth(y, sigma):
    """Gaussian-smooth a 1-D array with edge renormalization (no scipy)."""
    if sigma <= 0 or len(y) < 3:
        return y
    radius = max(1, int(round(3 * sigma)))
    t = np.arange(-radius, radius + 1)
    k = np.exp(-(t * t) / (2.0 * sigma * sigma))
    k /= k.sum()
    num = np.convolve(y, k, mode="same")
    den = np.convolve(np.ones_like(y), k, mode="same")  # normalize at edges
    return num / den


def token_formatter(x, _pos):
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            v = x / div
            return f"{v:g}{suf}"
    return f"{x:g}"


# Fixed per-model colors (tab10 indices) so every figure uses the same color for
# the same model, matching the startM1 plots. A "<model> (best)" line inherits
# its base model's color. Models not listed fall back to positional coloring.
MODEL_COLOR_IDX = {
    "gpt-5.5": 0,              # blue
    "claude-opus-4.6": 1,     # orange
    "claude-opus-4.7": 2,     # green
    "claude-opus-4.8": 3,     # red
    "deepseek-v4-pro": 4,     # purple
    "glm-5.2": 5,             # brown
    "gemini-3.1-pro-preview": 6,  # pink
    "gpt-5.6-sol": 7,         # grey (overridden by MODEL_COLOR_HEX below)
    "kimi-k3": 9,             # cyan
}

# Explicit hex overrides take precedence over MODEL_COLOR_IDX -- use for a
# headline model that needs a brighter, higher-contrast color than tab10 offers
# (especially for its shaded band on the dark theme).
MODEL_COLOR_HEX = {
    "gpt-5.6-sol": "#ffd21e",  # bright gold -- headline model, visible line + band
}


def _base_model(name):
    return name[:-len(" (best)")] if name.endswith(" (best)") else name


def plot(data, out_path, log_x=True, smooth=0.0, markers=False, ymin=0.0,
         legend=True, label_fontsize=11, figsize=(8, 6), dpi=150,
         bbox_tight=False, ymax=None, ytick_step=None, dark=False,
         tick_fontsize=None, axis_fontsize=None):
    if dark:
        plt.style.use("dark_background")
    # Gridline / milestone-label greys: light-on-dark vs dark-on-light.
    grid_c = "0.4" if dark else "0.3"
    ms_line_c = "0.4" if dark else "0.3"
    ms_text_c = "0.6" if dark else "0.45"
    runs = data["runs"]
    # Optional: only these (base) models get a shaded band spanning mean->best
    # (max) run; everyone else is a bare mean line. If absent, all models get a
    # full min-max band.
    band_models = data.get("band_models")
    models = []
    for r in runs:  # preserve first-seen order
        if r["model"] not in models:
            models.append(r["model"])

    # Every token value that appears in any trajectory -- so the x-grid has a
    # sample at every point in the trace, not just a synthetic spacing.
    trace_tokens = sorted({
        p[0] for r in runs for p in (r.get("trace") or []) if p[0] > 0
    })
    all_tokens = trace_tokens + [
        t for r in runs for t in (r.get("step_tokens") or []) if t > 0
    ] + [run_reach(r) for r in runs]
    all_tokens = [t for t in all_tokens if t and t > 0]
    if not all_tokens:
        sys.exit("No positive token values found in runs.")
    x_min, x_max = min(all_tokens), max(all_tokens)
    if trace_tokens:
        # Use the real trace points as the grid (dense, fine-grained).
        x_grid = np.array(trace_tokens, dtype=float)
        if not log_x and x_grid[0] > 0:
            x_grid = np.concatenate([[0.0], x_grid])
            x_min = 0
    elif log_x:
        x_grid = np.logspace(np.log10(x_min), np.log10(x_max), 400)
    else:
        x_grid = np.linspace(0, x_max, 400)
        x_min = 0

    max_steps = data.get("max_steps")
    if max_steps is None:
        max_steps = max(len(r.get("step_tokens") or []) for r in runs)
    yhi = ymax if ymax is not None else max_steps  # visible y upper bound

    fig, ax = plt.subplots(figsize=figsize)
    cmap = plt.get_cmap("tab10")

    endpoints = []  # (xe, ye, model, color) for right-edge labels
    for idx, model in enumerate(models):
        model_runs = [r for r in runs if r["model"] == model]
        mean, lo, hi = aggregate(model_runs, x_grid)
        valid = ~np.isnan(mean)
        if not valid.any():
            continue
        ym, yl, yh = mean[valid], lo[valid], hi[valid]
        if smooth:
            ym = gaussian_smooth(ym, smooth)
            yl = gaussian_smooth(yl, smooth)
            yh = gaussian_smooth(yh, smooth)
        hexc = MODEL_COLOR_HEX.get(_base_model(model))
        ci = MODEL_COLOR_IDX.get(_base_model(model))
        if hexc is not None:
            color = hexc
        elif ci is not None:
            color = cmap(ci % 10)
        else:
            color = cmap(idx % 10)
        is_best = model.endswith(" (best)")
        if band_models is None:
            ax.fill_between(x_grid[valid], yl, yh, color=color, alpha=0.15,
                            linewidth=0)  # legacy: full min-max band for all
        elif _base_model(model) in band_models and not is_best:
            # band from the base model's mean up to its best (max) run; the
            # "<model> (best)" series only contributes a label at the band's
            # upper edge -- no separate line.
            ax.fill_between(x_grid[valid], ym, yh, color=color, alpha=0.18,
                            linewidth=0)
        if not is_best:  # "(best)" gets a label only, no line
            ax.plot(x_grid[valid], ym, color=color, linewidth=2, label=model)
        if markers:  # one dot per actual trace point, per run
            for r in model_runs:
                tok, stp = run_curve(r)
                ax.plot(tok, stp, linestyle="none", marker=".", markersize=3,
                        color=color, alpha=0.5, zorder=3)
        endpoints.append((x_grid[valid][-1], ym[-1], model, color))

    # Right-edge labels, de-collided: enforce a minimum vertical gap so labels
    # of models that finish at the same step don't overlap (each gets a thin
    # leader line back to its actual endpoint).
    if endpoints:
        endpoints.sort(key=lambda e: e[1])
        gap = (yhi - ymin) * 0.045
        last = ymin - gap
        label_x = x_max + (x_max - x_min) * 0.012
        for xe, ye, model, color in endpoints:
            ly = max(ye, last + gap)
            last = ly
            ax.annotate(
                model, xy=(xe, ye), xytext=(label_x, ly), textcoords="data",
                va="center", ha="left", fontsize=label_fontsize, color=color,
                fontweight="bold", annotation_clip=False,
            )

    # milestone gridlines (skip any below the y-axis floor so their labels
    # don't float outside the plot area)
    for name, step in (data.get("milestones") or {}).items():
        if step < ymin or step > yhi:
            continue
        ax.axhline(step, color=ms_line_c, linewidth=0.7, zorder=0)
        ax.text(
            x_min, step + 0.05 * max_steps * 0.0 + 0.1, name,
            fontsize=max(8, label_fontsize - 2), color=ms_text_c,
            va="bottom", ha="left",
        )

    if log_x:
        ax.set_xscale("log")
        ax.xaxis.set_major_locator(LogLocator(base=10))
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(ymin, yhi)
    if ytick_step:
        ax.yaxis.set_major_locator(MultipleLocator(ytick_step))
    else:
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))  # whole-number steps
    ax.set_xlabel("Cumulative tokens" + (" (log)" if log_x else ""),
                  fontsize=axis_fontsize)
    ax.set_ylabel("Flags Captured", fontsize=axis_fontsize)
    if tick_fontsize:
        ax.tick_params(axis="both", labelsize=tick_fontsize)
    if data.get("title"):
        ax.set_title(data["title"])
    ax.xaxis.set_major_formatter(FuncFormatter(token_formatter))
    ax.grid(axis="x", which="major", color=grid_c, linewidth=0.6)
    if legend:
        ax.legend(loc="upper left", fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi,
                bbox_inches="tight" if bbox_tight else None)
    print(f"Wrote {out_path}")


def demo_data():
    """Synthetic runs that mimic the paper's log-linear trends."""
    rng = np.random.default_rng(0)
    models = {
        "GPT-4o": (1.7, 0.15),       # (asymptotic-ish steps at 10M, growth rate)
        "Sonnet 3.7": (5.8, 0.9),
        "Sonnet 4.5": (6.1, 1.0),
        "Opus 4.5": (7.6, 1.3),
        "Opus 4.6": (9.8, 1.7),
    }
    runs = []
    for model, (base, rate) in models.items():
        limit = 1e8 if model in ("Sonnet 4.5", "Opus 4.5", "Opus 4.6") else 1e7
        n_runs = 5 if limit == 1e8 else 10
        for _ in range(n_runs):
            # number of steps this run ultimately reaches, with variance
            total = max(1, int(round(base + rate * (np.log10(limit) - 7) * 3
                                     + rng.normal(0, 1.5))))
            # spread step completions log-uniformly across the token range
            ts = np.sort(10 ** rng.uniform(4, np.log10(limit), size=total))
            runs.append({
                "model": model,
                "step_tokens": [float(t) for t in ts],
                "token_limit": limit,
            })
    return {
        "title": "The Last Ones (synthetic demo)",
        "max_steps": 32,
        "milestones": {
            "M1: Reconnaissance": 4, "M2: Lateral movement": 7,
            "M3: Browser cred theft": 9, "M4: Wiki/cred replay": 13,
            "M5: Web app exploit": 19, "M6: C2 reverse eng.": 22,
        },
        "runs": runs,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", nargs="?", help="Path to runs JSON (see header).")
    ap.add_argument("-o", "--out", default="figure1.png", help="Output image path.")
    ap.add_argument("--demo", action="store_true", help="Use built-in synthetic data.")
    ap.add_argument("--linear", action="store_true",
                    help="Use a linear token x-axis instead of log scale.")
    ap.add_argument("--smooth", type=float, nargs="?", const=6.0, default=0.0,
                    help="Gaussian-smooth the curves. Optional sigma in grid "
                    "points (default 6 when flag given; larger = smoother).")
    ap.add_argument("--markers", action="store_true",
                    help="Draw a dot at every point in each run's trace.")
    ap.add_argument("--ymin", type=float, default=0.0,
                    help="Y-axis lower bound (e.g. 13 to start at the M5 baseline).")
    ap.add_argument("--ymax", type=float, default=None,
                    help="Y-axis upper bound (default = max_steps).")
    ap.add_argument("--ytick-step", type=float, default=None,
                    help="Spacing between y-axis ticks (e.g. 2).")
    ap.add_argument("--no-legend", action="store_true",
                    help="Omit the legend box (right-edge labels still identify lines).")
    ap.add_argument("--label-fontsize", type=float, default=11.0,
                    help="Font size for the right-edge model labels.")
    ap.add_argument("--figsize", default="8x6",
                    help="Figure size WxH in inches (e.g. 8x6).")
    ap.add_argument("--dpi", type=float, default=150, help="Output DPI.")
    ap.add_argument("--bbox-tight", action="store_true",
                    help="Save with bbox_inches='tight' (crop to content).")
    ap.add_argument("--dark", action="store_true",
                    help="Dark theme (black background, light text/gridlines).")
    ap.add_argument("--tick-fontsize", type=float, default=None,
                    help="Font size for x/y tick labels (default: rcParams).")
    ap.add_argument("--axis-fontsize", type=float, default=None,
                    help="Font size for x/y axis titles (default: rcParams).")
    args = ap.parse_args()
    fw, fh = (float(v) for v in args.figsize.lower().split("x"))

    if args.demo:
        data = demo_data()
    elif args.data:
        with open(args.data) as f:
            data = json.load(f)
    else:
        ap.error("provide a data JSON file or --demo")
    plot(data, args.out, log_x=not args.linear, smooth=args.smooth,
         markers=args.markers, ymin=args.ymin, legend=not args.no_legend,
         label_fontsize=args.label_fontsize, figsize=(fw, fh), dpi=args.dpi,
         bbox_tight=args.bbox_tight, ymax=args.ymax, ytick_step=args.ytick_step,
         dark=args.dark, tick_fontsize=args.tick_fontsize,
         axis_fontsize=args.axis_fontsize)


if __name__ == "__main__":
    main()
