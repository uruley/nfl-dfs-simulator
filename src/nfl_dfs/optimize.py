"""Fast Showdown optimizer: best CPT + 5 FLEX under salary for a sampled script."""

from __future__ import annotations

from typing import Sequence

import numpy as np

from nfl_dfs.showdown_rules import (
    CPT_MULT,
    N_FLEX,
    SALARY_CAP,
    Lineup,
    Player,
)


def _flex_fill(
    players: Sequence[Player],
    fp: np.ndarray,
    cpt_i: int,
    salary_cap: int,
) -> tuple[list[int], int] | None:
    """Greedy + local-swap FLEX fill for a fixed CPT. Returns (flex_idxs, sal_used)."""
    n = len(players)
    cpt_sal = players[cpt_i].cpt_salary
    if cpt_sal >= salary_cap:
        return None
    rem = salary_cap - cpt_sal
    salaries = [p.salary for p in players]

    cands: list[tuple[float, int, int]] = []
    for j in range(n):
        if j == cpt_i or not players[j].can_flex:
            continue
        cands.append((float(fp[j]), salaries[j], j))
    cands.sort(key=lambda x: (-x[0], x[1], x[2]))

    chosen: list[int] = []
    used = 0
    for fpts, sal, j in cands:
        if len(chosen) >= N_FLEX:
            break
        if used + sal <= rem:
            chosen.append(j)
            used += sal
    if len(chosen) < N_FLEX:
        leftover = [c for c in cands if c[2] not in chosen]
        leftover.sort(key=lambda x: (-(x[0] / max(1, x[1] / 1000.0)), -x[0]))
        for fpts, sal, j in leftover:
            if len(chosen) >= N_FLEX:
                break
            if used + sal <= rem:
                chosen.append(j)
                used += sal
    if len(chosen) < N_FLEX:
        return None

    chosen_set = set(chosen)
    improved = True
    while improved:
        improved = False
        for k, j in enumerate(list(chosen)):
            for fpts, sal, alt in cands:
                if alt in chosen_set or alt == cpt_i:
                    continue
                new_used = used - salaries[j] + sal
                if new_used <= rem and fpts > float(fp[j]):
                    chosen_set.remove(j)
                    chosen_set.add(alt)
                    chosen[k] = alt
                    used = new_used
                    improved = True
                    break
            if improved:
                break
    return chosen, cpt_sal + used


def build_best_lineup(
    players: Sequence[Player],
    fp: np.ndarray,
    script_id: int = -1,
    tag: str = "",
    salary_cap: int = SALARY_CAP,
) -> Lineup | None:
    """Try each CPT; return the single highest-scoring legal Showdown lineup."""
    best: Lineup | None = None
    for cpt_i, p in enumerate(players):
        if not p.can_cpt:
            continue
        filled = _flex_fill(players, fp, cpt_i, salary_cap)
        if filled is None:
            continue
        flex_is, sal = filled
        total = float(fp[cpt_i]) * CPT_MULT + float(sum(fp[j] for j in flex_is))
        lu = Lineup(
            cpt=players[cpt_i],
            flex=[players[j] for j in flex_is],
            script_id=script_id,
            sim_fp=total,
            salary=sal,
            tag=tag,
        )
        if best is None or lu.sim_fp > best.sim_fp:
            best = lu
    return best


def build_lineups_by_cpt(
    players: Sequence[Player],
    fp: np.ndarray,
    script_id: int = -1,
    tag: str = "",
    salary_cap: int = SALARY_CAP,
    top_n: int = 6,
) -> list[Lineup]:
    """Best lineup for each CPT, return top_n by score (portfolio diversity)."""
    by_cpt: list[Lineup] = []
    for cpt_i, p in enumerate(players):
        if not p.can_cpt:
            continue
        filled = _flex_fill(players, fp, cpt_i, salary_cap)
        if filled is None:
            continue
        flex_is, sal = filled
        total = float(fp[cpt_i]) * CPT_MULT + float(sum(fp[j] for j in flex_is))
        by_cpt.append(
            Lineup(
                cpt=players[cpt_i],
                flex=[players[j] for j in flex_is],
                script_id=script_id,
                sim_fp=total,
                salary=sal,
                tag=tag,
            )
        )
    by_cpt.sort(key=lambda x: -x.sim_fp)
    return by_cpt[:top_n]
