# GLC MCP Platform

Public modular MCP at **https://mcp.glc-rag.hu** (see `docs/architecture-layers.md`).

## Agent registration (quick start for LLM agents)

Agents self-register, get **auto-approved** access to listed services, and receive an `api_token` immediately.

```bash
curl -sS -X POST 'https://mcp.glc-rag.hu/api/auth/register' \
  -H 'Content-Type: application/json' \
  -d '{"email":"agent@example.com","password":"choose-a-strong-password","account_type":"agent"}'
```

Response includes `api_token` and `approved_services`. Then:

```bash
curl -sS -X POST 'https://mcp.glc-rag.hu/mcp' \
  -H 'Authorization: Bearer mcp_YOUR_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

| Resource | URL |
|----------|-----|
| Agent guide (HTML) | https://mcp.glc-rag.hu/guide/agent |
| Agent guide (MD) | https://mcp.glc-rag.hu/guide/agent.md |
| Web register | https://mcp.glc-rag.hu/register (check **Register as agent**) |
| Agent index | https://mcp.glc-rag.hu/llms.txt |
| Well-known | https://mcp.glc-rag.hu/.well-known/mcp |
| MCP endpoint | https://mcp.glc-rag.hu/mcp |

A system admin can suspend an agent (blocks login + MCP) or set service access back to `pending`.

## Human registration

1. Register at `/register` (human account)
2. Admin → request a service
3. System admin approves
4. Admin → create API token
5. Call `/mcp` with `Authorization: Bearer mcp_...`

## Quick start (local server)

```bash
# deps (once)
cd /home/pergel/mcp && /root/.local/bin/uv sync

# run
./scripts/run-server.sh
# -> http://127.0.0.1:8780
```

Bootstrap system-admin (from `.env`):

- email: `admin@mcp.local`
- password: `ChangeMeAdmin1!`

## Layout

| Path | Role |
|------|------|
| `packages/core` | identity, MCP protocol, docs generator, encryption |
| `packages/hello` | hello_ping smoke service |
| `packages/geo` | geo MCP tools (read-only `rag_dev.geo_entities`) |
| `apps/mcp-server` | FastAPI wiring |
| `apps/web` | public / admin / system-admin SPA |
| `infra/nginx` | vhost for mcp.glc-rag.hu |

## Databases

- App: PostgreSQL **`MCP`**
- Geo data: PostgreSQL **`rag_dev`** on `:5433` (read-only from MCP)

## Nginx

```bash
sudo cp /home/pergel/mcp/infra/nginx/mcp.glc-rag.hu.conf /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/mcp.glc-rag.hu.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# TLS:
# sudo certbot --nginx -d mcp.glc-rag.hu
```

## Systemd

```bash
sudo cp /home/pergel/mcp/infra/mcp-platform.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mcp-platform
```
