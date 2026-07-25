# Nyilvános MCP platform — rétegterv

Státusz: **MVP implementálva** — éles domain **https://mcp.glc-rag.hu** (nginx + Let’s Encrypt).  
Backend: `127.0.0.1:8780` (csak nginx elől); szolgáltatás: `mcp-platform.service`.  
Első szolgáltatások: **hello** (élő), **geo** (üres DB + pgvector).  
DB: app = PostgreSQL **`MCP`**; geo = **`mcp_geo`** üres + **pgvector**.

## Célkép

| Felület | Ki | Mit csinál |
|--------|----|------------|
| **Public web** | bárki (auth nélkül is) | **dinamikus** MCP szolgáltatáslista + leírások; **auto-generált docs** (ember + agent); belépés Adminba vagy System-adminba |
| **Admin** | regisztrált org user | fiók, API kulcs; **szolgáltatásra regisztráció** (kérelem); saját státusz / usage |
| **System-admin** | platform üzemeltető | regisztrált userek **MCP hozzáférésének engedélyezése**; szolgáltatás katalogus; audit |
| **MCP endpoint** | LLM kliensek | tool hívások API kulccsal — csak **system-admin által engedélyezett** szolgáltatásokra |

Az MCP **nem** helyettesíti a web UI-t: a web a **emberi + kulcskezelő + engedélyezési** felület, az MCP a **gép–gép tool** felület.

Az MCP nem „gondolkodik”: az LLM kiadja a tool call-t → az MCP végrehajtja (pl. hello / geo) → visszaadja az eredményt.

### Hozzáférési folyamat

```text
Public oldal
  │  dinamikus lista: elérhető MCP szolgáltatások + leírás
  │  belépés → Admin  vagy  System-admin
  ▼
Admin
  │  user regisztrál egy adott MCP szolgáltatásra (kérelem)
  ▼
System-admin
  │  engedélyezi a regisztrált usernek az adott MCP használatát
  ▼
MCP endpoint
     a user API tokenje csak az engedélyezett szolgáltatások tooljait látja / hívja
```

---

## Rétegek (alulról felfelé)

```text
┌─────────────────────────────────────────────────────────┐
│  L7  Surfaces                                            │
│  Public Web │ Tenant Admin │ System Admin │ MCP HTTP     │
│  Host: mcp.glc-rag.hu                                    │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  L6  Edge / Gateway                                      │
│  nginx (TLS), rate limit, CORS, API-key / session auth   │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  L5  Identity & Tenancy                                  │
│  1 user ↔ 1 org; service registration request;           │
│  system-admin approve → enabled services;                │
│  1 API token az összes eng. szolgáltatáshoz;             │
│  roles (user/admin/sysadmin); audit; kvóta unlimited     │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  L4  MCP Protocol Adapter                                │
│  transport: streamable HTTP                              │
│  tools/list, tools/call filtered by approved services    │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  L3  Capability / Service registry                       │
│  katalogus: név + leírás (public lista forrása);         │
│  „hello”, majd „geo”, …; versioning                      │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  L2  Domain services                                     │
│  HelloService (smoke)                                    │
│  GeoService — DB-független interfész; spec később        │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│  L1  Data adapters                                       │
│  Geo DB: üres váz + vector (pgvector); ~30 GB később     │
│  App DB: PostgreSQL adatbázis neve **MCP**               │
└─────────────────────────────────────────────────────────┘
```

**Miért így:** a geo DB / spec későbbi megadása **L1–L2**-n marad; a nyilvános MCP, kulcsok, admin **L5–L7**-en nem törik el, ha a geo forrás változik.

---

## Rétegenként

### L1 — Data adapters

Két világ:

