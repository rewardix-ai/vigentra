"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { LoadingPanel } from "@/components/Shell";
import { Card, EmptyState, FootageNotice, Notice, PageHeader, Pill, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { download, parseCSV, toCSV } from "@/lib/csv";
import type {
  AttachmentTypeValue,
  CameraTypeValue,
  InstallationRequest,
  LocalRoleValue,
  Operator,
  PurposeValue,
  SourceTypeValue,
} from "@/lib/types";

/**
 * Bulk camera onboarding.
 *
 * The operator uploads a CSV with one row per camera. Each row is validated
 * client-side (types + required fields), converted into the same canonical
 * form the single-camera page uses, and POSTed. Every row ends up as an
 * INSTALLATION_OPERATOR-owned record in that operator's department system,
 * where validation registers it without human approval.
 *
 * The point of doing this from CSV rather than a dedicated bulk API is that
 * one row = one identical audit trail to a manually raised form. No shortcut
 * around the department validation gate.
 */

const REQUIRED = [
  "camera_name",
  "external_camera_id",
  "camera_type",
  "installation_purpose",
  "owning_unit",
  "district",
  "road_or_junction",
  "view_direction",
  "latitude",
  "longitude",
  "source_type",
  "installation_date",
  "permitted_local_roles",
] as const;

const OPTIONAL = [
  "camera_serial_number",
  "camera_vendor",
  "camera_model",
  "police_station_or_zone",
  "local_admin_contact",
  "maintenance_agency",
  "installation_vendor",
  "address_or_landmark",
  "coverage_description",
  "entry_exit_zone_description",
  "vms_name",
  "vms_vendor",
  "resolution",
  "fps",
  "codec",
  "retention_days",
  "timezone",
  "commissioning_date",
  "attachment_ref_site_survey",
  "attachment_ref_installation_certificate",
  "attachment_ref_camera_photograph",
] as const;

const TEMPLATE_HEADER = [...REQUIRED, ...OPTIONAL];

const TEMPLATE_ROWS = [
  [
    "Sarkhej Circle North",
    "TRF-AHM-B001",
    "fixed",
    "junction monitoring",
    "Ahmedabad Traffic Zone 3",
    "Ahmedabad",
    "Sarkhej Circle",
    "northbound",
    "22.9955",
    "72.5012",
    "RTSP",
    "2026-08-19",
    "department_operator | investigator",
    "GTPX7712340001",
    "Demo Vendor",
    "Demo IP Camera X1",
    "Sarkhej PS",
    "+91 79 2650 9101",
    "Gujarat Infotech Services",
    "Metro Systems Pvt Ltd",
    "Sarkhej Circle, north arm",
    "Circle north approach",
    "",
    "Traffic VMS Demo",
    "GTP-VMS",
    "1920x1080",
    "25",
    "H.264",
    "30",
    "Asia/Kolkata",
    "",
    "DOC-TRF-SS-B001",
    "DOC-TRF-IC-B001",
    "",
  ],
  [
    "Sarkhej Circle South",
    "TRF-AHM-B002",
    "PTZ",
    "junction monitoring",
    "Ahmedabad Traffic Zone 3",
    "Ahmedabad",
    "Sarkhej Circle",
    "southbound",
    "22.9948",
    "72.5015",
    "VMS_API",
    "2026-08-19",
    "department_operator",
    "GTPX7712340002",
    "Demo Vendor",
    "Demo PTZ Camera P2",
    "Sarkhej PS",
    "",
    "",
    "",
    "",
    "",
    "",
    "Traffic VMS Demo",
    "GTP-VMS",
    "2560x1440",
    "30",
    "H.265",
    "45",
    "Asia/Kolkata",
    "",
    "",
    "",
    "",
  ],
];

const CAMERA_TYPES: CameraTypeValue[] = [
  "fixed",
  "PTZ",
  "dome",
  "bullet",
  "ANPR-capable",
  "thermal",
  "other",
];
const PURPOSES: PurposeValue[] = [
  "traffic monitoring",
  "public safety",
  "junction monitoring",
  "highway monitoring",
  "other",
];
const SOURCE_TYPES: SourceTypeValue[] = ["RTSP", "ONVIF", "VMS_API", "NVR", "other"];
const CAMERA_TYPE_ALIASES: Record<string, CameraTypeValue> = {
  anpr: "ANPR-capable",
  "anpr capable": "ANPR-capable",
};
const PURPOSE_ALIASES: Record<string, PurposeValue> = {
  "traffic violation detection": "traffic monitoring",
  "general surveillance": "public safety",
  "speed monitoring": "traffic monitoring",
  "traffic flow monitoring": "traffic monitoring",
  "e-challan generation": "traffic monitoring",
  "illegal parking detection": "public safety",
  surveillance: "public safety",
  surveilliance: "public safety",
};
const SOURCE_TYPE_ALIASES: Record<string, SourceTypeValue> = {
  "fixed ip": "other",
  "fixed ip camera": "other",
  ip: "other",
  "ip camera": "other",
};
const LOCAL_ROLES: LocalRoleValue[] = [
  "department_operator",
  "district_supervisor",
  "state_supervisor",
  "investigator",
  "system_admin",
  "auditor",
];

interface ParsedRow {
  line: number;
  raw: Record<string, string>;
  form: Record<string, unknown> | null;
  errors: string[];
}

interface Outcome {
  line: number;
  external_camera_id: string;
  camera_name: string;
  status: "created" | "created+submitted" | "failed";
  request_id?: string;
  message?: string;
}

function trimmed(value: string | undefined): string {
  return (value ?? "").trim();
}

function toNumber(value: string, field: string, errors: string[], integer = false): number | null {
  const raw = trimmed(value);
  if (raw === "") return null;
  const num = Number(raw);
  if (Number.isNaN(num)) {
    errors.push(`${field} must be numeric`);
    return null;
  }
  if (integer && !Number.isInteger(num)) {
    errors.push(`${field} must be a whole number`);
    return null;
  }
  return num;
}

function parseRoles(value: string, errors: string[]): LocalRoleValue[] {
  const tokens = value
    .split(/[|,;]/)
    .map((token) => token.trim().toLowerCase())
    .filter(Boolean);
  const accepted: LocalRoleValue[] = [];
  for (const token of tokens) {
    if ((LOCAL_ROLES as string[]).includes(token)) {
      if (!accepted.includes(token as LocalRoleValue)) accepted.push(token as LocalRoleValue);
    } else {
      errors.push(`unknown local role "${token}"`);
    }
  }
  if (accepted.length === 0) errors.push("permitted_local_roles is required");
  return accepted;
}

function normalise(
  raw: Record<string, string>,
  line: number,
  department: string,
): ParsedRow {
  const errors: string[] = [];
  const missing = REQUIRED.filter((field) => trimmed(raw[field]) === "");
  if (missing.length > 0) {
    errors.push(`missing required field(s): ${missing.join(", ")}`);
  }

  const cameraType = trimmed(raw.camera_type).toLowerCase();
  const canonicalType = CAMERA_TYPE_ALIASES[cameraType] ?? CAMERA_TYPES.find((c) => c.toLowerCase() === cameraType);
  if (!canonicalType && cameraType !== "") errors.push(`camera_type "${raw.camera_type}" not recognised`);

  const purpose = trimmed(raw.installation_purpose).toLowerCase();
  const canonicalPurpose = PURPOSE_ALIASES[purpose] ?? PURPOSES.find((p) => p === purpose);
  if (!canonicalPurpose && purpose !== "") errors.push(`installation_purpose "${raw.installation_purpose}" not recognised`);

  const sourceType = trimmed(raw.source_type).toLowerCase();
  const canonicalSource = SOURCE_TYPE_ALIASES[sourceType] ?? SOURCE_TYPES.find((s) => s.toLowerCase() === sourceType);
  if (!canonicalSource && sourceType !== "") errors.push(`source_type "${raw.source_type}" not recognised`);

  const latitude = toNumber(raw.latitude, "latitude", errors);
  const longitude = toNumber(raw.longitude, "longitude", errors);
  if (latitude !== null && (latitude < -90 || latitude > 90)) errors.push("latitude must be between -90 and 90");
  if (longitude !== null && (longitude < -180 || longitude > 180)) errors.push("longitude must be between -180 and 180");
  const fps = toNumber(raw.fps, "fps", errors, true);
  const retention = toNumber(raw.retention_days, "retention_days", errors, true);
  const roles = parseRoles(raw.permitted_local_roles ?? "", errors);

  const attachments: {
    document_type: AttachmentTypeValue;
    reference: string;
    filename: string | null;
  }[] = [];
  const attachmentColumns: [string, AttachmentTypeValue][] = [
    ["attachment_ref_site_survey", "site_survey"],
    ["attachment_ref_installation_certificate", "installation_certificate"],
    ["attachment_ref_camera_photograph", "camera_photograph"],
  ];
  for (const [column, type] of attachmentColumns) {
    const ref = trimmed(raw[column]);
    if (ref !== "") attachments.push({ document_type: type, reference: ref, filename: null });
  }

  if (errors.length > 0) {
    return { line, raw, form: null, errors };
  }

  const form: Record<string, unknown> = {
    camera_name: trimmed(raw.camera_name),
    external_camera_id: trimmed(raw.external_camera_id),
    camera_serial_number: trimmed(raw.camera_serial_number) || null,
    camera_vendor: trimmed(raw.camera_vendor) || null,
    camera_model: trimmed(raw.camera_model) || null,
    camera_type: canonicalType,
    installation_purpose: canonicalPurpose,
    owning_department: department,
    owning_unit: trimmed(raw.owning_unit),
    district: trimmed(raw.district),
    police_station_or_zone: trimmed(raw.police_station_or_zone) || null,
    local_admin_contact: trimmed(raw.local_admin_contact) || null,
    maintenance_agency: trimmed(raw.maintenance_agency) || null,
    installation_vendor: trimmed(raw.installation_vendor) || null,
    latitude,
    longitude,
    address_or_landmark: trimmed(raw.address_or_landmark) || null,
    road_or_junction: trimmed(raw.road_or_junction),
    view_direction: trimmed(raw.view_direction),
    coverage_description: trimmed(raw.coverage_description) || null,
    entry_exit_zone_description: trimmed(raw.entry_exit_zone_description) || null,
    source_type: canonicalSource,
    vms_name: trimmed(raw.vms_name) || null,
    vms_vendor: trimmed(raw.vms_vendor) || null,
    resolution: trimmed(raw.resolution) || null,
    fps,
    codec: trimmed(raw.codec) || null,
    supports_live: true,
    supports_playback: true,
    retention_days: retention,
    timezone: trimmed(raw.timezone) || "Asia/Kolkata",
    installation_date: trimmed(raw.installation_date),
    commissioning_date: trimmed(raw.commissioning_date) || null,
    permitted_local_roles: roles,
    attachments,
  };
  return { line, raw, form, errors };
}

export default function BulkOnboardingPage() {
  const [operator, setOperator] = useState<Operator | null>(null);
  const [rows, setRows] = useState<ParsedRow[]>([]);
  const [submitOnUpload, setSubmitOnUpload] = useState(true);
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [message, setMessage] = useState<{ tone: "ok" | "warn" | "bad"; text: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setOperator)
      .catch(() => setOperator(null))
      .finally(() => setLoading(false));
  }, []);

  const canUpload = (operator?.permissions ?? []).includes("installation:create");

  const summary = useMemo(() => {
    const valid = rows.filter((row) => row.errors.length === 0).length;
    return { total: rows.length, valid, invalid: rows.length - valid };
  }, [rows]);

  async function onFile(file: File) {
    setMessage(null);
    setOutcomes([]);
    const text = await file.text();
    const parsed = parseCSV(text);
    if (parsed.length === 0) {
      setMessage({ tone: "bad", text: "That file is empty." });
      setRows([]);
      return;
    }
    const header = parsed[0].map((column) => column.trim().toLowerCase());
    const dataLines = parsed.slice(1);
    if (dataLines.length === 0) {
      setMessage({ tone: "warn", text: "The file has a header but no data rows." });
      setRows([]);
      return;
    }

    const parsedRows: ParsedRow[] = dataLines.map((cells, index) => {
      const record: Record<string, string> = {};
      header.forEach((column, columnIndex) => {
        record[column] = cells[columnIndex] ?? "";
      });
      return normalise(record, index + 2, operator?.department ?? "");
    });
    setRows(parsedRows);
  }

  async function submitAll() {
    if (!operator) return;
    setBusy(true);
    setMessage(null);
    const results: Outcome[] = [];
    for (const row of rows) {
      if (row.errors.length > 0 || !row.form) {
        results.push({
          line: row.line,
          external_camera_id: row.raw.external_camera_id ?? "",
          camera_name: row.raw.camera_name ?? "",
          status: "failed",
          message: row.errors.join("; "),
        });
        continue;
      }
      try {
        const draft: InstallationRequest = await api.createInstallationRequest(row.form);
        let submitted = draft;
        let status: Outcome["status"] = "created";
        if (submitOnUpload) {
          submitted = await api.submitRequest(draft.request_id);
          status = "created+submitted";
        }
        const validationErrors = submitted.validation_errors ?? [];
        results.push({
          line: row.line,
          external_camera_id: row.form.external_camera_id as string,
          camera_name: row.form.camera_name as string,
          status: validationErrors.length > 0 ? "failed" : status,
          request_id: draft.request_id,
          message: validationErrors.length > 0 ? validationErrors.join("; ") : undefined,
        });
      } catch (err) {
        results.push({
          line: row.line,
          external_camera_id: (row.form.external_camera_id as string) ?? "",
          camera_name: (row.form.camera_name as string) ?? "",
          status: "failed",
          message: err instanceof Error ? err.message : String(err),
        });
      }
    }
    setOutcomes(results);
    setBusy(false);
    const failed = results.filter((r) => r.status === "failed").length;
    const created = results.length - failed;
    setMessage(
      failed === 0
        ? { tone: "ok", text: `${created} record(s) created${submitOnUpload ? " and registered after validation" : " as drafts"}.` }
        : {
            tone: "warn",
            text: `${created} created, ${failed} failed. Fix the CSV rows below and re-upload.`,
          },
    );
    setRows([]);
  }

  function downloadTemplate() {
    download("sentinel-bulk-camera-template.csv", toCSV(TEMPLATE_ROWS, TEMPLATE_HEADER));
  }

  function downloadFailedOutcomes() {
    const rows = outcomes.filter((o) => o.status === "failed");
    if (rows.length === 0) return;
    const header = ["line", "external_camera_id", "camera_name", "status", "message"];
    download(
      "sentinel-bulk-failures.csv",
      toCSV(
        rows.map((o) => [o.line, o.external_camera_id, o.camera_name, o.status, o.message ?? ""]),
        header,
      ),
    );
  }

  if (loading) return <LoadingPanel label="Loading" />;

  if (!canUpload) {
    return (
      <>
        <PageHeader
          title="Bulk CCTV onboarding"
          breadcrumb={[{ label: "Installation requests", href: "/installations" }, { label: "Bulk" }]}
        />
        <Notice tone="bad">
          Your role ({operator?.role.replace(/_/g, " ") ?? "unknown"}) does not include
          <span className="mono"> installation:create</span>. Ask an installation operator in your
          department to raise these forms, or sign in with an operator account.
        </Notice>
      </>
    );
  }

  return (
    <>
      <PageHeader
        breadcrumb={[
          { label: "Installation requests", href: "/installations" },
          { label: "Bulk" },
        ]}
        title="Bulk CCTV onboarding"
        subtitle={`Raising for ${operator?.department}${
          operator?.unit ? ` · ${operator.unit}` : ""
        }`}
        actions={
          <>
            <button className="btn" onClick={downloadTemplate}>
              Download template
            </button>
            <Link className="btn" href="/installations/new">
              Single form
            </Link>
          </>
        }
      />

      <div className="space-y-3">
        <Notice tone="info" title="How this works">
          Every row becomes a separate installation request in{" "}
          <strong>{operator?.department}</strong>&rsquo;s own system. The same validation and
          validation runs automatically — the batch upload is just a faster way to raise the forms, not a
          shortcut around validation.
        </Notice>
        <FootageNotice compact custodian={operator?.department} />

        <Card title="Upload CSV">
          <div className="px-4 py-4">
            <label className="flex flex-col items-start gap-2">
              <span className="field-label">CSV file</span>
              <input
                type="file"
                accept=".csv,text/csv"
                className="text-[13px]"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void onFile(file);
                }}
              />
            </label>
            <label className="mt-3 flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={submitOnUpload}
                onChange={(event) => setSubmitOnUpload(event.target.checked)}
              />
              Validate and register each valid row immediately (uncheck to keep as drafts)
            </label>
            <p className="mt-2 text-2xs text-ink-500">
              The template covers every accepted column. Required columns are:{" "}
              <span className="mono">{REQUIRED.join(", ")}</span>.
              Local roles can be pipe- or comma-separated (e.g.{" "}
              <span className="mono">department_operator | investigator</span>).
            </p>
          </div>

          {rows.length > 0 && (
            <div className="border-t border-line px-4 py-3">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <div className="flex gap-1.5">
                  <Pill tone="info">{summary.total} data entries</Pill>
                  <Pill tone={summary.valid > 0 ? "ok" : "idle"}>{summary.valid} valid</Pill>
                  {summary.invalid > 0 && <Pill tone="bad">{summary.invalid} invalid</Pill>}
                </div>
                <div className="flex gap-2">
                  <button className="btn" onClick={() => setRows([])} disabled={busy}>
                    Discard
                  </button>
                  <button
                    className="btn btn-primary"
                    disabled={busy || summary.valid === 0}
                    onClick={submitAll}
                  >
                    {busy && <Spinner />}
                    {submitOnUpload
                      ? `Validate and register ${summary.valid} form(s)`
                      : `Save ${summary.valid} draft(s)`}
                  </button>
                </div>
              </div>
              <div className="max-h-[60vh] overflow-auto">
                <table className="data-table">
                  <caption className="sr-only">
                    One preview row for every data entry in the uploaded CSV, including invalid entries
                  </caption>
                  <thead>
                    <tr>
                      <th className="sticky top-0 z-10">Row</th>
                      <th className="sticky top-0 z-10">Camera name</th>
                      <th className="sticky top-0 z-10">Camera ID</th>
                      <th className="sticky top-0 z-10">District</th>
                      <th className="sticky top-0 z-10">Coordinates</th>
                      <th className="sticky top-0 z-10">Local roles</th>
                      <th className="sticky top-0 z-10">Validation</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.line}>
                        <td className="mono text-ink-500">{row.line}</td>
                        <td className="font-medium">{row.raw.camera_name || "—"}</td>
                        <td className="mono">{row.raw.external_camera_id || "—"}</td>
                        <td>{row.raw.district || "—"}</td>
                        <td className="mono text-ink-500">
                          {row.raw.latitude || "—"}, {row.raw.longitude || "—"}
                        </td>
                        <td className="text-ink-500">{row.raw.permitted_local_roles || "—"}</td>
                        <td>
                          {row.errors.length === 0 ? (
                            <Pill tone="ok">OK</Pill>
                          ) : (
                            <span className="text-2xs text-bad">{row.errors.join("; ")}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </Card>

        {message && <Notice tone={message.tone}>{message.text}</Notice>}

        {outcomes.length > 0 && (
          <Card
            title="Result"
            action={
              outcomes.some((o) => o.status === "failed") && (
                <button className="btn btn-sm" onClick={downloadFailedOutcomes}>
                  Download failed rows
                </button>
              )
            }
          >
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Row</th>
                    <th>Camera</th>
                    <th>Camera ID</th>
                    <th>Status</th>
                    <th>Request ID</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {outcomes.map((outcome) => (
                    <tr key={`${outcome.line}-${outcome.external_camera_id}`}>
                      <td className="mono text-ink-500">{outcome.line}</td>
                      <td>{outcome.camera_name || "—"}</td>
                      <td className="mono">{outcome.external_camera_id || "—"}</td>
                      <td>
                        {outcome.status === "failed" ? (
                          <Pill tone="bad">failed</Pill>
                        ) : outcome.status === "created+submitted" ? (
                          <Pill tone="ok">submitted</Pill>
                        ) : (
                          <Pill tone="info">draft</Pill>
                        )}
                      </td>
                      <td className="mono">
                        {outcome.request_id ? (
                          <Link
                            className="text-brand-600 hover:underline"
                            href={`/installations/${encodeURIComponent(outcome.request_id)}`}
                          >
                            {outcome.request_id}
                          </Link>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="text-ink-500">{outcome.message ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {rows.length === 0 && outcomes.length === 0 && (
          <EmptyState
            message="Choose a CSV file to preview the rows before creating anything."
            hint="Download the template above to see the expected columns."
          />
        )}
      </div>
    </>
  );
}
