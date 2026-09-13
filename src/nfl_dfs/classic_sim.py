"""Classic slate simulation: correlated game-script noise across multi-game slate.

Method (documented):
  For each sim draw:
  1. For every distinct game on the slate, sample a mini game-script
     (pace, pass_tilt, margin, weather) — same generators as Showdown.
  2. Tilt each player's mean FP by their game's script (QB/WR/TE vs RB/DST).
  3. Add correlated residuals: shared team latent + pass-game latent + idiosyncratic.

This is simpler than full Showdown CPT search but keeps cross-player correlation
within games so "stack" outcomes and blowup games appear together.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from nfl_dfs.classic_rules import ClassicPlayer
from nfl_dfs.scripts import pos_code, sample_scripts


def prepare_classic_arrays(
    players: Sequence[ClassicPlayer],
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Return teams list, team_idx, pos_codes, stds, game_keys list (per player)."""
    teams = sorted({p.team for p in players if p.team})
    if not teams:
        teams = ["UNK"]
    team_to_idx = {t: i for i, t in enumerate(teams)}
    teams_idx = np.array([team_to_idx.get(p.team, 0) for p in players], dtype=np.int32)
    pos_codes = np.array([pos_code(p.position) for p in players], dtype=np.int32)
    stds = np.array([p.std for p in players], dtype=np.float64)
    game_keys = [p.game_key or p.team for p in players]
    return teams, teams_idx, pos_codes, stds, game_keys


def _script_mean_for_player(p: ClassicPlayer, script: dict) -> float:
    """Tilt one player's mean for a single-game script (Classic, no CPT)."""
    mu = p.proj_fp * p.boost
    pace_factor = script["pace"] / 48.0
    weather = script["weather"]
    pass_tilt = script["pass_tilt"]
    home_share = script["home_share"]
    margin = script["margin"]
    pos = p.position

    # Treat alphabetically-first team in game_key as "home" for share tilt
    gk = p.game_key or ""
    home = gk.split("@")[0] if "@" in gk else p.team
    if p.team == home:
        team_share = home_share
        winning = margin > 7
        losing = margin < -7
    else:
        team_share = 1.0 - home_share
        winning = margin < -7
        losing = margin > 7

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
    else:
        style = 1.0

    if script["tag"] == "blowout" and losing and pos in ("QB", "WR", "TE"):
        style *= 1.06
    if script["tag"] == "shootout" and pos in ("QB", "WR", "TE"):
        style *= 1.05

    return max(0.05, mu * pace_factor * weather * share_tilt * style)


def sample_classic_outcomes(
    players: Sequence[ClassicPlayer],
    teams_idx: np.ndarray,
    pos_codes: np.ndarray,
    stds: np.ndarray,
    game_keys: list[str],
    rng: np.random.Generator,
    sim_id: int = 0,
) -> tuple[np.ndarray, str]:
    """One Classic sim draw: per-game scripts + correlated noise.

    Returns (fp_array, dominant_tag) where dominant_tag is the mode script tag.
    """
    # One script per distinct game
    unique_games = sorted(set(game_keys))
    scripts_by_game: dict[str, dict] = {}
    for g in unique_games:
        # reuse sample_scripts(1) for each game with offset seed via rng state
        scripts_by_game[g] = sample_scripts(1, rng)[0]
        scripts_by_game[g]["script_id"] = sim_id

    means = np.zeros(len(players), dtype=np.float64)
    tags = []
    for i, p in enumerate(players):
        sc = scripts_by_game[game_keys[i]]
        means[i] = _script_mean_for_player(p, sc)
        tags.append(sc["tag"])

    # Correlated residuals (team + pass latents)
    unique_teams = np.unique(teams_idx)
    team_shock = {int(t): float(rng.normal(0, 1.0)) for t in unique_teams}
    pass_shock = {int(t): float(rng.normal(0, 1.0)) for t in unique_teams}

    out = np.zeros(len(players), dtype=np.float64)
    for i in range(len(players)):
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

    # Dominant tag by frequency across players' games
    from collections import Counter

    tag = Counter(tags).most_common(1)[0][0] if tags else "mixed"
    return out, tag
