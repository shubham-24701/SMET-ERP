"""Phase 7 messages: what each event says (templates) and how it leaves (email, SMS, WhatsApp).

Each external channel has a mode, set in Administration → Messages:
- off: nothing is queued on that channel.
- mock: messages are recorded and marked delivered; nothing leaves the server. For trying templates.
- sandbox: the provider is really called, but every message goes to the channel's test recipient.
- production: the provider is called for the real recipient.
The server setting EXTERNAL_SENDS (off | sandbox | production, default off) caps every channel: no
message reaches a provider until whoever runs the server switches it on, whatever the screen says.

Credentials are stored encrypted with SECRETS_KEY and never shown again. A message goes out once:
its delivery row is marked `sending` before the provider is called, and a row found still `sending`
(the worker died mid-call) becomes `unknown` instead of being sent a second time.

The provider calls follow the providers' published HTTP APIs (Twilio Messages, Meta WhatsApp Cloud
API) and SMTP. They are tested here against recorded requests only: none has been run against a
real account (docs/INTEGRATION_REGISTER.md).
"""

import base64
import json
import logging
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, require
from app.config import get_settings
from app.inventory import conflict
from app.jobs import enqueue, handler, periodic
from app.logs import redact
from app.masters import Limit, Offset, page, rules
from app.models import (
    MessageDelivery,
    MessageOptOut,
    MessageProvider,
    MessageTemplate,
    utcnow,
)

router = APIRouter(tags=["messages"])
log = logging.getLogger("smet.messages")
CHANNELS = ("in_app", "email", "customer_email", "sms", "whatsapp")
EXTERNAL = ("email", "sms", "whatsapp")
MAX_ATTEMPTS = 5


# ---------- events and their wording ----------


@dataclass(frozen=True)
class Event:
    label: str
    vars: tuple[str, ...]
    customer: bool = False  # may also reach the customer (with their consent)


EVENTS: dict[str, Event] = {
    "approval.requested": Event("Approval requested", ("number", "summary", "by")),
    "approval.decided": Event("Approval decided", ("number", "summary", "by", "reason")),
    "stock.low": Event("Stock below minimum", ("item", "free", "minimum")),
    "procurement.delayed": Event("Supplier delivery late", ("number", "supplier", "expected", "days_late")),
    "assembly.delayed": Event("Assembly order late", ("number", "item", "target", "days_late")),
    "readiness.changed": Event("Delivery or project progress", ("number", "summary")),
    "service.status": Event("Service request status", ("number", "status", "summary"), customer=True),
    "amc.visit_upcoming": Event("Maintenance visit coming up", ("number", "contract", "customer", "date")),
    "maintenance.overdue": Event("Maintenance overdue", ("number", "contract", "customer", "date", "days_late")),
    "amc.expiring": Event("Maintenance contract ending", ("number", "customer", "ends")),
    "warranty.outcome": Event("Warranty decision", ("number", "serial", "outcome", "expiry", "reason"), customer=True),
    "warranty.missing_info": Event("Warranty registration needs information", ("number", "serial", "reason"), customer=True),
}  # fmt: skip
COMMON_VARS = ("company", "url")  # url: the record in SMET; staff email only

# (subject, body). Staff wording on in_app and email; customer wording on sms and whatsapp.
_IN_APP: dict[str, tuple[str, str]] = {
    "approval.requested": ("{number} waits for your approval", "{summary} by {by}."),
    "approval.decided": ("{number}: decision made", "{summary} by {by}. {reason}"),
    "stock.low": ("{item} is below minimum", "{free} free against a minimum of {minimum}."),
    "procurement.delayed": ("{number} from {supplier} is late", "Expected {expected}; {days_late} days late."),
    "assembly.delayed": ("{number} is late", "{item}, target {target}; {days_late} days late."),
    "readiness.changed": ("{number} moved on", "{summary}."),
    "service.status": ("{number}: {status}", "{summary}."),
    "amc.visit_upcoming": ("Maintenance visit {number} on {date}", "{contract} for {customer}."),
    "maintenance.overdue": ("Maintenance overdue: {number}", "{contract} for {customer}, due {date}; {days_late} days late."),
    "amc.expiring": ("{number} ends on {ends}", "Maintenance contract for {customer}: time to renew."),
    "warranty.outcome": ("{number}: warranty {outcome}", "Battery {serial}. {reason}"),
    "warranty.missing_info": ("{number} needs more information", "Battery {serial}: {reason}"),
}  # fmt: skip
_CUSTOMER: dict[str, str] = {
    "service.status": "{company}: your service request {number} is now {status}.",
    "warranty.outcome": "{company}: warranty registration {number} for battery {serial}: {outcome}.",
    "warranty.missing_info": "{company}: we need more information for warranty registration {number}: {reason}",
}


