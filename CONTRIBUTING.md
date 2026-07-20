# Contributing to Nilgiri

Thanks for your interest in Nilgiri, an open-source cyber range for evaluating
the offensive-cybersecurity capabilities of AI agents. Contributions to the
**range**, the **scorer**, and the **leaderboard data** are all welcome.

There are two common ways to contribute:

1. [Get your agent on the leaderboard](#running-your-agent-and-updating-the-leaderboard)
   — run the range against your model and submit the numbers.
2. [Improve the range or tooling](#improving-the-range-or-tooling) — milestones,
   Ansible roles, the scorer, or docs.

Either way, contributions land through a **pull request** against `main`.

---

## Running your agent and updating the leaderboard

The leaderboard (`docs/leaderboard/results.json`, rendered on the site's
Leaderboard page) ranks agents by how far they get through Nilgiri's
9-milestone, 32-flag attack chain, reporting **Avg CTFs@3** and **Avg
Milestones@3** globally plus **Pass@3 per milestone**, sliced by output-token
budget. To add your agent, you run the range, produce Inspect AI eval logs, and
turn those logs into a `results.json` entry.

### 1. Stand up the range

Nilgiri runs on a Linux host with libvirt/KVM, Terraform, Packer, and Ansible.
The full prerequisites and per-milestone role documentation live in the
[repository README](README.md). The short version:

```
make tooling-check
make venv
make host-bootstrap            # one-shot: libguestfs perms, swtpm, host iptables
make terraform-init
make networks                  # 5 nets (4 victim mode=none + 1 attacker NAT)
make packer-kali               # needs a prebuilt Kali qcow2 (see kali.pkr.hcl)
make packer-winserver2022      # ~7 min; needs a Windows Server 2022 eval ISO
make packer-win11              # ~18 min; needs a Windows 11 eval ISO + swtpm
make apply                     # terraform: networks + base/COW volumes
make vms                       # define + start all 16 domains via virsh
make m1 m2 m3 m4 m5 m6 m7 m8-m9 # per-milestone Ansible provisioning
make snapshot-all              # baseline snapshots for a clean eval state
```

`make snapshot-all` captures the `clean-substrate` (pre-provisioning) and
`clean-eval` (post-provisioning) baselines. Every run should start from a clean
`clean-eval` snapshot — the range is shared mutable state (live VMs, Active
Directory, VPN), so runs cannot overlap without corrupting each other's world.

### 2. Run your agent against the range

Episodes run through [Inspect AI](https://inspect.aisi.org.uk/) via the `eval`
targets. Point `MODEL` at any Inspect-supported model id:

```
# Direct provider
make eval MODEL=anthropic/claude-opus-4-8
make eval-openai OPENAI_MODEL=openai/gpt-5.6-sol      # export OPENAI_API_KEY first

# OpenRouter (export OPENROUTER_API_KEY first)
make eval MODEL=openrouter/moonshotai/kimi-k3

# Local OpenAI-compatible endpoint (e.g. vLLM)
make eval-local VLLM_URL=http://HOST:8000/v1 VLLM_MODEL=org/model
```

Notes:

- **Budget.** Leaderboard numbers are reported per output-token budget. To
  match the published full-chain runs, use a `token_limit` of 100M and start at
  M1 (the default `start_milestone`). Pass a different limit with the token-limit
  argument if you want to publish at a smaller budget.
- **Multiple runs.** The board reports `@3` statistics, so a qualifying entry
  needs **at least 3 full-chain runs**. `make eval-multi RUNS=3 ...` (or
  `eval-openai-multi` / `eval-hf-multi`) reverts to the clean snapshot before
  each run so the samples are independent. The range's nondeterminism means
  run-to-run variance is real and expected — averaging over runs is the point.
- **Local models via vLLM** must be launched with `--enable-auto-tool-choice`
  and a matching `--tool-call-parser`, or the agent's tool calls never fire and
  the run scores zero. See the `eval-local` target comments in the `Makefile`.

Eval logs (`.eval` files) are written to `inspect/nilgiri/logs/`. Browse them
with:

```
.venv/bin/inspect view --log-dir inspect/nilgiri/logs
```

### 3. Turn the logs into a leaderboard entry

Maintainers regenerate `results.json` directly from the `.eval` logs with the
generator (it needs `inspect_ai` from the project venv):

```
python docs/scripts/generate_results.py \
    --logs inspect/nilgiri/logs \
    --base docs/leaderboard/results.json \
    -o docs/leaderboard/results.json
```

The generator selects the latest 3 successful, M1-start, full-budget runs per
model, reconstructs the token spend at which each flag was first captured, and
computes prerequisite-credited sequential milestone progress. 

If you can't share raw logs, you can instead **hand-edit
`docs/leaderboard/results.json`**: add a model entry (or correct an existing
one) following the shape of the entries already there, and set
`data_quality.status` to `measured` (and `verified: true`) only once real eval
logs back the numbers. Use `estimated` when a row is sourced from a public
announcement rather than a run you can reproduce. Keep `flags/manifest.yaml`
(flag counts, milestone boundaries) as the source of truth.

### 4. Open a PR

Open a pull request that changes `docs/leaderboard/results.json`. CI
(`.github/workflows/validate-data.yml`) runs
`docs/scripts/validate_results.py` automatically and checks your edit against
`docs/leaderboard/schema.json` plus Nilgiri's semantic rules:

- flag counts against `flags/manifest.yaml`,
- valid `pass_at_3` fractions (multiples of `1/3`),
- monotonicity of progress across token budgets,
- and the required `data_quality` caveats.

Reproduce the check locally before pushing:

```
pip install -r docs/scripts/requirements.txt
python docs/scripts/validate_results.py \
    docs/leaderboard/results.json docs/leaderboard/schema.json
```

Green means the numbers are internally consistent. In the PR description, say
how the run was produced (model id, budget, number of runs, provider) so a
maintainer can flip `verified: true` with confidence.

### 5. Merge

On merge to `main`, `.github/workflows/deploy-pages.yml` re-validates the data,
rebuilds the site with the new numbers baked in, and redeploys to GitHub Pages —
no manual deploy step. Your agent is on the public board.

---

## Improving the range or tooling

Contributions to the range itself are welcome — new milestones, fixes to the
Ansible provisioning roles, scorer improvements, or documentation. Some
starting points:

- **Milestones** are provisioned by the per-milestone `make` targets (`m1` …
  `m8-m9`) and their Ansible roles; `docs/walkthrough.md` documents the intended
  solution path for each.
- **Flags** are defined in `flags/manifest.yaml` — the single source of truth
  for flag UUIDs, milestone membership, and ordering. Changing it affects both
  scoring and leaderboard validation.
- **The scorer** (`per_step_flag_scorer`) lives with the Inspect task under
  `inspect/nilgiri/`.
- **Smoke tests** (`make smoke-m3`, `smoke-m4-chain`, `smoke-m5`, …) verify a
  milestone's chain end-to-end and are the fastest way to confirm a change
  didn't break provisioning.

For anything that changes flags, milestone boundaries, or the scorer, run the
relevant smoke test and note the result in your PR. Please open an issue first
for larger structural changes so we can align on direction before you invest the
work.

---

## Questions and issues

Found a bug in the range, the scorer, or the data? Open an issue or a pull
request at <https://github.com/sparclabs/nilgiri>. Thanks for helping make cyber
evaluation open and reproducible.
