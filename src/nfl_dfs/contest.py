"""Contest pricing: ownership-weighted field simulation + crude leverage fallback."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from nfl_dfs.showdown_rules import (
    CPT_MULT,
    N_FLEX,
    SALARY_CAP,
    Lineup,
    Player,
    lineup_salary,
)


@dataclass
class ContestConfig:
    field_size: int = 1000
    entry_fee: float = 5.0
    # simple top-heavy prize structure: list of (place_from, place_to, payout)
    prizes: list[tuple[int, int, float]] = field(default_factory=list)
    spread: float | None = None
    total: float | None = None

    def __post_init__(self) -> None:
        if not self.prizes:
            pool = self.field_size * self.entry_fee * 0.80  # ~20% rake
            self.prizes = [
                (1, 1, pool * 0.20),
                (2, 2, pool * 0.12),
                (3, 3, pool * 0.08),
                (4, 10, pool * 0.04),
                (11, 20, pool * 0.02),
            ]


@dataclass
class PricedLineup:
    lineup: Lineup
    leverage: float
    chalk_own: float
    est_cash_rate: float
    win_rate: float = 0.0
    top1_rate: float = 0.0
    est_EV: float = 0.0
    notes: str = ""


def normalize_own(raw: float | None, default: float = 0.15) -> float:
    """Accept 0–1 or 0–100 ownership into a 0–1 fraction."""
    if raw is None:
        return default
    o = float(raw)
    if o > 1.0:
        o = o / 100.0
    return float(np.clip(o, 0.0005, 0.99))


def build_own_map(
    players: Sequence[Player],
    ownership: dict[str, float] | None = None,
    default: float = 0.15,
) -> dict[str, float]:
    """Merge CSV/external ownership with player.own_est."""
    own_map: dict[str, float] = {}
    for p in players:
        if ownership and p.dk_id in ownership:
            own_map[p.dk_id] = normalize_own(ownership[p.dk_id], default)
        elif p.own_est is not None:
            own_map[p.dk_id] = normalize_own(p.own_est, default)
        else:
            own_map[p.dk_id] = default
    if ownership:
        for k, v in ownership.items():
            own_map.setdefault(k, normalize_own(v, default))
    return own_map


def load_ownership_csv(path: Path) -> dict[str, float]:
    """Load dk_id → ownership from CSV (dk_id|ID + own_est|ownership|own)."""
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            if not dk_id:
                continue
            raw = None
            for col in ("own_est", "ownership", "own", "Own", "Ownership"):
                if col in row and str(row.get(col, "")).strip() != "":
                    try:
                        raw = float(row[col])
                    except ValueError:
                        raw = None
                    break
            if raw is None:
                continue
            out[dk_id] = normalize_own(raw)
    return out


def load_contest_meta(path: Path) -> ContestConfig:
    """Load ContestConfig (+ optional spread/total) from JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    prizes: list[tuple[int, int, float]] = []
    for item in data.get("prizes") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            prizes.append((int(item[0]), int(item[1]), float(item[2])))
        elif isinstance(item, dict):
            prizes.append(
                (int(item["place_from"]), int(item["place_to"]), float(item["payout"]))
            )
    return ContestConfig(
        field_size=int(data.get("field_size", 1000)),
        entry_fee=float(data.get("entry_fee", 5.0)),
        prizes=prizes,
        spread=float(data["spread"]) if data.get("spread") is not None else None,
        total=float(data["total"]) if data.get("total") is not None else None,
    )


def _avg_own(lineup: Lineup, own_map: dict[str, float], default: float = 0.15) -> float:
    owns = [own_map.get(pid, default) for pid in lineup.player_ids()]
    return float(sum(owns) / max(1, len(owns)))


def prize_for_place(contest: ContestConfig, place: int) -> float:
    for lo, hi, pay in contest.prizes:
        if lo <= place <= hi:
            return pay
    return 0.0


def cash_line(contest: ContestConfig) -> int:
    """Worst paying place (cash line) from prize table."""
    hi = 0
    for _lo, h, pay in contest.prizes:
        if pay > 0:
            hi = max(hi, h)
    return max(1, hi)


def _weighted_choice(
    ids: list[str], weights: np.ndarray, rng: np.random.Generator
) -> str:
    w = np.asarray(weights, dtype=np.float64)
    s = float(w.sum())
    if s <= 0 or not np.isfinite(s):
        w = np.ones(len(ids), dtype=np.float64) / max(1, len(ids))
    else:
        w = w / s
    idx = int(rng.choice(len(ids), p=w))
    return ids[idx]


