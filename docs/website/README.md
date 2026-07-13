# Nilgiri website

Source for the Nilgiri GitHub Pages site (https://sparclabs.github.io/nilgiri/), built with
[Astro](https://astro.build/) + [Tailwind CSS](https://tailwindcss.com/) on the
[AstroWind](https://github.com/onwidget/astrowind) template, plus [AG Grid Community](https://www.ag-grid.com/) for
the leaderboard table.

## Commands

Run from this directory (`docs/website/`):

| Command           | Purpose                                          |
| ----------------- | ------------------------------------------------ |
| `npm install`     | Install dependencies                             |
| `npm run dev`     | Start the dev server at `localhost:4321/nilgiri` |
| `npm run build`   | Production build to `./dist/`                    |
| `npm run preview` | Preview the production build locally             |
| `npm run check`   | `astro check` + ESLint + Prettier                |

Node.js >= 22.12.0 is required.

## Structure

- `src/pages/index.astro` — homepage (benchmark overview, methodology, milestones, key findings).
- `src/pages/leaderboard.astro` + `src/components/leaderboard/` — the filterable leaderboard (AG Grid island).
- `src/data/results.ts` — reads `../../leaderboard/results.json` at build time (see below); this is the only place
  that couples the site to the leaderboard data.
- `src/components/widgets/` — page sections; most are stock AstroWind widgets, plus a few written for this site
  (`NetworkTopology.astro`, `KeyFindings.astro`, `QuickStart.astro`).
- `src/config.yaml` / `src/navigation.ts` — site metadata and header/footer links.

## Leaderboard data

The leaderboard's data lives outside this Astro project, in `../leaderboard/` (`results.json` + `schema.json`), and
is generated/validated by the scripts in `../scripts/`. See the repository root README for how to update it — in
short: edit `docs/leaderboard/results.json` directly and open a PR; CI validates it, and merging to `main`
automatically rebuilds and redeploys this site.

## Deployment

`.github/workflows/deploy-pages.yml` (repository root) builds this project and deploys it to GitHub Pages on every
push to `main` that touches `docs/website/**` or `docs/leaderboard/**`. `astro.config.ts` sets `site`/`base` for the
`sparclabs/nilgiri` project-page URL; update both if the repository is ever renamed or transferred.
