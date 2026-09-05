"""
storage/database.py — PRAMAAN
==============================
Database connection, session management and one-time schema creation.

WHY SQLITE, AND WHY A SEPARATE PACKAGE
---------------------------------------
The demo must run from a clean clone with no service to start and no
credentials to configure, so the store is a single file under data/.
BUILD_PLAN.md's Phase H still moves this to PostgreSQL/Supabase with RLS;
keeping every database concern inside storage/ — never inside engine/ and
never inside a route handler — is what makes that migration a change of
engine URL and session factory rather than a rewrite of api/main.py.

engine/ deliberately does not import anything from here. The vision engine
stays a pure function of its inputs: it can be run from the CLI on a laptop
with no database at all, exactly as it can today.

LAYOUT ON DISK
--------------
    data/
      pramaan.db              the SQLite file
      evidence/<id>/          one directory per inspection, see repository.py

Both paths are overridable with PRAMAAN_DATA_DIR (and the URL alone with
PRAMAAN_DATABASE_URL) so a test run or a second instance can be pointed
somewhere else without editing code.
"""
import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("PRAMAAN_DATA_DIR", _REPO_ROOT / "data")).resolve()
EVIDENCE_DIR = DATA_DIR / "evidence"
DB_PATH = DATA_DIR / "pramaan.db"

DATABASE_URL = os.environ.get("PRAMAAN_DATABASE_URL", f"sqlite:///{DB_PATH}")


class Base(DeclarativeBase):
    """Declarative base for every ORM model in storage/models.py."""


# check_same_thread=False: FastAPI runs sync endpoints in a worker threadpool,
# so a connection opened on one thread can be used on another. Safe here
# because a Session is never shared between requests — each one gets its own
# from get_db()/session_scope() below.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)

# expire_on_commit=False so a returned ORM object can still be read after its
# session closes — the /inspect handler commits, then serialises the row's id
# into the response it was already going to send.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """
    Create data/, data/evidence/ and any missing tables. Idempotent, and
    called on application startup (api/main.py's lifespan), which is what
    "handle database initialisation automatically" means in practice: a
    fresh clone needs no migration step before the first scan.

    create_all() only ever ADDS missing tables. It does not alter an
    existing one, so a future column change needs a real migration (Alembic
    in Phase H) rather than a silent schema drift. _add_missing_columns()
    below is the one narrow exception — see its docstring.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    from storage import models  # noqa: F401  — registers the tables on Base
    Base.metadata.create_all(engine)
    _add_missing_columns()


#: Columns added to an already-created table after the fact, as
#: (table, column, SQL type). ADDITIVE ONLY: a nullable column with no
#: default, which SQLite can add in place without rewriting or reading a
#: single existing row. Nothing here may drop, rename or retype a column —
#: that is a real migration and belongs to Alembic in Phase H. The point is
#: that an inspector who has been recording evidence for weeks keeps every
#: row, and the new column simply reads NULL on all of them, which is the
#: honest answer: those inspections were never analysed.
_ADDITIVE_COLUMNS = [
    ("inspections", "analysis_json", "JSON"),
]


def _add_missing_columns() -> None:
    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, sql_type in _ADDITIVE_COLUMNS:
            if table not in existing_tables:
                continue        # create_all() just made it, with the column
            have = {c["name"] for c in inspector.get_columns(table)}
            if column in have:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def get_db():
    """FastAPI dependency: one Session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Session:
    """
    Same lifecycle as get_db() for code that is not a route dependency —
    the persistence step inside POST /inspect, which is an async handler
    and so cannot take a generator dependency's cleanup for granted.
    Commits on success, rolls back on any exception.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