def sample_field_lineup(
    players: Sequence[Player],
    own_map: dict[str, float],
    rng: np.random.Generator,
    salary_cap: int = SALARY_CAP,
    max_tries: int = 40,
) -> Lineup | None:
    """Sample one legal Showdown CPT+5FLEX lineup weighted by ownership."""
    by_id = {p.dk_id: p for p in players}
    cpt_pool = [p for p in players if p.can_cpt]
    flex_pool = [p for p in players if p.can_flex]
    if len(cpt_pool) < 1 or len(flex_pool) < N_FLEX + 1:
        return None

    for _ in range(max_tries):
        cpt_ids = [p.dk_id for p in cpt_pool]
        cpt_w = np.array([own_map.get(i, 0.15) for i in cpt_ids], dtype=np.float64)
        cpt_id = _weighted_choice(cpt_ids, cpt_w, rng)
        cpt = by_id[cpt_id]
        rem = salary_cap - cpt.cpt_salary
        if rem <= 0:
            continue

        chosen: list[Player] = []
        used_sal = 0
        used_ids = {cpt_id}
        ok = True
        for _slot in range(N_FLEX):
            avail = [
                p
                for p in flex_pool
                if p.dk_id not in used_ids and p.salary <= (rem - used_sal)
            ]
            if not avail:
                ok = False
                break
            ids = [p.dk_id for p in avail]
            w = np.array([own_map.get(i, 0.15) for i in ids], dtype=np.float64)
            pick = _weighted_choice(ids, w, rng)
            pl = by_id[pick]
            chosen.append(pl)
            used_ids.add(pick)
            used_sal += pl.salary
        if not ok or len(chosen) != N_FLEX:
            continue
        sal = lineup_salary(cpt, chosen)
        if sal > salary_cap:
            continue
        sim_fp = cpt.proj_fp * CPT_MULT + sum(p.proj_fp for p in chosen)
        return Lineup(
            cpt=cpt,
            flex=chosen,
            script_id=-1,
            sim_fp=sim_fp,
            salary=sal,
            tag="field",
        )
    return None


def sample_field_lineups(
    players: Sequence[Player],
    own_map: dict[str, float],
    n: int,
    rng: np.random.Generator,
    salary_cap: int = SALARY_CAP,
) -> list[Lineup]:
    """Sample N opponent lineups (legal Showdown) weighted by ownership."""
    out: list[Lineup] = []
    for _ in range(max(0, n)):
        lu = sample_field_lineup(players, own_map, rng, salary_cap=salary_cap)
        if lu is not None:
            out.append(lu)
    return out


def lineup_fp_from_map(lineup: Lineup, fp_by_id: dict[str, float]) -> float:
    """Score a Showdown lineup with CPT×1.5 using a player FP map."""
    cpt_fp = float(fp_by_id.get(lineup.cpt.dk_id, lineup.cpt.proj_fp))
    flex_fp = sum(float(fp_by_id.get(p.dk_id, p.proj_fp)) for p in lineup.flex)
    return cpt_fp * CPT_MULT + flex_fp


def _place_among(score: float, field_scores: np.ndarray) -> int:
    """1-based place counting opponents strictly above ``score``."""
    better = int(np.sum(field_scores > score))
    return better + 1


