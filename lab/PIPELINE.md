# Showdown pipeline (quiet prep sketch)

Owner: NFL Sim Lab · Manager lock required before live slate  
Inputs locked by Manager: slate pool + contest (Flashback waits on results zip)

## Flow

1. **Inputs** (from Projections / Scout / Manager)
   - Pool: DK Showdown player pool (`Name (id)`, salary, CPT/FLEX elig, team)
   - Projections: `exports/projections-showdown.csv` per PROJECTIONS.md
   - Corr notes: `exports/corr-notes.md` (script tags: shootout / grind / blowout / weather)
   - Optional: Vegas totals, ownership proxy (`own_est`)

2. **Game scripts** (`lab/showdown_sim.py`)
   - Draw N scripts (default ~2k–10k) over pace / pass-run tilt / score margin / weather
   - Sample player FP with team/script correlation (QB↔WR+, RB↔total, bring-backs, DST↔opp skill)
   - CPT scoring: 1.5× FP; salary check uses 1.5× CPT salary; cap $50k; 1 CPT + 5 FLEX; unique players; same game

3. **Best lineup per script**
   - Optimize (or strong greedy) legal Showdown roster maximizing script FP
   - Aggregate: lineup frequency, CPT vs FLEX exposure by player, script-cluster coverage

4. **Contest price** (`lab/contest_pricing.py`)
   - Price high-frequency / high-EV lineups vs field ownership when available
   - Leverage = sim exposure − chalk own; flag clones of one chalk path
   - Output: pricing summary for Coach (not an upload CSV)

5. **Portfolio**
   - Select diversified set across scripts (~40% soft player exposure default; Manager override)
   - Hand exposure targets + recommended lineup set notes to Builder (Builder writes upload CSV)

6. **Gates — next build only** (`lab/gates.py`)
   - Dead-weight floors, CPT eligibility, chalk-leverage tags, correlation soft rules
   - **Never** patch mid-slate live upload files; apply calibrated gates on next build copies

7. **Contest Flashback** (`lab/flashback_calibrate.py`) — HOLD until results zip
   - Compare sim exposures vs actual contest finishes / ownership
   - Metrics: Spearman, top-overlap, own vs sim MAE → update gate constants next slate

## Deliverables to Coach
- `lab/sim-results-showdown.csv` + summary
- `lab/contest-pricing-summary.txt` (when contest known)
- Gate note for next build (never live mid-slate patch)
- Flashback calibration note (post-results only)

## Out of scope for Lab
- CSV uploads / email delivery (Builder / Manager)
- Inventing projections (Projections owns)
