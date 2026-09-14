# NFL Projections — output contract

Owner: NFL Projections  
Consumers: NFL Sim Lab (gates/sims), NFL Builder (rosters). Not an upload file.

## Production files
| Role | Path |
|------|------|
| Classic production | `/home/box/nfl-dfs/exports/projections-classic.csv` |
| Showdown production | `/home/box/nfl-dfs/exports/projections-showdown.csv` |
| Correlation notes | `/home/box/nfl-dfs/exports/corr-notes.md` |
| Slate brief (Scout + harvest) | `/home/box/nfl-dfs/sources/slate-brief-YYYY-MM-DD.md` |

Never overwrite a delivered file; version (`-v2`) or date the upgrade.

## Classic schema
`slate,dk_id,name,position,team,opp,salary,game_info,proj_fp,floor,ceiling,std,own_est,implied_td,sources,notes`

- `position`: DK Classic slot eligibility (`QB`/`RB`/`WR`/`TE`/`DST`; multi-pos as `RB/WR`)
- `proj_fp`: mean DraftKings fantasy points (PPR, DK scoring)
- `floor` / `ceiling`: ~20th / ~80th percentile, not min/max
- `own_est`: public ownership proxy when sourced; blank if unknown
- `sources`: pipe-separated tags (`fantasypros|espn|vegas|scout|est`)
- `notes`: injury, role, weather, estimate flags. Prefix estimates with `ESTIMATE:`

## Showdown schema
Same columns plus:

`cpt_salary,cpt_proj,cpt_ceiling,flex_value,cpt_value`

- `cpt_salary` = `salary * 1.5` (DK CPT pricing)
- `cpt_proj` = `proj_fp * 1.5`
- Values are `proj / salary * 1000` (FLEX) and `cpt_proj / cpt_salary * 1000` (CPT)

## Correlation notes (required with every slate)
Short bullets, not a matrix dump:
- Positive: QB+pass-catcher, RB+team total, bring-back opposite WR in shootouts
- Negative: RB vs same-team pass game in low-total scripts; DST vs opposing skill
- Game-script tags: shootout / grind / blowout-risk / weather-down

## Blend rules
1. Never invent stats. Missing source → blank or `ESTIMATE:` with why.
2. Blend free public means with Vegas implied team totals and Scout injury/role.
3. Salary is DK only. No FanDuel columns in production files.
4. Inactives / OUT → `proj_fp=0` and note. Questionable stays in pool with haircut + note.
5. Projections only — no lineups, no DK upload, no email to user.

## DK scoring used (unless official contest page overrides)
Pass: 0.04/yd, 4/TD, −1/INT, +3 at 300 yds  
Rush/Rec: 0.1/yd, 6/TD, 1/rec, +3 at 100 rush or 100 rec  
Fumble lost −1; 2PT +2

## Desk lock (2026-09-13)
Build NFL DFS simulator **before** any live slate. No live projection runs, salary chase, or Builder pings until sim CLI + this contract are confirmed by NFLDFSCoach / Sim Lab. Existing `exports/projections-showdown.csv` is fixture-only and may be overwritten by sim work.

## Sim Lab required fields (draft — confirm when CLI lands)
Minimum for gated Showdown sims:
- Identity: `dk_id` (required when lobby export available), `name`, `position`, `team`, `salary`
- Means: `proj_fp`, `floor`, `ceiling`, `std`
- Showdown: `cpt_salary`, `cpt_proj`, `cpt_ceiling`
- Provenance: `sources`, `notes` (`ESTIMATE:` / inactive flags)

Optional / blank OK until sourced: `own_est`, `implied_td`, `flex_value`, `cpt_value`, `opp`, `game_info`, `slate`, `rush_share`, `target_share`, `rz_share` (0–1 usage priors for scorepath)

Classic omits CPT columns. DST and K included when in the DK pool.

## Salary pool ingest
DK lobby / Salary Cap CSV → normalized pool: see `INGEST.md` and `python -m nfl_dfs ingest-dk-salary`.
