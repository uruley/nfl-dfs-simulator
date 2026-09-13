"""Contest Flashback: score delivered lineups vs actuals; emit next-build gates.

NEVER mutates files under uploads/ — only reads them and writes to --out (e.g. backtests/).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from nfl_dfs.backtest import load_actual_fp
from nfl_dfs.scoring import apply_cpt_multiplier
from nfl_dfs.showdown_rules import CPT_MULT, parse_cell as sd_parse_cell

ContestKind = Literal["showdown", "classic"]

SHOWDOWN_HEADER = ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"]
CLASSIC_HEADER = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]


@dataclass
class ScoredLineup:
    index: int
    kind: ContestKind
    cells: list[str]
    player_ids: list[str]
    score: float
    rank: int = 0


@dataclass
class FlashbackResult:
    kind: ContestKind
    scores: list[ScoredLineup]
    mean_fp: float
    max_fp: float
    min_fp: float
    exposures: dict[str, float]
    player_actual: dict[str, float]
    id_to_name: dict[str, str]
    gates: list[dict[str, Any]] = field(default_factory=list)
    field_notes: str = ""


def detect_upload_kind(header: list[str]) -> ContestKind:
    h = [c.strip().upper() for c in header]
    if h and h[0] == "CPT":
        return "showdown"
    if "QB" in h and "DST" in h:
        return "classic"
    if h[:6] == SHOWDOWN_HEADER:
        return "showdown"
    return "classic"


def load_lineups_csv(path: Path) -> tuple[ContestKind, list[list[str]]]:
    """Load upload/export CSV → (kind, list of cell rows).

    Cells expected as 'Name (id)'. Skips blank rows.
    """
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"empty lineups file: {path}")
    header = rows[0]
    kind = detect_upload_kind(header)
    lineups: list[list[str]] = []
    for row in rows[1:]:
        cells = [c.strip() for c in row if c.strip()]
        if not cells:
            continue
        lineups.append(cells)
    return kind, lineups


def _ids_from_cells(cells: list[str]) -> tuple[list[str], dict[str, str]]:
    ids: list[str] = []
    names: dict[str, str] = {}
    for c in cells:
        try:
            name, dk_id = sd_parse_cell(c)
        except ValueError:
            # bare id fallback
            dk_id = c
            name = c
        ids.append(dk_id)
        names[dk_id] = name
    return ids, names


def score_cells(
    cells: list[str],
    actual: dict[str, float],
    kind: ContestKind,
) -> tuple[float, list[str], dict[str, str]]:
    ids, names = _ids_from_cells(cells)
    if kind == "showdown":
        if not ids:
            return 0.0, ids, names
        total = apply_cpt_multiplier(actual.get(ids[0], 0.0), CPT_MULT)
        for pid in ids[1:]:
            total += actual.get(pid, 0.0)
        return total, ids, names
    total = sum(actual.get(pid, 0.0) for pid in ids)
    return total, ids, names


def _portfolio_exposures(lineups_ids: list[list[str]]) -> dict[str, float]:
    n = max(1, len(lineups_ids))
    cnt: Counter = Counter()
    for ids in lineups_ids:
        for pid in set(ids):  # once per lineup
            cnt[pid] += 1
    return {pid: c / n for pid, c in cnt.items()}


def build_gates(
    exposures: dict[str, float],
    actual: dict[str, float],
    id_to_name: dict[str, str],
    *,
    fade_exp_min: float = 0.35,
    fade_fp_max: float = 6.0,
    boost_exp_max: float = 0.20,
    boost_fp_min: float = 18.0,
) -> list[dict[str, Any]]:
    """Conservative next-build gates.

    - fade: high portfolio exposure + low actual FP
    - boost: low exposure + high actual FP (under-owned winners)
    Caps: at most 5 fades and 5 boosts; requires clear thresholds.
    """
    gates: list[dict[str, Any]] = []
    ranked = sorted(exposures.items(), key=lambda x: -x[1])

    fades = []
    for pid, exp in ranked:
        fp = actual.get(pid, 0.0)
        if exp >= fade_exp_min and fp < fade_fp_max:
            fades.append(
                {
                    "action": "fade",
                    "dk_id": pid,
                    "name": id_to_name.get(pid, pid),
                    "exposure": round(exp, 4),
                    "actual_fp": round(fp, 2),
                    "suggestion": "reduce exposure next build (overexposed underperformer)",
                    "read_hint": f"{pid}=0.75",
                }
            )
    for g in fades[:5]:
        gates.append(g)

    # boost: players who scored big but we under-used (or missed)
    # include actual keys not in exposures as exp=0
    all_pids = set(actual) | set(exposures)
    boosts = []
    for pid in all_pids:
        exp = exposures.get(pid, 0.0)
        fp = actual.get(pid, 0.0)
        if exp <= boost_exp_max and fp >= boost_fp_min:
            boosts.append(
                {
                    "action": "boost",
                    "dk_id": pid,
                    "name": id_to_name.get(pid, pid),
                    "exposure": round(exp, 4),
                    "actual_fp": round(fp, 2),
                    "suggestion": "consider higher exposure next build (under-owned winner)",
                    "read_hint": f"{pid}=1.15",
                }
            )
    boosts.sort(key=lambda g: (-g["actual_fp"], g["exposure"]))
    for g in boosts[:5]:
        gates.append(g)

    return gates


def load_contest_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_payout_csv(path: Path | None) -> list[tuple[int, float]]:
    """Optional payouts: place,payout columns → list of (place, payout)."""
    if path is None or not path.exists():
        return []
    out: list[tuple[int, float]] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            place = row.get("place") or row.get("Place") or row.get("rank")
            pay = row.get("payout") or row.get("Payout") or row.get("prize")
            if place and pay not in (None, ""):
                out.append((int(place), float(pay)))
    return out


def estimate_field_ranks(
    scores: list[float],
    contest: dict[str, Any],
    rng_seed: int = 7,
) -> list[int]:
    """Optional crude field sim: sample field_size scores ~ N(mean, std) and rank portfolio.

    Returns 1-based estimated place for each portfolio lineup.
    """
    import numpy as np

    field_size = int(contest.get("field_size") or 0)
    if field_size <= 0 or not scores:
        return [0] * len(scores)
    mean = float(contest.get("field_mean") or (sum(scores) / len(scores)))
    std = float(contest.get("field_std") or max(8.0, np.std(scores) * 1.2 if len(scores) > 1 else 15.0))
    rng = np.random.default_rng(rng_seed)
    field = rng.normal(mean, std, size=field_size)
    places = []
    for s in scores:
        # place = 1 + count of field scores strictly greater
        place = 1 + int(np.sum(field > s))
        places.append(place)
    return places


def run_flashback(
    lineups_path: Path,
    actuals_path: Path,
    contest_path: Path | None = None,
    payouts_path: Path | None = None,
) -> FlashbackResult:
    kind, cell_rows = load_lineups_csv(lineups_path)
    actual = load_actual_fp(actuals_path)
    contest = load_contest_json(contest_path)
    payouts = load_payout_csv(payouts_path)

    scored: list[ScoredLineup] = []
    all_ids: list[list[str]] = []
    id_to_name: dict[str, str] = {}
    for i, cells in enumerate(cell_rows):
        fp, ids, names = score_cells(cells, actual, kind)
        id_to_name.update(names)
        all_ids.append(ids)
        scored.append(
            ScoredLineup(
                index=i,
                kind=kind,
                cells=cells,
                player_ids=ids,
                score=fp,
            )
        )

    ranked = sorted(range(len(scored)), key=lambda i: -scored[i].score)
    for rank, i in enumerate(ranked, start=1):
        scored[i].rank = rank

    scores_only = [s.score for s in scored]
    exposures = _portfolio_exposures(all_ids)
    gates = build_gates(exposures, actual, id_to_name)

    field_notes = ""
    if contest.get("field_size"):
        places = estimate_field_ranks(scores_only, contest)
        field_notes = (
            f"Field sim field_size={contest['field_size']}: "
            f"best_est_place={min(places) if places else 'n/a'} "
            f"median_est_place={sorted(places)[len(places)//2] if places else 'n/a'}"
        )
        for s, place in zip(scored, places):
            # stash as attribute via cells note — keep on object
            setattr(s, "est_place", place)

    if payouts and scores_only:
        # map portfolio rank → payout if place matches
        pay_map = {p: pay for p, pay in payouts}
        total_pay = 0.0
        for s in scored:
            total_pay += pay_map.get(s.rank, 0.0)
        field_notes += ("; " if field_notes else "") + f"payout_csv_sum_by_portfolio_rank=${total_pay:.2f}"

    if not scores_only:
        mean = max_ = min_ = 0.0
    else:
        mean = sum(scores_only) / len(scores_only)
        max_ = max(scores_only)
        min_ = min(scores_only)

    return FlashbackResult(
        kind=kind,
        scores=scored,
        mean_fp=mean,
        max_fp=max_,
        min_fp=min_,
        exposures=exposures,
        player_actual=actual,
        id_to_name=id_to_name,
        gates=gates,
        field_notes=field_notes,
    )


def write_flashback_artifacts(result: FlashbackResult, out_dir: Path) -> dict[str, Path]:
    """Write scores CSV, summary MD, next-build-gates JSON. Never touches uploads/."""
    out_dir = Path(out_dir)
    # Safety: refuse to write into uploads/
    if out_dir.resolve().name == "uploads" or "uploads" in out_dir.resolve().parts:
        # allow only if explicitly not the delivered uploads tree — hard refuse
        raise ValueError("Refusing to write flashback artifacts under uploads/")

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    scores_path = out_dir / "flashback-scores.csv"
    with scores_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["lineup_index", "rank", "score", "kind", "players"]
        if result.scores and hasattr(result.scores[0], "est_place"):
            fields.insert(3, "est_place")
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in sorted(result.scores, key=lambda x: x.rank):
            row = {
                "lineup_index": s.index,
                "rank": s.rank,
                "score": round(s.score, 2),
                "kind": s.kind,
                "players": " | ".join(s.cells),
            }
            if hasattr(s, "est_place"):
                row["est_place"] = getattr(s, "est_place")
            w.writerow(row)
    paths["scores"] = scores_path

    summary_path = out_dir / "flashback-summary.md"
    lines = [
        f"# Contest Flashback ({result.kind})",
        "",
        "Gates apply **next build only**. This tool never mutates `uploads/`.",
        "",
        f"- lineups: {len(result.scores)}",
        f"- mean_fp: {result.mean_fp:.2f}",
        f"- max_fp: {result.max_fp:.2f}",
        f"- min_fp: {result.min_fp:.2f}",
        "",
    ]
    if result.field_notes:
        lines.append(f"- field: {result.field_notes}")
        lines.append("")
    lines.append("## Top lineups")
    for s in sorted(result.scores, key=lambda x: x.rank)[:10]:
        lines.append(f"- #{s.rank} (idx {s.index}): **{s.score:.2f}** — {', '.join(s.cells[:3])}…")
    lines.append("")
    lines.append("## Exposures vs actual")
    for pid, exp in sorted(result.exposures.items(), key=lambda x: -x[1])[:15]:
        fp = result.player_actual.get(pid, float("nan"))
        name = result.id_to_name.get(pid, pid)
        lines.append(f"- {name} ({pid}): exp={exp:.1%} actual={fp}")
    lines.append("")
    lines.append("## Next-build gates (conservative)")
    if not result.gates:
        lines.append("- (none triggered)")
    for g in result.gates:
        lines.append(
            f"- **{g['action'].upper()}** {g['name']} ({g['dk_id']}): "
            f"exp={g['exposure']:.1%} fp={g['actual_fp']} — {g['suggestion']}"
        )
    lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    paths["summary"] = summary_path

    gates_path = out_dir / "next-build-gates.json"
    payload = {
        "version": 1,
        "kind": result.kind,
        "policy": {
            "fade_exp_min": 0.35,
            "fade_fp_max": 6.0,
            "boost_exp_max": 0.20,
            "boost_fp_min": 18.0,
            "max_fades": 5,
            "max_boosts": 5,
            "note": "Conservative defaults. Apply next build only; never patch live uploads.",
        },
        "portfolio": {
            "n_lineups": len(result.scores),
            "mean_fp": round(result.mean_fp, 2),
            "max_fp": round(result.max_fp, 2),
            "min_fp": round(result.min_fp, 2),
        },
        "gates": result.gates,
    }
    gates_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    paths["gates"] = gates_path
    return paths