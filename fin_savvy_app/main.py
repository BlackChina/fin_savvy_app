from calendar import month_name, monthrange
from collections import defaultdict
from datetime import date, timedelta
from io import StringIO
from tempfile import NamedTemporaryFile
import csv as csv_module
import json
import logging
import os
import time
import uuid

from jinja2 import Environment, FileSystemLoader
from jinja2.exceptions import UndefinedError

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import alerts, auth, classifier, crud, csv_parser, insights, models, pdf_parser, receipt_ocr, schemas, tax_calc
from .database import SessionLocal, init_db

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Fin Savvy API")

BASE_DIR = os.path.dirname(__file__)
_template_dir = os.path.join(BASE_DIR, "templates")


def _format_currency(value: float | None) -> str:
    """Format number with space as thousands separator and 2 decimals (e.g. 12 345.67)."""
    if value is None:
        return "0.00"
    try:
        v = float(value)
    except (TypeError, ValueError, UndefinedError):
        return "0.00"
    return f"{v:,.2f}".replace(",", " ")


_jinja_env = Environment(loader=FileSystemLoader(_template_dir))
_jinja_env.filters["format_currency"] = _format_currency
templates = Jinja2Templates(env=_jinja_env)
static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
UPLOAD_RECEIPTS_DIR = os.path.join(BASE_DIR, "uploads", "receipts")
UPLOAD_PAYSLIPS_DIR = os.path.join(BASE_DIR, "uploads", "payslips")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-change-me"),
)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user_id(request: Request, db: Session = Depends(get_db)) -> int | None:
    username = request.session.get("user")
    if not username:
        return None
    user = crud.get_user_by_username(db, username)
    return user.id if user else None


@app.on_event("startup")
def on_startup() -> None:
    """Create tables / seed; retry when Postgres is still starting (Docker race)."""
    max_attempts = 15
    sleep_s = 2
    last: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            init_db()
            if attempt > 1:
                logger.warning("init_db succeeded on attempt %s/%s", attempt, max_attempts)
            return
        except OperationalError as e:
            last = e
            logger.warning(
                "Database not reachable yet (%s/%s): %s",
                attempt,
                max_attempts,
                e,
            )
            if attempt < max_attempts:
                time.sleep(sleep_s)
        except Exception:
            logger.exception("init_db failed (not a connection error) — check logs and schema.")
            raise
    logger.error("Giving up: Postgres never became reachable from the app container.")
    raise last  # type: ignore[misc]


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@app.post("/login", response_model=None, response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = crud.get_user_by_username(db, username)
    if user and auth.verify_password(password, user.password_hash):
        request.session["user"] = user.username
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid credentials"},
        status_code=400,
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "error": None},
    )


@app.post("/register", response_model=None, response_class=HTMLResponse)
async def register_submit(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if crud.get_user_by_username(db, username):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Username already taken"},
            status_code=400,
        )
    if crud.get_user_by_email(db, email):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Email already registered"},
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Password must be at least 6 characters"},
            status_code=400,
        )
    user = crud.create_user(db, username=username, email=email, password=password)
    crud.create_bank_account(
        db, user.id,
        schemas.BankAccountCreate(name="Current Account", institution="My Bank", currency="ZAR"),
    )
    return RedirectResponse(url="/login", status_code=303)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "error": None, "sent": False},
    )


@app.post("/forgot-password", response_model=None)
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = crud.get_user_by_email(db, email)
    if user:
        token = crud.create_password_reset_token(db, user.id)
        path = request.url_for("reset_password_page")
        reset_url = f"{str(request.base_url).rstrip('/')}{path}?token={token}"
        return templates.TemplateResponse(
            "forgot_password.html",
            {
                "request": request,
                "error": None,
                "sent": True,
                "reset_url": str(reset_url),
            },
        )
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request, "error": None, "sent": True},
    )


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(
    request: Request,
    token: str | None = None,
) -> HTMLResponse:
    if not token:
        return RedirectResponse(url="/forgot-password", status_code=303)
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "error": None},
    )


@app.post("/reset-password", response_model=None, response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = crud.get_user_by_reset_token(db, token)
    if not user:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Invalid or expired link"},
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "error": "Password must be at least 6 characters"},
            status_code=400,
        )
    crud.use_reset_token(db, token)
    crud.update_user_password(db, user.id, password)
    return RedirectResponse(url="/login", status_code=303)