- **App DB (`MCP`):** PostgreSQL adatbázis neve: **`MCP`**. Users, orgs (1:1 user↔org), roles, API keys (nincs lejárat), szolgáltatás-regisztráció kérelmek (`pending` / `approved` / `rejected`), system-admin által engedélyezett MCP szolgáltatások, usage/audit (kvóta alapból nincs limit). Kis memóriaigény. Helyi Postgres (`127.0.0.1:5432`).
- **Geo DB:** egyelőre **üres** (nincs betöltött geo adat). Elrejtve `GeoRepository` adapter mögé.
  - Kötelező kiegészítő: **vector plugin** — PostgreSQL **`pgvector`** extension (embedding / hasonlóság keresés a geo szolgáltatáshoz).
  - **Memória foglalás később: ~30 GB** (amikor a geo adathalmaz bekerül) — a DB / host sizing ezt vegye figyelembe.
  - Spec + adatbetöltés később; addig a schema/extension előkészíthető, a tartalom üres.

### L2 — Domain services

Tiszta use-case-ek. Nem tud HTTP-ről, MCP-ről, sessionről.

- `HelloService` — első smoke szolgáltatás.
- `GeoService` — specifikáció és DB később.

### L3 — Capability / service registry

Katalogus: melyik szolgáltatás él (`hello`, majd `geo`, …), **név + publikus leírás**, tool nevek, verzió.

A **public oldal dinamikusan** ebből a katalogusból listáz (nem hardcode).  
A tényleges használati jog **nem** automatikus: Admin kérelem → System-admin approve.

### L4 — MCP protocol adapter

- **Transport: streamable HTTP** (webes proxy / nginx barát).
- Protokoll: `tools/list`, `tools/call`, hibák, tool JSON schema.
- Auth contextet az L5-ből kapja.

### L5 — Identity & tenancy

- **Tenant modell:** 1 user = 1 org.
- **Szolgáltatás-hozzáférés:**
  1. Adminban a user **regisztrál** egy MCP szolgáltatásra → státusz `pending`
  2. System-admin **engedélyezi** → `approved` (vagy elutasítja → `rejected`)
  3. MCP csak `approved` szolgáltatások tooljait szolgálja ki
- **Egy API token** érvényes az org összes **approved** szolgáltatására.
- Role-ok: `user` / `org_admin` / `system_admin` (belépés a **public** oldalról)
- Session (web) vs API key (MCP)
- **Kulcs:** create / rotate / revoke Adminban; létrehozás után **nincs lejárat**
- **Kvóta:** alapértelmezetten **korlátlan**
- Audit log (kérelem + approve/reject események)
- **Adatizoláció:** egy user API-n keresztül **nem** olvashatja / írhatja más user adatait jog nélkül (lásd Authorization)

### L6 — Edge / gateway

- Host: `mcp.glc-rag.hu` — nginx + Let’s Encrypt **kész** (`COOKIE_SECURE=true`)
- TLS (1. titkosítási réteg), (opcionális) rate limit, CORS, auth routing
- Web/Admin/System-admin API: **két rétegű titkosítás** (lásd lent); MCP token kivétel

### L7 — Surfaces

Egy host, role szerinti felületek:

| Útvonal (váz) | Felület | Auth |
|---------------|---------|------|
| `/` | **Public** — dinamikus MCP szolgáltatáslista + leírások; belépő Admin / System-admin felé | nincs (lista), login link |
| `/admin…` | **Admin** — regisztráció szolgáltatásra, API kulcs, saját kérelmek státusza | session (`org_admin` / `user`) |
| `/system-admin…` | **System-admin** — pending kérelmek approve/reject; katalogus; audit | session (`system_admin`) |
| `/mcp` | MCP streamable HTTP | API key (MCP token — **nincs** két rétegű payload-titkosítás) |

Példa:

- Web: `https://mcp.glc-rag.hu/`
- MCP: `https://mcp.glc-rag.hu/mcp`

---

## Auth szétválasztás

| Csatorna | Auth | Titkosítás |
|----------|------|------------|
| Public | nincs a listához; belépés innen Adminba vagy System-adminba | lista: TLS; érzékeny API: 2 réteg |
| Admin web | session (public login után) | **2 rétegű** API titkosítás |
| System-admin web | session, szigorúbb role (2FA később) | **2 rétegű** API titkosítás |
| MCP | **egy org-szintű API key** (MCP token); csak **approved** szolgáltatások | TLS elegendő; **MCP token / MCP forgalom kivétel** a 2. réteg alól |

