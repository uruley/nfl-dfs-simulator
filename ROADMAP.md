# NFL DFS Simulator — roadmap

## Product thesis
**SaberSim-shaped engine** + **Grok Bot desk OS**.

SaberSim solves: many game paths → best lineup per path → price vs contest → portfolio + Flashback.
We match that method in open code, then wrap it with a managed desk (Scout → Projections → Sim Lab → Builder → Manager) so football takes, free intel, gates, and delivery are first-class — not a black-box UI.

## Done (v1–v3 / 0.4.0)
- [x] Score-path / possession engine → best lineup per realized path
- [x] `--engine scorepath|legacy` on Showdown + Classic; `path-summaries.csv`

## Done (v1–v2)
- [x] Showdown scripts → best lineup/script → portfolio (~40% exposure)
- [x] Classic 9-slot path
- [x] DK salary-file ingest
- [x] Contest Flashback → next-build gates (no mid-slate upload mutation)
- [x] Desk agents + hard rules
- [x] Richer scripts: Vegas spread/total priors (offline-capable, seeded)
- [x] Field simulation for contest pricing (ownership-weighted opponents → win/cash/EV)
- [x] Ownership-aware portfolio (diversify vs field chalk + self exposure)
- [x] Entry-ID upload writer + multi-entry contest file
- [x] `OPERATING.md` — exact CLI each specialist runs + file contracts

## Next hardening (in flight)
### Engine (SaberSim-like)
- [x] Drive-count / score-path model depth (`scorepath.py`, `--engine scorepath`)
- [x] Scale knobs: n_scripts toward production (1k–5k+) with `--progress-every`
- [ ] Classic field-sim pricing parity

### Desk OS (Grok Bot team)
- [ ] Slate lock protocol (Manager unlock message template — drafted in OPERATING.md)
- [ ] Flashback → Lab gates → Builder checklist automation hooks
- [x] Optional slate watcher (`scratch-watch` — OUT/INACTIVE vs entered lineups)

## Non-goals (for now)
Paid projection scrapers, cloning SaberSim UI, FanDuel, live mid-slate Lab patches.
