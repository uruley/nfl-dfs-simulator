# NFL DFS — DraftKings Simulator

Local Python package for the desk DFS method (SaberSim-shaped engine + Grok Bot desk OS):

1. **Simulate** score-paths / possessions (Showdown + Classic; Vegas-aware) or legacy mean-tilt scripts.
2. **Optimize** legal DK lineups under salary + roster rules.
3. **Price** vs an ownership-weighted field (win/cash/EV) or crude leverage.
4. **Portfolio** with default **~40%** player exposure + diversify vs field chalk.
5. **Ingest** real DK salary-file CSVs into normalized pools.
6. **Contest Flashback** — score uploads vs actuals; emit next-build gates (never mutates `uploads/`).
7. **Scratch watch** — compare entered lineups vs OUT/INACTIVE; alert on hits only (never mutates uploads).

Hard rules: `DK-NFL-SHOWDOWN-RULES.md`, `DK-NFL-CLASSIC-RULES.md`.  
Projection contracts: `PROJECTIONS.md`. Salary ingest: `INGEST.md`.  
Desk map: `DESK.md` · Operating CLI: `OPERATING.md` · Roadmap: `ROADMAP.md`.

**Live slate lock is ON** — use fixtures / saved exports only; do not scrape live DK for production lineups.

## Install

```bash
cd /home/box/nfl-dfs
source .venv/bin/activate   # or: python3.11+ -m venv .venv && pip install -e ".[dev]"
pip install -e ".[dev]"
```

Requires Python 3.11+ and `numpy`. No paid APIs; tests need no network.

## CLI overview

| Command | Purpose |
|---------|---------|
| `sim-showdown` | Showdown scripts → CPT+FLEX → field price → portfolio |
| `sim-classic` | Classic multi-game sims → 9-slot lineups → portfolio |
| `ingest-dk-salary` | DK salary CSV → normalized pool CSV |
| `flashback` | Score lineups vs actuals → scores + summary + gates |
| `scratch-watch` | Entered lineups vs OUT/INACTIVE → alert on hits |

---

### 1) Showdown

```bash
python -m nfl_dfs sim-showdown \
  --pool fixtures/showdown_pool.csv \
  --projections fixtures/projections.csv \
  --n-scripts 200 \
  --portfolio 20 \
  --exposure 0.40 \
  --seed 7 \
  --engine scorepath \
  --out /home/box/nfl-dfs/exports
```

`--engine scorepath` (default) uses the possession model; `--engine legacy` keeps mean-tilt residuals. Optional `--progress-every 100` logs to stderr.

**Vegas-aware scripts** (optional): bias pace / pass_tilt / margin from priors.

```bash
python -m nfl_dfs sim-showdown \
  --pool fixtures/showdown_pool.csv \
  --projections fixtures/projections.csv \
  --spread -2.5 --total 48.5 \
  --n-scripts 500 --portfolio 20 --exposure 0.40 --seed 7 \
  --out /home/box/nfl-dfs/exports
```

- `--spread` — home-team point spread (negative = home favored).
- `--total` — Vegas over/under; higher totals → higher pace + pass tilt.
- Also accepted via `--contest-meta` JSON keys `spread` / `total` (CLI flags override).
- Seed remains deterministic when Vegas priors are set.

**Field simulation contest pricing** (optional):

```bash
python -m nfl_dfs sim-showdown \
  --pool fixtures/showdown_pool.csv \
  --projections fixtures/projections.csv \
  --field-sims 500 \
  --ownership fixtures/ownership_sample.csv \
  --field-size 1000 --entry-fee 5 \
  --n-scripts 200 --portfolio 20 --seed 7 \
  --out /home/box/nfl-dfs/exports
```

- `--field-sims N` — sample N ownership-weighted legal CPT+5FLEX opponent lineups; estimate `win_rate`, `top1_rate`, `cash_rate`, `leverage`, `est_EV`.
- `--ownership path` — optional CSV (`dk_id,own_est`); else uses `own_est` on projections.
- Without `--field-sims`, falls back to crude `leverage ≈ sim_frequency − avg_ownership`.

**Entry-ID upload** (multi-entry):

```bash
# Sequential IDs
python -m nfl_dfs sim-showdown ... --entry-id-start 100001 --out exports/

# Or from a DK entry template CSV
python -m nfl_dfs sim-showdown ... --entry-ids fixtures/entry_ids_sample.csv --out exports/
```

Header becomes `Entry ID,CPT,FLEX,FLEX,FLEX,FLEX,FLEX`. Omit both flags for bare `CPT,FLEX×5`.

Other optional flags: `--read NAME=1.2`, `--contest-meta fixtures/contest_sample.json`, `--actuals`, `--backtest-out`.