def default_template(event: str, channel: str) -> tuple[str, str] | None:
    if channel == "in_app":
        return _IN_APP[event]
    if channel == "email":  # staff copy, with a link to the record
        subject, body = _IN_APP[event]
        return subject, body + "\n\nOpen in SMET: {url}"
    if channel == "customer_email" and event in _CUSTOMER:  # customer wording, no link
        return _IN_APP[event][0], _CUSTOMER[event]
    return ("", _CUSTOMER[event]) if channel in ("sms", "whatsapp") and event in _CUSTOMER else None


VAR = re.compile(r"\{(\w+)\}")


def check_text(text: str, event: str) -> str | None:
    """None if the text is fine; else what is wrong, in words."""
    if "{" in VAR.sub("", text) or "}" in VAR.sub("", text):
        return "Braces only go around a field name, like {number}"
    unknown = set(VAR.findall(text)) - set(EVENTS[event].vars) - set(COMMON_VARS)
    if unknown:
        return f"Unknown field {', '.join('{' + u + '}' for u in sorted(unknown))}"
    return None


def render(text: str, values: dict) -> str:
    return VAR.sub(lambda m: str(values.get(m[1], "")), text).strip()


@dataclass
class Wording:
    subject: str
    body: str
    provider_template: str
    language: str
    active: bool


def wording(db: Session, event: str, channel: str) -> Wording | None:
    t = db.scalar(
        select(MessageTemplate).where(
            MessageTemplate.event == event, MessageTemplate.channel == channel
        )
    )
    if t:
        return Wording(t.subject, t.body, t.provider_template, t.language, t.active)
    d = default_template(event, channel)
    return Wording(d[0], d[1], "", "en", True) if d else None


# ---------- addresses ----------

EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def normalise(channel: str, address: str, country_code: str) -> str | None:
    """An email in lower case, or a phone number as +<digits> (E.164); None if it isn't one."""
    a = address.strip()
    if channel == "email":
        return a.lower() if EMAIL.fullmatch(a) else None
    if EMAIL.search(a):
        return None
    d = re.sub(r"[\s().-]", "", a)
    if d.startswith("+"):
        d = d[1:]
    elif d.startswith("00"):
        d = d[2:]
    elif d.startswith("0"):
        d = country_code + d[1:]
    elif len(d) <= 9:  # a local number written without its leading 0
        d = country_code + d
    return "+" + d if d.isdigit() and 8 <= len(d) <= 15 else None


def mask(address: str) -> str:
    if "@" in address:
        name, _, domain = address.partition("@")
        return f"{name[:1]}•••@{domain}"
    return address[:5] + "•" * max(len(address) - 7, 0) + address[-2:]


# ---------- providers ----------


class Retry(Exception):
    """A temporary failure before the message got through: worth trying again later."""


class Unsure(Exception):
    """The provider may have the message (a timeout after sending, an unreadable answer): it is
    never sent again by itself, so it can't arrive twice."""


@dataclass
class Outgoing:
    to: str
    subject: str
    body: str
    params: list[str]
    template: str
    language: str


def http(method: str, url: str, *, headers: dict | None = None, form: dict | None = None,
         body: dict | None = None, timeout: int = 15) -> tuple[int, dict]:  # fmt: skip
    """Standard library only. Network errors are temporary (Retry); HTTP answers are returned."""
    h = dict(headers or {})
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif form is not None:
        data, h["Content-Type"] = urlencode(form).encode(), "application/x-www-form-urlencoded"
    try:
        with urlopen(Request(url, data, h, method=method), timeout=timeout) as r:  # noqa: S310 - https only
            raw = r.read()
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except URLError as e:  # no connection: the request never got through
        raise Retry(f"Couldn't reach the provider ({e.reason})") from e
    except (TimeoutError, OSError) as e:  # sent, but no answer: it may have arrived
        raise Unsure(f"No answer from the provider after sending ({e})") from e
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError as e:
        raise Unsure("The provider accepted the request but its answer couldn't be read") from e


HTTP = http  # tests replace this; nothing in tests reaches a network


def _ref(payload: dict, *path) -> str:
    """The provider's id for an accepted message; a 2xx without one is unsure, not failed."""
    try:
        for k in path:
            payload = payload[k]
        return str(payload)
    except (KeyError, IndexError, TypeError) as e:
        raise Unsure("The provider accepted the message but sent no reference") from e


