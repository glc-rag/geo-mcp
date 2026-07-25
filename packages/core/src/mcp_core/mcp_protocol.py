"""Minimal MCP JSON-RPC over streamable HTTP (tools + resources)."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from mcp_core.docs import DocGenerator
from mcp_core.identity import McpAuthContext
from mcp_core.registry import ServiceRegistry


PROTOCOL_VERSION = "2024-11-05"


class McpProtocol:
    def __init__(self, registry: ServiceRegistry, docs: DocGenerator) -> None:
        self.registry = registry
        self.docs = docs

    def _tools_for(self, auth: McpAuthContext) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for sid in auth.approved_services:
            mod = self.registry.get(sid)
            if not mod:
                continue
            for t in mod.tools:
                tools.append(
                    {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.input_schema,
                    }
                )
        return tools

    def _find_tool(self, auth: McpAuthContext, name: str):
        for sid in auth.approved_services:
            mod = self.registry.get(sid)
            if not mod:
                continue
            for t in mod.tools:
                if t.name == name:
                    return sid, t
        return None, None

    def _resources_for(self, auth: McpAuthContext) -> list[dict[str, Any]]:
        resources = [
            {
                "uri": "docs://agent",
                "name": "Agent registration",
                "description": "How agents self-register and obtain an api_token",
                "mimeType": "text/markdown",
            },
            {
                "uri": "docs://catalog",
                "name": "Service catalog",
                "description": "All approved services documentation index",
                "mimeType": "text/markdown",
            },
        ]
        for sid in auth.approved_services:
            mod = self.registry.get(sid)
            if not mod:
                continue
            resources.append(
                {
                    "uri": f"docs://{sid}",
                    "name": f"{mod.name} docs",
                    "description": mod.description,
                    "mimeType": "text/markdown",
                }
            )
        return resources

    async def handle(self, message: dict[str, Any], auth: McpAuthContext) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        # notifications have no id / no response
        if msg_id is None and method and method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {"name": "glc-mcp-platform", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self._tools_for(auth)}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                _sid, tool = self._find_tool(auth, name)
                if tool is None:
                    return self._error(msg_id, -32601, f"Unknown or unauthorized tool: {name}")
                out = await tool.handler(arguments if isinstance(arguments, dict) else {})
                result = {
                    "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                    "structuredContent": out,
                    "isError": False,
                }
            elif method == "resources/list":
                result = {"resources": self._resources_for(auth)}
            elif method == "resources/read":
                uri = params.get("uri", "")
                text = await self._read_resource(auth, uri)
                if text is None:
                    return self._error(msg_id, -32602, f"Unknown resource: {uri}")
                result = {
                    "contents": [
                        {"uri": uri, "mimeType": "text/markdown", "text": text}
                    ]
                }
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return self._error(msg_id, -32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as exc:  # noqa: BLE001
            return self._error(msg_id, -32000, str(exc))

    async def _read_resource(self, auth: McpAuthContext, uri: str) -> str | None:
        if uri == "docs://agent":
            return self.docs.agent_registration_markdown()
        if uri == "docs://catalog":
            mods = [
                m
                for sid in auth.approved_services
                if (m := self.registry.get(sid)) is not None
            ]
            return self.docs.catalog_markdown(mods)
        if uri.startswith("docs://"):
            sid = uri.removeprefix("docs://")
            if sid not in auth.approved_services:
                return None
            mod = self.registry.get(sid)
            if not mod:
                return None
            return self.docs.service_markdown(mod)
        return None

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def new_session_id() -> str:
        return str(uuid4())
