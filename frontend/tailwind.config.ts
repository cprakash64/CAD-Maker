import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Neutral graphite surfaces — engineering tool, not navy "AI SaaS".
        ink: "#0c0d10", // page background
        panel: "#15171c", // cards / surfaces
        raised: "#1c1f26", // inputs / secondary surfaces / hover
        edge: "#2b2f38", // hairline borders
        accent: "#3f7fe0", // restrained steel blue (used sparingly)
        viewport: "#0a0b0e", // 3D viewport background
      },
      fontFamily: {
        mono: [
          "ui-monospace", "SFMono-Regular", "Menlo", "Consolas",
          "Liberation Mono", "monospace",
        ],
      },
      borderRadius: {
        md: "6px",
        lg: "8px",
        xl: "12px",
      },
    },
  },
  plugins: [],
};

export default config;
