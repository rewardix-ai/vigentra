import type { LatLngBoundsExpression } from "leaflet";

/**
 * The only part of the world this registry has anything to say about.
 *
 * Web Mercator tiles repeat horizontally forever, so a map left unconstrained
 * draws India again every 360 degrees and lets an operator pan to a fourth
 * copy of Gujarat with thirty cameras that are not there. It also lets them
 * zoom out to a whole-world view where every camera in the estate collapses
 * onto a single pixel over the subcontinent.
 *
 * Neither is a view anyone wants and both look like faults. The bounds below
 * are India with a margin, applied as a hard stop rather than a rubber band:
 * a map you can drag off the country and have snap back still shows the wrong
 * thing while you are dragging.
 *
 * Generous on purpose. This clamps the *view*, not the data, so it must never
 * be the reason a camera cannot be seen - Kutch reaches past 68 degrees and
 * the north-east past 97, and a border camera sitting exactly on the edge
 * still needs room around it to be inspected.
 */
export const INDIA_BOUNDS: LatLngBoundsExpression = [
  [5.5, 66.5], // south-west, below Kanyakumari and west of Kutch
  [38.5, 99.5], // north-east, above Ladakh and east of Arunachal
];

/**
 * Furthest out the map may zoom.
 *
 * At 4, India fills the frame. Below that the tiles start repeating at the
 * edges even inside the bounds, which is the artefact this exists to prevent.
 */
export const INDIA_MIN_ZOOM = 4;