Az LLM **soha** ne kapjon system-admin jogot toolon keresztül — csak a kulcshoz kötött org + system-admin által engedélyezett szolgáltatások.

---

## API titkosítás (két réteg)

A web / admin / system-admin **API** hívásaira kötelező a **két rétegű titkosítás**.

| Réteg | Hol | Cél |
|-------|-----|-----|
| **1. Transport** | TLS (HTTPS / nginx) | csatorna védelme |
| **2. Application** | request/response payload titkosítás (app szint) | tartalom védelme TLS terminálás / proxy mögött is |

**Kivétel:** az **MCP token** és az MCP endpoint (`/mcp`) forgalma — itt nincs második (application) réteg; a kliens (LLM) egyszerű API-key + TLS modellel csatlakozik.

Megjegyzés implementációhoz: a 2. réteg algoritmusa / kulcscsere (pl. session-kötött kulcs) a részletes security tervben rögzítendő; a követelmény már most kötelező a webes API-kra.

---

## Authorization & adatizoláció (kötelező)

Egy felhasználó az API-n **ne tudja** más felhasználó adatait **olvasni vagy írni**, ha nincs ehhez joga.

### Szabályok

1. Minden érzékeny API hívás a **bejelentkezett / tokenhez kötött identity** kontextusában fut (`user_id` / `org_id`).
2. **Default deny:** idegen user / org erőforrás → `403` (vagy `404`, ha nem akarunk létezést elárulni).
3. **Saját scope:** Admin user csak a **saját org** adatait (kulcsok, kérelmek, usage) látja / módosítja.
4. **System-admin** kivétel: platform szintű olvasás/írás (approve, userek, audit) — explicit role, nem „mindenki admin”.
5. **MCP token:** csak a tokenhez tartozó org **approved** szolgáltatásait hívja; más org / más user kontextusába nem léphet át.
6. Nincs „userId a body-ban → azt a usert szerkesztem” a session/token ellenőrzése nélkül — az identity **mindig** a szerver oldali auth-ból jön, nem a kliens által megadott idegen ID-ból.
7. Listázó endpointok alapból **szűrve** a saját org-ra; system-admin listák külön, role-gated route-on.

### Ellenőrzés helye

- L5 / core middleware: minden Admin / System-admin / (ahol releváns) API handler előtt.
- Adatréteg: query-k `org_id` / `user_id` kötelező feltétellel (ne csak UI-ban szűrjünk).

---

## Szolgáltatások (első release)

| Sorrend | Szolgáltatás | Megjegyzés |
|---------|--------------|------------|
| 1 | **hello** | hello-world smoke teszt (első élő MCP tool) |
| 2 | **geo** | DB egyelőre **üres**; **pgvector** kötelező; spec + adat később; memória ~**30 GB** betöltéskor |

Geo tool kontrakt / DB adapter csak a spec után kerül a tervbe.

---

## Modularitás (kötelező)

Az MCP szerver **szigorúan moduláris**: tiszta határok, egy felelősség / modul, nincs „god package”.

### Alapelvek

1. **Egy MCP szolgáltatás = egy izolált modul** (`hello`, `geo`, …) — saját toolok, domain, adapter; más szolgáltatás kódját nem importálja.
2. **Core ≠ szolgáltatás** — auth, registry, MCP transport, tenancy a core-ban; a szolgáltatásmodulok csak a core által definiált **plugin interfészen** keresztül csatlakoznak.
3. **Függőség iránya:** `apps/*` → `packages/core` + `packages/<service>`; szolgáltatás → szolgáltatás **tilos**.
4. **Új szolgáltatás** = új package + registry bejegyzés + (opcionális) leírás a public katalogushoz — a core és a többi szolgáltatás **érintetlen** marad.
5. **Tiszta kód:** vékony adapterek, domain a középütt, nincs üzleti logika az HTTP/MCP handlerben; publikus API-k kicsik; belső részletek nem szivárognak át package határon.
6. **Tesztelhetőség:** minden szolgáltatásmodul önállóan unit-tesztelhető (mockolt repository); core auth/registry külön.

