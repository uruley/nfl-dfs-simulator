# DraftKings NFL Showdown — hard rules (enforce every build)

Operating method (desk OS): many game scripts → best lineup per script → price vs real contest → portfolio across scripts. User football takes tilt sims or hand-pick; Contest Flashback / backtest calibrates gates next build.

## Roster
- Slots: CPT, FLEX, FLEX, FLEX, FLEX, FLEX (6 players)
- CPT = 1.5× DK fantasy points and 1.5× salary of that player
- Salary cap: $50,000 (must be ≤ 50000 after CPT multiplier)
- Unique players only (CPT player cannot also appear in FLEX)
- Use DK player IDs in upload cells: `Name (id)`
- All players from the single Showdown game / contest template

## Scoring (DK NFL — enforce in sims)
- Pass Yd: 0.04 | Pass TD: 4 | INT: −1 | Rush/Rec Yd: 0.1 | Rush/Rec TD: 6
- Reception: 1 (PPR) | Fumble lost: −1 | 2PT: 2
- 300+ pass yds: +3 | 100+ rush / 100+ rec yds: +3 each
- (Prefer official DK contest scoring page when available)

## Portfolio / exposure
- Soft player exposure default: ~40% of lineups (Manager may override per slate)
- Spread portfolio across distinct game scripts (not clones of one chalk build)
- Prefer uniqueness vs field when contest-priced; do not ship duplicate lineups

## Upload CSV
- Two formats (Builder chooses under Manager direction):
  - Bare Lineup Upload: header exactly `CPT,FLEX,FLEX,FLEX,FLEX,FLEX`
  - Entry-ID upload: include Entry ID column when entering a specific contest multi-entry
- No projections columns in upload files; lineup rows only
- Never overwrite a delivered file; use `-v2` (or dated) for upgrades
- Sim Lab never patches mid-slate live upload files — fixes apply next build

## Builder checklist before writing lineups-*.csv
1. Salary ≤ 50000 for every row (CPT salary × 1.5 counted)
2. Valid CPT + 5 FLEX; all from Showdown pool
3. Six unique players
4. Exposure ≤ slate cap (~40% default)
5. Portfolio covers multiple sim scripts / outcomes
6. Validate all rows; do not ship if any fail
