"""Ownership-aware portfolio prefers diversity vs field chalk."""

from pathlib import Path

import numpy as np

from nfl_dfs.contest import build_own_map
from nfl_dfs.optimize import build_lineups_by_cpt
from nfl_dfs.portfolio import build_portfolio, _field_chalk_penalty
from nfl_dfs.showdown_rules import Lineup, Player, load_pool_csv, load_projections_csv, merge_pool_proj

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"


def _players():
    return merge_pool_proj(load_pool_csv(POOL), load_projections_csv(PROJ))


def _make_lineup(players, cpt_id, flex_ids, sim_fp=100.0):
    by = {p.dk_id: p for p in players}
    return Lineup(
        cpt=by[cpt_id],
        flex=[by[i] for i in flex_ids],
        script_id=0,
        sim_fp=sim_fp,
        salary=0,
        tag="test",
    )


def test_field_chalk_penalty_higher_for_chalk_stack():
    players = _players()
    # Extreme chalk map
    own = {p.dk_id: 0.05 for p in players}
    chalk_ids = ["900001", "900002", "900003", "900004", "900005", "900006"]
    for i in chalk_ids:
        own[i] = 0.40
    chalk_lu = _make_lineup(
        players, "900001", ["900002", "900003", "900004", "900005", "900006"], sim_fp=110
    )
    contrarian_lu = _make_lineup(
        players, "900011", ["900012", "900017", "900018", "900021", "900019"], sim_fp=110
    )
    assert _field_chalk_penalty(chalk_lu, own) > _field_chalk_penalty(contrarian_lu, own) + 5


def test_portfolio_prefers_lower_chalk_when_scores_close():
    players = _players()
    # Make chalk ownership extreme on stars; low on cheap
    own = {p.dk_id: 0.03 for p in players}
    for pid in ("900001", "900002", "900003", "900004", "900005", "900006"):
        own[pid] = 0.45

    rng = np.random.default_rng(11)
    cands = []
    for s in range(60):
        fp = np.array(
            [max(0.1, p.proj_fp + rng.normal(0, p.std * 0.4)) for p in players],
            dtype=np.float64,
        )
        cands.extend(build_lineups_by_cpt(players, fp, script_id=s, tag="close", top_n=4))

    port_aware = build_portfolio(
        cands, size=12, max_exposure=0.40, player_pool=players, own_map=own
    )
    port_blind = build_portfolio(
        cands, size=12, max_exposure=0.40, player_pool=players, own_map=None
    )
    assert len(port_aware.lineups) == 12
    assert len(port_blind.lineups) == 12

    def avg_chalk(port):
        vals = []
        for lu in port.lineups:
            vals.append(sum(own.get(pid, 0.15) for pid in lu.player_ids()) / 6.0)
        return sum(vals) / len(vals)

    # Ownership-aware should have lower or equal avg field chalk
    assert avg_chalk(port_aware) <= avg_chalk(port_blind) + 0.02

    # Exposure still capped (~40% with soft relax to 50%)
    for exp in port_aware.exposures.values():
        assert exp <= 0.50 + 1e-9
