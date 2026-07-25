# Agent registration

Canonical public guide (also served live):

- https://mcp.glc-rag.hu/guide/agent
- https://mcp.glc-rag.hu/guide/agent.md
- https://mcp.glc-rag.hu/llms.txt

## API

```http
POST https://mcp.glc-rag.hu/api/auth/register
Content-Type: application/json

{
  "email": "agent@example.com",
  "password": "choose-a-strong-password",
  "account_type": "agent"
}
```

Returns `api_token` + `approved_services` (all listed MCP services). System admin may suspend the agent.

## MCP

```http
POST https://mcp.glc-rag.hu/mcp
Authorization: Bearer mcp_...
```

Source of truth for generated copy: `packages/core/src/mcp_core/agent_docs.py`.