def _answer(code: int, payload: dict, what: str) -> dict:
    if code == 429 or code >= 500:
        raise Retry(f"{what}: the provider answered {code}; will try again")
    if code >= 400:
        msg = payload.get("message") or (payload.get("error") or {}).get("message") or ""
        raise ValueError(f"{what}: the provider refused it ({code}) {msg}".strip())
    return payload


@dataclass(frozen=True)
class Field_:
    name: str
    label: str
    pattern: str = r".+"
    required: bool = True


class Smtp:
    label = "SMTP server"
    secret_label = "Password"  # noqa: S105 - the field's label
    fields = (
        Field_("host", "Server", r"[A-Za-z0-9.-]{1,253}"),
        Field_("port", "Port", r"\d{2,5}"),
        Field_("security", "Security (starttls, ssl or none)", r"starttls|ssl|none"),
        Field_("username", "User name", r".{0,254}", required=False),
        Field_("from_address", "Send from (email address)", EMAIL.pattern),
    )

    def __init__(self, s: dict, secret: str):
        self.s, self.secret = s, secret

    def _connect(self) -> smtplib.SMTP:
        host, port = self.s["host"], int(self.s["port"])
        try:
            if self.s["security"] == "ssl":
                c = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
            else:
                c = smtplib.SMTP(host, port, timeout=20)
                if self.s["security"] == "starttls":
                    c.starttls(context=ssl.create_default_context())
            if self.s.get("username"):
                c.login(self.s["username"], self.secret)
            return c
        except smtplib.SMTPAuthenticationError as e:
            raise ValueError("The server refused the user name or password") from e
        except (smtplib.SMTPException, OSError) as e:
            raise Retry(f"Couldn't reach the mail server ({e})") from e

    def send(self, m: Outgoing) -> str:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.s["from_address"], m.to, m.subject
        msg["Message-ID"] = ref = make_msgid(domain=self.s["from_address"].split("@")[1])
        msg.set_content(m.body)
        c = self._connect()
        try:
            c.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            raise ValueError("The server refused the recipient address") from e
        except smtplib.SMTPResponseException as e:  # a clear refusal with a reply code
            raise ValueError(f"The mail server refused the message ({e.smtp_code})") from e
        except (smtplib.SMTPException, OSError) as e:  # cut off mid-way: it may have been accepted
            raise Unsure(f"The mail server dropped the connection while sending ({e})") from e
        finally:
            try:
                c.quit()
            except (smtplib.SMTPException, OSError):
                pass
        return ref

    def test(self) -> str:
        c = self._connect()
        c.noop()
        c.quit()
        return f"Signed in to {self.s['host']}"

    def status(self, ref: str) -> str | None:
        return None  # SMTP reports nothing after acceptance


class Twilio:
    label = "Twilio"
    secret_label = "Auth token"  # noqa: S105 - the field's label
    fields = (
        Field_("account_sid", "Account SID", r"AC[0-9a-f]{32}"),
        Field_("from_number", "Send from (number, +971…)", r"\+\d{8,15}"),
    )
    STATUS = {"delivered": "delivered", "undelivered": "failed", "failed": "failed"}

    def __init__(self, s: dict, secret: str):
        sid = s["account_sid"]
        self.base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
        self.auth = {
            "Authorization": "Basic " + base64.b64encode(f"{sid}:{secret}".encode()).decode()
        }
        self.s = s

    def send(self, m: Outgoing) -> str:
        code, p = HTTP("POST", f"{self.base}/Messages.json", headers=self.auth,
                       form={"To": m.to, "From": self.s["from_number"], "Body": m.body})  # fmt: skip
        return _ref(_answer(code, p, "Twilio"), "sid")

    def test(self) -> str:
        code, p = HTTP("GET", f"{self.base}.json", headers=self.auth)
        p = _answer(code, p, "Twilio")
        return f"Connected to Twilio account {p.get('friendly_name', '')} ({p.get('status', '')})"

    def status(self, ref: str) -> str | None:
        if not re.fullmatch(r"[A-Z]{2}[0-9a-f]{32}", ref):
            return None
        code, p = HTTP("GET", f"{self.base}/Messages/{ref}.json", headers=self.auth)
        return self.STATUS.get(_answer(code, p, "Twilio").get("status", ""))


