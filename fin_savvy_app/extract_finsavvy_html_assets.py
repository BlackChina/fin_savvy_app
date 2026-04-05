#!/usr/bin/env python3
"""Extract data:image base64 blobs from static/finsavvy_background.html into poster PNGs.

Login/register use finsavvy_top_brand.png and finsavvy_bottom_brand.png. The app
refreshes them from the HTML on startup; you can also run:

  python3 extract_finsavvy_html_assets.py
"""
from __future__ import annotations

import base64
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent


def sync_poster_pngs_from_background_html(static_dir: str | Path) -> bool:
    """Write finsavvy_top_brand.png and finsavvy_bottom_brand.png from finsavvy_background.html.

    Returns True if both files were written; False if HTML missing or fewer than 2 images.
    """
    static = Path(static_dir)
    html_path = static / "finsavvy_background.html"
    out_top = static / "finsavvy_top_brand.png"
    out_bot = static / "finsavvy_bottom_brand.png"

    if not html_path.is_file():
        logger.warning("Missing %s — poster PNGs not refreshed", html_path)
        return False

    text = html_path.read_text(encoding="utf-8", errors="replace")
    pat = re.compile(
        r"data:image/(png|jpeg|jpg);base64,([\sA-Za-z0-9+/=]+)",
        re.I,
    )
    matches = pat.findall(text)
    if len(matches) < 2:
        logger.warning(
            "finsavvy_background.html must contain at least 2 data:image base64 blobs; found %s",
            len(matches),
        )
        return False

    for i, (_, b64) in enumerate(matches[:2]):
        raw = base64.b64decode("".join(b64.split()))
        out = out_top if i == 0 else out_bot
        out.write_bytes(raw)

    logger.info(
        "Poster PNGs synced from finsavvy_background.html (%s bytes, %s bytes)",
        out_top.stat().st_size,
        out_bot.stat().st_size,
    )
    return True


def main() -> int:
    static = BASE / "static"
    if not sync_poster_pngs_from_background_html(static):
        print("Sync failed — need static/finsavvy_background.html with 2+ embedded images.", file=sys.stderr)
        return 1
    top = static / "finsavvy_top_brand.png"
    bot = static / "finsavvy_bottom_brand.png"
    print("Wrote", top, top.stat().st_size, "bytes")
    print("Wrote", bot, bot.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
