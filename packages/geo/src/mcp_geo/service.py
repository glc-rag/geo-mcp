from __future__ import annotations

from typing import Any

import asyncpg

from mcp_core.plugin import ServiceDocs, ToolSpec
from mcp_geo.repo import GeoRepository
from mcp_geo.serialize import ENTITY_TYPES, PLACE_KINDS, clamp_limit


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    v = args.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _opt_int(args: dict[str, Any], key: str) -> int | None:
    v = args.get(key)
    if v is None or v == "":
        return None
    return int(v)


def _opt_float(args: dict[str, Any], key: str) -> float | None:
    v = args.get(key)
    if v is None or v == "":
        return None
    return float(v)


def _opt_bool(args: dict[str, Any], key: str) -> bool | None:
    v = args.get(key)
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "yes"}:
        return True
    if s in {"0", "false", "no"}:
        return False
    return None


class GeoService:
    id = "geo"
    name = "Geo"
    description = (
        "GeoNames-backed geospatial MCP: countries, cities, admin regions, marine, "
        "POIs (airports, peaks, lakes, …), distance and nearby search."
    )
    version = "0.2.0"
    listed = True
    status = "available"
    docs = ServiceDocs(
        summary=(
            "Read-only access to rag_dev.geo_entities (~2.5M GeoNames rows). "
            "Resolve names, inspect countries/cities/admin/marine/places, coastal filters, "
            "haversine distance, nearby radius search, airport IATA lookup, and embedding-neighbor search."
        ),
        usage_notes=(
            "Prefer geo_resolve / geo_get for IDs, then specialized tools. "
            "Admin1 short codes (e.g. '03') + country_code bind to admin_code 'IT.03'. "
            "Coastal fields are precomputed on city rows. RAG database is never written."
        ),
        errors=(
            "Missing coords on countries → distance uses capital. "
            "Unknown names return empty results. Invalid API key → platform 401."
        ),
    )

    def __init__(self, repo: GeoRepository) -> None:
        self._repo = repo
        et_enum = {"type": "string", "enum": ENTITY_TYPES}
        pk_enum = {"type": "string", "enum": PLACE_KINDS}
        self.tools = [
            ToolSpec(
                name="geo_status",
                description="Geo DB health, read-only mode, and row counts by entity_type.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=self.geo_status,
                examples=[{}],
            ),
            ToolSpec(
                name="geo_resolve",
                description="Resolve a name, ISO2/ISO3, or IATA code to geo entities (best matches).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "entity_type": et_enum,
                        "country_code": {"type": "string", "description": "ISO2 filter"},
                        "place_kind": pk_enum,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=self.geo_resolve,
                examples=[{"query": "Budapest", "entity_type": "city"}, {"query": "HU"}, {"query": "BUD"}],
            ),
            ToolSpec(
                name="geo_get",
                description="Full entity profile by geoname_id (columns + complete payload).",
                input_schema={
                    "type": "object",
                    "properties": {"geoname_id": {"type": "integer"}},
                    "required": ["geoname_id"],
                    "additionalProperties": False,
                },
                handler=self.geo_get,
                examples=[{"geoname_id": 3054643}],
            ),
            ToolSpec(
                name="geo_text_search",
                description="Fuzzy text search (pg_trgm) over names and search_text.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "entity_type": et_enum,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=self.geo_text_search,
                examples=[{"query": "Balaton", "entity_type": "place"}],
            ),
            ToolSpec(
                name="geo_semantic_search",
                description=(
                    "Embedding-space neighbors around the best name match (uses stored e5 vectors; "
                    "no write to DB). Falls back to text search if anchor has no embedding."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "entity_type": et_enum,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=self.geo_semantic_search,
                examples=[{"query": "Budapest", "entity_type": "city", "limit": 10}],
            ),
            ToolSpec(
                name="geo_country_get",
                description="Country facts by ISO2 or geoname_id (currency, languages, coastal flags, …).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "iso2": {"type": "string"},
                        "geoname_id": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_country_get,
                examples=[{"iso2": "HU"}],
            ),
            ToolSpec(
                name="geo_country_list",
                description="List countries, optionally filtered by continent_code.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "continent_code": {"type": "string", "description": "e.g. EU, AS, AF"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 252, "default": 50},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_country_list,
                examples=[{"continent_code": "EU", "limit": 20}],
            ),
            ToolSpec(
                name="geo_country_neighbours",
                description="Neighbouring countries via payload.neighbours ISO2 list.",
                input_schema={
                    "type": "object",
                    "properties": {"iso2": {"type": "string"}},
                    "required": ["iso2"],
                    "additionalProperties": False,
                },
                handler=self.geo_country_neighbours,
                examples=[{"iso2": "HU"}],
            ),
            ToolSpec(
                name="geo_country_capital",
                description="Resolve country capital via capital_geoname_id.",
                input_schema={
                    "type": "object",
                    "properties": {"iso2": {"type": "string"}},
                    "required": ["iso2"],
                    "additionalProperties": False,
                },
                handler=self.geo_country_capital,
                examples=[{"iso2": "IT"}],
            ),
            ToolSpec(
                name="geo_admin1_list",
                description="List admin1 regions for a country (e.g. Italian regions).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "country_code": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                    },
                    "required": ["country_code"],
                    "additionalProperties": False,
                },
                handler=self.geo_admin1_list,
                examples=[{"country_code": "IT"}],
            ),
            ToolSpec(
                name="geo_admin1_get",
                description="Get admin1 by admin_code (e.g. IT.03) or geoname_id.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "admin_code": {"type": "string"},
                        "geoname_id": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_admin1_get,
                examples=[{"admin_code": "IT.03"}],
            ),
            ToolSpec(
                name="geo_admin2_list",
                description="List admin2 for a country, optionally under an admin1 short code.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "country_code": {"type": "string"},
                        "admin1_code": {"type": "string", "description": "Short code e.g. 03"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                    },
                    "required": ["country_code"],
                    "additionalProperties": False,
                },
                handler=self.geo_admin2_list,
                examples=[{"country_code": "IT", "admin1_code": "03"}],
            ),
            ToolSpec(
                name="geo_admin2_get",
                description="Get admin2 by admin_code or geoname_id.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "admin_code": {"type": "string"},
                        "geoname_id": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_admin2_get,
                examples=[{}],
            ),
            ToolSpec(
                name="geo_cities_search",
                description=(
                    "Search cities with filters: country, admin1 short code, population, "
                    "coastal_category, distance_to_coast_km, is_capital, name query."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "country_code": {"type": "string"},
                        "admin1_code": {"type": "string"},
                        "min_population": {"type": "integer"},
                        "max_population": {"type": "integer"},
                        "coastal_category": {
                            "type": "string",
                            "enum": ["direct_coastal", "coastal", "near_coast", "inland"],
                        },
                        "max_distance_to_coast_km": {"type": "number"},
                        "is_capital": {"type": "boolean"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_cities_search,
                examples=[
                    {
                        "country_code": "IT",
                        "admin1_code": "03",
                        "max_population": 500,
                        "max_distance_to_coast_km": 5,
                    }
                ],
            ),
            ToolSpec(
                name="geo_coastal_cities",
                description="Shortcut for coastal city lists (distance and/or coastal_category).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "country_code": {"type": "string"},
                        "admin1_code": {"type": "string"},
                        "max_distance_to_coast_km": {"type": "number", "default": 5},
                        "coastal_category": {
                            "type": "string",
                            "enum": ["direct_coastal", "coastal", "near_coast", "inland"],
                        },
                        "max_population": {"type": "integer"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                    "required": ["country_code"],
                    "additionalProperties": False,
                },
                handler=self.geo_coastal_cities,
                examples=[{"country_code": "IT", "admin1_code": "03", "max_distance_to_coast_km": 5}],
            ),
            ToolSpec(
                name="geo_region_cities",
                description="Cities in an admin1 region (country_code + admin1 short code).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "country_code": {"type": "string"},
                        "admin1_code": {"type": "string"},
                        "min_population": {"type": "integer"},
                        "max_population": {"type": "integer"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                    "required": ["country_code", "admin1_code"],
                    "additionalProperties": False,
                },
                handler=self.geo_region_cities,
                examples=[{"country_code": "IT", "admin1_code": "03", "min_population": 10000}],
            ),
            ToolSpec(
                name="geo_nearest_marine",
                description="Precomputed nearest sea/bay for a city (nearest_marine_* + coastal fields).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "geoname_id": {"type": "integer"},
                        "city_query": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_nearest_marine,
                examples=[{"city_query": "Rimini"}],
            ),
            ToolSpec(
                name="geo_marine_get",
                description="Marine entity (sea, bay, …) by geoname_id or name query.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "geoname_id": {"type": "integer"},
                        "query": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_marine_get,
                examples=[{"query": "Adriatic"}],
            ),
            ToolSpec(
                name="geo_distance",
                description="Haversine distance (km) between two places (id or name). Countries use capital coords.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "from_id": {"type": "integer"},
                        "from_query": {"type": "string"},
                        "to_id": {"type": "integer"},
                        "to_query": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_distance,
                examples=[{"from_query": "Budapest", "to_query": "Vienna"}],
            ),
            ToolSpec(
                name="geo_nearby",
                description="Entities within radius_km of a center (id, name, or lat/lon). Optional type/kind filters.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "center_id": {"type": "integer"},
                        "center_query": {"type": "string"},
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"},
                        "radius_km": {"type": "number", "default": 30},
                        "entity_type": et_enum,
                        "place_kind": pk_enum,
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_nearby,
                examples=[
                    {
                        "center_query": "Budapest",
                        "radius_km": 40,
                        "entity_type": "place",
                        "place_kind": "airport",
                    }
                ],
            ),
            ToolSpec(
                name="geo_places_search",
                description=f"Search POIs by place_kind ({', '.join(PLACE_KINDS[:8])}, …), country, name, elevation.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "place_kind": pk_enum,
                        "country_code": {"type": "string"},
                        "query": {"type": "string"},
                        "min_elevation": {"type": "number"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_places_search,
                examples=[{"place_kind": "lake", "country_code": "HU", "limit": 10}],
            ),
            ToolSpec(
                name="geo_airport_lookup",
                description="Find airports by IATA/ICAO, name, country, or near a city.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "iata": {"type": "string"},
                        "icao": {"type": "string"},
                        "query": {"type": "string"},
                        "country_code": {"type": "string"},
                        "near_query": {"type": "string"},
                        "radius_km": {"type": "number", "default": 50},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    },
                    "additionalProperties": False,
                },
                handler=self.geo_airport_lookup,
                examples=[{"iata": "BUD"}, {"near_query": "Budapest", "radius_km": 60}],
            ),
            ToolSpec(
                name="geo_rank_places",
                description="Rank places of a kind (e.g. highest peaks) optionally within a country.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "place_kind": pk_enum,
                        "country_code": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    },
                    "required": ["place_kind"],
                    "additionalProperties": False,
                },
                handler=self.geo_rank_places,
                examples=[{"place_kind": "peak", "country_code": "IT", "limit": 5}],
            ),
            ToolSpec(
                name="geo_place_kinds",
                description="List supported place_kind values and entity_type values.",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=self.geo_place_kinds,
                examples=[{}],
            ),
        ]

    async def geo_status(self, _args: dict[str, Any]) -> dict[str, Any]:
        ok, msg = await self._repo.health()
        counts = await self._repo.counts_by_type() if ok else {}
        return {
            "ok": ok,
            "message": msg,
            "read_only": True,
            "source": "rag_dev.public.geo_entities",
            "counts": counts,
            "place_kinds": PLACE_KINDS,
        }

    async def geo_resolve(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.resolve(
            query=_opt_str(args, "query") or "",
            entity_type=_opt_str(args, "entity_type"),
            country_code=_opt_str(args, "country_code"),
            place_kind=_opt_str(args, "place_kind"),
            limit=clamp_limit(args.get("limit"), 10),
        )
        return {"query": args.get("query"), "count": len(results), "results": results}

    async def geo_get(self, args: dict[str, Any]) -> dict[str, Any]:
        ent = await self._repo.get_by_id(int(args["geoname_id"]))
        if not ent:
            return {"error": "not found", "geoname_id": args["geoname_id"]}
        return {"entity": ent}

    async def geo_text_search(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.text_search(
            query=_opt_str(args, "query") or "",
            entity_type=_opt_str(args, "entity_type"),
            limit=clamp_limit(args.get("limit")),
        )
        return {"count": len(results), "results": results}

    async def geo_semantic_search(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._repo.semantic_via_anchor(
            query=_opt_str(args, "query") or "",
            entity_type=_opt_str(args, "entity_type"),
            limit=clamp_limit(args.get("limit")),
        )

    async def geo_country_get(self, args: dict[str, Any]) -> dict[str, Any]:
        ent = await self._repo.country_get(
            iso2=_opt_str(args, "iso2"),
            geoname_id=_opt_int(args, "geoname_id"),
        )
        if not ent:
            return {"error": "country not found"}
        return {"country": ent}

    async def geo_country_list(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.country_list(
            continent_code=_opt_str(args, "continent_code"),
            limit=clamp_limit(args.get("limit"), 50, 252),
        )
        return {"count": len(results), "results": results}

    async def geo_country_neighbours(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._repo.country_neighbours(_opt_str(args, "iso2") or "")

    async def geo_country_capital(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._repo.country_capital(_opt_str(args, "iso2") or "")

    async def geo_admin1_list(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.admin_list(
            level=1,
            country_code=_opt_str(args, "country_code") or "",
            limit=clamp_limit(args.get("limit"), 50, 100),
        )
        return {"count": len(results), "results": results}

    async def geo_admin1_get(self, args: dict[str, Any]) -> dict[str, Any]:
        ent = await self._repo.admin_get(
            level=1,
            admin_code=_opt_str(args, "admin_code"),
            geoname_id=_opt_int(args, "geoname_id"),
        )
        if not ent:
            return {"error": "admin1 not found"}
        return {"admin1": ent}

    async def geo_admin2_list(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.admin_list(
            level=2,
            country_code=_opt_str(args, "country_code") or "",
            admin1_code=_opt_str(args, "admin1_code"),
            limit=clamp_limit(args.get("limit"), 50, 100),
        )
        return {"count": len(results), "results": results}

    async def geo_admin2_get(self, args: dict[str, Any]) -> dict[str, Any]:
        ent = await self._repo.admin_get(
            level=2,
            admin_code=_opt_str(args, "admin_code"),
            geoname_id=_opt_int(args, "geoname_id"),
        )
        if not ent:
            return {"error": "admin2 not found"}
        return {"admin2": ent}

    async def geo_cities_search(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.cities_search(
            country_code=_opt_str(args, "country_code"),
            admin1_code=_opt_str(args, "admin1_code"),
            min_population=_opt_int(args, "min_population"),
            max_population=_opt_int(args, "max_population"),
            coastal_category=_opt_str(args, "coastal_category"),
            max_distance_to_coast_km=_opt_float(args, "max_distance_to_coast_km"),
            is_capital=_opt_bool(args, "is_capital"),
            query=_opt_str(args, "query"),
            limit=clamp_limit(args.get("limit")),
        )
        return {"count": len(results), "results": results}

    async def geo_coastal_cities(self, args: dict[str, Any]) -> dict[str, Any]:
        max_d = _opt_float(args, "max_distance_to_coast_km")
        cat = _opt_str(args, "coastal_category")
        if max_d is None and cat is None:
            max_d = 5.0
        results = await self._repo.cities_search(
            country_code=_opt_str(args, "country_code"),
            admin1_code=_opt_str(args, "admin1_code"),
            max_population=_opt_int(args, "max_population"),
            coastal_category=cat,
            max_distance_to_coast_km=max_d,
            limit=clamp_limit(args.get("limit")),
        )
        return {"count": len(results), "results": results}

    async def geo_region_cities(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.cities_search(
            country_code=_opt_str(args, "country_code"),
            admin1_code=_opt_str(args, "admin1_code"),
            min_population=_opt_int(args, "min_population"),
            max_population=_opt_int(args, "max_population"),
            limit=clamp_limit(args.get("limit")),
        )
        return {
            "country_code": args.get("country_code"),
            "admin1_code": args.get("admin1_code"),
            "count": len(results),
            "results": results,
        }

    async def geo_nearest_marine(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._repo.nearest_marine(
            geoname_id=_opt_int(args, "geoname_id"),
            city_query=_opt_str(args, "city_query"),
        )

    async def geo_marine_get(self, args: dict[str, Any]) -> dict[str, Any]:
        ent = await self._repo.marine_get(
            geoname_id=_opt_int(args, "geoname_id"),
            query=_opt_str(args, "query"),
        )
        if not ent:
            return {"error": "marine not found"}
        return {"marine": ent}

    async def geo_distance(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._repo.distance(
            from_id=_opt_int(args, "from_id"),
            from_query=_opt_str(args, "from_query"),
            to_id=_opt_int(args, "to_id"),
            to_query=_opt_str(args, "to_query"),
        )

    async def geo_nearby(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._repo.nearby(
            center_id=_opt_int(args, "center_id"),
            center_query=_opt_str(args, "center_query"),
            latitude=_opt_float(args, "latitude"),
            longitude=_opt_float(args, "longitude"),
            radius_km=float(args.get("radius_km") or 30),
            entity_type=_opt_str(args, "entity_type"),
            place_kind=_opt_str(args, "place_kind"),
            limit=clamp_limit(args.get("limit")),
        )

    async def geo_places_search(self, args: dict[str, Any]) -> dict[str, Any]:
        results = await self._repo.places_search(
            place_kind=_opt_str(args, "place_kind"),
            country_code=_opt_str(args, "country_code"),
            query=_opt_str(args, "query"),
            min_elevation=_opt_float(args, "min_elevation"),
            limit=clamp_limit(args.get("limit")),
        )
        return {"count": len(results), "results": results}

    async def geo_airport_lookup(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._repo.airport_lookup(
            iata=_opt_str(args, "iata"),
            icao=_opt_str(args, "icao"),
            query=_opt_str(args, "query"),
            country_code=_opt_str(args, "country_code"),
            near_query=_opt_str(args, "near_query"),
            radius_km=float(args.get("radius_km") or 50),
            limit=clamp_limit(args.get("limit"), 10),
        )

    async def geo_rank_places(self, args: dict[str, Any]) -> dict[str, Any]:
        kind = _opt_str(args, "place_kind")
        if not kind:
            return {"error": "place_kind required"}
        results = await self._repo.rank_places(
            place_kind=kind,
            country_code=_opt_str(args, "country_code"),
            limit=clamp_limit(args.get("limit"), 10),
        )
        return {"place_kind": kind, "count": len(results), "results": results}

    async def geo_place_kinds(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"entity_types": ENTITY_TYPES, "place_kinds": PLACE_KINDS}

    async def health(self) -> tuple[bool, str]:
        return await self._repo.health()


def create_module(pool: asyncpg.Pool) -> GeoService:
    return GeoService(GeoRepository(pool))
