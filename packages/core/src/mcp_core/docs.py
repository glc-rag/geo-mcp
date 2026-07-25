from __future__ import annotations

import json
from typing import Iterable

from mcp_core.agent_docs import (
    AGENT_GUIDE_PATH,
    AGENT_REGISTER_PATH,
    agent_registration_curl,
    agent_registration_markdown,
)
from mcp_core.plugin import McpServiceModule
from mcp_core.registry import ServiceRegistry


class DocGenerator:
    def __init__(self, registry: ServiceRegistry, *, public_base_url: str) -> None:
        self.registry = registry
        self.base = public_base_url.rstrip("/")

    def agent_registration_markdown(self) -> str:
        return agent_registration_markdown(base=self.base)

    def service_markdown(self, module: McpServiceModule) -> str:
        lines: list[str] = [
            f"# {module.name}",
            "",
            module.docs.summary or module.description,
            "",
            f"**Service id:** `{module.id}`  ",
            f"**Version:** `{module.version}`  ",
            f"**Status:** `{module.status}`",
            "",
            "## Authentication",
            "",
            f"MCP endpoint: `{self.base}/mcp` (streamable HTTP)",
            "",
            "**Agents (recommended):** self-register with `account_type=agent` to get an",
            f"auto-approved token — see [{self.base}{AGENT_GUIDE_PATH}]({self.base}{AGENT_GUIDE_PATH}).",
            "",
            "Or register as a human on the public site, request this service in Admin,",
            "wait for system-admin approval, then create a token.",
            "",
            "```http",
            "Authorization: Bearer mcp_...",
            "```",
            "",
            "Cursor `mcp.json` example:",
            "",
            "```json",
            json.dumps(
                {
                    "mcpServers": {
                        module.id: {
                            "url": f"{self.base}/mcp",
                            "headers": {"Authorization": "Bearer mcp_YOUR_TOKEN"},
                        }
                    }
                },
                indent=2,
            ),
            "```",
            "",
            "## Tools",
            "",
        ]
        for tool in module.tools:
            lines.extend(
                [
                    f"### `{tool.name}`",
                    "",
                    tool.description,
                    "",
                    "**Input schema:**",
                    "",
                    "```json",
                    json.dumps(tool.input_schema, indent=2),
                    "```",
                    "",
                ]
            )
            if tool.examples:
                lines.append("**Examples:**")
                lines.append("")
                for ex in tool.examples:
                    lines.append("```json")
                    lines.append(json.dumps(ex, indent=2))
                    lines.append("```")
                    lines.append("")
        if module.docs.usage_notes:
            lines.extend(["## Usage notes", "", module.docs.usage_notes, ""])
        if module.docs.errors:
            lines.extend(["## Errors / limits", "", module.docs.errors, ""])
        lines.extend(
            [
                "## Agent discovery",
                "",
                f"- Agent registration: `{self.base}{AGENT_GUIDE_PATH}`",
                f"- Markdown: `{self.base}/guide/{module.id}.md`",
                f"- Index: `{self.base}/llms.txt`",
                f"- MCP resource: `docs://{module.id}`",
                "",
            ]
        )
        return "\n".join(lines)

    def catalog_markdown(self, modules: Iterable[McpServiceModule] | None = None) -> str:
        mods = list(modules) if modules is not None else self.registry.listed()
        lines = [
            "# MCP service catalog",
            "",
            f"Platform: {self.base}",
            "",
            "## Services",
            "",
        ]
        for m in mods:
            lines.append(f"- [{m.name}]({self.base}/guide/{m.id}.md) — {m.description}")
        lines.extend(
            [
                "",
                "## Agent registration",
                "",
                f"Agents: POST `{self.base}{AGENT_REGISTER_PATH}` with",
                '`{"account_type":"agent"}` → auto-approved services + `api_token`.',
                "",
                f"Full guide: [{self.base}{AGENT_GUIDE_PATH}]({self.base}{AGENT_GUIDE_PATH})",
                f"Markdown: [{self.base}{AGENT_GUIDE_PATH}.md]({self.base}{AGENT_GUIDE_PATH}.md)",
                "",
                "## Connect",
                "",
                f"Streamable HTTP: `{self.base}/mcp`",
                "",
                "Header: `Authorization: Bearer <api_token>`",
                "",
            ]
        )
        return "\n".join(lines)

    def llms_txt(self) -> str:
        lines = [
            "# MCP Platform",
            "",
            f"> Platform docs for agents. Base: {self.base}",
            "",
            "## Agent registration",
            "",
            f"- [Agent registration guide]({self.base}{AGENT_GUIDE_PATH}): self-register, auto-approve, get `api_token`",
            f"- [Agent registration (markdown)]({self.base}{AGENT_GUIDE_PATH}.md)",
            f"- Register API: `POST {self.base}{AGENT_REGISTER_PATH}` with `account_type=agent`",
            f"- Web UI: [{self.base}/register]({self.base}/register) (check “Register as agent”)",
            "",
            "## Docs",
            "",
            f"- [Catalog]({self.base}/guide/catalog.md): All listed MCP services",
        ]
        for m in self.registry.listed():
            lines.append(f"- [{m.name}]({self.base}/guide/{m.id}.md): {m.description}")
        lines.extend(
            [
                "",
                "## Discovery",
                "",
                f"- [Well-known MCP]({self.base}/.well-known/mcp)",
                f"- [Server card]({self.base}/.well-known/mcp/server-card.json)",
                f"- [Full text]({self.base}/llms-full.txt)",
                "",
                "## Quick register (curl)",
                "",
                "```bash",
                agent_registration_curl(base=self.base),
                "```",
                "",
            ]
        )
        return "\n".join(lines)

    def llms_full(self) -> str:
        parts = [
            self.agent_registration_markdown(),
            "",
            "---",
            "",
            self.catalog_markdown(),
            "",
        ]
        for m in self.registry.listed():
            parts.append(self.service_markdown(m))
            parts.append("\n---\n")
        return "\n".join(parts)

    def well_known_mcp(self) -> dict:
        return {
            "mcp_version": "1.0",
            "server_name": "GLC MCP Platform",
            "server_version": "0.1.0",
            "endpoints": {"streamable_http": f"{self.base}/mcp"},
            "capabilities": {"tools": True, "resources": True, "prompts": False},
            "authentication": {
                "required": True,
                "methods": ["api_key"],
                "agent_registration": {
                    "url": f"{self.base}{AGENT_REGISTER_PATH}",
                    "method": "POST",
                    "body": {
                        "email": "string",
                        "password": "string",
                        "account_type": "agent",
                    },
                    "returns": ["api_token", "approved_services"],
                    "docs": f"{self.base}{AGENT_GUIDE_PATH}",
                },
            },
            "documentation": f"{self.base}/guide",
            "agent_registration_docs": f"{self.base}{AGENT_GUIDE_PATH}",
            "llms_txt": f"{self.base}/llms.txt",
        }

    def server_card(self) -> dict:
        return {
            "version": "1.0",
            "protocolVersion": "2025-03-26",
            "serverInfo": {
                "name": "GLC MCP Platform",
                "version": "0.1.0",
                "description": "Public modular MCP: hello, geo, and more. Agents can self-register.",
                "homepage": self.base,
            },
            "transport": {"type": "streamable-http", "url": f"{self.base}/mcp"},
            "capabilities": {"tools": True, "resources": True},
            "authentication": {
                "methods": ["api_key"],
                "agentRegistration": f"{self.base}{AGENT_GUIDE_PATH}",
            },
            "documentation": f"{self.base}/guide",
            "llmsTxt": f"{self.base}/llms.txt",
        }

    def html_docs_index(self) -> str:
        items = "".join(
            f'<li><a href="/guide/{m.id}"><strong>{m.name}</strong></a> — {m.description} '
            f'(<a href="/guide/{m.id}.md">markdown</a>)</li>'
            for m in self.registry.listed()
        )
        curl = agent_registration_curl(base=self.base).replace("<", "&lt;")
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP Docs</title>
<link rel="stylesheet" href="/assets/style.css?v=docs2"></head>
<body class="docs">
<div class="shell">
<header class="top"><a href="/">← Home</a> · <a href="/llms.txt">llms.txt</a> · <a href="/llms-full.txt">llms-full.txt</a></header>
<main>
<h1 class="brand" style="font-size:2.4rem">MCP documentation</h1>
<p class="tagline">Auto-generated from service modules. Agents should read the <code>/llms.txt</code> index.</p>
<ul>{items or "<li>No listed services.</li>"}</ul>
<div class="panel">
<h2>Agent registration</h2>
<p>Agents self-register with <code>account_type=agent</code>, get auto-approved services and an <code>api_token</code>.</p>
<p><a class="btn primary" href="{AGENT_GUIDE_PATH}">Agent guide</a>
   <a class="btn" href="{AGENT_GUIDE_PATH}.md">Markdown</a>
   <a class="btn" href="/register">Register (web)</a></p>
