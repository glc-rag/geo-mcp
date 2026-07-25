from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("/home/pergel/mcp/.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    public_base_url: str = "https://mcp.glc-rag.hu"
    host: str = "127.0.0.1"
    port: int = 8780

    # App DB — PostgreSQL database name MCP
    database_url: str = "postgresql://mcp_app:mcp_app_change_me@127.0.0.1:5432/MCP"
    # Geo DB — empty + pgvector
    geo_database_url: str = "postgresql://mcp_app:mcp_app_change_me@127.0.0.1:5432/mcp_geo"

    session_ttl_hours: int = 12
    cookie_name: str = "mcp_session"
    cookie_secure: bool = False  # True behind HTTPS in prod
    cookie_samesite: str = "lax"

    bootstrap_admin_email: str = "admin@mcp.local"
    bootstrap_admin_password: str = "ChangeMeAdmin1!"
    require_email_verify: bool = False

    rate_login_per_min: int = 10
    rate_api_per_min: int = 120
    rate_mcp_per_min: int = 300

    # AES-256 key (urlsafe b64, 32 bytes) to store API tokens for Admin re-display
    token_store_key_b64: str = ""

    jwt_secret: str = ""
    jwt_ttl_hours: int = 12

    web_dist_dir: str = ""  # filled at runtime if empty


@lru_cache
def get_settings() -> Settings:
    return Settings()


def token_store_key_bytes() -> bytes:
    """Derive / load 32-byte key for encrypting stored API tokens."""
    import base64
    import hashlib

    s = get_settings()
    if s.token_store_key_b64:
        raw = base64.urlsafe_b64decode(s.token_store_key_b64.encode("ascii"))
        if len(raw) != 32:
            raise RuntimeError("TOKEN_STORE_KEY_B64 must decode to 32 bytes")
        return raw
    # Deterministic fallback from DB URL (dev); set TOKEN_STORE_KEY_B64 in prod
    return hashlib.sha256(s.database_url.encode("utf-8")).digest()