### Modulhatárok (váz)

```text
apps/mcp-server          → összerakás (wiring), nincs domain logika
apps/web                 → public + admin + system-admin UI
packages/core            → MCP transport, auth, registry, registration/approve
packages/hello           → HelloService + tools (smoke)
packages/geo             → GeoService + tools + GeoRepository adapter
packages/<next-service>  → ugyanaz a minta
```

### Plugin / service contract (L3)

Minden szolgáltatásmodul implementálja pl.:

- `id`, `name`, `description` (public lista)
- `tools[]` (név, **gazdag description**, JSON schema, handler, opcionális példák)
- `docs` — használati szöveg (markdown részletek: auth, példahívás, hibák) — **a publikus / agent docs forrása**
- opcionális health / readiness

A core `tools/list` / `tools/call` csak a registry + user **approved** set alapján szűr — a szolgáltatás nem tud más user jogairól.

A **dokumentáció automatikusan** a registry + modul `docs` / tool metaadatából keletkezik (nincs kézzel másolt párhuzamos wiki).

---

## Automatikus MCP dokumentáció (ember + agent)

### Mit csinálnak máshol (ipar / specreferencia)

| Minta | Hol | Cél |
|-------|-----|-----|
| **`llms.txt`** | pl. [modelcontextprotocol.io/llms.txt](https://modelcontextprotocol.io/llms.txt) | agent-barát index: linkek a markdown oldalakra |
| **Gazdag `tools/list`** | MCP spec | tool `description` + `inputSchema` — az agent elsődleges „hogyan használd” forrása csatlakozás után |
| **MCP Resources** | MCP spec `resources/*` | `docs://…` erőforrások, amiket az agent `resources/read`-del beolvas |
| **`.well-known/mcp`** | SEP-1960 irány | gép discovery: endpoint, auth, capabilities (auth előtt) |
| **Server Card** | SEP-1649 / 2127 irány | `/.well-known/mcp/server-card.json` — név, leírás, transport URL, homepage |
| **Emberi docs oldal** | vendor landing + `/docs` | Cursor config példa, token, szolgáltatásonkénti leírás |

### Nálunk: egy forrás → több felület (kötelező)

A katalogus / plugin metaadat **SSOT**. A core **DocGenerator** belőle állít elő mindent; új szolgáltatás modul = docs magától bővül.

```text
packages/<service>  docs + tools meta
         │
         ▼
   packages/core  DocGenerator + registry
         │
         ├─► /                  nyitóoldal: lista + „Dokumentáció” link
         ├─► /docs              emberi HTML (szolgáltatásonként)
         ├─► /docs/{service}.md ugyanaz markdownban (ember + agent fetch)
         ├─► /llms.txt          agent index (összes szolgáltatás + linkek)
         ├─► /llms-full.txt     opcionális: teljes docs egy fájlban
         ├─► /.well-known/mcp   discovery (streamable_http URL, api_key auth)
         ├─► /.well-known/mcp/server-card.json
         └─► MCP resources      docs://catalog, docs://{service}
```

### Nyilvános URL-ek (auth nélkül, csak TLS)

| URL | Közönség | Tartalom |
|-----|----------|----------|
| `/docs` | ember | HTML: összes **listed** szolgáltatás + bekötési útmutató |
| `/docs/{id}` | ember | egy szolgáltatás: leírás, toolok, példák, hibák |
| `/docs/{id}.md` | ember + agent | ugyanez markdown (egyszerű `GET`, scrapelhető) |
| `/llms.txt` | agent | index a markdown oldalakra (mcp.io minta) |
| `/llms-full.txt` | agent | teljes használati szöveg egy válaszban |
| `/.well-known/mcp` | kliens discovery | `streamable_http`, `authentication: api_key`, `documentation` URL |
| `/.well-known/mcp/server-card.json` | kliens discovery | serverInfo, transport, homepage, docs link |

A nyitóoldal (`/`) minden szolgáltatás kártyáján linkel: **Docs** → `/docs/{id}`.

### MCP-n keresztül (token után)

| Mechanizmus | Viselkedés |
|-------------|------------|
| `tools/list` | csak **approved** toolok; description + schema mindig friss a modulból |
| `resources/list` | legalább: `docs://catalog`, `docs://{service}` (approved service-ekre) |
| `resources/read` | a generált markdown (ugyanaz, mint `/docs/{id}.md`) |

Így az agent **böngésző nélkül** is elolvashatja a használati útmutatót MCP resource-ként.

### Doc tartalom / szolgáltatás (generált váz)

1. Mi ez a szolgáltatás (1–2 bekezdés)
2. Auth: MCP URL + API key header (példa Cursor `mcp.json`)
3. Tool tábla: név, mikor használd, paraméterek, példa request/response
4. Tipikus hibák / korlátok
5. Verzió

**Hello** példából automatikusan: `hello_ping` input/output a modulból — nincs külön kézi oldal.

### Titkosítás

A docs / `llms.txt` / `.well-known/*` **publikus**, csak TLS (nincs 2. réteg) — az agentek és crawler-ek egyszerűen olvassák.

Monorepo, **élesen elkülönülő** csomagok:

| Csomag | Felelősség |
|--------|------------|
| `apps/web` | public + admin + system-admin UI (role-gated) |
| `apps/mcp-server` | csak wiring: core + regisztrált service modulok |
| `packages/core` | L4–L6 közös: streamable HTTP, identity, registration/approve, service registry |
| `packages/hello` | izolált hello modul (L2) |
| `packages/geo` | izolált geo modul; üres DB + **pgvector**; ~30 GB adat később |
| `packages/<service>` | minden új MCP szolgáltatás külön package |
| `infra` | nginx (`mcp.glc-rag.hu`); Postgres DB **`MCP`**; geo üres + pgvector; ~30 GB később |
| `docs/` | tervezési dokumentumok |

**Tilos:** közös „utils” szemeteszsák domain logikával; cross-import szolgáltatásmodulok között; üzleti szabály az `apps/mcp-server` belsejében.

---

## Döntések (lezárva — te)

1. **Tenant modell:** 1 user = 1 org; **egy API token** jó az összes **approved** szolgáltatáshoz.
2. **MCP transport:** streamable HTTP.
3. **Kulcs életciklus:** create / rotate / revoke Adminban; létrehozás után **nincs lejárat**.
4. **Kvóta:** alapértelmezetten **korlátlan**.
5. **Első szolgáltatások:** először csak **hello** (teszt), majd **geo** (spec később).
6. **Host:** `mcp.glc-rag.hu` — nginx + Let’s Encrypt kész (a tervbeli `glc-reg` DNS NXDOMAIN volt).
7. **Public oldal:** dinamikus szolgáltatáslista + leírások (L3 katalogus).
8. **Belépés:** public → Admin vagy System-admin.
9. **Engedélyezés:** Adminban szolgáltatásra regisztráció (kérelem) → System-admin approve után használható az MCP.
10. **Architektúra:** szigorúan moduláris MCP; core + izolált service package-ek; tiszta kód, tilos a szolgáltatás–szolgáltatás függőség.
11. **API titkosítás:** web/admin/system-admin API **két rétegű** (TLS + application payload); **MCP token / `/mcp` kivétel**.
12. **Adatizoláció:** user A nem olvashatja / írhatja user B adatait jog nélkül; default deny; identity mindig szerver oldali auth-ból; system-admin explicit kivétel.
13. **Auto docs:** nyitóoldalról; SSOT = service modul meta; `/docs`, `/llms.txt`, `.well-known/mcp*`, MCP `docs://` resources.
14. **App DB neve:** PostgreSQL adatbázis **`MCP`** (rendszer / identity / katalogus).
15. **Geo DB:** egyelőre **üres**; kötelező **vector kiegészítő (`pgvector`)**; ~30 GB adat később.

---

## Alapértelmezett döntések (agent javaslat)

Ezek a korábban nyitott pontok **intelligens alapértékei** a meglévő pergel / `*.glc-rag.hu` mintára (FastAPI, nginx, Certbot). Felülírhatók; amíg nincs ellenkező utasítás, ez a terv.

### Tech stack

| Réteg | Választás | Indok |
|-------|-----------|--------|
| Backend | **Python 3.11+ / FastAPI / uvicorn** | planer, xtest, caai mintája; MCP streamable HTTP jól illik |
| Frontend | **Vite + TypeScript SPA** (egy app, role-gated route-ok) | xtest mintája; public/admin/sysadmin egy build |
| App DB | **PostgreSQL**, adatbázis neve: **`MCP`** | identity, kulcsok, registration, audit; helyi `127.0.0.1:5432` |
| Geo DB | PostgreSQL (külön DB vagy schema), **egyelőre üres** + **`pgvector`** | vector keresés; ~30 GB RAM/adat később; ne keveredjen a `MCP` app DB tartalmával |
| Package layout | monorepo (`apps/*`, `packages/*`), `uv` vagy pip | moduláris követelmény |
| Backend port | **8780** (localhost; nginx proxy) | szabad, konzisztens a többi glc service-szel |

### TLS / nginx

- **Let’s Encrypt + Certbot** (ugyanaz a minta, mint `planner.glc-rag.hu` stb.).
- Nginx: `mcp.glc-rag.hu` → `127.0.0.1:8780`; `/mcp` hossú timeout, buffering off (streamable HTTP).
- ACME webroot: `/home/pergel/mcp/public` (vagy app gyökér).

### User regisztráció & bootstrap

- **Self-registration:** email + jelszó → létrehoz `user` + 1 org; jelszó **argon2id**.
- Email verify: **MVP-ben kikapcsolható** (`REQUIRE_EMAIL_VERIFY=false`); ha van SMTP, később bekapcsolható.
- **System-admin bootstrap:** első indításkor env:
  - `MCP_BOOTSTRAP_ADMIN_EMAIL`
  - `MCP_BOOTSTRAP_ADMIN_PASSWORD`
  - csak ha még nincs `system_admin` a DB-ben (idempotens seed).
- Org role MVP: a regisztráló user = **`org_admin`** (1 user = 1 org mellett nincs külön „tag” szerep az első release-ben).

### 2. titkosítási réteg (web API)

| Elem | Választás |
|------|-----------|
| Algoritmus | **AES-256-GCM** |
| Kulcscsere | login / session létrehozás után a szerver kiad egy **per-session CEK**-et (TLS-en); kliens memóriában tartja (nem localStorage hosszú távon) |
| Request | body = `{ "iv", "ciphertext", "tag" }` (+ `Content-Type: application/json`); opcionális header `X-Payload-Encrypted: 1` |
| Response | ugyanez visszafelé |
| Hatáskör | `/api/admin/*`, `/api/system-admin/*`, auth utáni érzékeny `/api/*` |
| **Kivétel** | `/mcp`, MCP token, public katalogus, **`/docs*`, `/llms*.txt`, `/.well-known/mcp*`** (csak TLS) |
| Kulcs forgatás | session lejáratakor új CEK; session TTL pl. **12 óra** |

### Abuse rate limit (nem termék-kvóta)

Kvóta unlimited marad; DoS ellen soft limit:

| Csatorna | Limit (alap) |
|----------|----------------|
| Login / regisztráció | 10 / perc / IP |
| Web API (session) | 120 / perc / user |
| MCP `/mcp` | 300 / perc / token |

429 + `Retry-After`. System-admin felülírhatja később.

### Authorization mátrix (MVP)

| Erőforrás | org_admin | system_admin |
|-----------|-----------|--------------|
| Saját profil / jelszó | RW | RW (bármely user) |
| Saját API token | create/rotate/revoke | revoke (emergency) |
| Service registration (saját) | create, read | read all, approve/reject |
| Más org adatai | **tiltva** | RW (platform) |
| Service katalogus (publikus mezők) | R | RW |
| Audit log | saját org R | all R |
| MCP tools (approved) | call via token | nem MCP-n keresztül „sudo” |

Default deny minden másra.

### Service registration állapotgép

`pending` → `approved` \| `rejected`; `rejected` után újra `pending` (új kérelem).  
`approved` → system-admin `revoked` (opcionális) → MCP azonnal eldobja.

### Public katalogus mezők

`id`, `name`, `description`, `version`, `listed` (bool), `status` (`available` \| `beta` \| `maintenance`).

### Service plugin interfész (core)

```text
McpServiceModule:
  id: str
  name: str
  description: str
  version: str
  listed: bool
  docs: MarkdownDoc | structured sections   # auto docs forrása
  tools: list[ToolSpec]   # name, description, inputSchema, examples?, handler
  health() -> ok|err      # opcionális
```

Új szolgáltatás = új `packages/<id>` + wiring az `apps/mcp-server`-ben; core + **DocGenerator** változatlan — a `/docs`, `/llms.txt` és `docs://` resource magától megjelenik.

### Hello tool (smoke)

| Tool | Input | Output |
|------|-------|--------|
| `hello_ping` | `{ "name"?: string }` | `{ "message": "hello, {name\|world}", "ts": iso8601 }` |

### Route váz

| Path | Felület |
|------|---------|
| `/` | public lista + login + **Docs** linkek |
| `/docs`, `/docs/{id}`, `/docs/{id}.md` | auto-generált emberi / markdown docs |
| `/llms.txt`, `/llms-full.txt` | agent-olvasható docs index / full |
| `/.well-known/mcp`, `/.well-known/mcp/server-card.json` | MCP discovery |
| `/login` | közös login (role szerint redirect) |
| `/register` | self-reg |
| `/admin` | token, service kérelmek, státusz |
| `/system-admin` | pending approve, userek, katalogus edit |
| `/api/...` | web API (2 rétegű titkosítás ahol kell) |
| `/mcp` | MCP streamable HTTP (+ `resources` docs://…) |
| `/health` | liveness |
| `/ready` | app DB (+ később geo) readiness |

### Értesítés, audit, backup, monitoring

| Téma | Alapérték |
|------|-----------|
| Approve értesítés | Admin UI státusz; email csak ha `SMTP_*` be van állítva |
| Audit retention | **90 nap**, utána purge job |
| App DB backup | napi `pg_dump` az **`MCP`** DB-ről, 7 nap (`/home/pergel/db-backups` mintára) |
| Geo backup | amíg üres: skip; adatbetöltés után külön ütem (heti full, ~30 GB) |
| Logging | nginx access/error + app JSON log; nincs titok a logban (token hash only) |
| Staging | MVP: **egyetlen** prod env; később opcionális |

### MCP kliens doksi (public)

Rövid példa: Cursor MCP config → URL `https://mcp.glc-rag.hu/mcp` + API key header. A token **soha** ne kerüljön a public gitbe.

---

## Még tőled várunk (nem találgatható)

1. **Geo specifikáció** (toolok, mezők) + mikor / honnan töltjük az üres geo DB-t.

**Host megjegyzés:** a tervben szereplő `mcp.glc-reg.hu` publikus DNS-en **NXDOMAIN** volt; éles host: **`mcp.glc-rag.hu`** (A rekord + Let’s Encrypt kész).

---

## Következő lépések (implementáció előtt / közben)

1. Postgres: létrehozni az **`MCP`** adatbázist + app séma (users, orgs, api_keys, …).
2. Geo: üres DB/schema + `CREATE EXTENSION vector` (**pgvector**).
3. DocGenerator: `hello` → minta `/docs/hello.md` + `/llms.txt` + `docs://hello`.
4. Nginx vhost + Certbot a `mcp.glc-rag.hu`-hoz.
5. Scaffold monorepo: `packages/core`, `packages/hello`, `apps/mcp-server`, `apps/web`.
6. Geo adat + tool spec megérkezése → `packages/geo` feltöltése.
