# ML-based transaction classification (optional)

The app can classify transactions (category + party) in three ways:

| Mode    | Env value              | How it works |
|---------|------------------------|--------------|
| **keyword** | default, or `FINSAVVY_CLASSIFIER=keyword` | Rules in `classifier.py` only. No ML. |
| **local**   | `FINSAVVY_CLASSIFIER=local` | Models trained from your CSV. No API key. |
| **openai**  | `FINSAVVY_CLASSIFIER=openai` + `OPENAI_API_KEY` | OpenAI API (for when you’re ready). |

When using **local** or **openai**, classification is **keyword-first**: if a keyword rule in `classifier.py` matches the description, that result is used. ML is only used when no keyword rule matches, so known merchants (e.g. Spar, Vodacom, Checkers) stay correct and ML fills in for unknowns.

---

## Local ML (no API)

Use your own labeled data to train small models. No API key needed.

### 1. Create a labeled CSV

Format (header required):

```csv
description,category,party
SPAR STORE 123,Groceries,Spar
VODACOM DEBIT,Telecommunications,Vodacom
BOSSASOMERSET,Dining,Bossa
```

- **description** – bank transaction description (as it appears on statements).
- **category** – one of your categories (e.g. Groceries, Dining, Telecommunications, …).
- **party** – payee/merchant name (e.g. Spar, Vodacom, Bossa).

Export from Excel/Sheets or type rows. The more rows (e.g. 50+), the better the model. You can start from `fin_savvy_app/data/labeled_transactions.csv` and add your real data.

**Export from your database (hints for labeling):** unique descriptions with current classifier guesses and extra columns (`count`, `last_date`, `avg_amount`) are written by:

```bash
docker compose run --rm app python -m fin_savvy_app.export_training_csv \
  -o /app/fin_savvy_app/data/export_candidates.csv --username mfundo
```

**Merge** a hand-edited file on top of the seed CSV (dedupe by normalized description; overlays win):

```bash
docker compose run --rm app python -m fin_savvy_app.merge_labeled_csv \
  --base /app/fin_savvy_app/data/labeled_transactions.csv \
  --overlay /app/fin_savvy_app/data/my_labels.csv \
  -o /app/fin_savvy_app/data/labeled_merged.csv
```

End-to-end checklist: `docs/Classification_Training_Workflow.md`.

### 2. Train the models

From the **project root** (e.g. `foobar-it-solutions`).

**Option A – Docker (recommended; no venv on host):**

```bash
docker compose build
docker compose run --rm app python -m fin_savvy_app.train_classifier
```

**Option B – Virtual environment on your machine:**

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install scikit-learn joblib
python -m fin_savvy_app.train_classifier
deactivate
```

Default CSV path: `fin_savvy_app/data/labeled_transactions.csv`. Or pass a path:

```bash
python -m fin_savvy_app.train_classifier /path/to/your_labeled.csv
```

This writes to `fin_savvy_app/data/`:

- `local_category_model.joblib`
- `local_party_model.joblib`

### 3. Use local ML in the app

Set:

```bash
FINSAVVY_CLASSIFIER=local
```

No `OPENAI_API_KEY` needed. Restart the app. The app will load the joblib models and use them for category and party; if a prediction fails or is below the confidence threshold, the category shows as **Other** (keywords already ran first).

#### Why “Dining” (or one category) can look huge

Totals in the dashboard are **sums of whatever category each transaction was assigned**. They are **not** checked against salary or income.

If your training CSV has **many more rows for one category** (e.g. Dining) than others, a plain logistic model often **predicts that class for almost every unknown description**. That inflates that category to nearly **all spending**, which is what you are seeing—not a bug in addition.

Mitigations built into the app:

- **`FINSAVVY_ML_MIN_PROBABILITY`** (default `0.35`): the local model must assign at least this probability to its top class; otherwise the transaction is treated as uncategorized and shows as **Other** (keyword rules already ran and did not match). Raise it (e.g. `0.45`) to be stricter; set to `0` to disable the threshold (old behaviour).
- **Retrain** with `class_weight="balanced"` in `train_classifier.py` so rare categories are not ignored.

For a clean slate without ML while you relabel data: `FINSAVVY_CLASSIFIER=keyword`.

### Troubleshooting: “Parties you pay” mostly one merchant (e.g. Bossa)

1. **Keywords run first** — only lines whose description contains a party keyword (see `PARTY_KEYWORDS` in `classifier.py`) get that label without ML.
2. **Everything else** goes to **local ML** when `FINSAVVY_CLASSIFIER=local`. A small logistic model trained on a skewed CSV often predicts the **same party** for many unrelated strings (especially if `FINSAVVY_ML_MIN_PROBABILITY=0`, which always accepts the top guess).
3. **Fix:** raise **`FINSAVVY_ML_MIN_PROBABILITY`** (try `0.32`–`0.45`); export real descriptions with `export_training_csv.py`, label diverse parties, **merge** into your training CSV, **retrain**, restart the app. Add **specific** keyword rows for recurring gibberish (e.g. bank internal codes) once you know what they are.
4. **Salary showing as Bossa** — if the bank line literally contains Bossa merchant strings, that is the keyword path; otherwise it is the same ML collapse; use min probability + relabel or a payroll keyword for your employer text.

**OpenAI mode** (`FINSAVVY_CLASSIFIER=openai` + API key) can read messy descriptions better, but adds cost, latency, and sending descriptions off-device; try threshold + retrain + keywords first.

### 4. Retrain when you add data

Add more rows to your CSV, then run the training command again. Restart the app to load the new models.

---

## API (OpenAI) – for later

When you want to use an API instead of (or in addition to) local ML:

1. Install: `pip install openai` (in `requirements.txt`).
2. Set:
   - `FINSAVVY_CLASSIFIER=openai`
   - `OPENAI_API_KEY=sk-...`
3. Run the app. The same `classify_with_ml` path will call the OpenAI API; results are cached in memory.

The code uses **gpt-4o-mini** and falls back to the keyword classifier if the API fails or returns nothing.
