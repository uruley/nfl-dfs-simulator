#!/usr/bin/env python3
"""NFL Showdown Monte Carlo — many game scripts → best CPT+FLEX lineup per script.

Inspired by SaberSim Showdown method. Offline lab under /home/box/nfl-dfs/lab.
Schemas align with /home/box/nfl-dfs/PROJECTIONS.md Showdown columns.
Never patches mid-slate live upload files.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

DESK = Path("/home/box/nfl-dfs")
LAB = DESK / "lab"
DEFAULT_POOL = DESK / "uploads" / "dk-showdown-player-pool.csv"
DEFAULT_PROJ = DESK / "exports" / "projections-showdown.csv"
DEFAULT_OUT = LAB / "sim-results-showdown.csv"
DEFAULT_SUMMARY = LAB / "sim-results-showdown-summary.txt"
DEFAULT_N = 2000
DEFAULT_SEED = 42
SALARY_CAP = 50_000
CPT_MULT = 1.5
N_FLEX = 5


def safe_float(x, default=None):
    try:
        if x is None or str(x).strip() == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def synthesize_demo_pool(seed: int = 42) -> list[dict]:
    """Tiny fake 2-team Showdown pool (no real inputs needed)."""
    rng = np.random.default_rng(seed)
    # KC @ BUF style toy slate
    base = [
        # name, pos, team, salary, proj_fp, std
        ("Patrick Mahomes", "QB", "KC", 10600, 22.5, 7.0),
        ("Josh Allen", "QB", "BUF", 11200, 24.0, 7.5),
        ("Travis Kelce", "TE", "KC", 8200, 15.5, 5.5),
        ("Stefon Diggs", "WR", "BUF", 9000, 16.8, 6.0),
        ("Isiah Pacheco", "RB", "KC", 6800, 13.2, 5.0),
        ("James Cook", "RB", "BUF", 7200, 14.0, 5.2),
        ("Rashee Rice", "WR", "KC", 6400, 12.5, 5.0),
        ("Dalton Kincaid", "TE", "BUF", 4600, 9.5, 4.0),
        ("Marquise Brown", "WR", "KC", 4200, 8.8, 4.2),
        ("Khalil Shakir", "WR", "BUF", 4800, 10.2, 4.5),
        ("Noah Gray", "TE", "KC", 2800, 5.5, 3.0),
        ("Ty Johnson", "RB", "BUF", 2400, 4.8, 2.8),
        ("Chiefs", "DST", "KC", 3200, 6.5, 4.0),
        ("Bills", "DST", "BUF", 3600, 7.0, 4.2),
        ("Harrison Butker", "K", "KC", 4000, 8.0, 3.5),
        ("Tyler Bass", "K", "BUF", 3800, 7.5, 3.5),
        ("Justin Watson", "WR", "KC", 2200, 3.5, 2.5),
        ("Mack Hollins", "WR", "BUF", 2000, 3.2, 2.4),
    ]
    players = []
    for i, (name, pos, team, sal, proj, std) in enumerate(base):
        noise = float(rng.normal(0, 0.3))
        proj_fp = max(0.5, proj + noise)
        cpt_sal = int(round(sal * CPT_MULT))
        cpt_proj = round(proj_fp * CPT_MULT, 3)
        players.append(
            {
                "dk_id": str(900000 + i),
                "name": name,
                "position": pos,
                "team": team,
                "opp": "BUF" if team == "KC" else "KC",
                "salary": sal,
                "proj_fp": round(proj_fp, 3),
                "floor": round(max(0.1, proj_fp - std * 0.84), 3),
                "ceiling": round(proj_fp + std * 0.84, 3),
                "std": std,
                "own_est": "",
                "cpt_salary": cpt_sal,
                "cpt_proj": cpt_proj,
                "flex_value": round(proj_fp / (sal / 1000.0), 3),
                "cpt_value": round(cpt_proj / (cpt_sal / 1000.0), 3),
                "roster_positions": "CPT/FLEX",
                "sources": "demo",
                "notes": "ESTIMATE: demo synthetic",
            }
        )
    return players


def load_pool_csv(path: Path) -> list[dict]:
    """Load DK Showdown pool (flexible column names)."""
    players = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            name = (row.get("name") or row.get("Name") or "").strip()
            if not dk_id and not name:
                continue
            sal = safe_float(row.get("salary") or row.get("Salary"), 0) or 0
            roster = (
                row.get("roster_positions")
                or row.get("Roster Position")
                or row.get("Position")
                or "CPT/FLEX"
            )
            players.append(
                {
                    "dk_id": dk_id or name,
                    "name": name or dk_id,
                    "position": (row.get("position") or row.get("Position") or "").strip(),
                    "team": (row.get("team") or row.get("TeamAbbrev") or row.get("Team") or "").strip(),
                    "opp": (row.get("opp") or "").strip(),
                    "salary": int(sal),
                    "roster_positions": str(roster).upper(),
                    "proj_fp": None,
                    "std": None,
                }
            )
    return players


def load_projections_csv(path: Path) -> dict[str, dict]:
    """Load Showdown projections per PROJECTIONS.md schema → keyed by dk_id."""
    out: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            if not dk_id:
                continue
            proj = safe_float(row.get("proj_fp"))
            if proj is None:
                continue
            std = safe_float(row.get("std"))
            floor = safe_float(row.get("floor"))
            ceiling = safe_float(row.get("ceiling"))
            if std is None and floor is not None and ceiling is not None:
                # ~20th/80th ≈ ±0.84σ
                std = max(1.0, (ceiling - floor) / 1.68)
            if std is None:
                std = max(3.0, proj * 0.40)
            sal = safe_float(row.get("salary") or row.get("cpt_salary"))
            cpt_sal = safe_float(row.get("cpt_salary"))
            if cpt_sal is None and sal is not None:
                cpt_sal = sal * CPT_MULT
            cpt_proj = safe_float(row.get("cpt_proj"))
            if cpt_proj is None:
                cpt_proj = proj * CPT_MULT
            out[dk_id] = {
                "dk_id": dk_id,
                "name": (row.get("name") or "").strip(),
                "position": (row.get("position") or "").strip(),
                "team": (row.get("team") or "").strip(),
                "opp": (row.get("opp") or "").strip(),
                "salary": int(sal) if sal is not None else None,
                "proj_fp": proj,
                "floor": floor,
                "ceiling": ceiling,
                "std": std,
                "own_est": safe_float(row.get("own_est")),
                "cpt_salary": int(cpt_sal) if cpt_sal is not None else None,
                "cpt_proj": cpt_proj,
                "flex_value": safe_float(row.get("flex_value")),
                "cpt_value": safe_float(row.get("cpt_value")),
                "sources": (row.get("sources") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
            }
    return out


def merge_pool_proj(pool: list[dict], proj: dict[str, dict]) -> list[dict]:
    """Join pool salaries/eligibility with projection means/std."""
    merged = []
    for p in pool:
        dk_id = p["dk_id"]
        pr = proj.get(dk_id, {})
        salary = p.get("salary") or pr.get("salary") or 0
        proj_fp = pr.get("proj_fp")
        if proj_fp is None:
            proj_fp = p.get("proj_fp")
        if proj_fp is None:
            continue  # skip unprojected
        std = pr.get("std") or p.get("std") or max(3.0, float(proj_fp) * 0.40)
        roster = p.get("roster_positions") or "CPT/FLEX"
        # CPT/FLEX eligibility: DK Showdown usually both; allow CPT-only / FLEX-only
        can_cpt = "CPT" in str(roster).upper() or str(roster).upper() in ("", "CPT/FLEX")
        can_flex = "FLEX" in str(roster).upper() or str(roster).upper() in ("", "CPT/FLEX")
        if not can_cpt and not can_flex:
            can_cpt = can_flex = True
        cpt_sal = pr.get("cpt_salary") or int(round(salary * CPT_MULT))
        merged.append(
            {
                "dk_id": dk_id,
                "name": pr.get("name") or p.get("name") or dk_id,
                "position": (pr.get("position") or p.get("position") or "").upper(),
                "team": (pr.get("team") or p.get("team") or "").upper(),
                "opp": (pr.get("opp") or p.get("opp") or "").upper(),
                "salary": int(salary),
                "cpt_salary": int(cpt_sal),
                "proj_fp": float(proj_fp),
                "std": float(std),
                "can_cpt": can_cpt,
                "can_flex": can_flex,
                "own_est": pr.get("own_est"),
            }
        )
    return merged


def sample_scripts(n: int, rng: np.random.Generator) -> list[dict]:
    """Draw game-script paths: pace, pass/run tilt, margin (blowout vs close)."""
    scripts = []
    for i in range(n):
        # total game pace proxy (combined team totals tilt)
        pace = float(rng.normal(48.0, 8.0))  # combined implied points-ish
        # home (team A) share of scoring; blowout if extreme
        home_share = float(rng.beta(5, 5))  # centered ~0.5
        margin = (home_share - 0.5) * pace * 2.0  # positive = home ahead
        # pass tilt: >0.5 more pass-heavy league game
        pass_tilt = float(rng.beta(4.5, 4.0))
        # weather / grind factor: 1.0 normal, <1 down environment
        weather = float(np.clip(rng.normal(1.0, 0.08), 0.75, 1.15))
        if abs(margin) >= 14:
            tag = "blowout"
        elif pace >= 52 and pass_tilt >= 0.55:
            tag = "shootout"
        elif pace <= 40 or weather < 0.88:
            tag = "grind"
        else:
            tag = "close"
        scripts.append(
            {
                "script_id": i,
                "pace": pace,
                "home_share": home_share,
                "margin": margin,
                "pass_tilt": pass_tilt,
                "weather": weather,
                "tag": tag,
            }
        )
    return scripts


def script_player_means(players: list[dict], script: dict, teams: list[str]) -> np.ndarray:
    """Adjust each player's mean FP for this game script (team/game correlation)."""
    home = teams[0] if teams else ""
    away = teams[1] if len(teams) > 1 else ""
    means = np.zeros(len(players), dtype=np.float64)
    pace_factor = script["pace"] / 48.0
    weather = script["weather"]
    pass_tilt = script["pass_tilt"]
    home_share = script["home_share"]
    margin = script["margin"]

    for i, p in enumerate(players):
        mu = p["proj_fp"]
        team = p["team"]
        pos = p["position"]
        # team scoring share
        if team == home:
            team_share = home_share
            winning = margin > 7
            losing = margin < -7
        elif team == away:
            team_share = 1.0 - home_share
            winning = margin < -7
            losing = margin > 7
        else:
            team_share = 0.5
            winning = losing = False

        share_tilt = 0.85 + 0.30 * team_share  # more scoring env → more FP
        # position × pass/run
        if pos == "QB" or pos == "WR" or pos == "TE":
            style = 0.80 + 0.40 * pass_tilt
        elif pos == "RB":
            style = 1.15 - 0.35 * pass_tilt
            if winning:
                style *= 1.08  # positive game script for RB
            if losing:
                style *= 0.92
        elif pos == "DST":
            # DST likes opponent struggle / blowout lead
            style = 1.05 if winning else (0.90 if losing else 1.0)
            style *= 1.10 - 0.15 * (pace_factor - 1.0)  # lower pace slightly better
        elif pos == "K":
            style = 0.95 + 0.10 * pace_factor
        else:
            style = 1.0

        # blowout: trailing pass-catchers get slight bump late; leading RBs bump already applied
        if script["tag"] == "blowout" and losing and pos in ("QB", "WR", "TE"):
            style *= 1.06
        if script["tag"] == "shootout" and pos in ("QB", "WR", "TE"):
            style *= 1.05

        adj = mu * pace_factor * weather * share_tilt * style
        means[i] = max(0.05, adj)
    return means


