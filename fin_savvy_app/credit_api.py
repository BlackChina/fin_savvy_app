"""
Credit bureau integration (stub).

When you have a provider (Experian, TransUnion, etc.):
1. Set CREDIT_API_KEY (and any base URL env vars your provider needs).
2. Implement fetch_score(user_id) returning dict with score + optional history.
3. Call it from main.api_credit_score or a dedicated route.

Do not commit real API secrets; use environment variables only.
"""

from __future__ import annotations

import os
from typing import Any


def fetch_credit_stub(user_id: int) -> dict[str, Any]:
    """Placeholder until a real bureau client is implemented."""
    demo = (os.environ.get("FINSAVVY_CREDIT_SCORE_NORMALIZED") or "").strip()
    if demo:
        try:
            v = max(0.0, min(100.0, float(demo)))
            return {
                "score": v,
                "history": [],
                "message": "Demo credit signal from FINSAVVY_CREDIT_SCORE_NORMALIZED (0–100). Replace with bureau data in production.",
            }
        except ValueError:
            pass
    return {
        "score": None,
        "history": [],
        "message": "Implement fetch_credit_stub in credit_api.py with your provider SDK.",
    }
