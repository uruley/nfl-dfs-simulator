"""Classic portfolio construction with exposure caps (no CPT)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence

from nfl_dfs.portfolio import GPP_MAJOR_TAGS, major_tags_for_lineup

from nfl_dfs.classic_rules import (
    SALARY_CAP,
    SLOT_ORDER,
    ClassicLineup,
    ClassicPlayer,
    lineup_salary,
    slate_game_count,
    validate_lineup,
)


@dataclass
class ClassicPortfolioResult:
    lineups: list[ClassicLineup]
    exposures: dict[str, float]
    script_tags: dict[str, int]
    n_unique_scripts: int
    n_unique_lineups: int
    major_tag_counts: dict[str, int] | None = None


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


def _diversity_score(lu: ClassicLineup, exp: Counter) -> float:
    penalty = sum(exp[pid] for pid in lu.player_ids())
    return lu.sim_fp - 8.0 * penalty


def _repair_lineup(
    lu: ClassicLineup,
    pool: Sequence[ClassicPlayer],
    exp: Counter,
    portfolio_size: int,
    max_exposure: float,
) -> ClassicLineup | None:
    """Swap over-exposed players for under-exposed legal alternatives."""
    max_count = int(max_exposure * portfolio_size + 1e-9)
    over = {pid for pid, c in exp.items() if c >= max_count}
    n_games = slate_game_count(pool)

    players = list(lu.players)
    used = {p.dk_id for p in players}

    for slot_i, p in enumerate(list(players)):
        if p.dk_id not in over and exp[p.dk_id] < max_count:
            continue
        slot = SLOT_ORDER[slot_i]
        alts = [
            cand
            for cand in pool
            if cand.dk_id not in used
            and cand.can_slot(slot)
            and exp[cand.dk_id] < max_count
        ]
        alts.sort(key=lambda x: -x.proj_fp)
        swapped = False
        for alt in alts:
            trial = list(players)
            trial[slot_i] = alt
            if lineup_salary(trial) > SALARY_CAP:
                continue
            trial_lu = ClassicLineup(
                players=trial,
                script_id=lu.script_id,
                sim_fp=sum(x.proj_fp for x in trial),
                salary=lineup_salary(trial),
                tag=lu.tag,
            )
            if validate_lineup(trial_lu, slate_n_games=n_games):
                continue
            used.discard(p.dk_id)
            used.add(alt.dk_id)
            players = trial
            swapped = True
            break
        if not swapped:
            return None

    ids = [p.dk_id for p in players]
    if len(ids) != len(set(ids)):
        return None
    if _would_exceed(exp, ids, portfolio_size, max_exposure):
        return None
    return ClassicLineup(
        players=players,
        script_id=lu.script_id,
        sim_fp=sum(p.proj_fp for p in players),
        salary=lineup_salary(players),
        tag=lu.tag,
    )


def build_classic_portfolio(
    candidates: Sequence[ClassicLineup],
    size: int = 20,
    max_exposure: float = 0.40,
    player_pool: Sequence[ClassicPlayer] | None = None,
    *,
    gpp: bool = True,
    min_per_tag: int = 2,
) -> ClassicPortfolioResult:
    if size <= 0:
        return ClassicPortfolioResult([], {}, {}, 0, 0, {})

    best_by_key: dict[str, ClassicLineup] = {}
    for lu in candidates:
        k = lu.key()
        if k not in best_by_key or lu.sim_fp > best_by_key[k].sim_fp:
            best_by_key[k] = lu
    unique = list(best_by_key.values())
    if not unique:
        return ClassicPortfolioResult([], {}, {}, 0, 0, {})

    pool: list[ClassicPlayer] = list(player_pool) if player_pool is not None else []
    if not pool:
        seen: dict[str, ClassicPlayer] = {}
        for lu in unique:
            for p in lu.players:
                seen[p.dk_id] = p
        pool = list(seen.values())

    selected: list[ClassicLineup] = []
    exp: Counter = Counter()
    used_keys: set[str] = set()
    used_scripts: set[int] = set()
    tag_counts: Counter = Counter()
    major_tag_counts: Counter = Counter()

    def try_add(lu: ClassicLineup, cap: float) -> bool:
        if lu.key() in used_keys:
            return False
        if _would_exceed(exp, lu.player_ids(), size, cap):
            return False
        selected.append(lu)
        used_keys.add(lu.key())
        if lu.script_id >= 0:
            used_scripts.add(lu.script_id)
        tag_counts[lu.tag or "unknown"] += 1
        for mt in major_tags_for_lineup(lu.tag):
            major_tag_counts[mt] += 1
        for pid in lu.player_ids():
            exp[pid] += 1
        return True

    # Pass GPP-0: seed min_per_tag from each major script family
    if gpp and min_per_tag > 0:
        by_major: dict[str, list[ClassicLineup]] = defaultdict(list)
        for lu in unique:
            for m in major_tags_for_lineup(lu.tag):
                by_major[m].append(lu)
        for m in by_major:
            by_major[m].sort(key=lambda x: -x.sim_fp)
        for maj in GPP_MAJOR_TAGS:
            if maj not in by_major:
                continue
            taken = 0
            for lu in by_major[maj]:
                if taken >= min_per_tag or len(selected) >= size:
                    break
                if try_add(lu, max_exposure):
                    taken += 1

    # Pass A: highest sim_fp first
    for lu in sorted(unique, key=lambda x: -x.sim_fp):
        if len(selected) >= size:
            break
        try_add(lu, max_exposure)

    # Pass B: diversity greedy
    while len(selected) < size:
        best: ClassicLineup | None = None
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

    # Pass C: repair
    if len(selected) < size:
        ranked = sorted(unique, key=lambda lu: -_diversity_score(lu, exp))
        for lu in ranked:
            if len(selected) >= size:
                break
            repaired = _repair_lineup(lu, pool, exp, size, max_exposure)
            if repaired is not None:
                try_add(repaired, max_exposure)

    # Pass D: slight relax
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
    return ClassicPortfolioResult(
        lineups=selected,
        exposures=exposures,
        script_tags=dict(tag_counts),
        n_unique_scripts=len(used_scripts),
        n_unique_lineups=len(used_keys),
        major_tag_counts=dict(major_tag_counts),
    )