def sample_outcomes(
    means: np.ndarray,
    stds: np.ndarray,
    teams_idx: np.ndarray,
    pos_codes: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Sample correlated FP: shared team latent + independent residual."""
    n = len(means)
    # team latent shocks (2 teams typically)
    unique_teams = np.unique(teams_idx)
    team_shock = {int(t): float(rng.normal(0, 1.0)) for t in unique_teams}
    # pass-game latent per team for QB/WR/TE correlation
    pass_shock = {int(t): float(rng.normal(0, 1.0)) for t in unique_teams}

    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        t = int(teams_idx[i])
        z_ind = float(rng.normal(0, 1.0))
        z_team = team_shock.get(t, 0.0)
        z_pass = pass_shock.get(t, 0.0)
        pos = int(pos_codes[i])
        # pos: 0=QB, 1=WR/TE, 2=RB, 3=DST/K/other
        if pos == 0:  # QB
            z = 0.45 * z_team + 0.40 * z_pass + 0.50 * z_ind
        elif pos == 1:  # WR/TE
            z = 0.35 * z_team + 0.45 * z_pass + 0.55 * z_ind
        elif pos == 2:  # RB — negative-ish vs pass shock
            z = 0.40 * z_team - 0.15 * z_pass + 0.65 * z_ind
        else:
            z = 0.30 * z_team + 0.75 * z_ind
        # normalize roughly
        z = z / 1.05
        out[i] = means[i] + stds[i] * z
    return out


def pos_code(pos: str) -> int:
    p = (pos or "").upper()
    if p == "QB":
        return 0
    if p in ("WR", "TE"):
        return 1
    if p == "RB":
        return 2
    return 3


def build_best_lineup(
    players: list[dict],
    fp: np.ndarray,
) -> tuple[int, list[int], float, int] | None:
    """Greedy-optimal Showdown: try each CPT, pick top FLEX under remaining salary.

    Returns (cpt_idx, flex_idxs, total_fp, salary_used) or None.
    CPT scores 1.5x sampled FP; salary uses 1.5x.
    """
    n = len(players)
    best = None  # (score, cpt_i, flex_list, sal)
    # Pre-sort flex candidates by FP/salary value then FP for greedy fill
    for cpt_i in range(n):
        if not players[cpt_i]["can_cpt"]:
            continue
        cpt_sal = players[cpt_i]["cpt_salary"]
        if cpt_sal >= SALARY_CAP:
            continue
        rem = SALARY_CAP - cpt_sal
        cpt_fp = float(fp[cpt_i]) * CPT_MULT
        # candidates: not cpt, can flex, salary fits eventually
        cands = []
        for j in range(n):
            if j == cpt_i or not players[j]["can_flex"]:
                continue
            cands.append((float(fp[j]), players[j]["salary"], j))
        # sort by FP desc for primary greedy; then knapsack-ish repair
        cands.sort(key=lambda x: (-x[0], x[1], x[2]))
        chosen: list[int] = []
        used = 0
        for fpts, sal, j in cands:
            if len(chosen) >= N_FLEX:
                break
            if used + sal <= rem:
                chosen.append(j)
                used += sal
        if len(chosen) < N_FLEX:
            # try value sort fallback for leftover cheap pieces
            leftover = [c for c in cands if c[2] not in chosen]
            leftover.sort(key=lambda x: (-(x[0] / max(1, x[1] / 1000.0)), -x[0]))
            for fpts, sal, j in leftover:
                if len(chosen) >= N_FLEX:
                    break
                if used + sal <= rem:
                    chosen.append(j)
                    used += sal
        if len(chosen) < N_FLEX:
            continue
        # local improvement: if salary left, swap in higher FP within rem
        chosen_set = set(chosen)
        improved = True
        while improved:
            improved = False
            for k, j in enumerate(list(chosen)):
                for fpts, sal, alt in cands:
                    if alt in chosen_set or alt == cpt_i:
                        continue
                    new_used = used - players[j]["salary"] + sal
                    if new_used <= rem and fpts > float(fp[j]):
                        chosen_set.remove(j)
                        chosen_set.add(alt)
                        chosen[k] = alt
                        used = new_used
                        improved = True
                        break
                if improved:
                    break

        total = cpt_fp + float(sum(fp[j] for j in chosen))
        total_sal = cpt_sal + used
        if best is None or total > best[0]:
            best = (total, cpt_i, list(chosen), total_sal)
    if best is None:
        return None
    return best[1], best[2], best[0], best[3]


def lineup_key(cpt_id: str, flex_ids: list[str]) -> str:
    return cpt_id + "|" + ",".join(sorted(flex_ids))


def run_sim(
    players: list[dict],
    n_sims: int,
    seed: int,
) -> tuple[list[dict], dict, list[dict]]:
    """Run N scripts; return lineup frequency rows, exposure dict, script summaries."""
    rng = np.random.default_rng(seed)
    teams = sorted({p["team"] for p in players if p["team"]})
    if len(teams) < 2:
        teams = teams + ["AWAY"] if teams else ["HOME", "AWAY"]
    team_to_idx = {t: i for i, t in enumerate(teams)}
    teams_idx = np.array([team_to_idx.get(p["team"], 0) for p in players], dtype=np.int32)
    pos_codes = np.array([pos_code(p["position"]) for p in players], dtype=np.int32)
    stds = np.array([p["std"] for p in players], dtype=np.float64)

    scripts = sample_scripts(n_sims, rng)
    lineup_counts: Counter = Counter()
    lineup_scores: dict[str, list[float]] = defaultdict(list)
    lineup_meta: dict[str, dict] = {}
    cpt_exp: Counter = Counter()
    flex_exp: Counter = Counter()
    any_exp: Counter = Counter()
    tag_counts: Counter = Counter()
    built = 0

    for script in scripts:
        tag_counts[script["tag"]] += 1
        means = script_player_means(players, script, teams)
        fp = sample_outcomes(means, stds, teams_idx, pos_codes, rng)
        result = build_best_lineup(players, fp)
        if result is None:
            continue
        cpt_i, flex_is, score, sal = result
        built += 1
        cpt = players[cpt_i]
        flex = [players[j] for j in flex_is]
        cpt_id = cpt["dk_id"]
        flex_ids = [p["dk_id"] for p in flex]
        key = lineup_key(cpt_id, flex_ids)
        lineup_counts[key] += 1
        lineup_scores[key].append(score)
        if key not in lineup_meta:
            lineup_meta[key] = {
                "cpt_id": cpt_id,
                "cpt_name": cpt["name"],
                "flex_ids": flex_ids,
                "flex_names": [p["name"] for p in flex],
                "salary": sal,
            }
        cpt_exp[cpt_id] += 1
        for fid in flex_ids:
            flex_exp[fid] += 1
        any_exp[cpt_id] += 1
        for fid in flex_ids:
            any_exp[fid] += 1

    rows = []
    for key, cnt in lineup_counts.most_common():
        scores = lineup_scores[key]
        meta = lineup_meta[key]
        rows.append(
            {
                "rank": 0,  # filled below
                "frequency": cnt,
                "freq_pct": round(100.0 * cnt / max(1, built), 4),
                "mean_fp": round(float(np.mean(scores)), 3),
                "p50_fp": round(float(np.percentile(scores, 50)), 3),
                "p90_fp": round(float(np.percentile(scores, 90)), 3),
                "salary": meta["salary"],
                "cpt_id": meta["cpt_id"],
                "cpt_name": meta["cpt_name"],
                "flex_ids": "|".join(meta["flex_ids"]),
                "flex_names": "|".join(meta["flex_names"]),
                "lineup_key": key,
            }
        )
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    id_to_name = {p["dk_id"]: p["name"] for p in players}
    exposure = {
        "n_built": built,
        "n_sims": n_sims,
        "cpt": {
            pid: {
                "name": id_to_name.get(pid, pid),
                "count": c,
                "pct": round(100.0 * c / max(1, built), 3),
            }
            for pid, c in cpt_exp.most_common()
        },
        "flex": {
            pid: {
                "name": id_to_name.get(pid, pid),
                "count": c,
                "pct": round(100.0 * c / max(1, built), 3),
            }
            for pid, c in flex_exp.most_common()
        },
        "any": {
            pid: {
                "name": id_to_name.get(pid, pid),
                "count": c,
                "pct": round(100.0 * c / max(1, built), 3),
            }
            for pid, c in any_exp.most_common()
        },
        "script_tags": dict(tag_counts),
        "teams": teams,
    }
    return rows, exposure, scripts


def write_outputs(
    rows: list[dict],
    exposure: dict,
    out_csv: Path,
    summary_path: Path,
    n_sims: int,
    seed: int,
    demo: bool,
) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank",
        "frequency",
        "freq_pct",
        "mean_fp",
        "p50_fp",
        "p90_fp",
        "salary",
        "cpt_id",
        "cpt_name",
        "flex_ids",
        "flex_names",
        "lineup_key",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    built = exposure["n_built"]
    lines = []
    lines.append("NFL DFS lab — Showdown game-script simulation")
    lines.append(f"mode={'demo' if demo else 'live-inputs'}  sims={n_sims}  built={built}  seed={seed}")
    lines.append(f"teams={','.join(exposure['teams'])}  salary_cap={SALARY_CAP}  cpt_mult={CPT_MULT}")
    lines.append(f"script_tags={exposure['script_tags']}")
    lines.append("")
    lines.append("Top 10 lineups by frequency:")
    for r in rows[:10]:
        lines.append(
            f"  #{r['rank']:>3}  freq={r['frequency']:>5} ({r['freq_pct']:.2f}%)  "
            f"mean={r['mean_fp']:.1f}  p90={r['p90_fp']:.1f}  sal={r['salary']}  "
            f"CPT={r['cpt_name']}  FLEX={r['flex_names'].replace('|', ', ')}"
        )
    lines.append("")
    lines.append("Top CPT exposure:")
    for i, (pid, info) in enumerate(list(exposure["cpt"].items())[:8]):
        lines.append(f"  {info['pct']:6.2f}%  CPT  {info['name']} ({pid})")
    lines.append("")
    lines.append("Top FLEX exposure:")
    for i, (pid, info) in enumerate(list(exposure["flex"].items())[:10]):
        lines.append(f"  {info['pct']:6.2f}%  FLEX {info['name']} ({pid})")
    lines.append("")
    lines.append("Expected portfolio note: diversify across script tags; soft ~40% player exposure default.")
    lines.append(f"Full CSV: {out_csv}")
    lines.append("Gates apply next build only — never patch mid-slate live uploads.")

    # also write a compact exposure CSV sidecar for contest_pricing / flashback
    exp_path = LAB / "sim-results-showdown-exposure.csv"
    with exp_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["dk_id", "name", "cpt_pct", "flex_pct", "any_pct", "cpt_n", "flex_n"]
        )
        w.writeheader()
        all_ids = set(exposure["cpt"]) | set(exposure["flex"]) | set(exposure["any"])
        for pid in sorted(all_ids, key=lambda x: -exposure["any"].get(x, {}).get("pct", 0)):
            c = exposure["cpt"].get(pid, {})
            fl = exposure["flex"].get(pid, {})
            a = exposure["any"].get(pid, {})
            w.writerow(
                {
                    "dk_id": pid,
                    "name": a.get("name") or c.get("name") or fl.get("name") or pid,
                    "cpt_pct": c.get("pct", 0.0),
                    "flex_pct": fl.get("pct", 0.0),
                    "any_pct": a.get("pct", 0.0),
                    "cpt_n": c.get("count", 0),
                    "flex_n": fl.get("count", 0),
                }
            )

    text = "\n".join(lines) + "\n"
    summary_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote exposure: {exp_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="NFL Showdown game-script simulator")
    ap.add_argument("--demo", action="store_true", help="Synthetic 2-team pool; no real inputs")
    ap.add_argument("--pool", type=Path, default=DEFAULT_POOL)
    ap.add_argument("--projections", type=Path, default=DEFAULT_PROJ)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("-n", "--sims", type=int, default=DEFAULT_N)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args()

    if args.demo:
        raw = synthesize_demo_pool(args.seed)
        players = merge_pool_proj(
            [
                {
                    "dk_id": p["dk_id"],
                    "name": p["name"],
                    "position": p["position"],
                    "team": p["team"],
                    "opp": p["opp"],
                    "salary": p["salary"],
                    "roster_positions": p["roster_positions"],
                    "proj_fp": p["proj_fp"],
                    "std": p["std"],
                }
                for p in raw
            ],
            {p["dk_id"]: p for p in raw},
        )
    else:
        if not args.pool.exists() and not args.projections.exists():
            print(
                "ERROR: need --pool and/or --projections, or pass --demo",
                file=sys.stderr,
            )
            return 1
        pool = load_pool_csv(args.pool) if args.pool.exists() else []
        proj = load_projections_csv(args.projections) if args.projections.exists() else {}
        if not pool and proj:
            # projections-only: treat as pool
            pool = [
                {
                    "dk_id": v["dk_id"],
                    "name": v["name"],
                    "position": v["position"],
                    "team": v["team"],
                    "opp": v.get("opp", ""),
                    "salary": v.get("salary") or 0,
                    "roster_positions": "CPT/FLEX",
                }
                for v in proj.values()
            ]
        players = merge_pool_proj(pool, proj)

    if len(players) < 6:
        print(f"ERROR: need ≥6 projected players, got {len(players)}", file=sys.stderr)
        return 1

    print(f"Players={len(players)}  sims={args.sims}  seed={args.seed}  demo={args.demo}")
    rows, exposure, _scripts = run_sim(players, args.sims, args.seed)
    if not rows:
        print("ERROR: no legal lineups built across scripts", file=sys.stderr)
        return 1
    write_outputs(rows, exposure, args.out, args.summary, args.sims, args.seed, args.demo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
