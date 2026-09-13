# DK salary-file ingest

Convert DraftKings lobby / Salary Cap CSV exports into normalized pool CSVs that
`sim-showdown` and `sim-classic` understand.

## CLI

```bash
python -m nfl_dfs ingest-dk-salary \
  --input path/to/dk_salary_export.csv \
  --out fixtures/or/exports/normalized_pool.csv
```

## Detection

| Roster Position signals | Contest |
|-------------------------|---------|
| Contains `CPT` | Showdown |
| `QB` / `RB` / `WR` / `TE` / `DST` (no CPT) | Classic |

## Supported input columns

Common DK variants (any subset; case-insensitive):

- `Position`
- `Name + ID` **or** separate `Name` + `ID`
- `Roster Position`
- `Salary`
- `Game Info` (parsed into `opp` + `game_key` when `TeamAbbrev` present)
- `TeamAbbrev`
- `AvgPointsPerGame` (carried through when present; not required for sims)

`Name (ID)` cells are parsed when Name+ID is combined or when Name itself embeds the id.

## Output schemas

**Showdown pool** (matches `fixtures/showdown_pool.csv`):

`dk_id,name,position,team,opp,salary,roster_positions`

**Classic pool** (matches `fixtures/classic_pool.csv`):

`dk_id,name,position,team,opp,salary,game_info,game_key,roster_positions`

## Fixtures

- `fixtures/dk_salary_showdown_sample.csv`
- `fixtures/dk_salary_classic_sample.csv`

Live slate lock: use fixtures / saved exports only — do not scrape live DK for production lineups.
