from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp_core.plugin import ServiceDocs, ToolSpec


class HelloService:
    id = "hello"
    name = "Hello"
    description = "Smoke-test MCP service (hello world)."
    version = "0.1.0"
    listed = True
    status = "available"
    docs = ServiceDocs(
        summary="Minimal hello-world service to verify MCP connectivity and API keys.",
        usage_notes="Call `hello_ping` after system-admin approves the hello service for your org.",
        errors="Unauthorized tools are hidden from tools/list when the service is not approved.",
    )

    def __init__(self) -> None:
        self.tools = [
            ToolSpec(
                name="hello_ping",
                description="Return a greeting message and server timestamp.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Optional name to greet (default: world)",
                        }
                    },
                    "additionalProperties": False,
                },
                handler=self.ping,
                examples=[{"name": "Attila"}, {}],
            )
        ]

    async def ping(self, args: dict[str, Any]) -> dict[str, Any]:
        name = (args.get("name") or "world").strip() or "world"
        return {
            "message": f"hello, {name}",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    async def health(self) -> tuple[bool, str]:
        return True, "ok"


def create_module() -> HelloService:
    return HelloService()
