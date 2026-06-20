import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0b1020",
        panel: "#11182e",
        edge: "#1f2a44",
        accent: "#5b8cff",
      },
    },
  },
  plugins: [],
};

export default config;
