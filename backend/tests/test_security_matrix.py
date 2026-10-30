"""Checks over EVERY route, read from the API's own route list, so a new route that forgets its
permission fails here without anyone writing a test for it."""

import re

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import new_client

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# Reachable without signing in, by design.
PUBLIC = {
    ("GET", "/api/v1/health/live"), ("GET", "/api/v1/health/ready"), ("GET", "/api/v1/auth/csrf"),
    ("GET", "/api/v1/auth/setup-status"), ("POST", "/api/v1/auth/setup"), ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"), ("GET", "/api/v1/public/verify/{token}"),
    ("GET", "/api/v1/public/lookup"), ("POST", "/api/v1/public/registrations"), ("POST", "/api/v1/public/service-requests"),
}  # fmt: skip
# Need a session but no permission: your own profile and password.
SELF = {("GET", "/api/v1/auth/me"), ("POST", "/api/v1/auth/password")}
ZERO = "00000000-0000-0000-0000-000000000000"


def routes() -> list[tuple[str, str]]:
    return sorted((m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops)


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", ZERO, path)


def call(c: TestClient, method: str, path: str):
    return c.request(method, fill(path), json={} if method in UNSAFE else None)


def test_every_route_but_the_public_ones_needs_sign_in(admin):
    c = new_client()
    bad = [(m, p, r.status_code) for m, p in routes() if p.startswith("/api/") and (m, p) not in PUBLIC
           if (r := call(c, m, p)).status_code != 401]  # fmt: skip
    assert bad == []


def test_a_user_without_permissions_is_refused_everywhere(user_with):
    nobody = user_with()
    bad = [(m, p, r.status_code) for m, p in routes()
           if p.startswith("/api/") and (m, p) not in PUBLIC | SELF
           if (r := call(nobody, m, p)).status_code != 403]  # fmt: skip
    assert bad == []


def test_every_write_needs_the_csrf_token(admin):
    admin.headers.pop("X-CSRF-Token")
    bad = [(m, p, r.status_code) for m, p in routes() if m in UNSAFE and p.startswith("/api/")
           if (r := call(admin, m, p)).status_code != 403 or "Security check" not in r.text]  # fmt: skip
    assert bad == []


def test_route_list_is_not_empty():
    assert len(routes()) > 15
