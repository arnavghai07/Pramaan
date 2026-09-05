"""
storage/models.py — PRAMAAN
============================
The ORM table behind inspection history.

TWO KINDS OF COLUMN, ON PURPOSE
--------------------------------
1. The FULL result, as JSON: rule6_result_json, rule7_result_json,
   findings_json. These are the record of what the engine actually decided,
   stored whole so a saved inspection can be replayed through the same
   Pydantic models the live API returns and nothing is lost to a schema
   that was designed before the question was asked.

2. DENORMALISED summary columns: overall_status, manufacturer, mrp,
   net_quantity, mfg_date, rule7_verdict. Every one of these already exists
   inside the JSON above. They are copied out because a list view, a search
   box and a district dashboard cannot filter or aggregate across a JSON
   blob in SQLite without a full table scan and a parse per row. They are a
   read-optimisation, never a second source of truth: repository.py fills
   them from the JSON at write time and nothing updates one without the
   other.

WHY THE OVERLAY PNG IS NOT IN HERE
-----------------------------------
Rule 7's evidence overlay travels over HTTP as base64 inside the Rule 7
result. Stored that way it would put a few hundred KB of base64 in every
row, which every list query would then drag off disk to answer a question
about a verdict string. It is written to the evidence directory as a real
PNG instead, and re-attached on read — see repository.py.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text)
from sqlalchemy.orm import Mapped, mapped_column

from storage.database import Base

# The two roles the prototype recognises. Kept as plain strings rather than a
# database ENUM: SQLite has no native enum, and a CHECK-constrained enum would
# need a migration to add the third role (a state Controller) that Phase I
# will want. Validated at the API boundary instead — see api/auth.py.
ROLE_ADMIN = "ADMIN"
ROLE_INSPECTOR = "INSPECTOR"
ROLES = (ROLE_ADMIN, ROLE_INSPECTOR)


def _utcnow() -> datetime:
    """
    Naive UTC. SQLite has no timezone-aware storage, so an aware datetime
    would have its tzinfo silently dropped on the way in and come back
    looking like local time. Storing naive UTC by convention and marking it
    as UTC at the API boundary (api/models.py serialises with a trailing Z)
    keeps the round-trip unambiguous.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    """
    An officer who can sign in. Passwords are never stored — only a bcrypt
    hash, which is a one-way function with a per-user salt baked into the
    output string, so two officers who choose the same password still get
    different hashes and neither can be recovered from the database.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<User {self.username!r} role={self.role!r}>"


class Inspection(Base):
    """One completed POST /inspect, with its evidence."""

    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Never inferred from the photo — the VLM has no product-name field and
    # inventing one would be a value the pack does not carry. Supplied by
    # the client or left null.
    product_name: Mapped[Optional[str]] = mapped_column(String(200), index=True)

    # --- verdict, as decided by engine/verdict.py. Copied, never recomputed.
    overall_status: Mapped[str] = mapped_column(String(32), index=True)
    mandatory_present: Mapped[int] = mapped_column(Integer)
    mandatory_total: Mapped[int] = mapped_column(Integer)
    rule7_verdict: Mapped[Optional[str]] = mapped_column(String(16), index=True)

    # --- who ran it. inspector_name is denormalised alongside the id for the
    #     same reason as the columns below: a history list should not need a
    #     join to say who inspected a pack, and the record of WHO signed a
    #     verdict must survive that officer's account being removed.
    inspector_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True)
    inspector_name: Mapped[Optional[str]] = mapped_column(String(120))

    # --- denormalised search/aggregation columns (see module docstring)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(300), index=True)
    mrp: Mapped[Optional[float]] = mapped_column(Float)
    net_quantity: Mapped[Optional[str]] = mapped_column(String(64))
    mfg_date: Mapped[Optional[str]] = mapped_column(String(64))

    # --- the full engine output
    rule6_result_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    rule7_result_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    findings_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    # --- evidence, stored RELATIVE to storage.database.DATA_DIR so moving or
    #     copying the data directory does not invalidate every stored path.
    rule6_image_path: Mapped[Optional[str]] = mapped_column(Text)
    rule7_image_path: Mapped[Optional[str]] = mapped_column(Text)
    rule7_overlay_path: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return (f"<Inspection id={self.id} status={self.overall_status!r} "
                f"created_at={self.created_at!r}>")
