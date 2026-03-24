# Fin Savvy – core features vs implementation

| Feature | Status | How it works in this repo |
|--------|--------|---------------------------|
| **Bank statement import** | PDF + CSV | PDF: `pdf_parser.py` (Standard Bank–style text). CSV: `csv_parser.py` — headers like `Date`, `Description`, `Amount` or `Debit`/`Credit`. Upload at `/upload`. |
| **Expense classification** | Rules + optional ML | `classifier.py` keywords first, then `ml_classifier.py` when `FINSAVVY_CLASSIFIER=local` (see `ML_CLASSIFIER.md`). |
| **Receipts / invoices** | Upload + optional OCR | `/receipts` stores amount, date, file. Checkbox **Try OCR** runs `receipt_ocr.py` (Pillow + pytesseract) on PNG/JPG — install system package **`tesseract-ocr`** for it to work. |
| **Cash tracking** | Yes | Cash withdrawals flagged on import; receipt totals vs cash on dashboard; warning card + `/api/alerts`. |
| **Budget insights** | API (Pandas) | `GET /api/insights/budget?account_id=&period=YYYY-MM` — category totals, daily series (`insights.py`). Use for extra charts or mobile clients. |
| **Tax calculator** | SA brackets | `tax_calc.py`; `/tax` UI; **Download text report** → `/tax/report?income=…`. Payslip files stored, not auto-parsed yet. |
| **Credit score** | Placeholder API | `GET /api/credit/score` (logged-in). Set `CREDIT_API_KEY` when ready; implement `credit_api.py` with your bureau. |
| **Notifications / flags** | In-app + JSON | `alerts.py` — dashboard banners + `GET /api/alerts?account_id=&period=YYYY-MM` for cron/external monitors. |

## Cron example (alerts)

```bash
# After logging in, cookies are awkward for curl; use a small script with requests + session,
# or call from your own worker with a service token if you add one later.
curl -b "session=..." "http://localhost:8001/api/alerts?account_id=1&period=2026-03"
```

## Docker / OCR

For OCR in containers, extend your image e.g.:

```dockerfile
RUN apt-get update && apt-get install -y tesseract-ocr && rm -rf /var/lib/apt/lists/*
```

## Dependencies added

- `pandas` — budget insights API  
- `Pillow`, `pytesseract` — optional receipt OCR (requires `tesseract-ocr` binary)
- `python-docx` — optional; run `python scripts/build_feature_roadmap_docx.py` for Word roadmaps in `docs/`

## Recently added (UI, no external APIs)

- **Monthly budgets** — `/budgets` set limits per category (per account or all accounts); dashboard “Spending by category” shows budget line when set.
- **CSV export** — dashboard link `Download CSV` → `/export/transactions.csv?account_id=&period=YYYY-MM`.
- **Month-over-month** — previous month income/expense summary on dashboard.
- **Search** — filter expense/income lists by description (`q=`).
- **Receipt ↔ transaction** — on `/receipts`, pick account then link each receipt to a bank expense (last ~120 days).
- **Settings** — `/settings` lists categories/parties from classifier; link to change password.
- **Change password** — `/account/password`.
- **Roadmap docs** — `docs/FINSAVVY_FEATURE_ROADMAP_FULL.docx`, `FINSAVVY_FEATURE_ROADMAP_STATUS.docx` (+ `.rtf` copies); regenerate with `scripts/build_feature_roadmap_docx.py`.
