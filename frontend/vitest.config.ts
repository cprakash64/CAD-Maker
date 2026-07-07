import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// Unit tests are pure logic (no component rendering yet), so skip CSS/PostCSS
// processing entirely — this also avoids loading the Tailwind PostCSS plugin,
// which fails in some Node/Vite environments and would otherwise block the run.
export default defineConfig({
  // Inline (empty) PostCSS config so Vite never searches for / loads the
  // Tailwind postcss.config.mjs, which fails in some Node/Vite environments.
  css: { postcss: { plugins: [] } },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
