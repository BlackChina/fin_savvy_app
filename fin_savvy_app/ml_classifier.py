"""
Optional ML-based transaction classification (category + party).

Modes (env FINSAVVY_CLASSIFIER):
  keyword  - use only keyword rules in classifier.py (default)
  local    - use models trained from your CSV (train_classifier.py); no API
  openai   - use OpenAI API (requires OPENAI_API_KEY); for later use

Optional (local ML): FINSAVVY_ML_MIN_PROBABILITY — if set (e.g. 0.35), drop predictions whose
top-class probability is below that threshold (otherwise we always use argmax to reduce “Other”).

When FINSAVVY_CLASSIFIER=keyword (default), local joblib models are still used after keywords fail
if both model files exist — set FINSAVVY_ML_AFTER_KEYWORD=0 to disable (keywords only).

Provisions for API: when FINSAVVY_CLASSIFIER=openai and OPENAI_API_KEY is set,
the OpenAI path is used. Local mode requires no API key.
"""

from __future__ import annotations

import os
import re
from difflib import get_close_matches
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

# Strip common SA / card-scheme noise so keyword rules and TF–IDF see merchant text.
_BANK_NOISE_PREFIX = re.compile(
    r"^(?:"
    r"POS\s+PURCHASE\s*|POS\s*|PURCHASE\s*|"
    r"CONTACTLESS\s*|TAP\s+TO\s+PAY\s*|"
    r"ONLINE\s+PURCHASE\s*|ONLINE\s+PAYMENT\s*|"
    r"DEBIT\s+ORDER\s*|D/O\s+TO\s*|D/O\s*|"
    r"DEBIT\s+|CREDIT\s+|"
    r"EFT\s+|EFT\s+PAYMENT\s+|"
    r"INSTANT\s+PAY\s+|IMMEDIATE\s+PAY\s+|"
    r"REQUEST\s+TO\s+PAY\s+|RTP\s+"
    r")+",
    re.IGNORECASE,
)
_MASKED_PAN = re.compile(r"\*+\d{2,4}\*+|\b\d{4}\s+\d{4}\s+\d{4}\s+\d{4}\b", re.IGNORECASE)
_LEADING_IDS = re.compile(r"^(?:\d+\s+)+")
# Scheme / channel noise before merchant name (SA statements)
_CARD_SCHEME_PREFIX = re.compile(
    r"^(?:"
    r"MASTERCARD|VISA|DEBIT\s+CARD|CREDIT\s+CARD|CHEQUE\s+CARD|"
    r"TRACK\s*2|CHIP|SWIPE|"
    r"SECURE\s+3D|3D\s+SECURE|"
    r"APPLE\s+PAY|GOOGLE\s+PAY|SAMSUNG\s+PAY"
    r")\s+",
    re.IGNORECASE,
)
# Trailing bank reference tokens (keep merchant to the left)
_TRAILING_REF = re.compile(
    r"\s+(?:REF\.?\s*[:#]?\s*[A-Z0-9\-]{4,}|"
    r"AUTH\.?\s*[:#]?\s*\d+|"
    r"RRN\s*[:#]?\s*\d+|"
    r"TERMINAL\s*[:#]?\s*\w+)$",
    re.IGNORECASE,
)


def normalize_bank_description(description: str) -> str:
    """Uppercase description with POS/EFT prefixes and masked card numbers removed."""
    s = (description or "").strip()
    if not s:
        return ""
    s = _MASKED_PAN.sub(" ", s)
    s = _BANK_NOISE_PREFIX.sub("", s, count=1)
    s = _CARD_SCHEME_PREFIX.sub("", s, count=1)
    s = _LEADING_IDS.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _TRAILING_REF.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


