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
 *   /brand/vigentra-mark-light.png  the mark reversed to white (dark surfaces)
 * Re-run that script when the logo changes; nothing here needs editing.
 */

export const PRODUCT_NAME = "Vigentra";

/**
 * The mark alone. `tone` picks the cut: the reversed white one for a dark
 * surface, the original colours on light surfaces. `labelled` when it
 * stands without the wordmark, so a screen reader is not handed an unlabelled link.
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
    // width/height give the browser the mark's shape before the file arrives.
    // Without them the first paint after sign-in has a zero-width mark, and the
    // wordmark jumps sideways when the image lands.
    <img
      src={tone === "onDark" ? "/brand/vigentra-mark-light.png" : "/brand/vigentra-mark.png"}
      width={395}
      height={270}
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
 * Lockup dimensions are worked out in pixels at full scale and set in rem, so
 * the wordmark and its tagline follow the console's size setting together with
 * the mark beside them, whose height is already a rem utility.
 */
const rem = (px: number) => `${px / 16}rem`;

/**
 * "VIGILANCE | INTELLIGENCE | SAFER ROADS", spread to exactly the wordmark's
 * width as it is in the logo. Flexbox, not SVG: Safari lays out an SVG
 * `textLength` with tspans glyph by glyph and scrambles the line.
 */
function LogoTagline({ width, dark }: { width: number; dark: boolean }) {
  const bar = <span style={{ color: "#e0443a" }}>|</span>;
  return (
    <span
      aria-hidden
      className="flex justify-between whitespace-nowrap font-normal leading-none"
      style={{
        width: rem(width),
        fontSize: rem(width * 0.0367),
        letterSpacing: "0.08em",
        color: dark ? "rgba(255,255,255,0.62)" : "#5b6472",
      }}
    >
      <span>VIGILANCE</span>
      {bar}
      <span>INTELLIGENCE</span>
      {bar}
      <span>SAFER ROADS</span>
    </span>
  );
}

/**
 * Mark plus wordmark plus the line under it.
 *
 * `tone` picks the cut: `onLight` in the navigation pill, on the sign-in page
 * and on any other light surface; `onDark` is the reversed lockup for a dark
 * one, which the console does not currently have. The
 * tagline's capitals and tracking belong to the logo artwork, which is why
 * they survive in an interface that otherwise sets no type that way.
 * `size="lg"` on a light surface is the full
 * approved lockup image, exactly as drawn. Otherwise it is the mark beside the
 * wordmark cut from the same artwork, with the tagline justified under it.
 */
export function BrandLockup({
  tone = "onLight",
  size = "sm",
}: {
  tone?: "onDark" | "onLight";
  size?: "sm" | "lg";
}) {
  const dark = tone === "onDark";
  const large = size === "lg";

  if (large && !dark) {
    return (
      <img
        src="/brand/vigentra-logo.png"
        width={955}
        height={501}
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
      <span className="flex flex-col gap-[0.3125rem]">
        <img
          src={dark ? "/brand/vigentra-wordmark-light.png" : "/brand/vigentra-wordmark.png"}
          alt=""
          style={{ height: rem(wordHeight), width: rem(wordWidth) }}
          draggable={false}
        />
        <LogoTagline width={wordWidth} dark={dark} />
      </span>
    </span>
  );
}
