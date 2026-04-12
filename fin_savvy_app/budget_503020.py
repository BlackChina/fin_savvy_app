"""
Default monthly budget using the 50 / 30 / 20 rule on an estimated income baseline,
with category splits informed by the user's recent spending pattern (same account).
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from . import budget_recommendations, classifier, crud, models

# 50% needs — essential living + work
_NEEDS = frozenset(
    {
        "Telecommunications",
        "Groceries",
        "Fuel",
        "Transport",
        "Rent",
        "Utilities",
        "Health",
        "Education",
        "Insurance",
        "Bank Fees",
    }
)
_WANTS = frozenset(
    {
        "Dining",
        "Shopping",
        "Entertainment",
        "Alcohol & nightlife",
        "Personal Care",
        "Gifts",
        "Charity",
        "Travel",
    }
)
_SAVINGS = frozenset({"Savings", "Investments"})


def _bucket_for_category(name: str) -> str:
    if name in _NEEDS:
        return "needs"
    if name in _WANTS:
        return "wants"
    if name in _SAVINGS:
        return "savings"
    return "wants"


def budget_bucket_for_category(name: str) -> str:
    """Public alias for 50/30/20 bucket assignment (needs / wants / savings)."""
    return _bucket_for_category(name)


def _compliance_penalty_from_bucket_totals(
    buckets: dict[str, float],
    total_lim: float,
    income_estimate: float,
) -> float:
    if income_estimate is None or float(income_estimate) <= 0:
        return 0.0
    if total_lim <= 0:
        return 0.0
    targets = (0.50, 0.30, 0.20)
    keys = ("needs", "wants", "savings")
    dev = 0.0
    for k, t in zip(keys, targets):
        share = buckets[k] / total_lim
        dev += abs(share - t)
    return float(min(15.0, dev * 35.0))


def compliance_penalty_points(
    limits_by_category: dict[str, float],
    income_estimate: float,
) -> float:
    """
    Points to subtract from the composite score (0–15) when committed limits do not follow 50/30/20
    shares of income (for scratch / legacy / customized paths). Infers bucket from category names.
    """
    buckets = {"needs": 0.0, "wants": 0.0, "savings": 0.0}
    for cat, lim in limits_by_category.items():
        if lim <= 0:
            continue
        buckets[budget_bucket_for_category(cat)] += float(lim)
    total_lim = sum(buckets.values())
    return _compliance_penalty_from_bucket_totals(buckets, total_lim, income_estimate)


def split_balance_traffic_light(
    needs: float,
    wants: float,
    savings: float,
) -> dict[str, Any]:
    """
    Classify Needs/Wants/Savings share of allocated limits for UI (customize / tallies).

    Red: wants exceed needs, or savings share is at/below 10%.
    Green: at least as disciplined as 50/30/20 (needs>=~50%, wants<=~30%, savings>=~20%),
          or an equal needs/wants split with savings at/above ~20% (e.g. 40/40/20).
    Amber: other cases (e.g. equal needs/wants with savings between 10% and 20%).
    """
    total = float(needs) + float(wants) + float(savings)
    if total <= 0:
        return {
            "total": 0.0,
            "needs": float(needs),
            "wants": float(wants),
            "savings": float(savings),
            "needs_pct": 0.0,
            "wants_pct": 0.0,
            "savings_pct": 0.0,
            "state": "neutral",
            "label": "No limits yet",
        }
    n_pct = 100.0 * float(needs) / total
    w_pct = 100.0 * float(wants) / total
    s_pct = 100.0 * float(savings) / total
    eps = 0.85
    state = "amber"
    label = "Review split vs 50/30/20"

    if w_pct > n_pct + 0.15:
        state = "red"
        label = "Wants exceed needs"
    elif s_pct <= 10.0 + 1e-9:
        state = "red"
        label = "Savings & debt share is 10% or below"
    elif n_pct >= 50.0 - eps and w_pct <= 30.0 + eps and s_pct >= 20.0 - eps:
        state = "green"
        label = "At or better than 50/30/20"
    elif abs(n_pct - w_pct) <= eps and s_pct >= 20.0 - eps:
        state = "green"
        label = "Balanced needs/wants with healthy savings share"
    elif abs(n_pct - w_pct) <= eps and s_pct > 10.0 + 1e-9:
        state = "amber"
        label = "Equal needs/wants; savings under 20% target"
    elif s_pct < 20.0 - 1e-9 and s_pct > 10.0 + 1e-9 and w_pct <= n_pct + 0.15:
        state = "amber"
        label = "Savings share above 10% but under 20%"
    elif w_pct > 30.0 + eps and s_pct >= 10.0 - 1e-9:
        state = "amber"
        label = "Wants high relative to 30% guideline"

    return {
        "total": total,
        "needs": float(needs),
        "wants": float(wants),
        "savings": float(savings),
        "needs_pct": round(n_pct, 1),
        "wants_pct": round(w_pct, 1),
        "savings_pct": round(s_pct, 1),
        "state": state,
        "label": label,
    }


def compliance_penalty_from_limit_bucket_rows(
    rows: list[tuple[float, str]],
    income_estimate: float,
) -> float:
    """Same as compliance_penalty_points but each row is (limit, bucket) with bucket needs|wants|savings."""
    buckets = {"needs": 0.0, "wants": 0.0, "savings": 0.0}
    for lim, b in rows:
        lim = float(lim)
        if lim <= 0:
            continue
        bk = (b or "").strip().lower()
        if bk not in buckets:
            bk = "wants"
        buckets[bk] += lim
    total_lim = sum(buckets.values())
    return _compliance_penalty_from_bucket_totals(buckets, total_lim, income_estimate)



def _avg_income_last_months(db: Session, account_id: int, priors: list[tuple[int, int]]) -> float:
    tot = 0.0
    n = 0
    for y, m in priors:
        s, e = budget_recommendations._month_date_bounds(y, m)
        tot += budget_recommendations._income_total_month(db, account_id, s, e)
        n += 1
    return tot / n if n else 0.0


def _avg_expense_last_months(db: Session, account_id: int, priors: list[tuple[int, int]]) -> float:
    tot = 0.0
    n = 0
    for y, m in priors:
        s, e = budget_recommendations._month_date_bounds(y, m)
        by_c = budget_recommendations._expense_totals_by_category(db, account_id, s, e)
        tot += sum(by_c.values())
        n += 1
    return tot / n if n else 0.0


def _aggregate_expense_by_category_span(
    db: Session,
    account_id: int,
    months: list[tuple[int, int]],
) -> dict[str, float]:
    agg: dict[str, float] = defaultdict(float)
    for y, m in months:
        s, e = budget_recommendations._month_date_bounds(y, m)
        part = budget_recommendations._expense_totals_by_category(db, account_id, s, e)
        for k, v in part.items():
            agg[k] += v
    return dict(agg)


def min_monthly_carryover_default() -> float:
    raw = os.environ.get("FINSAVVY_MIN_MONTHLY_CARRYOVER", "2000").strip() or "2000"
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 2000.0


def build_default_month_budget(
    db: Session,
    account_id: int,
    year_month: str,
    *,
    lookback_months: int = 6,
) -> dict[str, Any] | None:
    """
    Returns dict with:
      income_estimate (for UI / score: prior calendar month income when available),
      needs_pool, wants_pool, savings_pool,
      lines: [{category, limit, bucket}],
      reference_total (sum of limits),
      prior_month_for_income, prior_month_income, min_monthly_carryover, allocatable_for_502020,
      rule_note (str)
    """
    parts = year_month.strip().split("-")
    if len(parts) != 2:
        return None
    y, m = int(parts[0]), int(parts[1])
    priors = budget_recommendations._prior_months(y, m, lookback_months)
    if not priors:
        return None

    inc_avg = _avg_income_last_months(db, account_id, priors)
    exp_avg = _avg_expense_last_months(db, account_id, priors)
    # Baseline income for rule: prefer observed income; else infer from spending.
    income_est = max(inc_avg, exp_avg * 1.05) if inc_avg < 500 else max(inc_avg, exp_avg)
    if income_est < 500:
        income_est = max(exp_avg, 5000.0)

    prior_y, prior_m = (y - 1, 12) if m == 1 else (y, m - 1)
    prior_income = float(crud.sum_income_for_account_calendar_month(db, account_id, prior_y, prior_m))
    min_carry = _min_monthly_carryover_amount()
    use_prior_income = prior_income >= 100.0
    if use_prior_income:
        envelope_income = max(0.0, prior_income - min_carry)
        if envelope_income <= 0:
            envelope_income = prior_income
        income_for_pools = envelope_income
        income_display = prior_income
    else:
        income_for_pools = float(income_est)
        income_display = float(income_est)

    needs_pool = income_for_pools * 0.50
    wants_pool = income_for_pools * 0.30
    savings_pool = income_for_pools * 0.20

    span_totals = _aggregate_expense_by_category_span(db, account_id, priors)
    bucket_spend = {"needs": 0.0, "wants": 0.0, "savings": 0.0}
    bucket_cats: dict[str, list[str]] = {"needs": [], "wants": [], "savings": []}
    for cat, amt in span_totals.items():
        b = _bucket_for_category(cat)
        bucket_spend[b] += amt
        bucket_cats[b].append(cat)

    lines: list[dict[str, Any]] = []
    for bucket, pool in (("needs", needs_pool), ("wants", wants_pool), ("savings", savings_pool)):
        cats = sorted(set(bucket_cats[bucket]), key=lambda c: -span_totals.get(c, 0.0))
        spend_sum = bucket_spend[bucket]
        if spend_sum <= 0 or not cats:
            # Even split across classifier names in this bucket so user still sees lines
            fallback = [c for c in classifier.get_all_category_names() if _bucket_for_category(c) == bucket][:4]
            if not fallback:
                continue
            each = max(100.0, pool / len(fallback))
            for c in fallback:
                lines.append({"category": c, "limit": math.ceil(each / 50) * 50, "bucket": bucket})
            continue
        subset = cats[:12]
        subsum = sum(span_totals.get(c, 0.0) for c in subset)
        for cat in subset:
            share = (span_totals.get(cat, 0.0) / subsum) if subsum > 0 else (1.0 / max(len(subset), 1))
            raw = pool * share
            lim = max(50.0, math.ceil(raw / 50.0) * 50.0)
            lines.append({"category": cat, "limit": float(lim), "bucket": bucket})

    # Collapse duplicate categories (shouldn't happen) and trim tiny lines
    merged: dict[str, dict[str, Any]] = {}
    for row in lines:
        c = row["category"]
        if c not in merged:
            merged[c] = {"category": c, "limit": 0.0, "bucket": row["bucket"]}
        merged[c]["limit"] += row["limit"]
    out_lines = sorted(merged.values(), key=lambda r: (-r["limit"], r["category"]))
    ref_total = sum(r["limit"] for r in out_lines)

    allocatable = float(income_for_pools)
    if use_prior_income and allocatable > 0 and ref_total > 0 and abs(ref_total - allocatable) > 25.0:
        adj = allocatable / ref_total
        for r in out_lines:
            r["limit"] = max(50.0, math.ceil(float(r["limit"]) * adj / 50.0) * 50.0)
        ref_total = sum(float(r["limit"]) for r in out_lines)
        drift = allocatable - ref_total
        if abs(drift) >= 1.0 and out_lines:
            i0 = max(range(len(out_lines)), key=lambda i: float(out_lines[i]["limit"]))
            out_lines[i0]["limit"] = max(50.0, float(out_lines[i0]["limit"]) + drift)
            ref_total = sum(float(r["limit"]) for r in out_lines)

    if use_prior_income:
        pm_label = f"{prior_y:04d}-{prior_m:02d}"
        rule_note = (
            f"Based on last month’s income on this account (R {prior_income:.2f} in {pm_label}, same basis as the dashboard), "
            f"we reserve R {min_carry:.2f} as minimum carry-over; the 50/30/20 split applies to about R {allocatable:.2f} after that reserve. "
            "Category lines use your usual spending mix from prior months."
        )
    else:
        rule_note = (
            "50% Needs (essentials), 30% Wants (discretionary), 20% Savings & investments — "
            "pools are split across your usual categories from the last several months "
            "(last month’s income was not enough to anchor the envelope, so we used your recent average)."
        )

    return {
        "income_estimate": round(income_display, 2),
        "needs_pool": round(needs_pool, 2),
        "wants_pool": round(wants_pool, 2),
        "savings_pool": round(savings_pool, 2),
        "lines": out_lines,
        "reference_total": round(ref_total, 2),
        "prior_month_for_income": f"{prior_y:04d}-{prior_m:02d}",
        "prior_month_income": round(prior_income, 2) if use_prior_income else None,
        "min_monthly_carryover": round(min_carry, 2),
        "allocatable_for_502020": round(allocatable, 2),
        "rule_note": rule_note,
    }
