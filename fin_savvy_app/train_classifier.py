"""
Train local ML models for transaction category and party from a labeled CSV.

CSV format (header required):
  description,category,party

Example:
  description,category,party
  "SPAR STORE 123",Groceries,Spar
  "VODACOM DEBIT",Telecommunications,Vodacom

Run from project root:
  python -m fin_savvy_app.train_classifier [path/to/labeled_transactions.csv]

Default CSV path: fin_savvy_app/data/labeled_transactions.csv

Saves to fin_savvy_app/data/:
  - local_vectorizer.joblib
  - local_category_model.joblib
  - local_party_model.joblib
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

# Project root: parent of fin_savvy_app
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DEFAULT_CSV = DATA_DIR / "labeled_transactions.csv"


def train(csv_path: Path) -> None:
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        print("Create a file with header: description,category,party")
        print("Add one row per labeled transaction (export from your spreadsheet).")
        sys.exit(1)

    descriptions: list[str] = []
    categories: list[str] = []
    parties: list[str] = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "description" not in (reader.fieldnames or []):
            print("CSV must have columns: description,category,party")
            sys.exit(1)
        for row in reader:
            desc = (row.get("description") or "").strip()
            cat = (row.get("category") or "").strip()
            party = (row.get("party") or "").strip()
            if desc and cat and party:
                descriptions.append(desc)
                categories.append(cat)
                parties.append(party)

    if len(descriptions) < 10:
        print(f"Need at least 10 labeled rows; got {len(descriptions)}. Add more to the CSV.")
        sys.exit(1)

    # Shared vectorizer (same text features for both tasks)
    vectorizer = TfidfVectorizer(
        max_features=8000,
        ngram_range=(1, 2),
        min_df=1,
        strip_accents="unicode",
        lowercase=True,
    )
    X = vectorizer.fit_transform(descriptions)

    # Category classifier — class_weight balances skewed CSVs (e.g. too many Dining rows).
    pipe_cat = Pipeline([
        ("vec", TfidfVectorizer(
            max_features=8000,
            ngram_range=(1, 2),
            min_df=1,
            strip_accents="unicode",
            lowercase=True,
        )),
        ("clf", LogisticRegression(max_iter=500, C=0.5, class_weight="balanced")),
    ])
    pipe_cat.fit(descriptions, categories)

    # Party classifier
    pipe_party = Pipeline([
        ("vec", TfidfVectorizer(
            max_features=8000,
            ngram_range=(1, 2),
            min_df=1,
            strip_accents="unicode",
            lowercase=True,
        )),
        ("clf", LogisticRegression(max_iter=500, C=0.5, class_weight="balanced")),
    ])
    pipe_party.fit(descriptions, parties)

    joblib.dump(pipe_cat, DATA_DIR / "local_category_model.joblib")
    joblib.dump(pipe_party, DATA_DIR / "local_party_model.joblib")

    print(f"Trained on {len(descriptions)} rows. Models saved to {DATA_DIR}")
    print("Set FINSAVVY_CLASSIFIER=local and restart the app to use them.")


def main() -> None:
    if len(sys.argv) > 1:
        csv_path = Path(sys.argv[1])
    else:
        csv_path = DEFAULT_CSV
    train(csv_path)


if __name__ == "__main__":
    main()
