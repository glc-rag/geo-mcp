from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from mcp_core.encryption import cek_to_b64, new_cek, open_secret, seal_secret
from mcp_core.settings import token_store_key_bytes

ph = PasswordHasher()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class UserContext:
    user_id: UUID
    org_id: UUID
    email: str
    role: str
    account_type: str = "human"  # human | agent
    status: str = "active"  # active | suspended
    session_id: UUID | None = None
    cek_b64: str | None = None


@dataclass
class McpAuthContext:
    org_id: UUID
    api_key_id: UUID
    approved_services: list[str]


def _row_user(row: asyncpg.Record, *, session_id: UUID | None = None, cek_b64: str | None = None) -> UserContext:
    return UserContext(
        user_id=row["user_id"] if "user_id" in row.keys() else row["id"],
        org_id=row["org_id"],
        email=row["email"],
        role=row["role"],
        account_type=row["account_type"] if "account_type" in row.keys() else "human",
        status=row["status"] if "status" in row.keys() else "active",
        session_id=session_id,
        cek_b64=cek_b64,
    )


async def ensure_schema(pool: asyncpg.Pool, schema_sql: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)


async def bootstrap_admin(
    pool: asyncpg.Pool,
    *,
    email: str,
    password: str,
) -> None:
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM users WHERE role = 'system_admin' LIMIT 1"
        )
        if existing:
            return
        async with conn.transaction():
            org_id = await conn.fetchval(
                "INSERT INTO orgs(name) VALUES ($1) RETURNING id",
                "system",
            )
            await conn.execute(
                """
                INSERT INTO users(org_id, email, password_hash, role, email_verified)
                VALUES ($1, $2, $3, 'system_admin', TRUE)
                """,
                org_id,
                email.lower().strip(),
                ph.hash(password),
            )


async def register_user(
    pool: asyncpg.Pool,
    email: str,
    password: str,
    *,
    account_type: str = "human",
    auto_approve_services: list[str] | None = None,
) -> tuple[UserContext, dict[str, Any] | None]:
    """Register org_admin. Agents get auto-approved services + API key.

    Returns (user, agent_bootstrap) where agent_bootstrap has api_token when account_type=agent.
    """
    email_n = email.lower().strip()
    atype = (account_type or "human").strip().lower()
    if atype not in {"human", "agent"}:
        raise ValueError("account_type must be human or agent")
    auto_services = list(auto_approve_services or [])
    agent_key: dict[str, Any] | None = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            org_id = await conn.fetchval(
                "INSERT INTO orgs(name) VALUES ($1) RETURNING id",
                email_n.split("@")[0] or "org",
            )
            row = await conn.fetchrow(
                """
                INSERT INTO users(org_id, email, password_hash, role, email_verified, account_type, status)
                VALUES ($1, $2, $3, 'org_admin', FALSE, $4, 'active')
                RETURNING id, org_id, email, role, account_type, status
                """,
                org_id,
                email_n,
                ph.hash(password),
                atype,
            )
            await _audit(
                conn,
                row["id"],
                org_id,
                "user.register",
                {"email": email_n, "account_type": atype},
            )
            if atype == "agent" and auto_services:
                for sid in auto_services:
                    await conn.execute(
                        """
                        INSERT INTO service_registrations(org_id, user_id, service_id, status)
                        VALUES ($1, $2, $3, 'approved')
                        ON CONFLICT (org_id, service_id) DO UPDATE
                          SET status = 'approved', updated_at = now(), user_id = EXCLUDED.user_id
                        """,
                        org_id,
                        row["id"],
                        sid,
                    )
                await _audit(
                    conn,
                    row["id"],
                    org_id,
                    "service.auto_approve_agent",
                    {"services": auto_services},
                )

    user = UserContext(
        user_id=row["id"],
        org_id=row["org_id"],
        email=row["email"],
        role=row["role"],
        account_type=row["account_type"],
        status=row["status"],
    )
    if atype == "agent":
        agent_key = await create_api_key(pool, user.org_id, user.user_id, name="agent-default")
    return user, agent_key


async def get_user(pool: asyncpg.Pool, user_id: UUID) -> UserContext | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, org_id, email, role, account_type, status
            FROM users WHERE id = $1
            """,
            user_id,
        )
    if not row:
        return None
    return _row_user(row)


async def authenticate(pool: asyncpg.Pool, email: str, password: str) -> UserContext | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, org_id, email, role, password_hash, account_type, status
            FROM users WHERE email = $1
            """,
            email.lower().strip(),
        )
        if not row:
            return None
        try:
            ph.verify(row["password_hash"], password)
        except VerifyMismatchError:
            return None
        if row["status"] != "active":
            return None
        return _row_user(row)


async def create_session(
    pool: asyncpg.Pool,
    user: UserContext,
    *,
    ttl_hours: int,
) -> tuple[str, str, datetime]:
    raw = secrets.token_urlsafe(32)
    cek = new_cek()
    cek_b64 = cek_to_b64(cek)
    expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    async with pool.acquire() as conn:
        sid = await conn.fetchval(
            """
            INSERT INTO sessions(user_id, token_hash, cek_b64, expires_at)
            VALUES ($1, $2, $3, $4) RETURNING id
            """,
            user.user_id,
            _hash_token(raw),
            cek_b64,
            expires,
        )
    return raw, cek_b64, expires


