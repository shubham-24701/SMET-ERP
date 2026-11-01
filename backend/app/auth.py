"""Sign-in, sessions and the permission check every protected route uses.

Sessions are opaque random tokens in an HttpOnly cookie; only their SHA-256 is stored, so a copy
of the database can't be used to sign in. Writes also need the CSRF header (main.py).
"""

import hashlib
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import record
from app.config import get_settings
from app.db import get_db
from app.models import AuthSession, Company, Role, RolePermission, User, UserRole, utcnow
from app.rbac import sync_templates

SESSION_COOKIE = "smet_session"  # unique names: browsers share cookies across localhost ports
CSRF_COOKIE = "smet_csrf"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

router = APIRouter(prefix="/auth", tags=["auth"])
hasher = PasswordHasher()
_DUMMY_HASH = hasher.hash("timing-equaliser")  # verified when the email is unknown: same timing
_HASHING = threading.BoundedSemaphore(4)  # each check uses 64 MiB; a login burst can't take GBs

# shortcut: in memory, per process, resets on restart; move to the database before running
# more than one API process.
_attempts: dict[str, list[float]] = defaultdict(list)
LOGIN_LIMIT, LOGIN_WINDOW_S, CLIENT_LOGIN_LIMIT = 5, 60, 30
MAX_KEYS = 10_000


def rate_limit(key: str, limit: int, window_s: int, message: str) -> None:
    now = time.monotonic()
    if len(_attempts) > MAX_KEYS:  # bounded memory: forget keys idle for over an hour
        for k in [k for k, ts in _attempts.items() if not ts or now - ts[-1] > 3600]:
            del _attempts[k]
    recent = [t for t in _attempts[key] if now - t < window_s]
    if len(recent) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, message, headers={"Retry-After": str(window_s)}
        )
    _attempts[key] = [*recent, now]


def check_password(v: str) -> str:
    if len(v) < 12:
        raise ValueError("Use at least 12 characters")
    if len(set(v)) < 5:
        raise ValueError("Use a less repetitive password")
    return v


class SetupIn(BaseModel):
    company_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)
    ]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(max_length=256)
    _pw = field_validator("password")(check_password)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)
    _pw = field_validator("new_password")(check_password)


@dataclass
class Principal:
    user: User
    session_id: object
    roles: list[str]
    permissions: frozenset[str]

    def can(self, perm: str) -> bool:
        return perm in self.permissions


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_access(db: Session, user_id) -> tuple[list[str], frozenset[str]]:
    """Role names and the union of their permissions, read fresh on every request, so a role
    change takes effect on the user's next click."""
    rows = db.execute(
        select(Role.name, RolePermission.permission)
        .join(UserRole, UserRole.role_id == Role.id)
        .outerjoin(RolePermission, RolePermission.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    ).all()
    return sorted({name for name, _ in rows}), frozenset(p for _, p in rows if p)


def start_session(db: Session, response: Response, user: User) -> None:
    """A fresh random token on every sign-in (session rotation)."""
    s = get_settings()
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.execute(
        delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at < now)
    )
    db.add(
        AuthSession(token_hash=hash_token(token), user_id=user.id,
                    expires_at=now + timedelta(hours=s.session_ttl_hours))
    )  # fmt: skip
    user.last_login_at = now
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=s.session_ttl_hours * 3600, httponly=True,
        samesite="lax", secure=s.cookie_secure, path="/",
    )  # fmt: skip


def _signed_in(request: Request, db: Annotated[Session, Depends(get_db)]) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    row = (
        db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        if token
        else None
    )
    now = utcnow()
    idle = timedelta(minutes=get_settings().session_idle_minutes)
    if row is not None and (row.expires_at < now or row.last_seen_at + idle < now):
        db.delete(row)
        db.commit()
        row = None
    user = db.get(User, row.user_id) if row else None
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    if now - row.last_seen_at > timedelta(seconds=60):  # one write a minute, not one per request
        row.last_seen_at = now
        db.commit()
    roles, perms = load_access(db, user.id)
    return Principal(user, row.id, roles, perms)


SignedIn = Annotated[Principal, Depends(_signed_in)]
DB = Annotated[Session, Depends(get_db)]


def watched(perm: str) -> bool:
    """Refusals worth keeping in the audit log: approvals, corrections, codes, exports, settings,
    users and roles (an attempt there is a signal; an ordinary 403 is just a hidden button)."""
    return perm.split(".")[1] in (
        "approve",
        "correct",
        "sensitive",
        "reveal",
        "configure",
        "export",
    ) or perm.startswith(("users.", "roles."))


def audit_denial(user: User, missing: list[str]) -> None:
    from app.db import SessionLocal  # its own transaction: the refused request rolls back

    with SessionLocal() as db:
        record(db, user, "access.denied", "user", user.id, f"Refused: needs {', '.join(missing)}")
        db.commit()


