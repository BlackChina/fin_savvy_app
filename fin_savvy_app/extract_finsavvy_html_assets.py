#!/usr/bin/env python3
"""Extract data:image base64 PNGs from static/finsavvy_background.html into static/*.png.

Run from repo root or fin_savvy_app:
  python3 extract_finsavvy_html_assets.py

Writes finsavvy_top_brand.png and finsavvy_bottom_brand.png (first img = top, second = bottom).
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
HTML = BASE / "static" / "finsavvy_background.html"
OUT_TOP = BASE / "static" / "finsavvy_top_brand.png"
OUT_BOT = BASE / "static" / "finsavvy_bottom_brand.png"


def main() -> int:
    if not HTML.is_file():
        print("Missing", HTML, file=sys.stderr)
        return 1
    text = HTML.read_text(encoding="utf-8", errors="replace")
    pat = re.compile(
        r'data:image/(png|jpeg|jpg);base64,([A-Za-z0-9+/=]+)',
        re.I,
    )
    matches = pat.findall(text)
    if len(matches) < 2:
        print("Expected at least 2 data:image base64 blobs in HTML, found", len(matches), file=sys.stderr)
        return 1
    for i, (_, b64) in enumerate(matches[:2]):
        raw = base64.b64decode(b64)
        out = OUT_TOP if i == 0 else OUT_BOT
        out.write_bytes(raw)
        print("Wrote", out, len(raw), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
