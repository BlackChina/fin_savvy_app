"""
In-app alerts: missing receipt coverage, unclassified-heavy spending, etc.
(Cron-style scheduling can call the same helpers via HTTP /api/alerts later.)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from . import classifier, crud, models


def compute_dashboard_alerts(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    transaction_date_min: date | None,
    transaction_date_max: date,
    receipt_period_start: date,
    receipt_period_end: date,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    fl = [
        models.Statement.bank_account_id == account_id,
        models.Transaction.date <= transaction_date_max,
    ]
    if transaction_date_min is not None:
        fl.append(models.Transaction.date >= transaction_date_min)

    cash_total = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            *fl,
            models.Transaction.is_cash_withdrawal.is_(True),
        )
        .all()
    )
    cash_sum = float(sum(abs(t.amount) for t in cash_total))

    receipt_total = crud.get_receipt_total_for_user(
        db, user_id, receipt_period_start, receipt_period_end
    )
    if cash_sum > 0:
        pct = min(100.0, (receipt_total / cash_sum) * 100.0) if cash_sum else 100.0
        if pct < 80:
            alerts.append(
                {
                    "level": "warning",
                    "code": "low_receipt_coverage",
                    "title": "Cash receipts",
                    "message": f"Only {pct:.0f}% of cash withdrawals are covered by receipts this period. Upload receipts to improve tracking.",
                }
            )

    expenses = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            *fl,
            models.Transaction.direction == "EXPENSE",
        )
        .all()
    )
    if expenses:
        other_total = 0.0
        total_abs = 0.0
        for t in expenses:
            a = abs(t.amount)
            total_abs += a
            if (classifier.get_category_label(t.description_raw, t.amount) or "Other") == "Other":
                other_total += a
        if total_abs > 0 and other_total / total_abs >= 0.25:
            alerts.append(
                {
                    "level": "info",
                    "code": "unclassified_spending",
                    "title": "Unclassified spending",
                    "message": f"About {(other_total / total_abs * 100):.0f}% of expenses are in “Other”. Add keywords or train the ML classifier for better categories.",
                }
            )

    return alerts
