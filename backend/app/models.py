"""Tables. Phase 1: company profile, users, roles, sessions, audit log, jobs.

Conventions: UUID keys, timestamps are timezone-aware UTC (`timestamptz`), shown in the company's
time zone by the web app. Editable records carry `version` for optimistic locking.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _ts(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), **kw)


class Company(Base):
    """The one company this installation serves (single row, id = 1)."""

    __tablename__ = "company"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(64), server_default="Asia/Dubai")
    currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default=false())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    # Who the role is for: internal staff, or a distributor's / customer's portal users.
    audience: Mapped[str] = mapped_column(String(12), server_default="internal")
    # Set on the role templates SMET ships (stable even if the role is renamed); null = custom.
    template_key: Mapped[str | None] = mapped_column(String(40), unique=True)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True, index=True
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = _id()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the cookie
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only: a database trigger refuses UPDATE, DELETE and TRUNCATE (migration 0001)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = _ts(index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)  # no FK: outlives users
    actor_name: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), server_default="")
    entity_id: Mapped[str] = mapped_column(String(64), server_default="")
    summary: Mapped[str] = mapped_column(String(300), server_default="")
    changes: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    reason: Mapped[str] = mapped_column(String(500), server_default="")
    ip: Mapped[str] = mapped_column(String(45), server_default="")
    request_id: Mapped[str] = mapped_column(String(16), server_default="")


class TemplateGrant(Base):
    """A template permission already given to a shipped role once. Never given again, so a
    permission an administrator removed stays removed when a later version adds new ones."""

    __tablename__ = "template_grants"
    template_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_due", "status", "run_at"),)
    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    key: Mapped[str | None] = mapped_column(String(200), unique=True)  # idempotency key
    status: Mapped[str] = mapped_column(
        String(10), server_default="pending"
    )  # pending|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5")
    run_at: Mapped[datetime] = _ts()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(300), server_default="")
    created_at: Mapped[datetime] = _ts()
