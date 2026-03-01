"""Simple rule-based classifier for transaction descriptions."""

GENEROSITY_KEYWORDS = (
    "GIVENGAIN",
    "TITHE",
    "CHURCH",
    "OFFERING",
    "DONATION",
    "CHARITY",
    "NPO",
)

DISCRETIONARY_KEYWORDS = (
    "LIQUORSHOP",
    "BOOTLEGGER",
    "BOSSASOMERSET",
    "BOSSA ",
    "UBER",
    "PANAROTTIS",
    "FERRYMANS",
    "TIGERS MILK",
    "THE COPPER CL",
    "MUGGANDBEANCH",
    "CHEVERERESTAU",
    "GRABABITE",
    "CORNERPOCKETL",
    "WIMPY",
    "MCD ",
    "EMPACT",
    "BLUE GECKO",
)


def is_generosity(description: str) -> bool:
    d = (description or "").upper()
    return any(kw in d for kw in GENEROSITY_KEYWORDS)


def is_discretionary(description: str) -> bool:
    d = (description or "").upper()
    return any(kw in d for kw in DISCRETIONARY_KEYWORDS)


def get_category_label(description: str) -> str | None:
    """Returns 'Generosity', 'Discretionary', or None."""
    if is_generosity(description):
        return "Generosity"
    if is_discretionary(description):
        return "Discretionary"
    return None
