"""
Export unique transaction descriptions from the DB with classifier hints for ML labeling.

Output columns (header):
  description,category,party,count,last_date,avg_amount

`train_classifier.py` only reads description, category, party; extra columns are ignored
if you pass this file directly to training after filling blanks.

Usage (project root, venv):
  python -m fin_savvy_app.export_training_csv -o fin_savvy_app/data/export_candidates.csv

Docker:
  docker compose run --rm app python -m fin_savvy_app.export_training_csv \\
    -o /app/fin_savvy_app/data/export_candidates.csv --username mfundo
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func

from . import classifier, crud, models
from .database import SessionLocal


def _norm_key(description: str) -> str:
    return " ".join(description.strip().split()).casefold()


def export_for_user(
    db,
    user_id: int,
    account_id: int | None,
    min_count: int,
) -> list[tuple[str, str, str, int, date | None, float]]:
    """
    Returns rows: description, category_hint, party_hint, count, last_date, avg_amount.
    """
    q = (
        db.query(
            models.Transaction.description_raw,
            func.count(models.Transaction.id).label("cnt"),
            func.max(models.Transaction.date).label("last_date"),
            func.avg(models.Transaction.amount).label("avg_amt"),
        )
        .join(models.Statement)
        .join(models.BankAccount)
        .filter(models.BankAccount.user_id == user_id)
    )
    if account_id is not None:
        q = q.filter(models.Statement.bank_account_id == account_id)
    q = q.group_by(models.Transaction.description_raw).having(func.count(models.Transaction.id) >= min_count)
    q = q.order_by(func.count(models.Transaction.id).desc())
    rows = q.all()

    out: list[tuple[str, str, str, int, date | None, float]] = []
    seen: set[str] = set()
    for r in rows:
        desc = (r.description_raw or "").strip()
        if not desc:
            continue
        key = _norm_key(desc)
        if key in seen:
            continue
        seen.add(key)
        cnt = int(r.cnt)
        last_d = r.last_date
        avg_amt = float(r.avg_amt or 0.0)
        cat = classifier.get_category_label(desc, avg_amt) or ""
        party = classifier.get_party_name(desc, avg_amt) or ""
        out.append((desc, cat, party, cnt, last_d, avg_amt))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Export unique descriptions for classifier training CSV.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output CSV path (e.g. fin_savvy_app/data/export_candidates.csv)",
    )
    parser.add_argument(
        "--username",
        default="mfundo",
        help="User whose transactions to export (default: mfundo)",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        default=None,
        help="Limit to one bank account id (optional)",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=1,
        help="Only include descriptions with at least this many rows (default: 1)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = crud.get_user_by_username(db, args.username)
        if not user:
            print(f"User not found: {args.username}", file=sys.stderr)
            sys.exit(1)
        if args.account_id is not None:
            acc = (
                db.query(models.BankAccount)
                .filter(
                    models.BankAccount.id == args.account_id,
                    models.BankAccount.user_id == user.id,
                )
                .first()
            )
            if not acc:
                print(f"Bank account {args.account_id} not found for user {args.username}", file=sys.stderr)
                sys.exit(1)

        rows = export_for_user(db, user.id, args.account_id, args.min_count)
    finally:
        db.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["description", "category", "party", "count", "last_date", "avg_amount"])
        for desc, cat, party, cnt, last_d, avg_amt in rows:
            w.writerow(
                [
                    desc,
                    cat,
                    party,
                    cnt,
                    last_d.isoformat() if last_d else "",
                    f"{avg_amt:.2f}",
                ]
            )

    print(f"Wrote {len(rows)} unique descriptions to {args.output}")
    print("Review category/party columns, fix wrong hints, then merge into labeled_transactions.csv if needed.")


if __name__ == "__main__":
    main()
