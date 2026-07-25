from __future__ import annotations

from mcp_core.plugin import McpServiceModule


class ServiceRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, McpServiceModule] = {}

    def register(self, module: McpServiceModule) -> None:
        if module.id in self._modules:
            raise ValueError(f"duplicate service module: {module.id}")
        self._modules[module.id] = module

    def get(self, service_id: str) -> McpServiceModule | None:
        return self._modules.get(service_id)

    def all(self) -> list[McpServiceModule]:
        return list(self._modules.values())

    def listed(self) -> list[McpServiceModule]:
        return [m for m in self._modules.values() if m.listed]

    def catalog_public(self) -> list[dict]:
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "version": m.version,
                "listed": m.listed,
                "status": m.status,
            }
            for m in self.listed()
        ]
