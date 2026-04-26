"""
Transaction categorisation and party resolution.
- FINSAVVY_CLASSIFIER=local: use models trained from your CSV (train_classifier.py).
- FINSAVVY_CLASSIFIER=openai: use OpenAI API (set OPENAI_API_KEY) when you're ready.
- Otherwise: keyword rules (CATEGORY_KEYWORDS, PARTY_KEYWORDS).
First match wins; order matters (more specific keywords first).
"""
from __future__ import annotations

from . import ml_classifier
from .transaction_mappings import MANUAL_CATEGORY_KEYWORDS, MANUAL_PARTY_KEYWORDS

# December 2025: Transaction Category (from your spreadsheet column).
# Keywords match bank Description so we assign the right category for Spending by category.
CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Telecommunications", ("VODACOM", "VOD PREPAID", "VODCOM", "TELKOM", "MTN", "CELL C", "AIRTIME", "DATA", "MOBILE")),
    ("Groceries", (
        "SHOPRITE", "CHECKERS", "PICK N PAY", "PNP", "WOOLWORTHS", "SPAR", "OK FOODS",
        "FOOD LOVER", "FOOD LOVERS", "MAKRO", "GROCER", "HYPER", "SIXTY60", "CHECKERS 60", "BOXER", "USAVE",
        "ULTRA", "METRO ", "SUPERSPAR", "SUPER SPAR", "MEAT WORLD", "FRESHMARK", "WELLNESS WAREHOUSE",
    )),
    ("Fuel", ("ENGEN", "SHELL", "FUEL", "GARAGE", "BP ", "CALTEX", "SASOL")),
    ("Transport", (
        "UBER", "BOLT", "GAUTRAIN", "REA VAYA", "METROBUS", "TAXI", "PUBLIC TRANSPORT",
        "CAR PAYMENT", "CAR MAINTENANCE", "MY CITI", "PUTCO", "GOTRANSIT", "E-TOLL", "ETOLL",
    )),
    ("Rent", ("RENT", "LANDLORD", "MORTGAGE", "RATES", "TAXES")),
    ("Utilities", (
        "ESKOM", "ELECTRICITY", "CITY OF JHB", "CITY OF CT", "CITY OF CPT", "CITY OF TSHWANE",
        "CITY OF EKU", "MUNICIPAL", "PREPAID ELEC", "WATER", "TELKOM", "INTERNET",
        "RATES", "TAXES", "AFRIHOST", "AXXESS", "COOL IDEAS", "WEBAFRICA", "HEROTEL", "VUMATEL",
    )),
    ("Dining", (
        "UBER EATS", "MR D FOOD", "DEBONAIRS", "STEERS", "KFC", "MCDONALD", "BURGER KING",
        "NANDO", "WIMPY", "ROCOMAMAS", "SPUR", "OCEAN BASKET", "PANAROTTI", "NEWS CAFE",
        "TIGER'S MILK", "HUSSAR", "MONTANA", "RESTAURANT", "TAKEAWAY", "DINING", "PIZZA",
        "FINE DINING", "STEAKHOUSE", "BISTRO", "EATERY",
        "STARBUCKS", "COFFEE ", "VIDA E CAFFE", "SEATTLE COFFEE", "BOOTLEGGER COFFEE",
        "BOSSA", "BOSSASOMERSET", "BOSSASOMERS",
    )),
    ("Shopping", (
        "TAKEALOT", "EDGARS", "TRUWORTHS", "SPORTSCENE", "TOTALSPORTS", "STREET FEVER",
        "ZARA", "H&M", "COTTON ON", "SUPERBALIST", "ZANDO", "SHEIN", "TEMU", "AMAZON",
        "MAKRO", "GAME", "BUILDERS", "CASHBUILD", "LEROY", "OUTDOOR WAREHOUSE", "CAPE UNION",
        "TRAPPERS", "CLICKS", "DIS-CHEM", "DISCHEM", "CLOTHING", "SHOES", "ELECTRONICS", "FURNITURE",
        "HOME DECOR", "GARDEN", "PET STORE", "LIQUOR", "TOBACCO", "MR PRICE", "PEPKOR",
        "SNAPSCAN", "ZAPPER", "PAYFAST", "NETCASH", "PEACH", "OZOW", "STITCH", "YOCO",
        "PEP ", "PEPSTORE", "ACKERMANS", "RAGE ", "JET ", "VERIMARK", "HI-FI CORP", "INCredible",
        "CNA ", "EXCLUSIVE", "MUSICA", "WESTPACK",
    )),
    ("Entertainment", (
        "NETFLIX", "SPOTIFY", "SHOWMAX", "DSTV", "MULTICHOICE", "CINEMA", "MOVIE",
        "CONCERT", "SPORTING EVENT", "TICKETS", "THEATRE", "THEATER", "LIVE MUSIC",
    )),
    # Bars, clubs, and bottle-forward nights out — surfaced for lifestyle / “leakage” insights.
    ("Alcohol & nightlife", (
        "BAR ", " PUB", "TAVERN", "SHEBEEN", "NIGHTCLUB", "NIGHT CLUB", "BREWERY",
        "BRASSERIE", "COCKTAIL", "WINERY", "HOPS ", " CRAFT ",
    )),
    ("Health", (
        "CLINIC", "HOSPITAL", "PHARMACY", "DIS-CHEM", "CLICKS", "DOCTOR", "DENTIST",
        "OPTOMETRIST", "DISCOVERY", "HEALTH", "MEDICAL", "PRESCRIPTION",
        "NETCARE", "LIFE HEALTH", "MEDI CLINIC", "PATHCARE", "LANCET", "MEDICAL AID",
    )),
    ("Education", (
        "SCHOOL", "UNIVERSITY", "TUITION", "TEXTBOOK", "STATIONERY", "STATIONARY",
    )),
    ("Insurance", (
        "OLD MUTUAL", "MOMENTUM", "LIBERTY", "SANLAM", "INSURANCE", "CAR INSURANCE",
    )),
    # Avoid bare "BANK" — it matches placenames like ROSEBANK and mis-fires before lifestyle categories.
    ("Bank Fees", (
        "CAPITEC", "STANDARD BANK", "ABSA", "FNB", "ATM", "FEE", "DEBIT ORDER",
        "EXCESS INTEREST", "SERVICE FEE", "MONTHLY FEE", "ADMIN FEE",
        "INTERNET BANK", "APP PAYMENT", "POS PURCHASE FEE",
    )),
    ("Savings", ("SAVINGS", "SAVINGS ACCT", "DEPOSIT")),
    ("Investments", ("SANLAM", "INVESTMENT", "INVEST")),
    ("Personal Care", ("GYM", "HAIRDRESSER", "BARBER", "SPA", "SALON")),
    ("Gifts", ("GIFT", "GIFT SHOP")),
    ("Charity", ("CHARITY", "DONATION", "TITHE", "GIVENGAIN")),
    ("Travel", ("FLIGHT", "AIRLINE", "HOTEL", "ACCOMMODATION", "HOLIDAY", "TRAVEL AGENT")),
]

