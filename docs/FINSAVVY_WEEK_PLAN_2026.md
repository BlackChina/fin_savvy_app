# Fin Savvy – 1‑week functionality plan

**Period:** Monday 09 March 2026 → Sunday 15 March 2026  
**Rule:** One focused session per day; **one commit per day** to `main` (or a feature branch) at end of that day’s work.

---

## Monday 09 March 2026 – **Budgets (backend)**

**Goal:** Add monthly budgets per category so you can later show “spent vs budget”.

- [ ] Add `Budget` model: `user_id`, `bank_account_id` (optional), `category_name` (e.g. "Groceries"), `year_month` (e.g. `2026-03`), `amount` (limit in ZAR).
- [ ] DB migration or `init_db` update to create `budgets` table.
- [ ] CRUD: create/read/update/delete budget for a given user + period + category.
- [ ] Optional: API route `GET /api/budgets?period=2026-03` and `POST /api/budgets` (or form endpoints if you prefer HTML-only).

**End of day commit:**  
`git add … && git commit -m "feat: add Budget model and CRUD for monthly category budgets (2026-03-09)"`

---

## Tuesday 10 March 2026 – **Budgets (dashboard)**

**Goal:** Show budgets on the dashboard and compare to actual spending.

- [ ] On dashboard, for selected period, load budgets for that month.
- [ ] In “Spending by category”: show budget (if set) and variance (e.g. “R 3 200 / R 4 000” or “R 800 under”).
- [ ] Simple “Set budget” form or link to a small budget page: choose category, enter amount, save (uses CRUD from Monday).

**End of day commit:**  
`git add … && git commit -m "feat: dashboard budget vs actual and set budget UI (2026-03-10)"`

---

## Wednesday 11 March 2026 – **Export transactions (CSV)**

**Goal:** Let users download transactions for the selected period as CSV.

- [ ] New route, e.g. `GET /export/transactions.csv?account_id=…&period=2026-03` (or from dashboard with same filters).
- [ ] Response: CSV with columns such as Date, Description, Amount, Category, Party (using existing classifier).
- [ ] Use existing period/account logic; stream or generate CSV in memory for that period.
- [ ] Dashboard: add “Download CSV” button that links to this URL with current account + period.

**End of day commit:**  
`git add … && git commit -m "feat: CSV export of transactions for selected period (2026-03-11)"`

---

## Thursday 12 March 2026 – **Month‑over‑month summary**

**Goal:** Quick comparison of income/expenses vs previous month.

- [ ] On dashboard, compute totals for **previous month** (same account) in addition to current period.
- [ ] Add a small “vs last month” section: e.g. “Income: R 45 000 (↑ 10% from Feb)” / “Expenses: R 38 000 (↓ 5%)”.
- [ ] Optional: simple sparkline or +/- badge next to summary cards.

**End of day commit:**  
`git add … && git commit -m "feat: month-over-month comparison on dashboard (2026-03-12)"`

---

## Friday 13 March 2026 – **Receipts and transactions link**

**Goal:** Link uploaded receipts to transactions (e.g. match by date + amount or manual select).

- [ ] If not already present: add `transaction_id` (nullable FK) to receipt model, or a link table.
- [ ] UI on receipts page: for each receipt, optional “Link to transaction” (dropdown or search of transactions in that month).
- [ ] Dashboard or receipt view: show “Receipt” badge/link on a transaction when linked.
- [ ] Optional: suggest matches by date + amount and let user confirm.

**End of day commit:**  
`git add … && git commit -m "feat: link receipts to transactions and show on dashboard (2026-03-13)"`

---

## Saturday 14 March 2026 – **Settings / categories overview**

**Goal:** One place to see (and optionally tune) how categories and parties are used.

- [ ] New “Settings” or “Categories” page (e.g. `/settings` or `/categories`), protected (login required).
- [ ] List categories and parties used in the app (from classifier: keyword lists and/or from ML CSV).
- [ ] Optional: simple “Custom category” or “Custom party” form that appends to keyword lists or adds rows to a user-specific table (if you introduce one).
- [ ] Link to ML doc: “To improve auto-category, edit CSV and retrain” (link to `ML_CLASSIFIER.md` or `run_train_classifier.sh`).

**End of day commit:**  
`git add … && git commit -m "feat: settings/categories page and docs link (2026-03-14)"`

---

## Sunday 15 March 2026 – **Polish and docs**

**Goal:** Stabilise the week’s features and document them.

- [ ] Fix any bugs from the week (dashboard, export, budgets, receipts link).
- [ ] Update README: list new features (budgets, CSV export, month‑over‑month, receipt linking, settings).
- [ ] Optional: add a short “Changelog” or “Recent” section in README or `docs/CHANGELOG.md` for 2026-03-09 → 2026-03-15.
- [ ] Ensure Docker and `run_train_classifier.sh` still work; .gitignore and env docs up to date.

**End of day commit:**  
`git add … && git commit -m "docs and polish: README, changelog, bug fixes (2026-03-15)"`

---

## Commit checklist (each day)

1. Work only on that day’s scope.
2. Test locally (and in Docker if you use it).
3. Stage only relevant files:  
   `git add <files>`
4. Single commit with a clear message including the date:  
   `git commit -m "feat: … (2026-MM-DD)"`
5. Push at end of day (or when ready):  
   `git push origin main`

---

## Optional stretch (if time)

- **Recurring transactions:** Mark or detect recurring items (e.g. subscriptions) and show “Recurring” on dashboard.
- **Multi-account view:** Dashboard option to “All accounts” for the period (aggregate income/expenses).
- **Password change:** Logged-in user can change password from settings.

---

*If your “next week Sunday” was 15 **February** 2026 instead, shift all dates back by one month (09 Feb – 15 Feb) and use the same daily themes.*
