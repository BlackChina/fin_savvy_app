"""
Parse bank statement CSV exports into Transaction models.
Supports flexible column names (Standard Bank–style and generic exports).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO

from .models import Transaction

# Normalize header -> canonical field
_HEADER_ALIASES: dict[str, str] = {
    "date": "date",
    "transaction date": "date",
    "tran date": "date",
    "posting date": "date",
    "value date": "date",
    "description": "description",
    "details": "description",
    "detail": "description",
    "narration": "description",
    "reference": "description",
    "transaction description": "description",
    "amount": "amount",
    "debit amount": "debit",
    "credit amount": "credit",
    "debit": "debit",
    "credit": "credit",
    "money in": "credit",
    "money out": "debit",
    "balance": "balance",
    "running balance": "balance",
    "available balance": "balance",
}


@dataclass
class _RowParse:
    date: date
    description: str
    amount: float
    balance: float | None


def _norm_header(h: str) -> str:
    return " ".join((h or "").strip().lower().split())


def _map_headers(headers: list[str]) -> dict[str, str]:
    """Map original column names to canonical keys (date, description, amount, balance, debit, credit)."""
    out: dict[str, str] = {}
    for h in headers:
        key = _norm_header(h)
        canon = _HEADER_ALIASES.get(key)
        if canon:
            out[h] = canon
    return out


_DATE_PATTERNS = [
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
    ("%d/%m/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("%d-%m-%Y", re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")),
    ("%Y/%m/%d", re.compile(r"^\d{4}/\d{2}/\d{2}$")),
]


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt, pat in _DATE_PATTERNS:
        if pat.match(s):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_amount(s: str | None) -> float | None:
    if s is None:
        return None
    s = (s or "").strip()
    if not s or s.lower() in ("-", ""):
        return None
    # Remove currency symbols and spaces; handle thousands separators
    cleaned = re.sub(r"[^\d.,\-+]", "", s.replace(" ", ""))
    if not cleaned or cleaned in ("-", "+"):
        return None
    # If comma is decimal sep (EU style)
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _row_to_parse(row: dict[str, str], col_map: dict[str, str]) -> _RowParse | None:
    by_canon: dict[str, str] = {}
    for orig, canon in col_map.items():
        if orig in row and row[orig] is not None:
            by_canon[canon] = str(row[orig]).strip()

    d = _parse_date(by_canon.get("date"))
    if d is None:
        return None
    desc = by_canon.get("description") or "?"

    amt: float | None = None
    if "amount" in by_canon:
        amt = _parse_amount(by_canon.get("amount"))
    if amt is None:
        debit = _parse_amount(by_canon.get("debit"))
        credit = _parse_amount(by_canon.get("credit"))
        if debit and debit != 0:
            amt = -abs(debit)
        elif credit and credit != 0:
            amt = abs(credit)
    if amt is None:
        return None

    bal = _parse_amount(by_canon.get("balance"))
    return _RowParse(date=d, description=desc, amount=amt, balance=bal)


def parse_bank_csv(content: str) -> list[Transaction]:
    """
    Parse CSV text into Transaction ORM instances (statement_id not set).
    """
    text = content.strip()
    if not text:
        return []
    # Strip BOM
    if text.startswith("\ufeff"):
        text = text[1:]

    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        return []

    col_map = _map_headers(list(reader.fieldnames))
    if "date" not in col_map.values():
        return []
    # Need either amount or debit/credit mapping
    values_set = set(col_map.values())
    if "amount" not in values_set and not (("debit" in values_set or "credit" in values_set)):
        return []

    txs: list[Transaction] = []
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        parsed = _row_to_parse({k: (v or "") for k, v in row.items()}, col_map)
        if parsed is None:
            continue
        direction = "INCOME" if parsed.amount > 0 else "EXPENSE"
        desc_upper = parsed.description.upper()
        is_cash = any(
            kw in desc_upper
            for kw in (
                "AUTOBANK CASH WITHDRAWAL",
                "CASH WITHDRAWAL",
                "ATM WITHDRAWAL",
                "CASH @",
            )
        )
        txs.append(
            Transaction(
                date=parsed.date,
                description_raw=parsed.description,
                amount=parsed.amount,
                balance_after=parsed.balance,
                direction=direction,
                is_cash_withdrawal=is_cash,
            )
        )
    return txs


def parse_bank_csv_bytes(data: bytes) -> list[Transaction]:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return parse_bank_csv(data.decode(enc))
        except UnicodeDecodeError:
            continue
    return []
