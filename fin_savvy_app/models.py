from datetime import date, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(80), nullable=False, unique=True)
    email = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    bank_accounts = relationship("BankAccount", back_populates="user")
    receipts = relationship("Receipt", back_populates="user")
    payslips = relationship("Payslip", back_populates="user")
    monthly_budgets = relationship("MonthlyBudget", back_populates="user")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    institution = Column(String(100), nullable=False)
    account_type = Column(String(50), nullable=True)
    currency = Column(String(10), nullable=False, default="ZAR")

    user = relationship("User", back_populates="bank_accounts")
    statements = relationship("Statement", back_populates="bank_account")


class Statement(Base):
    __tablename__ = "statements"

    id = Column(Integer, primary_key=True, index=True)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    source_file_name = Column(String(255), nullable=True)

    bank_account = relationship("BankAccount", back_populates="statements")
    transactions = relationship("Transaction", back_populates="statement")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    type = Column(String(20), nullable=False)  # INCOME/EXPENSE/TRANSFER

    transactions = relationship("Transaction", back_populates="category")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    statement_id = Column(Integer, ForeignKey("statements.id"), nullable=False)
    date = Column(Date, nullable=False, default=date.today)
    description_raw = Column(Text, nullable=False)
    amount = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=True)
    direction = Column(String(20), nullable=False)  # INCOME/EXPENSE/TRANSFER
    is_cash_withdrawal = Column(Boolean, nullable=False, default=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)

    statement = relationship("Statement", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")
    linked_receipts = relationship("Receipt", back_populates="linked_transaction")


class MonthlyBudget(Base):
    """Per-user monthly spend limit for a classifier category (optional per bank account)."""

    __tablename__ = "monthly_budgets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=True)
    category_name = Column(String(100), nullable=False)
    year_month = Column(String(7), nullable=False)  # YYYY-MM
    amount_limit = Column(Float, nullable=False)
    other_detail = Column(String(120), nullable=True)  # when category_name is Other, user label (e.g. school fees)

    user = relationship("User", back_populates="monthly_budgets")
    bank_account = relationship("BankAccount")


class BudgetMonthCommitment(Base):
    """User has finalized a budget for this month/scope (system, customized, or scratch)."""

    __tablename__ = "budget_month_commitment"
    __table_args__ = (UniqueConstraint("user_id", "year_month", "scope_key", name="uq_budget_month_commitment"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    year_month = Column(String(7), nullable=False)
    scope_key = Column(String(32), nullable=False)
    mode = Column(String(24), nullable=False, default="unknown")  # system | customized | scratch
    system_recommended_total = Column(Float, nullable=True)
    committed_total = Column(Float, nullable=True)
    committed_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BudgetMonthProvenance(Base):
    """How a month's budget lines were chosen: app recommendation, hybrid, custom, or declined suggestion."""

    __tablename__ = "budget_month_provenance"
    __table_args__ = (UniqueConstraint("user_id", "year_month", "scope_key", name="uq_budget_month_provenance"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    year_month = Column(String(7), nullable=False)  # YYYY-MM
    scope_key = Column(String(32), nullable=False)  # "acc:12" for this-account limits, "global" for all-accounts
    origin = Column(String(32), nullable=False, default="unknown")
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Receipt(Base):
    """Scanned receipt or invoice; used for cash-spend tracking and tax."""
    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String(255), nullable=True)
    file_path = Column(String(512), nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="receipts")
    linked_transaction = relationship(
        "Transaction",
        back_populates="linked_receipts",
        foreign_keys=[transaction_id],
    )


class Payslip(Base):
    """Uploaded payslip for tax records."""
    __tablename__ = "payslips"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    file_path = Column(String(512), nullable=False)
    period_label = Column(String(100), nullable=True)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="payslips")

