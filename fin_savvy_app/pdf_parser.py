from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, List, Optional

import pdfplumber

from .models import Transaction


DATE_LINE_RE = re.compile(r"^(\d{2} [A-Za-z]{3} \d{2})\s+(.*)$")
AMOUNT_BALANCE_RE = re.compile(r"(-?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s+(-?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)$")


@dataclass
class ParsedTransaction:
    date: date
    description: str
    amount: float
    balance_after: Optional[float]


def _parse_date_str(s: str) -> date:
    # Example: "02 Dec 25" -> assume 20xx
    dt = datetime.strptime(s, "%d %b %y")
    return dt.date()


def _parse_amount(s: str) -> float:
    return float(s.replace(",", ""))


def parse_standard_bank_statement(path: str) -> List[ParsedTransaction]:
    rows: List[ParsedTransaction] = []

    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for raw_line in page.extract_text().splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                m_date = DATE_LINE_RE.match(line)
                if not m_date:
                    continue

                date_str, rest = m_date.groups()
                tx_date = _parse_date_str(date_str)

                # We expect description on this line; amount/balance often on same or next line
                desc = rest.strip()
                amount: Optional[float] = None
                balance: Optional[float] = None

                # First, try to find amount/balance in the same line
                m_amount = AMOUNT_BALANCE_RE.search(line)
                if m_amount:
                    amount = _parse_amount(m_amount.group(1))
                    balance = _parse_amount(m_amount.group(2))

                if amount is None or balance is None:
                    # For this simple version we skip lines where we can't confidently parse
                    continue

                rows.append(
                    ParsedTransaction(
                        date=tx_date,
                        description=desc,
                        amount=amount,
                        balance_after=balance,
                    )
                )

    return rows


def to_transaction_models(parsed: Iterable[ParsedTransaction]) -> List[Transaction]:
    txs: List[Transaction] = []
    for p in parsed:
        direction = "INCOME" if p.amount > 0 else "EXPENSE"
        txs.append(
            Transaction(
                date=p.date,
                description_raw=p.description,
                amount=p.amount,
                balance_after=p.balance_after,
                direction=direction,
                is_cash_withdrawal="AUTOBANK CASH WITHDRAWAL" in p.description.upper(),
            )
        )
    return txs