class MetaCloud:
    label = "WhatsApp Cloud API (Meta)"
    secret_label = "Access token"  # noqa: S105 - the field's label
    fields = (
        Field_("phone_number_id", "Phone number ID", r"\d{5,20}"),
        Field_("api_version", "Graph API version (e.g. v21.0)", r"v\d{1,2}\.\d"),
    )

    def __init__(self, s: dict, secret: str):
        self.url = f"https://graph.facebook.com/{s['api_version']}/{s['phone_number_id']}"
        self.auth = {"Authorization": f"Bearer {secret}"}

    def send(self, m: Outgoing) -> str:
        if not m.template:
            raise ValueError(
                "WhatsApp needs the name of a template approved by Meta (Messages → Templates)"
            )
        body = {"messaging_product": "whatsapp", "to": m.to.lstrip("+"), "type": "template",
                "template": {"name": m.template, "language": {"code": m.language},
                             "components": [{"type": "body", "parameters": [{"type": "text", "text": v} for v in m.params]}]}}  # fmt: skip
        code, p = HTTP("POST", f"{self.url}/messages", headers=self.auth, body=body)
        return _ref(_answer(code, p, "WhatsApp"), "messages", 0, "id")

    def test(self) -> str:
        code, p = HTTP(
            "GET", f"{self.url}?fields=display_phone_number,verified_name", headers=self.auth
        )
        p = _answer(code, p, "WhatsApp")
        return f"Connected to WhatsApp number {p.get('display_phone_number', '')} {p.get('verified_name', '')}".strip()

    def status(self, ref: str) -> str | None:
        return None  # Meta reports delivery by webhook only (Phase 8)


PROVIDERS = {
    "email": {"smtp": Smtp},
    "sms": {"twilio": Twilio},
    "whatsapp": {"meta_cloud": MetaCloud},
}


def cipher() -> Fernet:
    key = get_settings().secrets_key
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Provider credentials can't be stored yet: SECRETS_KEY is not set (run python -m app.setup_db)")  # fmt: skip
    return Fernet(key)


def adapter(p: MessageProvider):
    secret = cipher().decrypt(p.secret_ciphertext.encode()).decode() if p.secret_ciphertext else ""
    return PROVIDERS[p.channel][p.provider](p.settings, secret)


def server_allows(mode: str) -> bool:
    cap = get_settings().external_sends
    return (
        mode == "mock"
        or (mode == "sandbox" and cap in ("sandbox", "production"))
        or (mode == "production" and cap == "production")
    )


def blocked_reason(p: MessageProvider | None) -> str:
    if p is None or p.mode == "off":
        return "This channel is switched off"
    if not server_allows(p.mode):
        return f"Real sends are switched off on this server (EXTERNAL_SENDS={get_settings().external_sends})"
    if p.mode == "sandbox" and not p.test_recipient:
        return "Sandbox mode needs a test recipient"
    return ""


# ---------- queueing and sending ----------


def queue(db: Session, *, event: str, channel: str, to: str, key: str, values: dict,
          user_id: uuid.UUID | None = None, entity: tuple[str, str] = ("", "")) -> None:  # fmt: skip
    """Add an external message in the caller's transaction. Same key = sent once, ever.
    `user_id` None = a customer: customer wording, and texts only to numbers in SMET's country."""
    p = db.get(MessageProvider, channel)
    w = wording(db, event, "customer_email" if channel == "email" and user_id is None else channel)
    if p is None or p.mode == "off" or w is None or not w.active:
        return
    cc = str(rules(db)["phone_country_code"])
    addr = normalise(channel, to, cc)
    if addr is None or (channel != "email" and user_id is None and not addr.startswith(f"+{cc}")):
        return  # shortcut: no texts abroad, so a public form can't run up a bill (assumption A33)
    st, err = "queued", blocked_reason(p)
    if db.get(MessageOptOut, (channel, addr)):
        st, err = "skipped", "The recipient opted out of this channel"
    elif err:
        st = "blocked"
    row = dict(key=f"{key}:{channel}:{addr}"[:260], event=event, channel=channel, provider=p.provider, mode=p.mode,
               recipient=addr, user_id=user_id, entity_type=entity[0], entity_id=str(entity[1]),
               subject=render(w.subject, values)[:200], body=render(w.body, values),
               params=[str(values.get(v, "")) for v in VAR.findall(w.body)], status=st, error=err)  # fmt: skip
    new_id = db.scalar(
        insert(MessageDelivery)
        .values(id=uuid.uuid4(), **row)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(MessageDelivery.id)
    )
    if new_id and st == "queued":
        enqueue(db, "message.send", {"id": str(new_id)}, max_attempts=MAX_ATTEMPTS + 1)


