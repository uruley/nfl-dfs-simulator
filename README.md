# NFL DFS — DraftKings Showdown Simulator (v1)

Local Python package that implements the desk Showdown OS method:

1. **Simulate many game scripts** for one NFL game (pace, pass/run tilt, margin, weather).
2. **Best legal DK Showdown lineup per script** — CPT + 5 FLEX, $50k cap, CPT salary & points ×1.5, unique players.
3. **Price candidates** vs an optional contest (field size, simple prize pool, ownership leverage).
4. **Portfolio** across distinct scripts with default **40%** player exposure cap.
5. **Seed** + user **reads** (`--read NAME=1.2` boost / `ID=0.8` fade).
6. **Backtest hook** — score portfolio vs actual FP; write calibration notes. **Never mutates** delivered CSVs in `uploads/`.

Hard rules: `DK-NFL-SHOWDOWN-RULES.md`. Desk map: `DESK.md`. Projection contracts: `PROJECTIONS.md`.

## Install

```bash
cd /home/box/nfl-dfs
source .venv/bin/activate   # or: python3.11+ -m venv .venv && pip install -e ".[dev]"
pip install -e ".[dev]"
```

Requires Python 3.11+ and `numpy`. No paid APIs; tests need no network.

## CLI

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

Optional:

| Flag | Meaning |
|------|---------|
| `--read NAME=1.2` | Boost/fade (repeatable); matches `dk_id` or player name |
| `--field-size` / `--entry-fee` | Enable simple contest pricing / leverage sort |
| `--actuals fixtures/actuals.csv` | Backtest portfolio; writes calibration notes |
| `--backtest-out PATH` | Calibration markdown path (default under `--out`) |

### Outputs (under `--out`)

| File | Contract |
|------|----------|
| `lineups-showdown-upload.csv` | Bare DK Lineup Upload: header exactly `CPT,FLEX,FLEX,FLEX,FLEX,FLEX`; cells `Name (id)` |
| `sim-showdown-summary.txt` | Exposures, script-tag coverage, top leverage notes |
| `sim-showdown-exposures.csv` | Player → portfolio exposure |
| `sim-showdown-meta.json` | Run metadata |
| `backtest-calibration.md` | Only if `--actuals` given — next-build gates only |

## Method (short)

- Scripts tilt team means (QB/WR/TE vs RB/DST) then sample correlated residuals (team + pass-game latents).
- Optimizer: try each CPT; greedy FLEX by FP with value fallback + local swaps under remaining salary.
- Portfolio: unique lineup keys, prefer distinct `script_id`s, enforce exposure ≤ cap (tiny +5% relax if under-filled).
- Contest price: `leverage ≈ sim_frequency − avg_ownership` (uses `own_est` when present).

## DK scoring (enforced in `scoring.py`)

Pass Yd 0.04 · Pass TD 4 · INT −1 · Rush/Rec Yd 0.1 · Rush/Rec TD 6 · Rec 1 PPR · Fumble lost −1 · 2PT 2 · 300 pass +3 · 100 rush +3 · 100 rec +3.

## Layout

```
src/nfl_dfs/     scoring, showdown_rules, scripts, optimize, contest, portfolio, backtest, cli
fixtures/        fake two-team Showdown pool (~24) + projections + actuals
tests/           pytest (CPT salary, uniqueness, bonuses, exposure, CSV header, CLI)
lab/             Sim Lab notes / legacy prototypes (see lab/README.md)
uploads/         delivered uploads — never mutated by Lab/sim
exports/         sim outputs & production projection handoffs
backtests/       calibration artifacts
```

## Tests

```bash
cd /home/box/nfl-dfs && source .venv/bin/activate
pytest -q
```

Target: 200 scripts + 20-lineup portfolio finishes in under ~30s on this machine.
