# `geo_entities` — adatmezők és kötések

> Csak mezők, leírásuk, és hogyan kell őket összekötni, hogy eredmény legyen.  
> Nincs tool / MCP / UI. Tábla: egy sor = egy GeoNames entitás. **Nincs relációs FK** — a „kötés” oszlop + `payload` JSON logika.

---

## Adatbázis elérés (DEV)

| | |
|--|--|
| Host | `158.220.119.105` |
| Port | `5433` |
| Database | `rag_dev` |
| User | `postgres` |
| Password | `ChangeMe-12345!` |
| Tábla | `geo_entities` |

```
postgresql://postgres:ChangeMe-12345!@158.220.119.105:5433/rag_dev
```

```bash
psql "postgresql://postgres:ChangeMe-12345!@158.220.119.105:5433/rag_dev"
```

---

## 1. Táblaoszlopok (közös minden típusra)

| Mező | Hol van | Mit jelent | Tipikus szűrés / rendezés |
|------|---------|------------|---------------------------|
| `geoname_id` | oszlop PK | GeoNames ID; minden kötés célja | `WHERE geoname_id = :id` |
| `entity_type` | oszlop | `country` \| `city` \| `place` \| `admin1` \| `admin2` \| `marine` | mindig szűrd típusra |
| `name` / `name_hu` / `name_en` / `ascii_name` | oszlop | megjelenő / kereshető nevek | trgm / ILIKE; fallback: `search_text` |
| `iso2` / `iso3` | oszlop | ország ISO (ország-sorokon) | ország lookup |
| `country_code` | oszlop | ISO2 a város/POI/admin/marine soron | országhoz kötés |
| `place_kind` | oszlop | POI fajta (`airport`, `lake`, …); más típusnál gyakran NULL | `entity_type='place' AND place_kind=…` |
| `feature_code` | oszlop | GeoNames feature kód | finom szűrés |
| `admin_code` | oszlop | pl. `IT.03`, `HU.23` (admin1/admin2) | régiókód egyezés |
| `population` | oszlop | népesség | `ORDER BY population DESC` |
| `latitude` / `longitude` | oszlop | WGS84; országnál gyakran NULL | távolság / nearby bbox |
| `search_text` | oszlop | embed / szöveges összefoglaló forrás | |
| `payload` | jsonb | teljes MEZOK rekord (típusfüggő kulcsok) | `payload->>'kulcs'` |
| `embedding` | vector(1024) | e5-large; lehet NULL | hibrid keresés |
| `created_at` / `updated_at` | oszlop | audit | |

---

## 2. Payload mezők típusonként

### 2.1 Ország (`entity_type = country`)

| Mező | Leírás | Eredményhez kötés |
|------|--------|-------------------|
| `iso2` / `iso3` / `iso_numeric` | országkódok | város/POI: `country_code = iso2` |
| `name` / `name_hu` / `name_en` / `official_name` | nevek | megjelenítés |
| `capital_geoname_id` | főváros GeoNames ID | → `city.geoname_id` |
| `capital_name` | főváros név (denormalizált) | gyors megjelenítés ID nélkül |
| `population` / `area_km2` | népesség, terület | rangsor / összehasonlítás |
| `continent_code` / `continent_name` | kontinens | szűrés / csoport |
| `currency_code` / `currency_name` | pénznem | utazási kártya |
| `languages` / `languages_resolved` | nyelvek (nyers / feloldott) | |
| `phone_code` / `tld` | hívószám, domain | |
| `neighbours` | szomszéd ISO2 lista | → más `country` sor `iso2`-je |
| `neighbours_resolved` | szomszéd nevek | megjelenítés |
| `is_landlocked` / `is_island_country` / `is_coastal` | földrajzi jelleg | „van-e tengerpartja az országnak” |
| `has_shape` | van-e shape import | partszámítás megbízhatóság |
| `postal_code_format` / `postal_code_regex` | irányítószám minta | |
| `fips` / `equivalent_fips` | legacy kódok | ritka |
| `wikidata_id` / `wikipedia_url` | külső link | |

### 2.2 Város (`entity_type = city`)

