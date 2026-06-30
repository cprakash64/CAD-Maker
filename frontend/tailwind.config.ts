import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // --- Warm graphite / titanium / champagne studio system. ---
        // Colors resolve from CSS variables (see globals.css) so the whole app
        // follows the OS light/dark preference automatically. No blue/green/
        // purple anywhere; the only accent is aged champagne brass.
        ink: "rgb(var(--c-ink) / <alpha-value>)",
        panel: "rgb(var(--c-panel) / <alpha-value>)",
        raised: "rgb(var(--c-raised) / <alpha-value>)",
        edge: "rgb(var(--c-edge) / <alpha-value>)",
        accent: "rgb(var(--c-accent) / <alpha-value>)",
        "accent-hover": "rgb(var(--c-accent-hover) / <alpha-value>)",
        brass: "rgb(var(--c-accent) / <alpha-value>)",
        champagne: "rgb(var(--c-champagne) / <alpha-value>)",
        danger: "rgb(var(--c-danger) / <alpha-value>)",
        "on-accent": "rgb(var(--c-on-accent) / <alpha-value>)",
        viewport: "rgb(var(--c-viewport) / <alpha-value>)",

        // Warm titanium gray ramp (remaps default blue-tinted `slate`).
        slate: {
          50: "rgb(var(--c-slate-50) / <alpha-value>)",
          100: "rgb(var(--c-slate-100) / <alpha-value>)",
          200: "rgb(var(--c-slate-200) / <alpha-value>)",
          300: "rgb(var(--c-slate-300) / <alpha-value>)",
          400: "rgb(var(--c-slate-400) / <alpha-value>)",
          500: "rgb(var(--c-slate-500) / <alpha-value>)",
          600: "rgb(var(--c-slate-600) / <alpha-value>)",
          700: "rgb(var(--c-slate-700) / <alpha-value>)",
          800: "rgb(var(--c-slate-800) / <alpha-value>)",
          900: "rgb(var(--c-slate-900) / <alpha-value>)",
          950: "rgb(var(--c-slate-950) / <alpha-value>)",
        },

        // Success / affirmative = champagne brass, never green.
        emerald: {
          200: "rgb(var(--c-emerald-200) / <alpha-value>)",
          300: "rgb(var(--c-emerald-300) / <alpha-value>)",
          400: "rgb(var(--c-emerald-400) / <alpha-value>)",
          500: "rgb(var(--c-emerald-500) / <alpha-value>)",
          600: "rgb(var(--c-emerald-600) / <alpha-value>)",
          700: "rgb(var(--c-emerald-700) / <alpha-value>)",
        },

        // Danger = muted warm brick, never neon red.
        red: {
          100: "rgb(var(--c-red-100) / <alpha-value>)",
          200: "rgb(var(--c-red-200) / <alpha-value>)",
          300: "rgb(var(--c-red-300) / <alpha-value>)",
          400: "rgb(var(--c-red-400) / <alpha-value>)",
          500: "rgb(var(--c-red-500) / <alpha-value>)",
          600: "rgb(var(--c-red-600) / <alpha-value>)",
        },

        // Warnings = warmed amber-gold (no acid yellow).
        amber: {
          100: "rgb(var(--c-amber-100) / <alpha-value>)",
          200: "rgb(var(--c-amber-200) / <alpha-value>)",
          300: "rgb(var(--c-amber-300) / <alpha-value>)",
          400: "rgb(var(--c-amber-400) / <alpha-value>)",
          500: "rgb(var(--c-amber-500) / <alpha-value>)",
        },
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "SF Pro Text", "Segoe UI",
          "Inter", "Helvetica Neue", "Arial", "sans-serif",
        ],
        mono: [
          "ui-monospace", "SFMono-Regular", "Menlo", "Consolas",
          "Liberation Mono", "monospace",
        ],
      },
      borderRadius: {
        md: "8px",
        lg: "12px",
        xl: "16px",
        "2xl": "20px",
      },
      boxShadow: {
        // Soft, warm-tinted elevation — never a hard black drop shadow.
        glass: "0 1px 0 0 rgba(255,248,235,0.04) inset, 0 18px 40px -24px rgba(0,0,0,0.8)",
        lift: "0 1px 0 0 rgba(255,248,235,0.06) inset, 0 24px 50px -28px rgba(0,0,0,0.85)",
        "glow-accent": "0 0 0 1px rgba(214,170,77,0.35), 0 12px 32px -16px rgba(214,170,77,0.25)",
      },
      transitionTimingFunction: {
        premium: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      keyframes: {
        "fade-in-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "overlay-in": {
          "0%": { opacity: "0", transform: "translateY(-6px) scale(0.985)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
      },
      animation: {
        "fade-in-up": "fade-in-up 0.5s cubic-bezier(0.22,1,0.36,1) both",
        "fade-in": "fade-in 0.2s ease-out both",
        "overlay-in": "overlay-in 0.22s cubic-bezier(0.22,1,0.36,1) both",
      },
    },
  },
  plugins: [],
};

export default config;
