"""DraftKings NFL Showdown hard rules and pool I/O."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

SALARY_CAP = 50_000
CPT_MULT = 1.5
N_FLEX = 5
UPLOAD_HEADER = ["CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"]

CELL_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<id>[^)]+)\)\s*$")


@dataclass
class Player:
    dk_id: str
    name: str
    position: str
    team: str
    salary: int
    proj_fp: float
    std: float = 5.0
    opp: str = ""
    can_cpt: bool = True
    can_flex: bool = True
    own_est: float | None = None
    cpt_salary: int = 0
    boost: float = 1.0  # user read multiplier on sampled usage/FP mean

    def __post_init__(self) -> None:
        if not self.cpt_salary:
            self.cpt_salary = int(round(self.salary * CPT_MULT))

    def cell(self) -> str:
        return f"{self.name} ({self.dk_id})"


@dataclass
class Lineup:
    cpt: Player
    flex: list[Player]
    script_id: int = -1
    sim_fp: float = 0.0
    salary: int = 0
    tag: str = ""

    def player_ids(self) -> list[str]:
        return [self.cpt.dk_id] + [p.dk_id for p in self.flex]

    def key(self) -> str:
        return self.cpt.dk_id + "|" + ",".join(sorted(p.dk_id for p in self.flex))

    def cells(self) -> list[str]:
        return [self.cpt.cell()] + [p.cell() for p in self.flex]


def parse_cell(cell: str) -> tuple[str, str]:
    cell = (cell or "").strip()
    m = CELL_RE.match(cell)
    if not m:
        raise ValueError(f"Bad lineup cell (expected 'Name (id)'): {cell!r}")
    return m.group("name").strip(), m.group("id").strip()


def safe_float(x, default=None):
    try:
        if x is None or str(x).strip() == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def validate_lineup(lineup: Lineup, salary_cap: int = SALARY_CAP) -> list[str]:
    """Return list of rule violations (empty = legal)."""
    errs: list[str] = []
    if len(lineup.flex) != N_FLEX:
        errs.append(f"need {N_FLEX} FLEX, got {len(lineup.flex)}")
    ids = lineup.player_ids()
    if len(ids) != len(set(ids)):
        errs.append("players must be unique (CPT cannot also be FLEX)")
    if not lineup.cpt.can_cpt:
        errs.append(f"{lineup.cpt.name} not CPT-eligible")
    for p in lineup.flex:
        if not p.can_flex:
            errs.append(f"{p.name} not FLEX-eligible")
    sal = lineup.cpt.cpt_salary + sum(p.salary for p in lineup.flex)
    if sal > salary_cap:
        errs.append(f"salary {sal} exceeds cap {salary_cap}")
    lineup.salary = sal
    return errs


def lineup_salary(cpt: Player, flex: Iterable[Player]) -> int:
    return cpt.cpt_salary + sum(p.salary for p in flex)


def load_pool_csv(path: Path) -> list[dict]:
    players = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            name = (row.get("name") or row.get("Name") or "").strip()
            if not dk_id and not name:
                continue
            sal = safe_float(row.get("salary") or row.get("Salary"), 0) or 0
            roster = (
                row.get("roster_positions")
                or row.get("Roster Position")
                or row.get("roster_position")
                or "CPT/FLEX"
            )
            players.append(
                {
                    "dk_id": dk_id or name,
                    "name": name or dk_id,
                    "position": (row.get("position") or row.get("Position") or "").strip(),
                    "team": (row.get("team") or row.get("TeamAbbrev") or row.get("Team") or "").strip(),
                    "opp": (row.get("opp") or "").strip(),
                    "salary": int(sal),
                    "roster_positions": str(roster).upper(),
                }
            )
    return players


def load_projections_csv(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dk_id = str(row.get("dk_id") or row.get("ID") or "").strip()
            if not dk_id:
                continue
            proj = safe_float(row.get("proj_fp"))
            if proj is None:
                continue
            std = safe_float(row.get("std"))
            floor = safe_float(row.get("floor"))
            ceiling = safe_float(row.get("ceiling"))
            if std is None and floor is not None and ceiling is not None:
                std = max(1.0, (ceiling - floor) / 1.68)
            if std is None:
                std = max(3.0, proj * 0.40)
            sal = safe_float(row.get("salary"))
            cpt_sal = safe_float(row.get("cpt_salary"))
            if cpt_sal is None and sal is not None:
                cpt_sal = sal * CPT_MULT
            own = safe_float(row.get("own_est"))
            out[dk_id] = {
                "dk_id": dk_id,
                "name": (row.get("name") or "").strip(),
                "position": (row.get("position") or "").strip(),
                "team": (row.get("team") or "").strip(),
                "opp": (row.get("opp") or "").strip(),
                "salary": int(sal) if sal is not None else None,
                "proj_fp": proj,
                "std": std,
                "floor": floor,
                "ceiling": ceiling,
                "own_est": own,
                "cpt_salary": int(round(cpt_sal)) if cpt_sal is not None else None,
            }
    return out


def merge_pool_proj(pool: list[dict], proj: dict[str, dict]) -> list[Player]:
    merged: list[Player] = []
    for p in pool:
        dk_id = p["dk_id"]
        pr = proj.get(dk_id, {})
        salary = p.get("salary") or pr.get("salary") or 0
        proj_fp = pr.get("proj_fp", p.get("proj_fp"))
        if proj_fp is None:
            continue
        std = pr.get("std") or p.get("std") or max(3.0, float(proj_fp) * 0.40)
        roster = p.get("roster_positions") or "CPT/FLEX"
        ru = str(roster).upper()
        can_cpt = "CPT" in ru or ru in ("", "CPT/FLEX")
        can_flex = "FLEX" in ru or ru in ("", "CPT/FLEX")
        if not can_cpt and not can_flex:
            can_cpt = can_flex = True
        cpt_sal = pr.get("cpt_salary") or int(round(int(salary) * CPT_MULT))
        merged.append(
            Player(
                dk_id=dk_id,
                name=pr.get("name") or p.get("name") or dk_id,
                position=(pr.get("position") or p.get("position") or "").upper(),
                team=(pr.get("team") or p.get("team") or "").upper(),
                opp=(pr.get("opp") or p.get("opp") or "").upper(),
                salary=int(salary),
                proj_fp=float(proj_fp),
                std=float(std),
                can_cpt=can_cpt,
                can_flex=can_flex,
                own_est=pr.get("own_est"),
                cpt_salary=int(cpt_sal),
            )
        )
    return merged


def apply_reads(players: list[Player], reads: dict[str, float]) -> None:
    """Apply boost/fade multipliers keyed by dk_id or name (case-insensitive).

    Multiplier >1 boosts mean/usage in sims; <1 fades.
    """
    by_id = {p.dk_id: p for p in players}
    by_name = {p.name.lower(): p for p in players}
    for key, mult in reads.items():
        p = by_id.get(str(key)) or by_name.get(str(key).lower())
        if p is not None:
            p.boost = float(mult)


def write_upload_csv(path: Path, lineups: list[Lineup]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(UPLOAD_HEADER)
        for lu in lineups:
            w.writerow(lu.cells())