# December 2025: Party Name (from your spreadsheet column).
# Keywords match bank Description so Parties you pay groups by the party you specified.
PARTY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Vodacom", ("VODACOM", "VOD PREPAID", "VODCOM")),
    ("Shoprite Checkers", ("SHOPRITE", "CHECKERS HYPER", "CHECKERS ")),
    ("Pick n Pay", ("PICK N PAY", "PNP ", "PNP HYPER")),
    ("Checkers", ("CHECKERS",)),
    ("Woolworths", ("WOOLWORTHS",)),
    ("Engen", ("ENGEN",)),
    ("Shell", ("SHELL",)),
    ("Capitec Bank", ("CAPITEC",)),
    ("Standard Bank", ("STANDARD BANK",)),
    ("Absa", ("ABSA",)),
    ("FNB", ("FNB",)),
    ("Takealot", ("TAKEALOT",)),
    ("Uber Eats", ("UBER EATS",)),
    ("Mr D Food", ("MR D FOOD", "MR D ",)),
    ("Netflix", ("NETFLIX",)),
    ("Spotify", ("SPOTIFY",)),
    ("Showmax", ("SHOWMAX",)),
    ("DStv", ("DSTV",)),
    ("Eskom", ("ESKOM",)),
    ("City of JHB", ("CITY OF JHB", "CITY OF JOHANNESBURG", "COJ")),
    ("Telkom", ("TELKOM",)),
    ("MultiChoice", ("MULTICHOICE",)),
    ("Old Mutual", ("OLD MUTUAL",)),
    ("Sanlam", ("SANLAM",)),
    ("Discovery", ("DISCOVERY",)),
    ("Momentum", ("MOMENTUM",)),
    ("Liberty", ("LIBERTY",)),
    ("PnP", ("PNP",)),
    ("Clicks", ("CLICKS",)),
    ("Dis-Chem", ("DIS-CHEM", "DISCHEM")),
    ("Edgars", ("EDGARS",)),
    ("Truworths", ("TRUWORTHS",)),
    ("Sportscene", ("SPORTSCENE",)),
    ("Totalsports", ("TOTALSPORTS",)),
    ("Street Fever", ("STREET FEVER",)),
    ("Zara", ("ZARA",)),
    ("H&M", ("H&M", "H & M")),
    ("Cotton On", ("COTTON ON",)),
    ("Superbalist", ("SUPERBALIST",)),
    ("Zando", ("ZANDO",)),
    ("Shein", ("SHEIN",)),
    ("Temu", ("TEMU",)),
    ("Amazon", ("AMAZON",)),
    ("Uber", ("UBER ", "UBER RIDE")),  # after Uber Eats
    ("Bolt", ("BOLT",)),
    ("Gautrain", ("GAUTRAIN",)),
    ("Rea Vaya", ("REA VAYA",)),
    ("Metrobus", ("METROBUS",)),
    ("Taxi", ("TAXI",)),
    ("Spar", ("SPAR",)),
    ("OK Foods", ("OK FOODS",)),
    ("Food Lover's Market", ("FOOD LOVER",)),
    ("Makro", ("MAKRO",)),
    ("Game", ("GAME ", "GAME STORE")),
    ("Builders Warehouse", ("BUILDERS",)),
    ("Cashbuild", ("CASHBUILD",)),
    ("Leroy Merlin", ("LEROY",)),
    ("Outdoor Warehouse", ("OUTDOOR WHS", "OUTDOOR WAREHOUSE")),
    ("Cape Union Mart", ("CAPE UNION",)),
    ("Trappers Trading", ("TRAPPERS",)),
    ("Montana Restaurant", ("MONTANA",)),
    ("Debonairs Pizza", ("DEBONAIRS",)),
    ("Steers", ("STEERS",)),
    ("KFC", ("KFC",)),
    ("McDonald's", ("MCDONALD",)),
    ("Burger King", ("BURGER KING",)),
    ("Nando's", ("NANDO",)),
    ("Wimpy", ("WIMPY",)),
    ("RocoMamas", ("ROCOMAMAS",)),
    ("Spur", ("SPUR",)),
    ("Ocean Basket", ("OCEAN BASKET",)),
    ("Panarottis", ("PANAROTTI",)),
    ("News Cafe", ("NEWS CAFE",)),
    ("Tiger's Milk", ("TIGER'S MILK", "TIGERS MILK")),
    ("Hussar Grill", ("HUSSAR",)),
    # Avoid bare "BOSSA" — substring matches are too broad; local ML already over-predicts common parties.
    ("Bossa", ("BOSSASOMERS", "BOSSASOMERSET", "BOSSA SOMERSET", "BOSSA SOMERSET WEST", "C*BOSSASOMER")),
    ("Gym", ("GYM", "ZONEFITNES", "ZONE FITNESS")),
    ("Hair Salon", ("HAIRDRESSER", "HAIR SALON")),
    ("Barber Shop", ("BARBER",)),
    ("Spa", ("SPA",)),
    ("Landlord", ("RENT", "LANDLORD")),
    ("Bank", ("MORTGAGE", "BANK TRANSFER", "STANDARD BANK", "FNB", "ABSA", "CAPITEC")),
    ("Fuel Station", ("FUEL", "GARAGE")),
    ("Car Insurance Co.", ("CAR INSURANCE",)),
    ("Car Dealership", ("CAR PAYMENT",)),
    ("Airline", ("FLIGHT", "AIRLINE")),
    ("Hotel", ("HOTEL", "ACCOMMODATION")),
    ("Travel Agent", ("HOLIDAY", "TRAVEL AGENT")),
    ("Cinema", ("CINEMA", "MOVIE")),
    ("Concert Venue", ("CONCERT",)),
    ("Sporting Arena", ("SPORTING EVENT",)),
    ("Charity", ("CHARITY", "DONATION", "TITHE", "GIVENGAIN")),
    ("Gift Shop", ("GIFT SHOP",)),
    ("Investment Firm", ("INVESTMENT", "INVEST")),
    ("Savings Acct", ("SAVINGS",)),
]

