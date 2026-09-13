"""Game-script sampling and correlated fantasy-point outcomes."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from nfl_dfs.showdown_rules import Player


def sample_scripts(n: int, rng: np.random.Generator) -> list[dict]:
    """Draw game-script paths: pace, pass/run tilt, margin (blowout vs close)."""
    scripts: list[dict] = []
    for i in range(n):
        pace = float(rng.normal(48.0, 8.0))
        home_share = float(rng.beta(5, 5))
        margin = (home_share - 0.5) * pace * 2.0
        pass_tilt = float(rng.beta(4.5, 4.0))
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


def pos_code(pos: str) -> int:
    p = (pos or "").upper()
    if p == "QB":
        return 0
    if p in ("WR", "TE"):
        return 1
    if p == "RB":
        return 2
    return 3


def script_player_means(
    players: Sequence[Player], script: dict, teams: list[str]
) -> np.ndarray:
    """Adjust each player's mean FP for this game script (incl. user boost)."""
    home = teams[0] if teams else ""
    away = teams[1] if len(teams) > 1 else ""
    means = np.zeros(len(players), dtype=np.float64)
    pace_factor = script["pace"] / 48.0
    weather = script["weather"]
    pass_tilt = script["pass_tilt"]
    home_share = script["home_share"]
    margin = script["margin"]

    for i, p in enumerate(players):
        mu = p.proj_fp * p.boost
        team = p.team
        pos = p.position
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

        share_tilt = 0.85 + 0.30 * team_share
        if pos in ("QB", "WR", "TE"):
            style = 0.80 + 0.40 * pass_tilt
        elif pos == "RB":
            style = 1.15 - 0.35 * pass_tilt
            if winning:
                style *= 1.08
            if losing:
                style *= 0.92
        elif pos == "DST":
            style = 1.05 if winning else (0.90 if losing else 1.0)
            style *= 1.10 - 0.15 * (pace_factor - 1.0)
        elif pos == "K":
            style = 0.95 + 0.10 * pace_factor
        else:
            style = 1.0

        if script["tag"] == "blowout" and losing and pos in ("QB", "WR", "TE"):
            style *= 1.06
        if script["tag"] == "shootout" and pos in ("QB", "WR", "TE"):
            style *= 1.05

        means[i] = max(0.05, mu * pace_factor * weather * share_tilt * style)
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
    unique_teams = np.unique(teams_idx)
    team_shock = {int(t): float(rng.normal(0, 1.0)) for t in unique_teams}
    pass_shock = {int(t): float(rng.normal(0, 1.0)) for t in unique_teams}

    out = np.zeros(n, dtype=np.float64)
    for i in range(n):
        t = int(teams_idx[i])
        z_ind = float(rng.normal(0, 1.0))
        z_team = team_shock.get(t, 0.0)
        z_pass = pass_shock.get(t, 0.0)
        pos = int(pos_codes[i])
        if pos == 0:
            z = 0.45 * z_team + 0.40 * z_pass + 0.50 * z_ind
        elif pos == 1:
            z = 0.35 * z_team + 0.45 * z_pass + 0.55 * z_ind
        elif pos == 2:
            z = 0.40 * z_team - 0.15 * z_pass + 0.65 * z_ind
        else:
            z = 0.30 * z_team + 0.75 * z_ind
        z = z / 1.05
        out[i] = max(0.0, means[i] + stds[i] * z)
    return out


def prepare_arrays(players: Sequence[Player]) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    teams = sorted({p.team for p in players if p.team})
    if len(teams) < 2:
        teams = (teams + ["AWAY"]) if teams else ["HOME", "AWAY"]
    team_to_idx = {t: i for i, t in enumerate(teams)}
    teams_idx = np.array([team_to_idx.get(p.team, 0) for p in players], dtype=np.int32)
    pos_codes = np.array([pos_code(p.position) for p in players], dtype=np.int32)
    stds = np.array([p.std for p in players], dtype=np.float64)
    return teams, teams_idx, pos_codes, stds
