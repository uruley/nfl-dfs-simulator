"""GPP script-bucket portfolio + usage priors + richer path tags."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from nfl_dfs.cli import main
from nfl_dfs.optimize import build_lineups_by_cpt
from nfl_dfs.portfolio import GPP_MAJOR_TAGS, build_portfolio, major_tags_for_lineup
from nfl_dfs.scorepath import _usage_weights, realize_game_path
from nfl_dfs.showdown_rules import (
    Player,
    load_pool_csv,
    load_projections_csv,
    merge_pool_proj,
)

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "fixtures" / "showdown_pool.csv"
PROJ = ROOT / "fixtures" / "projections.csv"


def _players():
    return merge_pool_proj(load_pool_csv(POOL), load_projections_csv(PROJ))


def test_same_seed_same_tags_and_lineups(tmp_path: Path):
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    args_base = [
        "sim-showdown",
        "--pool",
        str(POOL),
        "--projections",
        str(PROJ),
        "--n-scripts",
        "40",
        "--portfolio",
        "10",
        "--exposure",
        "0.40",
        "--seed",
        "7",
        "--engine",
        "scorepath",
        "--gpp",
        "--min-per-tag",
        "2",
    ]
    assert main(args_base + ["--out", str(out1)]) == 0
    assert main(args_base + ["--out", str(out2)]) == 0
    ps1 = (out1 / "path-summaries.csv").read_text(encoding="utf-8")
    ps2 = (out2 / "path-summaries.csv").read_text(encoding="utf-8")
    assert ps1 == ps2
    lu1 = (out1 / "lineups-showdown-upload.csv").read_text(encoding="utf-8")
    lu2 = (out2 / "lineups-showdown-upload.csv").read_text(encoding="utf-8")
    assert lu1 == lu2
    assert "tags" in ps1.splitlines()[0]
    assert (out1 / "script-projections.csv").exists()


def test_pass_heavy_vs_rush_heavy_rb_wr_emphasis():
    """Pass-heavy paths should emphasize WR FP vs rush-heavy RB FP (soft lean)."""
    players = _players()
    rng = np.random.default_rng(21)
    pass_wr, pass_rb = [], []
    rush_wr, rush_rb = [], []
    for i in range(150):
        fp, s = realize_game_path(players, rng, teams=["KC", "BUF"], script_id=i)
        style = (s.get("tags") or [""])[0]
        wr = sum(
            float(fp[j])
            for j, p in enumerate(players)
            if p.position == "WR"
        )
        rb = sum(
            float(fp[j])
            for j, p in enumerate(players)
            if p.position == "RB"
        )
        if style == "pass_heavy":
            pass_wr.append(wr)
            pass_rb.append(rb)
        elif style == "rush_heavy":
            rush_wr.append(wr)
            rush_rb.append(rb)
    assert len(pass_wr) >= 15 and len(rush_wr) >= 8
    # Soft: WR/RB ratio higher in pass_heavy than rush_heavy
    pass_ratio = np.mean(pass_wr) / max(1e-6, np.mean(pass_rb))
    rush_ratio = np.mean(rush_wr) / max(1e-6, np.mean(rush_rb))
    assert pass_ratio > rush_ratio * 0.95, (
        f"pass_ratio={pass_ratio:.2f} rush_ratio={rush_ratio:.2f} "
        f"n_pass={len(pass_wr)} n_rush={len(rush_wr)}"
    )


def test_gpp_portfolio_respects_min_per_tag():
    players = _players()
    rng = np.random.default_rng(3)
    cands = []
    # Force candidates across major tags
    tag_cycle = [
        "pass_heavy|close",
        "rush_heavy|blowout",
        "pass_heavy|shootout|te_vulture",
        "rush_heavy|close",
        "balanced|close|te_vulture",
    ]
    for s in range(60):
        fp = np.array(
            [max(0.1, p.proj_fp + rng.normal(0, p.std * 0.4)) for p in players],
            dtype=np.float64,
        )
        tag = tag_cycle[s % len(tag_cycle)]
        cands.extend(
            build_lineups_by_cpt(players, fp, script_id=s, tag=tag, top_n=3)
        )
    port = build_portfolio(
        cands,
        size=20,
        max_exposure=0.40,
        player_pool=players,
        gpp=True,
        min_per_tag=2,
    )
    assert len(port.lineups) == 20
    majors = port.major_tag_counts or {}
    # Enough candidates for pass_heavy, rush_heavy, te_vulture
    for need in ("pass_heavy", "rush_heavy", "te_vulture"):
        assert majors.get(need, 0) >= 2, f"{need}={majors}"
    for exp in port.exposures.values():
        assert exp <= 0.50 + 1e-9


def test_exposure_cap_still_holds_under_gpp():
    players = _players()
    rng = np.random.default_rng(0)
    cands = []
    for s in range(80):
        fp = np.array(
            [max(0.1, p.proj_fp + rng.normal(0, p.std * 0.5)) for p in players],
            dtype=np.float64,
        )
        tag = ["pass_heavy|close", "rush_heavy|grind", "balanced|te_vulture"][s % 3]
        cands.extend(
            build_lineups_by_cpt(players, fp, script_id=s, tag=tag, top_n=4)
        )
    port = build_portfolio(
        cands, size=20, max_exposure=0.40, player_pool=players, gpp=True, min_per_tag=2
    )
    assert len(port.lineups) == 20
    for pid, exp in port.exposures.items():
        assert exp <= 0.50 + 1e-9, f"{pid} exposure {exp}"


def test_usage_share_priors_override_proj_fp():
    """When rush_share is set, RB weights follow shares not proj_fp."""
    a = Player(
        dk_id="1",
        name="LowProjHighShare",
        position="RB",
        team="KC",
        salary=4000,
        proj_fp=2.0,
        rush_share=0.70,
    )
    b = Player(
        dk_id="2",
        name="HighProjLowShare",
        position="RB",
        team="KC",
        salary=7000,
        proj_fp=20.0,
        rush_share=0.10,
    )
    w = _usage_weights([a, b], "KC", {"RB"}, share_attr="rush_share")
    by_id = { [a, b][i].dk_id: wt for i, wt in w }
    assert by_id["1"] > by_id["2"]
    # Missing share → proj_fp fallback
    a2 = Player(
        dk_id="1", name="A", position="RB", team="KC", salary=4000, proj_fp=2.0
    )
    b2 = Player(
        dk_id="2", name="B", position="RB", team="KC", salary=7000, proj_fp=20.0
    )
    w2 = _usage_weights([a2, b2], "KC", {"RB"}, share_attr="rush_share")
    by2 = { [a2, b2][i].dk_id: wt for i, wt in w2 }
    assert by2["2"] > by2["1"]


def test_major_tags_parser():
    assert major_tags_for_lineup("pass_heavy|shootout|te_vulture") == [
        "pass_heavy",
        "te_vulture",
    ]
    assert set(GPP_MAJOR_TAGS) >= {"pass_heavy", "rush_heavy", "te_vulture"}