| Mező | Leírás | Eredményhez kötés |
|------|--------|-------------------|
| `country_code` | ISO2 | → `country.iso2` |
| `admin1_code` … `admin4_code` | admin szintek (rövid kód, pl. `03`) | admin1: lásd §3.2 |
| `parent_geoname_id` | szülő hely ID | → másik `geoname_id` |
| `is_capital` / `capital_level` | főváros / szint | |
| `population` + `population_rank_*` | népesség + rang (ország / admin1 / admin2 / global) | „N. legnagyobb város” |
| `latitude` / `longitude` | koordináta | távolság bármely lat/lon helyhez |
| `elevation` / `digital_elevation` | magasság | |
| `is_coastal` | bool partjel | lista: tengerparti városok |
| `distance_to_coast_km` | parttávolság (km) | rendezés / küszöb (`< 5`, `< 50`) |
| `coastal_category` | `direct_coastal` ≤5 / `coastal` ≤15 / `near_coast` ≤50 / `inland` | kategória-szűrés |
| `coastal_confidence` | megbízhatóság (shape vs fallback) | UI: „becslés” vs pontos |
| `nearest_marine_geoname_id` | legközelebbi tenger/öböl ID | → `marine.geoname_id` |
| `nearest_marine_name` | denormalizált név | gyors válasz |
| `nearest_marine_distance_km` | távolság a marine ponthoz | |
| `nearest_marine_feature_code` | marine feature | |
| `timezone` + `timezone_*_offset` | időzóna / GMT / DST | |
| `iata` / `icao` | ha a város soron van (ritka) | reptér inkább `place` |
| `postal_code` | irányítószám | |
| `alternate_names` | aliasok | keresés |
| `geohash` / `h3_res_5` / `h3_res_7` / `h3_res_9` | spatial index kulcsok | sugaras / grid közelség |
| `feature_*` | GeoNames feature meta | |
| `cc2` / `modification_date` / `import_marker` | import meta | ritka |
| `wikidata_id` / `wikipedia_url` | külső link | |

### 2.3 POI (`entity_type = place`)

| Mező | Leírás | Eredményhez kötés |
|------|--------|-------------------|
| `place_kind` | fajta (lásd §2.3.1) | kötelező szűrő turista-listáknál |
| `country_code` | ISO2 | → ország |
| `admin1_code` / `admin2_code` | régió | → admin |
| `parent_geoname_id` | szülő (város/régió) | → city/admin |
| `latitude` / `longitude` | koordináta | nearby / távolság várostól |
| `elevation` / `digital_elevation` | magasság (hegy, hágó) | `ORDER BY elevation DESC` |
| `iata` / `icao` | reptér kód | `place_kind='airport' AND iata=…` |
| `population` | ha van | |
| `timezone` | | |
| `feature_*` / nevek / wiki | meta | |

#### 2.3.1 Gyakori `place_kind` értékek

`hill`, `mountain`, `peak`, `volcano`, `lake`, `lakes`, `reservoir`, `waterfall`, `island`, `islands`, `atoll`, `airport`, `heliport`, `park`, `nature_reserve`, `wildlife_reserve`, `pass`, `ruin`, `castle`, `museum`, `monument`, `oilfield`, …

### 2.4 Admin1 (`entity_type = admin1`)

| Mező | Leírás | Eredményhez kötés |
|------|--------|-------------------|
| `code` (payload) / `admin_code` (oszlop) | pl. `IT.03` vagy rövid | város: `country_code` + `admin1_code` |
| `country_code` | ISO2 | → ország |
| `seat_geoname_id` | székhely város | → `city.geoname_id` |
| `type` | admin típus | |
| `population` / nevek / wiki | | |

### 2.5 Admin2 (`entity_type = admin2`)

| Mező | Leírás | Eredményhez kötés |
|------|--------|-------------------|
| `code` / `admin_code` | teljes kód | |
| `admin1_code` | szülő admin1 rövid kód | → admin1 |
| `country_code` | ISO2 | |
| `seat_geoname_id` | székhely | → city |
| `population` / nevek / wiki | | |

### 2.6 Marine (`entity_type = marine`)

| Mező | Leírás | Eredményhez kötés |
|------|--------|-------------------|
| `name` / `name_hu` / `name_en` | tenger, öböl, … | város `nearest_marine_*` célja |
| `latitude` / `longitude` | reprezentatív pont | távolság számítás importkor |
| `area_km2` | terület ha van | |
| `country_code` | ha van | |
| `feature_*` / wiki | | |

---

## 3. Kötési minták (hogyan kapj eredményt)

### 3.1 Ország ↔ város / POI

```
country.iso2  =  city.country_code
country.iso2  =  place.country_code
```

Ország listája városokra: `entity_type='city' AND country_code = :iso2` (+ opcionális `population`, `is_coastal`, `coastal_category`).

### 3.2 Város ↔ admin1 (régiónév)