<pre>{curl}</pre>
</div>
<div class="panel">
<h2>Connect</h2>
<pre>URL: {self.base}/mcp
Header: Authorization: Bearer &lt;api-token&gt;</pre>
</div>
</main>
</div></body></html>"""

    def html_agent_registration(self) -> str:
        md = self.agent_registration_markdown()
        escaped = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent registration</title>
<link rel="stylesheet" href="/assets/style.css?v=docs2"></head>
<body class="docs">
<div class="shell">
<header class="top"><a href="/guide">← Docs</a> · <a href="{AGENT_GUIDE_PATH}.md">Markdown</a> · <a href="/register">Register</a> · <a href="/">Home</a></header>
<main><pre class="md">{escaped}</pre></main>
</div></body></html>"""

    def html_service(self, module: McpServiceModule) -> str:
        md = self.service_markdown(module)
        escaped = (
            md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{module.name} docs</title>
<link rel="stylesheet" href="/assets/style.css?v=docs2"></head>
<body class="docs">
<div class="shell">
<header class="top"><a href="/guide">← Docs</a> · <a href="/guide/{module.id}.md">Markdown</a> · <a href="{AGENT_GUIDE_PATH}">Agent register</a> · <a href="/">Home</a></header>
<main><pre class="md">{escaped}</pre></main>
</div></body></html>"""
