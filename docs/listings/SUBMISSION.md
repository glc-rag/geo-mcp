# Listing pack — GLC Geo MCP (free remote)

Use these snippets when submitting. Endpoint: `https://mcp.glc-rag.hu/mcp`

## Short description (≤200 chars)

Free GeoNames geospatial MCP (countries, cities, POIs, distance, nearby). Remote streamable HTTP. Agents self-register for an API token.

## Long description

GLC Geo MCP is a free, public Model Context Protocol server backed by ~2.5M GeoNames entities (countries, cities, admin1/2, marine, places). Tools cover resolve/search, coastal city filters, airports (IATA), haversine distance, nearby radius search, and embedding-neighbor search.

- **Transport:** streamable HTTP  
- **URL:** https://mcp.glc-rag.hu/mcp  
- **Auth:** Bearer API token (`Authorization: Bearer mcp_…`)  
- **Agent signup:** `POST https://mcp.glc-rag.hu/api/auth/register` with `{"account_type":"agent",…}` → auto-approved services + `api_token`  
- **Docs:** https://mcp.glc-rag.hu/guide/agent · https://mcp.glc-rag.hu/llms.txt · https://mcp.glc-rag.hu/guide/geo  
- **Pricing:** free (system admin may suspend abusive accounts)

## Cursor `mcp.json`

```json
{
  "mcpServers": {
    "glc-geo": {
      "url": "https://mcp.glc-rag.hu/mcp",
      "headers": {
        "Authorization": "Bearer mcp_YOUR_TOKEN"
      }
    }
  }
}
```

## Agent register curl

```bash
curl -sS -X POST 'https://mcp.glc-rag.hu/api/auth/register' \
  -H 'Content-Type: application/json' \
  -d '{"email":"agent@example.com","password":"choose-a-strong-password","account_type":"agent"}'
```

## Official MCP Registry

File ready: `/home/pergel/mcp/server.json`

Blocked until you authenticate:

1. Publish a **public GitHub repo** of this project (set `repository.url` in `server.json`).
2. Install publisher: see https://modelcontextprotocol.io/registry/quickstart  
3. Namespace auth — pick one:
   - `io.github.<you>/geo-mcp` → `mcp-publisher login github`
   - `hu.glc-rag/geo-mcp` → domain ownership for `glc-rag.hu` (`mcp-publisher login dns` or `http`)
4. `mcp-publisher publish`

Remote docs: https://modelcontextprotocol.io/registry/remote-servers

## GitHub awesome lists (PR)

Suggested bullet:

```markdown
- [GLC Geo MCP](https://mcp.glc-rag.hu) - Free remote GeoNames geospatial MCP (countries, cities, POIs, distance, nearby). Streamable HTTP + agent self-register for API token. Docs: https://mcp.glc-rag.hu/llms.txt
```

Targets:

- https://github.com/punkpeye/awesome-mcp-servers (category: Location / Maps / Travel)
- https://github.com/TensorBlock/awesome-mcp-servers (issue form or docs PR)

Requires: `gh auth login` (or PAT) on this machine, then fork + PR.

## Web directories (human form / account)

| Site | Submit |
|------|--------|
| mcpservers.org | https://mcpservers.org/submit |
| FindMCP | https://findmcp.dev/ |
| mcp.so | https://mcp.so/ |
| Smithery | https://smithery.ai/ |
| Glama | https://glama.ai/ |

Paste short + long description + MCP URL + auth notes from above.
