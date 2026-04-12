"""Quick checks for customize validation and split traffic light (run: python3 -m fin_savvy_app.test_budget_validate)."""

from __future__ import annotations

from fin_savvy_app import budget_503020, budget_validate


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> None:
    base = [
        {"category": "A", "limit": 100.0},
        {"category": "B", "limit": 100.0},
        {"category": "C", "limit": 100.0},
        {"category": "D", "limit": 100.0},
        {"category": "E", "limit": 100.0},
        {"category": "F", "limit": 100.0},
        {"category": "G", "limit": 100.0},
        {"category": "H", "limit": 100.0},
        {"category": "I", "limit": 100.0},
        {"category": "J", "limit": 100.0},
    ]
    cap = budget_validate.max_add_or_remove_lines(10)
    _assert(cap == 4, f"expected cap 4 for n=10, got {cap}")

    # Remove 4 lines, tweak totals within 75–125% of 1000
    sub = [{"category": c, "limit": 125.0} for c in ("A", "B", "C", "F", "G", "H")]
    err = budget_validate.validate_customized_503020_flexible(base, sub)
    _assert(err is None, err)

    # Remove 5 lines (>40% of 10)
    sub2 = [{"category": c, "limit": 200.0} for c in ("A", "B", "C", "D", "E")]
    err2 = budget_validate.validate_customized_503020_flexible(base, sub2)
    _assert(err2 is not None and "remove" in err2.lower(), err2)

    # Add 5 new categories
    sub3 = list(base) + [
        {"category": "X1", "limit": 10.0},
        {"category": "X2", "limit": 10.0},
        {"category": "X3", "limit": 10.0},
        {"category": "X4", "limit": 10.0},
        {"category": "X5", "limit": 10.0},
    ]
    err3 = budget_validate.validate_customized_503020_flexible(base, sub3)
    _assert(err3 is not None and "add" in err3.lower(), err3)

    # Prior-month income caps the high bound: 125% of 1000 = 1250 but income 1000 → max 1000
    sub_cap = [{"category": c, "limit": 110.0} for c in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")]
    err_cap = budget_validate.validate_customized_503020_flexible(
        base, sub_cap, prior_month_income=1000.0
    )
    _assert(err_cap is not None and ("income" in err_cap.lower() or "exceed" in err_cap.lower()), err_cap)

    _assert(budget_validate.previous_year_month("2025-03") == "2025-02", "prev ym")
    _assert(budget_validate.previous_year_month("2025-01") == "2024-12", "prev ym jan")

    g = budget_503020.split_balance_traffic_light(500, 300, 200)
    _assert(g["state"] == "green", g)

    amb = budget_503020.split_balance_traffic_light(430, 430, 140)
    _assert(amb["state"] == "amber", amb)

    red = budget_503020.split_balance_traffic_light(450, 450, 100)
    _assert(red["state"] == "red", red)

    base_g = [{"category": "Groceries", "limit": 100.0}]
    sub_dup = [{"category": "Groceries", "limit": 40.0}, {"category": "Groceries", "limit": 60.0}]
    err_dup = budget_validate.validate_customized_503020_flexible(base_g, sub_dup)
    _assert(err_dup is not None and "Groceries" in err_dup and "2" in err_dup, err_dup)

    sub_other_dup = [
        {"category": "Other", "limit": 10.0, "other_detail": "Pet"},
        {"category": "Other", "limit": 20.0, "other_detail": "Pet"},
    ]
    msg_o = budget_validate.duplicate_budget_lines_user_message(sub_other_dup)
    _assert("Other" in msg_o and "Pet" in msg_o, msg_o)

    print("budget_validate / split_balance_traffic_light: OK")


if __name__ == "__main__":
    main()
