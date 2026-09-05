"""
api/auth.py — PRAMAAN
======================
Sign-in, token issuing, and the dependencies that protect routes.

THE SHAPE OF THIS, AND WHY IT IS THIS SMALL
--------------------------------------------
An inspection is an enforcement act, so it has to be attributable to a named
officer — that is the whole reason authentication exists here, not a checkbox.
What it does NOT need before a prototype demo is refresh-token rotation, a
session table, password-reset email, or an OAuth provider. So: one bearer
token, signed HS256, carrying the user id and role, checked on every
protected request. Roughly eighty lines of moving parts, all visible.

WHERE THE SIGNING SECRET COMES FROM
------------------------------------
PRAMAAN_JWT_SECRET if it is set. Otherwise a random secret is generated once
and persisted to data/jwt_secret (gitignored, 0600 where the OS supports it).

That fallback exists because the alternative for a zero-config local demo is
a default secret committed to the repository, and a signing key in version
control means anyone with the source can mint a valid admin token. Generating
per-machine keeps START_BACKEND.bat working with no setup while never
shipping a key. Because it is persisted rather than regenerated per process,
a server restart does not sign every officer out mid-inspection.

The secret is only ever used server-side. The browser receives a signed
token, never the key that signed it.
"""
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from api.models import LoginRequest, TokenResponse, UserOut
from storage import users as users_repo
from storage.database import DATA_DIR, get_db
from storage.models import ROLE_ADMIN, User

ALGORITHM = "HS256"
TOKEN_TTL_HOURS = int(os.environ.get("PRAMAAN_TOKEN_TTL_HOURS", "12"))

router = APIRouter(prefix="/auth", tags=["auth"])

# auto_error=False so a missing header reaches our own handler and produces
# PRAMAAN's error shape ({"detail": ...}) with a message an inspector can act
# on, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)


def _load_or_create_secret() -> str:
    configured = os.environ.get("PRAMAAN_JWT_SECRET")
    if configured:
        return configured

    key_path = DATA_DIR / "jwt_secret"
    if key_path.exists():
        return key_path.read_text().strip()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    key_path.write_text(generated)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # best effort; Windows ACLs don't map onto POSIX modes
    print(f"[auth] generated a signing secret at {key_path}. Set "
          "PRAMAAN_JWT_SECRET to control it explicitly.", file=sys.stderr)
    return generated


_SECRET: Optional[str] = None


def secret() -> str:
    """Resolved once per process, lazily, so importing this module is cheap."""
    global _SECRET
    if _SECRET is None:
        _SECRET = _load_or_create_secret()
    return _SECRET


def create_token(user: User) -> tuple[str, int]:
    """
    Returns (token, seconds_until_expiry).

    The role is carried in the token for the CLIENT's benefit (to lay out a
    menu). The server re-reads the role from the database on every request —
    see current_user() — so an officer demoted from ADMIN loses admin access
    immediately rather than whenever their token happens to expire.
    """
    expires_delta = timedelta(hours=TOKEN_TTL_HOURS)
    expires_at = datetime.now(timezone.utc) + expires_delta
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret(), algorithm=ALGORITHM), int(
        expires_delta.total_seconds())


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """
    The signed-in officer, or 401. Attach to any route that must be
    attributable or must not be public.
    """
    if credentials is None:
        raise _unauthorized("sign in to use this endpoint")

    try:
        payload = jwt.decode(credentials.credentials, secret(),
                             algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("session expired — sign in again")
    except jwt.InvalidTokenError:
        raise _unauthorized("invalid session token")

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise _unauthorized("invalid session token")

    user = users_repo.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("this account is no longer active")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """
    ADMIN-only routes. Checked against the database row current_user() just
    loaded, never against the role claim inside the token.
    """
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="this action requires an administrator account")
    return user


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """
    Exchange credentials for a bearer token.

    One identical message for every failure — unknown user, wrong password,
    deactivated account. Naming which one it was would confirm to an
    attacker which usernames exist.
    """
    user = users_repo.authenticate(db, body.username, body.password)
    if user is None:
        raise _unauthorized("incorrect username or password")

    token, expires_in = create_token(user)
    return {"access_token": token, "token_type": "bearer",
            "expires_in": expires_in, "user": UserOut.model_validate(user)}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    """
    Who the caller is, according to the server. The frontend calls this on
    load to re-establish a session from a stored token instead of trusting
    what it cached about the user.
    """
    return user
