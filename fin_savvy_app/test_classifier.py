"""
Quick test of category/party classification. Run from project root with venv active:

  python -m fin_savvy_app.test_classifier

Or with FINSAVVY_CLASSIFIER=local to test ML:

  FINSAVVY_CLASSIFIER=local python -m fin_savvy_app.test_classifier
"""

from fin_savvy_app import classifier, ml_classifier

SAMPLES = [
    "SPAR ROSEBANK",
    "VODACOM",
    "BOSSA SOMERSET",
    "CHECKERS HYPER",
    "UNKNOWN MERCHANT XYZ",
    "TAXI UBER",
]


def main() -> None:
    mode = "local" if ml_classifier.is_ml_enabled() else "keyword"
    print(f"Classifier mode: {mode}\n")
    print(f"{'Description':<30} {'Category':<20} {'Party':<25}")
    print("-" * 75)
    for desc in SAMPLES:
        cat = classifier.get_category_label(desc, None) or "Other"
        party = classifier.get_party_name(desc, None)
        print(f"{desc:<30} {cat:<20} {party:<25}")
    print("\nIf mode is 'local', the above used your trained ML models.")


if __name__ == "__main__":
    main()
