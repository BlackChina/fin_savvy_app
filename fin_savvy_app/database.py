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
    _seed_default_user()


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
    finally:
        db.close()

