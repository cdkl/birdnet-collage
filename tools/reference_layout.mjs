// tools/reference_layout.mjs
// Generates ground-truth tile positions from the JS mask-packing algorithm.
//
// Usage:
//   node tools/reference_layout.mjs < species.json > positions.json
//
// Where species.json is an array of {sci, com, n} objects.
// Outputs: [{slug, x, y, fullW, fullH, pose}] for each tile.
//
// DIMS/MASKS are read from frontend/apt.js so we use the exact same data
// as the browser.

import { readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const APT_JS = join(ROOT, "frontend", "apt.js");

// --- Load DIMS and MASKS from apt.js ---
const src = readFileSync(APT_JS, "utf-8");

function extractJSON(src, varName) {
  const m = src.match(new RegExp(`var\\s+${varName}\\s*=\\s*(\\{[\\s\\S]+?\\})\\s*;`));
  if (!m) throw new Error(`Could not find var ${varName}`);
  return JSON.parse(m[1]);
}

const DIMS = extractJSON(src, "DIMS");
const MASKS = extractJSON(src, "MASKS");

// --- atob polyfill for Node.js ---
function atob(b64) {
  return Buffer.from(b64, "base64").toString("binary");
}

// --- Ported functions from apt.js ---

function slugify(sci) {
  return sci.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

let maskCache = {};

function loadMask(slug) {
  if (maskCache[slug]) return maskCache[slug];
  const rec = MASKS[slug];
  if (!rec) return null;
  const bytes = atob(rec.bits);
  const w = rec.w, h = rec.h;
  const cells = [];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      const b = bytes.charCodeAt(i >> 3);
      if ((b >> (7 - (i & 7))) & 1) cells.push([x, y]);
    }
  }
  return (maskCache[slug] = { w, h, cells });
}

function tuning(n) {
  return {
    packingBudgetFrac: n <= 4  ? 0.46 :
                        n <= 12 ? 0.40 :
                        n <= 24 ? 0.34 :
                                  0.28,
    countExp: 0.65,
    minTileAreaFrac: n <= 8  ? 0.0100 :
                      n <= 20 ? 0.0075 :
                                0.0055,
    ellipseAspectBias: 2.1,
  };
}

const GRID_STRIDE = 4;
const COLLAGE_PAD = 3;
const FLY_PROB = 0.15;

function aspect(sci) {
  const d = DIMS[slugify(sci)];
  return d ? d[0] / d[1] : 1.4;
}

// Seeded Park-Miller PRNG
let seed = 0x9e3779b9;
function rand() {
  seed = (seed * 16807) % 2147483647;
  return seed / 2147483647;
}

