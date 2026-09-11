/**
 * Constants shared by more than one page. Each used to be copied into every
 * file that needed it, and the copies had already drifted in key order.
 */
import type { CameraTypeValue, PurposeValue, SourceTypeValue } from "./types";

/** Pill tone for each watchlist category (alerts and watchlist pages). */
export const CATEGORY_TONE: Record<string, "ok" | "warn" | "bad" | "idle" | "info"> = {
  stolen: "bad",
  wanted: "bad",
  blacklist: "warn",
  missing: "info",
  suspect: "warn",
};

/** One colour per detection class (traffic counts and the detection overlay). */
export const CLASS_COLOUR: Record<string, string> = {
  person: "#f59e0b",
  bicycle: "#a3e635",
  motorcycle: "#22d3ee",
  car: "#4ade80",
  bus: "#c084fc",
  truck: "#fb7185",
  "auto-rickshaw": "#fbbf24",
};

/** Installation form vocabularies (single form and bulk CSV upload). */
export const CAMERA_TYPES: CameraTypeValue[] = [
  "fixed", "PTZ", "dome", "bullet", "ANPR-capable", "thermal", "other",
];
export const PURPOSES: PurposeValue[] = [
  "traffic monitoring", "public safety", "junction monitoring", "highway monitoring", "other",
];
export const SOURCE_TYPES: SourceTypeValue[] = ["RTSP", "ONVIF", "VMS_API", "NVR", "other"];
