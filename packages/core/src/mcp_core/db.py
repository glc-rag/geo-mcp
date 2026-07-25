from __future__ import annotations

import asyncpg

from mcp_core.settings import Settings

_pool: asyncpg.Pool | None = None
_geo_pool: asyncpg.Pool | None = None


async def init_pools(settings: Settings) -> None:
    global _pool, _geo_pool
    _pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=10)
    # Geo / rag_dev: force read-only transactions (never mutate RAG data).
    _geo_pool = await asyncpg.create_pool(
        dsn=settings.geo_database_url,
        min_size=1,
        max_size=8,
        command_timeout=60,
        server_settings={"default_transaction_read_only": "on"},
    )


async def close_pools() -> None:
    global _pool, _geo_pool
    if _pool is not None:
        await _pool.close()
        _pool = None
    if _geo_pool is not None:
        await _geo_pool.close()
        _geo_pool = None


def app_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("App DB pool not initialized")
    return _pool


def geo_pool() -> asyncpg.Pool:
    if _geo_pool is None:
        raise RuntimeError("Geo DB pool not initialized")
    return _geo_pool
