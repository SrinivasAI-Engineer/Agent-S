from __future__ import annotations

import json
import hashlib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.config import settings
from app.logging import get_logger
from app.security import decrypt_str, encrypt_str
from app.state import now_iso

log = get_logger(__name__)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|awaiting_human|awaiting_auth|completed|terminated
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(128), default="")  # url-hash etc.

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TokenStore(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # twitter|linkedin

    encrypted_json: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SocialConnection(Base):
    """Multiple Twitter/LinkedIn accounts per user; each row = one connected account."""
    __tablename__ = "social_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)  # str(User.id)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # twitter|linkedin
    account_id: Mapped[str] = mapped_column(String(128), index=True)  # provider's user id (e.g. twitter id, linkedin urn)
    display_name: Mapped[str] = mapped_column(String(255), default="")  # @handle or profile name
    label: Mapped[str] = mapped_column(String(128), default="")  # user-defined e.g. "Work", "Personal"
    encrypted_json: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # twitter|linkedin
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    code_verifier: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


engine = create_engine(settings.database_url, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


def save_execution_state(execution_id: str, state: dict[str, Any], status: Optional[str] = None) -> None:
    with get_session() as s:
        ex = s.scalar(select(Execution).where(Execution.execution_id == execution_id))
        if ex is None:
            raise RuntimeError(f"Execution not found: {execution_id}")
        ex.state_json = json.dumps(state, ensure_ascii=False)
        if status:
            ex.status = status
        s.add(ex)
        s.commit()


def create_execution(execution_id: str, user_id: str, url: str, initial_state: dict[str, Any], idempotency_key: str) -> None:
    with get_session() as s:
        ex = Execution(execution_id=execution_id, user_id=user_id, url=url, state_json=json.dumps(initial_state), idempotency_key=idempotency_key)
        s.add(ex)
        s.commit()


def mark_stuck_running_executions(reason: str) -> int:
    """Mark all executions with status 'running' as terminated (e.g. after server restart). Returns count updated."""
    with get_session() as s:
        rows = list(s.scalars(select(Execution).where(Execution.status == "running")).all())
        for ex in rows:
            state = json.loads(ex.state_json or "{}")
            state["terminated"] = True
            state["terminate_reason"] = reason
            state["updated_at"] = now_iso()
            ex.state_json = json.dumps(state, ensure_ascii=False)
            ex.status = "terminated"
            s.add(ex)
        s.commit()
        return len(rows)


def get_execution(execution_id: str) -> Execution:
    with get_session() as s:
        ex = s.scalar(select(Execution).where(Execution.execution_id == execution_id))
        if ex is None:
            raise RuntimeError(f"Execution not found: {execution_id}")
        return ex


def list_inbox(statuses: list[str], user_id: Optional[str] = None) -> list[Execution]:
    with get_session() as s:
        stmt = select(Execution).where(Execution.status.in_(statuses))
        if user_id is not None:
            stmt = stmt.where(Execution.user_id == user_id)
        stmt = stmt.order_by(Execution.updated_at.desc())
        return list(s.scalars(stmt).all())


def compute_idempotency_key(user_id: str, url: str) -> str:
    h = hashlib.sha256()
    h.update((user_id + "\n" + url).encode("utf-8"))
    return h.hexdigest()[:32]


def find_execution_by_idempotency(user_id: str, idempotency_key: str) -> Optional[Execution]:
    with get_session() as s:
        stmt = select(Execution).where(Execution.user_id == user_id, Execution.idempotency_key == idempotency_key)
        return s.scalar(stmt)


def upsert_tokens(user_id: str, provider: str, token_payload: dict[str, Any], expires_at: Optional[datetime]) -> None:
    plaintext = json.dumps(token_payload, ensure_ascii=False)
    encrypted = encrypt_str(plaintext)
    with get_session() as s:
        row = s.scalar(select(TokenStore).where(TokenStore.user_id == user_id, TokenStore.provider == provider))
        if row is None:
            row = TokenStore(user_id=user_id, provider=provider)
        row.encrypted_json = encrypted
        row.expires_at = expires_at
        s.add(row)
        s.commit()


def get_tokens(user_id: str, provider: str) -> Optional[dict[str, Any]]:
    with get_session() as s:
        row = s.scalar(select(TokenStore).where(TokenStore.user_id == user_id, TokenStore.provider == provider))
        if row is None or not row.encrypted_json:
            return None
        plaintext = decrypt_str(row.encrypted_json)
        return json.loads(plaintext)


def get_tokens_expiry(user_id: str, provider: str) -> Optional[datetime]:
    with get_session() as s:
        row = s.scalar(select(TokenStore).where(TokenStore.user_id == user_id, TokenStore.provider == provider))
        return None if row is None else row.expires_at


def store_oauth_state(state: str, provider: str, user_id: str, code_verifier: str) -> None:
    with get_session() as s:
        row = OAuthState(state=state, provider=provider, user_id=user_id, code_verifier=code_verifier)
        s.add(row)
        s.commit()


def pop_oauth_state(state: str, provider: str) -> Optional[dict[str, str]]:
    with get_session() as s:
        row = s.scalar(select(OAuthState).where(OAuthState.state == state, OAuthState.provider == provider))
        if row is None:
            return None
        data = {"state": row.state, "provider": row.provider, "user_id": row.user_id, "code_verifier": row.code_verifier}
        s.delete(row)
        s.commit()
        return data


# ---- Users (signup/login) ----
def create_user(email: str, password_hash: str) -> "User":
    with get_session() as s:
        u = User(email=email.strip().lower(), password_hash=password_hash)
        s.add(u)
        s.commit()
        s.refresh(u)
        return u


def get_user_by_email(email: str) -> Optional[User]:
    with get_session() as s:
        return s.scalar(select(User).where(User.email == email.strip().lower()))


def get_user_by_id(user_id: str) -> Optional[User]:
    with get_session() as s:
        try:
            uid = int(user_id)
        except ValueError:
            return None
        return s.scalar(select(User).where(User.id == uid))


# ---- Social connections (multiple accounts per user) ----
def add_connection(
    user_id: str,
    provider: str,
    account_id: str,
    display_name: str,
    token_payload: dict[str, Any],
    expires_at: Optional[datetime] = None,
    label: str = "",
    is_default: bool = False,
) -> "SocialConnection":
    plaintext = json.dumps(token_payload, ensure_ascii=False)
    encrypted = encrypt_str(plaintext)
    with get_session() as s:
        # If this is first connection for (user_id, provider) or is_default=True, set as default
        existing = list(s.scalars(select(SocialConnection).where(SocialConnection.user_id == user_id, SocialConnection.provider == provider)))
        if is_default or not existing:
            for row in existing:
                row.is_default = False
                s.add(row)
        conn = SocialConnection(
            user_id=user_id,
            provider=provider,
            account_id=account_id,
            display_name=display_name or account_id,
            label=label.strip(),
            encrypted_json=encrypted,
            expires_at=expires_at,
            is_default=is_default or not existing,
        )
        s.add(conn)
        s.commit()
        s.refresh(conn)
        return conn


def list_connections(user_id: str) -> list[dict[str, Any]]:
    with get_session() as s:
        rows = list(s.scalars(select(SocialConnection).where(SocialConnection.user_id == user_id).order_by(SocialConnection.provider, SocialConnection.is_default.desc(), SocialConnection.id)))
        return [
            {
                "id": r.id,
                "provider": r.provider,
                "account_id": r.account_id,
                "display_name": r.display_name,
                "label": r.label or r.display_name or str(r.account_id),
                "is_default": r.is_default,
            }
            for r in rows
        ]


def get_connection(connection_id: int, user_id: Optional[str] = None) -> Optional[SocialConnection]:
    with get_session() as s:
        stmt = select(SocialConnection).where(SocialConnection.id == connection_id)
        if user_id is not None:
            stmt = stmt.where(SocialConnection.user_id == user_id)
        return s.scalar(stmt)


def get_connection_tokens(connection_id: int) -> Optional[dict[str, Any]]:
    row = get_connection(connection_id)
    if row is None or not row.encrypted_json:
        return None
    plaintext = decrypt_str(row.encrypted_json)
    return json.loads(plaintext)


def get_default_connection_tokens(user_id: str, provider: str) -> Optional[dict[str, Any]]:
    """Return tokens for the default connection for (user_id, provider), or first if no default."""
    with get_session() as s:
        row = s.scalar(
            select(SocialConnection)
            .where(SocialConnection.user_id == user_id, SocialConnection.provider == provider)
            .order_by(SocialConnection.is_default.desc(), SocialConnection.id)
            .limit(1)
        )
        if row is None or not row.encrypted_json:
            return None
        plaintext = decrypt_str(row.encrypted_json)
        return json.loads(plaintext)


def get_default_connection_expiry(user_id: str, provider: str) -> Optional[datetime]:
    """Return expires_at for the default connection for (user_id, provider), or None."""
    with get_session() as s:
        row = s.scalar(
            select(SocialConnection)
            .where(SocialConnection.user_id == user_id, SocialConnection.provider == provider)
            .order_by(SocialConnection.is_default.desc(), SocialConnection.id)
            .limit(1)
        )
        return row.expires_at if row else None


def get_default_connection_id(user_id: str, provider: str) -> Optional[int]:
    """Return the default connection id for (user_id, provider), or first connection's id."""
    with get_session() as s:
        row = s.scalar(
            select(SocialConnection)
            .where(SocialConnection.user_id == user_id, SocialConnection.provider == provider)
            .order_by(SocialConnection.is_default.desc(), SocialConnection.id)
            .limit(1)
        )
        return row.id if row else None


def update_connection_tokens(
    connection_id: int, user_id: str, token_payload: dict[str, Any], expires_at: Optional[datetime] = None
) -> bool:
    """Update stored tokens for a connection (e.g. after refresh)."""
    plaintext = json.dumps(token_payload, ensure_ascii=False)
    encrypted = encrypt_str(plaintext)
    with get_session() as s:
        row = s.scalar(select(SocialConnection).where(SocialConnection.id == connection_id, SocialConnection.user_id == user_id))
        if row is None:
            log.warning("update_connection_tokens: no row for connection_id=%s user_id=%s (deleted or wrong user)", connection_id, user_id)
            return False
        row.encrypted_json = encrypted
        if expires_at is not None:
            row.expires_at = expires_at
        s.add(row)
        s.commit()
        return True


def delete_connection(connection_id: int, user_id: str) -> bool:
    with get_session() as s:
        row = s.scalar(select(SocialConnection).where(SocialConnection.id == connection_id, SocialConnection.user_id == user_id))
        if row is None:
            return False
        s.delete(row)
        # If was default, make another connection default
        remaining = list(s.scalars(select(SocialConnection).where(SocialConnection.user_id == user_id, SocialConnection.provider == row.provider)))
        if remaining and not any(r.is_default for r in remaining):
            remaining[0].is_default = True
            s.add(remaining[0])
        s.commit()
        return True


def update_connection(connection_id: int, user_id: str, label: Optional[str] = None, is_default: Optional[bool] = None) -> bool:
    with get_session() as s:
        row = s.scalar(select(SocialConnection).where(SocialConnection.id == connection_id, SocialConnection.user_id == user_id))
        if row is None:
            return False
        if label is not None:
            row.label = label.strip()
        if is_default is not None and is_default:
            # Unset other defaults for same provider
            for r in s.scalars(select(SocialConnection).where(SocialConnection.user_id == user_id, SocialConnection.provider == row.provider)):
                r.is_default = r.id == connection_id
                s.add(r)
        s.add(row)
        s.commit()
        return True