@app.post("/logout", response_model=None)
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    account_id: int = 1,
    period: str | None = None,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    import traceback

    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)

    # Prefer period from query string to avoid any param injection issues
    period = request.query_params.get("period") or period
    expense_sort = request.query_params.get("expense_sort") or "date"
    income_sort = request.query_params.get("income_sort") or "date"
    search_q = request.query_params.get("q")
    try:
        return _render_dashboard(
            request, user, user_id, account_id, period, db, expense_sort, income_sort, search_q=search_q
        )
    except Exception as e:
        tb = traceback.format_exc()
        return HTMLResponse(
            content=f"<pre style='background:#1e293b;color:#e2e8f0;padding:1rem;overflow:auto;'>{tb}</pre>",
            status_code=500,
        )


def _render_dashboard(
    request,
    user,
    user_id,
    account_id,
    period,
    db,
    expense_sort: str = "date",
    income_sort: str = "date",
    search_q: str | None = None,
):
    accounts = crud.list_bank_accounts(db, user_id)
    if not accounts:
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "account_id": 0,
                "accounts": [],
                "available_months": [],
                "month_names": {i: month_name[i] for i in range(1, 13)},
                "period_label": "No account",
                "month_name": "",
                "year": "",
                "month": None,
                "total_income": 0.0,
                "total_expense": 0.0,
                "net": 0.0,
                "tx_count": 0,
                "generosity_total": 0.0,
                "discretionary_total": 0.0,
                "all_expenses": [],
                "all_income": [],
                "expense_categories": [],
                "income_categories": [],
                "expense_sort": "date",
                "income_sort": "date",
                "chart_income_labels_json": "[]",
                "chart_income_data_json": "[]",
                "chart_expense_labels_json": "[]",
                "chart_expense_data_json": "[]",
                "parties_outgoing": [],
                "parties_incoming": [],
                "parties_outgoing_detailed": [],
                "parties_incoming_detailed": [],
                "username": user,
                "no_accounts": True,
                "cash_withdrawal_total": 0.0,
                "receipt_total": 0.0,
                "cash_receipts_flag": False,
                "receipt_coverage_pct": 100,
                "expense_by_category": {},
                "dashboard_alerts": [],
                "search_q": "",
                "prev_month_income": None,
                "prev_month_expense": None,
                "prev_month_label": "",
                "budget_by_category": {},
            },
        )
    if not crud.get_bank_account_for_user(db, account_id, user_id):
        account_id = accounts[0].id
    available_months = crud.get_available_months(db, account_id)

    latest_date = (
        db.query(func.max(models.Transaction.date))
        .join(models.Statement)
        .filter(models.Statement.bank_account_id == account_id)
        .scalar()
    )

    year, month = None, None
    if period and isinstance(period, str):
        try:
            y, m = period.strip().split("-")
            year, month = int(y.strip()), int(m.strip())
        except (ValueError, AttributeError):
            pass

    if latest_date is None:
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "account_id": account_id,
                "accounts": accounts,
                "available_months": [],
                "month_names": {i: month_name[i] for i in range(1, 13)},
                "period_label": "No data yet",
                "month_name": "",
                "year": "",
                "month": None,
                "total_income": 0.0,
                "total_expense": 0.0,
                "net": 0.0,
                "tx_count": 0,
                "generosity_total": 0.0,
                "discretionary_total": 0.0,
                "all_expenses": [],
                "all_income": [],
                "expense_categories": [],
                "income_categories": [],
                "expense_sort": "date",
                "income_sort": "date",
                "chart_income_labels_json": "[]",
                "chart_income_data_json": "[]",
                "chart_expense_labels_json": "[]",
                "chart_expense_data_json": "[]",
                "parties_outgoing": [],
                "parties_incoming": [],
                "parties_outgoing_detailed": [],
                "parties_incoming_detailed": [],
                "username": user,
                "no_accounts": False,
                "cash_withdrawal_total": 0.0,
                "receipt_total": 0.0,
                "cash_receipts_flag": False,
                "receipt_coverage_pct": 100,
                "expense_by_category": {},
                "dashboard_alerts": [],
                "search_q": "",
                "prev_month_income": None,
                "prev_month_expense": None,
                "prev_month_label": "",
                "budget_by_category": {},
            },
        )

    # Only fall back to latest month when no period was given or parsing failed.
    # Never override an explicit period from the URL so the dropdown selection is respected.
    if year is None or month is None:
        year, month = latest_date.year, latest_date.month

    period_start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    period_end = date(year, month, last_day)

    if month == 1:
        py, pm = year - 1, 12
    else:
        py, pm = year, month - 1
    prev_period_start = date(py, pm, 1)
    _, prev_last = monthrange(py, pm)
    prev_period_end = date(py, pm, prev_last)
    prev_month_label = f"{month_name[pm]} {py}"
    prev_income_q = (
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0))
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= prev_period_start,
            models.Transaction.date <= prev_period_end,
            models.Transaction.direction == "INCOME",
        )
    )
    prev_month_income = float(prev_income_q.scalar() or 0.0)
    prev_expense_q = (
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0))
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= prev_period_start,
            models.Transaction.date <= prev_period_end,
            models.Transaction.direction == "EXPENSE",
        )
    )
    prev_month_expense = float(prev_expense_q.scalar() or 0.0)

    income_q = (
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0))
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "INCOME",
        )
    )
    total_income = income_q.scalar() or 0.0

    expense_q = (
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0.0))
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "EXPENSE",
        )
    )
    total_expense = expense_q.scalar() or 0.0

    net = total_income + total_expense

    tx_count = (
        db.query(func.count(models.Transaction.id))
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
        )
        .scalar()
        or 0
    )

    all_expenses = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "EXPENSE",
        )
        .all()
    )
    generosity_total = sum(abs(t.amount) for t in all_expenses if classifier.is_generosity(t.description_raw))
    discretionary_total = sum(abs(t.amount) for t in all_expenses if classifier.is_discretionary(t.description_raw))

    expenses_q = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "EXPENSE",
        )
    )
    all_expenses = expenses_q.order_by(models.Transaction.date, models.Transaction.id).all()
    if expense_sort == "amount":
        all_expenses = sorted(all_expenses, key=lambda t: (t.amount, t.id))
    elif expense_sort == "date_category":
        all_expenses = sorted(
            all_expenses,
            key=lambda t: (classifier.get_category_label(t.description_raw, t.amount) or "Other", t.date, t.id),
        )
    elif expense_sort == "amount_category":
        all_expenses = sorted(
            all_expenses,
            key=lambda t: (classifier.get_category_label(t.description_raw, t.amount) or "Other", -abs(t.amount), t.id),
        )

    income_q = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "INCOME",
        )
    )
    all_income = income_q.order_by(models.Transaction.date, models.Transaction.id).all()
    if income_sort == "amount":
        all_income = sorted(all_income, key=lambda t: (-t.amount, t.id))
    elif income_sort == "date_category":
        all_income = sorted(
            all_income,
            key=lambda t: (classifier.get_category_label(t.description_raw, t.amount) or "Other", t.date, t.id),
        )
    elif income_sort == "amount_category":
        all_income = sorted(
            all_income,
            key=lambda t: (classifier.get_category_label(t.description_raw, t.amount) or "Other", -t.amount, t.id),
        )

    _sq = (search_q or "").strip().lower()
    if _sq:
        all_expenses = [t for t in all_expenses if _sq in (t.description_raw or "").lower()]
        all_income = [t for t in all_income if _sq in (t.description_raw or "").lower()]
        total_income = sum(t.amount for t in all_income)
        total_expense = sum(t.amount for t in all_expenses)
        net = total_income + total_expense
        tx_count = len(all_expenses) + len(all_income)
        generosity_total = sum(abs(t.amount) for t in all_expenses if classifier.is_generosity(t.description_raw))
        discretionary_total = sum(abs(t.amount) for t in all_expenses if classifier.is_discretionary(t.description_raw))

    expense_categories = [classifier.get_category_label(t.description_raw, t.amount) or "Other" for t in all_expenses]
    income_categories = [classifier.get_category_label(t.description_raw, t.amount) or "Other" for t in all_income]

    category_names = classifier.get_all_category_names()
    expense_by_category: dict[str, float] = {name: 0.0 for name in category_names}
    expense_by_category["Other"] = 0.0
    for t in all_expenses:
        label = classifier.get_category_label(t.description_raw, t.amount) or "Other"
        expense_by_category[label] = expense_by_category.get(label, 0.0) + abs(t.amount)

    ym = f"{year}-{month:02d}"
    budget_rows = crud.list_budgets_for_user(db, user_id, ym, bank_account_id=account_id)
    budget_by_category: dict[str, float] = {}
    for b in budget_rows:
        if b.bank_account_id == account_id:
            budget_by_category[b.category_name] = b.amount_limit
    for b in budget_rows:
        if b.bank_account_id is None and b.category_name not in budget_by_category:
            budget_by_category[b.category_name] = b.amount_limit

    # Daily aggregates for timeseries charts
    income_by_day: dict[str, float] = defaultdict(float)
    expense_by_day: dict[str, float] = defaultdict(float)
    d = period_start
    while d <= period_end:
        key = d.isoformat()
        income_by_day[key]
        expense_by_day[key]
        d += timedelta(days=1)
    for t in all_expenses:
        expense_by_day[t.date.isoformat()] += abs(t.amount)
    for t in all_income:
        income_by_day[t.date.isoformat()] += t.amount
    chart_income_labels = sorted(income_by_day.keys())
    chart_income_data = [income_by_day[k] for k in chart_income_labels]
    chart_expense_labels = sorted(expense_by_day.keys())
    chart_expense_data = [expense_by_day[k] for k in chart_expense_labels]

    # Pivot-style: build party summary + transaction list from current period data (all_expenses / all_income)
    party_to_expenses: dict[str, list] = {}
    for t in all_expenses:
        party = classifier.get_party_name(t.description_raw, t.amount)
        party_to_expenses.setdefault(party, []).append(t)
    parties_outgoing_detailed = []
    for party_name, txs in party_to_expenses.items():
        total = sum(abs(t.amount) for t in txs)
        last_date = max(t.date for t in txs)
        parties_outgoing_detailed.append({
            "party_name": party_name,
            "total": total,
            "count": len(txs),
            "last_date": last_date,
            "transactions": txs,
        })
    parties_outgoing_detailed.sort(key=lambda x: x["total"], reverse=True)

    party_to_income: dict[str, list] = {}
    for t in all_income:
        party = classifier.get_party_name(t.description_raw, t.amount)
        party_to_income.setdefault(party, []).append(t)
    parties_incoming_detailed = []
    for party_name, txs in party_to_income.items():
        total = sum(t.amount for t in txs)
        last_date = max(t.date for t in txs)
        parties_incoming_detailed.append({
            "party_name": party_name,
            "total": total,
            "count": len(txs),
            "last_date": last_date,
            "transactions": txs,
        })
    parties_incoming_detailed.sort(key=lambda x: x["total"], reverse=True)

    # Keep legacy list for any code that expects it
    parties_outgoing = [(p["party_name"], p["total"], p["count"], p["last_date"]) for p in parties_outgoing_detailed]
    parties_incoming = [(p["party_name"], p["total"], p["count"], p["last_date"]) for p in parties_incoming_detailed]

    period_label = f"{month_name[month]} {year}"
    month_names = {i: month_name[i] for i in range(1, 13)}

    try:
        cash_withdrawal_total = crud.get_cash_withdrawal_total_for_user(
            db, user_id, period_start, period_end
        )
        receipt_total = crud.get_receipt_total_for_user(db, user_id, period_start, period_end)
    except Exception:
        cash_withdrawal_total = 0.0
        receipt_total = 0.0
    cash_receipts_ratio = (
        (receipt_total / cash_withdrawal_total) if cash_withdrawal_total else 1.0
    )
    RECEIPT_COVERAGE_THRESHOLD = 0.80
    cash_receipts_flag = (
        cash_withdrawal_total > 0 and cash_receipts_ratio < RECEIPT_COVERAGE_THRESHOLD
    )

    try:
        dashboard_alerts = alerts.compute_dashboard_alerts(
            db,
            user_id=user_id,
            account_id=account_id,
            period_start=period_start,
            period_end=period_end,
        )
    except Exception:
        dashboard_alerts = []

    response = templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "account_id": account_id,
            "accounts": accounts,
            "available_months": available_months,
            "month_names": month_names,
            "period_label": period_label,
            "month_name": month_name[month],
            "year": year,
            "month": month,
            "username": user,
            "total_income": float(total_income),
            "total_expense": float(total_expense),
            "net": float(net),
            "tx_count": int(tx_count),
            "generosity_total": float(generosity_total),
            "discretionary_total": float(discretionary_total),
            "all_expenses": all_expenses,
            "all_income": all_income,
            "expense_categories": expense_categories,
            "income_categories": income_categories,
            "expense_sort": expense_sort,
            "income_sort": income_sort,
            "chart_income_labels_json": json.dumps(chart_income_labels),
            "chart_income_data_json": json.dumps(chart_income_data),
            "chart_expense_labels_json": json.dumps(chart_expense_labels),
            "chart_expense_data_json": json.dumps(chart_expense_data),
            "parties_outgoing": parties_outgoing,
            "parties_incoming": parties_incoming,
            "parties_outgoing_detailed": parties_outgoing_detailed,
            "parties_incoming_detailed": parties_incoming_detailed,
            "no_accounts": False,
            "cash_withdrawal_total": float(cash_withdrawal_total),
            "receipt_total": float(receipt_total),
            "cash_receipts_flag": cash_receipts_flag,
            "receipt_coverage_pct": round(cash_receipts_ratio * 100, 0) if cash_withdrawal_total else 100,
            "expense_by_category": expense_by_category,
            "dashboard_alerts": dashboard_alerts,
            "search_q": search_q or "",
            "prev_month_income": prev_month_income,
            "prev_month_expense": prev_month_expense,
            "prev_month_label": prev_month_label,
            "budget_by_category": budget_by_category,
        },
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


