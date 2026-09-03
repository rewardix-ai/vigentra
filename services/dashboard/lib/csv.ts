/**
 * CSV utilities used by the registry export and the bulk-onboarding import.
 *
 * Escaping rules follow RFC 4180: values that contain a comma, quote or
 * newline are wrapped in double quotes; embedded quotes are doubled.
 */

import type { Camera } from "./types";

function escape(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  if (/[",\r\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function toCSV(rows: readonly (readonly unknown[])[], header: readonly string[]): string {
  const lines = [header.map(escape).join(",")];
  for (const row of rows) lines.push(row.map(escape).join(","));
  // Windows-style newlines so Excel opens the file cleanly on this platform.
  return lines.join("\r\n") + "\r\n";
}

export function cameraCsvHeader(): string[] {
  return [
    "camera_id",
    "external_camera_id",
    "source_system",
    "owning_department",
    "owning_unit",
    "name",
    "camera_type",
    "installation_purpose",
    "district",
    "road_or_junction",
    "landmark",
    "latitude",
    "longitude",
    "view_direction",
    "vms_name",
    "resolution",
    "fps",
    "codec",
    "installation_date",
    "commissioning_date",
    "installation_status",
    "approval_status",
    "health_status",
    "last_frame_utc",
    "vigentra_sync_status",
    "last_metadata_sync_utc",
    "footage_access_via_vigentra",
  ];
}

export function cameraCsvRow(camera: Camera): unknown[] {
  return [
    camera.camera_id,
    camera.external_camera_id,
    camera.source_system,
    camera.owning_department,
    camera.owning_unit ?? "",
    camera.name,
    camera.camera_type,
    camera.installation_purpose ?? "",
    camera.location.district,
    camera.location.road_or_junction ?? "",
    camera.location.landmark ?? "",
    camera.location.latitude ?? "",
    camera.location.longitude ?? "",
    camera.location.view_direction,
    camera.vms_name ?? "",
    camera.technical_summary.resolution ?? "",
    camera.technical_summary.fps ?? "",
    camera.technical_summary.codec ?? "",
    camera.installation.installation_date ?? "",
    camera.installation.commissioning_date ?? "",
    camera.installation.installation_status,
    camera.approval.status,
    camera.health.status,
    camera.health.last_frame_utc ?? "",
    camera.vigentra_sync.status,
    camera.vigentra_sync.synced_at_utc ?? "",
    // Always false in Module 1 - included so downstream consumers of the CSV
    // can see the boundary in their own tooling too.
    camera.footage_access_via_vigentra ? "true" : "false",
  ];
}

/** Trigger a browser download from an in-memory string. */
export function download(filename: string, contents: string, mime = "text/csv;charset=utf-8"): void {
  const blob = new Blob([contents], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/**
 * Minimal CSV parser: handles quoted fields, embedded commas, escaped quotes
 * and CRLF or LF line endings. Enough for hand-produced spreadsheets and
 * exports from Excel / LibreOffice - not a full RFC 4180 implementation.
 */
export function parseCSV(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const character = text[i];
    if (inQuotes) {
      if (character === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += character;
      }
      continue;
    }
    if (character === '"') {
      inQuotes = true;
    } else if (character === ",") {
      row.push(field);
      field = "";
    } else if (character === "\n" || character === "\r") {
      // eat a trailing \n after \r
      if (character === "\r" && text[i + 1] === "\n") i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  // Drop any all-empty trailing row (common when the file ends in a newline).
  while (rows.length > 0 && rows[rows.length - 1].every((value) => value === "")) {
    rows.pop();
  }
  return rows;
}
