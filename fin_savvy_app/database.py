import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://fin_savvy_user:fin_savvy_password@localhost:5432/fin_savvy",
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_schema_patches()
    _backfill_budget_commitments()
    _seed_default_user()


def _backfill_budget_commitments() -> None:
    """Create legacy commitment rows for months that already had budget lines before commitments existed."""
    try:
        from . import crud, models
    except Exception:
        return
    db = SessionLocal()
    try:
        pairs = (
            db.query(
                models.MonthlyBudget.user_id,
                models.MonthlyBudget.year_month,
                models.MonthlyBudget.bank_account_id,
            )
            .filter(models.MonthlyBudget.bank_account_id.isnot(None))
            .distinct()
            .all()
        )
        for uid, ym, bid in pairs:
            if not ym or bid is None:
                continue
            sk = f"acc:{bid}"
            if crud.get_budget_commitment(db, int(uid), str(ym), sk):
                continue
            rows = crud.list_budgets_for_user(db, int(uid), str(ym), bank_account_id=int(bid))
            if not rows:
                continue
            prov = crud.get_budget_provenance(db, int(uid), str(ym), sk)
            if len(rows) < 2 and (prov is None or prov == "unknown"):
                continue
            tot = sum(float(r.amount_limit) for r in rows)
            crud.upsert_budget_commitment(
                db,
                user_id=int(uid),
                year_month=str(ym),
                scope_key=sk,
                mode="legacy",
                system_recommended_total=None,
                committed_total=float(tot),
            )
    except Exception:
        pass
    finally:
        db.close()


def _ensure_schema_patches() -> None:
    """Add columns missing on existing DBs (PostgreSQL)."""
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        tables = insp.get_table_names()
        if "receipts" in tables:
            cols = {c["name"] for c in insp.get_columns("receipts")}
            if "transaction_id" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE receipts ADD COLUMN transaction_id INTEGER"))
        if "budget_month_provenance" not in tables:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        CREATE TABLE budget_month_provenance (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES users(id),
                            year_month VARCHAR(7) NOT NULL,
                            scope_key VARCHAR(32) NOT NULL,
                            origin VARCHAR(32) NOT NULL DEFAULT 'unknown',
                            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                            CONSTRAINT uq_budget_month_provenance UNIQUE (user_id, year_month, scope_key)
                        )
                        """
                    )
                )
        if "monthly_budgets" in tables:
            mb_cols = {c["name"] for c in insp.get_columns("monthly_budgets")}
            if "other_detail" not in mb_cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE monthly_budgets ADD COLUMN other_detail VARCHAR(120)"))
            if "budget_bucket" not in mb_cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE monthly_budgets ADD COLUMN budget_bucket VARCHAR(16)"))
        if "budget_month_commitment" not in tables:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        CREATE TABLE budget_month_commitment (
                            id SERIAL PRIMARY KEY,
                            user_id INTEGER NOT NULL REFERENCES users(id),
                            year_month VARCHAR(7) NOT NULL,
                            scope_key VARCHAR(32) NOT NULL,
                            mode VARCHAR(24) NOT NULL DEFAULT 'unknown',
                            system_recommended_total DOUBLE PRECISION,
                            committed_total DOUBLE PRECISION,
                            committed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                            carryover_shortfall_streak INTEGER NOT NULL DEFAULT 0,
                            CONSTRAINT uq_budget_month_commitment UNIQUE (user_id, year_month, scope_key)
                        )
                        """
                    )
                )
        if "budget_month_commitment" in tables:
            bmc_cols = {c["name"] for c in insp.get_columns("budget_month_commitment")}
            if "carryover_shortfall_streak" not in bmc_cols:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE budget_month_commitment ADD COLUMN carryover_shortfall_streak INTEGER NOT NULL DEFAULT 0"
                        )
                    )
        if "payslips" in tables:
            ps_cols = {c["name"] for c in insp.get_columns("payslips")}
            if "gross_pay" not in ps_cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE payslips ADD COLUMN gross_pay DOUBLE PRECISION"))
            if "net_pay" not in ps_cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE payslips ADD COLUMN net_pay DOUBLE PRECISION"))
            if "paye_estimate" not in ps_cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE payslips ADD COLUMN paye_estimate DOUBLE PRECISION"))
    except Exception:
        pass


def _seed_default_user() -> None:
    from . import auth, models

    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            user = models.User(
                username="mfundo",
                email="mfundo@example.com",
                password_hash=auth.hash_password("password123"),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            if db.query(models.BankAccount).filter(models.BankAccount.user_id == user.id).count() == 0:
                db.add(
                    models.BankAccount(
                        user_id=user.id,
                        name="Current Account",
                        institution="My Bank",
                        currency="ZAR",
                    )
                )
                db.commit()
        else:
            user = db.query(models.User).filter(models.User.username == "mfundo").first()
            if user and db.query(models.BankAccount).filter(models.BankAccount.user_id == user.id).count() == 0:
                db.add(
                    models.BankAccount(
                        user_id=user.id,
                        name="Current Account",
                        institution="My Bank",
                        currency="ZAR",
                    )
                )
                db.commit()
    finally:
        db.close()

