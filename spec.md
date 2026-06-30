# birdnet-collage — Specification

## Purpose

A standalone decoupled service that displays a live bird detection collage. Extracts and repurposes the "Heard Recently" collage generation from [AvianVisitors](https://github.com/Twarner491/AvianVisitors) (a BirdNET-Pi fork) as an independent service backed by [Birdnet-GO](https://github.com/tphakala/birdnet-go)'s REST API.

## Requirements

- **R1**: Run as a containerized service deployable as a Proxmox LXC.
- **R2**: Consume bird detection data from a Birdnet-GO instance via its `/api/v2/detections` REST endpoint.
- **R3**: Display a live bird collage, stats view, and atlas (field guide) view.
- **R4**: Support time windows: 1H, 12H, 24H, 7D, ALL.
- **R5**: Bundle AvianVisitors' 498 kachō-e bird illustrations (249 species × 2 poses: perched + flight).
- **R6**: Require zero configuration of Birdnet-GO beyond its default REST API.
- **R7**: Support both HTTP and HTTPS Birdnet-GO URLs.
- **R8**: Provide diagnostics endpoint for troubleshooting.

## Architecture

```
Browser ──► birdnet-collage (Flask + gunicorn) ──► Birdnet-GO REST API
E-ink  ──►       │    port 8081 (container)              /api/v2/detections
                  ├─ /api/recent?hours=N
                  ├─ /api/stats
                  ├─ /api/lifelist
                  ├─ /api/species?sci=X
                  ├─ /api/img/{species}?pose=N   ← 498 bundled PNGs
                  ├─ /api/eink?w=N&h=N&hours=N   ← Rendered collage PNG (e-ink)
                  ├─ /api/health
                  ├─ /api/diagnostics
                  └─ /api/debug
```

**Stack**: Python 3.12 → Flask → gunicorn (2 workers) inside Docker. Frontend is static HTML/CSS/JS adapted from AvianVisitors.

## Key decisions

| Decision | Rationale |
|---|---|
| Python/Flask backend | Simple API proxy; no heavy computation |
| Bundle AvianVisitors illustrations | 498 pre-generated kachō-e art; no runtime image generation |
| AvianVisitors frontend ported, not forked | Removed admin, auth, audio, recordings, Wiki fallback |
| Birdnet-GO `/api/v2/detections` as data source | No additional Birdnet-GO configuration required |
| Client-side time-window filtering | Birdnet-GO API lacks date-range params; paginate and filter locally |
| Paginate at 1000/page (GO's cap) | Fetch pages until oldest detection predates cutoff |
| Docker exec form in CMD | Shell form caused syntax errors with `create_app()` |
| Container port 8081 always; host port mapped via `${PORT}` | Decouples internal bind from external exposure |
| 2 vCPU / 512 MB RAM recommendation | Thin proxy; 290MB of illustrations loaded at startup |
| Python-side collage renderer (Pillow) | Server-rendered PNG for e-ink clients; Python port of JS mask-packing algorithm with reference-validation tests |
| ETag-based 304 caching | E-ink clients detect no-change via If-None-Match, avoiding unnecessary render + transfer |
| Two independent algorithm implementations | JS (web view) and Python (e-ink render) share the same MASKS/DIMS data; validated against shared reference fixtures |

## API adaptation (AvianVisitors → Birdnet-GO)

| Original (BirdNET-Pi PHP → SQLite) | Adapted |
|---|---|
| `birdnet-api.php?action=recent&hours=N` | `/api/recent?hours=N` (client fetches GO, filters, aggregates by species) |
| `birdnet-api.php?action=stats` | `/api/stats` (aggregates recent, 24h, 1h windows) |
| `birdnet-api.php?action=lifelist` | `/api/lifelist` (all-time fetch, unlimited hours) |
| `birdnet-api.php?action=species&sci=X` | `/api/species?sci=X` (searches all-time list) |
| `cutout.php?sci=X&pose=N` | `/api/img/{species}?pose=N` (static PNGs, slugified) |
| _new_ | `/api/eink?w=N&h=N&hours=N` (server-rendered collage PNG with title, ETag caching) |
| `recording.php?file=X` | _removed_ |
| `wiki.php?sci=X` | _removed_ |
| `menu.php`, `config.php`, `birdnet-status.php` | _removed_ |

## Removed from AvianVisitors

- Admin panel (system status, logs, service restart)
- Password authentication / lock screen
- Live audio streaming (Icecast)
- Recording playback and spectrograms
- Wikipedia + rembg dynamic image fallback
- Cloudflare, Home Assistant, MQTT forwarding
- BirdNET-Pi service management
- Menu population via API

## Frontend field contract

Backend API responses must match what `apt.js` expects:

**`/api/recent?hours=N`** → `{hours: int, species: [{sci, com, n, best_conf, last_seen}]}` — sorted by `n` descending.
**`/api/stats`** → `{totals: {detections, species}, today: {detections, species}, last_hour: {detections}, as_of}`
**`/api/lifelist`** → `{species: [{sci, com, n}]}`
**`/api/species?sci=X`** → `{sci, summary: {com, total, first_seen, last_seen, best_conf} | null}`
**`/api/img/{species}?pose=1|2`** → PNG image; slugified from scientific name.
**`/api/eink?w=1600&h=1200&hours=24`** → `image/png` body with `ETag` header. Server-rendered collage + site title. Supports `If-None-Match` → 304. Parameters: `w` (200–4000), `h` (200–4000), `hours` (1–1000000).

## Illustrations

- 498 PNGs in `frontend/assets/illustrations/` (290MB total)
- Naming: `{downcased-hyphenated-sci}.png` (perched), `{slug}-2.png` (flight)
- Cover 249 species, California/Western North America focused
- Species outside coverage render without an image in the collage (JS handles missing masks gracefully)
- Illustrations generated by AvianVisitors' Gemini 2.5 Flash Image pipeline

## Limitations

- **Illustration coverage**: 249 Western NA species; Eastern species lack art
- **No date-range API**: paginates and filters client-side; ~37 pages for full history
- **No audio playback**: recordings, spectrograms removed
- **No species filtering**: all GO detections shown regardless of region
- **No admin interface**: diagnostics via `/api/diagnostics` only
- **E-ink render first-request latency**: The first `/api/eink` request after data changes takes 5–10s (fetch + mask pack + composite). Subsequent requests return 304 or serve cached PNG in <1s.
- **Web view unaffected**: The frontend's `apt.js` drives the web collage independently. The Python renderer is only used by `/api/eink`.
- **Stale cache on Birdnet-GO outage**: If Birdnet-GO is unreachable, `/api/eink` serves the last cached image (up to 24h old) rather than erroring.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `BIRDNET_GO_URL` | (required) | Birdnet-GO base URL (http or https) |
| `BIRDNET_GO_TOKEN` | (none) | Bearer token if GO auth enabled |
| `PORT` | 8081 | Host port for docker-compose mapping |
| `SITE_TITLE` | `birdnet collage` | Title shown in browser tab, header, about modal |

**Constraint**: Every env var consumed by the application must appear in:
1. `src/config.py` — Python-side default and `os.getenv()` call.
2. `docker-compose.yml` `environment:` block — forwards the value into the container. Use `${VAR:-default}` syntax so it works with or without a `.env` file.
3. `.env.example` — documents the variable for users.

Failure to add an env var to docker-compose results in the container silently using the Python-side default.

## Deployment (Proxmox LXC)

Debian/Ubuntu LXC → install Docker → clone repo → set `.env` → `docker compose up -d`.
Systemd unit at `deploy/birdnet-collage.service` for auto-start on boot.

Resources: 1 vCPU, 512 MB RAM, 8 GB disk.

## Tests

118 pytest tests covering: Birdnet-GO client (pagination, filtering, aggregation, error handling), Flask endpoints (all API routes, image serving, clamps, error paths), slugify, diagnostics, collage renderer (PRNG, mask decoder, tuning, tile sizing, spiral packer with reference validation against JS), e-ink endpoint (PNG response, ETag/304, resolution clamping). Run via `python3 -m pytest`.

## Attribution

Collage rendering, frontend, and illustrations adapted from [AvianVisitors](https://github.com/Twarner491/AvianVisitors) by @Twarner491.
Detection data from [Birdnet-GO](https://github.com/tphakala/birdnet-go) by @tphakala.
Acoustic classification by [BirdNET](https://birdnet.cornell.edu/) (Cornell Lab of Ornithology / Chemnitz University).
License: CC-BY-NC-SA-4.0 (inherited).