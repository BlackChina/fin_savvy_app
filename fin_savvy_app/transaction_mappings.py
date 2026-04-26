"""
User-maintained transaction mappings.

How this works:
1) The classifier checks these mappings first (top to bottom).
2) If no mapping matches, it falls back to built-in keyword rules.
3) If still no match, ML may assist depending on environment settings.

Tips:
- Put the most specific patterns first.
- Use uppercase substrings as they appear in your bank description.
- Keep patterns reasonably unique to avoid accidental matches.
"""

from __future__ import annotations

# Category mappings: (CategoryName, ("KEYWORD 1", "KEYWORD 2", ...))
# Example:
# ("Groceries", ("CHECKERS SIXTY60", "WOOLWORTHS FOODS")),
MANUAL_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # Add your own category mappings here.
]

# Party mappings: (PartyName, ("KEYWORD 1", "KEYWORD 2", ...))
# Example:
# ("Checkers", ("CHECKERS SIXTY60", "CHECKERS HYPER")),
MANUAL_PARTY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # Add your own party mappings here.
]
