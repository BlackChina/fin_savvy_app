"""
FinSavvy Score (0–100): budget adherence, lifestyle spend share vs your own baseline, and cash receipt coverage.

Transparency-first: sub-scores and plain-language factors are returned for the UI.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from . import budget_503020, classifier, crud, models


LIFESTYLE_CATEGORIES = frozenset({"Dining", "Entertainment", "Alcohol & nightlife"})


def _parse_period(year_month: str) -> tuple[int, int] | None:
    parts = year_month.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        y, m = int(parts[0]), int(parts[1])
        if m < 1 or m > 12:
            return None
        return y, m
    except ValueError:
        return None


def _month_bounds(y: int, m: int) -> tuple[date, date]:
    start = date(y, m, 1)
    end = date(y, m, monthrange(y, m)[1])
    return start, end


def _aggregate_expense_by_category(
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
    out: dict[str, float] = {}
    for t in rows:
        cat = classifier.get_category_label(t.description_raw, t.amount) or "Other"
        out[cat] = out.get(cat, 0.0) + abs(float(t.amount))
    return out


def _budget_compliance_rows_from_db(
    db: Session,
    user_id: int,
    account_id: int,
    year_month: str,
) -> list[tuple[float, str]]:
    """(limit, bucket) per budget row, using stored budget_bucket when set (sums all lines, not merged by name)."""

    def _row_tuple(b: models.MonthlyBudget) -> tuple[float, str]:
        lim = float(b.amount_limit)
        raw_bb = getattr(b, "budget_bucket", None)
        bb = raw_bb.strip().lower() if isinstance(raw_bb, str) and raw_bb.strip() else None
        bfk = bb if bb in ("needs", "wants", "savings") else None
        if not bfk:
            bfk = budget_503020.budget_bucket_for_category(b.category_name)
        return lim, bfk

    rows = crud.list_budgets_for_user(db, user_id, year_month, bank_account_id=account_id)
    acc_keys: set[tuple[str, str | None]] = set()
    out: list[tuple[float, str]] = []
    for b in rows:
        if b.bank_account_id == account_id:
            acc_keys.add((b.category_name, b.other_detail))
            if float(b.amount_limit) > 0:
                out.append(_row_tuple(b))
    for b in rows:
        if b.bank_account_id is None and (b.category_name, b.other_detail) not in acc_keys:
            if float(b.amount_limit) > 0:
                out.append(_row_tuple(b))
    return out


def _budget_map_for_account(
    db: Session,
    user_id: int,
    account_id: int,
    year_month: str,
) -> dict[str, float]:
    """Effective limit per category (account row beats 'all accounts' row)."""
    rows = crud.list_budgets_for_user(db, user_id, year_month, bank_account_id=account_id)
    limits: dict[str, float] = {}
    for b in rows:
        if b.bank_account_id == account_id:
            limits[b.category_name] = float(b.amount_limit)
    for b in rows:
        if b.bank_account_id is None and b.category_name not in limits:
            limits[b.category_name] = float(b.amount_limit)
    return limits


def _lifestyle_share_for_month(db: Session, account_id: int, y: int, m: int) -> float | None:
    start, end = _month_bounds(y, m)
    by_cat = _aggregate_expense_by_category(db, account_id, start, end)
    total = sum(by_cat.values())
    if total <= 0:
        return None
    life = sum(by_cat.get(c, 0.0) for c in LIFESTYLE_CATEGORIES)
    return life / total


def _prior_lifestyle_shares(db: Session, account_id: int, y: int, m: int, count: int) -> list[float]:
    shares: list[float] = []
    cm, cy = m, y
    for _ in range(count):
        cm -= 1
        if cm < 1:
            cm = 12
            cy -= 1
        sh = _lifestyle_share_for_month(db, account_id, cy, cm)
        if sh is not None:
            shares.append(sh)
    return shares


def _budget_adherence_score(
    limits: dict[str, float], spent_by_cat: dict[str, float]
) -> tuple[float | None, list[dict[str, Any]]]:
    """
    Per budgeted category: utilization = spent/limit.
    Category score: 100 if util<=1; else linear drop to 0 at util>=2.
    Overall: average of category scores (only categories with limit > 0).
    """
    rows_detail: list[dict[str, Any]] = []
    scores: list[float] = []
    for cat, lim in sorted(limits.items()):
        if lim <= 0:
            continue
        spent = spent_by_cat.get(cat, 0.0)
        u = spent / lim
        if u <= 1.0:
            s = 100.0
            status = "within"
        elif u >= 2.0:
            s = 0.0
            status = "over"
        else:
            s = max(0.0, 100.0 * (2.0 - u))
            status = "over"
        scores.append(s)
        rows_detail.append(
            {
                "category": cat,
                "limit": lim,
                "spent": spent,
                "utilization_pct": round(100.0 * u, 1),
                "remaining": lim - spent,
                "status": status,
                "category_score": round(s, 1),
            }
        )
    if not scores:
        return None, rows_detail
    return sum(scores) / len(scores), rows_detail


def _lifestyle_score(
    db: Session,
    account_id: int,
    y: int,
    m: int,
    current_share: float | None,
) -> tuple[float, dict[str, Any]]:
    meta: dict[str, Any] = {"current_share_pct": None, "baseline_share_pct": None, "note": ""}
    if current_share is None:
        return 75.0, {**meta, "note": "No expenses this month — neutral lifestyle component."}
    meta["current_share_pct"] = round(current_share * 100.0, 1)
    prior = _prior_lifestyle_shares(db, account_id, y, m, 6)
    if len(prior) >= 2:
        base = float(median(prior))
        meta["baseline_share_pct"] = round(base * 100.0, 1)
        # Improvement when current lifestyle share is below your typical median.
        delta = base - current_share
        raw = 70.0 + 30.0 * (delta / max(base, 0.08))
        score = max(0.0, min(100.0, raw))
        if delta >= 0.02:
            meta["note"] = "Lifestyle spend (dining, entertainment, alcohol & nightlife) is below your recent typical share — good for gradual improvement."
        elif delta <= -0.02:
            meta["note"] = "Lifestyle spend is above your recent typical share — a common place to look for leakage."
        else:
            meta["note"] = "Lifestyle share is close to your recent norm."
        return score, meta
    # Not enough history: score from absolute share only (softer).
    meta["note"] = "Not enough prior months to personalise; scored from this month’s lifestyle share only."
    score = max(0.0, min(100.0, 100.0 - 120.0 * current_share))
    return score, meta


def _receipt_score(cash: float, receipts: float) -> tuple[float, dict[str, Any]]:
    meta: dict[str, Any] = {"cash_withdrawn": cash, "receipts_total": receipts, "coverage_pct": None}
    if cash <= 0:
        meta["note"] = (
            "No ATM-style cash withdrawals recorded this month — this pillar scores neutrally until you "
            "withdraw cash and upload receipts to show coverage."
        )
        return 76.0, meta
    ratio = receipts / cash
    meta["coverage_pct"] = round(min(100.0, ratio * 100.0), 1)
    # 85%+ receipt value vs cash = full score; scale linearly below.
    s = min(100.0, (ratio / 0.85) * 100.0) if ratio < 0.85 else 100.0
    if ratio >= 0.85:
        meta["note"] = "Receipts cover at least 85% of recorded cash withdrawals — strong accountability."
    elif ratio >= 0.5:
        meta["note"] = "Part of your cash withdrawals are covered by receipts; upload more to improve this pillar."
    else:
        meta["note"] = "A large share of cash withdrawals is not yet matched by receipt amounts — work on coverage here."
    return s, meta


def _grade(total: float) -> str:
    if total >= 90:
        return "A"
    if total >= 80:
        return "B"
    if total >= 70:
        return "C"
    if total >= 60:
        return "D"
    return "E"


def compute_month_score_payload(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    year_month: str,
) -> dict[str, Any] | None:
    parsed = _parse_period(year_month)
    if not parsed:
        return None
    y, m = parsed
    start, end = _month_bounds(y, m)
    spent = _aggregate_expense_by_category(db, account_id, start, end)
    limits = _budget_map_for_account(db, user_id, account_id, year_month)
    total_expense = sum(spent.values())

    lifestyle_spend = sum(spent.get(c, 0.0) for c in LIFESTYLE_CATEGORIES)
    current_lifestyle_share = (lifestyle_spend / total_expense) if total_expense > 0 else None

    adherence, adherence_rows = _budget_adherence_score(limits, spent)
    has_budgets = adherence is not None
    lifestyle, lifestyle_meta = _lifestyle_score(db, account_id, y, m, current_lifestyle_share)

    cash = crud.get_cash_withdrawal_total_for_account(db, account_id, start, end)
    receipts = crud.get_receipt_total_for_user(db, user_id, start, end)
    receipt, receipt_meta = _receipt_score(cash, receipts)

    if has_budgets:
        w_b, w_l, w_r = 0.45, 0.30, 0.25
        composite = w_b * float(adherence) + w_l * lifestyle + w_r * receipt
    else:
        w_b, w_l, w_r = 0.0, 0.55, 0.45
        composite = w_l * lifestyle + w_r * receipt

    total = max(0.0, min(100.0, composite))
    scope_key = f"acc:{account_id}"
    commitment = crud.get_budget_commitment(db, user_id, year_month, scope_key)
    income_est: float | None = None
    inc_payload = budget_503020.build_default_month_budget(db, account_id, year_month)
    if inc_payload:
        income_est = float(inc_payload["income_estimate"])

    if commitment and has_budgets:
        if commitment.mode == "system":
            total = min(100.0, total + 1.0)
        elif commitment.mode in ("scratch", "legacy", "customized") and income_est and income_est > 0:
            comp_rows = _budget_compliance_rows_from_db(db, user_id, account_id, year_month)
            pen = budget_503020.compliance_penalty_from_limit_bucket_rows(comp_rows, income_est)
            total = max(0.0, total - pen)
        st = commitment.system_recommended_total
        ct = commitment.committed_total
        if (
            commitment.mode in ("scratch", "legacy")
            and st is not None
            and ct is not None
            and float(st) > 0
        ):
            dev = abs(float(ct) - float(st)) / float(st)
            if dev > 0.25:
                total = max(0.0, total - min(8.0, (dev - 0.25) * 25.0))
        if (
            commitment.mode == "customized"
            and income_est is not None
            and st is not None
            and ct is not None
            and float(st) > 0
            and has_budgets
        ):
            incf = float(income_est)
            stf = float(st)
            ctf = float(ct)
            base_rem = incf - stf
            new_rem = incf - ctf
            if new_rem > base_rem + 0.5:
                gain = new_rem - base_rem
                if base_rem >= 10.0:
                    pct_improve = gain / base_rem
                    bonus = min(4.0, pct_improve * 10.0)
                else:
                    bonus = min(4.0, (gain / stf) * 35.0)
                total = min(100.0, total + bonus)
        if (
            st is not None
            and ct is not None
            and float(st) > 0
            and float(ct) <= 0.85 * float(st)
            and has_budgets
        ):
            total = min(100.0, total + 2.0)

    total = round(max(0.0, min(100.0, total)), 1)

    transparency: list[dict[str, str]] = []
    if has_budgets:
        transparency.append(
            {
                "title": f"Budget discipline ({int(round(w_b * 100))}% of your score)",
                "body": "For each category where you set a limit this month, we compare actual spend to that limit. "
                "Staying at or under 100% of the limit scores 100 for that line; going to 200% of the limit scores 0. "
                "We average those line scores.",
            }
        )
    else:
        transparency.append(
            {
                "title": "Budget discipline",
                "body": "You have not set any positive limits for this month yet, so this pillar is skipped and the other two are weighted more.",
            }
        )
    transparency.append(
        {
            "title": f"Lifestyle & leakage ({int(round(w_l * 100))}% of your score)",
            "body": "We sum Dining, Entertainment, and Alcohol & nightlife as a share of all spending. "
            "When enough history exists, we compare that share to your median share over the six months before — "
            "improvement (lower share than your norm) lifts the score. Coffee and similar treats are mostly captured under Dining keywords.",
        }
    )
    transparency.append(
        {
            "title": f"Cash & receipts ({int(round(w_r * 100))}% of your score)",
            "body": "ATM-style withdrawals flagged on statements are compared to receipt amounts you uploaded for the same calendar month. "
            "Strong scores when receipts cover at least 85% of withdrawals; lower coverage reduces this part. "
            "If there were no cash withdrawals this month, this pillar stays in a neutral band until you have cash activity to measure.",
        }
    )

    improvements: list[str] = []
    if has_budgets and float(adherence) < 75:
        improvements.append("Tighten a few category limits or reduce spend in the rows marked over budget.")
    if lifestyle < 75:
        improvements.append("Review dining, entertainment, and alcohol & nightlife — small cuts here compound.")
    if receipt < 80 and cash > 0:
        improvements.append("Upload receipts for cash spending so unaccounted withdrawals stop dragging the score.")
    if not improvements:
        improvements.append("Keep tracking month to month; small consistent wins show up in this score over time.")

    if commitment and has_budgets:
        transparency.append(
            {
                "title": "Budget commitment mode",
                "body": "Your score can reflect how you set this month’s limits: following the accepted 50/30/20 table "
                "gives a small lift; fully custom budgets are checked against the same rule-of-thumb split of your "
                "estimated income, and very different totals versus the app’s suggested envelope can reduce the headline score slightly.",
            }
        )
        if commitment.mode == "customized":
            transparency.append(
                {
                    "title": "Carry-over room (customized plan)",
                    "body": "When you customize the suggested envelope, we compare estimated income minus your committed "
                    "limits to the same figure for the original suggestion. If you leave more unallocated income "
                    "(month-to-month breathing room, separate from the savings lines in your plan), that earns a small "
                    "FinSavvy boost, capped so the headline score stays fair.",
                }
            )

    return {
        "year_month": year_month,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "total_expense": total_expense,
        "lifestyle_spend": lifestyle_spend,
        "lifestyle_share_pct": round((current_lifestyle_share or 0) * 100, 1) if current_lifestyle_share is not None else None,
        "budget_rows": adherence_rows,
        "has_budgets": has_budgets,
        "weights": {"budget": w_b, "lifestyle": w_l, "receipt": w_r},
        "components": {
            "budget_adherence": round(adherence, 1) if has_budgets else None,
            "lifestyle": round(lifestyle, 1),
            "receipt_coverage": round(receipt, 1),
        },
        "finsavvy_score": total,
        "grade": _grade(total),
        "lifestyle_meta": lifestyle_meta,
        "receipt_meta": receipt_meta,
        "transparency": transparency,
        "improvements": improvements,
        "budget_commitment": (
            {
                "mode": commitment.mode,
                "system_recommended_total": commitment.system_recommended_total,
                "committed_total": commitment.committed_total,
            }
            if commitment
            else None
        ),
    }