def canonical_category_label(raw: str | None, category_choices: list[str]) -> str | None:
    """Map model output (spacing/casing) onto the app's category list."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    alias = {
        "food & dining": "Dining",
        "food and dining": "Dining",
        "restaurants": "Dining",
        "eating out": "Dining",
        "groceries / food": "Groceries",
        "grocery": "Groceries",
        "supermarket": "Groceries",
        "petrol": "Fuel",
        "gas": "Fuel",
        "cell phone": "Telecommunications",
        "mobile phone": "Telecommunications",
        "internet & tv": "Utilities",
        "subscriptions": "Entertainment",
        "streaming": "Entertainment",
        "healthcare": "Health",
        "pharmacy": "Health",
        "atm fees": "Bank Fees",
        "banking fees": "Bank Fees",
        "transfer": "Bank Fees",
        "misc": "Other",
        "miscellaneous": "Other",
    }
    s_lo = s.lower()
    if s_lo in alias:
        s = alias[s_lo]
    if s in category_choices:
        return s
    lowered = {c.lower(): c for c in category_choices}
    if s.lower() in lowered:
        return lowered[s.lower()]
    close = get_close_matches(s, category_choices, n=1, cutoff=0.82)
    if close:
        return close[0]
    close_lo = get_close_matches(s.lower(), [c.lower() for c in category_choices], n=1, cutoff=0.88)
    if close_lo:
        for c in category_choices:
            if c.lower() == close_lo[0]:
                return c
    return None

# Lazy-loaded local models
_local_category_pipe = None
_local_party_pipe = None


def _local_model_paths() -> tuple[Path, Path]:
    return _DATA_DIR / "local_category_model.joblib", _DATA_DIR / "local_party_model.joblib"


def local_model_files_exist() -> bool:
    cat_path, party_path = _local_model_paths()
    return cat_path.is_file() and party_path.is_file()


def _allow_keyword_local_fallback() -> bool:
    """Use trained local models after keyword rules when mode is keyword (default on)."""
    if _CLASSIFIER_MODE != "keyword":
        return False
    v = os.environ.get("FINSAVVY_ML_AFTER_KEYWORD", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _should_load_local_sklearn() -> bool:
    return _CLASSIFIER_MODE == "local" or (_allow_keyword_local_fallback() and local_model_files_exist())


def _get_local_models() -> tuple[object | None, object | None]:
    """Load and cache local joblib models."""
    global _local_category_pipe, _local_party_pipe
    if _local_category_pipe is not None and _local_party_pipe is not None:
        return _local_category_pipe, _local_party_pipe
    if not _should_load_local_sklearn():
        return (None, None)
    cat_path, party_path = _local_model_paths()
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
    """True if ML can assist category/party (local, openai, or keyword+on-disk models)."""
    if _CLASSIFIER_MODE == "openai":
        return bool(_API_KEY)
    if _CLASSIFIER_MODE == "local":
        cat_pipe, party_pipe = _get_local_models()
        return cat_pipe is not None and party_pipe is not None
    if _allow_keyword_local_fallback() and local_model_files_exist():
        return True
    return False


def spending_breakdown_caption() -> str:
    """Short label for dashboard “Spending by category” (keyword vs ML-assisted)."""
    if _CLASSIFIER_MODE == "openai" and _API_KEY:
        return "OpenAI-assisted"
    if _CLASSIFIER_MODE == "local" and local_model_files_exist():
        return "Local ML"
    if _allow_keyword_local_fallback() and local_model_files_exist():
        return "Keywords + local ML"
    return "Keyword rules"


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

    if _CLASSIFIER_MODE == "openai":
        if not _API_KEY:
            return (None, None)
        result = _classify_openai(description, amount, category_choices)
    elif _CLASSIFIER_MODE == "local" or _allow_keyword_local_fallback():
        cat_pipe, party_pipe = _get_local_models()
        if cat_pipe is None or party_pipe is None:
            return (None, None)
        result = _classify_local(description, category_choices)
        norm = normalize_bank_description(description)
        raw_upper = (description or "").strip().upper()
        if norm and norm != raw_upper and (result[0] is None or result[0] == "Other"):
            alt = _classify_local(norm, category_choices)
            if alt[0] and alt[0] != "Other":
                result = alt
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
        # If unset, always use argmax (avoids flooding “Other” when max prob is modest).
        min_p_raw = os.environ.get("FINSAVVY_ML_MIN_PROBABILITY", "").strip()
        min_p: float | None = float(min_p_raw) if min_p_raw else None

        cat: str | None
        if hasattr(cat_pipe, "predict_proba"):
            classes = _pipeline_category_classes(cat_pipe)
            probs = cat_pipe.predict_proba([description])[0]
            if classes is not None and len(probs) == len(classes):
                best_i = int(probs.argmax())
                best_prob = float(probs[best_i])
                cat = str(classes[best_i])
                if min_p is not None and best_prob < min_p:
                    cat = None
            else:
                cat = str(cat_pipe.predict([description])[0])
        else:
            cat = str(cat_pipe.predict([description])[0])

        cat = canonical_category_label(cat, category_choices)
        if cat is not None and cat not in category_choices and "Other" in category_choices:
            cat = "Other"

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
                category = canonical_category_label(category, category_choices) or category
                if category not in category_choices:
                    category = "Other" if "Other" in category_choices else (category_choices[0] if category_choices else "Other")
            elif line.upper().startswith("PARTY:"):
                party = line.split(":", 1)[1].strip()
                party = re.sub(r"\s+", " ", party)[:80]
        return (category, party)
    except Exception:
        return (None, None)
