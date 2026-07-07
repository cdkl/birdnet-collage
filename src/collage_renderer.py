"""Server-side collage renderer — Python port of apt.js mask-packing algorithm.

Produces PNG images matching the web frontend's collage layout.
Validated against JS reference outputs in tests/fixtures/.
"""

import base64
import hashlib
import io
import logging
import math
import os
from typing import Any, Optional, Dict

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

ILLUSTRATIONS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "assets", "illustrations"
)

# Tunables — must match apt.js exactly
GRID_STRIDE = 4
COLLAGE_PAD = 3

# Park-Miller PRNG seed (golden ratio)
_PRNG_SEED = 0x9E3779B9
_PRNG_MOD = 2147483647
_PRNG_MUL = 16807


class _ParkMillerPRNG:
    """Seeded Park-Miller LCG — matches JS: seed = (seed * 16807) % 2147483647"""

    def __init__(self):
        self.seed = _PRNG_SEED

    def reset(self):
        self.seed = _PRNG_SEED

    def random(self) -> float:
        self.seed = (self.seed * _PRNG_MUL) % _PRNG_MOD
        return self.seed / _PRNG_MOD


def slugify(sci_name: str) -> str:
    """Normalize a scientific name to a file/slug string — matches apt.js."""
    s = sci_name.lower()
    parts = []
    for ch in s:
        if ch.isalnum():
            parts.append(ch)
        elif parts and parts[-1] != "-":
            parts.append("-")
    slug = "".join(parts)
    return slug.strip("-")


# Mask cache: slug -> dict with w, h, cells
_mask_cache: Dict[str, Dict[str, Any]] = {}


def _decode_mask(mask_rec: dict) -> Optional[dict[str, Any]]:
    """Decode a base64-encoded binary mask into a sparse cell list.

    Matches apt.js loadMask(): atob + MSB-first bit unpacking.
    """
    slug = mask_rec.get("_slug", "")
    if slug in _mask_cache:
        return _mask_cache[slug]

    bits_b64 = mask_rec.get("bits", "")
    w = mask_rec["w"]
    h = mask_rec["h"]

    raw_bytes = base64.b64decode(bits_b64)
    cells = []
    for y in range(h):
        for x in range(w):
            i = y * w + x
            byte_idx = i >> 3
            if byte_idx >= len(raw_bytes):
                continue
            b = raw_bytes[byte_idx]
            # MSB-first: bit position 7 - (i & 7)
            if (b >> (7 - (i & 7))) & 1:
                cells.append([x, y])

    result = {"w": w, "h": h, "cells": cells}
    _mask_cache[slug] = result
    return result


def _load_mask(slug: str, masks_data: dict) -> Optional[dict[str, Any]]:
    """Load and decode a mask by slug."""
    if slug in _mask_cache:
        return _mask_cache[slug]
    rec = masks_data.get(slug)
    if not rec:
        return None
    rec["_slug"] = slug
    return _decode_mask(rec)


def _tuning(n: int) -> dict[str, float]:
    """Tuning parameters based on species count — matches apt.js tuning()."""
    if n <= 4:
        budget = 0.46
    elif n <= 12:
        budget = 0.40
    elif n <= 24:
        budget = 0.34
    else:
        budget = 0.28

    if n <= 8:
        min_area = 0.0100
    elif n <= 20:
        min_area = 0.0075
    else:
        min_area = 0.0055

    return {
        "packingBudgetFrac": budget,
        "countExp": 0.65,
        "minTileAreaFrac": min_area,
        "ellipseAspectBias": 2.1,
    }