@handler("message.send")
def send_job(db: Session, payload: dict) -> None:
    d = db.get(MessageDelivery, uuid.UUID(payload["id"]), with_for_update=True)
    if d is None or d.status not in ("queued", "sending"):
        db.rollback()
        return
    if d.status == "sending":  # an earlier attempt died during the provider call
        d.status, d.error = (
            "unknown",
            "Interrupted while sending; not sent again so it can't arrive twice",
        )
        d.updated_at = utcnow()
        db.commit()
        return
    p = db.get(MessageProvider, d.channel)
    if reason := blocked_reason(p):
        d.status, d.error, d.updated_at = "blocked", reason, utcnow()
        db.commit()
        return
    if db.get(MessageOptOut, (d.channel, d.recipient)):
        d.status, d.error, d.updated_at = (
            "skipped",
            "The recipient opted out of this channel",
            utcnow(),
        )
        db.commit()
        return
    w = wording(db, d.event, d.channel)  # the WhatsApp template name and language
    d.status, d.attempts, d.mode, d.provider = "sending", d.attempts + 1, p.mode, p.provider
    d.sent_to = p.test_recipient if p.mode == "sandbox" else d.recipient
    d.updated_at = utcnow()
    db.commit()
    out = Outgoing(d.sent_to, d.subject, d.body, d.params, w.provider_template if w else "",
                   w.language if w else "en")  # fmt: skip
    try:
        ref = f"mock-{d.id.hex[:12]}" if p.mode == "mock" else adapter(p).send(out)
    except Retry as e:
        d.status = "queued" if d.attempts < MAX_ATTEMPTS else "failed"
        d.error, d.updated_at = redact(str(e))[:300], utcnow()
        db.commit()
        if d.status == "queued":
            raise  # the job queue tries again with a growing delay
        return
    except Unsure as e:
        d.status, d.error, d.updated_at = "unknown", redact(str(e))[:300], utcnow()
        db.commit()
        return
    except Exception as e:  # refused: trying again would get the same answer
        d.status, d.error, d.updated_at = "failed", redact(str(e))[:300], utcnow()
        db.commit()
        return
    d.status, d.provider_ref, d.error = (
        ("delivered" if p.mode == "mock" else "sent"),
        str(ref)[:120],
        "",
    )
    d.updated_at = utcnow()
    db.commit()


@periodic(900)
def refresh_status(db: Session) -> None:
    """Asks the provider what became of recent messages, where it can say (Twilio)."""
    rows = db.scalars(select(MessageDelivery).where(MessageDelivery.status == "sent", MessageDelivery.provider == "twilio",
                                                    MessageDelivery.created_at > utcnow() - timedelta(days=2)).limit(50)).all()  # fmt: skip
    for d in rows:
        p = db.get(MessageProvider, d.channel)
        if (
            p is None
            or p.provider != d.provider
            or p.mode not in ("sandbox", "production")
            or not server_allows(p.mode)
        ):
            continue
        try:
            new = adapter(p).status(d.provider_ref)
        except Exception:  # a status check failing changes nothing
            log.warning("Status check failed for message %s", d.id, exc_info=True)
            continue
        if new:
            d.status, d.updated_at = new, utcnow()
    db.commit()


# ---------- administration ----------

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


def _provider_row(channel: str, p: MessageProvider | None) -> dict:
    kinds = PROVIDERS[channel]
    return {
        "channel": channel,
        "providers": [
            {"key": k, "label": c.label, "secret_label": c.secret_label,
             "fields": [{"name": f.name, "label": f.label, "required": f.required} for f in c.fields]}
            for k, c in kinds.items()
        ],
        "provider": p.provider if p else next(iter(kinds)),
        "mode": p.mode if p else "off",
        "settings": p.settings if p else {},
        "secret_set": bool(p and p.secret_ciphertext),
        "test_recipient": p.test_recipient if p else "",
        "last_test": {"at": p.last_test_at, "ok": p.last_test_ok, "message": p.last_test_message} if p and p.last_test_at else None,
        "blocked": blocked_reason(p) if p and p.mode != "off" else "",
        "version": p.version if p else 0,
    }  # fmt: skip


@router.get("/messages/providers")
def list_providers(_: require("notifications.configure"), db: DB) -> dict:
    return {"items": [_provider_row(c, db.get(MessageProvider, c)) for c in EXTERNAL],
            "external_sends": get_settings().external_sends}  # fmt: skip


