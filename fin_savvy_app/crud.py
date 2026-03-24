from datetime import date, datetime, timedelta
from secrets import token_urlsafe
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import auth, classifier, models, schemas


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
    """Returns list of (year, month) tuples for months that have transactions, newest first."""
    year_col = func.extract("year", models.Transaction.date)
    month_col = func.extract("month", models.Transaction.date)
    rows = (
        db.query(year_col.label("y"), month_col.label("m"))
        .join(models.Statement)
        .filter(models.Statement.bank_account_id == account_id)
        .distinct()
        .order_by(year_col.desc(), month_col.desc())
        .all()
    )
    return [(int(r.y), int(r.m)) for r in rows]


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


def upsert_monthly_budget(
    db: Session,
    *,
    user_id: int,
    category_name: str,
    year_month: str,
    amount_limit: float,
    bank_account_id: int | None = None,
) -> models.MonthlyBudget:
    q = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == user_id,
        models.MonthlyBudget.category_name == category_name,
        models.MonthlyBudget.year_month == year_month,
    )
    if bank_account_id is None:
        q = q.filter(models.MonthlyBudget.bank_account_id.is_(None))
    else:
        q = q.filter(models.MonthlyBudget.bank_account_id == bank_account_id)
    row = q.first()
    if row:
        row.amount_limit = float(amount_limit)
    else:
        row = models.MonthlyBudget(
            user_id=user_id,
            bank_account_id=bank_account_id,
            category_name=category_name,
            year_month=year_month,
            amount_limit=float(amount_limit),
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


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