def _compute_tile_sizes(
    species: list[dict], w: int, h: int, dims: dict, masks_data: dict
) -> list:  # list of dict or None values
    """Build tiles with scores and dimensions — matches apt.js renderCollage sizing."""
    T = _tuning(len(species))
    vp_area = w * h
    budget = vp_area * T["packingBudgetFrac"]
    min_area = vp_area * T["minTileAreaFrac"]

    tiles = []
    for s in species:
        base = slugify(s["sci"])
        mask = _load_mask(base, masks_data)
        if not mask:
            continue
        d = dims.get(base, [1.4, 1.0])
        ar = d[0] / d[1] if d[1] else 1.4
        n_raw = s.get("n", 1)
        if not n_raw or n_raw == 0:
            n_raw = 1
        score = math.pow(max(1, n_raw), T["countExp"])
        tiles.append({
            "mask": mask,
            "data": s,
            "slug": base,
            "ar": ar,
            "score": score,
        })

    if not tiles:
        return []

    sum_score = sum(t["score"] for t in tiles) or 1
    for t in tiles:
        t["area"] = max(min_area, budget * t["score"] / sum_score)

    # If flooring pushed us over budget, shrink over-budget tiles
    sum_a = sum(t["area"] for t in tiles)
    if sum_a > budget:
        fixed_sum = sum(t["area"] for t in tiles if t["area"] <= min_area + 1e-9)
        flex_sum = sum_a - fixed_sum
        flex_budget = max(0, budget - fixed_sum)
        shrink = min(1, flex_budget / flex_sum) if flex_sum > 0 else 1
        for t in tiles:
            if t["area"] > min_area + 1e-9:
                t["area"] *= shrink

    for t in tiles:
        t["fullW"] = math.sqrt(t["area"] * t["ar"])
        t["fullH"] = t["fullW"] / t["ar"]

    return tiles


def _mask_pack(
    tiles: list[dict], W: int, H: int, xBias: float, yBias: float, pad: int, prng: _ParkMillerPRNG
) -> list[dict]:
    """Spiral mask packing — matches apt.js maskPack()."""
    GW = math.ceil(W / GRID_STRIDE) + 2
    GH = math.ceil(H / GRID_STRIDE) + 2
    grid = [0] * (GW * GH)

    def _cell_range(tile, tx, ty, c):
        sx = tile["fullW"] / tile["mask"]["w"]
        sy = tile["fullH"] / tile["mask"]["h"]
        x0 = int((tx + c[0] * sx) / GRID_STRIDE)
        y0 = int((ty + c[1] * sy) / GRID_STRIDE)
        x1 = int((tx + (c[0] + 1) * sx) / GRID_STRIDE)
        y1 = int((ty + (c[1] + 1) * sy) / GRID_STRIDE)
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(GW - 1, x1)
        y1 = min(GH - 1, y1)
        return (x0, y0, x1, y1)

    def _collides(tile, tx, ty):
        cells = tile["mask"]["cells"]
        for c in cells:
            x0, y0, x1, y1 = _cell_range(tile, tx, ty, c)
            for gy in range(y0, y1 + 1):
                off = gy * GW
                for gx in range(x0, x1 + 1):
                    if grid[off + gx]:
                        return True
        return False

    def _stamp(tile, tx, ty):
        cells = tile["mask"]["cells"]
        for c in cells:
            x0, y0, x1, y1 = _cell_range(tile, tx, ty, c)
            gy0 = max(0, y0 - pad)
            gy1 = min(GH - 1, y1 + pad)
            gx0 = max(0, x0 - pad)
            gx1 = min(GW - 1, x1 + pad)
            for gy in range(gy0, gy1 + 1):
                off = gy * GW
                for gx in range(gx0, gx1 + 1):
                    grid[off + gx] = 1

    def _off_grid(tile, tx, ty):
        return tx < 0 or ty < 0 or tx + tile["fullW"] > W or ty + tile["fullH"] > H

    cx = W / 2
    cy = H / 2

    # Sort largest first
    tiles.sort(key=lambda t: -(t["fullW"] * t["fullH"]))

    placed = []

    for i, t in enumerate(tiles):
        if i == 0:
            t["x"] = cx - t["fullW"] / 2
            t["y"] = cy - t["fullH"] / 2
            _stamp(t, t["x"], t["y"])
            placed.append(t)
            continue

        # Center of mass of placed tiles (area-weighted)
        com_x = 0.0
        com_y = 0.0
        com_w = 0.0
        for p in placed:
            a = p["fullW"] * p["fullH"]
            com_x += (p["x"] + p["fullW"] / 2) * a
            com_y += (p["y"] + p["fullH"] / 2) * a
            com_w += a
        if com_w:
            com_x /= com_w
            com_y /= com_w

        best = None
        best_cost = float("inf")
        step = max(GRID_STRIDE, min(t["fullW"], t["fullH"]) * 0.05)
        max_r = max(W, H)
        found_ring = -1
        phase = prng.random() * math.pi * 2

        r = 0.0
        while r <= max_r:
            if found_ring >= 0 and r > found_ring + step * 2:
                break
            samples = max(36, int(r / 1.6))
            for k in range(samples):
                theta = phase + (k / samples) * math.pi * 2
                px = cx + r * xBias * math.cos(theta) - t["fullW"] / 2
                py = cy + r * yBias * math.sin(theta) - t["fullH"] / 2
                if _off_grid(t, px, py):
                    continue
                if _collides(t, px, py):
                    continue
                dxx = px + t["fullW"] / 2 - com_x
                dyy = py + t["fullH"] / 2 - com_y
                cost = math.hypot(dxx / xBias, dyy / yBias) + prng.random() * step * 0.5
                if cost < best_cost:
                    best_cost = cost
                    best = (px, py)
            if best and found_ring < 0:
                found_ring = r
            r += step

        if best:
            t["x"] = best[0]
            t["y"] = best[1]
            _stamp(t, t["x"], t["y"])
            placed.append(t)
        else:
            t["x"] = -99999
            t["y"] = -99999
            placed.append(t)

    return placed


