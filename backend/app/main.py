import logging
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import (
    assembly,
    codes,
    custom,
    dispatch,
    files,
    installation,
    inventory,
    jobs,
    masters,
    messaging,
    notify,
    procurement,
    projects,
    public,
    reports,
    sales,
    service,
    trace,
    warranty,
)
from app.admin import router as admin_router
from app.audit import request_info
from app.auth import CSRF_COOKIE, DB, require
from app.auth import router as auth_router
from app.config import BACKEND_DIR, VERSION, get_settings
from app.db import SessionLocal
from app.logs import redact, setup_logging
from app.models import Company
from app.rbac import sync_templates

setup_logging()
log = logging.getLogger("smet")
settings = get_settings()
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",  # business records and exports never sit in a browser cache
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",  # API: data only
}
HEAD = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()


@asynccontextmanager
async def lifespan(_: FastAPI):
    with SessionLocal() as db:  # role templates added by this version reach existing installs
        if db.get(Company, 1):
            sync_templates(db)
            masters.seed_reference_data(db)
            db.commit()
    stop = jobs.start_in_background() if settings.run_worker_in_api else None
    yield
    if stop:
        stop.set()


app = FastAPI(
    lifespan=lifespan,
    title="SMET Operations API",
    version=VERSION,
    openapi_url="/api/v1/openapi.json" if settings.api_docs else None,
    docs_url="/api/v1/docs" if settings.api_docs else None,
    redoc_url=None,
)

MAX_BODY = 1 * 2**20  # JSON bodies
UPLOAD_PATH = re.compile(
    r"^/api/v1/((visits|warranty-registrations|service-requests)/[^/]+/files|public/registrations)$"
)


class BodyLimit:
    """Refuses bodies over MAX_BODY, counting bytes as they arrive, so chunked uploads without a
    Content-Length are cut off at the limit too, never read whole."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            return await self.app(scope, receive, send)
        too_large = JSONResponse({"detail": "That request is too large."}, 413)
        limit = MAX_BODY
        if UPLOAD_PATH.match(scope["path"]):  # a file plus the form around it
            limit = settings.max_upload_mb * 2**20 + 64 * 2**10
        if int(dict(scope["headers"]).get(b"content-length") or 0) > limit:
            return await too_large(scope, receive, send)
        body, message = bytearray(), {"more_body": True}
        while message.get("more_body"):
            message = await receive()
            if message["type"] != "http.request":
                break  # the client went away; let the app see that
            body += message.get("body", b"")
            if len(body) > limit:
                return await too_large(scope, receive, send)
        first = {"type": "http.request", "body": bytes(body), "more_body": False}
        if message["type"] != "http.request":
            first = message

        async def replay():
            nonlocal first
            if first is not None:
                out, first = first, None
                return out
            return await receive()

        await self.app(scope, replay, send)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
        cookie = request.cookies.get(CSRF_COOKIE, "").encode()
        header = request.headers.get("x-csrf-token", "").encode()
        if not cookie or not secrets.compare_digest(cookie, header):
            return JSONResponse(
                {"detail": "Security check failed. Reload the page and try again."}, 403
            )
    return await call_next(request)


def client_ip(request: Request) -> str:
    """The caller's address. Behind a trusted reverse proxy, the last address the proxy saw
    (X-Forwarded-For), so a client can't pick its own address by sending the header itself."""
    peer = request.client.host if request.client else ""
    trusted = settings.trusted_proxies
    if peer not in trusted:
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    while hops and hops[-1] in trusted:  # skip our own proxies, right to left
        hops.pop()
    return hops[-1] if hops else peer


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request_info.set({"ip": client_ip(request), "request_id": request_id})
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:  # a calm 500 with the request id; the traceback goes to the log only
        log.exception("Unhandled error", extra={"request_id": request_id})
        response = JSONResponse({"detail": f"The server had a problem (ref {request_id})."}, 500)
    response.headers["X-Request-ID"] = request_id
    docs = request.url.path.startswith(("/api/v1/docs", "/api/v1/openapi.json"))
    for name, value in SECURITY_HEADERS.items():
        if not (docs and name == "Content-Security-Policy"):  # the docs page loads Swagger UI
            response.headers.setdefault(name, value)
    log.info("%s %s %s %.1fms", request.method, request.url.path[:200], response.status_code,
             (time.perf_counter() - start) * 1000, extra={"request_id": request_id})  # fmt: skip
    return response