async def resolve_session(pool: asyncpg.Pool, raw_token: str) -> UserContext | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.id AS session_id, s.cek_b64, s.expires_at,
                   u.id AS user_id, u.org_id, u.email, u.role, u.account_type, u.status
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = $1
            """,
            _hash_token(raw_token),
        )
        if not row:
            return None
        if row["expires_at"] < datetime.now(timezone.utc):
            await conn.execute("DELETE FROM sessions WHERE id = $1", row["session_id"])
            return None
        if row["status"] != "active":
            return None
        return _row_user(row, session_id=row["session_id"], cek_b64=row["cek_b64"])


async def destroy_session(pool: asyncpg.Pool, raw_token: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sessions WHERE token_hash = $1",
            _hash_token(raw_token),
        )


async def create_api_key(
    pool: asyncpg.Pool, org_id: UUID, user_id: UUID, name: str = "default"
) -> dict[str, Any]:
    raw = "mcp_" + secrets.token_urlsafe(32)
    prefix = raw[:12]
    enc = seal_secret(token_store_key_bytes(), raw)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO api_keys(org_id, name, token_prefix, token_hash, token_enc)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, name, token_prefix, created_at
            """,
            org_id,
            name,
            prefix,
            _hash_token(raw),
            enc,
        )
        await _audit(conn, user_id, org_id, "api_key.create", {"prefix": prefix, "id": str(row["id"])})
    return {
        "id": row["id"],
        "name": row["name"],
        "token_prefix": row["token_prefix"],
        "token": raw,
        "created_at": row["created_at"],
    }


async def revoke_api_key(
    pool: asyncpg.Pool, org_id: UUID, user_id: UUID, key_id: UUID | None = None
) -> None:
    async with pool.acquire() as conn:
        if key_id is not None:
            await conn.execute(
                """
                UPDATE api_keys SET revoked_at = now()
                WHERE id = $1 AND org_id = $2 AND revoked_at IS NULL
                """,
                key_id,
                org_id,
            )
            await _audit(conn, user_id, org_id, "api_key.revoke", {"id": str(key_id)})
        else:
            await conn.execute(
                "UPDATE api_keys SET revoked_at = now() WHERE org_id = $1 AND revoked_at IS NULL",
                org_id,
            )
            await _audit(conn, user_id, org_id, "api_key.revoke_all", {})


async def list_api_keys(pool: asyncpg.Pool, org_id: UUID) -> list[dict[str, Any]]:
    """Own-org keys with recoverable token (for Admin copy/paste)."""
    store_key = token_store_key_bytes()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, token_prefix, token_enc, revoked_at, created_at
            FROM api_keys
            WHERE org_id = $1
            ORDER BY created_at DESC
            """,
            org_id,
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        token = None
        if r["token_enc"] and r["revoked_at"] is None:
            try:
                token = open_secret(store_key, r["token_enc"])
            except Exception:  # noqa: BLE001
                token = None
        out.append(
            {
                "id": r["id"],
                "name": r["name"],
                "token_prefix": r["token_prefix"],
                "token": token,
                "revoked": r["revoked_at"] is not None,
                "revoked_at": r["revoked_at"],
                "created_at": r["created_at"],
                "recoverable": token is not None,
            }
        )
    return out


async def get_active_api_key_meta(pool: asyncpg.Pool, org_id: UUID) -> dict[str, Any] | None:
    keys = await list_api_keys(pool, org_id)
    for k in keys:
        if not k["revoked"]:
            return k
    return None


async def resolve_api_key(pool: asyncpg.Pool, raw: str) -> McpAuthContext | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT k.id, k.org_id FROM api_keys k
            WHERE k.token_hash = $1 AND k.revoked_at IS NULL
              AND EXISTS (
                SELECT 1 FROM users u
                WHERE u.org_id = k.org_id AND u.status = 'active'
              )
            """,
            _hash_token(raw),
        )
        if not row:
            return None
        approved = await conn.fetch(
            """
            SELECT service_id FROM service_registrations
            WHERE org_id = $1 AND status = 'approved'
            """,
            row["org_id"],
        )
        return McpAuthContext(
            org_id=row["org_id"],
            api_key_id=row["id"],
            approved_services=[r["service_id"] for r in approved],
        )