def _scale_to_fit(
    tiles: list[dict], W: int, H: int, xBias: float, yBias: float, pad: int, prng: _ParkMillerPRNG
) -> list[dict]:
    """Iterative scale-to-fit loop — matches apt.js 10-iteration shrink loop."""

    def _bounds(arr):
        L = float("inf")
        R = float("-inf")
        T = float("inf")
        B = float("-inf")
        for t in arr:
            if t.get("x", 0) < -1000:
                continue
            tx = t["x"]
            ty = t["y"]
            if tx < L:
                L = tx
            if tx + t["fullW"] > R:
                R = tx + t["fullW"]
            if ty < T:
                T = ty
            if ty + t["fullH"] > B:
                B = ty + t["fullH"]
        return L, R, T, B

    placed = _mask_pack(tiles, W, H, xBias, yBias, pad, prng)
    L, R, T, B = _bounds(placed)

    for _ in range(10):
        missing = any(t.get("x", 0) < -1000 for t in placed)
        overflow = L < 0 or T < 0 or R > W or B > H
        if not missing and not overflow:
            break

        scale = 0.93
        if overflow:
            clW = R - L
            clH = B - T
            sx = (W * 0.96) / max(clW, W * 0.96)
            sy = (H * 0.94) / max(clH, H * 0.94)
            scale = min(scale, sx, sy)

        for t in tiles:
            t["fullW"] *= scale
            t["fullH"] *= scale

        placed = _mask_pack(tiles, W, H, xBias, yBias, pad, prng)
        L, R, T, B = _bounds(placed)

    # Re-centre cluster in viewport
    cx = (L + R) / 2
    cy = (T + B) / 2
    dx = W / 2 - cx
    dy = H / 2 - cy
    if abs(dx) > 1 or abs(dy) > 1:
        for t in placed:
            if t.get("x", 0) > -1000:
                t["x"] += dx
                t["y"] += dy

    return placed


