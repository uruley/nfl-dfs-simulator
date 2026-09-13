"""DraftKings NFL fantasy scoring."""

from __future__ import annotations

from dataclasses import dataclass


# Official DK NFL scoring (Showdown / Classic skill players)
PASS_YD = 0.04
PASS_TD = 4.0
INT = -1.0
RUSH_YD = 0.1
REC_YD = 0.1
RUSH_TD = 6.0
REC_TD = 6.0
RECEPTION = 1.0  # PPR
FUMBLE_LOST = -1.0
TWO_PT = 2.0
BONUS_PASS_300 = 3.0
BONUS_RUSH_100 = 3.0
BONUS_REC_100 = 3.0


@dataclass(frozen=True)
class StatLine:
    pass_yd: float = 0.0
    pass_td: float = 0.0
    interceptions: float = 0.0
    rush_yd: float = 0.0
    rush_td: float = 0.0
    rec_yd: float = 0.0
    rec_td: float = 0.0
    receptions: float = 0.0
    fumbles_lost: float = 0.0
    two_pt: float = 0.0


def score_stats(s: StatLine) -> float:
    """Convert a box-score style stat line to DK fantasy points."""
    fp = 0.0
    fp += s.pass_yd * PASS_YD
    fp += s.pass_td * PASS_TD
    fp += s.interceptions * INT
    fp += s.rush_yd * RUSH_YD
    fp += s.rush_td * RUSH_TD
    fp += s.rec_yd * REC_YD
    fp += s.rec_td * REC_TD
    fp += s.receptions * RECEPTION
    fp += s.fumbles_lost * FUMBLE_LOST
    fp += s.two_pt * TWO_PT
    if s.pass_yd >= 300:
        fp += BONUS_PASS_300
    if s.rush_yd >= 100:
        fp += BONUS_RUSH_100
    if s.rec_yd >= 100:
        fp += BONUS_REC_100
    return fp


def apply_cpt_multiplier(flex_fp: float, mult: float = 1.5) -> float:
    """CPT fantasy points = FLEX FP × multiplier."""
    return flex_fp * mult
