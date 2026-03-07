from calendar import month_name, monthrange
from collections import defaultdict
from datetime import date, timedelta
from tempfile import NamedTemporaryFile
import json
import os
import uuid

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import auth, classifier, crud, models, pdf_parser, schemas, tax_calc
from .database import SessionLocal, init_db


app = FastAPI(title="Fin Savvy API")

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
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
    init_db()


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
    try:
        return _render_dashboard(request, user, user_id, account_id, period, db, expense_sort, income_sort)
    except Exception as e:
        tb = traceback.format_exc()
        return HTMLResponse(
            content=f"<pre style='background:#1e293b;color:#e2e8f0;padding:1rem;overflow:auto;'>{tb}</pre>",
            status_code=500,
        )


def _render_dashboard(request, user, user_id, account_id, period, db, expense_sort: str = "date", income_sort: str = "date"):
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
            },
        )

    # Only fall back to latest month when no period was given or parsing failed.
    # Never override an explicit period from the URL so the dropdown selection is respected.
    if year is None or month is None:
        year, month = latest_date.year, latest_date.month

    period_start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    period_end = date(year, month, last_day)

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
            key=lambda t: (classifier.get_category_label(t.description_raw) or "Other", t.date, t.id),
        )
    elif expense_sort == "amount_category":
        all_expenses = sorted(
            all_expenses,
            key=lambda t: (classifier.get_category_label(t.description_raw) or "Other", -abs(t.amount), t.id),
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
            key=lambda t: (classifier.get_category_label(t.description_raw) or "Other", t.date, t.id),
        )
    elif income_sort == "amount_category":
        all_income = sorted(
            all_income,
            key=lambda t: (classifier.get_category_label(t.description_raw) or "Other", -t.amount, t.id),
        )

    expense_categories = [classifier.get_category_label(t.description_raw) or "Other" for t in all_expenses]
    income_categories = [classifier.get_category_label(t.description_raw) or "Other" for t in all_income]

    category_names = classifier.get_all_category_names()
    expense_by_category: dict[str, float] = {name: 0.0 for name in category_names}
    expense_by_category["Other"] = 0.0
    for t in all_expenses:
        label = classifier.get_category_label(t.description_raw) or "Other"
        expense_by_category[label] = expense_by_category.get(label, 0.0) + abs(t.amount)

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
        party = classifier.get_party_name(t.description_raw)
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
        party = classifier.get_party_name(t.description_raw)
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
        },
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return response


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

    if file.content_type not in ("application/pdf", "application/octet-stream"):
        accounts = crud.list_bank_accounts(db, user_id)
        return templates.TemplateResponse(
            "upload.html",
            {
                "request": request,
                "accounts": accounts,
                "username": user,
                "error": "File must be a PDF",
            },
            status_code=400,
        )

    with NamedTemporaryFile(delete=True, suffix=".pdf") as tmp:
        content = await file.read()
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
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    receipts = crud.list_receipts_for_user(db, user_id)
    return templates.TemplateResponse(
        "receipts.html",
        {"request": request, "username": user, "receipts": receipts, "error": None},
    )


@app.post("/receipts", response_model=None, response_class=HTMLResponse)
async def receipt_submit(
    request: Request,
    receipt_date: date = Form(...),
    amount: float = Form(...),
    description: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user_id: int | None = Depends(get_current_user_id),
):
    user = request.session.get("user")
    if not user or user_id is None:
        return RedirectResponse(url="/login", status_code=303)
    file_path = None
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
    crud.create_receipt(
        db, user_id=user_id, date=receipt_date, amount=amount,
        description=description.strip() or None if description else None, file_path=file_path,
    )
    return RedirectResponse(url="/receipts", status_code=303)


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
    return templates.TemplateResponse(
        "credit.html",
        {"request": request, "username": user},
    )


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