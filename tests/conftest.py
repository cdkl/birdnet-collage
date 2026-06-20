import pytest
from datetime import datetime, timedelta, timezone


@pytest.fixture
def now():
    return datetime(2026, 6, 20, 18, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_detections(now):
    """Create sample detection records matching Birdnet-GO's API format."""
    return [
        {
            "id": 1,
            "date": "2026-06-20",
            "time": "17:55:00",
            "timestamp": (now - timedelta(minutes=5)).isoformat(),
            "scientificName": "Bombycilla cedrorum",
            "commonName": "Cedar Waxwing",
            "confidence": 0.92,
        },
        {
            "id": 2,
            "date": "2026-06-20",
            "time": "17:50:00",
            "timestamp": (now - timedelta(minutes=10)).isoformat(),
            "scientificName": "Turdus migratorius",
            "commonName": "American Robin",
            "confidence": 0.85,
        },
        {
            "id": 3,
            "date": "2026-06-20",
            "time": "17:48:00",
            "timestamp": (now - timedelta(minutes=12)).isoformat(),
            "scientificName": "Bombycilla cedrorum",
            "commonName": "Cedar Waxwing",
            "confidence": 0.78,
        },
        {
            "id": 4,
            "date": "2026-06-20",
            "time": "17:30:00",
            "timestamp": (now - timedelta(minutes=30)).isoformat(),
            "scientificName": "Turdus migratorius",
            "commonName": "American Robin",
            "confidence": 0.91,
        },
        {
            "id": 5,
            "date": "2026-06-20",
            "time": "16:00:00",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "scientificName": "Passerina cyanea",
            "commonName": "Indigo Bunting",
            "confidence": 0.65,
        },
        {
            "id": 6,
            "date": "2026-06-19",
            "time": "18:00:00",
            "timestamp": (now - timedelta(hours=24)).isoformat(),
            "scientificName": "Corvus brachyrhynchos",
            "commonName": "American Crow",
            "confidence": 0.74,
        },
    ]


@pytest.fixture
def api_response(sample_detections):
    """Mock Birdnet-GO API response format."""
    return {"data": sample_detections, "total": len(sample_detections), "limit": 1000, "offset": 0}


@pytest.fixture
def multi_page_detections(now):
    """Create 1500 detections spanning 3 pages to test pagination."""
    detections = []
    for i in range(1500):
        minutes_ago = i * 2  # each detection 2 minutes apart
        detections.append({
            "id": i + 1,
            "date": (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d"),
            "time": (now - timedelta(minutes=minutes_ago)).strftime("%H:%M:%S"),
            "timestamp": (now - timedelta(minutes=minutes_ago)).isoformat(),
            "scientificName": f"Species_{i % 10}",
            "commonName": f"Common Name {i % 10}",
            "confidence": 0.5 + (i % 50) / 100,
        })
    return detections