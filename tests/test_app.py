import pytest
import os
import tempfile
from unittest.mock import Mock, patch, PropertyMock

from src.app import create_app, slugify
from src.config import Config


def make_mock_client(species_lists=None, health_ok=True, raises=None):
    """Create a mock BirdnetGoClient that returns controlled data.

    species_lists: dict mapping hours → list of species dicts.
    e.g. {1: [...], 24: [...], 8760: [...], 1000000: [...]}
    """
    mock = Mock()
    if raises:
        mock.get_recent_species.side_effect = raises
        mock.health_check.side_effect = raises
    else:
        if species_lists:
            def get_species(hours):
                # Return the closest matching window, or empty
                for h in sorted(species_lists.keys()):
                    if hours <= h:
                        return species_lists[h]
                return species_lists.get(max(species_lists.keys()), []) if species_lists else []
            mock.get_recent_species.side_effect = get_species
        else:
            mock.get_recent_species.return_value = []
        mock.health_check.return_value = health_ok
    return mock


class TestRecentEndpoint:
    def test_returns_species_list(self):
        species = [
            {"sci": "Bombycilla cedrorum", "com": "Cedar Waxwing", "n": 10, "best_conf": 0.9, "last_seen": "2026-06-20T18:00:00+00:00"},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 5, "best_conf": 0.85, "last_seen": "2026-06-20T17:55:00+00:00"},
        ]
        mock = make_mock_client(species_lists={24: species})
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/recent?hours=24")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["hours"] == 24
                assert len(data["species"]) == 2

    def test_default_hours(self):
        mock = make_mock_client(species_lists={24: []})
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/recent")
                assert resp.status_code == 200
                assert resp.get_json()["hours"] == 24

    def test_clamps_negative_hours(self):
        mock = make_mock_client(species_lists={1: []})
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/recent?hours=-5")
                assert resp.get_json()["hours"] == 1

    def test_clamps_large_hours(self):
        mock = make_mock_client(species_lists={1000000: []})
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/recent?hours=999999999")
                assert resp.get_json()["hours"] == 1000000

    def test_handles_client_error_gracefully(self):
        mock = make_mock_client(raises=Exception("API down"))
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/recent?hours=24")
                assert resp.status_code == 503
                data = resp.get_json()
                assert data["species"] == []
                assert "error" in data


class TestStatsEndpoint:
    def test_returns_stats_format(self):
        species_1h = [{"sci": "s1", "com": "c1", "n": 3, "best_conf": 0.9, "last_seen": ""}]
        species_24h = [
            {"sci": "s1", "com": "c1", "n": 10, "best_conf": 0.9, "last_seen": ""},
            {"sci": "s2", "com": "c2", "n": 5, "best_conf": 0.85, "last_seen": ""},
        ]
        species_all = species_24h + [{"sci": "s3", "com": "c3", "n": 1, "best_conf": 0.65, "last_seen": ""}]

        mock = make_mock_client(species_lists={1: species_1h, 24: species_24h, 8760: species_all})
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/stats")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["totals"]["detections"] == 16
                assert data["totals"]["species"] == 3
                assert data["today"]["detections"] == 15
                assert data["today"]["species"] == 2
                assert data["last_hour"]["detections"] == 3

    def test_handles_error_gracefully(self):
        mock = make_mock_client(raises=Exception("Down"))
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/stats")
                assert resp.get_json()["totals"]["detections"] == 0


class TestLifelistEndpoint:
    def test_returns_all_species(self):
        species = [{"sci": "Species a", "com": "A", "n": 1, "best_conf": 0.9, "last_seen": ""}]
        mock = make_mock_client(species_lists={1000000: species})
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/lifelist")
                assert len(resp.get_json()["species"]) == 1


class TestSpeciesEndpoint:
    def test_returns_species_detail(self):
        all_species = [
            {"sci": "Bombycilla cedrorum", "com": "Cedar Waxwing", "n": 10, "best_conf": 0.92, "last_seen": "2026-06-20T18:00:00+00:00"},
        ]
        mock = make_mock_client(species_lists={1000000: all_species})
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/species?sci=Bombycilla%20cedrorum")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["summary"]["com"] == "Cedar Waxwing"
                assert data["summary"]["total"] == 10

    def test_missing_sci_returns_400(self):
        mock = make_mock_client()
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/species")
                assert resp.status_code == 400

    def test_unknown_species_returns_null_summary(self):
        mock = make_mock_client()
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/species?sci=NonExistent%20bird")
                assert resp.get_json()["summary"] is None