# Generosity / giving: tithes, offerings, faith-based and registered charity flows (dashboard summary).
_GENEROSITY_KEYWORDS: frozenset[str] = frozenset(
    {
        "TITHE",
        "TITHES",
        "TITHING",
        "OFFERING",
        "OFFERTORY",
        "STEWARDSHIP",
        "GIVENGAIN",
        "GIVEN GAIN",
        "CHARITY",
        "DONATION",
        "ZAKAT",
        "ZAKAAT",
        "SADAQAH",
        "PARISH",
        "CHURCH",
        "SYNAGOGUE",
        "MOSQUE",
        "TEMPLE",
        "PASTOR",
        "ARCHDIOCESE",
        "DIOCESE",
        "NPO PAYMENT",
        "SECTION 18A",
    }
)
_DISCRETIONARY_KEYWORDS: set[str] = set()


def _match_keyword_rules(description_upper: str, rules: list[tuple[str, tuple[str, ...]]]) -> str | None:
    for label, keywords in rules:
        if any(kw.upper() in description_upper for kw in keywords):
            return label
    return None


def get_party_name(description: str, amount: float | None = None) -> str:
    """Returns the party name: keyword match first, then ML if enabled and no match, else description."""
    d = (description or "").upper()
    mapped_party = _match_keyword_rules(d, MANUAL_PARTY_KEYWORDS)
    if mapped_party:
        return mapped_party
    for party_name, keywords in PARTY_KEYWORDS:
        if any(kw.upper() in d for kw in keywords):
            return party_name
    d_norm = ml_classifier.normalize_bank_description(description or "")
    if d_norm and d_norm != d:
        mapped_party = _match_keyword_rules(d_norm, MANUAL_PARTY_KEYWORDS)
        if mapped_party:
            return mapped_party
        for party_name, keywords in PARTY_KEYWORDS:
            if any(kw.upper() in d_norm for kw in keywords):
                return party_name
    if ml_classifier.is_ml_enabled():
        cat_choices = get_all_category_names() + ["Other"]
        _cat, party = ml_classifier.classify_with_ml(description, amount, cat_choices)
        if party:
            return party
    return (description or "").strip() or "Other"


