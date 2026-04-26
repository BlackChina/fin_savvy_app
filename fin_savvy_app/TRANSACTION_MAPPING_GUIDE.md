# Transaction Mapping Guide

Use `fin_savvy_app/transaction_mappings.py` to force category and party matching for merchants you already know.

## Where to edit

- `MANUAL_CATEGORY_KEYWORDS`: map description keywords to a category.
- `MANUAL_PARTY_KEYWORDS`: map description keywords to a party/merchant name.

These mappings run **before** built-in keywords and ML.

## Example

```python
MANUAL_CATEGORY_KEYWORDS = [
    ("Groceries", ("CHECKERS SIXTY60", "WOOLWORTHS FOODS")),
    ("Fuel", ("ENGEN SOMERSET WEST", "SHELL HELDERBERG")),
]

MANUAL_PARTY_KEYWORDS = [
    ("Checkers", ("CHECKERS SIXTY60", "CHECKERS HYPER")),
    ("Engen", ("ENGEN SOMERSET WEST",)),
]
```

## Best practices

- Use uppercase keyword snippets from the real bank description.
- Put the most specific patterns first.
- Add one merchant family at a time, then test.
- Keep category names consistent with your dashboard categories.

## Quick test

Run:

```bash
python3 -m fin_savvy_app.test_classifier
```

If you use a virtual environment:

```bash
./fin_savvy_app/venv/bin/python -m fin_savvy_app.test_classifier
```