def render_collage(
    species: list[dict],
    width: int,
    height: int,
    title: str = "",
    dims: Optional[dict] = None,
    masks_data: Optional[dict] = None,
    prng: Optional[_ParkMillerPRNG] = None,
) -> bytes:
    """Render a bird collage as PNG bytes.

    Args:
        species: List of dicts with keys 'sci', 'com', 'n' (detection count).
        width: Output image width in pixels.
        height: Output image height in pixels.
        title: Optional title text rendered at top.
        dims: DIMS dict (injected for testability).
        masks_data: MASKS dict (injected for testability).
        prng: PRNG instance (injected for testability/determinism).

    Returns:
        PNG image bytes.
    """
    if dims is None:
        from .collage_data import DIMS as _DIMS
        dims = _DIMS
    if masks_data is None:
        from .collage_data import MASKS as _MASKS
        masks_data = _MASKS
    if prng is None:
        prng = _ParkMillerPRNG()
    else:
        prng.reset()

    _mask_cache.clear()

    # --- Compute tile sizes ---
    tiles = _compute_tile_sizes(species, width, height, dims, masks_data)
    if not tiles:
        # Empty collage: return blank image with header
        img = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        _draw_title(draw, title, width)
        buf = _img_to_bytes(img)
        return buf

    # --- Run spiral packing ---
    narrow = width <= 700
    xBias = 1.0 if narrow else 2.1
    yBias = 1.7 if narrow else 1.0
    pad = max(1, COLLAGE_PAD - 1) if narrow else COLLAGE_PAD

    placed = _scale_to_fit(tiles, width, height, xBias, yBias, pad, prng)

    # --- Composite onto canvas ---
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    title_height = 0
    if title:
        title_height = _draw_title(draw, title, width)

    # Vertically centre the title + collage block, with a gap matching the
    # internal font gap between site name and heading.
    if title_height > 0 and placed:
        heading_size = min(max(24, int(round(width * 0.040))), 72)
        font_gap = max(4, int(round(heading_size * 0.25)))
        T = float("inf")
        B = float("-inf")
        for t in placed:
            if t.get("x", 0) < -1000:
                continue
            if t["y"] < T:
                T = t["y"]
            if t["y"] + t["fullH"] > B:
                B = t["y"] + t["fullH"]
        total_h = title_height + font_gap + (B - T)
        top_offset = (height - total_h) / 2
        shift_y = top_offset - T
        if B + shift_y > height - 15:
            shift_y = max(0, height - B - 15)
        if shift_y != 0:
            for t in placed:
                if t.get("x", 0) > -1000:
                    t["y"] += shift_y

    # Paste each tile's illustration (PNGs already have alpha transparency)
    for t in placed:
        tx = t.get("x", -99999)
        if tx < -1000:
            continue
        ty = t.get("y", 0)
        slug = t["slug"]

        # Load illustration
        ill_path = os.path.join(ILLUSTRATIONS_DIR, f"{slug}.png")
        if not os.path.isfile(ill_path):
            # Try flight variant, then skip
            ill_path2 = os.path.join(ILLUSTRATIONS_DIR, f"{slug}-2.png")
            if os.path.isfile(ill_path2):
                ill_path = ill_path2
            else:
                log.debug("Missing illustration: %s", slug)
                continue

        try:
            bird_img = Image.open(ill_path).convert("RGBA")
        except Exception:
            log.warning("Cannot open illustration: %s", ill_path)
            continue

        # Scale to tile dimensions
        fw = t["fullW"]
        fh = t["fullH"]
        bird_resized = bird_img.resize(
            (max(1, int(round(fw))), max(1, int(round(fh)))),
            Image.LANCZOS,
        )

        # Composite onto canvas using the illustration's own alpha channel
        canvas_x = max(0, int(round(tx)))
        canvas_y = max(0, int(round(ty)))
        img.paste(bird_resized, (canvas_x, canvas_y), bird_resized)

    buf = _img_to_bytes(img)
    return buf


_FONT_CACHE: dict = {}


