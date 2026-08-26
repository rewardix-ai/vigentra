import type { Config } from "tailwindcss";

/**
 * Government / enterprise asset-registry palette.
 * Restrained on purpose: navy chrome, neutral paper, status colour used only
 * where a status is actually being communicated.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          900: "#0e1e33",
          800: "#12233d",
          700: "#1b2f4b",
          600: "#25405f",
          500: "#2f5480",
        },
        brand: {
          700: "#153f7d",
          600: "#1b4f9c",
          500: "#2463b8",
          100: "#e3edf9",
          50: "#f1f6fc",
        },
        paper: "#f4f6f8",
        line: {
          DEFAULT: "#d8dee6",
          strong: "#bcc6d2",
        },
        ink: {
          900: "#1f2933",
          700: "#3b4753",
          500: "#5b6670",
          400: "#7b858f",
        },
        ok: { DEFAULT: "#1a7f47", bg: "#e7f4ec" },
        warn: { DEFAULT: "#a2680a", bg: "#fdf3e0" },
        bad: { DEFAULT: "#b3261e", bg: "#fbeae9" },
        idle: { DEFAULT: "#5b6670", bg: "#eef1f4" },
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto",
          "Helvetica Neue", "Arial", "sans-serif",
        ],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      /**
       * Apple-style radii: the corner grows with the surface rather than one
       * value doing every job. A 16px card around a 10px button reads as
       * concentric; the same 10px on both reads as a mistake.
       */
      borderRadius: {
        sm: "6px",
        DEFAULT: "10px",
        md: "12px",
        lg: "16px",
        xl: "20px",
        "2xl": "24px",
      },
      boxShadow: {
        card: "0 1px 2px rgba(16, 30, 51, 0.06)",
        raised: "0 2px 8px rgba(16, 30, 51, 0.10)",
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [],
};

export default config;
