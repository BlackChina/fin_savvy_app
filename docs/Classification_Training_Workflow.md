# Classification training workflow

This document matches the plan in `Classification_Training_Todo.docx` (checklist + commands). Use it from the **project root** (`foobar-it-solutions`) unless noted.

## Goals

1. **Export** real bank descriptions from Postgres with classifier hints (`category`, `party`) as a starting point for manual review.
2. **Label** — fix wrong hints in a spreadsheet or editor; keep columns `description`, `category`, `party`.
3. **Merge** seed data (`labeled_transactions.csv`) with your fixes or new rows (`merge_labeled_csv.py`).
4. **Train** — `train_classifier.py` writes `local_category_model.joblib` and `local_party_model.joblib`.
5. **Restart Docker** so the app reloads models (volume-mounted code + data still needs process restart).
6. **Validate** — `test_classifier.py` and spot-check the dashboard.
7. **Optional keywords** — high-confidence merchants in `classifier.py` stay keyword-first before ML (see `ML_CLASSIFIER.md`).

---

## 1. Export descriptions

Requires a running database and a user that has uploaded statements.

```bash
docker compose run --rm app python -m fin_savvy_app.export_training_csv \
  -o /app/fin_savvy_app/data/export_candidates.csv --username mfundo
```

Options:

| Flag | Meaning |
|------|--------|
| `--account-id N` | Only that bank account |
| `--min-count K` | Only descriptions appearing at least K times |

Copy the CSV off the container if needed (`docker cp`), or write under `fin_savvy_app/data/` (mounted from the host).

---

## 2. Build / edit labeled CSV

- Open `export_candidates.csv` (or a copy).
- Ensure every row you want to train has **non-empty** `category` and `party`.
- **Description** must match bank text (training uses raw string).

---

## 3. Merge with seed labels

Shipped seed file: `fin_savvy_app/data/labeled_transactions.csv`.

```bash
docker compose run --rm app python -m fin_savvy_app.merge_labeled_csv \
  --base /app/fin_savvy_app/data/labeled_transactions.csv \
  --overlay /app/fin_savvy_app/data/my_labels.csv \
  -o /app/fin_savvy_app/data/labeled_merged.csv
```

Overlays override the base when the **normalized** description matches (trim + case-insensitive). Rows missing `category` or `party` are dropped from the merged output.

Train from the merged file:

```bash
docker compose run --rm app python -m fin_savvy_app.train_classifier /app/fin_savvy_app/data/labeled_merged.csv
```

Or overwrite `labeled_transactions.csv` after review, then:

```bash
docker compose run --rm app python -m fin_savvy_app.train_classifier
```

---

## 4. Train (default path)

```bash
docker compose build
docker compose run --rm app python -m fin_savvy_app.train_classifier
```

Needs **at least 10** complete rows (`description`, `category`, `party` all non-empty).

---

## 5. Restart Docker

```bash
docker compose restart app
```

`docker-compose.yml` sets `FINSAVVY_CLASSIFIER=local` and mounts `./fin_savvy_app` so new `*.joblib` files under `fin_savvy_app/data/` are picked up after restart.

---

## 6. Validate

```bash
docker compose run --rm -e FINSAVVY_CLASSIFIER=local app python -m fin_savvy_app.test_classifier
```

Check sample lines and the live dashboard for “Other” share and obvious mislabels.

---

## 7. Optional keywords

Edit `fin_savvy_app/classifier.py` keyword rules for merchants that must never drift (e.g. salary strings, large utilities). ML runs **after** keywords when no rule matches.

---

## Environment reminders

| Variable | Role |
|----------|------|
| `FINSAVVY_CLASSIFIER` | `keyword` \| `local` \| `openai` |
| `FINSAVVY_ML_MIN_PROBABILITY` | Min top-class probability for local ML (compose may set `0` for permissive mode) |

See `fin_savvy_app/ML_CLASSIFIER.md` for detail.