class ProviderIn(BaseModel):
    version: int
    provider: str
    mode: Literal["off", "mock", "sandbox", "production"]
    settings: dict[
        str, Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    ] = {}
    secret: Annotated[str, StringConstraints(max_length=500)] = ""  # empty = keep the stored one
    test_recipient: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] = ""
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


@router.put("/messages/providers/{channel}")
def save_provider(channel: Literal["email", "sms", "whatsapp"], body: ProviderIn,
                  p: require("notifications.configure"), db: DB) -> dict:  # fmt: skip
    kind = PROVIDERS[channel].get(body.provider)
    if kind is None:
        raise field_error("provider", "Choose one of the listed providers")
    settings = {}
    for f in kind.fields:
        v = body.settings.get(f.name, "")
        if v and not re.fullmatch(f.pattern, v):
            raise field_error(f"settings.{f.name}", f"{f.label}: this doesn't look right")
        if not v and f.required and body.mode in ("sandbox", "production"):
            raise field_error(f"settings.{f.name}", f"{f.label} is needed")
        settings[f.name] = v
    test_to = ""
    if body.test_recipient:
        test_to = (
            normalise(channel, body.test_recipient, str(rules(db)["phone_country_code"])) or ""
        )
        if not test_to:
            raise field_error(
                "test_recipient",
                "Not an email address" if channel == "email" else "Not a phone number",
            )
    if body.mode == "sandbox" and not test_to:
        raise field_error("test_recipient", "Sandbox mode sends everything here, so it is needed")
    row = db.get(MessageProvider, channel, with_for_update=True)
    if (row.version if row else 0) != body.version:
        raise stale()
    if row is None:
        row = MessageProvider(channel=channel, provider=body.provider, version=0)
        db.add(row)
    before = {
        "provider": row.provider,
        "mode": row.mode,
        "settings": row.settings,
        "test_recipient": row.test_recipient,
    }
    # Where the stored credential goes may only change with the credential typed again: otherwise
    # pointing the channel at another server would hand it the stored password.
    target = (
        "provider",
        "host",
        "port",
        "security",
        "username",
        "account_sid",
        "phone_number_id",
        "api_version",
    )
    moved = row.provider != body.provider or any(
        (row.settings or {}).get(k, "") != settings.get(k, "") for k in target
    )
    if row.secret_ciphertext and moved and not body.secret:
        raise field_error(
            "secret", f"Type the {kind.secret_label.lower()} again: the server or account changed"
        )
    if settings.get("username") and settings.get("security") == "none":
        raise field_error(
            "settings.security", "Use starttls or ssl: a password is never sent unencrypted"
        )
    if body.secret:
        row.secret_ciphertext = cipher().encrypt(body.secret.encode()).decode()
    if body.mode in ("sandbox", "production") and not row.secret_ciphertext:
        raise field_error("secret", f"{kind.secret_label} is needed")
    row.provider, row.mode, row.settings, row.test_recipient = (
        body.provider,
        body.mode,
        settings,
        test_to,
    )
    row.version, row.updated_at = (row.version or 0) + 1, utcnow()
    after = {
        "provider": row.provider,
        "mode": row.mode,
        "settings": row.settings,
        "test_recipient": row.test_recipient,
    }
    changes = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
    if "test_recipient" in changes:
        changes["test_recipient"] = [mask(x) if x else "" for x in changes["test_recipient"]]
    if body.secret:
        changes["secret"] = ["[hidden]", "[hidden]"]
    record(db, p.user, "messages.provider", "message_provider", channel,
           f"{channel} messages: {row.provider}, {row.mode}", changes, body.reason)  # fmt: skip
    try:
        db.commit()
    except IntegrityError:  # someone else set this channel up a moment ago
        raise stale() from None
    return _provider_row(channel, row)


@router.post("/messages/providers/{channel}/test")
def test_provider(channel: Literal["email", "sms", "whatsapp"], p: require("notifications.configure"), db: DB) -> dict:  # fmt: skip
    """Checks the credentials with the provider. No message is sent."""
    row = db.get(MessageProvider, channel, with_for_update=True)
    if row is None or row.mode == "off":
        raise conflict("Switch the channel on first")
    if row.mode == "mock":
        ok, msg = True, "Mock mode: nothing is contacted, every message is recorded here only"
    elif not server_allows(row.mode):  # the server contacts nobody, not even to test
        raise conflict(blocked_reason(row))
    else:
        try:
            ok, msg = True, adapter(row).test()
        except (Retry, Unsure, ValueError, KeyError) as e:
            ok, msg = False, redact(str(e))
    row.last_test_at, row.last_test_ok, row.last_test_message = utcnow(), ok, msg[:300]
    record(db, p.user, "messages.test", "message_provider", channel, f"{channel} connection test: {'passed' if ok else 'failed'}")  # fmt: skip
    db.commit()
    return {"ok": ok, "message": msg}


