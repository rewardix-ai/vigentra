/**
 * The Vigentra identity, in one place.
 *
 * The mark and the wordmark appear in the console chrome, on the sign-in
 * screen and anywhere else the product names itself. Defining them once means
 * they cannot drift apart - a header that says one thing and a login screen
 * that says another is the most common way a product looks unfinished, and it
 * happens because the two were drawn separately.
 *
 * The name is vigilance and intelligence, and the mark is built from the same
 * two ideas: a shield for the first, an aperture for the second. It is drawn
 * on a 24-unit grid with a 1.5 stroke so it stays legible at the 16px the
 * navigation rail gives it, and inherits `currentColor` so one file serves
 * both the navy chrome and the light sign-in card.
 */

export const PRODUCT_NAME = "Vigentra";

/** The full positioning line. Long form: hero surfaces and page metadata. */
export const TAGLINE = "Unified AI Video Intelligence for Safer Cities";

/** Short form, for places that sit under the wordmark at 11px. */
export const TAGLINE_SHORT = "Unified AI Video Intelligence";

/**
 * The mark alone.
 *
 * `title` rather than `aria-hidden` when it stands without the wordmark, so a
 * screen reader is not handed an unlabelled link to the dashboard.
 */
export function VigentraMark({
  className = "h-4.5 w-4.5",
  labelled = false,
}: {
  className?: string;
  labelled?: boolean;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      role={labelled ? "img" : undefined}
      aria-label={labelled ? PRODUCT_NAME : undefined}
      aria-hidden={labelled ? undefined : true}
    >
      {/* Vigilance: a shield, drawn as a custodian's badge rather than a
          padlock - this platform holds a duty of care over other people's
          cameras, it does not lock things away. */}
      <path
        d="M12 2.6 4.3 5.7v5.8c0 4.7 3.2 8.6 7.7 9.6 4.5-1 7.7-4.9 7.7-9.6V5.7L12 2.6Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      {/* Intelligence: an aperture. A ring and a pupil read as a lens that is
          looking rather than an eye that is watching you - the distinction the
          whole access model is built on. */}
      <circle cx="12" cy="11.1" r="3.15" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="12" cy="11.1" r="1.15" fill="currentColor" />
      {/* Two short arcs: the signal being read off the lens. Dropped below
          16px, where they would close into a smudge. */}
      <path
        d="M7.4 15.6c1.2 1 2.8 1.6 4.6 1.6s3.4-.6 4.6-1.6"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        opacity="0.55"
      />
    </svg>
  );
}

/**
 * Mark plus wordmark plus the line under it.
 *
 * `tone` picks the two places this appears: `onDark` in the navy header,
 * `onLight` on the sign-in card. Passing the surface rather than reading a
 * theme keeps it a pure component and keeps the two call sites honest about
 * what they are placing it on.
 */
export function BrandLockup({
  tone = "onLight",
  size = "sm",
  subtitle = TAGLINE_SHORT,
}: {
  tone?: "onDark" | "onLight";
  size?: "sm" | "lg";
  subtitle?: string | null;
}) {
  const dark = tone === "onDark";
  const large = size === "lg";

  return (
    <span className="flex items-center gap-2.5">
      <span
        className={
          "flex items-center justify-center rounded " +
          (large ? "h-11 w-11 " : "h-8 w-8 ") +
          (dark
            ? "border border-white/25 bg-white/10 text-white"
            : "border border-navy-700 bg-navy-800 text-white")
        }
      >
        <VigentraMark className={large ? "h-6 w-6" : "h-4.5 w-4.5"} />
      </span>
      <span className="leading-tight">
        <span
          className={
            "block font-semibold tracking-wide " +
            (large ? "text-xl " : "text-[15px] ") +
            (dark ? "text-white" : "text-ink-900")
          }
        >
          VIGENTRA
        </span>
        {subtitle && (
          <span
            className={
              "block text-2xs uppercase tracking-wider " +
              (dark ? "text-white/60" : "text-ink-500")
            }
          >
            {subtitle}
          </span>
        )}
      </span>
    </span>
  );
}
