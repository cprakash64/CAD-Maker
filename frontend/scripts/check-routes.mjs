#!/usr/bin/env node
// Post-build check: prove the New Design routes exist in the App Router build.
// Run after `next build` (reads .next/app-path-routes-manifest.json).
import { readFileSync } from "node:fs";

const REQUIRED = ["/designs/new", "/new-design", "/new"];
const MANIFEST = ".next/app-path-routes-manifest.json";

let routes;
try {
  routes = Object.values(JSON.parse(readFileSync(MANIFEST, "utf8")));
} catch (err) {
  console.error(`check-routes: cannot read ${MANIFEST}. Run \`npm run build\` first.`);
  console.error(String(err));
  process.exit(1);
}

const missing = REQUIRED.filter((r) => !routes.includes(r));
if (missing.length) {
  console.error(`check-routes: MISSING routes: ${missing.join(", ")}`);
  console.error(`Found: ${routes.sort().join(", ")}`);
  process.exit(1);
}
console.log(`check-routes: OK — New Design routes present (${REQUIRED.join(", ")}).`);