def _load_font(style: str, size: int) -> ImageFont:
    """Load a serif font with the given style (Bold, Italic, ''), caching.

    Tries Georgia (macOS) then Liberation Serif (Docker/Linux), then
    DejaVu Serif (fallback). Liberation Serif is metrically compatible with Georgia.
    """
    key = (style, size)
    cached = _FONT_CACHE.get(key)
    if cached:
        return cached

    font_candidates = []

    # Georgia (macOS Supplemental, modern macOS paths)
    if style == "BoldItalic":
        font_candidates.append(
            ("Georgia", "/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf")
        )
    elif style:
        font_candidates.append(
            ("Georgia", f"/System/Library/Fonts/Supplemental/Georgia {style}.ttf")
        )
    else:
        font_candidates.append(
            ("Georgia", "/System/Library/Fonts/Supplemental/Georgia.ttf")
        )

    # Liberation Serif (Docker/Linux — metrically equivalent to Georgia)
    lib_suffix = f"-{style}" if style else ""
    font_candidates.append(
        ("Liberation Serif", f"/usr/share/fonts/truetype/liberation2/LiberationSerif{lib_suffix}.ttf")
    )
    font_candidates.append(
        ("Liberation Serif", f"/usr/share/fonts/truetype/liberation/LiberationSerif{lib_suffix}.ttf")
    )

    # DejaVu Serif (fallback)
    dv_suffix = f"-{style}" if style else ""
    font_candidates.append(
        ("DejaVu Serif", f"/usr/share/fonts/truetype/dejavu/DejaVuSerif{dv_suffix}.ttf")
    )

    font = None
    for name, path in font_candidates:
        try:
            font = ImageFont.truetype(path, size)
            log.debug("Loaded font: %s from %s", name, path)
            break
        except (OSError, IOError):
            continue

    if font is None:
        font = ImageFont.load_default()
        log.warning("No serif font found, using default bitmap font")

    _FONT_CACHE[key] = font
    return font


def _draw_text_spaced(draw, xy, text, font, fill, letter_spacing):
    """Draw text with per-character letter-spacing (px)."""
    x, y = xy
    for ch in text:
        bbox = draw.textbbox((0, 0), ch, font=font)
        cw = bbox[2] - bbox[0]
        draw.text((x - bbox[0], y), ch, fill=fill, font=font)
        x += cw + letter_spacing


def _draw_title(draw, title: str, width: int):
    """Draw two-line header: site name (small, italic, serif) above
    'HEARD RECENTLY' (large, bold, serif, uppercase, letter-spaced).

    Font sizes are width-relative, matching CSS clamp() expressions.
    """
    # Width-relative sizes — ~50% larger than before, matching the desired
    # visual proportion relative to the collage area.
    heading_size = min(max(24, int(round(width * 0.040))), 72)
    site_size = min(max(14, int(round(width * 0.022))), 28)

    heading_font = _load_font("Bold", heading_size)
    site_font = _load_font("Italic", site_size)

    heading_text = "HEARD RECENTLY"
    heading_color = (30, 30, 30)
    site_color = (100, 100, 100)

    # Site name (no letter spacing)
    site_bbox = draw.textbbox((0, 0), title, font=site_font)
    site_w = site_bbox[2] - site_bbox[0]
    site_h = site_bbox[3] - site_bbox[1]
    site_x = (width - site_w) / 2

    # Heading with letter-spacing (~0.06em)
    ls = max(1, int(round(heading_size * 0.06)))
    total_w = 0
    char_data = []
    for ch in heading_text:
        b = draw.textbbox((0, 0), ch, font=heading_font)
        cw = b[2] - b[0]
        char_data.append((ch, b, cw))
        total_w += cw + ls
    total_w -= ls

    heading_x = (width - total_w) / 2
    heading_h = char_data[0][1][3] - char_data[0][1][1] if char_data else 0

    # Vertical layout with padding
    gap = max(4, int(round(heading_size * 0.25)))
    pad_top = max(8, int(round(site_size * 0.8)))
    pad_bot = pad_top

    site_y = pad_top
    heading_y = pad_top + site_h + gap

    draw.text((site_x, site_y), title, fill=site_color, font=site_font)
    _draw_text_spaced(
        draw, (heading_x, heading_y), heading_text, heading_font,
        heading_color, ls,
    )

    return pad_top + site_h + gap + heading_h + pad_bot


def _img_to_bytes(img) -> bytes:
    """Save PIL Image to PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def compute_etag(species: list[dict], hours: int, img_version: str = "r12") -> str:
    """Compute a content-hash ETag from species data + params.

    Matches: sorted species (sci + n + last_seen) + hours + img_version
    """
    h = hashlib.sha256()
    sorted_species = sorted(species, key=lambda s: s.get("sci", ""))
    for s in sorted_species:
        h.update(s.get("sci", "").encode())
        h.update(str(s.get("n", 0)).encode())
        h.update(s.get("last_seen", "").encode())
    h.update(str(hours).encode())
    h.update(img_version.encode())
    return h.hexdigest()