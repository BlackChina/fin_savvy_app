import os
from datetime import date, datetime, timedelta
from secrets import token_urlsafe
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import auth, classifier, models, schemas


def dashboard_dedupe_enabled() -> bool:
    """When true, dashboard sums and lists count each duplicate import once (same date, amount, description, direction)."""
    return os.environ.get("FINSAVVY_DASHBOARD_DEDUPE", "1").strip().lower() not in ("0", "false", "no")


def dashboard_transaction_dedup_subquery(
    db: Session,
    account_id: int,
    transaction_date_min: date | None,
    transaction_date_max: date,
) -> Any | None:
    """
    Subquery with column kid = transaction id to keep per fingerprint (min id = first upload).
    Scoped to transactions whose date is in [min, max] (same as dashboard totals).
    """
    if not dashboard_dedupe_enabled():
        return None
    desc_norm = func.upper(func.trim(func.coalesce(models.Transaction.description_raw, "")))
    fl = [
        models.Statement.bank_account_id == account_id,
        models.Transaction.date <= transaction_date_max,
    ]
    if transaction_date_min is not None:
        fl.append(models.Transaction.date >= transaction_date_min)
    return (
        db.query(func.min(models.Transaction.id).label("kid"))
        .join(models.Statement)
        .filter(*fl)
        .group_by(
            models.Transaction.date,
            models.Transaction.amount,
            desc_norm,
            models.Transaction.direction,
        )
        .subquery()
    )


def get_user_by_username(db: Session, username: str) -> models.User | None:
    return db.query(models.User).filter(models.User.username == username).first()


def get_user_by_email(db: Session, email: str) -> models.User | None:
    return db.query(models.User).filter(models.User.email == email).first()


