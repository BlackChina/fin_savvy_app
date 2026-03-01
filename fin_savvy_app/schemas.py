from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class BankAccountBase(BaseModel):
    name: str
    institution: str
    account_type: Optional[str] = None
    currency: str = "ZAR"


class BankAccountCreate(BankAccountBase):
    pass


class BankAccountRead(BankAccountBase):
    id: int

    class Config:
        from_attributes = True


class StatementRead(BaseModel):
    id: int
    bank_account_id: int
    period_start: date
    period_end: date

    class Config:
        from_attributes = True


class TransactionRead(BaseModel):
    id: int
    statement_id: int
    date: date
    description_raw: str
    amount: float
    balance_after: Optional[float] = None
    direction: str
    is_cash_withdrawal: bool
    category_id: Optional[int] = None

    class Config:
        from_attributes = True


class ReceiptCreate(BaseModel):
    date: date
    amount: float
    description: Optional[str] = None


class ReceiptRead(BaseModel):
    id: int
    user_id: int
    date: date
    amount: float
    description: Optional[str] = None
    file_path: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

