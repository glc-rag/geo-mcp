"""Shared agent-registration copy used by docs generator, README, and public UI."""

AGENT_REGISTER_PATH = "/api/auth/register"
AGENT_GUIDE_PATH = "/guide/agent"


def agent_registration_markdown(*, base: str) -> str:
    base = base.rstrip("/")
    return f"""# Agent registration

Agents can self-register and receive an API token immediately.
Listed MCP services are **auto-approved**. A system admin may suspend the
agent account or move service access back to `pending`.

## Endpoint

`POST {base}{AGENT_REGISTER_PATH}`

## Request

```http
POST {base}{AGENT_REGISTER_PATH}
Content-Type: application/json

{{
  "email": "agent@example.com",
  "password": "choose-a-strong-password",
  "account_type": "agent"
}}
```

## Response (selected fields)

```json
{{
  "user": {{
    "email": "agent@example.com",
    "account_type": "agent",
    "status": "active",
    "role": "org_admin"
  }},
  "approved_services": ["hello", "geo"],
  "api_token": "mcp_...",
  "access_token": "<JWT for web/API>",
  "token_type": "bearer"
}}
```

Save `api_token` — it authenticates the MCP endpoint.

## Call MCP

```http
POST {base}/mcp
Authorization: Bearer mcp_...
Content-Type: application/json

{{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {{}}
}}
```

## curl

```bash
curl -sS -X POST '{base}{AGENT_REGISTER_PATH}' \\
  -H 'Content-Type: application/json' \\
  -d '{{"email":"agent@example.com","password":"choose-a-strong-password","account_type":"agent"}}'
```

## Cursor `mcp.json`

```json
{{
  "mcpServers": {{
    "glc-mcp": {{
      "url": "{base}/mcp",
      "headers": {{
        "Authorization": "Bearer mcp_YOUR_TOKEN"
      }}
    }}
  }}
}}
```

## Human vs agent

| | Human | Agent |
|--|-------|-------|
| `account_type` | `human` (default) | `agent` |
| Service access | Request in Admin → system-admin approve | Auto-approved on register |
| API token | Create in Admin after approval | Returned as `api_token` |
| Suspension | System admin can suspend | Same |

## Links

- Web register (check **Register as agent**): `{base}/register`
- This guide: `{base}{AGENT_GUIDE_PATH}`
- Markdown: `{base}{AGENT_GUIDE_PATH}.md`
- Catalog: `{base}/guide/catalog.md`
- Agent index: `{base}/llms.txt`
- MCP: `{base}/mcp`
"""


def agent_registration_curl(*, base: str) -> str:
    base = base.rstrip("/")
    return (
        f"curl -sS -X POST '{base}{AGENT_REGISTER_PATH}' \\\n"
        f"  -H 'Content-Type: application/json' \\\n"
        f"  -d '{{\"email\":\"agent@example.com\",\"password\":\"choose-a-strong-password\",\"account_type\":\"agent\"}}'"
    )
