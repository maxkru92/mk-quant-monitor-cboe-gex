// Fix: GEX normalization -- real-world SPX Net GEX range is actually 0-20B
// not 0-5B. Adjust MAX_GEX accordingly.
import { readFileSync, writeFileSync } from "fs";

let src = readFileSync("src/gex-compute.js", "utf8");
src = src.replace(
  'const MAX_GEX = 5_000_000_000; // 5B USD as reference',
  'const MAX_GEX = 20_000_000_000; // 20B USD as reference (real-world SPX range)'
);
writeFileSync("src/gex-compute.js", src);
console.log("normalizeGEX max adjusted to 20B");