// Spiral mask packing (identical to apt.js maskPack)
function maskPack(tiles, W, H, xBias, yBias, pad) {
  const GW = Math.ceil(W / GRID_STRIDE) + 2;
  const GH = Math.ceil(H / GRID_STRIDE) + 2;
  const grid = new Uint8Array(GW * GH);

  function cellRange(tile, tx, ty, c) {
    const sx = tile.fullW / tile.mask.w;
    const sy = tile.fullH / tile.mask.h;
    let x0 = (tx + c[0] * sx) / GRID_STRIDE | 0;
    let y0 = (ty + c[1] * sy) / GRID_STRIDE | 0;
    let x1 = (tx + (c[0] + 1) * sx) / GRID_STRIDE | 0;
    let y1 = (ty + (c[1] + 1) * sy) / GRID_STRIDE | 0;
    if (x0 < 0) x0 = 0; if (y0 < 0) y0 = 0;
    if (x1 >= GW) x1 = GW - 1; if (y1 >= GH) y1 = GH - 1;
    return [x0, y0, x1, y1];
  }

  function collides(tile, tx, ty) {
    const cells = tile.mask.cells;
    for (let i = 0; i < cells.length; i++) {
      const r = cellRange(tile, tx, ty, cells[i]);
      for (let gy = r[1]; gy <= r[3]; gy++) {
        const off = gy * GW;
        for (let gx = r[0]; gx <= r[2]; gx++) {
          if (grid[off + gx]) return true;
        }
      }
    }
    return false;
  }

  function stamp(tile, tx, ty) {
    const cells = tile.mask.cells;
    for (let i = 0; i < cells.length; i++) {
      const r = cellRange(tile, tx, ty, cells[i]);
      let gy0 = r[1] - pad, gy1 = r[3] + pad;
      let gx0 = r[0] - pad, gx1 = r[2] + pad;
      if (gy0 < 0) gy0 = 0; if (gx0 < 0) gx0 = 0;
      if (gy1 >= GH) gy1 = GH - 1; if (gx1 >= GW) gx1 = GW - 1;
      for (let gy = gy0; gy <= gy1; gy++) {
        const off = gy * GW;
        for (let gx = gx0; gx <= gx1; gx++) grid[off + gx] = 1;
      }
    }
  }

  function offGrid(tile, tx, ty) {
    return tx < 0 || ty < 0 || tx + tile.fullW > W || ty + tile.fullH > H;
  }

  const cx = W / 2, cy = H / 2;
  tiles.sort((a, b) => (b.fullW * b.fullH) - (a.fullW * a.fullH));
  const placed = [];

  for (let i = 0; i < tiles.length; i++) {
    const t = tiles[i];
    if (i === 0) {
      t.x = cx - t.fullW / 2;
      t.y = cy - t.fullH / 2;
      stamp(t, t.x, t.y);
      placed.push(t);
      continue;
    }

    let comX = 0, comY = 0, comW = 0;
    placed.forEach(p => {
      const a = p.fullW * p.fullH;
      comX += (p.x + p.fullW / 2) * a;
      comY += (p.y + p.fullH / 2) * a;
      comW += a;
    });
    comX /= comW; comY /= comW;

    let best = null, bestCost = Infinity;
    const step = Math.max(GRID_STRIDE, Math.min(t.fullW, t.fullH) * 0.05);
    const maxR = Math.max(W, H);
    let foundRing = -1;
    const phase = rand() * Math.PI * 2;
    for (let r = 0; r <= maxR; r += step) {
      if (foundRing >= 0 && r > foundRing + step * 2) break;
      const samples = Math.max(36, Math.floor(r / 1.6));
      for (let k = 0; k < samples; k++) {
        const theta = phase + (k / samples) * Math.PI * 2;
        const px = cx + r * xBias * Math.cos(theta) - t.fullW / 2;
        const py = cy + r * yBias * Math.sin(theta) - t.fullH / 2;
        if (offGrid(t, px, py)) continue;
        if (collides(t, px, py)) continue;
        const dxx = px + t.fullW / 2 - comX;
        const dyy = py + t.fullH / 2 - comY;
        const cost = Math.hypot(dxx / xBias, dyy / yBias) + rand() * step * 0.5;
        if (cost < bestCost) { bestCost = cost; best = { x: px, y: py }; }
      }
      if (best && foundRing < 0) foundRing = r;
    }
    if (best) {
      t.x = best.x; t.y = best.y;
      stamp(t, best.x, best.y);
      placed.push(t);
    } else {
      t.x = -99999; t.y = -99999;
      placed.push(t);
    }
  }
  return placed;
}

