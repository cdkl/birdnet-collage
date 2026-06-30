import logging
import os
import re
import time
from collections import deque
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, send_from_directory, request, abort, g

from .config import Config
from .birdnet_client import BirdnetGoClient
from .collage_renderer import render_collage, compute_etag

log = logging.getLogger(__name__)

ILLUSTRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "assets", "illustrations")

# Ring buffer for recent errors (in-memory, resets on restart)
_error_log = deque(maxlen=20)
_request_times = deque(maxlen=100)
_last_birdnet_fetch = None
_last_birdnet_success = False
_eink_cache = {}  # key -> {"etag": str, "png": bytes, "expires": float}


def _count_illustrations():
    try:
        return len([f for f in os.listdir(ILLUSTRATIONS_DIR) if f.endswith(".png")])
    except Exception:
        return 0


def slugify(sci_name):
    return re.sub(r"[^a-z0-9]+", "-", sci_name.lower()).strip("-")


def create_app(config=None):
    if config is None:
        config = Config()

    app = Flask(
        __name__,
        static_folder="../frontend",
        static_url_path="",
        template_folder="../frontend",
    )
    app.config.from_object(config)

    client = BirdnetGoClient(config.BIRDNET_GO_URL, config.BIRDNET_GO_TOKEN)

    # --- Request timing middleware ---
    @app.before_request
    def before_request():
        g.start_time = time.monotonic()

    @app.after_request
    def after_request(response):
        elapsed = time.monotonic() - g.get("start_time", time.monotonic())
        _request_times.append({
            "path": request.path,
            "method": request.method,
            "status": response.status_code,
            "elapsed_ms": round(elapsed * 1000, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if elapsed > 2.0:
            log.warning("Slow request: %s %s took %.1fs", request.method, request.path, elapsed)
        return response

    # --- API: diagnostics ---

    @app.route("/api/diagnostics")
    def api_diagnostics():
        global _last_birdnet_fetch, _last_birdnet_success
        go_ok = client.health_check()
        return jsonify({
            "birdnet_go": {
                "url": config.BIRDNET_GO_URL,
                "reachable": go_ok,
                "last_fetch": _last_birdnet_fetch,
                "last_fetch_success": _last_birdnet_success,
            },
            "illustrations": {
                "count": _count_illustrations(),
                "path": ILLUSTRATIONS_DIR,
            },
            "recent_requests": list(_request_times)[-20:],
            "recent_errors": list(_error_log),
            "uptime": {
                "as_of": datetime.now(timezone.utc).isoformat(),
            },
            "config": {
                "birdnet_go_url": config.BIRDNET_GO_URL,
                "port": config.PORT,
            },
            "eink_cache": {
                "entries": len(_eink_cache),
                "keys": list(_eink_cache.keys()),
                "ages_seconds": {
                    k: round(time.monotonic() - v.get("_ts", 0), 1)
                    for k, v in _eink_cache.items()
                },
            },
        })

    @app.route("/api/debug")
    def api_debug():
        """Raw fetch debug: shows exactly what Birdnet-GO returns."""
        try:
            raw_json = client._get("/detections", {"limit": 2, "offset": 0})
            items = raw_json.get("data", []) if isinstance(raw_json, dict) else []
            return jsonify({
                "status": "ok",
                "birdnet_go_url": client.base_url,
                "raw_keys": list(raw_json.keys()) if isinstance(raw_json, dict) else str(type(raw_json)),
                "raw_total": raw_json.get("total", "N/A") if isinstance(raw_json, dict) else "N/A",
                "data_count": len(items),
                "first_item_keys": list(items[0].keys()) if items else None,
                "first_item": items[0] if items else None,
            })
        except Exception as e:
            return jsonify({"error": str(e), "birdnet_go_url": client.base_url})

    # --- API: image serving ---

    @app.route("/api/img/<path:sci_name>")
    def api_img(sci_name: str):
        slug = slugify(sci_name)
        pose = request.args.get("pose", "1")
        if pose == "2":
            filename = f"{slug}-2.png"
        else:
            filename = f"{slug}.png"

        if os.path.isfile(os.path.join(ILLUSTRATIONS_DIR, filename)):
            return send_from_directory(ILLUSTRATIONS_DIR, filename)

        # Fallback: if pose-2 doesn't exist, serve pose-1
        if pose == "2":
            filename = f"{slug}.png"
            if os.path.isfile(os.path.join(ILLUSTRATIONS_DIR, filename)):
                return send_from_directory(ILLUSTRATIONS_DIR, filename)

        log.warning("Missing illustration: %s -> %s", sci_name, filename)
        abort(404)

    # --- API: collage-compatible endpoints ---

    @app.route("/api/recent")
    def api_recent():
        hours = request.args.get("hours", 24, type=int)
        hours = max(1, min(1000000, hours))
        try:
            t0 = time.monotonic()
            species = client.get_recent_species(hours=hours)
            elapsed = time.monotonic() - t0
            _record_fetch(len(species), elapsed, True)
        except Exception as e:
            log.exception("Failed to get recent species for %dh window", hours)
            _record_error("recent", hours, str(e))
            _record_fetch(0, 0, False)
            species = []
        return jsonify({
            "hours": hours,
            "species": species,
        })

    @app.route("/api/stats")
    def api_stats():
        try:
            t0 = time.monotonic()
            species = client.get_recent_species(hours=8760)
            total_detections = sum(s["n"] for s in species)
            total_species = len(species)
            species_24h = client.get_recent_species(hours=24)
            todays_detections = sum(s["n"] for s in species_24h)
            todays_species = len(species_24h)
            species_1h = client.get_recent_species(hours=1)
            last_hour = sum(s["n"] for s in species_1h)
            elapsed = time.monotonic() - t0
            _record_fetch(total_detections, elapsed, True)
        except Exception as e:
            log.exception("Failed to get stats")
            _record_error("stats", "all", str(e))
            _record_fetch(0, 0, False)
            total_detections = 0
            total_species = 0
            todays_detections = 0
            todays_species = 0
            last_hour = 0

        return jsonify({
            "totals": {
                "detections": total_detections,
                "species": total_species,
            },
            "today": {
                "detections": todays_detections,
                "species": todays_species,
            },
            "last_hour": {
                "detections": last_hour,
            },
            "as_of": datetime.now(timezone.utc).isoformat(),
        })

    @app.route("/api/lifelist")
    def api_lifelist():
        try:
            species = client.get_recent_species(hours=1000000)
        except Exception:
            log.exception("Failed to get lifelist")
            species = []
        return jsonify({
            "species": species,
        })

    @app.route("/api/species")
    def api_species():
        sci = request.args.get("sci", "")
        if not sci:
            abort(400)
        try:
            all_species = client.get_recent_species(hours=1000000)
            match = next((s for s in all_species if s["sci"] == sci), None)
        except Exception:
            log.exception("Failed to get species detail for %s", sci)
            match = None
        return jsonify({
            "sci": sci,
            "summary": {
                "com": match["com"] if match else sci,
                "total": match["n"] if match else 0,
                "first_seen": match.get("first_seen", ""),
                "last_seen": match.get("last_seen", ""),
                "best_conf": match.get("best_conf", 0),
            } if match else None,
            "detections": [],
        })

    # --- API: e-ink collage render ---

    @app.route("/api/eink")
    def api_eink():
        w = request.args.get("w", 1600, type=int)
        h = request.args.get("h", 1200, type=int)
        hours = request.args.get("hours", 24, type=int)
        w = max(200, min(4000, w))
        h = max(200, min(4000, h))
        hours = max(1, min(1000000, hours))

        key = f"eink:{w}:{h}:{hours}"
        cached = _eink_cache.get(key)

        # If client sent If-None-Match and it matches cache, 304 fast path
        client_etag = request.headers.get("If-None-Match", "").strip('"')
        if cached and client_etag and client_etag == cached["etag"]:
            return ("", 304)

        now = time.monotonic()

        # Fetch species data
        try:
            species = client.get_recent_species(hours=hours)
            _record_fetch(len(species), time.monotonic() - now, True)
        except Exception as e:
            log.exception("Failed to fetch species for eink render")
            _record_error("eink", f"{w}x{h} {hours}h", str(e))
            _record_fetch(0, time.monotonic() - now, False)
            # Serve stale cache if available
            if cached:
                log.info("Serving stale eink cache (%.0fs old)",
                         time.monotonic() - (cached.get("_ts", 0)))
                resp = (cached["png"], 200,
                        {"Content-Type": "image/png", "ETag": cached["etag"]})
                if client_etag and client_etag == cached["etag"]:
                    resp = ("", 304)
                return resp
            abort(503)

        # Compute ETag and check if cached data is still current
        etag = compute_etag(species, hours)
        if cached and cached["etag"] == etag:
            cached["_ts"] = now  # refresh timestamp
            _eink_cache[key] = cached
            if client_etag and client_etag == etag:
                return ("", 304)
            return (cached["png"], 200,
                    {"Content-Type": "image/png", "ETag": etag})

        # Render new collage
        t0 = time.monotonic()
        try:
            png = render_collage(species, w, h, config.SITE_TITLE)
        except Exception as e:
            log.exception("Failed to render eink collage")
            _record_error("eink_render", f"{w}x{h} {hours}h {len(species)}spp", str(e))
            if cached:
                return (cached["png"], 200,
                        {"Content-Type": "image/png", "ETag": cached["etag"]})
            abort(503)

        elapsed = time.monotonic() - t0
        if elapsed > 10:
            log.warning("Eink render slow: %d species at %dx%d in %.1fs",
                        len(species), w, h, elapsed)
        if elapsed > 45:
            log.error("Eink render critical: %.1fs at %dx%d (gunicorn timeout=60s)",
                      elapsed, w, h)

        _eink_cache[key] = {
            "etag": etag,
            "png": png,
            "expires": now + 86400,
            "_ts": now,
        }
        _record_fetch(len(species), elapsed, True)
        return (png, 200, {"Content-Type": "image/png", "ETag": etag})

    @app.route("/api/timeseries")
    def api_timeseries():
        days = request.args.get("days", 30, type=int)
        days = max(1, min(90, days))
        try:
            species = client.get_recent_species(hours=days * 24)
        except Exception:
            log.exception("Failed to get timeseries for %d days", days)
            species = []
        return jsonify({
            "days": days,
            "daily": [],
            "by_hour": [],
            "species": species,
            "as_of": datetime.now(timezone.utc).isoformat(),
        })

    @app.route("/api/firstseen")
    def api_firstseen():
        limit = request.args.get("limit", 10, type=int)
        limit = max(1, min(50, limit))
        try:
            species = client.get_recent_species(hours=1000000)
            recent = sorted(
                [s for s in species if s.get("first_seen")],
                key=lambda s: s.get("first_seen", ""),
                reverse=True,
            )[:limit]
        except Exception:
            log.exception("Failed to get firstseen")
            recent = []
        return jsonify({
            "species": recent,
        })

    @app.route("/api/health")
    def api_health():
        go_ok = client.health_check()
        try:
            num_illustrations = _count_illustrations()
        except Exception:
            num_illustrations = 0
        return jsonify({
            "status": "ok" if go_ok else "degraded",
            "birdnet_go": {
                "reachable": go_ok,
                "url": config.BIRDNET_GO_URL,
            },
            "illustrations": num_illustrations,
            "as_of": datetime.now(timezone.utc).isoformat(),
        })

    # --- Frontend routes ---

    @app.route("/")
    def index():
        return render_template("index.html", site_title=config.SITE_TITLE)

    @app.route("/<path:path>")
    def static_files(path):
        return send_from_directory(app.static_folder, path)

    return app


def _record_fetch(count, elapsed, success):
    global _last_birdnet_fetch, _last_birdnet_success
    _last_birdnet_fetch = datetime.now(timezone.utc).isoformat()
    _last_birdnet_success = success
    if elapsed > 3.0:
        log.warning("Slow Birdnet-GO fetch: %d results in %.1fs (success=%s)", count, elapsed, success)


def _record_error(endpoint, param, message):
    _error_log.append({
        "endpoint": endpoint,
        "param": str(param),
        "error": message[:200],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config = Config()
    log.info("Birdnet-GO URL: %s", config.BIRDNET_GO_URL)
    log.info("Illustrations directory: %s (%d PNGs)", ILLUSTRATIONS_DIR, _count_illustrations())
    log.info("Starting birdnet-collage on %s:%s", config.HOST, config.PORT)
    app = create_app(config)
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)


if __name__ == "__main__":
    main()