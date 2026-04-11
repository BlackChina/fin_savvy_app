"""
Default monthly budget using the 50 / 30 / 20 rule on an estimated income baseline,
with category splits informed by the user's recent spending pattern (same account).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from . import budget_recommendations, classifier, models

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


def build_default_month_budget(
    db: Session,
    account_id: int,
    year_month: str,
    *,
    lookback_months: int = 6,
) -> dict[str, Any] | None:
    """
    Returns dict with:
      income_estimate, needs_pool, wants_pool, savings_pool,
      lines: [{category, limit, bucket}],
      reference_total (sum of limits),
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

    needs_pool = income_est * 0.50
    wants_pool = income_est * 0.30
    savings_pool = income_est * 0.20

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

    return {
        "income_estimate": round(income_est, 2),
        "needs_pool": round(needs_pool, 2),
        "wants_pool": round(wants_pool, 2),
        "savings_pool": round(savings_pool, 2),
        "lines": out_lines,
        "reference_total": round(ref_total, 2),
        "rule_note": (
            "50% Needs (essentials), 30% Wants (discretionary), 20% Savings & investments — "
            "pools are split across your usual categories from the last several months."
        ),
    }