class TestHealthEndpoint:
    def test_health_ok(self):
        mock = make_mock_client(health_ok=True)
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/health")
                data = resp.get_json()
                assert data["status"] == "ok"
                assert data["birdnet_go"]["reachable"] is True

    def test_health_degraded(self):
        mock = make_mock_client(health_ok=False)
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/health")
                assert resp.get_json()["status"] == "degraded"


class TestFrontendRoutes:
    def _app(self):
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        mock = make_mock_client()
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            return app

    def test_index_serves_html(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert b"<!doctype html>" in resp.data.lower()

    def test_static_js_served(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/apt.js")
            assert resp.status_code == 200

    def test_static_css_served(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/styles.css")
            assert resp.status_code == 200


class TestImageEndpoint:
    def _app(self):
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        mock = make_mock_client()
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            return app

    def test_existing_illustration(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/api/img/Bombycilla%20cedrorum")
            assert resp.status_code == 200
            assert resp.content_type == "image/png"

    def test_missing_illustration_404(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/api/img/NonExistentSpecies%20doesnotexisti")
            assert resp.status_code == 404

    def test_flight_pose(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/api/img/Turdus%20migratorius?pose=2")
            assert resp.status_code == 200

    def test_flight_fallback_to_perched(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/api/img/Baeolophus%20inornatus?pose=2")
            assert resp.status_code == 200


class TestTimeseriesEndpoint:
    def _app(self):
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        mock = make_mock_client()
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            return app

    def test_clamps_days(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/api/timeseries?days=999")
            assert resp.get_json()["days"] == 90

    def test_returns_format(self):
        app = self._app()
        with app.test_client() as client:
            resp = client.get("/api/timeseries?days=7")
            data = resp.get_json()
            assert "daily" in data
            assert "by_hour" in data
            assert "species" in data


class TestDiagnosticsEndpoint:
    def test_returns_diagnostics(self):
        mock = make_mock_client(health_ok=True)
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/api/diagnostics")
                data = resp.get_json()
                assert data["birdnet_go"]["reachable"] is True
                assert data["birdnet_go"]["url"] == "http://mock:8080"
                assert "illustrations" in data
                assert isinstance(data["illustrations"]["count"], int)
                assert "config" in data
                assert "recent_requests" in data
                assert "recent_errors" in data
                assert "eink_cache" in data


class TestEinkEndpoint:
    """Tests for the /api/eink collage render endpoint."""

    def _app_and_client(self, species_lists=None, health_ok=True):
        mock = make_mock_client(species_lists=species_lists, health_ok=health_ok)
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        config.SITE_TITLE = "test collage"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            return app.test_client()

    def test_returns_png(self):
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 10,
             "best_conf": 0.9, "last_seen": "2026-06-20T18:00:00+00:00"},
        ]
        client = self._app_and_client(species_lists={24: species})
        resp = client.get("/api/eink")
        assert resp.status_code == 200
        assert resp.content_type == "image/png"
        assert resp.data[:4] == b'\x89PNG'
        assert "ETag" in resp.headers

    def test_304_on_match(self):
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 10,
             "best_conf": 0.9, "last_seen": "2026-06-20T18:00:00+00:00"},
        ]
        client = self._app_and_client(species_lists={24: species})
        # First request gets the ETag
        resp1 = client.get("/api/eink")
        etag = resp1.headers["ETag"]
        # Second request with If-None-Match should 304
        resp2 = client.get("/api/eink", headers={"If-None-Match": etag})
        assert resp2.status_code == 304
        assert resp2.data == b''

    def test_200_on_etag_mismatch(self):
        species1 = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 10,
             "best_conf": 0.9, "last_seen": "2026-06-20T18:00:00+00:00"},
        ]
        client = self._app_and_client(species_lists={24: species1})
        resp1 = client.get("/api/eink", headers={"If-None-Match": '"wrong-etag"'})
        assert resp1.status_code == 200

    def test_clamps_resolution(self):
        species = []
        client = self._app_and_client(species_lists={24: species})
        resp = client.get("/api/eink?w=50&h=99999")
        assert resp.status_code == 200

    def test_custom_params(self):
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 5,
             "best_conf": 0.9, "last_seen": "2026-06-20T18:00:00+00:00"},
        ]
        client = self._app_and_client(species_lists={1: species})
        resp = client.get("/api/eink?w=800&h=600&hours=1")
        assert resp.status_code == 200
        assert resp.data[:4] == b'\x89PNG'

    def test_handles_client_error(self):
        mock = make_mock_client(raises=Exception("API down"))
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                # Use custom params so no stale cache from other tests
                resp = client.get("/api/eink?w=333&h=222&hours=1")
                assert resp.status_code == 503