def is_generosity(description: str) -> bool:
    d = (description or "").upper()
    if any(kw in d for kw in _GENEROSITY_KEYWORDS):
        return True
    if get_category_label(description, None) == "Charity":
        return True
    return False


def is_discretionary(description: str) -> bool:
    d = (description or "").upper()
    return any(kw in d for kw in _DISCRETIONARY_KEYWORDS)


def get_category_label(description: str, amount: float | None = None) -> str | None:
    """Returns the category: keyword match first, then ML if enabled and no match, else None (Other)."""
    d = (description or "").upper()
    mapped_category = _match_keyword_rules(d, MANUAL_CATEGORY_KEYWORDS)
    if mapped_category:
        return mapped_category
    for name, keywords in CATEGORY_KEYWORDS:
        if any(kw.upper() in d for kw in keywords):
            return name
    d_norm = ml_classifier.normalize_bank_description(description or "")
    if d_norm and d_norm != d:
        mapped_category = _match_keyword_rules(d_norm, MANUAL_CATEGORY_KEYWORDS)
        if mapped_category:
            return mapped_category
        for name, keywords in CATEGORY_KEYWORDS:
            if any(kw.upper() in d_norm for kw in keywords):
                return name
    if ml_classifier.is_ml_enabled():
        cat_choices = get_all_category_names() + ["Other"]
        category, _party = ml_classifier.classify_with_ml(description, amount, cat_choices)
        if category and category != "Other":
            return category
        if category == "Other" and d_norm:
            category2, _ = ml_classifier.classify_with_ml(d_norm, amount, cat_choices)
            if category2 and category2 != "Other":
                return category2
        if category:
            return category
    return None


def spending_category_breakdown_caption() -> str:
    """UI label for how spending-by-category was derived (dashboard)."""
    return ml_classifier.spending_breakdown_caption()


def get_all_category_names() -> list[str]:
    """Returns all category names in order (for Spending by category and defaults)."""
    seen: set[str] = set()
    out: list[str] = []
    for name, _ in MANUAL_CATEGORY_KEYWORDS + CATEGORY_KEYWORDS:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def get_all_party_names() -> list[str]:
    """Party labels from keyword rules (for settings / budgets UI)."""
    seen: set[str] = set()
    out: list[str] = []
    for name, _ in MANUAL_PARTY_KEYWORDS + PARTY_KEYWORDS:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out