@app.get("/api/insights/budget")
def api_budget_insights(
    account_id: int,
    period: str,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    """Pandas-backed category totals and daily expense series for the selected month."""
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not crud.get_bank_account_for_user(db, account_id, user_id):
        return JSONResponse({"error": "Account not found"}, status_code=404)
    try:
        y, m = period.strip().split("-")
        year, month = int(y), int(m)
    except (ValueError, AttributeError):
        return JSONResponse({"error": "Use period=YYYY-MM"}, status_code=400)
    period_start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    period_end = date(year, month, last_day)
    expenses = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "EXPENSE",
        )
        .all()
    )
    tuples = [(t.date, t.description_raw, t.amount) for t in expenses]
    return JSONResponse(insights.build_budget_insights_payload(tuples))


@app.get("/api/alerts")
def api_alerts(
    account_id: int,
    period: str,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    """JSON alerts for automation / cron (same logic as dashboard banners)."""
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if not crud.get_bank_account_for_user(db, account_id, user_id):
        return JSONResponse({"error": "Account not found"}, status_code=404)
    try:
        y, m = period.strip().split("-")
        year, month = int(y), int(m)
    except (ValueError, AttributeError):
        return JSONResponse({"error": "Use period=YYYY-MM"}, status_code=400)
    period_start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    period_end = date(year, month, last_day)
    return JSONResponse(
        {
            "period": period,
            "alerts": alerts.compute_dashboard_alerts(
                db,
                user_id=user_id,
                account_id=account_id,
                period_start=period_start,
                period_end=period_end,
            ),
        }
    )


@app.get("/api/credit/score")
def api_credit_score(
    user_id: int | None = Depends(get_current_user_id),
) -> JSONResponse:
    """Placeholder for bureau API integration (set CREDIT_API_KEY when you have a provider)."""
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    key = os.getenv("CREDIT_API_KEY", "").strip()
    if not key:
        return JSONResponse(
            {
                "enabled": False,
                "score": None,
                "history": [],
                "message": "Credit bureau integration not configured. Set CREDIT_API_KEY and wire your provider.",
            }
        )
    return JSONResponse(
        {
            "enabled": True,
            "score": None,
            "history": [],
            "message": "API key present; implement provider calls in fin_savvy_app/credit_api.py when ready.",
        }
    )


@app.get("/tax/report")
def tax_report_download(
    income: float,
    user_id: int | None = Depends(get_current_user_id),
) -> Response:
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    if income < 0:
        income = 0.0
    result = tax_calc.calculate_tax(income)
    body = tax_calc.format_tax_report_text(result)
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="finsavvy-tax-estimate.txt"',
        },
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    accounts = crud.list_bank_accounts(db, user_id)
    return templates.TemplateResponse(
        "upload.html",
        {"request": request, "accounts": accounts, "username": user, "error": None, "add_account_error": None},
    )