async def request_service(
    pool: asyncpg.Pool, *, org_id: UUID, user_id: UUID, service_id: str
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO service_registrations(org_id, user_id, service_id, status)
            VALUES ($1, $2, $3, 'pending')
            ON CONFLICT (org_id, service_id) DO UPDATE
              SET status = CASE
                    WHEN service_registrations.status = 'approved' THEN service_registrations.status
                    ELSE 'pending'
                  END,
                  updated_at = now(),
                  user_id = EXCLUDED.user_id
            RETURNING id, org_id, service_id, status, created_at, updated_at
            """,
            org_id,
            user_id,
            service_id,
        )
        await _audit(
            conn, user_id, org_id, "service.request", {"service_id": service_id, "status": row["status"]}
        )
        return dict(row)


async def list_own_registrations(pool: asyncpg.Pool, org_id: UUID) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, service_id, status, created_at, updated_at
            FROM service_registrations WHERE org_id = $1
            ORDER BY created_at DESC
            """,
            org_id,
        )
        return [dict(r) for r in rows]


async def list_pending_registrations(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    return await list_registrations_sysadmin(pool, status="pending")


async def list_registrations_sysadmin(
    pool: asyncpg.Pool, *, status: str | None = None
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """
                SELECT r.id, r.org_id, r.user_id, r.service_id, r.status, r.created_at, r.updated_at,
                       u.email, u.account_type, u.status AS user_status, o.name AS org_name
                FROM service_registrations r
                JOIN users u ON u.id = r.user_id
                JOIN orgs o ON o.id = r.org_id
                WHERE r.status = $1
                ORDER BY r.updated_at DESC
                """,
                status,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT r.id, r.org_id, r.user_id, r.service_id, r.status, r.created_at, r.updated_at,
                       u.email, u.account_type, u.status AS user_status, o.name AS org_name
                FROM service_registrations r
                JOIN users u ON u.id = r.user_id
                JOIN orgs o ON o.id = r.org_id
                ORDER BY r.updated_at DESC
                LIMIT 500
                """
            )
        return [dict(r) for r in rows]


async def set_registration_status(
    pool: asyncpg.Pool,
    *,
    registration_id: UUID,
    status: str,
    actor_user_id: UUID,
) -> dict[str, Any] | None:
    # pending = suspend / re-queue for approval
    if status not in {"approved", "rejected", "revoked", "pending"}:
        raise ValueError("invalid status")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE service_registrations
            SET status = $2, updated_at = now()
            WHERE id = $1
            RETURNING id, org_id, service_id, status, updated_at
            """,
            registration_id,
            status,
        )
        if not row:
            return None
        action = "service.suspend" if status == "pending" else f"service.{status}"
        await _audit(
            conn,
            actor_user_id,
            row["org_id"],
            action,
            {"registration_id": str(registration_id), "service_id": row["service_id"], "status": status},
        )
        return dict(row)


async def list_users_sysadmin(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.email, u.role, u.org_id, u.account_type, u.status, u.created_at,
                   o.name AS org_name
            FROM users u JOIN orgs o ON o.id = u.org_id
            ORDER BY u.created_at DESC
            """
        )
        return [dict(r) for r in rows]


async def set_user_status(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    status: str,
    actor_user_id: UUID,
    suspend_registrations: bool = True,
) -> dict[str, Any] | None:
    """Suspend (status=suspended) or reactivate (status=active) a user.

    On suspend: drop sessions and optionally set all org service regs back to pending.
    """
    if status not in {"active", "suspended"}:
        raise ValueError("invalid user status")
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT id, org_id, email, role, account_type, status FROM users WHERE id = $1",
                user_id,
            )
            if not current:
                return None
            if current["role"] == "system_admin" and status == "suspended":
                raise ValueError("cannot suspend system_admin")
            row = await conn.fetchrow(
                """
                UPDATE users SET status = $2
                WHERE id = $1
                RETURNING id, org_id, email, role, account_type, status
                """,
                user_id,
                status,
            )
            if status == "suspended":
                await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
                if suspend_registrations:
                    await conn.execute(
                        """
                        UPDATE service_registrations
                        SET status = 'pending', updated_at = now()
                        WHERE org_id = $1 AND status = 'approved'
                        """,
                        row["org_id"],
                    )
            await _audit(
                conn,
                actor_user_id,
                row["org_id"],
                f"user.{status}",
                {
                    "user_id": str(user_id),
                    "email": row["email"],
                    "account_type": row["account_type"],
                    "suspend_registrations": suspend_registrations and status == "suspended",
                },
            )
            return dict(row)


async def list_audit(pool: asyncpg.Pool, *, org_id: UUID | None, limit: int = 100) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if org_id is None:
            rows = await conn.fetch(
                """
                SELECT id, actor_user_id, org_id, action, detail, created_at
                FROM audit_events ORDER BY id DESC LIMIT $1
                """,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, actor_user_id, org_id, action, detail, created_at
                FROM audit_events WHERE org_id = $1 ORDER BY id DESC LIMIT $2
                """,
                org_id,
                limit,
            )
        return [dict(r) for r in rows]


async def _audit(
    conn: asyncpg.Connection,
    actor_user_id: UUID | None,
    org_id: UUID | None,
    action: str,
    detail: dict[str, Any],
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_events(actor_user_id, org_id, action, detail)
        VALUES ($1, $2, $3, $4::jsonb)
        """,
        actor_user_id,
        org_id,
        action,
        __import__("json").dumps(detail),
    )
