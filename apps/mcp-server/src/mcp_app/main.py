from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, ORJSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mcp_core import (
    DocGenerator,
    McpProtocol,
    SCHEMA_SQL,
    ServiceRegistry,
    authenticate,
    bootstrap_admin,
    close_pools,
    create_api_key,
    create_session,
    decrypt_payload,
    destroy_session,
    encrypt_payload,
    ensure_schema,
    get_settings,
    get_user,
    init_pools,
    app_pool,
    geo_pool,
    limiter,
    list_api_keys,
    list_audit,
    list_own_registrations,
    list_pending_registrations,
    list_registrations_sysadmin,
    list_users_sysadmin,
    register_user,
    request_service,
    resolve_api_key,
    resolve_session,
    revoke_api_key,
    set_registration_status,
    set_user_status,
)
from mcp_core.identity import UserContext
from mcp_core.jwt_auth import (
    COOKIE_NAME as JWT_COOKIE,
    extract_bearer,
    issue_access_token,
    user_from_token,
)
from mcp_geo import create_module as create_geo
from mcp_hello import create_module as create_hello


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    account_type: str = Field(default="human", description="human | agent")


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str


class UserStatusBody(BaseModel):
    status: str  # active | suspended


class ServiceRequestBody(BaseModel):
    service_id: str


class RegistrationDecisionBody(BaseModel):
    status: str  # approved | rejected | revoked | pending


class EncryptedEnvelope(BaseModel):
    iv: str
    ciphertext: str
    tag: str


