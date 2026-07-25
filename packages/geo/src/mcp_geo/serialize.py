from __future__ import annotations

import json
from typing import Any

import asyncpg

PLACE_KINDS = [
    "hill",
    "mountain",
    "peak",
    "volcano",
    "lake",
    "lakes",
    "reservoir",
    "waterfall",
    "island",
    "islands",
    "atoll",
    "airport",
    "heliport",
    "park",
    "nature_reserve",
    "wildlife_reserve",
    "pass",
    "ruin",
    "castle",
    "museum",
    "monument",
    "oilfield",
]

ENTITY_TYPES = ["country", "city", "place", "admin1", "admin2", "marine"]

# Payload keys useful in list/card responses (full payload still in geo_get).
_SLIM_PAYLOAD_KEYS = (
    "official_name",
    "capital_geoname_id",
    "capital_name",
    "continent_code",
    "continent_name",
    "currency_code",
    "currency_name",
    "languages_resolved",
    "phone_code",
    "tld",
    "neighbours",
    "is_landlocked",
    "is_island_country",
    "is_coastal",
    "area_km2",
    "admin1_code",
    "admin2_code",
    "is_capital",
    "capital_level",
    "distance_to_coast_km",
    "coastal_category",
    "coastal_confidence",
    "nearest_marine_geoname_id",
    "nearest_marine_name",
    "nearest_marine_distance_km",
    "timezone",
    "iata",
    "icao",
    "elevation",
    "digital_elevation",
    "wikidata_id",
    "wikipedia_url",
    "seat_geoname_id",
    "code",
    "type",
)


def _payload_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return dict(raw)


def row_to_entity(row: asyncpg.Record, *, full_payload: bool = False) -> dict[str, Any]:
    payload = _payload_dict(row["payload"] if "payload" in row.keys() else {})
    out: dict[str, Any] = {
        "geoname_id": int(row["geoname_id"]),
        "entity_type": row["entity_type"],
        "name": row.get("name"),
        "name_hu": row.get("name_hu"),
        "name_en": row.get("name_en"),
        "ascii_name": row.get("ascii_name"),
        "iso2": row.get("iso2"),
        "iso3": row.get("iso3"),
        "country_code": row.get("country_code"),
        "place_kind": row.get("place_kind"),
        "feature_code": row.get("feature_code"),
        "admin_code": row.get("admin_code"),
        "population": row.get("population"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
    }
    if full_payload:
        out["payload"] = payload
        if "search_text" in row.keys():
            out["search_text"] = row["search_text"]
    else:
        slim = {k: payload[k] for k in _SLIM_PAYLOAD_KEYS if k in payload}
        if slim:
            out["payload"] = slim
    # Drop nulls for compactness
    return {k: v for k, v in out.items() if v is not None}


def clamp_limit(raw: Any, default: int = 20, maximum: int = 50) -> int:
    try:
        n = int(raw if raw is not None else default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(maximum, n))