def field_sim_price(
    lineups: Sequence[Lineup],
    players: Sequence[Player],
    *,
    contest: ContestConfig | None = None,
    own_map: dict[str, float] | None = None,
    n_field: int = 200,
    rng: np.random.Generator | None = None,
    fp_by_id: dict[str, float] | None = None,
    sim_freq: dict[str, float] | None = None,
) -> list[PricedLineup]:
    """Price candidates vs an ownership-weighted simulated field.

    Metrics: win_rate, top1_rate, cash_rate, leverage, est_EV.
    Uses ``fp_by_id`` (actuals / flashback) when provided; otherwise ``sim_fp``.
    """
    contest = contest or ContestConfig()
    own_map = own_map or build_own_map(players)
    rng = rng or np.random.default_rng(0)
    sim_freq = sim_freq or {}

    if n_field <= 0:
        return price_lineups(
            lineups, players, contest=contest, sim_freq=sim_freq, own_map=own_map
        )

    field = sample_field_lineups(players, own_map, n_field, rng)
    if not field:
        return price_lineups(
            lineups, players, contest=contest, sim_freq=sim_freq, own_map=own_map
        )

    if fp_by_id is not None:
        field_scores = np.array(
            [lineup_fp_from_map(lu, fp_by_id) for lu in field], dtype=np.float64
        )
    else:
        field_scores = np.array([lu.sim_fp for lu in field], dtype=np.float64)

    n_f = max(1, len(field_scores))
    cash_at = cash_line(contest)
    cash_at_sim = max(1, int(round(cash_at * (n_f / max(1, contest.field_size)))))
    top1_at = max(1, int(np.ceil(0.01 * n_f)))
    first_prize = prize_for_place(contest, 1)
    mid_cash_prize = prize_for_place(contest, max(1, cash_at // 2))

    best_by_key: dict[str, Lineup] = {}
    for lu in lineups:
        k = lu.key()
        if k not in best_by_key or lu.sim_fp > best_by_key[k].sim_fp:
            best_by_key[k] = lu

    priced: list[PricedLineup] = []
    for lu in best_by_key.values():
        chalk = _avg_own(lu, own_map)
        if fp_by_id is not None:
            score = lineup_fp_from_map(lu, fp_by_id)
        else:
            score = float(lu.sim_fp)

        place = _place_among(score, field_scores)
        win_rate = float(np.mean(field_scores < score))
        top1_rate = 1.0 if place <= top1_at else 0.0
        # Soft cash: fraction of field we finish ahead of, scaled to payout depth.
        beat_frac = float(np.mean(field_scores <= score))
        hard_cash = 1.0 if place <= cash_at_sim else 0.0
        cash_rate = max(hard_cash, min(0.55, beat_frac * (cash_at / max(1, contest.field_size)) * 5.0))

        scale = contest.field_size / n_f
        contest_place = max(1, int(round(place * scale)))
        payout = prize_for_place(contest, contest_place)
        est_ev = (
            win_rate * first_prize * 0.55
            + cash_rate * mid_cash_prize * 0.35
            + payout * 0.15
            - contest.entry_fee
        )
        leverage = win_rate - chalk

        note = ""
        if chalk >= 0.35:
            note = "chalky"
        elif leverage >= 0.05 or win_rate >= 0.15:
            note = "leverage"

        priced.append(
            PricedLineup(
                lineup=lu,
                leverage=round(leverage, 4),
                chalk_own=round(chalk, 4),
                est_cash_rate=round(cash_rate, 4),
                win_rate=round(win_rate, 4),
                top1_rate=round(top1_rate, 4),
                est_EV=round(est_ev, 4),
                notes=note,
            )
        )

    priced.sort(key=lambda x: (-x.est_EV, -x.win_rate, -x.lineup.sim_fp))
    return priced


def price_lineups(
    lineups: Sequence[Lineup],
    players: Sequence[Player],
    contest: ContestConfig | None = None,
    sim_freq: dict[str, float] | None = None,
    own_map: dict[str, float] | None = None,
) -> list[PricedLineup]:
    """Crude price: leverage = sim frequency − chalk ownership (no field draw)."""
    contest = contest or ContestConfig()
    own_map = own_map or build_own_map(players)
    sim_freq = sim_freq or {}
    priced: list[PricedLineup] = []
    for lu in lineups:
        chalk = _avg_own(lu, own_map)
        freq = sim_freq.get(lu.key(), 0.0)
        leverage = freq - chalk
        est_cash = max(0.0, min(0.55, 0.20 + 0.15 * leverage + 0.002 * (lu.sim_fp - 80)))
        note = ""
        if chalk >= 0.35:
            note = "chalky"
        elif leverage >= 0.05:
            note = "leverage"
        priced.append(
            PricedLineup(
                lineup=lu,
                leverage=round(leverage, 4),
                chalk_own=round(chalk, 4),
                est_cash_rate=round(est_cash, 4),
                win_rate=0.0,
                top1_rate=0.0,
                est_EV=round(
                    est_cash * prize_for_place(contest, cash_line(contest))
                    - contest.entry_fee,
                    4,
                ),
                notes=note,
            )
        )
    priced.sort(key=lambda x: (-x.leverage, -x.lineup.sim_fp))
    return priced