def create_app() -> FastAPI:
    settings = get_settings()
    registry = ServiceRegistry()
    docs = DocGenerator(registry, public_base_url=settings.public_base_url)
    mcp = McpProtocol(registry, docs)

    # /docs = Swagger UI (public page, like glc-rag.hu).
    # API calls require JWT (Bearer) via Authorize — not a locked docs page.
    # /guide = public MCP service docs.
    app = FastAPI(
        title="GLC MCP Platform",
        default_response_class=ORJSONResponse,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    web_root = Path(__file__).resolve().parents[4] / "apps" / "web"
    assets_dir = web_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    def _jwt_user(request: Request) -> UserContext | None:
        token = extract_bearer(request.headers)
        if not token:
            token = request.cookies.get(JWT_COOKIE)
        if not token:
            return None
        return user_from_token(token)

    def _set_auth_cookies(resp: Response, *, session_raw: str, jwt_token: str, max_age: int) -> None:
        resp.set_cookie(
            settings.cookie_name,
            session_raw,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            max_age=max_age,
            path="/",
        )
        resp.set_cookie(
            JWT_COOKIE,
            jwt_token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            max_age=max_age,
            path="/",
        )

    @app.on_event("startup")
    async def _startup() -> None:
        await init_pools(settings)
        await ensure_schema(app_pool(), SCHEMA_SQL)
        async with app_pool().acquire() as conn:
            await conn.execute(
                "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS token_enc TEXT"
            )
        await bootstrap_admin(
            app_pool(),
            email=settings.bootstrap_admin_email,
            password=settings.bootstrap_admin_password,
        )
        registry.register(create_hello())
        registry.register(create_geo(geo_pool()))
        app.state.registry = registry
        app.state.docs = docs
        app.state.mcp = mcp
        app.state.settings = settings

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await close_pools()

    # ---------- helpers ----------

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    async def optional_user(request: Request) -> UserContext | None:
        # Session cookie first (keeps CEK for web UI); then JWT Bearer / mcp_jwt.
        # Always re-check DB status so suspended accounts are blocked immediately.
        raw = request.cookies.get(settings.cookie_name)
        if raw:
            sess = await resolve_session(app_pool(), raw)
            if sess is not None:
                return sess
        jwt_user = _jwt_user(request)
        if jwt_user is not None:
            live = await get_user(app_pool(), jwt_user.user_id)
            if live is None or live.status != "active":
                return None
            return live
        return None

    async def require_user(request: Request) -> UserContext:
        user = await optional_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        return user

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=getattr(app, "version", "0.1.0"),
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Login: POST /api/auth/login → access_token. Paste as Bearer.",
        }
        # Protect mutating / privileged API paths by default in Swagger.
        for path, methods in schema.get("paths", {}).items():
            if not path.startswith("/api/"):
                continue
            if path in {"/api/auth/login", "/api/auth/register", "/api/public/services"}:
                continue
            for method, op in methods.items():
                if method in {"get", "post", "put", "patch", "delete"} and isinstance(op, dict):
                    op.setdefault("security", [{"BearerAuth": []}])
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi

    async def require_org_admin(user: UserContext = Depends(require_user)) -> UserContext:
        if user.role not in {"org_admin", "system_admin"}:
            raise HTTPException(403, "Forbidden")
        return user

    async def require_sysadmin(user: UserContext = Depends(require_user)) -> UserContext:
        if user.role != "system_admin":
            raise HTTPException(403, "System admin only")
        return user

    async def read_api_json(request: Request, user: UserContext | None) -> Any:
        """Decrypt layer-2 payload when X-Payload-Encrypted: 1."""
        raw = await request.body()
        if not raw:
            return None
        data = json.loads(raw)
        if request.headers.get("X-Payload-Encrypted") == "1":
            if not user or not user.cek_b64:
                raise HTTPException(400, "Encrypted payload requires session CEK")
            from mcp_core.encryption import cek_from_b64

            return decrypt_payload(cek_from_b64(user.cek_b64), data)
        return data

    def maybe_encrypt(response_obj: Any, request: Request, user: UserContext | None) -> Any:
        if request.headers.get("X-Payload-Encrypted") == "1" and user and user.cek_b64:
            from mcp_core.encryption import cek_from_b64

            return encrypt_payload(cek_from_b64(user.cek_b64), response_obj)
        return response_obj

    # ---------- health ----------

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        try:
            async with app_pool().acquire() as conn:
                await conn.fetchval("SELECT 1")
            geo_ok, geo_msg = True, "ok"
            for m in registry.all():
                if m.id == "geo":
                    geo_ok, geo_msg = await m.health()
            return {"status": "ready", "app_db": "ok", "geo": {"ok": geo_ok, "message": geo_msg}}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, str(exc)) from exc

    # ---------- public catalog / docs / discovery ----------

    @app.get("/api/catalog")
    async def api_catalog() -> JSONResponse:
        return JSONResponse({"services": registry.catalog_public()})

    @app.get("/llms.txt")
    async def llms_txt() -> PlainTextResponse:
        return PlainTextResponse(docs.llms_txt(), media_type="text/plain; charset=utf-8")

    @app.get("/llms-full.txt")
    async def llms_full() -> PlainTextResponse:
        return PlainTextResponse(docs.llms_full(), media_type="text/plain; charset=utf-8")

    @app.get("/.well-known/mcp")
    async def well_known_mcp() -> dict[str, Any]:
        return docs.well_known_mcp()

    @app.get("/.well-known/mcp/server-card.json")
    async def server_card() -> dict[str, Any]:
        return docs.server_card()

    @app.get("/guide", response_class=HTMLResponse)
    async def guide_index() -> str:
        return docs.html_docs_index()

    @app.get("/guide/agent.md")
    async def guide_agent_md() -> PlainTextResponse:
        return PlainTextResponse(
            docs.agent_registration_markdown(), media_type="text/markdown; charset=utf-8"
        )

    @app.get("/guide/agent", response_class=HTMLResponse)
    async def guide_agent_html() -> str:
        return docs.html_agent_registration()

    @app.get("/guide/catalog.md")
    async def guide_catalog_md() -> PlainTextResponse:
        return PlainTextResponse(docs.catalog_markdown(), media_type="text/markdown; charset=utf-8")

    @app.get("/guide/{service_id}.md")
    async def guide_service_md(service_id: str) -> PlainTextResponse:
        if service_id == "agent":
            return PlainTextResponse(
                docs.agent_registration_markdown(), media_type="text/markdown; charset=utf-8"
            )
        mod = registry.get(service_id)
        if not mod or not mod.listed:
            raise HTTPException(404, "Unknown service")
        return PlainTextResponse(docs.service_markdown(mod), media_type="text/markdown; charset=utf-8")

    @app.get("/guide/{service_id}", response_class=HTMLResponse)
    async def guide_service_html(service_id: str) -> str:
        if service_id == "agent":
            return docs.html_agent_registration()
        mod = registry.get(service_id)
        if not mod or not mod.listed:
            raise HTTPException(404, "Unknown service")
        return docs.html_service(mod)

    # ---------- auth ----------

    @app.post("/api/auth/register")
    async def api_register(body: RegisterBody, request: Request) -> Response:
        if not limiter.allow(f"login:{client_ip(request)}", settings.rate_login_per_min):
            raise HTTPException(429, "Rate limit")
        account_type = (body.account_type or "human").strip().lower()
        auto_services: list[str] | None = None
        if account_type == "agent":
            auto_services = [m.id for m in registry.listed()]
        try:
            user, agent_key = await register_user(
                app_pool(),
                body.email,
                body.password,
                account_type=account_type,
                auto_approve_services=auto_services,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Registration failed: {exc}") from exc
        raw, cek_b64, expires = await create_session(
            app_pool(), user, ttl_hours=settings.session_ttl_hours
        )
        access_token = issue_access_token(user)
        payload: dict[str, Any] = {
            "user": {
                "email": user.email,
                "role": user.role,
                "org_id": str(user.org_id),
                "account_type": user.account_type,
                "status": user.status,
            },
            "access_token": access_token,
            "token_type": "bearer",
            "cek": cek_b64,
            "expires_at": expires.isoformat(),
        }
        if account_type == "agent":
            payload["approved_services"] = auto_services or []
            if agent_key:
                payload["api_token"] = agent_key["token"]
                payload["api_key"] = {
                    "id": str(agent_key["id"]),
                    "name": agent_key["name"],
                    "token_prefix": agent_key["token_prefix"],
                    "token": agent_key["token"],
                }
        resp = JSONResponse(payload)
        _set_auth_cookies(
            resp,
            session_raw=raw,
            jwt_token=access_token,
            max_age=settings.session_ttl_hours * 3600,
        )
        return resp

    @app.post("/api/auth/login")
    async def api_login(body: LoginBody, request: Request) -> Response:
        if not limiter.allow(f"login:{client_ip(request)}", settings.rate_login_per_min):
            raise HTTPException(429, "Rate limit")
        user = await authenticate(app_pool(), body.email, body.password)
        if not user:
            raise HTTPException(401, "Invalid credentials or account suspended")
        raw, cek_b64, expires = await create_session(
            app_pool(), user, ttl_hours=settings.session_ttl_hours
        )
        access_token = issue_access_token(user)
        resp = JSONResponse(
            {
                "user": {
                    "email": user.email,
                    "role": user.role,
                    "org_id": str(user.org_id),
                    "account_type": user.account_type,
                    "status": user.status,
                },
                "access_token": access_token,
                "token_type": "bearer",
                "cek": cek_b64,
                "expires_at": expires.isoformat(),
                "redirect": "/system-admin" if user.role == "system_admin" else "/admin",
            }
        )
        _set_auth_cookies(
            resp,
            session_raw=raw,
            jwt_token=access_token,
            max_age=settings.session_ttl_hours * 3600,
        )
        return resp

    @app.post("/api/auth/logout")
    async def api_logout(request: Request) -> Response:
        raw = request.cookies.get(settings.cookie_name)
        if raw:
            await destroy_session(app_pool(), raw)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(settings.cookie_name, path="/")
        resp.delete_cookie(JWT_COOKIE, path="/")
        return resp

    @app.get("/api/auth/me")
    async def api_me(request: Request, user: UserContext = Depends(require_user)) -> dict[str, Any]:
        # refresh JWT in response body for Swagger Authorize
        access_token = issue_access_token(user)
        return {
            "email": user.email,
            "role": user.role,
            "org_id": str(user.org_id),
            "user_id": str(user.user_id),
            "account_type": user.account_type,
            "status": user.status,
            "cek": user.cek_b64,
            "access_token": access_token,
            "token_type": "bearer",
        }

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version="0.1.0",
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schema["components"]["securitySchemes"]["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi

    # ---------- admin ----------

    @app.get("/api/admin/registrations")
    async def admin_regs(
        request: Request, user: UserContext = Depends(require_org_admin)
    ) -> Any:
        if not limiter.allow(f"api:{user.user_id}", settings.rate_api_per_min):
            raise HTTPException(429, "Rate limit")
        data = {"registrations": await list_own_registrations(app_pool(), user.org_id)}
        # serialize uuids/dates
        for r in data["registrations"]:
            r["id"] = str(r["id"])
            r["created_at"] = r["created_at"].isoformat()
            r["updated_at"] = r["updated_at"].isoformat()
        return maybe_encrypt(data, request, user)

    @app.post("/api/admin/registrations")
    async def admin_request_service(
        request: Request, user: UserContext = Depends(require_org_admin)
    ) -> Any:
        if not limiter.allow(f"api:{user.user_id}", settings.rate_api_per_min):
            raise HTTPException(429, "Rate limit")
        payload = await read_api_json(request, user)
        if payload is None:
            raise HTTPException(400, "Body required")
        service_id = payload.get("service_id")
        if not service_id or not registry.get(service_id):
            raise HTTPException(400, "Unknown service_id")
        # system_admin acting as org? still own org only
        row = await request_service(
            app_pool(), org_id=user.org_id, user_id=user.user_id, service_id=service_id
        )
        out = {
            "id": str(row["id"]),
            "service_id": row["service_id"],
            "status": row["status"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
        return maybe_encrypt(out, request, user)

    @app.get("/api/admin/api-key")
    async def admin_api_keys_list(
        request: Request, user: UserContext = Depends(require_org_admin)
    ) -> Any:
        if not limiter.allow(f"api:{user.user_id}", settings.rate_api_per_min):
            raise HTTPException(429, "Rate limit")
        keys = await list_api_keys(app_pool(), user.org_id)
        for k in keys:
            k["id"] = str(k["id"])
            k["created_at"] = k["created_at"].isoformat()
            if k["revoked_at"] is not None:
                k["revoked_at"] = k["revoked_at"].isoformat()
        return maybe_encrypt({"keys": keys}, request, user)

    @app.post("/api/admin/api-key")
    async def admin_api_key_create(
        request: Request, user: UserContext = Depends(require_org_admin)
    ) -> Any:
        payload = await read_api_json(request, user)
        name = "default"
        if isinstance(payload, dict) and payload.get("name"):
            name = str(payload["name"])[:80]
        created = await create_api_key(app_pool(), user.org_id, user.user_id, name=name)
        out = {
            "id": str(created["id"]),
            "name": created["name"],
            "token_prefix": created["token_prefix"],
            "token": created["token"],
            "created_at": created["created_at"].isoformat(),
        }
        return maybe_encrypt(out, request, user)

    @app.delete("/api/admin/api-key")
    async def admin_api_key_revoke(
        request: Request, user: UserContext = Depends(require_org_admin)
    ) -> Any:
        payload = await read_api_json(request, user)
        key_id = None
        if isinstance(payload, dict) and payload.get("id"):
            key_id = UUID(str(payload["id"]))
        await revoke_api_key(app_pool(), user.org_id, user.user_id, key_id=key_id)
        return maybe_encrypt({"ok": True}, request, user)

    @app.get("/api/admin/audit")
    async def admin_audit(
        request: Request, user: UserContext = Depends(require_org_admin)
    ) -> Any:
        rows = await list_audit(app_pool(), org_id=user.org_id)
        for r in rows:
            r["id"] = int(r["id"])
            r["actor_user_id"] = str(r["actor_user_id"]) if r["actor_user_id"] else None
            r["org_id"] = str(r["org_id"]) if r["org_id"] else None
            r["created_at"] = r["created_at"].isoformat()
            if isinstance(r["detail"], str):
                r["detail"] = json.loads(r["detail"])
        return maybe_encrypt({"events": rows}, request, user)

    # ---------- system-admin ----------

    @app.get("/api/system-admin/registrations/pending")
    async def sys_pending(
        request: Request, user: UserContext = Depends(require_sysadmin)
    ) -> Any:
        rows = await list_pending_registrations(app_pool())
        for r in rows:
            for k in ("id", "org_id", "user_id"):
                r[k] = str(r[k])
            r["created_at"] = r["created_at"].isoformat()
            if r.get("updated_at"):
                r["updated_at"] = r["updated_at"].isoformat()
        return maybe_encrypt({"registrations": rows}, request, user)

    @app.get("/api/system-admin/registrations")
    async def sys_registrations(
        request: Request, user: UserContext = Depends(require_sysadmin)
    ) -> Any:
        rows = await list_registrations_sysadmin(app_pool())
        for r in rows:
            for k in ("id", "org_id", "user_id"):
                r[k] = str(r[k])
            r["created_at"] = r["created_at"].isoformat()
            if r.get("updated_at"):
                r["updated_at"] = r["updated_at"].isoformat()
        return maybe_encrypt({"registrations": rows}, request, user)

    @app.post("/api/system-admin/registrations/{registration_id}")
    async def sys_decide(
        registration_id: UUID,
        request: Request,
        user: UserContext = Depends(require_sysadmin),
    ) -> Any:
        payload = await read_api_json(request, user)
        status = (payload or {}).get("status")
        try:
            row = await set_registration_status(
                app_pool(),
                registration_id=registration_id,
                status=status,
                actor_user_id=user.user_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not row:
            raise HTTPException(404, "Not found")
        out = {
            "id": str(row["id"]),
            "org_id": str(row["org_id"]),
            "service_id": row["service_id"],
            "status": row["status"],
            "updated_at": row["updated_at"].isoformat(),
        }
        return maybe_encrypt(out, request, user)

    @app.get("/api/system-admin/users")
    async def sys_users(
        request: Request, user: UserContext = Depends(require_sysadmin)
    ) -> Any:
        rows = await list_users_sysadmin(app_pool())
        for r in rows:
            r["id"] = str(r["id"])
            r["org_id"] = str(r["org_id"])
            r["created_at"] = r["created_at"].isoformat()
        return maybe_encrypt({"users": rows}, request, user)

    @app.post("/api/system-admin/users/{user_id}/status")
    async def sys_user_status(
        user_id: UUID,
        request: Request,
        user: UserContext = Depends(require_sysadmin),
    ) -> Any:
        payload = await read_api_json(request, user)
        status = (payload or {}).get("status")
        try:
            row = await set_user_status(
                app_pool(),
                user_id=user_id,
                status=status,
                actor_user_id=user.user_id,
                suspend_registrations=True,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not row:
            raise HTTPException(404, "Not found")
        out = {
            "id": str(row["id"]),
            "email": row["email"],
            "status": row["status"],
            "account_type": row["account_type"],
            "org_id": str(row["org_id"]),
        }
        return maybe_encrypt(out, request, user)

    @app.get("/api/system-admin/audit")
    async def sys_audit(
        request: Request, user: UserContext = Depends(require_sysadmin)
    ) -> Any:
        rows = await list_audit(app_pool(), org_id=None, limit=200)
        for r in rows:
            r["id"] = int(r["id"])
            r["actor_user_id"] = str(r["actor_user_id"]) if r["actor_user_id"] else None
            r["org_id"] = str(r["org_id"]) if r["org_id"] else None
            r["created_at"] = r["created_at"].isoformat()
            if isinstance(r["detail"], str):
                r["detail"] = json.loads(r["detail"])
        return maybe_encrypt({"events": rows}, request, user)

    # ---------- MCP streamable HTTP ----------

    def extract_api_key(request: Request) -> str | None:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.headers.get("X-Api-Key")

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
    async def mcp_endpoint(request: Request) -> Response:
        raw_key = extract_api_key(request)
        if not raw_key:
            raise HTTPException(401, "API key required")
        if not limiter.allow(f"mcp:{raw_key[:16]}", settings.rate_mcp_per_min):
            raise HTTPException(429, "Rate limit")
        auth_ctx = await resolve_api_key(app_pool(), raw_key)
        if not auth_ctx:
            raise HTTPException(401, "Invalid API key")

        if request.method == "DELETE":
            return Response(status_code=204)

        if request.method == "GET":
            # SSE stream placeholder — clients typically POST JSON-RPC
            return PlainTextResponse(
                "MCP streamable HTTP: POST JSON-RPC to this endpoint with Authorization Bearer token.\n",
                media_type="text/plain",
            )

        body = await request.json()
        session_id = request.headers.get("Mcp-Session-Id") or McpProtocol.new_session_id()

        async def handle_one(msg: dict[str, Any]) -> dict[str, Any] | None:
            return await mcp.handle(msg, auth_ctx)

        if isinstance(body, list):
            results = []
            for msg in body:
                r = await handle_one(msg)
                if r is not None:
                    results.append(r)
            payload: Any = results
        else:
            payload = await handle_one(body)
            if payload is None:
                return Response(status_code=202, headers={"Mcp-Session-Id": session_id})

        return JSONResponse(payload, headers={"Mcp-Session-Id": session_id})

    # ---------- static web ----------

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    index_file = web_root / "index.html"

    @app.middleware("http")
    async def no_cache_ui(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.endswith(".html") or path.startswith("/assets/") or path in {
            "/admin",
            "/login",
            "/register",
            "/system-admin",
        }:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa(full_path: str) -> HTMLResponse:
        # let API/docs/well-known already matched; SPA fallback
        if not index_file.is_file():
            return HTMLResponse("<h1>Web UI missing</h1>", status_code=500)
        html = index_file.read_text(encoding="utf-8")
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "mcp_app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
