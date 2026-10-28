"""The audit log: who did what, when, what changed and why. Append-only (database trigger)."""

from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent

# Set per request by the middleware in main.py, so callers don't pass the request around.
request_info: ContextVar[dict] = ContextVar("request_info", default={})  # noqa: B039 - read-only default

SENSITIVE_FIELDS = {"password", "password_hash", "token", "code", "secret"}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list]:
    """{field: [old, new]} for fields that changed; sensitive values are never stored."""
    out = {}
    for k in before.keys() | after.keys():
        old, new = before.get(k), after.get(k)
        if old != new:
            out[k] = ["[hidden]", "[hidden]"] if k in SENSITIVE_FIELDS else [old, new]
    return out


def record(
    db: Session,
    actor,
    action: str,
    entity_type: str = "",
    entity_id: object = "",
    summary: str = "",
    changes: dict | None = None,
    reason: str = "",
) -> None:
    """Add an audit row in the caller's transaction, so it commits (or rolls back) with the change.
    `actor` is a User or None (system)."""
    info = request_info.get()
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            actor_name=actor.name if actor else "System",
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            summary=summary[:300],
            changes=changes or {},
            reason=reason[:500],
            ip=info.get("ip", ""),
            request_id=info.get("request_id", ""),
        )
    )
