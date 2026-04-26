"""
Budget / spending insights using Pandas summaries (category totals, series for charts).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from . import classifier


def expense_dataframe(
    transactions: list[tuple[date, str, float]],
) -> pd.DataFrame:
    """
    transactions: list of (date, description_raw, amount) for expenses (amount negative in DB).
    """
    if not transactions:
        return pd.DataFrame(columns=["date", "description", "amount_abs", "category", "party"])

    rows = []
    for d, desc, amt in transactions:
        amt_abs = abs(float(amt))
        cat = classifier.get_category_label(desc, amt) or "Other"
        party = classifier.get_party_name(desc, amt)
        rows.append(
            {
                "date": d,
                "description": desc,
                "amount_abs": amt_abs,
                "category": cat,
                "party": party,
            }
        )
    return pd.DataFrame(rows)


def summarize_by_category(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"by_category": {}, "total_expenses": 0.0, "top_category": None}
    g = df.groupby("category", as_index=False)["amount_abs"].sum().sort_values("amount_abs", ascending=False)
    by_cat = {row["category"]: float(row["amount_abs"]) for _, row in g.iterrows()}
    total = float(df["amount_abs"].sum())
    top = str(g.iloc[0]["category"]) if len(g) else None
    return {"by_category": by_cat, "total_expenses": total, "top_category": top}


def daily_expense_series(df: pd.DataFrame) -> dict[str, list]:
    """For API / extra charts: labels (ISO dates) and values per day."""
    if df.empty:
        return {"labels": [], "values": []}
    df = df.copy()
    df["day"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    daily = df.groupby("day", as_index=False)["amount_abs"].sum().sort_values("day")
    return {
        "labels": daily["day"].tolist(),
        "values": [float(x) for x in daily["amount_abs"].tolist()],
    }


def build_budget_insights_payload(
    expense_tuples: list[tuple[date, str, float]],
) -> dict[str, Any]:
    df = expense_dataframe(expense_tuples)
    summary = summarize_by_category(df)
    daily = daily_expense_series(df)
    return {
        **summary,
        "daily_expenses": daily,
        "transaction_count": int(len(df)),
    }


def pattern_summary_for_month(
    expense_tuples: list[tuple[date, str, float]],
    *,
    top_n: int = 8,
) -> dict[str, Any]:
    """
    Lightweight “pattern analyser”: party concentration, lifestyle share, top merchants.
    """
    df = expense_dataframe(expense_tuples)
    if df.empty:
        return {
            "top_parties": [],
            "lifestyle_share_pct": None,
            "generosity_share_pct": None,
            "herfindahl_parties": None,
            "note": "No expense rows in this period.",
        }
    total = float(df["amount_abs"].sum())
    lifestyle_labels = frozenset({"Dining", "Entertainment", "Alcohol & nightlife"})
    lifestyle = float(df[df["category"].isin(lifestyle_labels)]["amount_abs"].sum())
    gen_mask = df["description"].map(lambda d: classifier.is_generosity(str(d)))
    generosity = float(df.loc[gen_mask, "amount_abs"].sum())
    party_totals = df.groupby("party", as_index=False)["amount_abs"].sum().sort_values("amount_abs", ascending=False)
    top = [
        {"party": str(row["party"]), "total": float(row["amount_abs"])}
        for _, row in party_totals.head(top_n).iterrows()
    ]
    shares = (party_totals["amount_abs"] / total) ** 2 if total > 0 else party_totals["amount_abs"] * 0
    hhi = float(shares.sum()) if total > 0 else None
    return {
        "top_parties": top,
        "lifestyle_share_pct": round((lifestyle / total) * 100.0, 1) if total > 0 else None,
        "generosity_share_pct": round((generosity / total) * 100.0, 1) if total > 0 else None,
        "herfindahl_parties": round(hhi, 4) if hhi is not None else None,
        "note": "Higher party concentration (HHI) means a few merchants dominate spend this month.",
    }
