# AGENTS.md

## Project governance

`spec.md` is the authoritative record of requirements, architecture, and constraints. Read it first when approaching any task. Update it when requirements change, new decisions are made, or limitations are removed.

## Running the project

```bash
# one-time setup
git config core.hooksPath .githooks   # enable pre-commit smoke tests

# local dev
cd birdnet-collage
pip install -r requirements.txt
BIRDNET_GO_URL=http://your-birdnet-go:8080 python3 -m flask --app src.app:create_app run --port 8081

# production (Docker)
docker compose up -d --build

# tests
python3 -m pytest
```

## Frontend constraints

`frontend/apt.js` is adapted from AvianVisitors. When modifying:

- The JS expects API responses in a specific format. See the **Frontend field contract** section in `spec.md`.
- Image paths use `/api/img/{slug}?pose=N`. The backend slugifies scientific names (lowercase, hyphens).
- Species without a mask in the `MASKS` object are silently dropped from the collage (line ~446: `if (!mask) return null`). This is expected behavior, not a bug.
- `DIMS` and `MASKS` are large inlined data objects (~700KB). Do not regenerate them without going through AvianVisitors' Python pipeline (`cutout.py` → `build_masks.py`).
- The JS is a **browser script**, not a Node module. It references `document`, `window`, and DOM APIs. Syntax check with `node -c apt.js`; functional testing requires a browser.

## Birdnet-GO API notes

- `/api/v2/detections` returns `{"data": [...], "total": N, "limit": N, "offset": N}`.
- Field names: `scientificName`, `commonName`, `date`, `time`, `timestamp` (ISO 8601 with timezone), `confidence`.
- Max 1000 items per page. Paginate with `offset`.
- No built-in date-range filtering. Filter client-side after fetching.
- Detection rate ~1,250/day on an active instance. Expect 1 page ≈ 22h of data.

## Image serving

- 498 PNG illustrations in `frontend/assets/illustrations/` (290MB). Included in the Docker image.
- Naming: `{downcased-hyphenated-sci}.png` for perched (pose=1), `{slug}-2.png` for flight (pose=2).
- Missing images return 404 — the JS handles this.
- `api_img()` in `app.py` slugifies input, checks file existence, and falls back pose-2 to pose-1 if missing.

## Python conventions

- No type annotations using `|` (union syntax) — Python 3.9 compatibility.
- Use `dict[str, dict]` style generics (3.9+ built-in support).
- Mock `BirdnetGoClient` in Flask tests by patching `src.app.BirdnetGoClient` *before* calling `create_app()`.
- Log level: INFO by default, DEBUG for verbose API tracing.

## Docker / deployment

- Container always binds 8081 internally. Host port mapped via `${PORT:-8081}:8081` in docker-compose.
- CMD uses exec form `["gunicorn", ...]` — shell form breaks on `create_app()` parentheses.
- `python:3.12-slim` image lacks `curl`. Debug with `docker exec birdnet-collage python3 -c "import requests; ..."`.
- Systemd unit at `deploy/birdnet-collage.service` expects the repo at `/opt/birdnet-collage`.

## Debugging

- `/api/health` — Birdnet-GO reachability + illustration count.
- `/api/diagnostics` — Birdnet-GO fetch history, recent request timings, error ring buffer, config.
- `/api/debug` — Raw Birdnet-GO API response (shows exact JSON keys and totals).
- Docker logs: `docker logs birdnet-collage`.
- Common failure modes:
  - **DNS**: container can't resolve Birdnet-GO hostname → `recent` returns 0 species, health still shows reachable (different endpoint). Fix: check DNS in container.
  - **Empty collage**: Birdnet-GO reachable but species lack masks in `MASKS` object → all tiles filtered at line ~446.
  - **Stale code**: `docker compose up -d` without `--build` uses cached image.

## Tests

57 tests in `tests/`:

| File | Scope |
|---|---|
| `test_birdnet_client.py` | API parsing, pagination, time filtering, species aggregation, error handling, edge cases |
| `test_app.py` | All API endpoints, image serving, input clamping, error responses, diagnostics |
| `test_slugify.py` | Name normalization edge cases |
| `conftest.py` | Shared fixtures: sample detections, mock API responses, multi-page data |