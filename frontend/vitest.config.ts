import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// jsdom covers both the pure-logic *.test.ts files (plain JS runs fine under
// it) and the *.test.tsx component-rendering tests (React Testing Library
// needs a DOM) -- one environment, no per-file overrides to maintain.
export default defineConfig({
  // The app's tsconfig sets jsx:"preserve" (Next.js's own compiler handles
  // the transform); Vitest runs through esbuild directly, so it needs an
  // explicit runtime here or JSX compiles to bare `React.createElement`
  // calls with no auto-import, blowing up with "React is not defined".
  esbuild: { jsx: "automatic" },
  // Inline (empty) PostCSS config so Vite never searches for / loads the
  // Tailwind postcss.config.mjs, which fails in some Node/Vite environments.
  css: { postcss: { plugins: [] } },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
