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

No `OPENAI_API_KEY` needed. Restart the app. The app will load the joblib models and use them for category and party; if a prediction fails, it falls back to the keyword rules.

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