Város payload: `admin1_code` = **rövid** kód (pl. `03`).  
Admin1: `admin_code` = **`{country_code}.{admin1_code}`** (pl. `IT.03`).

```
city.country_code + '.' + city.payload->>'admin1_code'
    ≈  admin1.admin_code
    OR admin1.payload->>'code'
    OR admin1.payload->>'admin1_code' = city rövid kód  (+ ugyanaz a country)
```

Fallback névmap: `GEO_ADMIN1_CODES_PATH` / `admin1CodesASCII.txt` ugyanerre a kulcsra.

### 3.3 Ország ↔ főváros

```
country.payload->>'capital_geoname_id'  =  city.geoname_id
```

### 3.4 Város ↔ legközelebbi tenger / öböl

Előszámolt (import BallTree) — **nem** élő JOIN kell listához:

```
city.payload->>'nearest_marine_geoname_id'  =  marine.geoname_id
```

Megjelenítéshez elég a denormalizált: `nearest_marine_name` + `nearest_marine_distance_km`.

Parttávolság / kategória **a város saját mezőiből** jön (`distance_to_coast_km`, `coastal_category`) — marine sor nélkül is listázható.

### 3.5 Város ↔ szülő / admin2

```
city.payload->>'parent_geoname_id'  →  másik geo_entities.geoname_id
city admin2_code + country  →  admin2 (hasonlóan admin1 mintához)
admin*.payload->>'seat_geoname_id'  →  city.geoname_id
```

### 3.6 Ország ↔ szomszédok

```
UNNEST(country.payload->'neighbours') AS iso2  →  másik country.iso2
```

### 3.7 Hely ↔ hely távolság (runtime)

Mindkét sornak kell `latitude` + `longitude`.  
Számítás: **haversine Pythonban** (nincs PostGIS).  
ID nélkül: névfeloldás → `geoname_id` → koordináta → km.

### 3.8 Nearby (sugaras lista)

1. Középpont: feloldott hely lat/lon  
2. Bbox SQL a lat/lon oszlopon  
3. Haversine szűrés km-re  
4. Opcionális: `place_kind` / `entity_type`  
5. Spatial kulcsok (`h3_res_*`, `geohash`) a payloadban — grid/sugaras gyorsításhoz

### 3.9 Reptér

```
entity_type = 'place'
AND place_kind = 'airport'
AND (payload->>'iata' = :code OR name …)
```

Ország / város közelség: `country_code` + nearby a város lat/lon körül.

### 3.10 Partos városlista (példa: Calabria, &lt;500 fő, ≤5 km)

```
entity_type = 'city'
AND country_code = 'IT'
AND (admin_code / payload admin1 → 'IT.03' kötés §3.2 szerint)
AND population < 500
AND (
  (payload->>'distance_to_coast_km')::float <= 5
  OR payload->>'coastal_category' = 'direct_coastal'
)
```

### 3.11 Egy entitás teljes profil

```
WHERE geoname_id = :id
```

Minden mező: oszlopok + `payload`. Régiónév: §3.2. Partblokk: §2.2 coastal mezők. Ország-tények: §3.1 után country sor.

---

## 4. Gyors „kérdés → mezők” térkép

| Kérdés | Elsődleges mezők | Kötés |
|--------|------------------|-------|
| Hol van X? | name*, lat/lon, country_code | resolve → sor |
| X országban milyen városok? | city + country_code + population | §3.1 |
| Van partja X-nek? | is_coastal, coastal_category, distance_to_coast_km | város/ország payload |
| Melyik tenger a legközelebb? | nearest_marine_* | §3.4 |
| Milyen messze A–B? | lat/lon mindkettőn | §3.7 |
| Reptér 30 km-en belül | place airport + nearby | §3.8–3.9 |
| Calabria / régió lista | admin1_code + admin_code | §3.2 |
| Főváros | capital_geoname_id | §3.3 |
| Szomszédok | neighbours | §3.6 |
| Legmagasabb hegy | place mountain/peak + elevation | place oszlop/payload |
| Pénznem / nyelv / TLD | country currency_*, languages*, tld | country payload |

---

## 5. Amit ne várj a mezőkből

- Nincs strand / szálloda / ár / nyitvatartás — csak geo + POI fajta.
- Nincs élő FK; hiányzó `nearest_marine_*` vagy `distance_to_coast_km` = nincs előszámolt kötés azon a soron.
- Ország lat/lon gyakran üres → ország–ország távolsághoz főváros (`capital_geoname_id`) kell.
- `embedding` NULL → vektorág kihagyható; név + facet SQL továbbra is megy.
