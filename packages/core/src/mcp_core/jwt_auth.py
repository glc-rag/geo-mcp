"""JWT access tokens (same pattern as glc-rag.hu docs protection)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt

from mcp_core.identity import UserContext
from mcp_core.settings import get_settings

ALGORITHM = "HS256"
COOKIE_NAME = "mcp_jwt"


def _secret() -> str:
    s = get_settings()
    secret = (s.jwt_secret or "").strip()
    if not secret:
        # stable fallback from DB URL — set JWT_SECRET in .env for prod
        import hashlib

        secret = hashlib.sha256(s.database_url.encode()).hexdigest()
    return secret


def issue_access_token(user: UserContext, *, hours: int | None = None) -> str:
    settings = get_settings()
    ttl = hours if hours is not None else settings.jwt_ttl_hours
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.user_id),
        "org_id": str(user.org_id),
        "email": user.email,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ttl)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, _secret(), algorithms=[ALGORITHM])


def user_from_token(token: str) -> UserContext | None:
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    try:
        return UserContext(
            user_id=UUID(payload["sub"]),
            org_id=UUID(payload["org_id"]),
            email=str(payload["email"]),
            role=str(payload["role"]),
        )
    except (KeyError, ValueError):
        return None


def extract_bearer(request_headers: Any) -> str | None:
    auth = request_headers.get("Authorization") or request_headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None