Outputs: `lineups-showdown-upload.csv`, `path-summaries.csv`, `sim-showdown-priced.csv`, summary, exposures, meta.

### 2) Classic

```bash
python -m nfl_dfs sim-classic \
  --pool fixtures/classic_pool.csv \
  --projections fixtures/classic_projections.csv \
  --n-sims 200 \
  --portfolio 20 \
  --exposure 0.40 \
  --seed 7 \
  --out /home/box/nfl-dfs/exports
```

Optional: `--read NAME=1.2` (repeatable), `--engine scorepath|legacy`, `--progress-every N`.

**Sim method (scorepath):** for each draw, run the possession model per slate game, merge realized FPs, then optimize a Classic lineup. Legacy: per-game mean-tilt + correlated residuals (`classic_sim.py`).

**Rules enforced:** QB,RB,RB,WR,WR,WR,TE,FLEX,DST; $50k; FLEX=RB/WR/TE; ≥2 games on multi-game slates; upload cells `Name (id)`.

Outputs: `lineups-classic-upload.csv` (header exactly `QB,RB,RB,WR,WR,WR,TE,FLEX,DST`), `path-summaries.csv`, summary, exposures, meta.

### 3) Ingest DK salary file

```bash
python -m nfl_dfs ingest-dk-salary \
  --input fixtures/dk_salary_classic_sample.csv \
  --out /tmp/classic_pool_norm.csv
```

Detects Showdown vs Classic from `Roster Position`. See `INGEST.md`.

### 4) Contest Flashback

```bash
python -m nfl_dfs flashback \
  --lineups exports/lineups-showdown-upload.csv \
  --actuals fixtures/actuals.csv \
  --contest fixtures/contest_sample.json \
  --out /home/box/nfl-dfs/backtests
```

Emits:

| File | Content |
|------|---------|
| `flashback-scores.csv` | Per-lineup FP (Showdown CPT×1.5 / Classic flat) + rank |
| `flashback-summary.md` | Brief + exposures vs actual |
| `next-build-gates.json` | Conservative fade/boost gates for **next** build only |

Optional `--payouts place,payout CSV`. **Never mutates `uploads/`.**

---

### 5) Scratch / inactives watch

Compare players in a delivered upload CSV against a status map (local CSV, optional public fetch, or Scout brief fallback). **Alerts only when someone in our lineups is OUT or INACTIVE** (DOUBTFUL = soft warn). Never mutates the lineups file.

```bash
python -m nfl_dfs scratch-watch \
  --lineups uploads/lineups-classic-2game-20-v4.csv \
  --pool exports/pool-classic-2game.csv \
  --games DAL@NYG,DEN@KC \
  --fetch \
  --out exports/scratch-watch
```

Offline / tests: `--status fixtures/scratch_status_sample.csv`.  
Clean automation: `--quiet-ok` (exit 0 + quiet stdout when `all_clear`).

Outputs: `scratch-watch-last.json`, `scratch-watch-report.md`, and `scratch-hits.csv` when any HIT.


## Method (short)

- **Score-path engine (default, `--engine scorepath`):** sample a possession-level game path (pace / margin / pass rate from Vegas priors + game state) → allocate team yards/TDs/turnovers → distribute to players via projection usage shares (`proj_fp × boost`) → DK FP via `scoring.py` → **best legal lineup for that realized FP vector**. Portfolio still exposure-capped ~40% and diversifies across path tags. Emits `path-summaries.csv`.
- **Legacy (`--engine legacy`):** previous mean-tilt + correlated residuals (Showdown / Classic).
- **Showdown:** CPT+5FLEX best lineup(s) per path; optional field sim pricing.
- **Classic:** per-game scorepath merge across slate games, then best 9-slot lineup.
- **Contest price (Showdown):** field sim → `win_rate` / `cash_rate` / `est_EV`; else crude `leverage ≈ sim_frequency − avg_ownership`.

## DK scoring (enforced in `scoring.py`)

Pass Yd 0.04 · Pass TD 4 · INT −1 · Rush/Rec Yd 0.1 · Rush/Rec TD 6 · Rec 1 PPR · Fumble lost −1 · 2PT 2 · 300 pass +3 · 100 rush +3 · 100 rec +3.

## Layout

```
src/nfl_dfs/     scoring, scorepath, showdown_rules, classic_*, scripts, optimize,
                 portfolio, contest, ingest, backtest, flashback, cli
fixtures/        showdown + classic pools/projections/actuals + ownership + entry IDs
tests/           pytest
uploads/         delivered uploads — never mutated by Lab/sim/flashback
exports/         sim outputs & projection handoffs
backtests/       flashback / calibration artifacts
```

## Tests

```bash
cd /home/box/nfl-dfs && source .venv/bin/activate
pytest -q
```
