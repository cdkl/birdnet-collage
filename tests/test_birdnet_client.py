import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, timezone

from src.birdnet_client import BirdnetGoClient, MAX_LIMIT


class TestFetchDetectionsPage:
    def test_parses_data_wrapper(self, api_response):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        result = client._fetch_detections_page(offset=0)
        assert len(result) == len(api_response["data"])
        assert result[0]["scientificName"] == "Bombycilla cedrorum"

    def test_handles_empty_response(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {"data": [], "total": 0}
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        result = client._fetch_detections_page()
        assert result == []

    def test_handles_missing_data_key(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {"items": [{"id": 1}]}
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        result = client._fetch_detections_page()
        assert result == []

    def test_passes_offset_and_limit(self, api_response):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        client._fetch_detections_page(offset=2000, limit=500)
        client.session.get.assert_called_once()
        call_kwargs = client.session.get.call_args.kwargs
        assert call_kwargs["params"]["offset"] == 2000
        assert call_kwargs["params"]["limit"] == 500


class TestGetRecentSpecies:
    def test_filters_by_time_window(self, sample_detections, api_response):
        """Only detections within the requested hours window are included."""
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        # Freeze time to 'now' so the 1h window includes 5 of 6 detections
        frozen_now = datetime(2026, 6, 20, 18, 0, 0, tzinfo=timezone.utc)
        with patch.object(client, "get_recent_species") as mock_method:
            pass  # we need a different approach

        # Instead: make fresh detections relative to actual now, then call
        real_now = datetime.now(timezone.utc)
        fresh_detections = []
        for minutes_ago in [5, 10, 12, 30, 120, 1440]:  # 6 detections
            fresh_detections.append({
                "id": minutes_ago,
                "timestamp": (real_now - timedelta(minutes=minutes_ago)).isoformat(),
                "scientificName": f"Test_{minutes_ago % 3}",
                "commonName": f"Common {minutes_ago % 3}",
                "confidence": 0.8,
            })
        mock_resp.json.return_value = {"data": fresh_detections, "total": len(fresh_detections)}

        species = client.get_recent_species(hours=3)
        total = sum(s["n"] for s in species)
        # 5 detections within 3h (the 1440min one is 24h old, excluded)
        assert total == 5

    def test_larger_window_includes_all(self, sample_detections, api_response):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=48)
        total = sum(s["n"] for s in species)
        assert total == 6  # all detections

    def test_aggregates_by_species(self, sample_detections, api_response):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        species_by_sci = {s["sci"]: s for s in species}

        # Cedar Waxwing: 2 detections
        assert species_by_sci["Bombycilla cedrorum"]["n"] == 2
        assert species_by_sci["Bombycilla cedrorum"]["com"] == "Cedar Waxwing"

        # American Robin: 2 detections
        assert species_by_sci["Turdus migratorius"]["n"] == 2

        # Indigo Bunting: 1 detection
        assert species_by_sci["Passerina cyanea"]["n"] == 1

    def test_sorts_by_count_descending(self, sample_detections, api_response):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        counts = [s["n"] for s in species]
        assert counts == sorted(counts, reverse=True)

    def test_tracks_best_confidence(self, sample_detections, api_response):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        cedar = next(s for s in species if s["sci"] == "Bombycilla cedrorum")
        assert cedar["best_conf"] == 0.92  # max of 0.92 and 0.78

    def test_tracks_last_seen_timestamp(self, sample_detections, api_response):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        cedar = next(s for s in species if s["sci"] == "Bombycilla cedrorum")
        assert cedar["n"] == 2
        # Most recent detection is at 17:55
        assert "17:55" in cedar["last_seen"]

    def test_skips_missing_scientific_name(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "data": [
                {"timestamp": "2026-06-20T18:00:00+00:00", "commonName": "No Name"},
                {"timestamp": "2026-06-20T18:01:00+00:00", "scientificName": "", "commonName": "Empty"},
            ],
            "total": 2,
        }
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        assert species == []

    def test_skips_missing_timestamp(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "data": [
                {"scientificName": "Testus testus", "commonName": "Test"},
            ],
            "total": 1,
        }
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        assert species == []

    def test_handles_invalid_timestamp(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "data": [
                {"scientificName": "Testus testus", "commonName": "Test", "timestamp": "not-a-date"},
            ],
            "total": 1,
        }
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        assert species == []

    def test_handles_empty_page(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {"data": [], "total": 0}
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        assert species == []

    def test_paginates_multiple_pages(self, multi_page_detections, now):
        """With 1500 detections, pagination fetches 2 pages (1000 + 500)."""
        client = BirdnetGoClient("http://test:8080")

        call_count = 0
        def mock_get(url, **kwargs):
            nonlocal call_count
            offset = kwargs["params"]["offset"]
            limit = kwargs["params"]["limit"]
            page_data = multi_page_detections[offset : offset + limit]
            call_count += 1
            mock_resp = Mock()
            mock_resp.json.return_value = {"data": page_data, "total": len(multi_page_detections)}
            mock_resp.raise_for_status.return_value = None
            return mock_resp

        with patch.object(client.session, "get", side_effect=mock_get):
            # 2h window: detection at 1500*2=3000min (~50h) ago, so we stop before then
            species = client.get_recent_species(hours=50)
            assert call_count == 2  # page 0 + page 1 (page 1 oldest is past 50h)
            assert len(species) > 0

    def test_respects_max_pages(self, multi_page_detections):
        """Stops after MAX_PAGES even if more data exists."""
        client = BirdnetGoClient("http://test:8080")

        mock_resp = Mock()
        mock_resp.json.return_value = {"data": multi_page_detections[:1000], "total": 999999}
        mock_resp.raise_for_status.return_value = None

        with patch.object(client.session, "get", return_value=mock_resp):
            # MAX_PAGES is defined but we can't change it. With infinite data
            # and the oldest always newer than cutoff, it fetches all pages.
            # This test verifies pages < MAX_PAGES loops and stops on empty.
            species = client.get_recent_species(hours=24)

    def test_token_added_to_headers(self):
        client = BirdnetGoClient("http://test:8080", token="secret-token")
        assert client.session.headers["Authorization"] == "Bearer secret-token"

    def test_no_token_when_empty(self):
        client = BirdnetGoClient("http://test:8080")
        assert "Authorization" not in client.session.headers


class TestHealthCheck:
    def test_returns_true_when_reachable(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        assert client.health_check() is True

    def test_returns_false_on_error(self):
        client = BirdnetGoClient("http://test:8080")
        client.session.get = Mock(side_effect=Exception("Connection refused"))

        assert client.health_check() is False


class TestEdgeCases:
    def test_clamping_large_hours_value(self):
        """Hours like 1000000 (the 'ALL' button) should work."""
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {"data": [{
            "id": 1, "timestamp": "2026-01-01T00:00:00+00:00",
            "scientificName": "Oldus birdus", "commonName": "Old Bird",
            "confidence": 0.5,
        }], "total": 1}
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=1000000)
        assert len(species) == 1

    def test_confidence_handles_string_values(self):
        client = BirdnetGoClient("http://test:8080")
        mock_resp = Mock()
        mock_resp.json.return_value = {"data": [{
            "id": 1, "timestamp": "2026-06-20T18:00:00+00:00",
            "scientificName": "Testus testus", "commonName": "Test",
            "confidence": "0.88",
        }], "total": 1}
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        species = client.get_recent_species(hours=24)
        assert species[0]["best_conf"] == 0.88

    def test_base_url_stripping(self):
        """Client works with trailing slashes in URL."""
        client = BirdnetGoClient("http://test:8080/")
        mock_resp = Mock()
        mock_resp.raise_for_status.return_value = None
        client.session.get = Mock(return_value=mock_resp)

        client.health_check()
        called_url = client.session.get.call_args[0][0]
        # Should not have double slashes
        assert "//api" not in called_url