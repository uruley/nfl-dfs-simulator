"""Score-path engine unit + integration tests."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from nfl_dfs.cli import main
from nfl_dfs.optimize import build_best_lineup, build_lineups_by_cpt
from nfl_dfs.scorepath import realize_classic_slate, realize_game_path
from nfl_dfs.showdown_rules import (
    load_pool_csv,
    load_projections_csv,
    merge_pool_proj,
)

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"
CPOOL = ROOT / "fixtures" / "classic_pool.csv"
CPROJ = ROOT / "fixtures" / "classic_projections.csv"


def _showdown_players():
    return merge_pool_proj(load_pool_csv(POOL), load_projections_csv(PROJ))


def test_deterministic_seed_identical_fp_totals():
    players = _showdown_players()
    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    fp1, s1 = realize_game_path(players, rng1, teams=["KC", "BUF"], script_id=0)
    fp2, s2 = realize_game_path(players, rng2, teams=["KC", "BUF"], script_id=0)
    assert np.allclose(fp1, fp2)
    assert abs(float(fp1.sum()) - float(fp2.sum())) < 1e-9
    assert s1["tag"] == s2["tag"]
    assert s1["possessions"] == s2["possessions"]


def test_realized_fps_prefer_high_usage():
    """High-proj players on a team should outscore low-proj teammates on average."""
    players = _showdown_players()
    kc = [p for p in players if p.team == "KC" and p.position in ("WR", "TE", "RB", "QB")]
    # Rank by proj_fp
    kc_sorted = sorted(kc, key=lambda p: -p.proj_fp)
    top_ids = {p.dk_id for p in kc_sorted[:3]}
    bot_ids = {p.dk_id for p in kc_sorted[-3:]}

    rng = np.random.default_rng(7)
    top_sum = 0.0
    bot_sum = 0.0
    n = 80
    for i in range(n):
        fp, _ = realize_game_path(players, rng, teams=["KC", "BUF"], script_id=i)
        by_id = {players[j].dk_id: float(fp[j]) for j in range(len(players))}
        top_sum += sum(by_id[i] for i in top_ids)
        bot_sum += sum(by_id[i] for i in bot_ids)
    assert top_sum > bot_sum * 1.3, f"top={top_sum:.1f} bot={bot_sum:.1f}"


def test_blowout_shifts_rb_vs_trailing_passcatchers():
    """Soft statistical lean: winning-team RBs vs trailing WR/TE over many paths.

    Force blowout-ish paths by using a large home favorite spread and comparing
    home RB total FP share vs away WR/TE when final_margin is large positive.
    """
    players = _showdown_players()
    rng = np.random.default_rng(11)
    home_rb = []
    away_pass = []
    n_blow = 0
    for i in range(120):
        fp, s = realize_game_path(
            players, rng, teams=["KC", "BUF"], spread=-10.0, total=44.0, script_id=i
        )
        if abs(float(s["final_margin"])) < 10:
            continue
        n_blow += 1
        # If KC (home) winning large
        if float(s["final_margin"]) > 10:
            rb_fp = sum(
                float(fp[j])
                for j, p in enumerate(players)
                if p.team == "KC" and p.position == "RB"
            )
            wr_fp = sum(
                float(fp[j])
                for j, p in enumerate(players)
                if p.team == "BUF" and p.position in ("WR", "TE")
            )
            home_rb.append(rb_fp)
            away_pass.append(wr_fp)
    assert n_blow >= 20, f"too few blowout paths: {n_blow}"
    # Soft lean: mean away pass-catchers should be competitive (they're trailing → pass)
    # and home RBs should get meaningful production (winning → run).
    assert np.mean(home_rb) > 5.0
    assert np.mean(away_pass) > 8.0
    # Directional: in blowouts with KC winning, KC RB mean vs BUF RB mean lean
    rng2 = np.random.default_rng(11)
    kc_rb_all, buf_rb_all = [], []
    for i in range(100):
        fp, s = realize_game_path(
            players, rng2, teams=["KC", "BUF"], spread=-14.0, total=42.0, script_id=i
        )
        if float(s["final_margin"]) < 12:
            continue
        kc_rb_all.append(
            sum(float(fp[j]) for j, p in enumerate(players) if p.team == "KC" and p.position == "RB")
        )
        buf_rb_all.append(
            sum(float(fp[j]) for j, p in enumerate(players) if p.team == "BUF" and p.position == "RB")
        )
    if kc_rb_all and buf_rb_all:
        # Soft: winning RBs tend to outscore losing RBs
        assert np.mean(kc_rb_all) >= np.mean(buf_rb_all) * 0.85


def test_optimizer_called_with_path_fps(monkeypatch):
    """Best-lineup-per-path must receive the realized scorepath FP array."""
    players = _showdown_players()
    seen = []

    real_build = build_lineups_by_cpt

    def spy(players_arg, fp, script_id=-1, tag="", salary_cap=50000, top_n=6):
        seen.append(np.array(fp, copy=True))
        return real_build(players_arg, fp, script_id=script_id, tag=tag, top_n=top_n)

    import nfl_dfs.cli as cli_mod

    monkeypatch.setattr(cli_mod, "build_lineups_by_cpt", spy)
    # Also spy build_best_lineup
    real_best = build_best_lineup

    def spy_best(players_arg, fp, script_id=-1, tag="", salary_cap=50000):
        seen.append(np.array(fp, copy=True))
        return real_best(players_arg, fp, script_id=script_id, tag=tag)

    monkeypatch.setattr(cli_mod, "build_best_lineup", spy_best)

    out = Path("/tmp/scorepath-spy-test")
    out.mkdir(parents=True, exist_ok=True)
    rc = main(
        [
            "sim-showdown",
            "--pool",
            str(POOL),
            "--projections",
            str(PROJ),
            "--n-scripts",
            "5",
            "--portfolio",
            "3",
            "--seed",
            "3",
            "--engine",
            "scorepath",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert len(seen) >= 5
    # FPs should not all be identical to mean projections (path noise)
    proj = np.array([p.proj_fp for p in players])
    assert any(not np.allclose(fp, proj) for fp in seen)


def test_legacy_engine_still_works(tmp_path: Path):
    out = tmp_path / "legacy"
    rc = main(
        [
            "sim-showdown",
            "--pool",
            str(POOL),
            "--projections",
            str(PROJ),
            "--n-scripts",
            "20",
            "--portfolio",
            "5",
            "--seed",
            "7",
            "--engine",
            "legacy",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    meta = (out / "sim-showdown-meta.json").read_text(encoding="utf-8")
    assert '"engine": "legacy"' in meta
    assert (out / "lineups-showdown-upload.csv").exists()
    assert (out / "path-summaries.csv").exists()


def test_scorepath_cli_emits_path_summaries(tmp_path: Path):
    out = tmp_path / "sp"
    rc = main(
        [
            "sim-showdown",
            "--pool",
            str(POOL),
            "--projections",
            str(PROJ),
            "--n-scripts",
            "30",
            "--portfolio",
            "5",
            "--seed",
            "7",
            "--engine",
            "scorepath",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    ps = out / "path-summaries.csv"
    assert ps.exists()
    text = ps.read_text(encoding="utf-8")
    assert "script_id" in text
    assert "possessions" in text
    meta = (out / "sim-showdown-meta.json").read_text(encoding="utf-8")
    assert '"engine": "scorepath"' in meta


def test_classic_scorepath_cli(tmp_path: Path):
    out = tmp_path / "classic-sp"
    rc = main(
        [
            "sim-classic",
            "--pool",
            str(CPOOL),
            "--projections",
            str(CPROJ),
            "--n-sims",
            "15",
            "--portfolio",
            "4",
            "--seed",
            "5",
            "--engine",
            "scorepath",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert (out / "path-summaries.csv").exists()
    assert (out / "lineups-classic-upload.csv").exists()


def test_classic_legacy_engine(tmp_path: Path):
    out = tmp_path / "classic-leg"
    rc = main(
        [
            "sim-classic",
            "--pool",
            str(CPOOL),
            "--projections",
            str(CPROJ),
            "--n-sims",
            "15",
            "--portfolio",
            "4",
            "--seed",
            "5",
            "--engine",
            "legacy",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    meta = (out / "sim-classic-meta.json").read_text(encoding="utf-8")
    assert '"engine": "legacy"' in meta


def test_realize_classic_slate_merges_games():
    from nfl_dfs.classic_rules import load_pool_csv as clp
    from nfl_dfs.classic_rules import load_projections_csv as clj
    from nfl_dfs.classic_rules import merge_pool_proj as clm

    players = clm(clp(CPOOL), clj(CPROJ))
    rng = np.random.default_rng(1)
    fp, summary = realize_classic_slate(players, rng, sim_id=0)
    assert len(fp) == len(players)
    assert summary["n_games"] >= 2
    assert float(fp.sum()) > 0
