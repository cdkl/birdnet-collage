"""Tests for src/collage_renderer.py — Python port of apt.js mask-packing algorithm."""

import json
import math
import os
from pathlib import Path

import pytest

from src.collage_data import DIMS, MASKS
from src.collage_renderer import (
    _ParkMillerPRNG,
    _decode_mask,
    _load_mask,
    _tuning,
    _compute_tile_sizes,
    _mask_pack,
    _scale_to_fit,
    render_collage,
    compute_etag,
    slugify,
    _mask_cache,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ============================================================
# Step 2: PRNG determinism
# ============================================================

class TestPRNG:
    def test_first_outputs(self):
        """First 20 outputs match JS reference values."""
        rng = _ParkMillerPRNG()
        expected = [
            0.5945036214,
            0.8223645663,
            0.4812657924,
            0.6341725577,
            0.538176975,
            0.1404187903,
            0.0186093487,
            0.7673234869,
            0.4058437275,
            0.0155277499,
            0.974892687,
            0.0213910947,
            0.5201281558,
            0.7939151194,
            0.3314116184,
            0.0350709199,
            0.4369509902,
            0.8352923309,
            0.7582048707,
            0.1492626062,
        ]
        for i, exp in enumerate(expected):
            assert abs(rng.random() - exp) < 1e-8, f"Mismatch at index {i}: got {rng.random()} expected {exp}"

    def test_reset(self):
        """reset() restores deterministic sequence."""
        rng = _ParkMillerPRNG()
        v1 = rng.random()
        v2 = rng.random()
        rng.reset()
        assert abs(rng.random() - v1) < 1e-10
        assert abs(rng.random() - v2) < 1e-10

    def test_deterministic_across_instances(self):
        """Two fresh instances produce identical sequences."""
        a = _ParkMillerPRNG()
        b = _ParkMillerPRNG()
        for _ in range(100):
            assert abs(a.random() - b.random()) < 1e-10


# ============================================================
# Step 3: Mask decoding
# ============================================================

class TestMaskDecoder:
    def test_decode_known_slug(self):
        """Decode a real mask entry and verify cell count."""
        rec = MASKS["acanthis-flammea"]
        decoded = _decode_mask(rec)
        assert decoded is not None
        assert decoded["w"] == 93
        assert decoded["h"] == 66
        # Cell count should match JS output (verified independently)
        assert len(decoded["cells"]) > 0
        assert len(decoded["cells"]) < 93 * 66  # sparse

    def test_decode_cells_are_in_bounds(self):
        """All decoded cells are within w x h bounds."""
        for slug, rec in list(MASKS.items())[:10]:
            decoded = _decode_mask(rec)
            for cx, cy in decoded["cells"]:
                assert 0 <= cx < decoded["w"], f"{slug}: cell x={cx} out of bounds"
                assert 0 <= cy < decoded["h"], f"{slug}: cell y={cy} out of bounds"

    def test_decode_is_sparse(self):
        """Mask should have fewer cells than total area (sparse)."""
        slug = "acanthis-flammea"
        rec = MASKS[slug]
        decoded = _decode_mask(rec)
        total = decoded["w"] * decoded["h"]
        assert len(decoded["cells"]) < total
        assert len(decoded["cells"]) > total * 0.1  # at least 10% density

    def test_load_missing_slug_returns_none(self):
        """Unknown slug returns None."""
        assert _load_mask("nonexistent-slug", MASKS) is None


# ============================================================
# Step 4: Tuning
# ============================================================

class TestTuning:
    @pytest.mark.parametrize("n,exp_budget,exp_min_area", [
        (1, 0.46, 0.0100),
        (4, 0.46, 0.0100),
        (5, 0.40, 0.0100),
        (8, 0.40, 0.0100),
        (9, 0.40, 0.0075),
        (12, 0.40, 0.0075),
        (13, 0.34, 0.0075),
        (20, 0.34, 0.0075),
        (21, 0.34, 0.0055),
        (24, 0.34, 0.0055),
        (25, 0.28, 0.0055),
        (100, 0.28, 0.0055),
    ])
    def test_tuning_thresholds(self, n, exp_budget, exp_min_area):
        T = _tuning(n)
        assert T["packingBudgetFrac"] == exp_budget
        assert T["minTileAreaFrac"] == exp_min_area
        assert T["countExp"] == 0.65
        assert T["ellipseAspectBias"] == 2.1


# ============================================================
# Step 4b: Tile sizing
# ============================================================

class TestTileSizing:
    def test_compute_tile_sizes_basic(self):
        """Basic tile sizing produces expected count and structure."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 100},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 50},
        ]
        tiles = _compute_tile_sizes(species, 800, 600, DIMS, MASKS)
        assert len(tiles) == 2
        for t in tiles:
            assert "fullW" in t
            assert "fullH" in t
            assert "area" in t
            assert t["fullW"] > 0
            assert t["fullH"] > 0

    def test_higher_count_gets_larger_tile(self):
        """The species with higher count gets a larger tile."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 200},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 10},
        ]
        tiles = _compute_tile_sizes(species, 800, 600, DIMS, MASKS)
        assert tiles[0]["area"] > tiles[1]["area"]

    def test_missing_mask_species_are_dropped(self):
        """Species without a mask are silently skipped."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 10},
            {"sci": "Picoides pubescens", "com": "Downy Woodpecker", "n": 5},
        ]
        tiles = _compute_tile_sizes(species, 800, 600, DIMS, MASKS)
        assert len(tiles) == 1
        assert tiles[0]["slug"] == "calypte-anna"


# ============================================================
# Step 5: Spiral packing — reference validation
# ============================================================

class TestMaskPack:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        _mask_cache.clear()

    def load_fixture(self, name):
        path = FIXTURES_DIR / f"{name}.json"
        with open(path) as f:
            return json.load(f)

    def positions_from_species(self, species, W, H):
        """Run the Python pipeline and return positioned tiles."""
        _mask_cache.clear()
        prng = _ParkMillerPRNG()
        tiles = _compute_tile_sizes(species, W, H, DIMS, MASKS)
        narrow = W <= 700
        xBias = 1.0 if narrow else 2.1
        yBias = 1.7 if narrow else 1.0
        pad = max(1, 2) if narrow else 3
        placed = _scale_to_fit(tiles, W, H, xBias, yBias, pad, prng)
        return placed

    def test_mask_pack_4spp(self):
        """4-species layout matches JS reference."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 398},
            {"sci": "Passer domesticus", "com": "House Sparrow", "n": 156},
            {"sci": "Haemorhous mexicanus", "com": "House Finch", "n": 142},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 98},
        ]
        ref = self.load_fixture("reference_layout_4spp_400x300")
        placed = self.positions_from_species(species, 400, 300)

        # Build lookup by slug
        py_by_slug = {t["slug"]: t for t in placed}
        for r in ref:
            py_tile = py_by_slug.get(r["slug"])
            assert py_tile is not None, f"Missing {r['slug']}"
            assert abs(py_tile["x"] - r["x"]) < 1.0, (
                f"{r['slug']} x: JS={r['x']} Python={py_tile['x']}"
            )
            assert abs(py_tile["y"] - r["y"]) < 1.0, (
                f"{r['slug']} y: JS={r['y']} Python={py_tile['y']}"
            )

    def test_mask_pack_12spp(self):
        """12-species layout matches JS reference."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 398},
            {"sci": "Passer domesticus", "com": "House Sparrow", "n": 156},
            {"sci": "Haemorhous mexicanus", "com": "House Finch", "n": 142},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 98},
            {"sci": "Zenaida macroura", "com": "Mourning Dove", "n": 87},
            {"sci": "Spinus psaltria", "com": "Lesser Goldfinch", "n": 76},
            {"sci": "Zonotrichia leucophrys", "com": "White-crowned Sparrow", "n": 65},
            {"sci": "Aphelocoma californica", "com": "California Scrub-Jay", "n": 54},
            {"sci": "Mimus polyglottos", "com": "Northern Mockingbird", "n": 43},
            {"sci": "Sayornis nigricans", "com": "Black Phoebe", "n": 38},
            {"sci": "Corvus brachyrhynchos", "com": "American Crow", "n": 31},
            {"sci": "Bombycilla cedrorum", "com": "Cedar Waxwing", "n": 29},
        ]
        ref = self.load_fixture("reference_layout_12spp_800x600")
        placed = self.positions_from_species(species, 800, 600)

        py_by_slug = {t["slug"]: t for t in placed}
        assert len(py_by_slug) == len(ref), (
            f"Count mismatch: JS={len(ref)} Python={len(py_by_slug)}"
        )
        for r in ref:
            py_tile = py_by_slug.get(r["slug"])
            assert py_tile is not None, f"Missing {r['slug']}"
            assert abs(py_tile["x"] - r["x"]) < 1.0, (
                f"{r['slug']} x: JS={r['x']} Python={py_tile['x']:.1f}"
            )
            assert abs(py_tile["y"] - r["y"]) < 1.0, (
                f"{r['slug']} y: JS={r['y']} Python={py_tile['y']:.1f}"
            )

    def test_mask_pack_24spp(self):
        """24-species layout matches JS reference (23 tiles, 1 missing mask)."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 398},
            {"sci": "Passer domesticus", "com": "House Sparrow", "n": 156},
            {"sci": "Haemorhous mexicanus", "com": "House Finch", "n": 142},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 98},
            {"sci": "Zenaida macroura", "com": "Mourning Dove", "n": 87},
            {"sci": "Spinus psaltria", "com": "Lesser Goldfinch", "n": 76},
            {"sci": "Zonotrichia leucophrys", "com": "White-crowned Sparrow", "n": 65},
            {"sci": "Aphelocoma californica", "com": "California Scrub-Jay", "n": 54},
            {"sci": "Mimus polyglottos", "com": "Northern Mockingbird", "n": 43},
            {"sci": "Sayornis nigricans", "com": "Black Phoebe", "n": 38},
            {"sci": "Corvus brachyrhynchos", "com": "American Crow", "n": 31},
            {"sci": "Bombycilla cedrorum", "com": "Cedar Waxwing", "n": 29},
            {"sci": "Pipilo maculatus", "com": "Spotted Towhee", "n": 27},
            {"sci": "Melospiza melodia", "com": "Song Sparrow", "n": 24},
            {"sci": "Junco hyemalis", "com": "Dark-eyed Junco", "n": 22},
            {"sci": "Setophaga coronata", "com": "Yellow-rumped Warbler", "n": 20},
            {"sci": "Sturnus vulgaris", "com": "European Starling", "n": 18},
            {"sci": "Columba livia", "com": "Rock Pigeon", "n": 16},
            {"sci": "Ardea herodias", "com": "Great Blue Heron", "n": 14},
            {"sci": "Buteo jamaicensis", "com": "Red-tailed Hawk", "n": 12},
            {"sci": "Megaceryle alcyon", "com": "Belted Kingfisher", "n": 10},
            {"sci": "Picoides pubescens", "com": "Downy Woodpecker", "n": 8},
            {"sci": "Sialia mexicana", "com": "Western Bluebird", "n": 6},
            {"sci": "Regulus calendula", "com": "Ruby-crowned Kinglet", "n": 4},
        ]
        ref = self.load_fixture("reference_layout_24spp_1600x1200")
        placed = self.positions_from_species(species, 1600, 1200)

        py_by_slug = {t["slug"]: t for t in placed}
        assert len(py_by_slug) == len(ref), (
            f"Count mismatch: JS={len(ref)} Python={len(py_by_slug)}"
        )
        for r in ref:
            py_tile = py_by_slug.get(r["slug"])
            assert py_tile is not None, f"Missing {r['slug']}"
            assert abs(py_tile["x"] - r["x"]) < 2.0, (
                f"{r['slug']} x: JS={r['x']} Python={py_tile['x']:.1f}"
            )
            assert abs(py_tile["y"] - r["y"]) < 2.0, (
                f"{r['slug']} y: JS={r['y']} Python={py_tile['y']:.1f}"
            )

    def test_deterministic_layout(self):
        """Same input produces identical layout every run."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 100},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 50},
        ]
        pos1 = [(t["x"], t["y"]) for t in self.positions_from_species(species, 600, 400)]
        pos2 = [(t["x"], t["y"]) for t in self.positions_from_species(species, 600, 400)]
        assert pos1 == pos2


# ============================================================
# Step 6: Integration — full render
# ============================================================

class TestRenderCollage:
    @pytest.fixture(autouse=True)
    def clear_cache(self):
        _mask_cache.clear()

    def test_returns_png_bytes(self):
        """render_collage returns valid PNG bytes."""
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 100},
            {"sci": "Turdus migratorius", "com": "American Robin", "n": 50},
        ]
        png = render_collage(species, 400, 300, title="Test")
        assert png[:4] == b'\x89PNG'

    def test_output_dimensions(self):
        """Output PNG matches requested dimensions."""
        from PIL import Image
        import io
        species = [
            {"sci": "Calypte anna", "com": "Anna's Hummingbird", "n": 100},
        ]
        png = render_collage(species, 500, 400, title="Test")
        img = Image.open(io.BytesIO(png))
        assert img.size == (500, 400)

    def test_empty_species(self):
        """Empty species list produces a blank image with title."""
        png = render_collage([], 400, 300, title="No Birds")
        assert png[:4] == b'\x89PNG'
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png))
        assert img.size == (400, 300)

    def test_etag_changes_with_data(self):
        """Different species produce different etags."""
        s1 = [{"sci": "Calypte anna", "n": 10, "last_seen": "2026-06-20T18:00:00"}]
        s2 = [{"sci": "Calypte anna", "n": 20, "last_seen": "2026-06-20T18:00:00"}]
        e1 = compute_etag(s1, 24)
        e2 = compute_etag(s2, 24)
        assert e1 != e2

    def test_etag_consistent(self):
        """Same data produces same etag."""
        s = [{"sci": "Calypte anna", "n": 10, "last_seen": "2026-06-20T18:00:00"}]
        assert compute_etag(s, 24) == compute_etag(s, 24)


# ============================================================
# Slugify
# ============================================================

class TestSlugify:
    def test_basic(self):
        assert slugify("Calypte anna") == "calypte-anna"

    def test_multiple_words(self):
        assert slugify("Bombycilla cedrorum") == "bombycilla-cedrorum"

    def test_special_chars(self):
        assert slugify("Zenaida macroura!") == "zenaida-macroura"

    def test_double_hyphens(self):
        assert slugify("a  b") == "a-b"

    def test_trailing_hyphens(self):
        assert slugify("-calypte anna-") == "calypte-anna"