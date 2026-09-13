"""Backtest hook: score a portfolio vs actual fantasy points; write calibration notes.

Never mutates delivered CSVs in uploads/.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from nfl_dfs.scoring import apply_cpt_multiplier
from nfl_dfs.showdown_rules import CPT_MULT, Lineup, parse_cell


@dataclass
class BacktestResult:
    lineup_scores: list[float]
    mean_fp: float
    max_fp: float
    min_fp: float
    notes_path: Path | None = None


def load_actual_fp(path: Path) -> dict[str, float]:
    """Load actual DK FP keyed by dk_id. Accepts columns: dk_id/ID, actual_fp/fp/proj_fp."""
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            if not dk_id:
                # try Name (id) cell
                cell = row.get("player") or row.get("Name") or ""
                if cell and "(" in cell:
                    try:
                        _, dk_id = parse_cell(cell)
                    except ValueError:
                        continue
            val = row.get("actual_fp") or row.get("fp") or row.get("fpts") or row.get("proj_fp")
            if dk_id and val not in (None, ""):
                out[dk_id] = float(val)
    return out


def score_lineup(lineup: Lineup, actual: dict[str, float]) -> float:
    cpt_fp = actual.get(lineup.cpt.dk_id, 0.0)
    total = apply_cpt_multiplier(cpt_fp, CPT_MULT)
    for p in lineup.flex:
        total += actual.get(p.dk_id, 0.0)
    return total


def score_portfolio(
    lineups: Sequence[Lineup],
    actual: dict[str, float],
) -> BacktestResult:
    scores = [score_lineup(lu, actual) for lu in lineups]
    if not scores:
        return BacktestResult([], 0.0, 0.0, 0.0)
    return BacktestResult(
        lineup_scores=scores,
        mean_fp=sum(scores) / len(scores),
        max_fp=max(scores),
        min_fp=min(scores),
    )


def write_calibration_notes(
    path: Path,
    result: BacktestResult,
    exposures: dict[str, float] | None = None,
    actual: dict[str, float] | None = None,
    extra: str = "",
) -> Path:
    """Write calibration notes for next-build gates. Does not touch uploads/."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Showdown backtest calibration notes",
        "",
        "Gates apply **next build only** — never patch mid-slate live upload files.",
        "",
        f"- portfolio_size: {len(result.lineup_scores)}",
        f"- mean_fp: {result.mean_fp:.2f}",
        f"- max_fp: {result.max_fp:.2f}",
        f"- min_fp: {result.min_fp:.2f}",
        "",
    ]
    if result.lineup_scores:
        ranked = sorted(enumerate(result.lineup_scores), key=lambda x: -x[1])
        lines.append("Top scored lineups (index, fp):")
        for idx, fp in ranked[:5]:
            lines.append(f"  - #{idx}: {fp:.2f}")
        lines.append("")
    if exposures and actual:
        # players with high exposure but low actual — fade candidates next build
        ranked_exp = sorted(exposures.items(), key=lambda x: -x[1])
        lines.append("High-exposure players vs actual FP:")
        for pid, exp in ranked_exp[:12]:
            fp = actual.get(pid, float("nan"))
            lines.append(f"  - {pid}: exposure={exp:.1%} actual_fp={fp}")
        lines.append("")
        dead = [
            (pid, exp, actual.get(pid, 0.0))
            for pid, exp in ranked_exp
            if actual.get(pid, 0.0) < 5.0 and exp >= 0.25
        ]
        if dead:
            lines.append("Calibration flags (high own/exposure, low actual):")
            for pid, exp, fp in dead:
                lines.append(f"  - FADE_NEXT: {pid} exp={exp:.1%} fp={fp:.1f}")
            lines.append("")
    if extra:
        lines.append(extra)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
