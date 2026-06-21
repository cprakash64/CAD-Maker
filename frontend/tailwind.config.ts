import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Carbon/graphite engineering workspace — no bright blue/green/purple.
        ink: "#0b0c0e", // carbon page background
        panel: "#131519", // graphite panels / cards
        raised: "#1a1d22", // steel/zinc raised surfaces, inputs, hover
        edge: "#2a2e36", // zinc hairline borders
        accent: "#c2974a", // brass — primary actions / highlights (used sparingly)
        brass: "#c2974a",
        danger: "#c25c52", // muted red — critical failure
        viewport: "#0a0b0d", // 3D viewport background
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
