import logging
import time
from datetime import datetime, timedelta, timezone
import requests

log = logging.getLogger(__name__)

MAX_LIMIT = 1000


class BirdnetGoClient:
    """Client for Birdnet-GO's REST API (api/v2)."""

    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "birdnet-collage/1.0",
        })
        self.session.verify = True
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path, params=None):
        url = f"{self.base_url}/api/v2{path}"
        t0 = time.monotonic()
        try:
            resp = self.session.get(url, params=params, timeout=30)
            elapsed = time.monotonic() - t0
            resp.raise_for_status()
            log.debug("GET %s → %d (%.2fs)", url, resp.status_code, elapsed)
            return resp.json()
        except requests.ConnectionError as e:
            log.error("Cannot reach Birdnet-GO at %s: %s", self.base_url, e)
            raise
        except requests.Timeout:
            log.error("Birdnet-GO timed out at %s (30s)", self.base_url)
            raise
        except requests.HTTPError as e:
            log.error("Birdnet-GO HTTP error: %s %s → %s", e.request.method, e.request.url, e.response.status_code if e.response else "no response")
            raise
        except requests.RequestException as e:
            log.error("Birdnet-GO request failed: %s", e)
            raise

    def _fetch_detections_page(self, offset=0, limit=MAX_LIMIT):
        data = self._get("/detections", params={
            "limit": limit,
            "offset": offset,
        })
        items = data.get("data", [])
        if not items and offset == 0:
            log.warning("Birdnet-GO returned 0 detections (offset=0). Check if detection is running.")
        return items

    def get_recent_species(self, hours=24):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        MAX_PAGES = 50

        all_detections = []
        pages = 0
        t0 = time.monotonic()
        while pages < MAX_PAGES:
            try:
                page_data = self._fetch_detections_page(
                    offset=pages * MAX_LIMIT, limit=MAX_LIMIT
                )
            except Exception:
                log.exception("Failed to fetch detections page %d", pages)
                if pages == 0:
                    raise
                break
            if not page_data:
                break
            all_detections.extend(page_data)
            pages += 1
            oldest_ts = page_data[-1].get("timestamp", "")
            if oldest_ts:
                try:
                    if datetime.fromisoformat(oldest_ts) < cutoff:
                        break
                except ValueError:
                    pass
            if len(page_data) < MAX_LIMIT:
                break

        elapsed = time.monotonic() - t0
        log.info("Fetched %d detections across %d page(s) in %.1fs for %dh window",
                 len(all_detections), pages, elapsed, hours)

        species_map: dict[str, dict] = {}
        kept = 0
        for d in all_detections:
            sci = d.get("scientificName", "")
            ts = d.get("timestamp", "")
            if not sci or not ts:
                continue
            try:
                detection_dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if detection_dt < cutoff:
                continue
            kept += 1

            if sci not in species_map:
                species_map[sci] = {
                    "sci": sci,
                    "com": d.get("commonName", sci),
                    "n": 0,
                    "best_conf": 0.0,
                    "last_seen": "",
                }
            species_map[sci]["n"] += 1
            species_map[sci]["best_conf"] = max(
                species_map[sci]["best_conf"],
                float(d.get("confidence", 0)),
            )
            if ts > species_map[sci]["last_seen"]:
                species_map[sci]["last_seen"] = ts

        log.info("Filtered to %d in-window detections across %d species",
                 kept, len(species_map))

        species = sorted(
            species_map.values(),
            key=lambda s: s["n"],
            reverse=True,
        )
        return species

    def health_check(self) -> bool:
        """Check if Birdnet-GO is reachable."""
        try:
            self._get("/system/info")
            return True
        except Exception:
            log.warning("Birdnet-GO health check failed at %s", self.base_url)
            return False