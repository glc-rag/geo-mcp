"""MCP service plugin contract — every service package implements this."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    examples: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceDocs:
    """Structured docs used by DocGenerator (SSOT with tools)."""

    summary: str
    usage_notes: str = ""
    errors: str = ""


class McpServiceModule(Protocol):
    id: str
    name: str
    description: str
    version: str
    listed: bool
    status: str  # available | beta | maintenance
    docs: ServiceDocs
    tools: list[ToolSpec]

    async def health(self) -> tuple[bool, str]:
        ...