@router.get("/messages/templates")
def list_templates(_: require("notifications.configure"), db: DB) -> dict:
    out = []
    for event, ev in EVENTS.items():
        for channel in CHANNELS:
            w = wording(db, event, channel)
            if w is None:
                continue
            t = db.scalar(select(MessageTemplate).where(MessageTemplate.event == event, MessageTemplate.channel == channel))  # fmt: skip
            out.append({"event": event, "label": ev.label, "channel": channel, "audience": "customer" if channel in ("sms", "whatsapp", "customer_email") else "staff",
                        "fields": [*ev.vars, "company"] + (["url"] if channel == "email" else []),
                        "subject": w.subject, "body": w.body, "provider_template": w.provider_template, "language": w.language,
                        "active": w.active, "customised": t is not None, "version": t.version if t else 0})  # fmt: skip
    return {"items": out}


class TemplateIn(BaseModel):
    version: int
    subject: Text = ""
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    provider_template: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=120, pattern=r"^[a-z0-9_]*$")
    ] = ""
    language: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z]{2}(_[A-Z]{2})?$")
    ] = "en"
    active: bool = True


def _checked(event: str, channel: str, subject: str, body: str) -> None:
    if event not in EVENTS or default_template(event, channel) is None:
        raise HTTPException(404, "There is no such message")
    for name, text in (("subject", subject), ("body", body)):
        if err := check_text(text, event):
            raise field_error(name, err)
    if channel in ("sms", "whatsapp") and len(body) > 640:
        raise field_error("body", "Keep text messages under 640 characters")
    if channel in ("in_app", "email", "customer_email") and not subject:
        raise field_error("subject", "A title is needed")


@router.put("/messages/templates/{event}/{channel}")
def save_template(event: str, channel: str, body: TemplateIn, p: require("notifications.configure"), db: DB) -> dict:  # fmt: skip
    _checked(event, channel, body.subject, body.body)
    t = db.scalar(select(MessageTemplate).where(MessageTemplate.event == event, MessageTemplate.channel == channel)
                  .with_for_update())  # fmt: skip
    if (t.version if t else 0) != body.version:
        raise stale()
    if t is None:
        t = MessageTemplate(event=event, channel=channel, version=0)
        db.add(t)
    before = {"subject": t.subject, "body": t.body, "active": t.active}
    t.subject, t.body, t.provider_template, t.language, t.active = (body.subject, body.body, body.provider_template,
                                                                    body.language, body.active)  # fmt: skip
    t.version, t.updated_at = (t.version or 0) + 1, utcnow()
    record(db, p.user, "messages.template", "message_template", f"{event}/{channel}", f"Message wording changed: {EVENTS[event].label}, {channel}",
           {k: [before[k], getattr(t, k)] for k in before if before[k] != getattr(t, k)})  # fmt: skip
    try:
        db.commit()
    except IntegrityError:  # someone else saved this wording a moment ago
        raise stale() from None
    return {"ok": True, "version": t.version}


class PreviewIn(BaseModel):
    event: str
    channel: Literal["in_app", "email", "customer_email", "sms", "whatsapp"]
    subject: Text = ""
    body: Annotated[str, StringConstraints(max_length=2000)]


SAMPLE = {"number": "SR-2026-00012", "summary": "SR-2026-00012 now scheduled", "by": "Sam Sample", "reason": "Proof of purchase is unreadable",
          "item": "12V 100Ah battery", "free": "3", "minimum": "10", "supplier": "Sample Supplier LLC", "expected": "2026-11-01",
          "days_late": "4", "target": "2026-11-01", "status": "scheduled", "contract": "AMC-2026-00003", "customer": "Sample Customer LLC",
          "date": "2026-11-12", "ends": "2026-12-31", "serial": "SMB-000123", "outcome": "approved", "expiry": "2028-10-31",
          "company": "SMET", "url": "https://smet.example/service/…"}  # fmt: skip


