"""
Best-effort payslip field extraction from PDF text (no OCR for scanned-only PDFs).

Used after upload to populate optional gross/net/PAYE columns for tax records.
Heuristics are conservative: values are only set when patterns match clearly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _parse_money_token(s: str) -> float | None:
    s = s.strip().replace(" ", "").replace("R", "").replace("r", "")
    if not s:
        return None
    s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _amount_on_line_with_label(text: str, labels: tuple[str, ...]) -> float | None:
    """First currency-like amount on a line that contains any of the labels (case-insensitive)."""
    for line in text.splitlines():
        lu = line.upper()
        if not any(lab.upper() in lu for lab in labels):
            continue
        for m in re.finditer(r"([\d][\d\s,]*[.,]\d{2})\b", line):
            raw = m.group(1).replace(" ", "")
            val = _parse_money_token(raw.replace(",", "."))
            if val is not None and val > 0:
                return val
    return None


def extract_payslip_fields_from_pdf(path: str | Path) -> dict[str, Any]:
    """
    Returns dict with keys gross_pay, net_pay, paye_estimate (each float or None).
    """
    path = Path(path)
    out: dict[str, Any] = {"gross_pay": None, "net_pay": None, "paye_estimate": None}
    if not path.is_file() or path.suffix.lower() != ".pdf":
        return out
    try:
        import pdfplumber
    except ImportError:
        return out
    try:
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return out
    if not text.strip():
        return out

    gross = _amount_on_line_with_label(
        text,
        (
            "GROSS SALARY",
            "GROSS PAY",
            "GROSS REMUNERATION",
            "TAXABLE INCOME",
            "TOTAL REMUNERATION",
        ),
    )
    net = _amount_on_line_with_label(
        text,
        (
            "NET PAY",
            "NETT PAY",
            "NET SALARY",
            "TAKE HOME",
            "TAKE-HOME",
            "AMOUNT PAYABLE",
            "NET REMUNERATION",
        ),
    )
    paye = _amount_on_line_with_label(
        text,
        (
            "PAYE",
            "PAY AS YOU EARN",
            "PAY-AS-YOU-EARN",
            "SITE",
            "EMPLOYEES TAX",
            "EMPLOYEE'S TAX",
        ),
    )
    out["gross_pay"] = gross
    out["net_pay"] = net
    out["paye_estimate"] = paye
    return out
