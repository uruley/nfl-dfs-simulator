# NFL Sim Lab — consuming Showdown sim outputs

Owner: **NFL Sim Lab** · Manager lock before live slate.  
Package CLI: `python -m nfl_dfs sim-showdown` (package under `/home/box/nfl-dfs`).  
Legacy prototype: `lab/showdown_sim.py` (still present; prefer the package for v1).

## What Lab consumes

| Input | Path / source |
|-------|----------------|
| Player pool | Manager / Scout — DK Showdown template (`Name (id)`, salary, CPT/FLEX) |
| Projections | `exports/projections-showdown.csv` per `PROJECTIONS.md` |
| Corr / script tags | `exports/corr-notes.md` |
| Optional ownership | `own_est` column on projections |
| Optional contest | field size, entry fee (CLI flags) |
| Results (Flashback) | actual FP CSV after slate — **not** mid-slate upload edits |

## Package outputs Lab hands to Coach / Builder

After:

```bash
python -m nfl_dfs sim-showdown \
  --pool <pool.csv> \
  --projections exports/projections-showdown.csv \
  --n-scripts 2000 --portfolio 20 --exposure 0.40 --seed 7 \
  --out exports/
```

| Artifact | Use |
|----------|-----|
| `exports/lineups-showdown-upload.csv` | Builder validates → Manager lock → user delivery. Header `CPT,FLEX×5`. |
| `exports/sim-showdown-summary.txt` | Coach brief: exposures, script-tag coverage, leverage notes |
| `exports/sim-showdown-exposures.csv` | Gate / Flashback inputs |
| `exports/sim-showdown-meta.json` | Reproducibility (seed, n_scripts) |
| `exports/backtest-calibration.md` or `backtests/` | **Next build** gate calibration only |

Lab may also keep working copies under `lab/` (e.g. `sim-results-showdown.csv` from the prototype). Prefer dated or `-v2` names; never overwrite a delivered upload.

## Hard constraints

1. **Never patch mid-slate live files in `uploads/`.** Fixes apply on the next build copy.
2. Soft player exposure default **~40%** (Manager override per slate).
3. Portfolio must span **multiple script tags** (shootout / grind / blowout / close) — not clones of one chalk build.
4. Validate every row: salary ≤ 50k with CPT×1.5, six unique players, CPT+5 FLEX from the same game pool.

## Pipeline sketch

See `lab/PIPELINE.md` for the full flow: scripts → best lineup → contest price → portfolio → gates → Contest Flashback.

## Out of scope for Lab

- Email / user delivery (Manager)
- Inventing projections (NFL Projections)
- Writing Classic 9-man lineups (separate Classic path)