@router.post("/messages/templates/preview")
def preview(body: PreviewIn, _: require("notifications.configure")) -> dict:
    """Fills the wording with sample values (marked as samples) and says what is wrong, if anything."""
    if body.event not in EVENTS:
        raise HTTPException(404, "There is no such message")
    errors = {
        n: e
        for n, t in (("subject", body.subject), ("body", body.body))
        if (e := check_text(t, body.event))
    }
    text = render(body.body, SAMPLE)
    return {"subject": render(body.subject, SAMPLE), "body": text, "errors": errors, "chars": len(text),
            "segments": -(-len(text) // 153) if len(text) > 160 else 1}  # fmt: skip


def _delivery_row(d: MessageDelivery) -> dict:
    return {"id": d.id, "event": d.event, "label": EVENTS[d.event].label if d.event in EVENTS else d.event,
            "channel": d.channel, "provider": d.provider, "mode": d.mode, "to": mask(d.recipient),
            "sent_to": mask(d.sent_to) if d.sent_to else "", "subject": d.subject, "status": d.status,
            "attempts": d.attempts, "error": d.error, "entity_type": d.entity_type, "entity_id": d.entity_id,
            "created_at": d.created_at, "updated_at": d.updated_at}  # fmt: skip


@router.get("/messages/deliveries")
def list_deliveries(
    _: require("notifications.view"),
    db: DB,
    status_: Annotated[str | None, Query(alias="status", max_length=80)] = None,
    channel: Annotated[str | None, Query(max_length=10)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    """Recipients are always masked here; the business record holds the contact."""
    q = select(MessageDelivery)
    if status_:
        q = q.where(MessageDelivery.status.in_(status_.split(",")))
    if channel:
        q = q.where(MessageDelivery.channel == channel)
    return page(db, q.order_by(MessageDelivery.created_at.desc()), limit, offset, _delivery_row)


@router.post("/messages/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: uuid.UUID, p: require("notifications.configure"), db: DB) -> dict:
    d = db.get(MessageDelivery, delivery_id, with_for_update=True)
    if d is None:
        raise HTTPException(404, "Message not found")
    if d.status not in ("failed", "blocked", "unknown"):
        raise conflict("Only failed, blocked or interrupted messages can be sent again")
    if db.get(MessageOptOut, (d.channel, d.recipient)):
        raise conflict("The recipient has opted out of this channel")
    d.status, d.error, d.attempts, d.updated_at = "queued", "", 0, utcnow()
    enqueue(db, "message.send", {"id": str(d.id)}, max_attempts=MAX_ATTEMPTS + 1)
    record(db, p.user, "messages.retry", "message_delivery", d.id, f"{d.channel} message to {mask(d.recipient)} queued again")  # fmt: skip
    db.commit()
    return _delivery_row(d)


@router.get("/messages/opt-outs")
def list_opt_outs(_: require("notifications.configure"), db: DB, limit: Limit = 50, offset: Offset = 0) -> dict:  # fmt: skip
    q = select(MessageOptOut).order_by(MessageOptOut.created_at.desc())
    return page(db, q, limit, offset, lambda o: {"channel": o.channel, "address": o.address,
                                                 "reason": o.reason, "created_at": o.created_at})  # fmt: skip


class OptOutIn(BaseModel):
    channel: Literal["email", "sms", "whatsapp"]
    address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=160)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


@router.post("/messages/opt-outs", status_code=status.HTTP_201_CREATED)
def add_opt_out(body: OptOutIn, p: require("notifications.configure"), db: DB) -> dict:
    addr = normalise(body.channel, body.address, str(rules(db)["phone_country_code"]))
    if addr is None:
        raise field_error(
            "address", "Not an email address" if body.channel == "email" else "Not a phone number"
        )
    db.execute(insert(MessageOptOut).values(channel=body.channel, address=addr, reason=body.reason, created_by=p.user.id)
               .on_conflict_do_nothing())  # fmt: skip
    record(db, p.user, "messages.opt_out", "message_opt_out", body.channel, f"{mask(addr)} opted out of {body.channel}", reason=body.reason)  # fmt: skip
    db.commit()
    return {"channel": body.channel, "address": addr}


@router.delete("/messages/opt-outs/{channel}/{address}", status_code=status.HTTP_204_NO_CONTENT)
def remove_opt_out(channel: str, address: str, p: require("notifications.configure"), db: DB,
                   reason: Annotated[str, Query(min_length=3, max_length=300)]) -> None:  # fmt: skip
    """Only with a reason: someone asked to hear from SMET again."""
    o = db.get(MessageOptOut, (channel, address))
    if o is None:
        raise HTTPException(404, "Not on the list")
    db.delete(o)
    record(db, p.user, "messages.opt_in", "message_opt_out", channel, f"{mask(address)} may receive {channel} again", reason=reason)  # fmt: skip
    db.commit()
