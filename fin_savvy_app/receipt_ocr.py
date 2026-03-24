"""
Optional OCR for receipt images (requires Pillow + pytesseract + system tesseract-ocr).
If dependencies are missing, functions return None and the UI falls back to manual entry.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple


class OcrGuess(NamedTuple):
    amount: float | None
    text_snippet: str


def ocr_receipt_image(path: str | Path) -> OcrGuess | None:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None

    path = Path(path)
    if not path.is_file():
        return None
    try:
        img = Image.open(path)
        text = pytesseract.image_to_string(img) or ""
    except Exception:
        return None

    if not text.strip():
        return OcrGuess(amount=None, text_snippet="")

    # Heuristic: find currency-like numbers (e.g. 123.45 or 1 234,56)
    amounts: list[float] = []
    for m in re.finditer(r"R?\s*([\d\s]{1,10}[.,]\d{2})\b", text, re.IGNORECASE):
        raw = m.group(1).replace(" ", "").replace(",", ".")
        try:
            amounts.append(float(raw))
        except ValueError:
            continue
    for m in re.finditer(r"\bTOTAL\b.*?(R?\s*)?([\d]{1,6}[.,]\d{2})", text, re.IGNORECASE | re.DOTALL):
        raw = m.group(2).replace(",", ".")
        try:
            amounts.append(float(raw))
        except ValueError:
            continue

    best = max(amounts) if amounts else None
    snippet = " ".join(text.split())[:500]
    return OcrGuess(amount=best, text_snippet=snippet)
