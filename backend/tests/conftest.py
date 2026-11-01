import base64
import os
import uuid

from tests.conftest_env import use_test_database

# Must run before app imports: the tests use their own database, which they wipe between tests.
use_test_database()
os.environ |= {
    "RUN_WORKER_IN_API": "false",  # tests run jobs explicitly with run_due_jobs()
    "ALLOWED_HOSTS": '["127.0.0.1", "localhost", "testserver"]',  # testserver: TestClient's host
    "API_DOCS": "true",  # on so the route list (openapi) can drive the security matrix
    "SESSION_IDLE_MINUTES": "60",
    "CODES_KEY": base64.urlsafe_b64encode(
        b"test-only " * 3 + b"!!"
    ).decode(),  # obviously not a real key
    "SECRETS_KEY": base64.urlsafe_b64encode(b"test-secrets " * 2 + b"!!!!!!").decode(),
    "EXTERNAL_SENDS": "off",
}

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.auth import CSRF_COOKIE, _attempts, hasher  # noqa: E402
from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.db import Base, SessionLocal, make_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, RolePermission, User, UserRole  # noqa: E402

API = "/api/v1"


def smet_today():
    """Today in the company's time zone (Asia/Dubai), as the app dates things."""
    from datetime import datetime  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    return datetime.now(ZoneInfo("Asia/Dubai")).date()


PASSWORD = "correct-horse-battery"
ADMIN = {"company_name": "Demo Trading LLC", "name": "Test Admin", "email": "admin@example.test",
         "password": PASSWORD}  # fmt: skip
owner_engine = make_engine(get_settings().owner_url)


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["skip_logging_config"] = True
    return cfg


def wipe() -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("SET LOCAL smet.audit_maintenance = 'on'"))
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    _attempts.clear()


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    command.upgrade(alembic_config(), "head")
    wipe()  # the browser tests share this database and leave their data behind


@pytest.fixture(autouse=True)
def clean_db():
    yield
    wipe()


def new_client(**kw) -> TestClient:
    """A browser stand-in: it sends the CSRF cookie's value as the header, also after sign-in
    replaces it, as the web app does."""
    c = TestClient(app, **kw)

    def follow(r):
        if token := r.cookies.get(CSRF_COOKIE):
            c.headers["X-CSRF-Token"] = token

    c.event_hooks["response"].append(follow)
    c.headers["X-CSRF-Token"] = c.get(f"{API}/auth/csrf").json()["csrf_token"]
    return c


@pytest.fixture
def client() -> TestClient:
    return new_client()


@pytest.fixture
def admin(client) -> TestClient:
    """Signed in as the first-run System Administrator."""
    assert client.post(f"{API}/auth/setup", json=ADMIN).status_code == 201
    return client


def signed_in(email: str, password: str = PASSWORD) -> TestClient:
    c = new_client()
    r = c.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def user_with(admin):
    """user_with("procurement_officer", ...) or user_with(perms=[...]): a signed-in client for a
    new user holding those role templates, or a custom role with exactly those permissions."""

    def make(
        *templates: str, perms: list[str] | None = None, email: str | None = None
    ) -> TestClient:
        email = email or f"u{uuid.uuid4().hex[:8]}@example.test"
        with SessionLocal() as db:
            user = User(email=email, name=email.split("@")[0], password_hash=hasher.hash(PASSWORD))
            db.add(user)
            db.flush()
            role_ids = [db.scalar(select(Role.id).where(Role.template_key == t)) for t in templates]
            if perms is not None:
                role = Role(name=f"custom {uuid.uuid4().hex[:6]}")
                db.add(role)
                db.flush()
                db.add_all([RolePermission(role_id=role.id, permission=x) for x in perms])
                role_ids.append(role.id)
            db.add_all([UserRole(user_id=user.id, role_id=r) for r in role_ids])
            db.commit()
        return signed_in(email)

    return make
