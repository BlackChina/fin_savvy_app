"""Browse past months: budget provenance (how limits were set) + FinSavvy score + adherence."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from . import crud, finsavvy_score, models


ORIGIN_LABELS: dict[str, str] = {
    "recommended": "App recommended",
    "recommended_custom": "Hybrid (app + your edits)",
    "manual_only": "Custom only",
    "declined": "Declined app suggestion",
    "unknown": "Not recorded",
}


def _label(origin: str | None) -> str:
    if not origin:
        return ORIGIN_LABELS["unknown"]
    return ORIGIN_LABELS.get(origin, origin.replace("_", " ").title())


def format_provenance_summary(acc_origin: str | None, global_origin: str | None) -> tuple[str, str]:
    """
    Returns (short_title, detail) for one month.
    scope_key acc:{id} vs global may both exist.
    """
    if acc_origin and global_origin:
        if acc_origin == global_origin:
            return _label(acc_origin), ""
        return (
            "Mixed scopes",
            f"This account: {_label(acc_origin)} · All accounts: {_label(global_origin)}",
        )
    if acc_origin:
        return _label(acc_origin), "Limits scoped to this account."
    if global_origin:
        return _label(global_origin), "Limits saved as “all accounts”."
    return ORIGIN_LABELS["unknown"], "No provenance stored yet (older data or budgets added before tracking)."


def list_history_months(db: Session, user_id: int, account_id: int, limit: int = 30) -> list[str]:
    yms: set[str] = set()
    for r in (
        db.query(models.MonthlyBudget.year_month)
        .filter(models.MonthlyBudget.user_id == user_id)
        .distinct()
        .all()
    ):
        if r[0]:
            yms.add(str(r[0]))
    for r in (
        db.query(models.BudgetMonthProvenance.year_month)
        .filter(models.BudgetMonthProvenance.user_id == user_id)
        .distinct()
        .all()
    ):
        if r[0]:
            yms.add(str(r[0]))
    for y, m in crud.get_available_months(db, account_id):
        yms.add(f"{y}-{m:02d}")
    return sorted(yms, reverse=True)[:limit]


def build_budget_history_rows(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    months: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sk_acc = f"acc:{account_id}"
    for ym in months:
        acc_o = crud.get_budget_provenance(db, user_id, ym, sk_acc)
        gl_o = crud.get_budget_provenance(db, user_id, ym, "global")
        short, detail = format_provenance_summary(acc_o, gl_o)
        fs = finsavvy_score.compute_month_score_payload(db, user_id=user_id, account_id=account_id, year_month=ym)
        score = fs["finsavvy_score"] if fs else None
        grade = fs["grade"] if fs else "—"
        adherence = fs["components"]["budget_adherence"] if fs and fs.get("has_budgets") else None
        lifestyle = fs["components"]["lifestyle"] if fs else None
        receipt = fs["components"]["receipt_coverage"] if fs else None
        has_budgets = bool(fs and fs.get("has_budgets"))
        rows.append(
            {
                "period": ym,
                "origin_short": short,
                "origin_detail": detail,
                "acc_origin": acc_o,
                "global_origin": gl_o,
                "finsavvy_score": score,
                "grade": grade,
                "adherence": adherence,
                "lifestyle": lifestyle,
                "receipt": receipt,
                "has_budgets": has_budgets,
                "view_url": f"/budgets?account_id={account_id}&period={ym}",
            }
        )
    return rows
