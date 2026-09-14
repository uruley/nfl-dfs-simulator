"""Possession-level score-path model (SaberSim-style).

For ONE NFL game (Showdown) or per-game on a Classic slate:
  1. Sample a game path (possessions, margin lean, pass rates).
  2. Allocate team yards / TDs / turnovers at possession grain.
  3. Distribute production to players via projection-derived usage shares
     (proj_fp × boost as relative weights within team/position groups).
  4. Convert box-score totals → DK fantasy points (scoring.py + DST/K heuristics).
  5. Return per-player realized FP + path summary dict.

Designed to be fast: 500 Showdown paths on ~25 players finishes in a few seconds.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Protocol, Sequence

import numpy as np

from nfl_dfs.scoring import StatLine, score_stats
from nfl_dfs.scripts import sample_scripts


class _HasProj(Protocol):
    dk_id: str
    name: str
    position: str
    team: str
    proj_fp: float
    boost: float


# ---------------------------------------------------------------------------
# Path sampling
# ---------------------------------------------------------------------------


def _resolve_teams(players: Sequence[_HasProj], teams: list[str] | None) -> list[str]:
    if teams and len(teams) >= 2:
        return [teams[0], teams[1]]
    found = sorted({p.team for p in players if getattr(p, "team", None)})
    if len(found) >= 2:
        return found[:2]
    if len(found) == 1:
        return [found[0], "AWAY"]
    return ["HOME", "AWAY"]


def sample_game_path(
    rng: np.random.Generator,
    *,
    spread: float | None = None,
    total: float | None = None,
    script_id: int = 0,
) -> dict[str, Any]:
    """Sample coarse game-path parameters (seeded via ``rng``).

    Reuses Vegas-aware script priors from ``scripts.sample_scripts``, then
    derives possession count and state-dependent pass rates.
    """
    sc = sample_scripts(1, rng, spread=spread, total=total)[0]
    pace = float(sc["pace"])
    # Total possessions both teams ≈ pace scaled into NFL drive range.
    # pace~48 → ~24 possessions/team-ish; map to total drives ~20–30.
    n_poss = int(np.clip(round(pace * 0.48 + float(rng.normal(0, 1.5))), 18, 34))
    home_share = float(sc["home_share"])
    margin = float(sc["margin"])
    pass_tilt = float(sc["pass_tilt"])
    weather = float(sc["weather"])
    tag = sc["tag"]

    # Base pass rates; trailing team gets +pass later during allocation.
    pass_home = float(np.clip(pass_tilt + 0.02 * (margin < 0), 0.35, 0.78))
    pass_away = float(np.clip(pass_tilt + 0.02 * (margin > 0), 0.35, 0.78))

    return {
        "script_id": script_id,
        "n_possessions": n_poss,
        "pace": pace,
        "home_share": home_share,
        "margin": margin,
        "final_margin": margin,  # refined during allocation
        "pass_tilt": pass_tilt,
        "pass_rate_home": pass_home,
        "pass_rate_away": pass_away,
        "weather": weather,
        "tag": tag,
        "vegas_spread": spread,
        "vegas_total": total,
    }


# ---------------------------------------------------------------------------
# Team box allocation (possession-level, coarse)
# ---------------------------------------------------------------------------


def _allocate_team_boxes(
    path: dict[str, Any],
    home: str,
    away: str,
    rng: np.random.Generator,
) -> dict[str, dict[str, float]]:
    """Walk possessions; accumulate yards/TDs/turnovers per team."""
    n = int(path["n_possessions"])
    home_share = float(path["home_share"])
    weather = float(path["weather"])
    pass_home = float(path["pass_rate_home"])
    pass_away = float(path["pass_rate_away"])
    # Soft lean: home gets slightly more possessions when favored.
    home_poss_p = float(np.clip(0.48 + 0.12 * (home_share - 0.5), 0.38, 0.62))

    boxes: dict[str, dict[str, float]] = {
        home: {
            "pass_yd": 0.0,
            "pass_td": 0.0,
            "rush_yd": 0.0,
            "rush_td": 0.0,
            "interceptions": 0.0,
            "fumbles_lost": 0.0,
            "receptions": 0.0,
            "points": 0.0,
            "sacks_allowed": 0.0,
            "pass_att": 0.0,
            "rush_att": 0.0,
        },
        away: {
            "pass_yd": 0.0,
            "pass_td": 0.0,
            "rush_yd": 0.0,
            "rush_td": 0.0,
            "interceptions": 0.0,
            "fumbles_lost": 0.0,
            "receptions": 0.0,
            "points": 0.0,
            "sacks_allowed": 0.0,
            "pass_att": 0.0,
            "rush_att": 0.0,
        },
    }

    score_h = 0.0
    score_a = 0.0

    for _ in range(n):
        is_home = bool(rng.random() < home_poss_p)
        team = home if is_home else away
        opp = away if is_home else home
        box = boxes[team]
        cur_margin = score_h - score_a if is_home else score_a - score_h
        # Trailing → more pass.
        base_pass = pass_home if is_home else pass_away
        if cur_margin < -7:
            p_pass = float(np.clip(base_pass + 0.12, 0.40, 0.85))
        elif cur_margin > 10:
            p_pass = float(np.clip(base_pass - 0.10, 0.28, 0.70))
        else:
            p_pass = base_pass

        is_pass = bool(rng.random() < p_pass)
        # Yards per possession (coarse).
        if is_pass:
            yds = float(max(0.0, rng.normal(18.0, 14.0) * weather))
            box["pass_yd"] += yds
            box["pass_att"] += float(max(1, round(yds / 7.5 + rng.uniform(0.5, 2.5))))
            # Completions proxy for PPR distribution later.
            comps = float(max(0, round(yds / 11.0 + rng.normal(0.3, 0.8))))
            box["receptions"] += comps
            # Sack chance (recorded on offense as sacks faced)
            if rng.random() < 0.07:
                box["sacks_allowed"] += 1.0
            # INT
            if rng.random() < 0.035:
                box["interceptions"] += 1.0
                continue  # turnover ends drive, no score
            # TD chance scales with yards
            td_p = float(np.clip(0.04 + yds / 220.0, 0.03, 0.28))
            if rng.random() < td_p:
                box["pass_td"] += 1.0
                box["points"] += 7.0
                if is_home:
                    score_h += 7.0
                else:
                    score_a += 7.0
            elif yds > 35 and rng.random() < 0.35:
                # FG
                box["points"] += 3.0
                if is_home:
                    score_h += 3.0
                else:
                    score_a += 3.0
        else:
            yds = float(max(0.0, rng.normal(12.0, 9.0) * (0.95 + 0.05 * weather)))
            box["rush_yd"] += yds
            box["rush_att"] += float(max(1, round(yds / 4.2 + rng.uniform(0.2, 1.5))))
            if rng.random() < 0.018:
                box["fumbles_lost"] += 1.0
                continue
            td_p = float(np.clip(0.03 + yds / 180.0, 0.02, 0.25))
            if rng.random() < td_p:
                box["rush_td"] += 1.0
                box["points"] += 7.0
                if is_home:
                    score_h += 7.0
                else:
                    score_a += 7.0
            elif yds > 28 and rng.random() < 0.30:
                box["points"] += 3.0
                if is_home:
                    score_h += 3.0
                else:
                    score_a += 3.0

    # Soft pull final scores toward path prior margin (blend).
    prior_margin = float(path["margin"])
    realized = score_h - score_a
    blended = 0.65 * realized + 0.35 * prior_margin
    path["final_margin"] = blended
    path["home_points"] = score_h
    path["away_points"] = score_a
    path["pass_rate"] = float(
        (boxes[home]["pass_att"] + boxes[away]["pass_att"])
        / max(
            1.0,
            boxes[home]["pass_att"]
            + boxes[away]["pass_att"]
            + boxes[home]["rush_att"]
            + boxes[away]["rush_att"],
        )
    )
    # Retag from realized margin / pace.
    if abs(blended) >= 14:
        path["tag"] = "blowout"
    elif path["pace"] >= 52 and path["pass_tilt"] >= 0.55:
        path["tag"] = "shootout"
    elif path["pace"] <= 40 or path["weather"] < 0.88:
        path["tag"] = "grind"
    else:
        path["tag"] = "close"

    boxes[home]["points"] = score_h
    boxes[away]["points"] = score_a
    return boxes


# ---------------------------------------------------------------------------
# Usage shares + distribution
# ---------------------------------------------------------------------------


def _usage_weights(
    players: Sequence[_HasProj],
    team: str,
    positions: set[str],
) -> list[tuple[int, float]]:
    """(index, weight) for players on ``team`` in ``positions``; proj×boost."""
    out: list[tuple[int, float]] = []
    for i, p in enumerate(players):
        if p.team != team:
            continue
        if (p.position or "").upper() not in positions:
            continue
        w = max(0.01, float(p.proj_fp) * float(getattr(p, "boost", 1.0) or 1.0))
        out.append((i, w))
    return out


def _dirichlet_like(weights: list[float], rng: np.random.Generator, conc: float = 40.0) -> np.ndarray:
    """Noisy share vector around normalized weights (Gamma stick-breaking proxy)."""
    w = np.asarray(weights, dtype=np.float64)
    if w.size == 0:
        return w
    w = np.maximum(w, 1e-6)
    w = w / w.sum()
    alpha = np.maximum(w * conc, 0.15)
    draws = rng.gamma(alpha, 1.0)
    s = draws.sum()
    if s <= 0:
        return w
    return draws / s


def _distribute_skill(
    players: Sequence[_HasProj],
    team: str,
    box: dict[str, float],
    rng: np.random.Generator,
    stats: list[StatLine],
) -> None:
    """Allocate team box to QB / RB / WR / TE StatLines in-place."""
    # QB: all pass_yd / pass_td / INT; small rush share.
    qbs = _usage_weights(players, team, {"QB"})
    if qbs:
        idxs = [i for i, _ in qbs]
        shares = _dirichlet_like([w for _, w in qbs], rng, conc=60.0)
        for j, i in enumerate(idxs):
            sh = float(shares[j])
            # Primary QB usually dominates — reinforce top weight.
            s = stats[i]
            stats[i] = StatLine(
                pass_yd=s.pass_yd + box["pass_yd"] * sh,
                pass_td=s.pass_td + box["pass_td"] * sh,
                interceptions=s.interceptions + box["interceptions"] * sh,
                rush_yd=s.rush_yd,
                rush_td=s.rush_td,
                rec_yd=s.rec_yd,
                rec_td=s.rec_td,
                receptions=s.receptions,
                fumbles_lost=s.fumbles_lost,
                two_pt=s.two_pt,
            )
        # QB rush: ~55% of team rush to QB is wrong; QBs get ~8–15% of rush yards.
        qb_rush_frac = float(np.clip(rng.normal(0.12, 0.04), 0.04, 0.25))
        top_qb = max(qbs, key=lambda t: t[1])[0]
        s = stats[top_qb]
        stats[top_qb] = StatLine(
            pass_yd=s.pass_yd,
            pass_td=s.pass_td,
            interceptions=s.interceptions,
            rush_yd=s.rush_yd + box["rush_yd"] * qb_rush_frac,
            rush_td=s.rush_td + box["rush_td"] * qb_rush_frac * 0.5,
            rec_yd=s.rec_yd,
            rec_td=s.rec_td,
            receptions=s.receptions,
            fumbles_lost=s.fumbles_lost + box["fumbles_lost"] * 0.15,
            two_pt=s.two_pt,
        )
        rb_rush_frac = 1.0 - qb_rush_frac
    else:
        rb_rush_frac = 1.0

    # RB rush yards / rush TDs (+ light receiving).
    rbs = _usage_weights(players, team, {"RB"})
    if rbs:
        idxs = [i for i, _ in rbs]
        shares = _dirichlet_like([w for _, w in rbs], rng, conc=25.0)
        rec_frac_rb = float(np.clip(rng.normal(0.22, 0.06), 0.08, 0.40))
        for j, i in enumerate(idxs):
            sh = float(shares[j])
            s = stats[i]
            stats[i] = StatLine(
                pass_yd=s.pass_yd,
                pass_td=s.pass_td,
                interceptions=s.interceptions,
                rush_yd=s.rush_yd + box["rush_yd"] * rb_rush_frac * sh,
                rush_td=s.rush_td + box["rush_td"] * (1.0 - 0.15) * sh,
                rec_yd=s.rec_yd + box["pass_yd"] * rec_frac_rb * sh * 0.35,
                rec_td=s.rec_td + box["pass_td"] * rec_frac_rb * sh * 0.25,
                receptions=s.receptions + box["receptions"] * rec_frac_rb * sh,
                fumbles_lost=s.fumbles_lost + box["fumbles_lost"] * 0.55 * sh,
                two_pt=s.two_pt,
            )
    else:
        rec_frac_rb = 0.15

    # WR/TE: remaining receiving.
    pass_catch = _usage_weights(players, team, {"WR", "TE"})
    remaining_rec = max(0.0, 1.0 - rec_frac_rb)
    if pass_catch:
        idxs = [i for i, _ in pass_catch]
        shares = _dirichlet_like([w for _, w in pass_catch], rng, conc=20.0)
        for j, i in enumerate(idxs):
            sh = float(shares[j])
            s = stats[i]
            stats[i] = StatLine(
                pass_yd=s.pass_yd,
                pass_td=s.pass_td,
                interceptions=s.interceptions,
                rush_yd=s.rush_yd,
                rush_td=s.rush_td,
                rec_yd=s.rec_yd + box["pass_yd"] * remaining_rec * sh,
                rec_td=s.rec_td + box["pass_td"] * remaining_rec * sh,
                receptions=s.receptions + box["receptions"] * remaining_rec * sh,
                fumbles_lost=s.fumbles_lost,
                two_pt=s.two_pt,
            )


def _dst_fp(points_allowed: float, turnovers: float, sacks: float) -> float:
    """Coarse DK DST scoring from path outcomes."""
    pa = points_allowed
    if pa == 0:
        base = 10.0
    elif pa <= 6:
        base = 7.0
    elif pa <= 13:
        base = 4.0
    elif pa <= 20:
        base = 1.0
    elif pa <= 27:
        base = 0.0
    elif pa <= 34:
        base = -1.0
    else:
        base = -4.0
    return max(0.0, base + 1.0 * sacks + 2.0 * turnovers)


def _k_fp(team_points: float, n_td: float, rng: np.random.Generator) -> float:
    """Estimate kicker FP from team scoring."""
    xp = n_td  # assume most TDs convert
    fg_pts = max(0.0, (team_points - 7.0 * n_td) / 3.0)  # FG count proxy
    # Mix of FG distances: mostly 3-pt DK (0-39), some 4.
    fp = xp * 1.0 + fg_pts * float(rng.choice([3.0, 3.0, 3.0, 4.0, 5.0]))
    return max(0.0, fp)


def _score_players(
    players: Sequence[_HasProj],
    home: str,
    away: str,
    boxes: dict[str, dict[str, float]],
    rng: np.random.Generator,
) -> np.ndarray:
    n = len(players)
    stats = [StatLine() for _ in range(n)]
    for team in (home, away):
        _distribute_skill(players, team, boxes[team], rng, stats)

    fps = np.zeros(n, dtype=np.float64)
    for i, p in enumerate(players):
        pos = (p.position or "").upper()
        if pos == "DST":
            opp = away if p.team == home else home
            opp_box = boxes[opp]
            own_forced = boxes[p.team]["interceptions"]  # weak proxy
            # Turnovers forced ≈ opponent INTs + opponent fumbles (from opp box).
            turnovers = opp_box["interceptions"] + opp_box["fumbles_lost"]
            # Opponent sacks_allowed on offense ≈ sacks by this DST
            sacks = opp_box.get("sacks_allowed", 0.0)
            fps[i] = _dst_fp(opp_box["points"], turnovers, sacks)
        elif pos == "K":
            box = boxes.get(p.team, boxes[home])
            n_td = box["pass_td"] + box["rush_td"]
            fps[i] = _k_fp(box["points"], n_td, rng)
        else:
            fps[i] = max(0.0, score_stats(stats[i]))
    return fps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def realize_game_path(
    players: Sequence[_HasProj],
    rng: np.random.Generator,
    *,
    teams: list[str] | None = None,
    spread: float | None = None,
    total: float | None = None,
    script_id: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Simulate one game path and return (realized_fp[n_players], summary).

    ``players`` may be Showdown ``Player`` or Classic ``ClassicPlayer`` for a
    single game subset. Usage weights = proj_fp × boost within team/position.
    """
    home, away = _resolve_teams(players, teams)
    path = sample_game_path(rng, spread=spread, total=total, script_id=script_id)
    boxes = _allocate_team_boxes(path, home, away, rng)
    fps = _score_players(players, home, away, boxes, rng)

    # Top scorers for summary.
    order = np.argsort(-fps)
    top = [
        {
            "dk_id": players[int(i)].dk_id,
            "name": players[int(i)].name,
            "fp": round(float(fps[int(i)]), 2),
        }
        for i in order[:5]
    ]
    summary: dict[str, Any] = {
        "script_id": script_id,
        "tag": path["tag"],
        "possessions": path["n_possessions"],
        "final_margin": round(float(path["final_margin"]), 2),
        "pass_rate": round(float(path["pass_rate"]), 3),
        "home": home,
        "away": away,
        "home_points": path.get("home_points", 0.0),
        "away_points": path.get("away_points", 0.0),
        "pace": round(float(path["pace"]), 2),
        "pass_tilt": round(float(path["pass_tilt"]), 3),
        "top_scorers": top,
        "vegas_spread": spread,
        "vegas_total": total,
    }
    return fps, summary


