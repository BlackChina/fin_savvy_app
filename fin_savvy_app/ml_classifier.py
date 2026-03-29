"""
Optional ML-based transaction classification (category + party).

Modes (env FINSAVVY_CLASSIFIER):
  keyword  - use only keyword rules in classifier.py (default)
  local    - use models trained from your CSV (train_classifier.py); no API
  openai   - use OpenAI API (requires OPENAI_API_KEY); for later use

Provisions for API: when FINSAVVY_CLASSIFIER=openai and OPENAI_API_KEY is set,
the OpenAI path is used. Local mode requires no API key.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_APP_DIR = Path(__file__).resolve().parent
_DATA_DIR = _APP_DIR / "data"

_CLASSIFIER_MODE = os.environ.get("FINSAVVY_CLASSIFIER", "keyword").strip().lower()
if _CLASSIFIER_MODE not in ("keyword", "local", "openai"):
    _CLASSIFIER_MODE = "keyword"

_API_KEY: str | None = os.environ.get("OPENAI_API_KEY", "").strip() or None

# In-memory cache (for both local and openai)
_ML_CACHE: dict[str, tuple[str | None, str | None]] = {}
_ML_CACHE_MAX = 2000

# Lazy-loaded local models
_local_category_pipe = None
_local_party_pipe = None


def _get_local_models() -> tuple[object | None, object | None]:
    """Load and cache local joblib models."""
    global _local_category_pipe, _local_party_pipe
    if _local_category_pipe is not None and _local_party_pipe is not None:
        return _local_category_pipe, _local_party_pipe
    if _CLASSIFIER_MODE != "local":
        return (None, None)
    cat_path = _DATA_DIR / "local_category_model.joblib"
    party_path = _DATA_DIR / "local_party_model.joblib"
    if not cat_path.exists() or not party_path.exists():
        return (None, None)
    try:
        import joblib
        _local_category_pipe = joblib.load(cat_path)
        _local_party_pipe = joblib.load(party_path)
        return _local_category_pipe, _local_party_pipe
    except Exception:
        return (None, None)


def is_ml_enabled() -> bool:
    """True if ML classification is active (local or openai mode)."""
    if _CLASSIFIER_MODE == "keyword":
        return False
    if _CLASSIFIER_MODE == "local":
        cat_pipe, party_pipe = _get_local_models()
        return cat_pipe is not None and party_pipe is not None
    if _CLASSIFIER_MODE == "openai":
        return bool(_API_KEY)
    return False


def classify_with_ml(
    description: str,
    amount: float | None,
    category_choices: list[str],
) -> tuple[str | None, str | None]:
    """
    Classify transaction: (category, party). Uses local or openai based on FINSAVVY_CLASSIFIER.
    Returns (None, None) to fall back to keyword classifier on failure or when ML not enabled.
    """
    if not description:
        return (None, None)

    cache_key = f"{description}|{amount}"
    if cache_key in _ML_CACHE:
        return _ML_CACHE[cache_key]

    if _CLASSIFIER_MODE == "local":
        result = _classify_local(description, category_choices)
    elif _CLASSIFIER_MODE == "openai" and _API_KEY:
        result = _classify_openai(description, amount, category_choices)
    else:
        return (None, None)

    if len(_ML_CACHE) < _ML_CACHE_MAX:
        _ML_CACHE[cache_key] = result
    return result


def _pipeline_category_classes(cat_pipe: object):
    if hasattr(cat_pipe, "classes_"):
        return getattr(cat_pipe, "classes_", None)
    named = getattr(cat_pipe, "named_steps", None)
    if named:
        clf = named.get("clf")
        if clf is not None and hasattr(clf, "classes_"):
            return clf.classes_
    return None


def _classify_local(description: str, category_choices: list[str]) -> tuple[str | None, str | None]:
    """Predict category and party using trained sklearn models."""
    cat_pipe, party_pipe = _get_local_models()
    if cat_pipe is None or party_pipe is None:
        return (None, None)
    try:
        min_p_raw = os.environ.get("FINSAVVY_ML_MIN_PROBABILITY", "0.35").strip()
        min_p = float(min_p_raw) if min_p_raw else 0.35

        cat: str | None
        if min_p <= 0:
            cat = str(cat_pipe.predict([description])[0])
        elif hasattr(cat_pipe, "predict_proba"):
            classes = _pipeline_category_classes(cat_pipe)
            probs = cat_pipe.predict_proba([description])[0]
            if classes is not None and len(probs) == len(classes):
                best_i = int(probs.argmax())
                best_prob = float(probs[best_i])
                cat = str(classes[best_i]) if best_prob >= min_p else None
            else:
                cat = str(cat_pipe.predict([description])[0])
        else:
            cat = str(cat_pipe.predict([description])[0])

        if cat is not None and cat not in category_choices and "Other" in category_choices:
            cat = "Other"

        # If category is uncertain, do not trust the party head either (same collapse issue).
        party: str | None = None
        if cat is not None:
            party = str(party_pipe.predict([description])[0])
        return (cat, party)
    except Exception:
        return (None, None)


def _classify_openai(
    description: str,
    amount: float | None,
    category_choices: list[str],
) -> tuple[str | None, str | None]:
    """Classify using OpenAI API (for later use)."""
    if not _API_KEY:
        return (None, None)
    try:
        import openai
    except ImportError:
        return (None, None)

    client = openai.OpenAI(api_key=_API_KEY)
    categories_str = ", ".join(category_choices)
    amount_str = f" Amount: {amount:.2f}" if amount is not None else ""

    prompt = f"""Classify this bank transaction for personal finance.

Transaction description: "{description}"{amount_str}

Reply with exactly two lines:
Line 1: CATEGORY: <one of: {categories_str}, or Other>
Line 2: PARTY: <short payee/merchant name, e.g. "Spar", "Netflix", "Vodacom">

Use the exact category from the list. For PARTY use a concise name (2-4 words max)."""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        category = None
        party = None
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("CATEGORY:"):
                category = line.split(":", 1)[1].strip()
                if category not in category_choices:
                    category = "Other" if "Other" in category_choices else (category_choices[0] if category_choices else "Other")
            elif line.upper().startswith("PARTY:"):
                party = line.split(":", 1)[1].strip()
                party = re.sub(r"\s+", " ", party)[:80]
        return (category, party)
    except Exception:
        return (None, None)
