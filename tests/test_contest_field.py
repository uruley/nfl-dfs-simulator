"""Field simulation contest pricing."""

from pathlib import Path

import numpy as np

from nfl_dfs.contest import (
    ContestConfig,
    build_own_map,
    field_sim_price,
    load_ownership_csv,
    sample_field_lineups,
)
from nfl_dfs.optimize import build_lineups_by_cpt
from nfl_dfs.showdown_rules import (
    load_pool_csv,
    load_projections_csv,
    merge_pool_proj,
    validate_lineup,
)

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"
OWN = ROOT / "fixtures" / "ownership_sample.csv"


def _players():
    return merge_pool_proj(load_pool_csv(POOL), load_projections_csv(PROJ))


def test_load_ownership_csv():
    own = load_ownership_csv(OWN)
    assert "900002" in own
    assert 0.3 < own["900002"] < 0.35  # 32%


def test_sample_field_lineups_legal():
    players = _players()
    own = build_own_map(players, load_ownership_csv(OWN))
    rng = np.random.default_rng(0)
    field = sample_field_lineups(players, own, n=25, rng=rng)
    assert len(field) >= 20
    for lu in field:
        assert validate_lineup(lu) == []
        assert lu.tag == "field"


def test_field_sim_price_metrics_tiny_n():
    players = _players()
    own = build_own_map(players)
    rng = np.random.default_rng(3)
    fp = np.array([p.proj_fp for p in players], dtype=np.float64)
    cands = build_lineups_by_cpt(players, fp, script_id=0, tag="close", top_n=6)
    contest = ContestConfig(field_size=100, entry_fee=3.0)
    priced = field_sim_price(
        cands,
        players,
        contest=contest,
        own_map=own,
        n_field=30,
        rng=rng,
    )
    assert priced
    for pr in priced:
        assert 0.0 <= pr.win_rate <= 1.0
        assert 0.0 <= pr.est_cash_rate <= 1.0
        assert hasattr(pr, "est_EV")
        assert hasattr(pr, "leverage")
        assert hasattr(pr, "top1_rate")
