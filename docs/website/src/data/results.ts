import fs from 'node:fs';
import path from 'node:path';

// Canonical leaderboard data lives in docs/leaderboard/, not under src/, so it
// stays a single source of truth shared with docs/scripts/*.py. Read at build
// time (this only ever runs in Node during `astro build`/`astro dev`), so the
// numbers are baked into the static output -- no client-side fetch, no CORS.
//
// Resolved from process.cwd() rather than import.meta.url: Vite relocates
// this module into dist/.prerender/chunks/ during the build, so a path
// relative to the module's own location breaks post-bundling. `astro
// build`/`astro dev` (and the CI workflow's `path: docs/website`) always run
// with docs/website as the working directory, so cwd is the stable anchor.
const RESULTS_PATH = path.resolve(process.cwd(), '../leaderboard/results.json');

export interface GlobalMetrics {
  avg_flags_captured_at_3: number;
  avg_milestones_completed_at_3: number;
  pass_at_3: number;
}

export interface MilestoneMetric {
  pass_at_3: number;
}

export interface BudgetSlice {
  overall: GlobalMetrics;
  milestones: Record<string, MilestoneMetric>;
}

export interface DataQuality {
  status: 'estimated' | 'measured';
  verified: boolean;
  method: string;
  source: string;
  caveat?: string;
}

export interface ModelEntry {
  id: string;
  display_name: string;
  provider: string;
  by_token_budget: Record<string, BudgetSlice>;
  data_quality: DataQuality;
  notes?: string;
}

export interface Milestone {
  id: string;
  name: string;
  flags: number;
}

export interface Results {
  generated_at: string;
  runs_per_model: number;
  benchmark: {
    name: string;
    total_flags: number;
    total_milestones: number;
    milestones: Milestone[];
    token_budgets: number[];
  };
  models: ModelEntry[];
}

export function loadResults(): Results {
  const raw = fs.readFileSync(RESULTS_PATH, 'utf-8');
  return JSON.parse(raw) as Results;
}
