"""
Keyword-based transaction categorisation and party resolution.
Driven by your documents: Transaction column → Transaction C (category) and Party Name.
Categories and parties below are taken from your document only.
First match wins; order matters.
"""

# From your document: Transaction C column = category for each transaction.
# Keywords below match the Transaction column (description) so we assign the right category.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Salary", ("SALARY",)),
    ("Revolving Loan", ("NPDMMTHI",)),
    ("Returned Debit Order", ("RETURNED DEBIT", "RDO", "REJECTED", "RETURNED")),
    ("Study Loan", ("STUDENT LOAN", "STUDY LOAN")),
]
# From your document: Party Name column = who the transaction is from/to.
# Keywords match the Transaction column so we group by the party you specified.
PARTY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("BCX - Emeron", ("SALARY", "BCX", "EMERON")),
    ("Sanelli Loans", ("NPDMMTHI", "SANELLI", "STUDENT LOAN", "STUDY LOAN")),
    ("Zone Fitness", ("ZONEFITNES", "ZONE FITNESS")),
]

# Used only for legacy summary cards (Generosity / Discretionary); empty = no matches.
_GENEROSITY_KEYWORDS: set[str] = set()
_DISCRETIONARY_KEYWORDS: set[str] = set()


def get_party_name(description: str) -> str:
    """Returns the first matching party name from PARTY_KEYWORDS, or the description itself."""
    d = (description or "").upper()
    for party_name, keywords in PARTY_KEYWORDS:
        if any(kw in d for kw in keywords):
            return party_name
    return (description or "").strip() or "Other"


def is_generosity(description: str) -> bool:
    d = (description or "").upper()
    return any(kw in d for kw in _GENEROSITY_KEYWORDS)


def is_discretionary(description: str) -> bool:
    d = (description or "").upper()
    return any(kw in d for kw in _DISCRETIONARY_KEYWORDS)


def get_category_label(description: str) -> str | None:
    """Returns the first matching category name, or None (then treat as 'Other')."""
    d = (description or "").upper()
    for name, keywords in CATEGORY_KEYWORDS:
        if any(kw in d for kw in keywords):
            return name
    return None


def get_all_category_names() -> list[str]:
    """Returns all category names in order (for Spending by category and defaults)."""
    return [name for name, _ in CATEGORY_KEYWORDS]
