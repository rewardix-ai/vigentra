import type { Config } from "tailwindcss";

/**
 * The console's design tokens: a gallery-white, monochrome system.
 *
 * Near-black ink on a white canvas, structure carried by a ladder of barely
 * there neutral tints rather than by shadows or colour, stadium-pill controls
 * and 24px cards. One electric blue is the only chromatic accent, and it is
 * spent on things that are asking somebody for a decision. Everything else
 * that is coloured on screen is content: the map, the video, a status.
 *
 * The colour, radius, shadow, type-size and weight scales are REPLACED rather
 * than extended. With Tailwind's defaults still present, `rounded-lg`,
 * `shadow-md` or `text-gray-500` would all compile, and a system that only
 * holds while nobody reaches past it does not hold.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      white: "#ffffff",
      black: "#000000",

      // Brand. The identity IS the ink: every primary action is an ink pill.
      primary: "#141414",
      "on-primary": "#ffffff",

      // Text.
      ink: { DEFAULT: "#141414", soft: "#262626" },
      muted: "#707070",
      // Decoration only - separators, resting icons.
      // At 2.3:1 on white it is not a colour for anything that must be read.
      faint: "#adadad",

      // Surfaces: the tint ladder that stands in for elevation.
      canvas: { DEFAULT: "#ffffff", soft: "#f3f3f3" },
      field: "#f0f0f0",
      hairline: { DEFAULT: "#e0e0e0", soft: "#f0f0f0" },

      accent: "#0066ff",

      // State. The source system defines no success/warning/error palette - it
      // describes marketing pages, which have nothing to report. An operations
      // console does, so these three exist, and they are held to one job: the
      // icon and label of a status. They never fill a surface.
      ok: "#1a7f47",
      warn: "#946000",
      bad: "#b3261e",
    },
    // A bare `border` is a control edge, not the text colour.
    borderColor: ({ theme }) => ({ ...theme("colors"), DEFAULT: "#e0e0e0" }),
    borderRadius: {
      none: "0px",
      sm: "16px", // inputs, media tiles, callouts, nav rows
      md: "24px", // content cards
      full: "9999px", // every control: buttons, pills, toggles, badges
    },
    // Shadow-free by design. Elevation is fill difference and hairlines; these
    // two are the exceptions the system itself makes - the lifted segment of a
    // segmented control, and a surface floating over a map or a video, which
    // has no page tint to separate it from what is underneath.
    boxShadow: {
      none: "none",
      segment: "0 1px 2px rgba(20, 20, 20, 0.12)",
      overlay: "0 2px 8px rgba(20, 20, 20, 0.16)",
    },
    // Saans' signature positions on the weight axis. There is no 500 and no
    // 700: text is 456, emphasis and controls are 600, headings are 652.
    fontWeight: {
      light: "300",
      normal: "456",
      semibold: "600",
      heading: "652",
    },
    // Each size carries its own leading and, where the role fixes one, its
    // weight - so `text-h3` is the whole heading, not a third of it. The sizes
    // are the design system's own, in rem at a 16px root; how large a rem is on
    // screen is a single setting at the top of globals.css.
    fontSize: {
      display: ["5rem", { lineHeight: "1", fontWeight: "652" }],
      h1: ["3.5rem", { lineHeight: "1", fontWeight: "652" }],
      h2: ["2.75rem", { lineHeight: "1.13", fontWeight: "652" }],
      h3: ["2rem", { lineHeight: "1.13", fontWeight: "652" }],
      h4: ["1.5rem", { lineHeight: "1.25", fontWeight: "652" }],
      title: ["1.25rem", { lineHeight: "1.3", fontWeight: "600" }],
      "body-lg": ["1.25rem", { lineHeight: "1.38", fontWeight: "300" }],
      body: ["1rem", { lineHeight: "1.38" }],
      "body-sm": ["0.875rem", { lineHeight: "1.43" }],
      label: ["0.75rem", { lineHeight: "1.33", fontWeight: "600" }],
      caption: ["0.75rem", { lineHeight: "1.33" }],
    },
    extend: {
      fontFamily: {
        // Saans is commercial and not shipped here; a machine that has it
        // installed uses it. Everyone else gets Inter, self-hosted as a
        // variable font so 652 and 456 are real positions, not rounded ones.
        sans: [
          "Saans", '"Inter Variable"', "Inter", "-apple-system", "BlinkMacSystemFont",
          '"Helvetica Neue"', "Arial", "sans-serif",
        ],
        // Not part of the source system. Kept for identifiers, coordinates and
        // registration numbers, where 0/O and 1/l must not be confusable.
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      spacing: {
        section: "5rem", // 80px between major blocks
        "section-lg": "7.5rem", // 120px between acts
      },
    },
  },
  plugins: [],
};

export default config;