def require(*perms: str):
    """`p: require("users.edit")` — signed in, password changed, and holding every permission."""

    def check(p: SignedIn) -> Principal:
        if p.user.must_change_password:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Change your password before continuing")
        if missing := [x for x in perms if not p.can(x)]:
            if any(watched(x) for x in missing):
                audit_denial(p.user, missing)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your roles do not allow this action")
        return p

    return Annotated[Principal, Depends(check)]


CurrentPrincipal = require()  # signed in with a usable password, any permissions


def me_payload(p: Principal, db: Session) -> dict:
    company = db.get(Company, 1)
    return {
        "user": {"id": p.user.id, "name": p.user.name, "email": p.user.email,
                 "must_change_password": p.user.must_change_password},
        "company": {"name": company.name, "timezone": company.timezone, "currency": company.currency},
        "roles": p.roles,
        "permissions": sorted(p.permissions),
    }  # fmt: skip


@router.get("/csrf")
def csrf(request: Request, response: Response) -> dict:
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    # Readable by JS on purpose (double-submit pattern); the session cookie stays HttpOnly.
    response.set_cookie(
        CSRF_COOKIE, token, samesite="lax", secure=get_settings().cookie_secure, path="/"
    )
    return {"csrf_token": token}


@router.get("/setup-status")
def setup_status(db: DB) -> dict:
    return {"needs_setup": db.get(Company, 1) is None}


@router.post("/setup", status_code=status.HTTP_201_CREATED)
def setup(body: SetupIn, response: Response, db: DB) -> dict:
    """First run only: the company profile and the first System Administrator."""
    done = HTTPException(status.HTTP_409_CONFLICT, "Setup has already been completed")
    if db.get(Company, 1):
        raise done
    db.add(Company(id=1, name=body.company_name))
    try:
        db.flush()  # a simultaneous setup collides on id 1 here
    except IntegrityError:
        raise done from None
    user = User(email=body.email.strip().lower(), name=body.name,
                password_hash=hasher.hash(body.password))  # fmt: skip
    db.add(user)
    sync_templates(db)
    from app.masters import seed_reference_data  # here: masters imports this module

    seed_reference_data(db)
    admin_role = db.scalar(select(Role).where(Role.template_key == "system_admin"))
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=admin_role.id))
    record(db, user, "company.setup", "company", 1, f"{body.company_name} set up")
    start_session(db, response, user)
    roles, perms = load_access(db, user.id)
    return me_payload(Principal(user, None, roles, perms), db)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DB) -> dict:
    email = body.email.strip().lower()
    client = request.client.host if request.client else "?"
    message = "Too many sign-in attempts. Try again in a minute."
    rate_limit(f"login|{client}", CLIENT_LOGIN_LIMIT, LOGIN_WINDOW_S, message)
    # Per account too, whatever the address: one address can't guess one password many times.
    rate_limit(f"login|{email}", LOGIN_LIMIT, LOGIN_WINDOW_S, message)
    user = db.scalar(select(User).where(User.email == email))
    try:
        with _HASHING:
            hasher.verify(user.password_hash if user else _DUMMY_HASH, body.password)
        if user is None:
            raise VerificationError()
    except VerificationError:
        if user:
            record(db, user, "auth.login_failed", "user", user.id)
            db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect"
        ) from None
    if not user.active:  # said only after the right password, so it reveals nothing to a guesser
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hasher.hash(body.password)
    _attempts.pop(f"login|{email}", None)
    record(db, user, "auth.login", "user", user.id)
    start_session(db, response, user)
    roles, perms = load_access(db, user.id)
    return me_payload(Principal(user, None, roles, perms), db)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DB) -> None:
    if token := request.cookies.get(SESSION_COOKIE):
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(p: SignedIn, db: DB) -> dict:
    return me_payload(p, db)


@router.post("/password")
def change_password(body: PasswordIn, p: SignedIn, db: DB) -> dict:
    rate_limit(
        f"password|{p.user.id}",
        LOGIN_LIMIT,
        LOGIN_WINDOW_S,
        "Too many attempts. Try again in a minute.",
    )
    try:
        with _HASHING:
            hasher.verify(p.user.password_hash, body.current_password)
    except VerificationError:
        raise HTTPException(
            422,
            [
                {
                    "loc": ["body", "current_password"],
                    "msg": "This is not your current password",
                    "type": "x",
                }
            ],
        ) from None
    if body.new_password == body.current_password:
        raise HTTPException(
            422,
            [{"loc": ["body", "new_password"], "msg": "Choose a different password", "type": "x"}],
        )
    p.user.password_hash = hasher.hash(body.new_password)
    p.user.must_change_password = False
    # Every other session ends: whoever knew the old password is signed out everywhere.
    db.execute(
        delete(AuthSession).where(AuthSession.user_id == p.user.id, AuthSession.id != p.session_id)
    )
    record(db, p.user, "auth.password_change", "user", p.user.id)
    db.commit()
    return me_payload(p, db)