def realize_classic_slate(
    players: Sequence[_HasProj],
    rng: np.random.Generator,
    *,
    sim_id: int = 0,
    game_spreads: dict[str, float] | None = None,
    game_totals: dict[str, float] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Per-game scorepath on a Classic slate; merge FPs into one array.

    Groups players by ``game_key`` (or team@opp). Dominant tag = mode across games.
    """
    n = len(players)
    fps = np.zeros(n, dtype=np.float64)
    # Group indices by game
    games: dict[str, list[int]] = {}
    for i, p in enumerate(players):
        gk = getattr(p, "game_key", None) or ""
        if not gk:
            t, o = p.team, getattr(p, "opp", "") or ""
            if t and o:
                pair = tuple(sorted([t.upper(), o.upper()]))
                gk = f"{pair[0]}@{pair[1]}"
            else:
                gk = t or p.dk_id
        games.setdefault(gk, []).append(i)

    tags: list[str] = []
    game_summaries: list[dict] = []
    for g_i, (gk, idxs) in enumerate(sorted(games.items())):
        sub = [players[i] for i in idxs]
        teams_in = sorted({p.team for p in sub if p.team})
        sp = (game_spreads or {}).get(gk)
        tot = (game_totals or {}).get(gk)
        sub_fp, summ = realize_game_path(
            sub,
            rng,
            teams=teams_in if len(teams_in) >= 2 else None,
            spread=sp,
            total=tot,
            script_id=sim_id * 100 + g_i,
        )
        for j, pi in enumerate(idxs):
            fps[pi] = sub_fp[j]
        tags.append(summ["tag"])
        game_summaries.append({"game": gk, **{k: summ[k] for k in ("tag", "possessions", "final_margin", "pass_rate")}})

    tag = Counter(tags).most_common(1)[0][0] if tags else "mixed"
    order = np.argsort(-fps)
    top = [
        {
            "dk_id": players[int(i)].dk_id,
            "name": players[int(i)].name,
            "fp": round(float(fps[int(i)]), 2),
        }
        for i in order[:5]
    ]
    summary: dict[str, Any] = {
        "script_id": sim_id,
        "tag": tag,
        "possessions": int(sum(g.get("possessions", 0) for g in game_summaries)),
        "final_margin": None,
        "pass_rate": float(np.mean([g["pass_rate"] for g in game_summaries])) if game_summaries else 0.0,
        "games": game_summaries,
        "top_scorers": top,
        "n_games": len(games),
    }
    return fps, summary


def write_path_summaries_csv(path: Any, summaries: Sequence[dict[str, Any]], max_rows: int = 200) -> None:
    """Write a sample of path summaries for Lab inspection."""
    import csv
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(summaries)[:max_rows]
    fieldnames = [
        "script_id",
        "tag",
        "possessions",
        "final_margin",
        "pass_rate",
        "home",
        "away",
        "home_points",
        "away_points",
        "top1_name",
        "top1_fp",
        "top2_name",
        "top2_fp",
        "top3_name",
        "top3_fp",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for s in rows:
            tops = s.get("top_scorers") or []
            row = {
                "script_id": s.get("script_id"),
                "tag": s.get("tag"),
                "possessions": s.get("possessions"),
                "final_margin": s.get("final_margin"),
                "pass_rate": s.get("pass_rate"),
                "home": s.get("home", ""),
                "away": s.get("away", ""),
                "home_points": s.get("home_points", ""),
                "away_points": s.get("away_points", ""),
                "top1_name": tops[0]["name"] if len(tops) > 0 else "",
                "top1_fp": tops[0]["fp"] if len(tops) > 0 else "",
                "top2_name": tops[1]["name"] if len(tops) > 1 else "",
                "top2_fp": tops[1]["fp"] if len(tops) > 1 else "",
                "top3_name": tops[2]["name"] if len(tops) > 2 else "",
                "top3_fp": tops[2]["fp"] if len(tops) > 2 else "",
            }
            w.writerow(row)
