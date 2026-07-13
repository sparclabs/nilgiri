#!/usr/bin/env python3
"""
Validate docs/leaderboard/results.json against docs/leaderboard/schema.json
plus semantic rules the JSON Schema alone can't express.

This is the file that actually gates leaderboard PRs: results.json is edited
by hand (see the "Updating the leaderboard" section of the project docs), so
this script -- not generate_results.py -- is what CI runs against a
contributor's edit.

Usage:
    python docs/scripts/validate_results.py [results.json] [schema.json]

Exits non-zero (and prints one INVALID line per problem) if anything fails.
"""

import json
import os
import sys

import yaml
from jsonschema import Draft202012Validator

HERE = os.path.dirname(os.path.abspath(__file__))
LEADERBOARD_DIR = os.path.normpath(os.path.join(HERE, "..", "leaderboard"))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

MILESTONE_ORDER = [f"M{i}" for i in range(1, 10)]

# 3 runs per model currently -> pass@3 must land on one of these fractions.
VALID_PASS_FRACTIONS = [0.0, 1 / 3, 2 / 3, 1.0]
TOLERANCE = 1e-3


def find_manifest():
    for cand in (
        os.path.join(REPO_ROOT, "flags", "manifest.yaml"),
        "flags/manifest.yaml",
    ):
        if os.path.exists(cand):
            return cand
    return None


def load_milestone_flag_counts():
    manifest_path = find_manifest()
    if not manifest_path:
        return None
    doc = yaml.safe_load(open(manifest_path, encoding="utf-8"))
    counts = {}
    for entry in doc["flags"]:
        m = entry["milestone"]
        counts[m] = counts.get(m, 0) + 1
    return counts


def close_to_any(value, options, tol=TOLERANCE):
    return any(abs(value - o) <= tol for o in options)


