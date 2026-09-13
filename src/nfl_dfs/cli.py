"""CLI: python -m nfl_dfs sim-showdown ..."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from nfl_dfs.backtest import (
    load_actual_fp,
    score_portfolio,
    write_calibration_notes,
)
from nfl_dfs.contest import ContestConfig, price_lineups
from nfl_dfs.optimize import build_lineups_by_cpt
from nfl_dfs.portfolio import build_portfolio
from nfl_dfs.scripts import prepare_arrays, sample_outcomes, sample_scripts, script_player_means
from nfl_dfs.showdown_rules import (
    SALARY_CAP,
    UPLOAD_HEADER,
    apply_reads,
    load_pool_csv,
    load_projections_csv,
    merge_pool_proj,
    write_upload_csv,
)


def _parse_reads(raw: list[str] | None) -> dict[str, float]:
    """Parse --read NAME=1.2 or ID=0.8 boost/fade specs."""
    out: dict[str, float] = {}
    if not raw:
        return out
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"Bad --read {item!r}; expected NAME=mult or ID=mult")
        k, v = item.rsplit("=", 1)
        out[k.strip()] = float(v)
    return out


def cmd_sim_showdown(args: argparse.Namespace) -> int:
    pool_path = Path(args.pool)
    proj_path = Path(args.projections)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    pool = load_pool_csv(pool_path) if pool_path.exists() else []
    proj = load_projections_csv(proj_path) if proj_path.exists() else {}
    if not pool and proj:
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

    reads = _parse_reads(args.read)
    apply_reads(players, reads)

    rng = np.random.default_rng(args.seed)
    teams, teams_idx, pos_codes, stds = prepare_arrays(players)
    scripts = sample_scripts(args.n_scripts, rng)

    candidates = []
    tag_counts: Counter = Counter()
    for script in scripts:
        tag_counts[script["tag"]] += 1
        means = script_player_means(players, script, teams)
        fp = sample_outcomes(means, stds, teams_idx, pos_codes, rng)
        # Top lineups across CPTs per script → more portfolio diversity
        alts = build_lineups_by_cpt(
            players, fp, script_id=script["script_id"], tag=script["tag"], top_n=4
        )
        candidates.extend(alts)

    if not candidates:
        print("ERROR: no legal lineups built across scripts", file=sys.stderr)
        return 1

    # frequency for contest pricing
    freq_counts: Counter = Counter(lu.key() for lu in candidates)
    n_built = len(candidates)
    sim_freq = {k: c / n_built for k, c in freq_counts.items()}

    contest = None
    if args.field_size or args.entry_fee:
        contest = ContestConfig(
            field_size=args.field_size or 1000,
            entry_fee=args.entry_fee or 5.0,
        )
    priced = price_lineups(candidates, players, contest=contest, sim_freq=sim_freq)

    # portfolio from candidates (distinct scripts); optionally bias by leverage
    # sort candidates: leverage then sim_fp for portfolio preference
    leverage_map = {p.lineup.key(): p.leverage for p in priced}
    ranked_for_port = sorted(
        candidates,
        key=lambda lu: (-leverage_map.get(lu.key(), 0.0), -lu.sim_fp),
    )
    port = build_portfolio(
        ranked_for_port,
        size=args.portfolio,
        max_exposure=args.exposure,
        player_pool=players,
    )

    if len(port.lineups) < args.portfolio:
        print(
            f"WARN: portfolio size {len(port.lineups)} < requested {args.portfolio}",
            file=sys.stderr,
        )

    upload_path = out_dir / "lineups-showdown-upload.csv"
    write_upload_csv(upload_path, port.lineups)

    # summary
    id_to_name = {p.dk_id: p.name for p in players}
    summary_path = out_dir / "sim-showdown-summary.txt"
    lines = [
        "NFL DFS — Showdown sim v1",
        f"players={len(players)}  n_scripts={args.n_scripts}  built={n_built}  seed={args.seed}",
        f"teams={','.join(teams)}  salary_cap={SALARY_CAP}  exposure_cap={args.exposure}",
        f"portfolio={len(port.lineups)}  unique_scripts={port.n_unique_scripts}  "
        f"unique_lineups={port.n_unique_lineups}",
        f"script_tags_sampled={dict(tag_counts)}",
        f"script_tags_portfolio={port.script_tags}",
        f"reads={reads or '{}'}",
        "",
        "Upload header: " + ",".join(UPLOAD_HEADER),
        f"Upload CSV: {upload_path}",
        "",
        "Player exposures (portfolio):",
    ]
    for pid, exp in sorted(port.exposures.items(), key=lambda x: -x[1]):
        lines.append(f"  {exp:6.1%}  {id_to_name.get(pid, pid)} ({pid})")
    lines.append("")
    lines.append("Top priced leverage lineups (among candidates):")
    for i, pr in enumerate(priced[:8]):
        lu = pr.lineup
        lines.append(
            f"  #{i+1} lev={pr.leverage:+.3f} chalk={pr.chalk_own:.2f} "
            f"sim_fp={lu.sim_fp:.1f} sal={lu.salary} tag={lu.tag} "
            f"CPT={lu.cpt.name} [{pr.notes}]"
        )
    if contest:
        lines.append("")
        lines.append(
            f"Contest pricing: field={contest.field_size} fee=${contest.entry_fee:.2f}"
        )
    lines.append("")
    lines.append("Never mutate delivered CSVs in uploads/; version upgrades with -v2.")
    summary_text = "\n".join(lines) + "\n"
    summary_path.write_text(summary_text, encoding="utf-8")

    # sidecar: exposures JSON + candidates sample
    exp_path = out_dir / "sim-showdown-exposures.csv"
    with exp_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["dk_id", "name", "exposure"])
        w.writeheader()
        for pid, exp in sorted(port.exposures.items(), key=lambda x: -x[1]):
            w.writerow(
                {"dk_id": pid, "name": id_to_name.get(pid, pid), "exposure": round(exp, 4)}
            )

    meta = {
        "n_scripts": args.n_scripts,
        "n_built": n_built,
        "portfolio": len(port.lineups),
        "seed": args.seed,
        "exposure_cap": args.exposure,
        "script_tags_portfolio": port.script_tags,
        "upload": str(upload_path),
    }
    (out_dir / "sim-showdown-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # optional backtest
    if args.actuals:
        actual = load_actual_fp(Path(args.actuals))
        bt = score_portfolio(port.lineups, actual)
        notes = write_calibration_notes(
            Path(args.backtest_out)
            if args.backtest_out
            else out_dir / "backtest-calibration.md",
            bt,
            exposures=port.exposures,
            actual=actual,
        )
        lines_bt = [
            "",
            f"Backtest mean_fp={bt.mean_fp:.2f} max={bt.max_fp:.2f} min={bt.min_fp:.2f}",
            f"Calibration notes: {notes}",
        ]
        summary_path.write_text(summary_text + "\n".join(lines_bt) + "\n", encoding="utf-8")
        print("\n".join(lines_bt))

    print(summary_text)
    print(f"Wrote {upload_path} ({len(port.lineups)} rows)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="nfl_dfs", description="NFL DFS Showdown simulator")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sim-showdown", help="Run Showdown game-script sim + portfolio")
    p.add_argument("--pool", required=True, help="Showdown player pool CSV")
    p.add_argument("--projections", required=True, help="Projections CSV")
    p.add_argument("--n-scripts", type=int, default=200)
    p.add_argument("--portfolio", type=int, default=20)
    p.add_argument("--exposure", type=float, default=0.40)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", required=True, help="Output directory")
    p.add_argument(
        "--read",
        action="append",
        default=[],
        help="Boost/fade read NAME=1.2 or dk_id=0.8 (repeatable)",
    )
    p.add_argument("--field-size", type=int, default=0, help="Optional contest field size")
    p.add_argument("--entry-fee", type=float, default=0.0, help="Optional contest entry fee")
    p.add_argument("--actuals", default="", help="Optional actual FP CSV for backtest")
    p.add_argument(
        "--backtest-out",
        default="",
        help="Calibration notes path (default: OUT/backtest-calibration.md)",
    )
    p.set_defaults(func=cmd_sim_showdown)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
