"""
Heuristic budget suggestions from recurring expense patterns (prior months only).

Looks at calendar months strictly before the selected budget month, sums expenses
per classifier category per month, and suggests a limit from the median of months
where that category had meaningful spend.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from . import classifier, models


def _prior_months(year: int, month: int, count: int) -> list[tuple[int, int]]:
    y, m = year, month
    out: list[tuple[int, int]] = []
    for _ in range(count):
        m -= 1
        if m < 1:
            m = 12
            y -= 1
        out.append((y, m))
    return out


def _month_date_bounds(y: int, m: int) -> tuple[date, date]:
    from calendar import monthrange

    start = date(y, m, 1)
    end = date(y, m, monthrange(y, m)[1])
    return start, end


def _expense_totals_by_category(
    db: Session,
    account_id: int,
    start: date,
    end: date,
) -> dict[str, float]:
    rows = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.direction == "EXPENSE",
            models.Transaction.date >= start,
            models.Transaction.date <= end,
        )
        .all()
    )
    agg: dict[str, float] = defaultdict(float)
    for t in rows:
        cat = classifier.get_category_label(t.description_raw, t.amount) or "Other"
        agg[cat] += abs(float(t.amount))
    return dict(agg)


def _income_total_month(db: Session, account_id: int, start: date, end: date) -> float:
    rows = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.direction == "INCOME",
            models.Transaction.date >= start,
            models.Transaction.date <= end,
        )
        .all()
    )
    return sum(max(0.0, float(t.amount)) for t in rows)


def compute_recommendations(
    db: Session,
    account_id: int,
    target_year_month: str,
    *,
    lookback_months: int = 6,
    min_months_with_spend: int = 2,
    min_monthly_spend: float = 75.0,
    cushion: float = 1.06,
) -> dict[str, Any]:
    """
    target_year_month: "YYYY-MM" for the month the user is budgeting (suggestions use prior months only).

    Returns:
      rows: list of {category_name, recommended_limit, months_with_spend, lookback_months, median_active_month}
      income_hint_avg: average total income per month over the same lookback window (0 if none)
      prior_months_used: list of "YYYY-MM" labels for transparency
    """
    parts = target_year_month.strip().split("-")
    if len(parts) != 2:
        return {"rows": [], "income_hint_avg": 0.0, "prior_months_used": []}
    y, m = int(parts[0]), int(parts[1])
    priors = _prior_months(y, m, lookback_months)
    if not priors:
        return {"rows": [], "income_hint_avg": 0.0, "prior_months_used": []}

    prior_labels = [f"{py}-{pm:02d}" for py, pm in priors]
    per_month_by_cat: list[dict[str, float]] = []
    income_by_month: list[float] = []

    for py, pm in priors:
        s, e = _month_date_bounds(py, pm)
        per_month_by_cat.append(_expense_totals_by_category(db, account_id, s, e))
        income_by_month.append(_income_total_month(db, account_id, s, e))

    income_hint_avg = (
        statistics.mean(income_by_month) if income_by_month and any(x > 0 for x in income_by_month) else 0.0
    )

    all_cats: set[str] = set()
    for dct in per_month_by_cat:
        all_cats.update(dct.keys())

    rows_out: list[dict[str, Any]] = []
    for cat in sorted(all_cats):
        series = [dct.get(cat, 0.0) for dct in per_month_by_cat]
        active = [x for x in series if x >= min_monthly_spend]
        if len(active) < min_months_with_spend:
            continue
        med = float(statistics.median(active))
        raw = med * cushion
        rounded = max(min_monthly_spend, math.ceil(raw / 50.0) * 50.0)
        rows_out.append(
            {
                "category_name": cat,
                "recommended_limit": rounded,
                "months_with_spend": len(active),
                "lookback_months": lookback_months,
                "median_active_month": med,
            }
        )
    rows_out.sort(key=lambda r: r["recommended_limit"], reverse=True)
    return {
        "rows": rows_out,
        "income_hint_avg": income_hint_avg,
        "prior_months_used": prior_labels,
    }


def apply_recommendations(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    year_month: str,
    bank_account_id: int | None,
    lookback_months: int = 6,
) -> int:
    """Upserts one budget row per recommended category. Returns number of rows written."""
    payload = compute_recommendations(db, account_id, year_month, lookback_months=lookback_months)
    from . import crud

    n = 0
    from . import budget_503020

    for row in payload["rows"]:
        cname = row["category_name"]
        crud.upsert_monthly_budget(
            db,
            user_id=user_id,
            category_name=cname,
            year_month=year_month,
            amount_limit=float(row["recommended_limit"]),
            bank_account_id=bank_account_id,
            budget_bucket=budget_503020.budget_bucket_for_category(cname),
        )
        n += 1
    return n
