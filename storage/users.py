"""
storage/users.py — PRAMAAN
===========================
Password hashing and the user queries behind sign-in.

WHY BCRYPT, AND WHY NOTHING CLEVERER
-------------------------------------
bcrypt is deliberately slow and salts every hash individually, which is what
makes a stolen database of hashes expensive rather than instant to attack.
A plain SHA-256 would be none of those things. bcrypt is Apache-2.0, so it
clears CLAUDE.md rule 6 (Apache/BSD/MIT only).

Password comparison goes through bcrypt.checkpw(), which compares in
constant time — an ordinary `==` on secrets leaks how many leading bytes
matched through how long it took to fail.

bcrypt truncates at 72 bytes. Longer passwords are rejected at the API
boundary rather than silently accepted and half-ignored, because a password
that is not fully checked is a password the user thinks is stronger than it
is.
"""
import os
import sys
from typing import Optional

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from storage.models import ROLE_ADMIN, ROLE_INSPECTOR, User

BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    """bcrypt hash, salt included in the returned string."""
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"password must be at most {BCRYPT_MAX_BYTES} bytes; bcrypt would "
            "silently ignore everything past that")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check. False for any malformed stored hash, never raises."""
    try:
        return bcrypt.checkpw(password.encode()[:BCRYPT_MAX_BYTES],
                              password_hash.encode())
    except (ValueError, TypeError):
        return False


def get_by_username(db: Session, username: str) -> Optional[User]:
    return db.scalar(select(User).where(User.username == username))


def get_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.get(User, user_id)


def create_user(db: Session, *, username: str, password: str, role: str,
                full_name: Optional[str] = None) -> User:
    user = User(username=username, password_hash=hash_password(password),
                role=role, full_name=full_name)
    db.add(user)
    db.flush()
    return user


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    """
    The user for these credentials, or None.

    Returns None for every failure — unknown username, wrong password,
    deactivated account — and the caller reports one identical message for
    all of them. Distinguishing "no such user" from "wrong password" tells
    an attacker which usernames are real.
    """
    user = get_by_username(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# ---------------------------------------------------------------------------
# Demo seeding
# ---------------------------------------------------------------------------

DEMO_USERS = [
    ("admin", "admin123", ROLE_ADMIN, "Demo Administrator"),
    ("inspector", "inspector123", ROLE_INSPECTOR, "Demo Field Inspector"),
]


def seed_demo_users(db: Session) -> list[str]:
    """
    Create the two demo accounts, but ONLY when the users table is empty.

    Guarding on emptiness rather than on each username is what stops this
    from resurrecting a deleted demo account, or from resetting a password
    an operator deliberately changed, every time the server restarts.

    These are development credentials and are printed to the server log on
    purpose. PRAMAAN_DISABLE_DEMO_USERS=1 turns seeding off entirely; any
    deployment beyond a local demo should set it.
    """
    if os.environ.get("PRAMAAN_DISABLE_DEMO_USERS") == "1":
        return []
    if db.scalar(select(func.count()).select_from(User)):
        return []

    created = []
    for username, password, role, full_name in DEMO_USERS:
        create_user(db, username=username, password=password, role=role,
                    full_name=full_name)
        created.append(f"{username} / {password}  ({role})")

    print("[auth] seeded demo users — CHANGE THESE OUTSIDE A LOCAL DEMO:",
          file=sys.stderr)
    for line in created:
        print(f"[auth]   {line}", file=sys.stderr)
    return created


__all__ = ["ROLE_ADMIN", "ROLE_INSPECTOR", "authenticate", "create_user",
           "get_by_id", "get_by_username", "hash_password", "seed_demo_users",
           "verify_password"]
