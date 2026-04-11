"""
Merge two labeled training CSVs (description,category,party). Dedupe by normalized description.

Later files win: each --overlay is applied in order after --base.

Usage:
  python -m fin_savvy_app.merge_labeled_csv \\
    --base fin_savvy_app/data/labeled_transactions.csv \\
    --overlay fin_savvy_app/data/my_fixes.csv \\
    -o fin_savvy_app/data/labeled_transactions_merged.csv

Then replace the base or pass the merged path to train_classifier.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


REQUIRED = ("description", "category", "party")


def _norm_key(description: str) -> str:
    return " ".join((description or "").strip().split()).casefold()


def _read_labeled(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        print(f"Missing file: {path}", file=sys.stderr)
        sys.exit(1)
    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        missing = [c for c in REQUIRED if c not in fields]
        if missing:
            print(f"{path}: missing columns {missing}; need {REQUIRED}", file=sys.stderr)
            sys.exit(1)
        for row in reader:
            desc = (row.get("description") or "").strip()
            if not desc:
                continue
            rows.append(
                {
                    "description": desc,
                    "category": (row.get("category") or "").strip(),
                    "party": (row.get("party") or "").strip(),
                }
            )
    return rows


def merge_layers(paths: list[Path]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for path in paths:
        for row in _read_labeled(path):
            key = _norm_key(row["description"])
            merged[key] = {
                "description": row["description"],
                "category": row["category"],
                "party": row["party"],
            }
    # Stable sort by description for readable diffs
    return sorted(merged.values(), key=lambda r: r["description"].casefold())


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge labeled CSVs for train_classifier.")
    parser.add_argument("--base", type=Path, required=True, help="First layer (e.g. shipped seed labels)")
    parser.add_argument(
        "--overlay",
        type=Path,
        action="append",
        default=[],
        help="Additional CSV(s); later overlays override earlier rows on same description",
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="Merged CSV path")
    args = parser.parse_args()

    layers = [args.base, *args.overlay]
    out_rows = merge_layers(layers)

    complete = [r for r in out_rows if r["category"] and r["party"]]
    skipped = len(out_rows) - len(complete)
    if skipped:
        print(f"Note: {skipped} row(s) missing category or party were dropped for training safety.", file=sys.stderr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(REQUIRED))
        w.writeheader()
        w.writerows(complete)

    print(f"Wrote {len(complete)} rows to {args.output}")


if __name__ == "__main__":
    main()