@app.post("/upload/add-account", response_model=None, response_class=HTMLResponse)
def upload_add_account(
    request: Request,
    name: str = Form(...),
    institution: str = Form(...),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    try:
        crud.create_bank_account(
            db, user_id,
            schemas.BankAccountCreate(name=name.strip(), institution=institution.strip(), currency="ZAR"),
        )
    except Exception:
        accounts = crud.list_bank_accounts(db, user_id)
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "accounts": accounts, "username": user, "error": None, "add_account_error": "Could not add account. Try again."},
            status_code=400,
        )
    return RedirectResponse(url="/upload", status_code=303)


@app.post("/upload", response_model=None, response_class=HTMLResponse)
async def upload_submit(
    request: Request,
    account_id: int = Form(...),
    period_start: date = Form(...),
    period_end: date = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    if not crud.get_bank_account_for_user(db, account_id, user_id):
        return RedirectResponse(url="/upload", status_code=303)

    content = await file.read()
    fname = (file.filename or "").lower()
    is_csv = fname.endswith(".csv") or (file.content_type or "").lower() in (
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
    )
    is_pdf = fname.endswith(".pdf") or (file.content_type or "").lower() in (
        "application/pdf",
        "application/octet-stream",
    )

    if is_csv:
        transactions = csv_parser.parse_bank_csv_bytes(content)
        if not transactions:
            accounts = crud.list_bank_accounts(db, user_id)
            return templates.TemplateResponse(
                "upload.html",
                {
                    "request": request,
                    "accounts": accounts,
                    "username": user,
                    "error": "Could not parse CSV. Use headers like Date, Description, Amount (or Debit/Credit).",
                },
                status_code=400,
            )
    elif is_pdf:
        with NamedTemporaryFile(delete=True, suffix=".pdf") as tmp:
            tmp.write(content)
            tmp.flush()
            parsed = pdf_parser.parse_standard_bank_statement(tmp.name)
            transactions = pdf_parser.to_transaction_models(parsed)
        if not transactions:
            accounts = crud.list_bank_accounts(db, user_id)
            return templates.TemplateResponse(
                "upload.html",
                {
                    "request": request,
                    "accounts": accounts,
                    "username": user,
                    "error": "Could not parse any transactions from PDF",
                },
                status_code=400,
            )
    else:
        accounts = crud.list_bank_accounts(db, user_id)
        return templates.TemplateResponse(
            "upload.html",
            {
                "request": request,
                "accounts": accounts,
                "username": user,
                "error": "Upload a PDF bank statement or a CSV export (Date, Description, Amount).",
            },
            status_code=400,
        )

    crud.create_statement_with_transactions(
        db,
        bank_account_id=account_id,
        period_start=period_start,
        period_end=period_end,
        source_file_name=file.filename,
        transactions=transactions,
    )

    return RedirectResponse(
        url=f"/?account_id={account_id}&period={period_start.year}-{period_start.month:02d}",
        status_code=303,
    )


os.makedirs(UPLOAD_RECEIPTS_DIR, exist_ok=True)
os.makedirs(UPLOAD_PAYSLIPS_DIR, exist_ok=True)


@app.get("/receipts", response_class=HTMLResponse)
def receipts_page(
    request: Request,
    account_id: int | None = None,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    accounts = crud.list_bank_accounts(db, user_id)
    account_id = request.query_params.get("account_id", type=int) or account_id
    if accounts and account_id is None:
        account_id = accounts[0].id
    if account_id and not crud.get_bank_account_for_user(db, account_id, user_id):
        account_id = accounts[0].id if accounts else None
    receipts = crud.list_receipts_for_user(db, user_id)
    linkable: list = []
    if account_id:
        end = date.today()
        start = end - timedelta(days=120)
        linkable = crud.list_transactions_for_linking(db, user_id, account_id, start, end)
    return templates.TemplateResponse(
        "receipts.html",
        {
            "request": request,
            "username": user,
            "receipts": receipts,
            "error": None,
            "accounts": accounts,
            "account_id": account_id or 0,
            "linkable_transactions": linkable,
        },
    )


@app.post("/receipts", response_model=None, response_class=HTMLResponse)
async def receipt_submit(
    request: Request,
    receipt_date: date = Form(...),
    amount: float = Form(...),
    description: str | None = Form(None),
    try_ocr: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    file_path = None
    amount_val = float(amount)
    desc_val = description.strip() if description else None
    use_ocr = try_ocr and try_ocr.lower() in ("1", "true", "on", "yes")
    if file and file.filename:
        ext = os.path.splitext(file.filename)[1] or ".bin"
        safe_ext = ext if ext.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".webp") else ".bin"
        user_dir = os.path.join(UPLOAD_RECEIPTS_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        file_path = f"{user_id}/{uuid.uuid4().hex}{safe_ext}"
        full_path = os.path.join(UPLOAD_RECEIPTS_DIR, file_path)
        content = await file.read()
        with open(full_path, "wb") as f:
            f.write(content)
        if use_ocr and safe_ext.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            guess = receipt_ocr.ocr_receipt_image(full_path)
            if guess:
                if guess.amount is not None:
                    amount_val = float(guess.amount)
                if not desc_val and guess.text_snippet:
                    desc_val = guess.text_snippet[:255]
    crud.create_receipt(
        db,
        user_id=user_id,
        date=receipt_date,
        amount=amount_val,
        description=desc_val,
        file_path=file_path,
    )
    acc = crud.list_bank_accounts(db, user_id)
    aid = acc[0].id if acc else 0
    return RedirectResponse(url=f"/receipts?account_id={aid}", status_code=303)


@app.get("/tax", response_class=HTMLResponse)
def tax_page(
    request: Request,
    income: str | None = None,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    tax_result = None
    if income is not None:
        try:
            annual = float(income)
            if annual >= 0:
                tax_result = tax_calc.calculate_tax(annual)
        except ValueError:
            pass
    payslips = crud.list_payslips_for_user(db, user_id)
    return templates.TemplateResponse(
        "tax.html",
        {
            "request": request,
            "username": user,
            "tax_result": tax_result,
            "payslips": payslips,
        },
    )


@app.post("/tax/payslips", response_model=None, response_class=HTMLResponse)
async def tax_payslip_upload(
    request: Request,
    file: UploadFile = File(...),
    period_label: str | None = Form(None),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    if not file.filename or not file.content_type:
        return RedirectResponse(url="/tax", status_code=303)
    ext = os.path.splitext(file.filename)[1] or ".pdf"
    safe_ext = ext if ext.lower() in (".pdf", ".png", ".jpg", ".jpeg") else ".pdf"
    user_dir = os.path.join(UPLOAD_PAYSLIPS_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    file_path = f"{user_id}/{uuid.uuid4().hex}{safe_ext}"
    full_path = os.path.join(UPLOAD_PAYSLIPS_DIR, file_path)
    content = await file.read()
    with open(full_path, "wb") as f:
        f.write(content)
    crud.create_payslip(db, user_id=user_id, file_path=file_path, period_label=period_label)
    return RedirectResponse(url="/tax", status_code=303)


@app.get("/tax/payslips/{payslip_id}/file")
def payslip_file(
    payslip_id: int,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> FileResponse:
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payslip = db.query(models.Payslip).filter(
        models.Payslip.id == payslip_id,
        models.Payslip.user_id == user_id,
    ).first()
    if not payslip:
        raise HTTPException(status_code=404, detail="Not found")
    full_path = os.path.join(UPLOAD_PAYSLIPS_DIR, payslip.file_path)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full_path, filename=os.path.basename(payslip.file_path))


@app.get("/credit", response_class=HTMLResponse)
def credit_page(
    request: Request,
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    credit_configured = bool(os.getenv("CREDIT_API_KEY", "").strip())
    return templates.TemplateResponse(
        "credit.html",
        {
            "request": request,
            "username": user,
            "credit_configured": credit_configured,
        },
    )


@app.get("/export/transactions.csv")
def export_transactions_csv(
    request: Request,
    account_id: int,
    period: str,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> Response:
    if user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    if not crud.get_bank_account_for_user(db, account_id, user_id):
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        y, m = period.strip().split("-")
        year, month = int(y), int(m)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="period=YYYY-MM required")
    period_start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    period_end = date(year, month, last_day)
    txs = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
        )
        .order_by(models.Transaction.date, models.Transaction.id)
        .all()
    )
    buf = StringIO()
    w = csv_module.writer(buf)
    w.writerow(["Date", "Description", "Amount", "Direction", "Category", "Party", "Cash withdrawal"])
    for t in txs:
        w.writerow(
            [
                t.date.isoformat(),
                (t.description_raw or "").replace("\n", " "),
                f"{t.amount:.2f}",
                t.direction,
                classifier.get_category_label(t.description_raw, t.amount) or "Other",
                classifier.get_party_name(t.description_raw, t.amount),
                "yes" if t.is_cash_withdrawal else "no",
            ]
        )
    body = buf.getvalue()
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="transactions-{period}.csv"',
        },
    )


@app.get("/budgets", response_class=HTMLResponse)
def budgets_page(
    request: Request,
    account_id: int | None = None,
    period: str | None = None,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    accounts = crud.list_bank_accounts(db, user_id)
    if not accounts:
        return templates.TemplateResponse(
            "budgets.html",
            {
                "request": request,
                "username": user,
                "accounts": [],
                "account_id": 0,
                "period": "",
                "budgets": [],
                "categories": classifier.get_all_category_names(),
                "error": None,
            },
        )
    if account_id is None:
        account_id = accounts[0].id
    if not crud.get_bank_account_for_user(db, account_id, user_id):
        account_id = accounts[0].id
    period = request.query_params.get("period") or period
    latest = (
        db.query(func.max(models.Transaction.date))
        .join(models.Statement)
        .filter(models.Statement.bank_account_id == account_id)
        .scalar()
    )
    if not period and latest:
        period = f"{latest.year}-{latest.month:02d}"
    if not period:
        period = f"{date.today().year}-{date.today().month:02d}"
    budgets = crud.list_budgets_for_user(db, user_id, period, bank_account_id=account_id)
    return templates.TemplateResponse(
        "budgets.html",
        {
            "request": request,
            "username": user,
            "accounts": accounts,
            "account_id": account_id,
            "period": period,
            "budgets": budgets,
            "categories": classifier.get_all_category_names() + ["Other"],
            "error": None,
        },
    )


@app.post("/budgets", response_model=None, response_class=HTMLResponse)
def budgets_save(
    request: Request,
    account_id: int = Form(...),
    year_month: str = Form(...),
    category_name: str = Form(...),
    amount_limit: float = Form(...),
    scope: str = Form("account"),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    if not crud.get_bank_account_for_user(db, account_id, user_id):
        return RedirectResponse(url="/budgets", status_code=303)
    bank_scope = None if scope == "all" else account_id
    crud.upsert_monthly_budget(
        db,
        user_id=user_id,
        category_name=category_name.strip(),
        year_month=year_month.strip(),
        amount_limit=max(0.0, amount_limit),
        bank_account_id=bank_scope,
    )
    return RedirectResponse(url=f"/budgets?account_id={account_id}&period={year_month}", status_code=303)


@app.post("/budgets/{budget_id}/delete", response_model=None, response_class=HTMLResponse)
def budgets_delete(
    budget_id: int,
    request: Request,
    account_id: int = Form(...),
    period: str = Form(...),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    crud.delete_monthly_budget(db, budget_id, user_id)
    return RedirectResponse(url=f"/budgets?account_id={account_id}&period={period}", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "username": user,
            "categories": classifier.get_all_category_names(),
            "parties": classifier.get_all_party_names(),
        },
    )


@app.get("/account/password", response_class=HTMLResponse)
def account_password_page(
    request: Request,
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        "account_password.html",
        {"request": request, "username": user, "error": None},
    )


@app.post("/account/password", response_model=None, response_class=HTMLResponse)
def account_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    u = crud.get_user_by_username(db, user)
    if not u or not auth.verify_password(current_password, u.password_hash):
        return templates.TemplateResponse(
            "account_password.html",
            {"request": request, "username": user, "error": "Current password is incorrect"},
            status_code=400,
        )
    if len(new_password) < 6:
        return templates.TemplateResponse(
            "account_password.html",
            {"request": request, "username": user, "error": "New password must be at least 6 characters"},
            status_code=400,
        )
    crud.update_user_password(db, user_id, new_password)
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/receipts/{receipt_id}/link", response_model=None, response_class=HTMLResponse)
def receipt_link_transaction(
    receipt_id: int,
    request: Request,
    transaction_id: str = Form(""),
    account_id: int = Form(...),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    tid: int | None
    try:
        tid = int(transaction_id) if transaction_id.strip() else None
    except ValueError:
        tid = None
    crud.set_receipt_transaction_link(db, receipt_id, user_id, tid)
    return RedirectResponse(url=f"/receipts?account_id={account_id}", status_code=303)


@app.get("/receipts/{receipt_id}/file")
def receipt_file(
    receipt_id: int,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> FileResponse:
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    receipt = db.query(models.Receipt).filter(
        models.Receipt.id == receipt_id,
        models.Receipt.user_id == user_id,
    ).first()
    if not receipt or not receipt.file_path:
        raise HTTPException(status_code=404, detail="File not found")
    full_path = os.path.join(UPLOAD_RECEIPTS_DIR, receipt.file_path)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full_path, filename=os.path.basename(receipt.file_path))


@app.post("/bank-accounts", response_model=schemas.BankAccountRead)
def create_bank_account(
    request: Request,
    data: schemas.BankAccountCreate,
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> schemas.BankAccountRead:
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return crud.create_bank_account(db, user_id, data)


@app.post(
    "/bank-accounts/{account_id}/statements/upload_pdf",
    response_model=schemas.StatementRead,
)
async def upload_statement_pdf(
    account_id: int,
    period_start: date,
    period_end: date,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> schemas.StatementRead:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    with NamedTemporaryFile(delete=True, suffix=".pdf") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp.flush()

        parsed = pdf_parser.parse_standard_bank_statement(tmp.name)
        transactions = pdf_parser.to_transaction_models(parsed)

    if not transactions:
        raise HTTPException(status_code=400, detail="Could not parse any transactions from PDF")

    statement = crud.create_statement_with_transactions(
        db,
        bank_account_id=account_id,
        period_start=period_start,
        period_end=period_end,
        source_file_name=file.filename,
        transactions=transactions,
    )
    return statement


@app.get(
    "/statements/{statement_id}/transactions",
    response_model=list[schemas.TransactionRead],
)
def list_statement_transactions(
    statement_id: int,
    db: Session = Depends(get_db),
) -> list[schemas.TransactionRead]:
    txs = crud.get_transactions_for_statement(db, statement_id)
    if not txs:
        raise HTTPException(status_code=404, detail="No transactions found for this statement")
    return txs