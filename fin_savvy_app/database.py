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
    _seed_default_user()


def _ensure_schema_patches() -> None:
    """Add columns missing on existing DBs (PostgreSQL)."""
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        if "receipts" not in insp.get_table_names():
            return
        cols = {c["name"] for c in insp.get_columns("receipts")}
        if "transaction_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE receipts ADD COLUMN transaction_id INTEGER"))
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

