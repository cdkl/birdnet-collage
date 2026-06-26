"""End-to-end smoke tests.

Catches syntax/import errors, validates the app starts, serves pages,
and templates render correctly. No network calls to Birdnet-GO.
"""

import os
import pytest
from unittest.mock import Mock, patch

from src.config import Config
from src.app import create_app, slugify, _count_illustrations


@pytest.fixture
def app():
    config = Config()
    config.BIRDNET_GO_URL = "http://mock:8080"
    config.SITE_TITLE = "test title"
    mock = Mock()
    mock.get_recent_species.return_value = []
    mock.health_check.return_value = True
    with patch("src.app.BirdnetGoClient", return_value=mock):
        app = create_app(config=config)
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


class TestModuleImports:
    """The most basic test: can we import everything?"""

    def test_config_imports(self):
        assert hasattr(Config, "BIRDNET_GO_URL")
        assert hasattr(Config, "SITE_TITLE")

    def test_app_imports(self):
        from src.app import create_app, slugify, _count_illustrations
        assert callable(create_app)
        assert callable(slugify)
        assert callable(_count_illustrations)

    def test_birdnet_client_imports(self):
        from src.birdnet_client import BirdnetGoClient
        assert callable(BirdnetGoClient)
        client = BirdnetGoClient("http://test:8080")
        assert client.base_url == "http://test:8080"


class TestAppStartup:
    """Can the app start and serve basic pages?"""

    def test_index_serves_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/html")

    def test_static_js_served(self, client):
        resp = client.get("/apt.js")
        assert resp.status_code == 200
        assert len(resp.data) > 10000

    def test_static_css_served(self, client):
        resp = client.get("/styles.css")
        assert resp.status_code == 200
        assert len(resp.data) > 1000

    def test_favicon_served(self, client):
        resp = client.get("/favicon.png")
        assert resp.status_code == 200

    def test_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["birdnet_go"]["reachable"] is True
        assert isinstance(data["illustrations"], int)

    def test_recent_endpoint_format(self, client):
        resp = client.get("/api/recent?hours=1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "species" in data
        assert data["hours"] == 1

    def test_lifelist_endpoint_format(self, client):
        resp = client.get("/api/lifelist")
        assert resp.status_code == 200
        assert "species" in resp.get_json()

    def test_stats_endpoint_format(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "totals" in data
        assert "today" in data
        assert "last_hour" in data
        assert "as_of" in data

    def test_species_endpoint_missing_param(self, client):
        resp = client.get("/api/species")
        assert resp.status_code == 400

    def test_diagnostics_endpoint(self, client):
        resp = client.get("/api/diagnostics")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "birdnet_go" in data
        assert "illustrations" in data
        assert "recent_requests" in data
        assert "recent_errors" in data


class TestTemplateRendering:
    """Does the Jinja template render correctly with config values?"""

    def test_site_title_in_page(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "<title>test title</title>" in html
        assert 'id="aboutLink">test title<' in html

    def test_default_title(self):
        config = Config()
        config.BIRDNET_GO_URL = "http://mock:8080"
        mock = Mock()
        mock.get_recent_species.return_value = []
        mock.health_check.return_value = True
        with patch("src.app.BirdnetGoClient", return_value=mock):
            app = create_app(config=config)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.get("/")
                html = resp.data.decode("utf-8")
                assert "<title>birdnet collage</title>" in html

    def test_static_files_passthrough(self, client):
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert 'src="./apt.js"' in html
        assert 'href="./styles.css"' in html
        assert 'href="./favicon.png"' in html


class TestIllustrationsDirectory:
    """Can we read the illustrations directory?"""

    def test_illustrations_count(self):
        count = _count_illustrations()
        assert count >= 498, f"Expected at least 498 illustrations, got {count}"

    def test_illustrations_dir_exists(self):
        from src.app import ILLUSTRATIONS_DIR
        assert os.path.isdir(ILLUSTRATIONS_DIR)

    def test_specific_illustrations_exist(self):
        from src.app import ILLUSTRATIONS_DIR
        for name in ["bombycilla-cedrorum.png", "turdus-migratorius.png",
                      "bombycilla-cedrorum-2.png", "passerina-cyanea.png"]:
            assert os.path.isfile(os.path.join(ILLUSTRATIONS_DIR, name)), \
                f"Missing illustration: {name}"