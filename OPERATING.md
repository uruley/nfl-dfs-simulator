# Desk operating system (Grok Bot team + engine)

Manager: **NFLDFSCoach** unlocks a named slate. Until then: specialists IDLE.

## Pipeline
```
Scout (free intel) → Projections (means/ceilings/own) → Sim Lab (CLI + gates)
  → Builder (upload CSV) → Manager (lock + email ulyssesruley@gmail.com)
```

## File contracts (`/home/box/nfl-dfs`)
| Stage | Path | Producer |
|-------|------|----------|
| DK salary export | `uploads/dk-salary-*.csv` | User / Manager |
| Normalized pool | `exports/pool-*.csv` | `ingest-dk-salary` (Lab or Manager) |
| Projections | `exports/projections-*.csv` | NFL Projections |
| Ownership (optional) | `exports/ownership-*.csv` or `fixtures/ownership_sample.csv` | Projections / Scout |
| Contest meta | `fixtures/contest_sample.json` / `exports/contest-*.json` | Lab (spread, total, field) |
| Sim portfolio | `exports/lineups-*-upload.csv` | `sim-showdown` / `sim-classic` (Lab) |
| Priced metrics | `exports/sim-showdown-priced.csv` | `sim-showdown` (Lab) |
| Gates | `backtests/next-build-gates.json` | `flashback` (Lab) |
| Delivered uploads | `uploads/lineups-*-vN.csv` | Builder (never overwritten) |
| Scratch watch | `exports/scratch-watch/` | `scratch-watch` (Lab / Manager) — never mutates uploads |

## CLI cheatsheet
```bash
cd /home/box/nfl-dfs && source .venv/bin/activate

# 1) Ingest DK lobby salary file
python -m nfl_dfs ingest-dk-salary --input uploads/dk-salary.csv --out exports/pool.csv

# 2) Showdown sim (Lab) — Vegas + field price + entry IDs
python -m nfl_dfs sim-showdown --pool exports/pool.csv --projections exports/projections.csv \
  --n-scripts 2000 --portfolio 20 --exposure 0.40 --seed 7 --out exports/ \
  --spread -2.5 --total 48.5 \
  --field-sims 1000 --ownership exports/ownership.csv \
  --field-size 1000 --entry-fee 5 \
  --entry-id-start 100001 \
  --read "Player Name=1.15"

# 2b) Contest meta JSON can carry spread/total/field_size/entry_fee
python -m nfl_dfs sim-showdown --pool exports/pool.csv --projections exports/projections.csv \
  --contest-meta exports/contest.json --field-sims 500 --out exports/

# 3) Classic sim
python -m nfl_dfs sim-classic --pool exports/pool.csv --projections exports/projections.csv \
  --portfolio 20 --exposure 0.40 --out exports/

# 4) Flashback after results (Lab) — does NOT touch uploads/
python -m nfl_dfs flashback --lineups exports/lineups-showdown-upload.csv \
  --actuals backtests/actuals.csv --out backtests/

# 5) Scratch / inactives watch (entered lineups vs OUT/INACTIVE) — never mutates uploads/
python -m nfl_dfs scratch-watch --lineups uploads/lineups-classic-2game-20-v4.csv \
  --pool exports/pool-classic-2game.csv --games DAL@NYG,DEN@KC --fetch \
  --out exports/scratch-watch
# Offline/tests: --status fixtures/scratch_status_sample.csv
# Clean slate: --quiet-ok (exit 0, minimal stdout when all_clear)
```

## Role rules
- **Scout:** free sources only; slate brief + flags (injuries, weather, Nabers-style snap risk); note Vegas spread/total.
- **Projections:** fill projection CSV schema (incl. `own_est`); never invent DK IDs/salaries — join to ingested pool.
- **Sim Lab:** owns CLI science + Flashback gates + `scratch-watch`; never patches live delivered CSVs.
- **Builder:** writes versioned upload CSVs from gated exports; Entry-ID vs bare per Manager (`--entry-ids` / `--entry-id-start`).
- **Manager:** slate lock, exposure overrides, user delivery, pushes to GitHub.

## Unlock template (Manager → specialists)
`UNLOCK SLATE: <Showdown|Classic> <GAME/SLATE> contest=<name> entries=N exposure=0.40 format=<bare|entry-id> spread=<x> total=<y>`
