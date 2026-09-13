# NFL DFS — DraftKings Simulator

Local Python package for the desk DFS method:

1. **Simulate** game scripts (Showdown) or multi-game correlated scripts (Classic).
2. **Optimize** legal DK lineups under salary + roster rules.
3. **Portfolio** with default **~40%** player exposure.
4. **Ingest** real DK salary-file CSVs into normalized pools.
5. **Contest Flashback** — score uploads vs actuals; emit next-build gates (never mutates `uploads/`).

Hard rules: `DK-NFL-SHOWDOWN-RULES.md`, `DK-NFL-CLASSIC-RULES.md`.  
Projection contracts: `PROJECTIONS.md`. Salary ingest: `INGEST.md`. Desk map: `DESK.md`.

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
| `sim-showdown` | Showdown scripts → CPT+FLEX lineups → portfolio |
| `sim-classic` | Classic multi-game sims → 9-slot lineups → portfolio |
| `ingest-dk-salary` | DK salary CSV → normalized pool CSV |
| `flashback` | Score lineups vs actuals → scores + summary + gates |

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
  --out /home/box/nfl-dfs/exports
```

Optional: `--read NAME=1.2`, `--field-size` / `--entry-fee`, `--actuals`, `--backtest-out`.

Outputs: `lineups-showdown-upload.csv` (header `CPT,FLEX×5`), summary, exposures, meta.

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

Optional: `--read NAME=1.2` (repeatable).

**Sim method:** for each draw, sample a mini game-script per slate game (pace / pass-tilt / margin / weather), tilt player means, then add team + pass-game correlated residuals. Documented in `src/nfl_dfs/classic_sim.py`.

**Rules enforced:** QB,RB,RB,WR,WR,WR,TE,FLEX,DST; $50k; FLEX=RB/WR/TE; ≥2 games on multi-game slates; upload cells `Name (id)`.

Outputs: `lineups-classic-upload.csv` (header exactly `QB,RB,RB,WR,WR,WR,TE,FLEX,DST`), summary, exposures, meta.

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

## Method (short)

- **Showdown:** scripts tilt team means then sample correlated residuals; try each CPT; portfolio with exposure + CPT diversity.
- **Classic:** per-game scripts across the slate + correlated noise; greedy slot fill with local swaps; portfolio exposure cap (~40%, soft +5–10% relax if under-filled).
- **Contest price (Showdown):** `leverage ≈ sim_frequency − avg_ownership`.

## DK scoring (enforced in `scoring.py`)

Pass Yd 0.04 · Pass TD 4 · INT −1 · Rush/Rec Yd 0.1 · Rush/Rec TD 6 · Rec 1 PPR · Fumble lost −1 · 2PT 2 · 300 pass +3 · 100 rush +3 · 100 rec +3.

## Layout

```
src/nfl_dfs/     scoring, showdown_rules, classic_*, scripts, optimize,
                 portfolio, contest, ingest, backtest, flashback, cli
fixtures/        showdown + classic pools/projections/actuals + DK salary samples
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
