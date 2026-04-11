"""Server-side rules for customizing the system 50/30/20 budget before commit."""

from __future__ import annotations

from typing import Any


def _norm_line(cat: str, lim: float) -> tuple[str, float]:
    return (cat.strip(), round(float(lim), 2))


def validate_customized_503020(
    baseline: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    *,
    max_line_change_ratio: float = 0.40,
    total_min_ratio: float = 0.75,
    total_max_ratio: float = 1.25,
) -> str | None:
    """
    baseline / submitted entries: {"category": str, "limit": float}.
    Returns error message or None if OK.
    Rules:
      - Same multiset of categories as baseline (one row per category).
      - At most max_line_change_ratio of the N lines may have a different limit than baseline.
      - Sum(submitted limits) within [total_min_ratio, total_max_ratio] of sum(baseline limits).
    """
    if not baseline:
        return "No baseline budget to customize — reload the page."
    base_map = {str(r["category"]).strip(): float(r["limit"]) for r in baseline}
    sub_map = {str(r["category"]).strip(): float(r["limit"]) for r in submitted}
    if set(base_map.keys()) != set(sub_map.keys()):
        return "Categories must match the suggested budget; you can only change limits (not add or remove lines here)."
    if len(base_map) != len(baseline):
        return "Baseline has duplicate categories — cannot validate."
    base_total = sum(base_map.values())
    if base_total <= 0:
        return "Invalid baseline total."
    sub_total = sum(sub_map[c] for c in base_map)
    lo, hi = base_total * total_min_ratio, base_total * total_max_ratio
    if sub_total < lo - 0.01 or sub_total > hi + 0.01:
        return (
            f"Total budget must stay within {(total_min_ratio * 100):.0f}%–{(total_max_ratio * 100):.0f}% "
            f"of the suggested total (R {base_total:,.2f}). Your total is R {sub_total:,.2f}."
        ).replace(",", " ")
    n = len(base_map)
    max_changed = max(0, int(n * max_line_change_ratio))
    changed = 0
    eps = 0.5
    for c, b_lim in base_map.items():
        if abs(float(sub_map[c]) - float(b_lim)) > eps:
            changed += 1
    if changed > max_changed:
        return (
            f"You can change at most {max_changed} of {n} budget lines (~{int(max_line_change_ratio * 100)}%). "
            f"You changed {changed}."
        )
    return None