def validate_document(doc, flag_counts=None, schema=None):
    """Returns a list of human-readable error strings; empty means valid."""
    errors = []

    if schema is None:
        schema_path = os.path.join(LEADERBOARD_DIR, "schema.json")
        schema = json.load(open(schema_path, encoding="utf-8"))

    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"schema: {path}: {err.message}")
    if errors:
        # Structural errors make semantic checks below unreliable/crash-prone.
        return errors

    if flag_counts is None:
        flag_counts = load_milestone_flag_counts()

    benchmark = doc["benchmark"]
    declared_milestone_flags = {m["id"]: m["flags"] for m in benchmark["milestones"]}

    if flag_counts is not None:
        for mid in MILESTONE_ORDER:
            if mid not in declared_milestone_flags:
                errors.append(f"benchmark.milestones: missing entry for {mid}")
                continue
            if declared_milestone_flags[mid] != flag_counts.get(mid):
                errors.append(
                    f"benchmark.milestones[{mid}].flags = {declared_milestone_flags[mid]} "
                    f"but flags/manifest.yaml has {flag_counts.get(mid)}"
                )

    total_declared = sum(declared_milestone_flags.values())
    if benchmark["total_flags"] != total_declared:
        errors.append(f"benchmark.total_flags = {benchmark['total_flags']} but milestones sum to {total_declared}")
    if benchmark["total_milestones"] != len(benchmark["milestones"]):
        errors.append(
            f"benchmark.total_milestones = {benchmark['total_milestones']} "
            f"but {len(benchmark['milestones'])} milestones are listed"
        )

    token_budgets = benchmark["token_budgets"]
    if token_budgets != sorted(token_budgets):
        errors.append(f"benchmark.token_budgets is not ascending: {token_budgets}")
    budget_keys = [str(b) for b in token_budgets]

    for model in doc["models"]:
        label = model.get("display_name", model.get("id", "<unknown model>"))

        declared_budgets = list(model["by_token_budget"].keys())
        if sorted(declared_budgets, key=int) != sorted(budget_keys, key=int):
            errors.append(
                f"{label}: by_token_budget keys {declared_budgets} don't match benchmark.token_budgets {budget_keys}"
            )

        prev_avg_flags = prev_avg_milestones = prev_overall_pass = None
        prev_milestone_pass = {}

        for budget_key in budget_keys:
            slice_ = model["by_token_budget"].get(budget_key)
            if slice_ is None:
                continue
            where = f"budget={budget_key}"

            overall = slice_["overall"]
            avg_flags = overall["avg_flags_captured_at_3"]
            avg_milestones = overall["avg_milestones_completed_at_3"]
            overall_pass = overall["pass_at_3"]

            if not (0 <= avg_flags <= benchmark["total_flags"] + TOLERANCE):
                errors.append(f"{label}/{where}: avg_flags_captured_at_3={avg_flags} out of range")
            if not (0 <= avg_milestones <= benchmark["total_milestones"] + TOLERANCE):
                errors.append(f"{label}/{where}: avg_milestones_completed_at_3={avg_milestones} out of range")
            if not close_to_any(overall_pass, VALID_PASS_FRACTIONS):
                errors.append(f"{label}/{where}: overall pass_at_3={overall_pass} is not one of 0, 1/3, 2/3, 1")

            milestone_passes = {}
            for mid in MILESTONE_ORDER:
                if mid not in slice_["milestones"]:
                    errors.append(f"{label}/{where}: missing milestones.{mid}")
                    continue
                p3 = slice_["milestones"][mid]["pass_at_3"]
                milestone_passes[mid] = p3
                if not close_to_any(p3, VALID_PASS_FRACTIONS):
                    errors.append(f"{label}/{where}/{mid}: pass_at_3={p3} is not one of 0, 1/3, 2/3, 1")

            if milestone_passes and overall_pass > min(milestone_passes.values()) + TOLERANCE:
                errors.append(
                    f"{label}/{where}: overall pass_at_3={overall_pass} exceeds its lowest milestone pass_at_3 "
                    f"({min(milestone_passes.values())}) -- clearing everything requires clearing every milestone"
                )
            # Pass@3 must be non-increasing as the milestone chain progresses
            # (sequential dependency: clearing M5 implies having cleared M4).
            ordered = [milestone_passes[m] for m in MILESTONE_ORDER if m in milestone_passes]
            for a, b in zip(ordered, ordered[1:]):
                if b > a + TOLERANCE:
                    errors.append(f"{label}/{where}: milestone pass_at_3 increased later in the chain ({ordered})")

            # Non-decreasing as token budget grows (more budget can't undo progress).
            if prev_avg_flags is not None and avg_flags < prev_avg_flags - TOLERANCE:
                errors.append(f"{label}/{where}: avg_flags_captured_at_3 decreased vs. the previous (smaller) budget")
            if prev_avg_milestones is not None and avg_milestones < prev_avg_milestones - TOLERANCE:
                errors.append(
                    f"{label}/{where}: avg_milestones_completed_at_3 decreased vs. the previous (smaller) budget"
                )
            if prev_overall_pass is not None and overall_pass < prev_overall_pass - TOLERANCE:
                errors.append(f"{label}/{where}: overall pass_at_3 decreased vs. the previous (smaller) budget")
            for mid, p3 in milestone_passes.items():
                if mid in prev_milestone_pass and p3 < prev_milestone_pass[mid] - TOLERANCE:
                    errors.append(f"{label}/{where}/{mid}: pass_at_3 decreased vs. the previous (smaller) budget")

            prev_avg_flags, prev_avg_milestones, prev_overall_pass = avg_flags, avg_milestones, overall_pass
            prev_milestone_pass = milestone_passes

        dq = model["data_quality"]
        if dq["status"] == "estimated" and not dq.get("caveat"):
            errors.append(f"{label}: data_quality.status is 'estimated' but caveat is empty")

    return errors


def main():
    args = sys.argv[1:]
    results_path = args[0] if len(args) > 0 else os.path.join(LEADERBOARD_DIR, "results.json")
    schema_path = args[1] if len(args) > 1 else os.path.join(LEADERBOARD_DIR, "schema.json")

    doc = json.load(open(results_path, encoding="utf-8"))
    schema = json.load(open(schema_path, encoding="utf-8"))
    flag_counts = load_milestone_flag_counts()

    errors = validate_document(doc, flag_counts=flag_counts, schema=schema)

    if errors:
        for e in errors:
            print(f"INVALID: {e}", file=sys.stderr)
        print(f"\n{len(errors)} problem(s) found in {results_path}", file=sys.stderr)
        sys.exit(1)

    max_budget = str(max(int(b) for b in doc["benchmark"]["token_budgets"]))
    print(f"OK: {results_path} is valid ({len(doc['models'])} models, {doc['runs_per_model']} runs each).")
    for model in doc["models"]:
        dq = model["data_quality"]
        badge = "verified" if dq["verified"] else dq["status"]
        top = model["by_token_budget"][max_budget]["overall"]
        print(
            f"  - {model['display_name']}: avg CTFs@3={top['avg_flags_captured_at_3']}/32, "
            f"avg milestones@3={top['avg_milestones_completed_at_3']}/9 [{badge}] (at {max_budget} tokens)"
        )


if __name__ == "__main__":
    main()