def create_user(db: Session, username: str, email: str, password: str) -> models.User:
    user = models.User(
        username=username,
        email=email,
        password_hash=auth.hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_password_reset_token(db: Session, user_id: int) -> str:
    token = token_urlsafe(48)
    record = models.PasswordResetToken(
        user_id=user_id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add(record)
    db.commit()
    return token


def get_user_by_reset_token(db: Session, token: str) -> models.User | None:
    record = (
        db.query(models.PasswordResetToken)
        .filter(
            models.PasswordResetToken.token == token,
            models.PasswordResetToken.used_at.is_(None),
            models.PasswordResetToken.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if not record:
        return None
    return db.query(models.User).filter(models.User.id == record.user_id).first()


def use_reset_token(db: Session, token: str) -> bool:
    record = db.query(models.PasswordResetToken).filter(models.PasswordResetToken.token == token).first()
    if not record:
        return False
    record.used_at = datetime.utcnow()
    db.commit()
    return True


def update_user_password(db: Session, user_id: int, new_password: str) -> None:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user:
        user.password_hash = auth.hash_password(new_password)
        db.commit()


def create_bank_account(db: Session, user_id: int, data: schemas.BankAccountCreate) -> models.BankAccount:
    account = models.BankAccount(
        user_id=user_id,
        name=data.name,
        institution=data.institution,
        account_type=data.account_type,
        currency=data.currency,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def get_bank_account_for_user(db: Session, account_id: int, user_id: int) -> models.BankAccount | None:
    return (
        db.query(models.BankAccount)
        .filter(models.BankAccount.id == account_id, models.BankAccount.user_id == user_id)
        .first()
    )


def create_statement_with_transactions(
    db: Session,
    *,
    bank_account_id: int,
    period_start: date,
    period_end: date,
    source_file_name: str | None,
    transactions: Iterable[models.Transaction],
) -> models.Statement:
    statement = models.Statement(
        bank_account_id=bank_account_id,
        period_start=period_start,
        period_end=period_end,
        source_file_name=source_file_name,
    )
    db.add(statement)
    db.flush()

    for t in transactions:
        t.statement_id = statement.id
        db.add(t)

    db.commit()
    db.refresh(statement)
    return statement


def get_transactions_for_statement(
    db: Session,
    statement_id: int,
) -> list[models.Transaction]:
    return (
        db.query(models.Transaction)
        .filter(models.Transaction.statement_id == statement_id)
        .order_by(models.Transaction.date, models.Transaction.id)
        .all()
    )


def list_bank_accounts(db: Session, user_id: int) -> list[models.BankAccount]:
    return (
        db.query(models.BankAccount)
        .filter(models.BankAccount.user_id == user_id)
        .order_by(models.BankAccount.id)
        .all()
    )


def create_receipt(
    db: Session,
    user_id: int,
    date: date,
    amount: float,
    description: str | None = None,
    file_path: str | None = None,
) -> models.Receipt:
    r = models.Receipt(
        user_id=user_id,
        date=date,
        amount=amount,
        description=description,
        file_path=file_path,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def list_receipts_for_user(
    db: Session,
    user_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[models.Receipt]:
    q = db.query(models.Receipt).filter(models.Receipt.user_id == user_id)
    if period_start is not None:
        q = q.filter(models.Receipt.date >= period_start)
    if period_end is not None:
        q = q.filter(models.Receipt.date <= period_end)
    return q.order_by(models.Receipt.date.desc()).all()


def get_cash_withdrawal_total_for_user(
    db: Session,
    user_id: int,
    period_start: date,
    period_end: date,
) -> float:
    total = (
        db.query(func.coalesce(func.sum(func.abs(models.Transaction.amount)), 0.0))
        .join(models.Statement)
        .join(models.BankAccount)
        .filter(
            models.BankAccount.user_id == user_id,
            models.Transaction.is_cash_withdrawal.is_(True),
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
        )
        .scalar()
    )
    return float(total or 0.0)


def get_cash_withdrawal_total_for_account(
    db: Session,
    account_id: int,
    period_start: date,
    period_end: date,
) -> float:
    """Cash withdrawals for one bank account in a date range."""
    total = (
        db.query(func.coalesce(func.sum(func.abs(models.Transaction.amount)), 0.0))
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.is_cash_withdrawal.is_(True),
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
        )
        .scalar()
    )
    return float(total or 0.0)


def get_receipt_total_for_user(
    db: Session,
    user_id: int,
    period_start: date,
    period_end: date,
) -> float:
    total = (
        db.query(func.coalesce(func.sum(models.Receipt.amount), 0.0))
        .filter(
            models.Receipt.user_id == user_id,
            models.Receipt.date >= period_start,
            models.Receipt.date <= period_end,
        )
        .scalar()
    )
    return float(total or 0.0)


def create_payslip(
    db: Session,
    user_id: int,
    file_path: str,
    period_label: str | None = None,
) -> models.Payslip:
    p = models.Payslip(
        user_id=user_id,
        file_path=file_path,
        period_label=period_label,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def list_payslips_for_user(db: Session, user_id: int) -> list[models.Payslip]:
    return (
        db.query(models.Payslip)
        .filter(models.Payslip.user_id == user_id)
        .order_by(models.Payslip.uploaded_at.desc())
        .all()
    )


def get_party_totals(
    db: Session,
    account_id: int,
    direction: str,
    sort_by: str = "total",
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[tuple[str, float, int, date]]:
    """Returns list of (description_raw, total_amount, count, last_date) for the account.
    If period_start and period_end are given, filters to that period only.
    sort_by: 'total' (by cumulative amount) or 'recent' (by last date).
    """
    q = (
        db.query(
            models.Transaction.description_raw,
            func.sum(func.abs(models.Transaction.amount)).label("total"),
            func.count(models.Transaction.id).label("cnt"),
            func.max(models.Transaction.date).label("last_date"),
        )
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.direction == direction,
        )
    )
    if period_start is not None:
        q = q.filter(models.Transaction.date >= period_start)
    if period_end is not None:
        q = q.filter(models.Transaction.date <= period_end)
    q = q.group_by(models.Transaction.description_raw)
    if sort_by == "recent":
        q = q.order_by(func.max(models.Transaction.date).desc())
    else:
        q = q.order_by(func.sum(func.abs(models.Transaction.amount)).desc())
    rows = q.limit(50).all()
    return [(r.description_raw, float(r.total), int(r.cnt), r.last_date) for r in rows]


def get_party_totals_by_party(
    db: Session,
    account_id: int,
    direction: str,
    sort_by: str = "total",
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[tuple[str, float, int, date]]:
    """Like get_party_totals but groups by resolved party name (from description keywords).
    Returns list of (party_name, total_amount, count, last_date).
    """
    q = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.direction == direction,
        )
    )
    if period_start is not None:
        q = q.filter(models.Transaction.date >= period_start)
    if period_end is not None:
        q = q.filter(models.Transaction.date <= period_end)
    rows = q.all()
    # Group by party name in Python
    agg: dict[str, tuple[float, int, date]] = {}
    for t in rows:
        party = classifier.get_party_name(t.description_raw)
        amt = abs(t.amount)
        if party not in agg:
            agg[party] = (0.0, 0, t.date)
        total, cnt, last = agg[party]
        agg[party] = (total + amt, cnt + 1, max(last, t.date) if last else t.date)
    out = [(party, total, cnt, last) for party, (total, cnt, last) in agg.items()]
    if sort_by == "recent":
        out.sort(key=lambda x: x[3] or date.min, reverse=True)
    else:
        out.sort(key=lambda x: x[1], reverse=True)
    return out[:50]


def get_available_months(db: Session, account_id: int) -> list[tuple[int, int]]:
    """Distinct (year, month) from transaction dates and statement period_start (union)."""
    y_tx = func.extract("year", models.Transaction.date)
    m_tx = func.extract("month", models.Transaction.date)
    tx_rows = (
        db.query(y_tx.label("y"), m_tx.label("m"))
        .join(models.Statement)
        .filter(models.Statement.bank_account_id == account_id)
        .distinct()
        .all()
    )
    y_st = func.extract("year", models.Statement.period_start)
    m_st = func.extract("month", models.Statement.period_start)
    st_rows = (
        db.query(y_st.label("y"), m_st.label("m"))
        .filter(models.Statement.bank_account_id == account_id)
        .distinct()
        .all()
    )
    merged = {(int(r.y), int(r.m)) for r in tx_rows} | {(int(r.y), int(r.m)) for r in st_rows}
    return sorted(merged, reverse=True)


def list_distinct_budget_months_for_user(db: Session, user_id: int, limit: int = 36) -> list[str]:
    """YYYY-MM values that have at least one saved budget row (newest first)."""
    rows = (
        db.query(models.MonthlyBudget.year_month)
        .filter(models.MonthlyBudget.user_id == user_id)
        .distinct()
        .all()
    )
    labels = sorted({str(r[0]) for r in rows if r[0]}, reverse=True)
    return labels[:limit]


def list_history_years_for_budget_navigation(db: Session, user_id: int, bank_account_id: int) -> list[int]:
    """
    Years to show in Budget history: any year with saved limits, any year with transactions/statements
    for this account, plus the current calendar year (so you can always return to the open month).
    """
    years = set(list_distinct_budget_years_for_account(db, user_id, bank_account_id))
    for y, _m in get_available_months(db, bank_account_id):
        years.add(int(y))
    years.add(date.today().year)
    return sorted(years, reverse=True)


def list_distinct_budget_years_for_account(db: Session, user_id: int, bank_account_id: int) -> list[int]:
    """Years that have at least one budget row for this user and account (or global rows)."""
    rows = (
        db.query(models.MonthlyBudget.year_month)
        .filter(
            models.MonthlyBudget.user_id == user_id,
            (models.MonthlyBudget.bank_account_id == bank_account_id)
            | (models.MonthlyBudget.bank_account_id.is_(None)),
        )
        .distinct()
        .all()
    )
    years: set[int] = set()
    for (ym,) in rows:
        if ym and len(str(ym)) >= 7:
            try:
                years.add(int(str(ym)[:4]))
            except ValueError:
                pass
    return sorted(years, reverse=True)


def list_budget_months_numeric_for_year(
    db: Session, user_id: int, bank_account_id: int, year: int
) -> list[int]:
    prefix = f"{year}-"
    rows = (
        db.query(models.MonthlyBudget.year_month)
        .filter(
            models.MonthlyBudget.user_id == user_id,
            models.MonthlyBudget.year_month.like(f"{prefix}%"),
            (models.MonthlyBudget.bank_account_id == bank_account_id)
            | (models.MonthlyBudget.bank_account_id.is_(None)),
        )
        .distinct()
        .all()
    )
    months: set[int] = set()
    for (ym,) in rows:
        if not ym or len(str(ym)) < 7:
            continue
        try:
            m = int(str(ym)[5:7])
            if 1 <= m <= 12:
                months.add(m)
        except ValueError:
            pass
    return sorted(months)


def list_learned_category_labels(
    db: Session, user_id: int, account_id: int, *, lookback_days: int = 420, limit: int = 50
) -> list[str]:
    """Distinct classifier labels from recent expenses (learned / recurring patterns)."""
    start = date.today() - timedelta(days=lookback_days)
    rows = (
        db.query(models.Transaction)
        .join(models.Statement)
        .join(models.BankAccount)
        .filter(
            models.BankAccount.user_id == user_id,
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= start,
            models.Transaction.direction == "EXPENSE",
        )
        .limit(8000)
        .all()
    )
    labels: set[str] = set()
    for t in rows:
        lab = classifier.get_category_label(t.description_raw, t.amount)
        if lab and lab.strip():
            labels.add(lab.strip())
    return sorted(labels)[:limit]


def list_budgets_for_user(
    db: Session,
    user_id: int,
    year_month: str,
    bank_account_id: int | None = None,
) -> list[models.MonthlyBudget]:
    q = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == user_id,
        models.MonthlyBudget.year_month == year_month,
    )
    if bank_account_id is not None:
        q = q.filter(
            (models.MonthlyBudget.bank_account_id == bank_account_id)
            | (models.MonthlyBudget.bank_account_id.is_(None))
        )
    return q.order_by(models.MonthlyBudget.category_name).all()


def normalize_budget_bucket(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in ("needs", "wants", "savings"):
        return s
    return None


def upsert_monthly_budget(
    db: Session,
    *,
    user_id: int,
    category_name: str,
    year_month: str,
    amount_limit: float,
    bank_account_id: int | None = None,
    other_detail: str | None = None,
    budget_bucket: str | None = None,
) -> models.MonthlyBudget:
    cat = category_name.strip()
    od: str | None = None
    if cat.lower() == "other":
        od = (other_detail or "").strip()[:120] or None
    bb = normalize_budget_bucket(budget_bucket)
    q = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == user_id,
        models.MonthlyBudget.category_name == cat,
        models.MonthlyBudget.year_month == year_month,
    )
    if bank_account_id is None:
        q = q.filter(models.MonthlyBudget.bank_account_id.is_(None))
    else:
        q = q.filter(models.MonthlyBudget.bank_account_id == bank_account_id)
    if od is not None:
        q = q.filter(models.MonthlyBudget.other_detail == od)
    else:
        q = q.filter(models.MonthlyBudget.other_detail.is_(None))
    row = q.first()
    if row:
        row.amount_limit = float(amount_limit)
        row.other_detail = od
        row.budget_bucket = bb
    else:
        row = models.MonthlyBudget(
            user_id=user_id,
            bank_account_id=bank_account_id,
            category_name=cat,
            year_month=year_month,
            amount_limit=float(amount_limit),
            other_detail=od,
            budget_bucket=bb,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_all_budgets_for_month_scope(
    db: Session, *, user_id: int, year_month: str, bank_account_id: int | None
) -> int:
    """Remove all budget lines for this month and scope (account id or global)."""
    q = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == user_id,
        models.MonthlyBudget.year_month == year_month,
    )
    if bank_account_id is None:
        q = q.filter(models.MonthlyBudget.bank_account_id.is_(None))
    else:
        q = q.filter(models.MonthlyBudget.bank_account_id == bank_account_id)
    n = q.delete(synchronize_session=False)
    db.commit()
    return int(n or 0)


def get_budget_commitment(
    db: Session, user_id: int, year_month: str, scope_key: str
) -> models.BudgetMonthCommitment | None:
    return (
        db.query(models.BudgetMonthCommitment)
        .filter(
            models.BudgetMonthCommitment.user_id == user_id,
            models.BudgetMonthCommitment.year_month == year_month,
            models.BudgetMonthCommitment.scope_key == scope_key,
        )
        .first()
    )


def upsert_budget_commitment(
    db: Session,
    *,
    user_id: int,
    year_month: str,
    scope_key: str,
    mode: str,
    system_recommended_total: float | None,
    committed_total: float | None,
) -> models.BudgetMonthCommitment:
    row = (
        db.query(models.BudgetMonthCommitment)
        .filter(
            models.BudgetMonthCommitment.user_id == user_id,
            models.BudgetMonthCommitment.year_month == year_month,
            models.BudgetMonthCommitment.scope_key == scope_key,
        )
        .first()
    )
    now = datetime.utcnow()
    if row:
        row.mode = mode
        row.system_recommended_total = system_recommended_total
        row.committed_total = committed_total
        row.committed_at = now
    else:
        row = models.BudgetMonthCommitment(
            user_id=user_id,
            year_month=year_month,
            scope_key=scope_key,
            mode=mode,
            system_recommended_total=system_recommended_total,
            committed_total=committed_total,
            committed_at=now,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_legacy_budget_commitment(
    db: Session, *, user_id: int, year_month: str, bank_account_id: int
) -> None:
    """If budget rows exist but no commitment row, record a legacy commitment (one-time migration per month)."""
    scope_key = f"acc:{bank_account_id}"
    if get_budget_commitment(db, user_id, year_month, scope_key):
        return
    rows = list_budgets_for_user(db, user_id, year_month, bank_account_id=bank_account_id)
    if not rows:
        return
    committed_total = sum(float(r.amount_limit) for r in rows)
    sys_tot: float | None = None
    try:
        from . import budget_503020

        payload = budget_503020.build_default_month_budget(db, bank_account_id, year_month)
        if payload:
            sys_tot = float(payload["reference_total"])
    except Exception:
        sys_tot = None
    upsert_budget_commitment(
        db,
        user_id=user_id,
        year_month=year_month,
        scope_key=scope_key,
        mode="legacy",
        system_recommended_total=sys_tot,
        committed_total=committed_total,
    )


def is_month_budget_finalized(
    db: Session, *, user_id: int, year_month: str, bank_account_id: int
) -> bool:
    """True only after explicit commit (or one-time DB backfill for pre-existing rows)."""
    scope_key = f"acc:{bank_account_id}"
    return get_budget_commitment(db, user_id, year_month, scope_key) is not None


def delete_monthly_budget(db: Session, budget_id: int, user_id: int) -> bool:
    row = (
        db.query(models.MonthlyBudget)
        .filter(models.MonthlyBudget.id == budget_id, models.MonthlyBudget.user_id == user_id)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def get_budget_provenance(db: Session, user_id: int, year_month: str, scope_key: str) -> str | None:
    row = (
        db.query(models.BudgetMonthProvenance)
        .filter(
            models.BudgetMonthProvenance.user_id == user_id,
            models.BudgetMonthProvenance.year_month == year_month,
            models.BudgetMonthProvenance.scope_key == scope_key,
        )
        .first()
    )
    return row.origin if row else None


def upsert_budget_provenance(db: Session, user_id: int, year_month: str, scope_key: str, origin: str) -> None:
    row = (
        db.query(models.BudgetMonthProvenance)
        .filter(
            models.BudgetMonthProvenance.user_id == user_id,
            models.BudgetMonthProvenance.year_month == year_month,
            models.BudgetMonthProvenance.scope_key == scope_key,
        )
        .first()
    )
    now = datetime.utcnow()
    if row:
        row.origin = origin
        row.updated_at = now
    else:
        db.add(
            models.BudgetMonthProvenance(
                user_id=user_id,
                year_month=year_month,
                scope_key=scope_key,
                origin=origin,
                updated_at=now,
            )
        )
    db.commit()


def note_manual_budget_change(db: Session, user_id: int, year_month: str, scope_key: str) -> None:
    """Call after user saves a budget line manually (not from accept-all)."""
    row = (
        db.query(models.BudgetMonthProvenance)
        .filter(
            models.BudgetMonthProvenance.user_id == user_id,
            models.BudgetMonthProvenance.year_month == year_month,
            models.BudgetMonthProvenance.scope_key == scope_key,
        )
        .first()
    )
    now = datetime.utcnow()
    if row:
        if row.origin == "recommended":
            row.origin = "recommended_custom"
        elif row.origin in ("declined", "unknown"):
            row.origin = "manual_only"
        row.updated_at = now
        db.commit()
        return
    db.add(
        models.BudgetMonthProvenance(
            user_id=user_id,
            year_month=year_month,
            scope_key=scope_key,
            origin="manual_only",
            updated_at=now,
        )
    )
    db.commit()


def list_transactions_for_linking(
    db: Session,
    user_id: int,
    account_id: int,
    period_start: date,
    period_end: date,
) -> list[models.Transaction]:
    """Expense transactions in period for receipt linking dropdown."""
    return (
        db.query(models.Transaction)
        .join(models.Statement)
        .join(models.BankAccount)
        .filter(
            models.BankAccount.user_id == user_id,
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "EXPENSE",
        )
        .order_by(models.Transaction.date.desc(), models.Transaction.id.desc())
        .limit(500)
        .all()
    )


def set_receipt_transaction_link(
    db: Session,
    receipt_id: int,
    user_id: int,
    transaction_id: int | None,
) -> bool:
    r = (
        db.query(models.Receipt)
        .filter(models.Receipt.id == receipt_id, models.Receipt.user_id == user_id)
        .first()
    )
    if not r:
        return False
    if transaction_id is None:
        r.transaction_id = None
    else:
        t = (
            db.query(models.Transaction)
            .join(models.Statement)
            .join(models.BankAccount)
            .filter(
                models.Transaction.id == transaction_id,
                models.BankAccount.user_id == user_id,
            )
            .first()
        )
        if not t:
            return False
        r.transaction_id = transaction_id
    db.commit()
    return True

