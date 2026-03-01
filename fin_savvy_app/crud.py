from datetime import date, datetime, timedelta
from secrets import token_urlsafe
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import auth, models, schemas


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


def create_bank_account(db: Session, data: schemas.BankAccountCreate) -> models.BankAccount:
    account = models.BankAccount(
        name=data.name,
        institution=data.institution,
        account_type=data.account_type,
        currency=data.currency,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


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


def list_bank_accounts(db: Session) -> list[models.BankAccount]:
    return db.query(models.BankAccount).order_by(models.BankAccount.id).all()


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

