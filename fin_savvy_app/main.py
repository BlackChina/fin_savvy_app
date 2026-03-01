from calendar import month_name, monthrange
from datetime import date
from tempfile import NamedTemporaryFile
import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import auth, classifier, crud, models, pdf_parser, schemas
from .database import SessionLocal, init_db


app = FastAPI(title="Fin Savvy API")

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

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


@app.post("/login", response_model=None)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
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


@app.post("/register", response_model=None)
async def register_submit(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
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
    crud.create_user(db, username=username, email=email, password=password)
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


@app.post("/reset-password", response_model=None)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
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
) -> HTMLResponse:
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    accounts = crud.list_bank_accounts(db)
    available_months = crud.get_available_months(db, account_id)

    latest_date = (
        db.query(func.max(models.Transaction.date))
        .join(models.Statement)
        .filter(models.Statement.bank_account_id == account_id)
        .scalar()
    )

    year, month = None, None
    if period:
        try:
            y, m = period.split("-")
            year, month = int(y), int(m)
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
                "top_expenses": [],
                "recent_tx": [],
                "username": user,
            },
        )

    if year is None or month is None:
        year, month = latest_date.year, latest_date.month
    elif available_months and (year, month) not in available_months:
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

    top_expenses = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(
            models.Statement.bank_account_id == account_id,
            models.Transaction.date >= period_start,
            models.Transaction.date <= period_end,
            models.Transaction.direction == "EXPENSE",
        )
        .order_by(models.Transaction.amount.asc())
        .limit(10)
        .all()
    )

    recent_tx = (
        db.query(models.Transaction)
        .join(models.Statement)
        .filter(models.Statement.bank_account_id == account_id)
        .order_by(models.Transaction.date.desc(), models.Transaction.id.desc())
        .limit(15)
        .all()
    )

    period_label = f"{month_name[month]} {year}"
    month_names = {i: month_name[i] for i in range(1, 13)}

    return templates.TemplateResponse(
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
            "top_expenses": top_expenses,
            "recent_tx": recent_tx,
        },
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    accounts = crud.list_bank_accounts(db)
    return templates.TemplateResponse(
        "upload.html",
        {"request": request, "accounts": accounts, "username": user, "error": None},
    )


@app.post("/upload", response_model=None)
async def upload_submit(
    request: Request,
    account_id: int = Form(...),
    period_start: date = Form(...),
    period_end: date = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    if file.content_type not in ("application/pdf", "application/octet-stream"):
        accounts = crud.list_bank_accounts(db)
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
        accounts = crud.list_bank_accounts(db)
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


@app.post("/bank-accounts", response_model=schemas.BankAccountRead)
def create_bank_account(
    data: schemas.BankAccountCreate,
    db: Session = Depends(get_db),
) -> schemas.BankAccountRead:
    return crud.create_bank_account(db, data)


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