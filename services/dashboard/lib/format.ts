/**
 * Display formatting.
 *
 * House rule: every timestamp crossing the API is UTC, and every timestamp an
 * operator reads is Asia/Kolkata. The conversion happens here and nowhere else.
 */

export const DISPLAY_TIMEZONE = "Asia/Kolkata";

const dateTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: DISPLAY_TIMEZONE,
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const dateTimeSeconds = new Intl.DateTimeFormat("en-IN", {
  timeZone: DISPLAY_TIMEZONE,
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const dateOnly = new Intl.DateTimeFormat("en-IN", {
  timeZone: DISPLAY_TIMEZONE,
  day: "2-digit",
  month: "short",
  year: "numeric",
});

export function ist(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return `${dateTime.format(parsed)} IST`;
}

export function istPrecise(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return dateTimeSeconds.format(parsed);
}

/** A plain calendar date (installation / commissioning), no timezone shift. */
export function calendarDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return dateOnly.format(parsed);
}

export function relative(value: string | null | undefined): string {
  if (!value) return "never";
  const parsed = new Date(value).getTime();
  if (Number.isNaN(parsed)) return "never";

  const seconds = Math.round((Date.now() - parsed) / 1000);
  if (seconds < 0) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function latency(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value)} ms`;
}

export function coordinates(lat: number | null, lng: number | null): string {
  if (lat === null || lat === undefined || lng === null || lng === undefined) {
    return "not recorded";
  }
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

/** "installation_form_created" -> "Installation form created". */
export function humanise(value: string | null | undefined): string {
  if (!value) return "—";
  const spaced = value.replace(/[_-]+/g, " ").trim().toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Preserves acronyms that should stay upper case. */
export function titleise(value: string | null | undefined): string {
  if (!value) return "—";
  const keep = new Set(["PTZ", "ANPR", "RTSP", "ONVIF", "NVR", "VMS_API"]);
  if (keep.has(value)) return value.replace("_", " ");
  return humanise(value);
}

export function departmentShort(department: string | null | undefined): string {
  if (!department) return "—";
  if (department === "Traffic Police") return "Traffic";
  if (department === "Municipal Corporation") return "Municipal";
  return department;
}

export function orDash(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}
