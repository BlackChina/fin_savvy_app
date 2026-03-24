"""
Simple South African income tax estimate (2025 tax year).
Uses SARS brackets and primary rebate only.
"""

# 2025/26 brackets: (threshold, rate, base_tax)
# tax = base_tax + rate * (income - threshold) for the bracket
_SA_BRACKETS = [
    (0, 0.18, 0),
    (237_100, 0.26, 42_678),
    (370_500, 0.31, 77_362),
    (512_800, 0.36, 121_475),
    (673_000, 0.39, 179_147),
    (857_900, 0.41, 251_258),
    (1_817_000, 0.45, 644_489),
]
PRIMARY_REBATE = 17_235


def calculate_tax(annual_taxable_income: float) -> dict:
    """
    Returns dict with estimated_tax, after_rebate, effective_rate, take_home.
    Income should be positive (annual taxable income in ZAR).
    """
    income = max(0.0, float(annual_taxable_income))
    tax_before_rebate = 0.0
    for i in range(len(_SA_BRACKETS) - 1, -1, -1):
        threshold, rate, base = _SA_BRACKETS[i]
        if income > threshold:
            tax_before_rebate = base + rate * (income - threshold)
            break
    after_rebate = max(0.0, tax_before_rebate - PRIMARY_REBATE)
    effective = (after_rebate / income * 100) if income else 0.0
    take_home = income - after_rebate
    return {
        "estimated_tax": round(after_rebate, 2),
        "tax_before_rebate": round(tax_before_rebate, 2),
        "rebate_applied": PRIMARY_REBATE,
        "effective_rate_pct": round(effective, 1),
        "take_home": round(take_home, 2),
        "annual_income": round(income, 2),
    }


def format_tax_report_text(result: dict) -> str:
    """Plain-text summary for download (SARS-style estimate disclaimer)."""
    lines = [
        "Fin Savvy – South African income tax estimate (simplified)",
        "This is not tax advice. Consult SARS or a tax practitioner.",
        "",
        f"Annual taxable income (ZAR): {result['annual_income']:,.2f}".replace(",", " "),
        f"Tax before primary rebate:     {result['tax_before_rebate']:,.2f}".replace(",", " "),
        f"Primary rebate applied:        {result['rebate_applied']:,.2f}".replace(",", " "),
        f"Estimated tax after rebate:  {result['estimated_tax']:,.2f}".replace(",", " "),
        f"Effective rate:              {result['effective_rate_pct']}%",
        f"Estimated take-home:         {result['take_home']:,.2f}".replace(",", " "),
        "",
    ]
    return "\n".join(lines)
