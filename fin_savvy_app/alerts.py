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
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    cash_total = crud.get_cash_withdrawal_total_for_account(
        db, account_id, period_start, period_end
    )
    receipt_total = crud.get_receipt_total_for_user(db, user_id, period_start, period_end)
    if cash_total > 0:
        pct = min(100.0, (receipt_total / cash_total) * 100.0) if cash_total else 100.0
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
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
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

    if not expenses and cash_total == 0:
        pass

    return alerts
