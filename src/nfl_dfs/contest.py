"""Optional contest pricing: field size, simple prizes, ownership leverage."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from nfl_dfs.showdown_rules import Lineup, Player


@dataclass
class ContestConfig:
    field_size: int = 1000
    entry_fee: float = 5.0
    # simple top-heavy prize structure: list of (place_from, place_to, payout)
    # default mini GPP: 1st 20%, 2nd 12%, 3rd 8%, 4-10 4% each, 11-20 2% each (of pool)
    prizes: list[tuple[int, int, float]] = field(default_factory=list)

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
    leverage: float  # sim uniqueness vs chalk ownership
    chalk_own: float  # product-ish / avg own of players
    est_cash_rate: float
    notes: str = ""


def _avg_own(lineup: Lineup, own_map: dict[str, float], default: float = 0.15) -> float:
    owns = []
    for pid in lineup.player_ids():
        owns.append(own_map.get(pid, default))
    return float(sum(owns) / max(1, len(owns)))


def price_lineups(
    lineups: Sequence[Lineup],
    players: Sequence[Player],
    contest: ContestConfig | None = None,
    sim_freq: dict[str, float] | None = None,
) -> list[PricedLineup]:
    """Price candidates: leverage = sim frequency − chalk ownership.

    When ownership missing, uses own_est on players or 15% default.
    """
    contest = contest or ContestConfig()
    own_map: dict[str, float] = {}
    for p in players:
        if p.own_est is not None:
            # accept 0–1 or 0–100
            o = float(p.own_est)
            own_map[p.dk_id] = o / 100.0 if o > 1.0 else o

    sim_freq = sim_freq or {}
    priced: list[PricedLineup] = []
    for lu in lineups:
        chalk = _avg_own(lu, own_map)
        freq = sim_freq.get(lu.key(), 0.0)
        # leverage: prefer lineups that sims like more than the field
        leverage = freq - chalk
        # crude cash proxy: higher sim_fp / lower chalk → better
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
                notes=note,
            )
        )
    priced.sort(key=lambda x: (-x.leverage, -x.lineup.sim_fp))
    return priced


def prize_for_place(contest: ContestConfig, place: int) -> float:
    for lo, hi, pay in contest.prizes:
        if lo <= place <= hi:
            return pay
    return 0.0
