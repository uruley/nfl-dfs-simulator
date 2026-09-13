from pathlib import Path

import numpy as np

from nfl_dfs.classic_optimize import build_lineups_diverse
from nfl_dfs.classic_portfolio import build_classic_portfolio
from nfl_dfs.classic_rules import (
    SALARY_CAP,
    UPLOAD_HEADER,
    load_pool_csv,
    load_projections_csv,
    merge_pool_proj,
    validate_lineup,
    write_upload_csv,
)
from nfl_dfs.classic_sim import prepare_classic_arrays, sample_classic_outcomes
from nfl_dfs.cli import main

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "classic_pool.csv"
PROJ = ROOT / "fixtures" / "classic_projections.csv"


def _players():
    return merge_pool_proj(load_pool_csv(POOL), load_projections_csv(PROJ))


def test_classic_optimize_legal():
    players = _players()
    fp = np.array([p.proj_fp for p in players], dtype=np.float64)
    lus = build_lineups_diverse(players, fp, script_id=0, tag="close", top_n=3)
    assert lus
    for lu in lus:
        assert validate_lineup(lu) == []
        assert lu.salary <= SALARY_CAP
        assert len(lu.player_ids()) == len(set(lu.player_ids()))
        assert len(lu.game_keys()) >= 2


def test_classic_exposure_cap():
    players = _players()
    rng = np.random.default_rng(1)
    _t, teams_idx, pos_codes, stds, game_keys = prepare_classic_arrays(players)
    cands = []
    for s in range(60):
        fp, tag = sample_classic_outcomes(
            players, teams_idx, pos_codes, stds, game_keys, rng, sim_id=s
        )
        cands.extend(build_lineups_diverse(players, fp, script_id=s, tag=tag, top_n=3))
    port = build_classic_portfolio(cands, size=15, max_exposure=0.40, player_pool=players)
    assert len(port.lineups) >= 10
    for pid, exp in port.exposures.items():
        assert exp <= 0.50 + 1e-9, f"{pid} exposure {exp}"


def test_cli_sim_classic(tmp_path: Path):
    out = tmp_path / "exports"
    rc = main(
        [
            "sim-classic",
            "--pool",
            str(POOL),
            "--projections",
            str(PROJ),
            "--n-sims",
            "40",
            "--portfolio",
            "8",
            "--exposure",
            "0.40",
            "--seed",
            "7",
            "--out",
            str(out),
            "--read",
            "Josh Allen=1.1",
        ]
    )
    assert rc == 0
    upload = out / "lineups-classic-upload.csv"
    assert upload.exists()
    lines = upload.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(UPLOAD_HEADER)
    assert len(lines) >= 5  # header + some lineups
    assert (out / "sim-classic-summary.txt").exists()
