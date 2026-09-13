from pathlib import Path

import numpy as np

from nfl_dfs.optimize import build_best_lineup, build_lineups_by_cpt
from nfl_dfs.portfolio import build_portfolio
from nfl_dfs.showdown_rules import (
    CPT_MULT,
    SALARY_CAP,
    load_pool_csv,
    load_projections_csv,
    merge_pool_proj,
    validate_lineup,
)

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"


def _players():
    return merge_pool_proj(load_pool_csv(POOL), load_projections_csv(PROJ))


def test_optimize_respects_cpt_salary_and_uniqueness():
    players = _players()
    fp = np.array([p.proj_fp for p in players], dtype=np.float64)
    lu = build_best_lineup(players, fp, script_id=0, tag="close")
    assert lu is not None
    assert validate_lineup(lu) == []
    assert lu.salary <= SALARY_CAP
    ids = lu.player_ids()
    assert len(ids) == len(set(ids))
    assert lu.cpt.cpt_salary == int(round(lu.cpt.salary * CPT_MULT))
    cpt_i = next(i for i, p in enumerate(players) if p.dk_id == lu.cpt.dk_id)
    flex_fp = sum(
        float(fp[next(i for i, p in enumerate(players) if p.dk_id == x.dk_id)])
        for x in lu.flex
    )
    expected = float(fp[cpt_i]) * CPT_MULT + flex_fp
    assert abs(lu.sim_fp - expected) < 1e-6


def test_exposure_cap():
    players = _players()
    rng = np.random.default_rng(0)
    cands = []
    for s in range(80):
        fp = np.array(
            [max(0.1, p.proj_fp + rng.normal(0, p.std * 0.5)) for p in players],
            dtype=np.float64,
        )
        cands.extend(
            build_lineups_by_cpt(players, fp, script_id=s, tag="close", top_n=4)
        )
    port = build_portfolio(
        cands, size=20, max_exposure=0.40, player_pool=players
    )
    assert len(port.lineups) == 20
    assert port.n_unique_lineups == 20
    # Default 40% with small relax (+0.10) → no player above 50%
    for pid, exp in port.exposures.items():
        assert exp <= 0.50 + 1e-9, f"{pid} exposure {exp}"
