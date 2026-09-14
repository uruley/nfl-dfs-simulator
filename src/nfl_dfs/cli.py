"""CLI: python -m nfl_dfs <command> ..."""

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
from nfl_dfs.contest import (
    ContestConfig,
    build_own_map,
    field_sim_price,
    load_contest_meta,
    load_ownership_csv,
    price_lineups,
)
from nfl_dfs.optimize import build_best_lineup, build_lineups_by_cpt
from nfl_dfs.portfolio import build_portfolio
from nfl_dfs.scorepath import realize_game_path, write_path_summaries_csv
from nfl_dfs.scripts import prepare_arrays, sample_outcomes, sample_scripts, script_player_means
from nfl_dfs.showdown_rules import (
    SALARY_CAP,
    UPLOAD_HEADER,
    UPLOAD_HEADER_ENTRY_ID,
    apply_reads,
    load_entry_ids,
    load_pool_csv,
    load_projections_csv,
    make_entry_ids,
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


def _resolve_entry_ids(args: argparse.Namespace, n: int) -> list[str] | None:
    """Return entry IDs for upload, or None for bare format."""
    if getattr(args, "entry_ids", None):
        ids = load_entry_ids(Path(args.entry_ids))
        if len(ids) < n:
            # Pad with sequential after last numeric if possible
            start = 1
            if ids:
                try:
                    start = int(ids[-1]) + 1
                except ValueError:
                    start = len(ids) + 1
            ids = ids + make_entry_ids(n - len(ids), start)
        return ids[:n]
    if getattr(args, "entry_id_start", None) not in (None, ""):
        return make_entry_ids(n, args.entry_id_start)
    return None


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

    # Vegas / contest meta
    contest: ContestConfig | None = None
    spread = args.spread
    total = args.total
    if args.contest_meta:
        contest = load_contest_meta(Path(args.contest_meta))
        if spread is None:
            spread = contest.spread
        if total is None:
            total = contest.total
    if args.field_size or args.entry_fee:
        contest = contest or ContestConfig()
        if args.field_size:
            contest.field_size = args.field_size
            # rebuild default prizes for new field if user didn't supply prizes
            if not args.contest_meta:
                contest.prizes = []
                contest.__post_init__()
        if args.entry_fee:
            contest.entry_fee = args.entry_fee
            if not args.contest_meta:
                contest.prizes = []
                contest.__post_init__()
    if contest is None and (args.field_sims or args.field_size or args.entry_fee):
        contest = ContestConfig(
            field_size=args.field_size or 1000,
            entry_fee=args.entry_fee or 5.0,
        )
    if contest is not None:
        contest.spread = spread
        contest.total = total

    # Ownership
    ownership_extra: dict[str, float] = {}
    if args.ownership:
        ownership_extra = load_ownership_csv(Path(args.ownership))
    own_map = build_own_map(players, ownership_extra or None)

    rng = np.random.default_rng(args.seed)
    teams, teams_idx, pos_codes, stds = prepare_arrays(players)
    engine = (getattr(args, "engine", None) or "scorepath").lower()
    progress_every = int(getattr(args, "progress_every", 0) or 0)

    candidates = []
    tag_counts: Counter = Counter()
    path_summaries: list[dict] = []

    if engine == "legacy":
        scripts = sample_scripts(args.n_scripts, rng, spread=spread, total=total)
        for script in scripts:
            tag_counts[script["tag"]] += 1
            means = script_player_means(players, script, teams)
            fp = sample_outcomes(means, stds, teams_idx, pos_codes, rng)
            alts = build_lineups_by_cpt(
                players, fp, script_id=script["script_id"], tag=script["tag"], top_n=4
            )
            candidates.extend(alts)
            path_summaries.append(
                {
                    "script_id": script["script_id"],
                    "tag": script["tag"],
                    "possessions": "",
                    "final_margin": round(float(script["margin"]), 2),
                    "pass_rate": round(float(script["pass_tilt"]), 3),
                    "home": teams[0] if teams else "",
                    "away": teams[1] if len(teams) > 1 else "",
                    "top_scorers": [],
                }
            )
            if progress_every and (script["script_id"] + 1) % progress_every == 0:
                print(
                    f"legacy progress {script['script_id']+1}/{args.n_scripts}",
                    file=sys.stderr,
                )
    else:
        # scorepath (default): realize FPs via possession model, then best lineup(s) for THAT path
        for i in range(args.n_scripts):
            fp, summary = realize_game_path(
                players,
                rng,
                teams=teams,
                spread=spread,
                total=total,
                script_id=i,
            )
            tag_counts[summary["tag"]] += 1
            path_summaries.append(summary)
            # Best legal CPT+5FLEX for this realized path (plus CPT diversity alts)
            best = build_best_lineup(
                players, fp, script_id=i, tag=summary["tag"]
            )
            alts = build_lineups_by_cpt(
                players, fp, script_id=i, tag=summary["tag"], top_n=4
            )
            if best is not None:
                # Ensure the true best is present
                keys = {lu.key() for lu in alts}
                if best.key() not in keys:
                    alts = [best] + alts
            candidates.extend(alts)
            if progress_every and (i + 1) % progress_every == 0:
                print(
                    f"scorepath progress {i+1}/{args.n_scripts}",
                    file=sys.stderr,
                )

    if not candidates:
        print("ERROR: no legal lineups built across scripts", file=sys.stderr)
        return 1

    freq_counts: Counter = Counter(lu.key() for lu in candidates)
    n_built = len(candidates)
    sim_freq = {k: c / n_built for k, c in freq_counts.items()}

    field_sims = int(args.field_sims or 0)
    if field_sims > 0:
        contest = contest or ContestConfig()
        priced = field_sim_price(
            candidates,
            players,
            contest=contest,
            own_map=own_map,
            n_field=field_sims,
            rng=rng,
            sim_freq=sim_freq,
        )
    else:
        priced = price_lineups(
            candidates, players, contest=contest, sim_freq=sim_freq, own_map=own_map
        )

    # Rank for portfolio: prefer field-sim EV / leverage, then sim_fp
    score_map = {
        p.lineup.key(): (
            p.est_EV if field_sims > 0 else p.leverage,
            p.win_rate,
            p.leverage,
            p.lineup.sim_fp,
        )
        for p in priced
    }
    ranked_for_port = sorted(
        candidates,
        key=lambda lu: score_map.get(
            lu.key(), (0.0, 0.0, 0.0, lu.sim_fp)
        ),
        reverse=True,
    )
    port = build_portfolio(
        ranked_for_port,
        size=args.portfolio,
        max_exposure=args.exposure,
        player_pool=players,
        own_map=own_map,
    )

    if len(port.lineups) < args.portfolio:
        print(
            f"WARN: portfolio size {len(port.lineups)} < requested {args.portfolio}",
            file=sys.stderr,
        )

    entry_ids = _resolve_entry_ids(args, len(port.lineups))
    upload_path = out_dir / "lineups-showdown-upload.csv"
    write_upload_csv(upload_path, port.lineups, entry_ids=entry_ids)
    header_used = UPLOAD_HEADER_ENTRY_ID if entry_ids is not None else UPLOAD_HEADER

    id_to_name = {p.dk_id: p.name for p in players}
    summary_path = out_dir / "sim-showdown-summary.txt"
    write_path_summaries_csv(out_dir / "path-summaries.csv", path_summaries)
    lines = [
        "NFL DFS — Showdown sim v3 (scorepath / SaberSim-like)",
        f"players={len(players)}  n_scripts={args.n_scripts}  built={n_built}  seed={args.seed}",
        f"engine={engine}  teams={','.join(teams)}  salary_cap={SALARY_CAP}  exposure_cap={args.exposure}",
        f"vegas_spread={spread}  vegas_total={total}",
        f"field_sims={field_sims}  ownership_players={len(own_map)}",
        f"portfolio={len(port.lineups)}  unique_scripts={port.n_unique_scripts}  "
        f"unique_lineups={port.n_unique_lineups}",
        f"script_tags_sampled={dict(tag_counts)}",
        f"script_tags_portfolio={port.script_tags}",
        f"reads={reads or '{}'}",
        "",
        "Upload header: " + ",".join(header_used),
        f"Upload CSV: {upload_path}",
        "",
        "Player exposures (portfolio):",
    ]
    for pid, exp in sorted(port.exposures.items(), key=lambda x: -x[1]):
        lines.append(f"  {exp:6.1%}  {id_to_name.get(pid, pid)} ({pid})")
    lines.append("")
    lines.append("Top priced lineups (among candidates):")
    for i, pr in enumerate(priced[:8]):
        lu = pr.lineup
        lines.append(
            f"  #{i+1} EV={pr.est_EV:+.2f} win={pr.win_rate:.3f} cash={pr.est_cash_rate:.3f} "
            f"lev={pr.leverage:+.3f} chalk={pr.chalk_own:.2f} "
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

    exp_path = out_dir / "sim-showdown-exposures.csv"
    with exp_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["dk_id", "name", "exposure"])
        w.writeheader()
        for pid, exp in sorted(port.exposures.items(), key=lambda x: -x[1]):
            w.writerow(
                {"dk_id": pid, "name": id_to_name.get(pid, pid), "exposure": round(exp, 4)}
            )

    # Priced metrics CSV for Lab / Builder
    priced_path = out_dir / "sim-showdown-priced.csv"
    with priced_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "key",
                "cpt",
                "sim_fp",
                "salary",
                "win_rate",
                "top1_rate",
                "cash_rate",
                "leverage",
                "chalk_own",
                "est_EV",
                "notes",
            ],
        )
        w.writeheader()
        for pr in priced[:200]:
            w.writerow(
                {
                    "key": pr.lineup.key(),
                    "cpt": pr.lineup.cpt.name,
                    "sim_fp": round(pr.lineup.sim_fp, 3),
                    "salary": pr.lineup.salary,
                    "win_rate": pr.win_rate,
                    "top1_rate": pr.top1_rate,
                    "cash_rate": pr.est_cash_rate,
                    "leverage": pr.leverage,
                    "chalk_own": pr.chalk_own,
                    "est_EV": pr.est_EV,
                    "notes": pr.notes,
                }
            )

    meta = {
        "n_scripts": args.n_scripts,
        "n_built": n_built,
        "portfolio": len(port.lineups),
        "seed": args.seed,
        "engine": engine,
        "exposure_cap": args.exposure,
        "vegas_spread": spread,
        "vegas_total": total,
        "field_sims": field_sims,
        "script_tags_portfolio": port.script_tags,
        "upload": str(upload_path),
        "upload_format": "entry-id" if entry_ids is not None else "bare",
        "path_summaries": str(out_dir / "path-summaries.csv"),
    }
    (out_dir / "sim-showdown-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

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


def cmd_sim_classic(args: argparse.Namespace) -> int:
    from nfl_dfs import classic_rules as cr
    from nfl_dfs.classic_optimize import build_lineups_diverse
    from nfl_dfs.classic_portfolio import build_classic_portfolio
    from nfl_dfs.classic_sim import prepare_classic_arrays, sample_classic_outcomes

    pool_path = Path(args.pool)
    proj_path = Path(args.projections)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    pool = cr.load_pool_csv(pool_path) if pool_path.exists() else []
    proj = cr.load_projections_csv(proj_path) if proj_path.exists() else {}
    if not pool and proj:
        pool = [
            {
                "dk_id": v["dk_id"],
                "name": v["name"],
                "position": v["position"],
                "team": v["team"],
                "opp": v.get("opp", ""),
                "salary": v.get("salary") or 0,
                "game_key": v.get("game_key", ""),
                "game_info": v.get("game_info", ""),
            }
            for v in proj.values()
        ]
    players = cr.merge_pool_proj(pool, proj)
    if len(players) < 9:
        print(f"ERROR: need ≥9 projected players, got {len(players)}", file=sys.stderr)
        return 1

    reads = _parse_reads(args.read)
    cr.apply_reads(players, reads)

    from nfl_dfs.scorepath import realize_classic_slate

    rng = np.random.default_rng(args.seed)
    _teams, teams_idx, pos_codes, stds, game_keys = prepare_classic_arrays(players)
    n_sims = args.n_sims
    engine = (getattr(args, "engine", None) or "scorepath").lower()
    progress_every = int(getattr(args, "progress_every", 0) or 0)

    candidates = []
    tag_counts: Counter = Counter()
    path_summaries: list[dict] = []
    for i in range(n_sims):
        if engine == "legacy":
            fp, tag = sample_classic_outcomes(
                players, teams_idx, pos_codes, stds, game_keys, rng, sim_id=i
            )
            summary = {
                "script_id": i,
                "tag": tag,
                "possessions": "",
                "final_margin": "",
                "pass_rate": "",
                "top_scorers": [],
            }
        else:
            fp, summary = realize_classic_slate(players, rng, sim_id=i)
            tag = summary["tag"]
        tag_counts[tag] += 1
        path_summaries.append(summary)
        alts = build_lineups_diverse(
            players, fp, script_id=i, tag=tag, top_n=4
        )
        candidates.extend(alts)
        if progress_every and (i + 1) % progress_every == 0:
            print(f"{engine} classic progress {i+1}/{n_sims}", file=sys.stderr)

    if not candidates:
        print("ERROR: no legal Classic lineups built across sims", file=sys.stderr)
        return 1

    port = build_classic_portfolio(
        candidates,
        size=args.portfolio,
        max_exposure=args.exposure,
        player_pool=players,
    )

    if len(port.lineups) < args.portfolio:
        print(
            f"WARN: portfolio size {len(port.lineups)} < requested {args.portfolio}",
            file=sys.stderr,
        )

    upload_path = out_dir / "lineups-classic-upload.csv"
    cr.write_upload_csv(upload_path, port.lineups)

    id_to_name = {p.dk_id: p.name for p in players}
    n_games = cr.slate_game_count(players)
    summary_path = out_dir / "sim-classic-summary.txt"
    write_path_summaries_csv(out_dir / "path-summaries.csv", path_summaries)
    lines = [
        "NFL DFS — Classic sim v2 (scorepath)",
        f"players={len(players)}  n_sims={n_sims}  built={len(candidates)}  seed={args.seed}",
        f"engine={engine}  slate_games={n_games}  salary_cap={cr.SALARY_CAP}  exposure_cap={args.exposure}",
        f"portfolio={len(port.lineups)}  unique_scripts={port.n_unique_scripts}  "
        f"unique_lineups={port.n_unique_lineups}",
        f"script_tags_sampled={dict(tag_counts)}",
        f"script_tags_portfolio={port.script_tags}",
        f"reads={reads or '{}'}",
        "",
        f"Sim method: engine={engine} (scorepath=possession model; legacy=mean-tilt+residuals).",
        "Upload header: " + ",".join(cr.UPLOAD_HEADER),
        f"Upload CSV: {upload_path}",
        "",
        "Player exposures (portfolio):",
    ]
    for pid, exp in sorted(port.exposures.items(), key=lambda x: -x[1]):
        lines.append(f"  {exp:6.1%}  {id_to_name.get(pid, pid)} ({pid})")
    lines.append("")
    lines.append("Never mutate delivered CSVs in uploads/; version upgrades with -v2.")
    summary_text = "\n".join(lines) + "\n"
    summary_path.write_text(summary_text, encoding="utf-8")

    exp_path = out_dir / "sim-classic-exposures.csv"
    with exp_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["dk_id", "name", "exposure"])
        w.writeheader()
        for pid, exp in sorted(port.exposures.items(), key=lambda x: -x[1]):
            w.writerow(
                {"dk_id": pid, "name": id_to_name.get(pid, pid), "exposure": round(exp, 4)}
            )

    meta = {
        "n_sims": n_sims,
        "n_built": len(candidates),
        "portfolio": len(port.lineups),
        "seed": args.seed,
        "exposure_cap": args.exposure,
        "slate_games": n_games,
        "script_tags_portfolio": port.script_tags,
        "upload": str(upload_path),
        "engine": engine,
        "sim_method": "scorepath" if engine != "legacy" else "per-game script tilt + correlated residuals",
        "path_summaries": str(out_dir / "path-summaries.csv"),
    }
    (out_dir / "sim-classic-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(summary_text)
    print(f"Wrote {upload_path} ({len(port.lineups)} rows)")
    return 0


def cmd_ingest_dk_salary(args: argparse.Namespace) -> int:
    from nfl_dfs.ingest import ingest_dk_salary_csv, write_normalized_pool

    src = Path(args.input)
    out = Path(args.out)
    result = ingest_dk_salary_csv(src)
    if not result.rows:
        print("ERROR: no players parsed", file=sys.stderr)
        return 1
    write_normalized_pool(out, result)
    print(
        f"Ingested {len(result.rows)} players as {result.kind} → {out}"
        + (f"  warnings={result.warnings}" if result.warnings else "")
    )
    return 0


def cmd_flashback(args: argparse.Namespace) -> int:
    from nfl_dfs.flashback import run_flashback, write_flashback_artifacts

    lineups = Path(args.lineups)
    actuals = Path(args.actuals)
    out_dir = Path(args.out)
    contest = Path(args.contest) if args.contest else None
    payouts = Path(args.payouts) if getattr(args, "payouts", None) else None

    # Hard safety: never write into uploads/
    if "uploads" in out_dir.resolve().parts and out_dir.resolve().name == "uploads":
        print("ERROR: refusing to write under uploads/", file=sys.stderr)
        return 1

    result = run_flashback(lineups, actuals, contest_path=contest, payouts_path=payouts)
    paths = write_flashback_artifacts(result, out_dir)
    print(
        f"Flashback ({result.kind}): n={len(result.scores)} "
        f"mean={result.mean_fp:.2f} max={result.max_fp:.2f}"
    )
    for k, p in paths.items():
        print(f"  {k}: {p}")
    if result.gates:
        print(f"  gates: {len(result.gates)} actionable")
    return 0



def cmd_scratch_watch(args: argparse.Namespace) -> int:
    from nfl_dfs.scratch_watch import (
        DEFAULT_OUT,
        run_scratch_watch,
        write_scratch_artifacts,
    )

    lineups = Path(args.lineups)
    if not lineups.exists():
        print(f"ERROR: lineups not found: {lineups}", file=sys.stderr)
        return 2
    pool = Path(args.pool) if args.pool else None
    status = Path(args.status) if args.status else None
    out_dir = Path(args.out) if args.out else DEFAULT_OUT
    games = [g.strip() for g in (args.games or "").split(",") if g.strip()]

    # Hard safety: never write into uploads/
    if "uploads" in out_dir.resolve().parts and out_dir.resolve().name == "uploads":
        print("ERROR: refusing to write under uploads/", file=sys.stderr)
        return 1

    result = run_scratch_watch(
        lineups,
        pool_path=pool,
        status_path=status,
        fetch=bool(args.fetch),
        games=games or None,
    )
    paths = write_scratch_artifacts(result, out_dir)

    quiet = bool(args.quiet_ok) and result.all_clear
    if not quiet:
        print(
            f"scratch-watch: lineups={result.lineups} unique={result.unique_players} "
            f"all_clear={result.all_clear} action={result.action} "
            f"hits={len(result.flagged)} warns={len(result.soft_warns)}"
        )
        for h in result.flagged:
            print(
                f"  HIT {h['status']:8} {h['name']} ({h['dk_id']}) "
                f"{h['team']} in {h['lineups_affected']} lineups"
            )
        for w in result.soft_warns:
            print(
                f"  WARN {w['status']:8} {w['name']} ({w['dk_id']}) "
                f"{w['team']} in {w['lineups_affected']} lineups"
            )
        if result.all_clear:
            print("  all clear — no OUT/INACTIVE players in entered lineups")
        else:
            print(f"  suggest next upload: {result.next_upload_suggestion} (do not mutate current)")
        for k, p in paths.items():
            print(f"  {k}: {p}")
    else:
        # still write artifacts; minimal/no stdout
        pass

    return 0 if result.all_clear else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="nfl_dfs", description="NFL DFS simulator")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sim-showdown", help="Run Showdown game-script sim + portfolio")
    p.add_argument("--pool", required=True, help="Showdown player pool CSV")
    p.add_argument("--projections", required=True, help="Projections CSV")
    p.add_argument("--n-scripts", type=int, default=200)
    p.add_argument("--portfolio", type=int, default=20)
    p.add_argument("--exposure", type=float, default=0.40)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument(
        "--engine",
        choices=["scorepath", "legacy"],
        default="scorepath",
        help="scorepath=possession model (default); legacy=mean-tilt residuals",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=0,
        help="Print progress to stderr every N sims (0=off)",
    )
    p.add_argument("--out", required=True, help="Output directory")
    p.add_argument(
        "--read",
        action="append",
        default=[],
        help="Boost/fade read NAME=1.2 or dk_id=0.8 (repeatable)",
    )
    p.add_argument("--field-size", type=int, default=0, help="Optional contest field size")
    p.add_argument("--entry-fee", type=float, default=0.0, help="Optional contest entry fee")
    p.add_argument(
        "--field-sims",
        type=int,
        default=0,
        help="Ownership-weighted opponent lineups for contest pricing (0=crude leverage)",
    )
    p.add_argument(
        "--ownership",
        default="",
        help="Optional ownership CSV (dk_id,own_est); else uses projections own_est",
    )
    p.add_argument(
        "--spread",
        type=float,
        default=None,
        help="Vegas home spread (negative = home favored); biases scripts",
    )
    p.add_argument(
        "--total",
        type=float,
        default=None,
        help="Vegas game total (O/U); biases pace/pass_tilt",
    )
    p.add_argument(
        "--contest-meta",
        default="",
        help="Optional contest JSON (field_size, entry_fee, spread, total, prizes)",
    )
    p.add_argument(
        "--entry-ids",
        default="",
        help="CSV of Entry IDs for multi-entry upload format",
    )
    p.add_argument(
        "--entry-id-start",
        default="",
        help="Generate sequential Entry IDs starting at this value",
    )
    p.add_argument("--actuals", default="", help="Optional actual FP CSV for backtest")
    p.add_argument(
        "--backtest-out",
        default="",
        help="Calibration notes path (default: OUT/backtest-calibration.md)",
    )
    p.set_defaults(func=cmd_sim_showdown)

    c = sub.add_parser("sim-classic", help="Run Classic multi-game sim + portfolio")
    c.add_argument("--pool", required=True, help="Classic player pool CSV")
    c.add_argument("--projections", required=True, help="Classic projections CSV")
    c.add_argument("--n-sims", type=int, default=200)
    c.add_argument("--portfolio", type=int, default=20)
    c.add_argument("--exposure", type=float, default=0.40)
    c.add_argument("--seed", type=int, default=7)
    c.add_argument(
        "--engine",
        choices=["scorepath", "legacy"],
        default="scorepath",
        help="scorepath=per-game possession model (default); legacy=mean-tilt",
    )
    c.add_argument(
        "--progress-every",
        type=int,
        default=0,
        help="Print progress to stderr every N sims (0=off)",
    )
    c.add_argument("--out", required=True, help="Output directory")
    c.add_argument(
        "--read",
        action="append",
        default=[],
        help="Boost/fade read NAME=1.2 or dk_id=0.8 (repeatable)",
    )
    c.set_defaults(func=cmd_sim_classic)

    ing = sub.add_parser(
        "ingest-dk-salary",
        help="Normalize DK salary-file CSV → showdown/classic pool CSV",
    )
    ing.add_argument("--input", "-i", required=True, help="DK salary export CSV")
    ing.add_argument("--out", "-o", required=True, help="Normalized pool CSV path")
    ing.set_defaults(func=cmd_ingest_dk_salary)

    fb = sub.add_parser(
        "flashback",
        help="Score lineups vs actuals; write scores/summary/next-build gates",
    )
    fb.add_argument(
        "--lineups",
        required=True,
        help="Upload or export CSV (Showdown or Classic header)",
    )
    fb.add_argument("--actuals", required=True, help="Actual FP CSV (dk_id, actual_fp)")
    fb.add_argument(
        "--contest",
        default="",
        help="Optional contest.json (field_size, field_mean, field_std)",
    )
    fb.add_argument(
        "--payouts",
        default="",
        help="Optional payout CSV (place,payout)",
    )
    fb.add_argument(
        "--out",
        required=True,
        help="Output directory (e.g. backtests/) — never uploads/",
    )
    fb.set_defaults(func=cmd_flashback)

    sw = sub.add_parser(
        "scratch-watch",
        help="Compare entered lineups vs OUT/INACTIVE status; alert on hits only",
    )
    sw.add_argument(
        "--lineups",
        required=True,
        help="DK upload CSV (Classic or Showdown) with Name (id) cells",
    )
    sw.add_argument(
        "--pool",
        default="",
        help="Optional pool CSV for team lookup by dk_id",
    )
    sw.add_argument(
        "--status",
        default="",
        help="Optional local inactives CSV (dk_id or name, team, status)",
    )
    sw.add_argument(
        "--fetch",
        action="store_true",
        help="Try free public inactives (NFL.com/ESPN); fall back to Scout briefs",
    )
    sw.add_argument(
        "--games",
        default="",
        help="Comma game keys like DAL@NYG,DEN@KC (fetch scope)",
    )
    sw.add_argument(
        "--out",
        default="/home/box/nfl-dfs/exports/scratch-watch",
        help="Output directory (default: exports/scratch-watch)",
    )
    sw.add_argument(
        "--quiet-ok",
        action="store_true",
        help="Exit 0 and suppress stdout when all_clear",
    )
    sw.set_defaults(func=cmd_scratch_watch)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