app.add_middleware(BodyLimit)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)  # outermost

api = APIRouter(prefix="/api/v1")
api.include_router(auth_router)
api.include_router(admin_router)
for module in (
    masters,
    procurement,
    inventory,
    assembly,
    sales,
    projects,
    dispatch,
    installation,
    files,
    trace,
    warranty,
    service,
    codes,
    public,
    messaging,
    notify,
    reports,
    custom,
):
    api.include_router(module.router)


@api.get("/health/live", tags=["health"])
def live() -> dict:
    return {"status": "ok", "version": VERSION}


@api.get("/health/ready", tags=["health"])
def ready(response: Response, db: DB) -> dict:
    """Ready = the database answers and its schema is at this version's migration."""
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
        current = db.scalar(text("SELECT version_num FROM alembic_version"))
        checks["migrations"] = "ok" if current == HEAD else "pending"
    except SQLAlchemyError:
        log.exception("Readiness check failed")
        checks.setdefault("database", "error")
        checks.setdefault("migrations", "error")
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "unavailable", "checks": checks}


@api.get("/system/status", tags=["health"])
def system_status(_: require("system.view"), db: DB) -> dict:
    return {
        "version": VERSION,
        "database_mb": round(
            db.scalar(text("SELECT pg_database_size(current_database())")) / 2**20, 1
        ),
        "database_version": db.scalar(text("SHOW server_version")),
        "jobs": jobs.summary(db),
        "worker_in_api": settings.run_worker_in_api,
        "integrations": integrations(db),
    }


def integrations(db) -> list[dict]:
    """What each outside dependency is doing: ok, off, warning (works, needs attention) or error."""
    from app.files import storage  # noqa: PLC0415
    from app.masters import company_tz  # noqa: PLC0415
    from app.messaging import EXTERNAL, blocked_reason  # noqa: PLC0415
    from app.models import MessageProvider  # noqa: PLC0415

    out = []
    try:
        s = storage()
        out.append({"name": f"File storage ({s.name})", "state": "ok", "detail": s.check()})
    except Exception as e:  # report, don't fail the page
        out.append({"name": "File storage", "state": "error", "detail": redact(str(e))[:200]})
    out.append({"name": "Virus scanner", "state": "ok" if settings.malware_scan_command else "off",
                "detail": "Every upload is scanned; refused or failed scans stop the upload" if settings.malware_scan_command
                else "Not set (MALWARE_SCAN_COMMAND): uploads are type-checked only"})  # fmt: skip
    for ch in EXTERNAL:
        p = db.get(MessageProvider, ch)
        if p is None or p.mode == "off":
            out.append({"name": f"Messages: {ch}", "state": "off", "detail": "Switched off"})
            continue
        why = blocked_reason(p)
        test = (
            ""
            if p.last_test_at is None
            else f"; last test {'passed' if p.last_test_ok else 'failed'}: {p.last_test_message}"
        )
        out.append({"name": f"Messages: {ch}", "state": "warning" if why or p.last_test_ok is False else "ok",
                    "detail": f"{p.provider}, {p.mode}" + (f"; {why}" if why else "") + test})  # fmt: skip
    out.append(
        {
            "name": "Battery supplier API",
            "state": "off",
            "detail": "Manual: reset codes are typed in (no supplier API documented)",
        }
    )
    dumps = (
        sorted(Path(settings.backup_dir).glob("smet-*.dump"))
        if Path(settings.backup_dir).exists()
        else []
    )
    if not dumps:
        out.append(
            {
                "name": "Backups",
                "state": "warning",
                "detail": f"None found in {settings.backup_dir}",
            }
        )
    else:
        age = (time.time() - dumps[-1].stat().st_mtime) / 86400
        out.append({"name": "Backups", "state": "ok" if age < 1.5 else "warning",
                    "detail": f"Newest {dumps[-1].name}, {age:.1f} days old"})  # fmt: skip
    server = time.strftime("%Z")
    company = company_tz(db)
    same = datetime.now().astimezone().utcoffset() == datetime.now(ZoneInfo(company)).utcoffset()
    out.append({"name": "Server clock", "state": "ok" if same else "warning",
                "detail": f"Server time zone {server}" + ("" if same else f"; set it to {company} (dates follow the server's clock)")})  # fmt: skip
    return out


app.include_router(api)