// --- Main: read stdin, run pipeline, emit positions ---
function main() {
  const input = JSON.parse(readFileSync("/dev/stdin", "utf-8"));
  const items = input.species || input;
  const W = input.W || input.w || input.width || 800;
  const H = input.H || input.h || input.height || 600;
  const ANIMATE = input.animate !== false;

  // Reset global state
  maskCache = {};
  seed = 0x9e3779b9 >>> 0;

  const T = tuning(items.length);
  const vpArea = W * H;
  const budget = vpArea * T.packingBudgetFrac;
  const minArea = vpArea * T.minTileAreaFrac;

  // Build tiles (no pose limiting — FLY_PROB is handled at snapshot time)
  const tiles = items.map(s => {
    const base = slugify(s.sci);
    const slug = base;
    const mask = loadMask(slug);
    if (!mask) return null;
    const d = DIMS[slug];
    const n = +s.n || 1;
    return {
      mask,
      data: s,
      slug,
      pose: 1,
      ar: d ? d[0] / d[1] : 1.4,
      score: Math.pow(Math.max(1, n), T.countExp),
    };
  }).filter(Boolean);

  const sumScore = tiles.reduce((a, t) => a + t.score, 0) || 1;
  tiles.forEach(t => {
    t.area = Math.max(minArea, budget * t.score / sumScore);
  });
  const sumA = tiles.reduce((a, t) => a + t.area, 0);
  if (sumA > budget) {
    const fixedSum = tiles.filter(t => t.area <= minArea + 1e-9)
      .reduce((a, t) => a + t.area, 0);
    const flexSum = sumA - fixedSum;
    const flexBudget = Math.max(0, budget - fixedSum);
    const shrink = flexSum > 0 ? Math.min(1, flexBudget / flexSum) : 1;
    tiles.forEach(t => { if (t.area > minArea + 1e-9) t.area *= shrink; });
  }

  tiles.forEach(t => {
    t.fullW = Math.sqrt(t.area * t.ar);
    t.fullH = t.fullW / t.ar;
  });

  const narrow = W <= 700;
  const xBias = narrow ? 1 : T.ellipseAspectBias;
  const yBias = narrow ? 1.7 : 1;
  const pad = narrow ? Math.max(1, COLLAGE_PAD - 1) : COLLAGE_PAD;
  let placed = maskPack(tiles, W, H, xBias, yBias, pad);

  // Scale-to-fit loop
  function clusterBounds(arr) {
    let L = Infinity, R = -Infinity, T2 = Infinity, B = -Infinity;
    arr.forEach(t => {
      if (t.x < -1000) return;
      if (t.x < L) L = t.x;
      if (t.x + t.fullW > R) R = t.x + t.fullW;
      if (t.y < T2) T2 = t.y;
      if (t.y + t.fullH > B) B = t.y + t.fullH;
    });
    return { L, R, T: T2, B };
  }

  let b = clusterBounds(placed);
  for (let iter = 0; iter < 10; iter++) {
    const missing = placed.some(t => t.x < -1000);
    const overflow = b.L < 0 || b.T < 0 || b.R > W || b.B > H;
    if (!missing && !overflow) break;
    let scale = 0.93;
    if (overflow) {
      const clW = b.R - b.L, clH = b.B - b.T;
      const sx = (W * 0.96) / Math.max(clW, W * 0.96);
      const sy = (H * 0.94) / Math.max(clH, H * 0.94);
      scale = Math.min(scale, sx, sy);
    }
    tiles.forEach(t => { t.fullW *= scale; t.fullH *= scale; });
    placed = maskPack(tiles, W, H, xBias, yBias, pad);
    b = clusterBounds(placed);
  }

  // Re-centre
  const dx = W / 2 - (b.L + b.R) / 2;
  const dy = H / 2 - (b.T + b.B) / 2;
  if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
    placed.forEach(t => { if (t.x > -1000) { t.x += dx; t.y += dy; } });
  }

  const result = placed.map(t => ({
    slug: t.slug,
    x: Math.round(t.x * 1000) / 1000,
    y: Math.round(t.y * 1000) / 1000,
    fullW: Math.round(t.fullW * 1000) / 1000,
    fullH: Math.round(t.fullH * 1000) / 1000,
    pose: t.pose,
    sci: t.data.sci,
    n: +t.data.n,
  }));

  process.stdout.write(JSON.stringify(result, null, 2));
}

main();