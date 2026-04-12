"""Server-side rules for customizing the system 50/30/20 budget before commit."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def previous_year_month(year_month: str) -> str | None:
    """Calendar month immediately before year_month (YYYY-MM)."""
    parts = year_month.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        y, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if m <= 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def _row_key(category: str, other_detail: str | None) -> str:
    c = (category or "").strip()
    if c.lower() == "other":
        od = (other_detail or "").strip().lower()
        return f"other::{od}"
    return f"cat::{c.lower()}"


def duplicate_budget_lines_user_message(submitted: list[dict[str, Any]]) -> str:
    """
    Explain which rows clash when two lines share the same category (or two Other lines share the same label).
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for r in submitted:
        cat = str(r.get("category") or "").strip()
        if not cat:
            continue
        od_raw = str(r.get("other_detail") or "").strip()[:120]
        od: str | None = od_raw if cat.lower() == "other" else None
        if cat.lower() == "other" and not od:
            continue
        k = _row_key(cat, od)
        if cat.lower() == "other":
            label = od or ""
            groups[k].append(f'Other ("{label}")')
        else:
            groups[k].append(cat)

    dup_blocks: list[str] = []
    for k, labels in sorted(groups.items(), key=lambda kv: kv[0]):
        if len(labels) < 2:
            continue
        display = labels[0]
        n = len(labels)
        if k.startswith("other::"):
            dup_blocks.append(
                f"• {display}: {n} rows share this same Other label. Merge the amounts into one row, "
                "or type a different label on the extra row so each Other line is distinct."
            )
        else:
            dup_blocks.append(
                f"• {display}: {n} rows list this category. Keep one row (merge limits into a single total if needed) "
                "or change one row to a different category."
            )

    if not dup_blocks:
        return (
            'Two or more lines use the same category, or two "Other" lines use the same label. '
            "Each category is stored once; each Other label is stored once."
        )

    return (
        "We could not save this budget because the same line appears more than once:\n\n"
        + "\n".join(dup_blocks)
    )


def max_add_or_remove_lines(baseline_line_count: int, *, max_line_change_ratio: float = 0.40) -> int:
    """How many baseline rows may be removed and how many new rows may be added (each capped separately)."""
    n = int(baseline_line_count)
    if n <= 0:
        return 0
    return max(0, int(n * max_line_change_ratio))


def validate_customized_503020_flexible(
    baseline: list[dict[str, Any]],
    submitted: list[dict[str, Any]],
    *,
    max_line_change_ratio: float = 0.40,
    total_min_ratio: float = 0.75,
    total_max_ratio: float = 1.25,
    prior_month_income: float | None = None,
) -> str | None:
    """
    baseline: [{"category", "limit", "other_detail" (optional)}, ...] from the system suggestion.
    submitted: [{"category", "limit", "other_detail" (optional)}, ...] non-empty rows only.

    Rules:
      - Sum(submitted limits) within ±25% of sum(baseline limits) (default 75%–125%).
      - At most int(n * max_line_change_ratio) baseline rows may be removed (by key), and at most
        that many brand-new rows may be added. Limit tweaks on kept rows do not count toward the 40%.
    """
    if not baseline:
        return "No baseline budget to customize — reload the page."
    base_map: dict[str, float] = {}
    for r in baseline:
        cat = str(r["category"]).strip()
        k = _row_key(cat, r.get("other_detail"))
        if k in base_map:
            return "Baseline has duplicate categories — cannot validate."
        base_map[k] = float(r["limit"])
    base_total = sum(base_map.values())
    if base_total <= 0:
        return "Invalid baseline total."

    sub_map: dict[str, float] = {}
    for r in submitted:
        cat = str(r["category"]).strip()
        if not cat:
            continue
        od = (str(r.get("other_detail") or "").strip()[:120] or None) if cat.lower() == "other" else None
        if cat.lower() == "other" and not od:
            return 'For category "Other", enter what this line covers (your custom label).'
        k = _row_key(cat, od)
        if k in sub_map:
            return duplicate_budget_lines_user_message(submitted)
        sub_map[k] = float(r["limit"])

    if not sub_map:
        return "Add at least one category with a positive limit."

    sub_total = sum(sub_map.values())
    lo = base_total * total_min_ratio
    pi = float(prior_month_income) if prior_month_income is not None and float(prior_month_income) > 0 else None
    if pi is not None:
        hi = min(base_total * total_max_ratio, pi)
        if sub_total > pi + 0.01:
            return (
                f"Allocated total cannot exceed last month’s income on this account (R {pi:,.2f}). "
                f"Your total is R {sub_total:,.2f}."
            ).replace(",", " ")
        if sub_total > hi + 0.01:
            return (
                f"Total budget must be at least {(total_min_ratio * 100):.0f}% of the suggested total (R {lo:,.2f}) "
                f"and at most the lower of {(total_max_ratio * 100):.0f}% of suggested (R {base_total * total_max_ratio:,.2f}) "
                f"and last month’s income (R {pi:,.2f}). Your total is R {sub_total:,.2f}."
            ).replace(",", " ")
        if sub_total < lo - 0.01:
            return (
                f"Total budget must be at least {(total_min_ratio * 100):.0f}% of the suggested total (R {lo:,.2f}). "
                f"Your total is R {sub_total:,.2f}."
            ).replace(",", " ")
    else:
        hi = base_total * total_max_ratio
        if sub_total < lo - 0.01 or sub_total > hi + 0.01:
            return (
                f"Total budget must stay within {(total_min_ratio * 100):.0f}%–{(total_max_ratio * 100):.0f}% "
                f"of the suggested total (R {base_total:,.2f}). Your total is R {sub_total:,.2f}."
            ).replace(",", " ")

    base_keys = set(base_map.keys())
    sub_keys = set(sub_map.keys())
    removed = len(base_keys - sub_keys)
    added = len(sub_keys - base_keys)
    n = len(baseline)
    cap = max_add_or_remove_lines(n, max_line_change_ratio=max_line_change_ratio)
    pct = int(round(max_line_change_ratio * 100))
    if removed > cap:
        return (
            f"You may remove at most {cap} of the {n} suggested lines ({pct}% of the list). "
            f"This submission removes {removed}."
        )
    if added > cap:
        return (
            f"You may add at most {cap} new lines on top of the {n} suggested ({pct}% of the list). "
            f"This submission adds {added} new line(s)."
        )
    return None


def validate_scratch_total_vs_prior_income(
    committed_total: float,
    *,
    prior_month_income: float | None,
) -> str | None:
    pi = float(prior_month_income) if prior_month_income is not None and float(prior_month_income) > 0 else None
    if pi is None:
        return None
    if float(committed_total) > pi + 0.01:
        return (
            f"Your budget total cannot exceed last month’s income on this account (R {pi:,.2f}). "
            f"Your total is R {float(committed_total):,.2f}."
        ).replace(",", " ")
    return None
