// Generate reference fixture JSONs for Python test validation.
// Creates 3 fixture files for 4, 12, and 24 species at different viewport sizes.
//
// Run:
//   node tools/generate_fixtures.mjs

import { writeFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { spawnSync } from "child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const FIXTURES = join(ROOT, "tests", "fixtures");
const REF_SCRIPT = join(__dirname, "reference_layout.mjs");

// Common California species with realistic counts
const ALL_SPECIES = [
  { sci: "Calypte anna", com: "Anna's Hummingbird", n: 398 },
  { sci: "Passer domesticus", com: "House Sparrow", n: 156 },
  { sci: "Haemorhous mexicanus", com: "House Finch", n: 142 },
  { sci: "Turdus migratorius", com: "American Robin", n: 98 },
  { sci: "Zenaida macroura", com: "Mourning Dove", n: 87 },
  { sci: "Spinus psaltria", com: "Lesser Goldfinch", n: 76 },
  { sci: "Zonotrichia leucophrys", com: "White-crowned Sparrow", n: 65 },
  { sci: "Aphelocoma californica", com: "California Scrub-Jay", n: 54 },
  { sci: "Mimus polyglottos", com: "Northern Mockingbird", n: 43 },
  { sci: "Sayornis nigricans", com: "Black Phoebe", n: 38 },
  { sci: "Corvus brachyrhynchos", com: "American Crow", n: 31 },
  { sci: "Bombycilla cedrorum", com: "Cedar Waxwing", n: 29 },
  { sci: "Pipilo maculatus", com: "Spotted Towhee", n: 27 },
  { sci: "Melospiza melodia", com: "Song Sparrow", n: 24 },
  { sci: "Junco hyemalis", com: "Dark-eyed Junco", n: 22 },
  { sci: "Setophaga coronata", com: "Yellow-rumped Warbler", n: 20 },
  { sci: "Sturnus vulgaris", com: "European Starling", n: 18 },
  { sci: "Columba livia", com: "Rock Pigeon", n: 16 },
  { sci: "Ardea herodias", com: "Great Blue Heron", n: 14 },
  { sci: "Buteo jamaicensis", com: "Red-tailed Hawk", n: 12 },
  { sci: "Megaceryle alcyon", com: "Belted Kingfisher", n: 10 },
  { sci: "Picoides pubescens", com: "Downy Woodpecker", n: 8 },
  { sci: "Sialia mexicana", com: "Western Bluebird", n: 6 },
  { sci: "Regulus calendula", com: "Ruby-crowned Kinglet", n: 4 },
];

const TEST_CASES = [
  { name: "reference_layout_4spp_400x300", species: ALL_SPECIES.slice(0, 4),  W: 400, H: 300 },
  { name: "reference_layout_12spp_800x600", species: ALL_SPECIES.slice(0, 12), W: 800, H: 600 },
  { name: "reference_layout_24spp_1600x1200", species: ALL_SPECIES.slice(0, 24), W: 1600, H: 1200 },
];

for (const tc of TEST_CASES) {
  const input = JSON.stringify(tc);
  const result = spawnSync("node", [REF_SCRIPT], { input, encoding: "utf-8" });
  if (result.status !== 0) {
    console.error(`FAILED: ${tc.name}`, result.stderr);
    process.exit(1);
  }
  const fixturePath = join(FIXTURES, `${tc.name}.json`);
  writeFileSync(fixturePath, result.stdout);
  const positions = JSON.parse(result.stdout);
  console.log(`${tc.name}: ${positions.length} tiles at ${tc.W}x${tc.H}`);
}