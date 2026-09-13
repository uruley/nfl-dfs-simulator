"""Portfolio construction across distinct scripts with exposure caps."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence

from nfl_dfs.showdown_rules import SALARY_CAP, Lineup, Player, lineup_salary


@dataclass
class PortfolioResult:
    lineups: list[Lineup]
    exposures: dict[str, float]  # dk_id -> fraction of portfolio
    script_tags: dict[str, int]
    n_unique_scripts: int
    n_unique_lineups: int


def _would_exceed(
    current: Counter,
    ids: list[str],
    portfolio_size: int,
    max_exposure: float,
) -> bool:
    for pid in ids:
        if (current[pid] + 1) / portfolio_size > max_exposure + 1e-9:
            return True
    return False


def _diversity_score(lu: Lineup, exp: Counter) -> float:
    penalty = sum(exp[pid] for pid in lu.player_ids())
    return lu.sim_fp - 8.0 * penalty


def _repair_lineup(
    lu: Lineup,
    pool: Sequence[Player],
    exp: Counter,
    portfolio_size: int,
    max_exposure: float,
) -> Lineup | None:
    """Swap over-exposed players for under-exposed legal alternatives."""
    max_count = int(max_exposure * portfolio_size + 1e-9)
    over = {pid for pid, c in exp.items() if c >= max_count}
    if not over and not _would_exceed(exp, lu.player_ids(), portfolio_size, max_exposure):
        return lu

    by_id = {p.dk_id: p for p in pool}
    # Start from lu; replace CPT if over-exposed
    cpt = lu.cpt
    flex = list(lu.flex)
    used = {cpt.dk_id, *(p.dk_id for p in flex)}

    def under_exposed_candidates(exclude: set[str], need_cpt: bool) -> list[Player]:
        out = []
        for p in pool:
            if p.dk_id in exclude:
                continue
            if need_cpt and not p.can_cpt:
                continue
            if not need_cpt and not p.can_flex:
                continue
            if exp[p.dk_id] >= max_count:
                continue
            out.append(p)
        # prefer higher proj among under-exposed
        out.sort(key=lambda p: -p.proj_fp)
        return out

    if cpt.dk_id in over or exp[cpt.dk_id] >= max_count:
        alts = under_exposed_candidates(used - {cpt.dk_id}, need_cpt=True)
        replaced = False
        for alt in alts:
            trial_used = (used - {cpt.dk_id}) | {alt.dk_id}
            trial_flex = [p for p in flex if p.dk_id != alt.dk_id]
            # ensure alt not already in flex
            if alt.dk_id in {p.dk_id for p in flex}:
                continue
            if lineup_salary(alt, flex) <= SALARY_CAP:
                cpt = alt
                used = {cpt.dk_id, *(p.dk_id for p in flex)}
                replaced = True
                break
        if not replaced:
            return None

    # Replace over-exposed FLEX one at a time
    for i, p in enumerate(list(flex)):
        if p.dk_id not in over and exp[p.dk_id] < max_count:
            continue
        exclude = {cpt.dk_id, *(x.dk_id for x in flex)}
        alts = under_exposed_candidates(exclude, need_cpt=False)
        swapped = False
        for alt in alts:
            trial = list(flex)
            trial[i] = alt
            if lineup_salary(cpt, trial) <= SALARY_CAP:
                flex = trial
                swapped = True
                break
        if not swapped:
            return None

    ids = [cpt.dk_id] + [p.dk_id for p in flex]
    if len(ids) != len(set(ids)):
        return None
    if _would_exceed(exp, ids, portfolio_size, max_exposure):
        return None
    if lineup_salary(cpt, flex) > SALARY_CAP:
        return None

    # Preserve script metadata; recompute rough sim_fp from projections
    sim_fp = cpt.proj_fp * 1.5 + sum(p.proj_fp for p in flex)
    return Lineup(
        cpt=cpt,
        flex=flex,
        script_id=lu.script_id,
        sim_fp=sim_fp,
        salary=lineup_salary(cpt, flex),
        tag=lu.tag,
    )


def build_portfolio(
    candidates: Sequence[Lineup],
    size: int = 20,
    max_exposure: float = 0.40,
    player_pool: Sequence[Player] | None = None,
) -> PortfolioResult:
    """Greedy portfolio: distinct keys, CPT diversity, exposure cap with repair."""
    if size <= 0:
        return PortfolioResult([], {}, {}, 0, 0)

    best_by_key: dict[str, Lineup] = {}
    for lu in candidates:
        k = lu.key()
        if k not in best_by_key or lu.sim_fp > best_by_key[k].sim_fp:
            best_by_key[k] = lu
    unique = list(best_by_key.values())
    if not unique:
        return PortfolioResult([], {}, {}, 0, 0)

    pool: list[Player] = list(player_pool) if player_pool is not None else []
    if not pool:
        seen: dict[str, Player] = {}
        for lu in unique:
            seen[lu.cpt.dk_id] = lu.cpt
            for p in lu.flex:
                seen[p.dk_id] = p
        pool = list(seen.values())

    by_cpt: dict[str, list[Lineup]] = defaultdict(list)
    for lu in unique:
        by_cpt[lu.cpt.dk_id].append(lu)
    for lst in by_cpt.values():
        lst.sort(key=lambda x: -x.sim_fp)

    selected: list[Lineup] = []
    exp: Counter = Counter()
    used_keys: set[str] = set()
    used_scripts: set[int] = set()
    tag_counts: Counter = Counter()

    def try_add(lu: Lineup, cap: float) -> bool:
        if lu.key() in used_keys:
            return False
        if _would_exceed(exp, lu.player_ids(), size, cap):
            return False
        selected.append(lu)
        used_keys.add(lu.key())
        if lu.script_id >= 0:
            used_scripts.add(lu.script_id)
        tag_counts[lu.tag or "unknown"] += 1
        for pid in lu.player_ids():
            exp[pid] += 1
        return True

    # Pass A: round-robin CPT
    cpt_order = sorted(by_cpt.keys(), key=lambda c: -by_cpt[c][0].sim_fp)
    progress = True
    while len(selected) < size and progress:
        progress = False
        for cpt_id in cpt_order:
            if len(selected) >= size:
                break
            for lu in by_cpt[cpt_id]:
                if try_add(lu, max_exposure):
                    progress = True
                    break

    # Pass B: diversity greedy
    while len(selected) < size:
        best: Lineup | None = None
        best_score = float("-inf")
        for lu in unique:
            if lu.key() in used_keys:
                continue
            if _would_exceed(exp, lu.player_ids(), size, max_exposure):
                continue
            score = _diversity_score(lu, exp)
            if lu.script_id >= 0 and lu.script_id not in used_scripts:
                score += 3.0
            if score > best_score:
                best_score = score
                best = lu
        if best is None:
            break
        try_add(best, max_exposure)

    # Pass C: repair over-exposed candidates into legal under-cap lineups
    if len(selected) < size:
        ranked = sorted(unique, key=lambda lu: -_diversity_score(lu, exp))
        for lu in ranked:
            if len(selected) >= size:
                break
            repaired = _repair_lineup(lu, pool, exp, size, max_exposure)
            if repaired is None:
                continue
            try_add(repaired, max_exposure)

    # Pass D: slight relax (+0.10) then repair again
    relax = max_exposure
    while len(selected) < size and relax < max_exposure + 0.10 - 1e-12:
        relax = min(1.0, relax + 0.05)
        ranked = sorted(unique, key=lambda lu: -_diversity_score(lu, exp))
        for lu in ranked:
            if len(selected) >= size:
                break
            if try_add(lu, relax):
                continue
            repaired = _repair_lineup(lu, pool, exp, size, relax)
            if repaired is not None:
                try_add(repaired, relax)

    n = max(1, len(selected))
    exposures = {pid: cnt / n for pid, cnt in exp.items()}
    return PortfolioResult(
        lineups=selected,
        exposures=exposures,
        script_tags=dict(tag_counts),
        n_unique_scripts=len(used_scripts),
        n_unique_lineups=len(used_keys),
    )
