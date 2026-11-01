"""Phase 8 hardening: backup and restore, the virus-scan hook, the S3 adapter, the client address
behind a proxy, retention, integration health and SMET's own date."""

import io
import shutil
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from app import backup, files, main
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.masters import company_today
from app.models import MessageDelivery, Notification, StoredFile, User, utcnow
from app.notify import purge_old
from tests import test_installation, test_platform
from tests.conftest import API, wipe
from tests.ops import item, ok, supplier
from tests.test_customization import po_body
from tests.test_installation import JPEG, bank, photo, visit
from tests.test_notifications import BUYER

site = test_installation.site


@pytest.mark.skipif(shutil.which("pg_dump") is None, reason="PostgreSQL client tools not installed")
def test_a_backup_restores_the_data_and_the_files(user_with, tmp_path):
    buyer = user_with(perms=BUYER)
    po = ok(
        buyer.post(f"{API}/purchase-orders", json=po_body(supplier(buyer), item(buyer, "CELL"))),
        201,
    )
    stored = tmp_path / "files"
    (stored / "ab").mkdir(parents=True)
    (stored / "ab" / "abc").write_bytes(b"evidence")
    dump, archive = backup.create(tmp_path / "out", files_dir=stored)
    assert backup.verify(dump) > 50 and archive is not None
    wipe()
    shutil.rmtree(stored)
    backup.restore(dump, files=archive, files_dir=stored)
    with SessionLocal() as db:
        assert db.scalar(select(User.email).where(User.email.like("u%@example.test"))) is not None
    assert (
        ok(buyer.get(f"{API}/purchase-orders/{po['id']}"))["number"] == po["number"]
    )  # sessions came back too
    assert (stored / "ab" / "abc").read_bytes() == b"evidence"
    test_platform.test_the_app_login_cannot_change_or_delete_audit_events(
        None
    )  # the restore gave no rights back
    dump.write_bytes(dump.read_bytes() + b"x")  # damaged after the fact: refused
    with pytest.raises(SystemExit, match="checksum"):
        backup.verify(dump)


def scanner(tmp_path, body: str) -> str:
    script = tmp_path / "scan.py"
    script.write_text(body)
    return f'"{sys.executable}" "{script}"'


def test_uploads_pass_the_virus_scanner_or_are_refused(site, tmp_path, monkeypatch):
    v = visit(site, "installation", [bank(site)])
    s = get_settings()
    monkeypatch.setattr(
        s,
        "malware_scan_command",
        scanner(
            tmp_path, "import sys\nsys.exit(1 if b'EICAR' in sys.stdin.buffer.read() else 0)\n"
        ),
    )
    r = photo(site["tech"], v, data=JPEG + b"EICAR")
    assert r.status_code == 422 and "virus scanner refused" in r.text
    f = ok(photo(site["tech"], v), 201)
    with SessionLocal() as db:
        assert db.get(StoredFile, f["id"]).scan == "clean"
    monkeypatch.setattr(s, "malware_scan_command", scanner(tmp_path, "import sys\nsys.exit(2)\n"))
    assert photo(site["tech"], v).status_code == 503  # a scanner that fails stores nothing
    monkeypatch.setattr(s, "malware_scan_command", "/nonexistent/scanner")
    assert photo(site["tech"], v).status_code == 503


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ServerSideEncryption):  # noqa: N803 - boto3's names
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        if (Bucket, Key) not in self.objects:
            raise type("NoSuchKey", (Exception,), {})()
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop((Bucket, Key), None)

    def head_bucket(self, Bucket):  # noqa: N803
        pass


def test_files_can_live_in_an_s3_bucket(site, monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(get_settings(), "s3_bucket", "smet-evidence")
    monkeypatch.setattr(files, "storage", lambda: files.S3Store(fake))
    v = visit(site, "installation", [bank(site)])
    f = ok(photo(site["tech"], v), 201)
    assert list(fake.objects) == [("smet-evidence", f"files/{f['id'].replace('-', '')}")]
    r = site["pm"].get(f"{API}/files/{f['id']}")
    assert (
        r.status_code == 200 and r.content == JPEG and "inline" in r.headers["content-disposition"]
    )
    fake.objects.clear()
    assert site["pm"].get(f"{API}/files/{f['id']}").status_code == 410


def test_the_client_address_is_believed_only_from_trusted_proxies(monkeypatch):
    def req(peer, xff):
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers={"x-forwarded-for": xff})

    monkeypatch.setattr(main.settings, "trusted_proxies", ["10.0.0.5"])
    assert (
        main.client_ip(req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    )  # not a proxy: header ignored
    assert (
        main.client_ip(req("10.0.0.5", "1.2.3.4, 198.51.100.7")) == "198.51.100.7"
    )  # the address the proxy saw
    assert main.client_ip(req("10.0.0.5", "")) == "10.0.0.5"


def test_retention_keeps_records_and_forgets_old_texts(admin, user_with):
    user_with(perms=["stock.view"])
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email != "admin@example.test"))
        old = utcnow() - timedelta(days=400)
        db.add_all([Notification(user_id=uid, event="stock.low", title="t", key="old", read_at=old, created_at=old),
                    Notification(user_id=uid, event="stock.low", title="t", key="unread", created_at=old),
                    MessageDelivery(key="k", event="stock.low", channel="sms", provider="twilio", mode="mock",
                                    recipient="+971501234567", subject="s", body="Hello Ali", status="delivered", created_at=old)])  # fmt: skip
        db.commit()
        purge_old(db)
        assert [n.key for n in db.scalars(select(Notification))] == ["unread"]
        d = db.scalar(select(MessageDelivery))
        assert (d.body, d.status, d.recipient) == ("", "delivered", "+9715••••••67")


def test_system_health_reports_each_outside_dependency(admin):
    names = [i["name"] for i in ok(admin.get(f"{API}/system/status"))["integrations"]]
    assert (
        names[:2] == ["File storage (local)", "Virus scanner"]
        and "Backups" in names
        and "Server clock" in names
    )
    assert {
        "Messages: email",
        "Messages: sms",
        "Messages: whatsapp",
        "Battery supplier API",
    } <= set(names)


def test_dates_are_smet_s_dates_not_the_server_s(admin):
    with SessionLocal() as db:
        db.execute(update(main.Company).values(timezone="Pacific/Kiritimati"))  # UTC+14
        db.commit()
        assert company_today(db) == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()


def test_production_needs_https_and_https_gets_hsts(admin, monkeypatch):
    """L5: production with insecure cookies refused at start; HTTPS answers carry HSTS."""
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(app_env="production", cookie_secure=False)
    assert "strict-transport-security" not in admin.get(f"{API}/health/live").headers
    monkeypatch.setattr(main.settings, "cookie_secure", True)
    assert (
        admin.get(f"{API}/health/live").headers["strict-transport-security"] == "max-age=31536000"
    )
