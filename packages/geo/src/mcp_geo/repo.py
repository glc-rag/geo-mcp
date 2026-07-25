from __future__ import annotations

from typing import Any

import asyncpg

from mcp_geo.haversine import bbox, haversine_km
from mcp_geo.serialize import clamp_limit, row_to_entity


class GeoRepository:
    """Read-only adapter over rag_dev.public.geo_entities."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def health(self) -> tuple[bool, str]:
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
                n = await conn.fetchval("SELECT COUNT(*) FROM geo_entities")
                ext = await conn.fetchval(
                    "SELECT extname FROM pg_extension WHERE extname = 'vector'"
                )
            return True, f"ok read-only geo_entities rows={n} vector={ext or 'missing'}"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    async def counts_by_type(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT entity_type, COUNT(*)::bigint AS n
                FROM geo_entities GROUP BY 1 ORDER BY 2 DESC
                """
            )
        return {r["entity_type"]: int(r["n"]) for r in rows}

    async def get_by_id(self, geoname_id: int) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                       iso2, iso3, country_code, place_kind, feature_code, admin_code,
                       population, latitude, longitude, search_text, payload
                FROM geo_entities WHERE geoname_id = $1
                """,
                geoname_id,
            )
        return row_to_entity(row, full_payload=True) if row else None

    async def resolve(
        self,
        *,
        query: str,
        entity_type: str | None = None,
        country_code: str | None = None,
        place_kind: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        limit = clamp_limit(limit, 10, 50)
        q = query.strip()
        if not q:
            return []
        # Exact IATA / ISO shortcuts
        async with self._pool.acquire() as conn:
            if len(q) in (2, 3) and q.isalpha():
                code = q.upper()
                if len(code) == 2:
                    row = await conn.fetchrow(
                        """
                        SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                               iso2, iso3, country_code, place_kind, feature_code, admin_code,
                               population, latitude, longitude, payload
                        FROM geo_entities
                        WHERE entity_type = 'country' AND (iso2 = $1 OR country_code = $1)
                        LIMIT 1
                        """,
                        code,
                    )
                    if row:
                        return [row_to_entity(row)]
                if len(code) == 3:
                    rows = await conn.fetch(
                        """
                        SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                               iso2, iso3, country_code, place_kind, feature_code, admin_code,
                               population, latitude, longitude, payload
                        FROM geo_entities
                        WHERE entity_type = 'place' AND place_kind = 'airport'
                          AND upper(payload->>'iata') = $1
                        LIMIT $2
                        """,
                        code,
                        limit,
                    )
                    if rows:
                        return [row_to_entity(r) for r in rows]
                    row = await conn.fetchrow(
                        """
                        SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                               iso2, iso3, country_code, place_kind, feature_code, admin_code,
                               population, latitude, longitude, payload
                        FROM geo_entities
                        WHERE entity_type = 'country' AND iso3 = $1
                        LIMIT 1
                        """,
                        code,
                    )
                    if row:
                        return [row_to_entity(row)]

            clauses = [
                """(
                    name ILIKE $1 OR name_hu ILIKE $1 OR name_en ILIKE $1
                    OR ascii_name ILIKE $1 OR search_text ILIKE $1
                )"""
            ]
            args: list[Any] = [f"%{q}%"]
            idx = 2
            if entity_type:
                clauses.append(f"entity_type = ${idx}")
                args.append(entity_type)
                idx += 1
            if country_code:
                clauses.append(f"country_code = ${idx}")
                args.append(country_code.upper())
                idx += 1
            if place_kind:
                clauses.append(f"place_kind = ${idx}")
                args.append(place_kind)
                idx += 1
            args.append(limit)
            sql = f"""
                SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                       iso2, iso3, country_code, place_kind, feature_code, admin_code,
                       population, latitude, longitude, payload,
                       GREATEST(
                         similarity(COALESCE(name, ''), $1_plain),
                         similarity(COALESCE(name_en, ''), $1_plain),
                         similarity(COALESCE(name_hu, ''), $1_plain),
                         similarity(COALESCE(ascii_name, ''), $1_plain)
                       ) AS score
                FROM geo_entities
                WHERE {' AND '.join(clauses)}
                ORDER BY
                  CASE WHEN lower(name) = lower($1_plain) THEN 0
                       WHEN lower(COALESCE(name_en,'')) = lower($1_plain) THEN 1
                       ELSE 2 END,
                  population DESC NULLS LAST,
                  score DESC NULLS LAST
                LIMIT ${idx}
            """
            # asyncpg can't do named $1_plain — rewrite cleanly
            sql = f"""
                SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                       iso2, iso3, country_code, place_kind, feature_code, admin_code,
                       population, latitude, longitude, payload
                FROM geo_entities
                WHERE {' AND '.join(clauses)}
                ORDER BY
                  CASE WHEN lower(COALESCE(name,'')) = lower(${idx + 1}) THEN 0
                       WHEN lower(COALESCE(name_en,'')) = lower(${idx + 1}) THEN 1
                       WHEN lower(COALESCE(ascii_name,'')) = lower(${idx + 1}) THEN 2
                       ELSE 3 END,
                  CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN 0 ELSE 1 END,
                  CASE entity_type
                    WHEN 'city' THEN 0
                    WHEN 'place' THEN 1
                    WHEN 'country' THEN 2
                    WHEN 'marine' THEN 3
                    WHEN 'admin1' THEN 4
                    WHEN 'admin2' THEN 5
                    ELSE 6 END,
                  population DESC NULLS LAST
                LIMIT ${idx}
            """
            args.append(q)
            rows = await conn.fetch(sql, *args)
        return [row_to_entity(r) for r in rows]

    async def country_get(self, *, iso2: str | None = None, geoname_id: int | None = None) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            if geoname_id is not None:
                row = await conn.fetchrow(
                    """
                    SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                           iso2, iso3, country_code, place_kind, feature_code, admin_code,
                           population, latitude, longitude, search_text, payload
                    FROM geo_entities WHERE geoname_id = $1 AND entity_type = 'country'
                    """,
                    geoname_id,
                )
            elif iso2:
                row = await conn.fetchrow(
                    """
                    SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                           iso2, iso3, country_code, place_kind, feature_code, admin_code,
                           population, latitude, longitude, search_text, payload
                    FROM geo_entities
                    WHERE entity_type = 'country' AND upper(iso2) = upper($1)
                    LIMIT 1
                    """,
                    iso2,
                )
            else:
                return None
        return row_to_entity(row, full_payload=True) if row else None

    async def country_list(
        self, *, continent_code: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit = clamp_limit(limit, 50, 252)
        async with self._pool.acquire() as conn:
            if continent_code:
                rows = await conn.fetch(
                    """
                    SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                           iso2, iso3, country_code, place_kind, feature_code, admin_code,
                           population, latitude, longitude, payload
                    FROM geo_entities
                    WHERE entity_type = 'country'
                      AND upper(payload->>'continent_code') = upper($1)
                    ORDER BY name
                    LIMIT $2
                    """,
                    continent_code,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                           iso2, iso3, country_code, place_kind, feature_code, admin_code,
                           population, latitude, longitude, payload
                    FROM geo_entities
                    WHERE entity_type = 'country'
                    ORDER BY name
                    LIMIT $1
                    """,
                    limit,
                )
        return [row_to_entity(r) for r in rows]

    async def country_neighbours(self, iso2: str) -> dict[str, Any]:
        country = await self.country_get(iso2=iso2)
        if not country:
            return {"iso2": iso2, "neighbours": [], "error": "country not found"}
        raw_n = (country.get("payload") or {}).get("neighbours")
        codes: list[str] = []
        if isinstance(raw_n, list):
            codes = [str(c).upper().strip() for c in raw_n if str(c).strip()]
        elif isinstance(raw_n, str) and raw_n.strip():
            codes = [c.strip().upper() for c in raw_n.split(",") if c.strip()]
        # Fallback: neighbours_resolved[].country_code
        if not codes:
            resolved = (country.get("payload") or {}).get("neighbours_resolved")
            if isinstance(resolved, str):
                try:
                    import json as _json

                    resolved = _json.loads(resolved)
                except Exception:  # noqa: BLE001
                    resolved = None
            if isinstance(resolved, list):
                for item in resolved:
                    if isinstance(item, dict) and item.get("country_code"):
                        codes.append(str(item["country_code"]).upper())
        codes = list(dict.fromkeys(codes))
        if not codes:
            return {"country": country, "neighbours": []}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                       iso2, iso3, country_code, place_kind, feature_code, admin_code,
                       population, latitude, longitude, payload
                FROM geo_entities
                WHERE entity_type = 'country' AND iso2 = ANY($1::text[])
                ORDER BY name
                """,
                codes,
            )
        return {"country": {k: country[k] for k in ("geoname_id", "name", "iso2", "iso3") if k in country}, "neighbours": [row_to_entity(r) for r in rows]}

    async def country_capital(self, iso2: str) -> dict[str, Any]:
        country = await self.country_get(iso2=iso2)
        if not country:
            return {"error": "country not found", "iso2": iso2}
        payload = country.get("payload") or {}
        cap_id = payload.get("capital_geoname_id")
        capital = None
        if cap_id is not None:
            try:
                capital = await self.get_by_id(int(cap_id))
            except (TypeError, ValueError):
                capital = None
        return {
            "country": {k: country[k] for k in ("geoname_id", "name", "iso2") if k in country},
            "capital_name": payload.get("capital_name"),
            "capital": capital,
        }

    async def admin_list(
        self,
        *,
        level: int,
        country_code: str,
        admin1_code: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        limit = clamp_limit(limit, 50, 100)
        et = "admin1" if level == 1 else "admin2"
        async with self._pool.acquire() as conn:
            if level == 1:
                rows = await conn.fetch(
                    """
                    SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                           iso2, iso3, country_code, place_kind, feature_code, admin_code,
                           population, latitude, longitude, payload
                    FROM geo_entities
                    WHERE entity_type = 'admin1' AND country_code = $1
                    ORDER BY name
                    LIMIT $2
                    """,
                    country_code.upper(),
                    limit,
                )
            else:
                if admin1_code:
                    code_prefix = f"{country_code.upper()}.{admin1_code}"
                    rows = await conn.fetch(
                        """
                        SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                               iso2, iso3, country_code, place_kind, feature_code, admin_code,
                               population, latitude, longitude, payload
                        FROM geo_entities
                        WHERE entity_type = 'admin2' AND country_code = $1
                          AND (
                            admin_code LIKE $2 || '.%'
                            OR payload->>'admin1_code' = $3
                          )
                        ORDER BY name
                        LIMIT $4
                        """,
                        country_code.upper(),
                        code_prefix,
                        admin1_code,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                               iso2, iso3, country_code, place_kind, feature_code, admin_code,
                               population, latitude, longitude, payload
                        FROM geo_entities
                        WHERE entity_type = 'admin2' AND country_code = $1
                        ORDER BY name
                        LIMIT $2
                        """,
                        country_code.upper(),
                        limit,
                    )
        return [row_to_entity(r) for r in rows]

    async def admin_get(self, *, level: int, admin_code: str | None = None, geoname_id: int | None = None) -> dict[str, Any] | None:
        et = "admin1" if level == 1 else "admin2"
        async with self._pool.acquire() as conn:
            if geoname_id is not None:
                row = await conn.fetchrow(
                    """
                    SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                           iso2, iso3, country_code, place_kind, feature_code, admin_code,
                           population, latitude, longitude, search_text, payload
                    FROM geo_entities WHERE geoname_id = $1 AND entity_type = $2
                    """,
                    geoname_id,
                    et,
                )
            elif admin_code:
                row = await conn.fetchrow(
                    """
                    SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                           iso2, iso3, country_code, place_kind, feature_code, admin_code,
                           population, latitude, longitude, search_text, payload
                    FROM geo_entities
                    WHERE entity_type = $1
                      AND (admin_code = $2 OR payload->>'code' = $2)
                    LIMIT 1
                    """,
                    et,
                    admin_code,
                )
            else:
                return None
        return row_to_entity(row, full_payload=True) if row else None

    async def cities_search(
        self,
        *,
        country_code: str | None = None,
        admin1_code: str | None = None,
        min_population: int | None = None,
        max_population: int | None = None,
        coastal_category: str | None = None,
        max_distance_to_coast_km: float | None = None,
        is_capital: bool | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = clamp_limit(limit)
        clauses = ["entity_type = 'city'"]
        args: list[Any] = []
        idx = 1
        if country_code:
            clauses.append(f"country_code = ${idx}")
            args.append(country_code.upper())
            idx += 1
        if admin1_code:
            clauses.append(f"payload->>'admin1_code' = ${idx}")
            args.append(admin1_code)
            idx += 1
        if min_population is not None:
            clauses.append(f"population >= ${idx}")
            args.append(min_population)
            idx += 1
        if max_population is not None:
            clauses.append(f"population <= ${idx}")
            args.append(max_population)
            idx += 1
        if coastal_category:
            clauses.append(f"payload->>'coastal_category' = ${idx}")
            args.append(coastal_category)
            idx += 1
        if max_distance_to_coast_km is not None:
            clauses.append(
                f"(payload->>'distance_to_coast_km')::float <= ${idx}"
            )
            args.append(max_distance_to_coast_km)
            idx += 1
        if is_capital is True:
            clauses.append("(payload->>'is_capital')::boolean = true")
        elif is_capital is False:
            clauses.append(
                "COALESCE((payload->>'is_capital')::boolean, false) = false"
            )
        if query:
            clauses.append(
                f"(name ILIKE ${idx} OR name_en ILIKE ${idx} OR ascii_name ILIKE ${idx})"
            )
            args.append(f"%{query}%")
            idx += 1
        args.append(limit)
        sql = f"""
            SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                   iso2, iso3, country_code, place_kind, feature_code, admin_code,
                   population, latitude, longitude, payload
            FROM geo_entities
            WHERE {' AND '.join(clauses)}
            ORDER BY population DESC NULLS LAST
            LIMIT ${idx}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [row_to_entity(r) for r in rows]

    async def nearest_marine(self, *, geoname_id: int | None = None, city_query: str | None = None) -> dict[str, Any]:
        city = None
        if geoname_id is not None:
            city = await self.get_by_id(geoname_id)
        elif city_query:
            hits = await self.resolve(query=city_query, entity_type="city", limit=1)
            if hits:
                city = await self.get_by_id(hits[0]["geoname_id"])
        if not city or city.get("entity_type") != "city":
            return {"error": "city not found"}
        payload = city.get("payload") or {}
        marine_id = payload.get("nearest_marine_geoname_id")
        marine = None
        if marine_id is not None:
            try:
                marine = await self.get_by_id(int(marine_id))
            except (TypeError, ValueError):
                marine = None
        return {
            "city": {k: city[k] for k in ("geoname_id", "name", "country_code", "latitude", "longitude") if k in city},
            "nearest_marine_name": payload.get("nearest_marine_name"),
            "nearest_marine_distance_km": payload.get("nearest_marine_distance_km"),
            "nearest_marine_feature_code": payload.get("nearest_marine_feature_code"),
            "distance_to_coast_km": payload.get("distance_to_coast_km"),
            "coastal_category": payload.get("coastal_category"),
            "marine": marine,
        }

    async def marine_get(self, *, geoname_id: int | None = None, query: str | None = None) -> dict[str, Any] | None:
        if geoname_id is not None:
            ent = await self.get_by_id(geoname_id)
            if ent and ent.get("entity_type") == "marine":
                return ent
            return None
        if query:
            hits = await self.resolve(query=query, entity_type="marine", limit=1)
            if hits:
                return await self.get_by_id(hits[0]["geoname_id"])
        return None

    async def coords_for(self, *, geoname_id: int | None = None, query: str | None = None, entity_type: str | None = None) -> dict[str, Any] | None:
        ent = None
        if geoname_id is not None:
            ent = await self.get_by_id(geoname_id)
        elif query:
            hits = await self.resolve(query=query, entity_type=entity_type, limit=1)
            if hits:
                ent = await self.get_by_id(hits[0]["geoname_id"])
        if not ent:
            return None
        lat, lon = ent.get("latitude"), ent.get("longitude")
        if lat is None or lon is None:
            # country → capital coords
            if ent.get("entity_type") == "country":
                iso = ent.get("iso2")
                if iso:
                    cap = await self.country_capital(iso)
                    c = cap.get("capital") or {}
                    if c.get("latitude") is not None:
                        return {
                            "entity": ent,
                            "latitude": c["latitude"],
                            "longitude": c["longitude"],
                            "coords_from": "capital",
                            "capital_geoname_id": c.get("geoname_id"),
                        }
            return {"entity": ent, "latitude": None, "longitude": None, "error": "no coordinates"}
        return {"entity": ent, "latitude": lat, "longitude": lon, "coords_from": "self"}

    async def distance(
        self,
        *,
        from_id: int | None = None,
        from_query: str | None = None,
        to_id: int | None = None,
        to_query: str | None = None,
    ) -> dict[str, Any]:
        a = await self.coords_for(geoname_id=from_id, query=from_query)
        b = await self.coords_for(geoname_id=to_id, query=to_query)
        if not a or a.get("latitude") is None:
            return {"error": "from location unresolved or missing coords", "from": a}
        if not b or b.get("latitude") is None:
            return {"error": "to location unresolved or missing coords", "to": b}
        km = haversine_km(float(a["latitude"]), float(a["longitude"]), float(b["latitude"]), float(b["longitude"]))
        return {
            "distance_km": round(km, 3),
            "from": {
                "geoname_id": a["entity"]["geoname_id"],
                "name": a["entity"].get("name"),
                "latitude": a["latitude"],
                "longitude": a["longitude"],
                "coords_from": a.get("coords_from"),
            },
            "to": {
                "geoname_id": b["entity"]["geoname_id"],
                "name": b["entity"].get("name"),
                "latitude": b["latitude"],
                "longitude": b["longitude"],
                "coords_from": b.get("coords_from"),
            },
        }

    async def nearby(
        self,
        *,
        center_id: int | None = None,
        center_query: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_km: float = 30,
        entity_type: str | None = None,
        place_kind: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        limit = clamp_limit(limit)
        radius_km = max(0.1, min(500.0, float(radius_km)))
        if latitude is None or longitude is None:
            c = await self.coords_for(geoname_id=center_id, query=center_query)
            if not c or c.get("latitude") is None:
                return {"error": "center unresolved", "center": c}
            latitude, longitude = float(c["latitude"]), float(c["longitude"])
            center_meta = {
                "geoname_id": c["entity"]["geoname_id"],
                "name": c["entity"].get("name"),
                "coords_from": c.get("coords_from"),
            }
        else:
            center_meta = {"latitude": latitude, "longitude": longitude}
        lat_min, lat_max, lon_min, lon_max = bbox(latitude, longitude, radius_km)
        clauses = [
            "latitude BETWEEN $1 AND $2",
            "longitude BETWEEN $3 AND $4",
            "latitude IS NOT NULL",
            "longitude IS NOT NULL",
        ]
        args: list[Any] = [lat_min, lat_max, lon_min, lon_max]
        idx = 5
        if entity_type:
            clauses.append(f"entity_type = ${idx}")
            args.append(entity_type)
            idx += 1
        if place_kind:
            clauses.append(f"place_kind = ${idx}")
            args.append(place_kind)
            idx += 1
        # Fetch extra for haversine filter
        fetch_n = min(500, limit * 15)
        args.append(fetch_n)
        sql = f"""
            SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                   iso2, iso3, country_code, place_kind, feature_code, admin_code,
                   population, latitude, longitude, payload
            FROM geo_entities
            WHERE {' AND '.join(clauses)}
            LIMIT ${idx}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        scored: list[dict[str, Any]] = []
        for r in rows:
            d = haversine_km(latitude, longitude, float(r["latitude"]), float(r["longitude"]))
            if d <= radius_km:
                ent = row_to_entity(r)
                ent["distance_km"] = round(d, 3)
                scored.append(ent)
        scored.sort(key=lambda x: x["distance_km"])
        return {
            "center": {**center_meta, "latitude": latitude, "longitude": longitude},
            "radius_km": radius_km,
            "count": min(len(scored), limit),
            "results": scored[:limit],
        }

    async def places_search(
        self,
        *,
        place_kind: str | None = None,
        country_code: str | None = None,
        query: str | None = None,
        min_elevation: float | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = clamp_limit(limit)
        clauses = ["entity_type = 'place'"]
        args: list[Any] = []
        idx = 1
        if place_kind:
            clauses.append(f"place_kind = ${idx}")
            args.append(place_kind)
            idx += 1
        if country_code:
            clauses.append(f"country_code = ${idx}")
            args.append(country_code.upper())
            idx += 1
        if query:
            clauses.append(
                f"(name ILIKE ${idx} OR name_en ILIKE ${idx} OR ascii_name ILIKE ${idx})"
            )
            args.append(f"%{query}%")
            idx += 1
        if min_elevation is not None:
            clauses.append(
                f"COALESCE((payload->>'elevation')::float, (payload->>'digital_elevation')::float) >= ${idx}"
            )
            args.append(min_elevation)
            idx += 1
        args.append(limit)
        sql = f"""
            SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                   iso2, iso3, country_code, place_kind, feature_code, admin_code,
                   population, latitude, longitude, payload
            FROM geo_entities
            WHERE {' AND '.join(clauses)}
            ORDER BY
              COALESCE((payload->>'elevation')::float, (payload->>'digital_elevation')::float)
                DESC NULLS LAST,
              population DESC NULLS LAST
            LIMIT ${idx}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [row_to_entity(r) for r in rows]

    async def airport_lookup(
        self,
        *,
        iata: str | None = None,
        icao: str | None = None,
        query: str | None = None,
        country_code: str | None = None,
        near_query: str | None = None,
        radius_km: float = 50,
        limit: int = 10,
    ) -> dict[str, Any]:
        limit = clamp_limit(limit, 10, 50)
        if iata or icao:
            async with self._pool.acquire() as conn:
                if iata:
                    rows = await conn.fetch(
                        """
                        SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                               iso2, iso3, country_code, place_kind, feature_code, admin_code,
                               population, latitude, longitude, payload
                        FROM geo_entities
                        WHERE entity_type = 'place' AND place_kind = 'airport'
                          AND upper(payload->>'iata') = upper($1)
                        LIMIT $2
                        """,
                        iata,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                               iso2, iso3, country_code, place_kind, feature_code, admin_code,
                               population, latitude, longitude, payload
                        FROM geo_entities
                        WHERE entity_type = 'place' AND place_kind = 'airport'
                          AND upper(payload->>'icao') = upper($1)
                        LIMIT $2
                        """,
                        icao,
                        limit,
                    )
            return {"results": [row_to_entity(r) for r in rows]}
        if near_query:
            return await self.nearby(
                center_query=near_query,
                radius_km=radius_km,
                entity_type="place",
                place_kind="airport",
                limit=limit,
            )
        results = await self.places_search(
            place_kind="airport",
            country_code=country_code,
            query=query,
            limit=limit,
        )
        return {"results": results}

    async def rank_places(
        self,
        *,
        place_kind: str,
        country_code: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        return await self.places_search(
            place_kind=place_kind,
            country_code=country_code,
            limit=limit,
        )

    async def text_search(
        self,
        *,
        query: str,
        entity_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = clamp_limit(limit)
        q = query.strip()
        if not q:
            return []
        clauses = [
            "(name % $1 OR name_en % $1 OR ascii_name % $1 OR search_text % $1 "
            "OR name ILIKE $2 OR search_text ILIKE $2)"
        ]
        args: list[Any] = [q, f"%{q}%"]
        idx = 3
        if entity_type:
            clauses.append(f"entity_type = ${idx}")
            args.append(entity_type)
            idx += 1
        args.append(limit)
        sql = f"""
            SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                   iso2, iso3, country_code, place_kind, feature_code, admin_code,
                   population, latitude, longitude, payload,
                   greatest(
                     similarity(COALESCE(name,''), $1),
                     similarity(COALESCE(name_en,''), $1),
                     similarity(COALESCE(ascii_name,''), $1)
                   ) AS score
            FROM geo_entities
            WHERE {' AND '.join(clauses)}
            ORDER BY score DESC NULLS LAST, population DESC NULLS LAST
            LIMIT ${idx}
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        out = []
        for r in rows:
            ent = row_to_entity(r)
            if "score" in r.keys() and r["score"] is not None:
                ent["similarity"] = float(r["score"])
            out.append(ent)
        return out

    async def semantic_via_anchor(
        self,
        *,
        query: str,
        entity_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Nearest neighbors in embedding space around the best name match (no external embedder)."""
        limit = clamp_limit(limit)
        anchors = await self.resolve(query=query, entity_type=entity_type, limit=3)
        if not anchors:
            text_hits = await self.text_search(query=query, entity_type=entity_type, limit=limit)
            return {"mode": "text_fallback", "anchor": None, "results": text_hits}
        anchor_id = anchors[0]["geoname_id"]
        async with self._pool.acquire() as conn:
            has_emb = await conn.fetchval(
                "SELECT embedding IS NOT NULL FROM geo_entities WHERE geoname_id = $1",
                anchor_id,
            )
            if not has_emb:
                text_hits = await self.text_search(query=query, entity_type=entity_type, limit=limit)
                return {
                    "mode": "text_fallback",
                    "anchor": anchors[0],
                    "results": text_hits,
                    "note": "anchor has no embedding",
                }
            clauses = ["embedding IS NOT NULL", "geoname_id <> $1"]
            args: list[Any] = [anchor_id]
            idx = 2
            if entity_type:
                clauses.append(f"entity_type = ${idx}")
                args.append(entity_type)
                idx += 1
            args.append(limit)
            sql = f"""
                SELECT geoname_id, entity_type, name, name_hu, name_en, ascii_name,
                       iso2, iso3, country_code, place_kind, feature_code, admin_code,
                       population, latitude, longitude, payload,
                       (embedding <=> (SELECT embedding FROM geo_entities WHERE geoname_id = $1)) AS dist
                FROM geo_entities
                WHERE {' AND '.join(clauses)}
                ORDER BY embedding <=> (SELECT embedding FROM geo_entities WHERE geoname_id = $1)
                LIMIT ${idx}
            """
            rows = await conn.fetch(sql, *args)
        results = []
        for r in rows:
            ent = row_to_entity(r)
            if r["dist"] is not None:
                ent["embedding_distance"] = float(r["dist"])
            results.append(ent)
        return {"mode": "embedding_neighbors", "anchor": anchors[0], "results": results}
