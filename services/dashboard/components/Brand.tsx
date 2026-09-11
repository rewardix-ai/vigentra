/**
 * The Vigentra identity, in one place.
 *
 * The mark and the wordmark appear in the console chrome, on the sign-in
 * screen and anywhere else the product names itself. Defining them once means
 * they cannot drift apart - a header that says one thing and a login screen
 * that says another is the most common way a product looks unfinished.
 *
 * The artwork is the approved logo, cut from the master in docs/brand/ by
 * scripts/brand_assets.py with its ground made transparent:
 *   /brand/vigentra-logo.png        the full lockup, exactly as drawn (light surfaces)
 *   /brand/vigentra-mark.png        the mark alone, in its own colours (light surfaces)
 *   /brand/vigentra-mark-light.png  the mark reversed to white (the navy chrome)
 * Re-run that script when the logo changes; nothing here needs editing.
 */

export const PRODUCT_NAME = "Vigentra";

/** The logo's own line, set under the wordmark with red separators. */
export const LOGO_TAGLINE = ["Vigilance", "Intelligence", "Safer Roads"] as const;

/**
 * The mark alone. `tone` picks the cut: the reversed white one for the navy
 * chrome, the original colours on light surfaces. `labelled` when it stands
 * without the wordmark, so a screen reader is not handed an unlabelled link.
 */
export function VigentraMark({
  className = "h-6 w-auto",
  tone = "onLight",
  labelled = false,
}: {
  className?: string;
  tone?: "onDark" | "onLight";
  labelled?: boolean;
}) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={tone === "onDark" ? "/brand/vigentra-mark-light.png" : "/brand/vigentra-mark.png"}
      alt={labelled ? PRODUCT_NAME : ""}
      aria-hidden={labelled ? undefined : true}
      className={className}
      draggable={false}
    />
  );
}

/** The wordmark as cut from the artwork (955 x 107). */
const WORDMARK_ASPECT = 955 / 107;

/**
 * "VIGILANCE | INTELLIGENCE | SAFER ROADS", stretched to exactly the
 * wordmark's width as it is in the logo. Set in SVG because `textLength` is
 * the only reliable way to justify one line to a measured width.
 */
function LogoTagline({ width, dark }: { width: number; dark: boolean }) {
  const red = "#e0443a";
  const gap = "\u00a0\u00a0";
  return (
    <svg viewBox={`0 0 ${width} 9`} width={width} height={9} aria-hidden className="block">
      <text
        x="0"
        y="7.4"
        textLength={width}
        lengthAdjust="spacing"
        fontSize="7.2"
        fontWeight={500}
        fill={dark ? "rgba(255,255,255,0.62)" : "#5b6472"}
      >
        {`VIGILANCE${gap}`}
        <tspan fill={red}>|</tspan>
        {`${gap}INTELLIGENCE${gap}`}
        <tspan fill={red}>|</tspan>
        {`${gap}SAFER ROADS`}
      </text>
    </svg>
  );
}

/**
 * Mark plus wordmark plus the line under it.
 *
 * `tone` picks the two places this appears: `onDark` in the navy header,
 * `onLight` on light surfaces. `size="lg"` on a light surface is the full
 * approved lockup image, exactly as drawn. Otherwise it is the mark beside the
 * wordmark cut from the same artwork, with the tagline justified under it;
 * pass `subtitle` as a string to replace the tagline, or null to drop it.
 */
export function BrandLockup({
  tone = "onLight",
  size = "sm",
  subtitle,
}: {
  tone?: "onDark" | "onLight";
  size?: "sm" | "lg";
  subtitle?: string | null;
}) {
  const dark = tone === "onDark";
  const large = size === "lg";

  if (large && !dark) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src="/brand/vigentra-logo.png"
        alt="Vigentra - Vigilance, Intelligence, Safer Roads"
        className="h-auto w-72 max-w-full"
        draggable={false}
      />
    );
  }

  const wordHeight = large ? 28 : 22;
  const wordWidth = Math.round(wordHeight * WORDMARK_ASPECT);

  return (
    <span
      className="flex items-center gap-3"
      role="img"
      aria-label="Vigentra - Vigilance, Intelligence, Safer Roads"
    >
      <VigentraMark tone={tone} className={(large ? "h-12" : "h-9") + " w-auto shrink-0"} />
      <span className="flex flex-col gap-[5px]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={dark ? "/brand/vigentra-wordmark-light.png" : "/brand/vigentra-wordmark.png"}
          alt=""
          style={{ height: wordHeight, width: wordWidth }}
          draggable={false}
        />
        {subtitle === undefined ? (
          <LogoTagline width={wordWidth} dark={dark} />
        ) : (
          subtitle && (
            <span
              className={
                "block text-2xs uppercase tracking-wider " + (dark ? "text-white/60" : "text-ink-500")
              }
            >
              {subtitle}
            </span>
          )
        )}
      </span>
    </span>
  );
}
