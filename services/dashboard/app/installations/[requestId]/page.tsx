"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  DepartmentTag,
  FloatTextarea,
  FootageNotice,
  Notice,
  PageHeader,
  Pill,
  RequestStatusPill,
  Spinner,
} from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { calendarDate, coordinates, ist, orDash, titleise } from "@/lib/format";
import type { InstallationRequest, Operator, RequestStatus } from "@/lib/types";

type ActionKind = "submit" | "suspend" | "decommission" | "sync";

const ACTION_LABEL: Record<ActionKind, string> = {
  submit: "Submit for validation",
  suspend: "Suspend",
  decommission: "Decommission",
  sync: "Synchronise metadata",
};

// A record leaves the operator's hands the moment it registers. There is no
// rejected state to come back from any more - validation failure is the only
// way a submitted form returns for editing.
const EDITABLE_STATUSES: RequestStatus[] = ["DRAFT", "VALIDATION_FAILED"];

interface ActionPrompt {
  kind: ActionKind;
  requiresReason: boolean;
}

/** A form field. */
function Show({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className={wide ? "sm:col-span-2 lg:col-span-3" : ""}>
      <div className="field-label">{label}</div>
      <div className="field-value break-words">{children}</div>
    </div>
  );
}

function formValue(form: Record<string, unknown>, key: string): string {
  const value = form[key];
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

export default function InstallationRequestPage() {
  const params = useParams<{ requestId: string }>();
  const requestId = decodeURIComponent(params.requestId);

  const [record, setRecord] = useState<InstallationRequest | null>(null);
  const [operator, setOperator] = useState<Operator | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "warn" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState<ActionPrompt | null>(null);
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    try {
      setRecord(await api.installationRequest(requestId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [requestId]);

  useEffect(() => {
    void load();
    api.me().then(setOperator).catch(() => undefined);
  }, [load]);

  const permissions = operator?.permissions ?? [];
  const sameDepartment =
    operator?.department === "*" || operator?.department === record?.owning_department;
  const canEdit =
    record !== null &&
    EDITABLE_STATUSES.includes(record.status) &&
    permissions.includes("installation:update") &&
    sameDepartment &&
    (operator?.role !== "installation_operator" ||
      record.created_by === null ||
      record.created_by === operator?.username);
  const canSubmit = canEdit && permissions.includes("installation:submit");
  const canSuspend =
    record !== null &&
    ["REGISTERED", "SYNCHRONIZED"].includes(record.status) &&
    permissions.includes("installation:suspend") &&
    sameDepartment;
  const canDecommission =
    record !== null &&
    ["REGISTERED", "SYNCHRONIZED", "SUSPENDED"].includes(record.status) &&
    permissions.includes("installation:decommission") &&
    sameDepartment;
  const canSync =
    record !== null &&
    ["REGISTERED", "SYNCHRONIZED"].includes(record.status) &&
    permissions.includes("installation:sync") &&
    sameDepartment;

  async function act(kind: ActionKind, actionReason?: string) {
    if (!record) return;
    setBusy(true);
    setNotice(null);
    try {
      let updated: InstallationRequest;
      switch (kind) {
        case "submit":
          updated = await api.submitRequest(record.request_id);
          break;
        case "suspend":
          updated = await api.suspendRequest(record.request_id, actionReason ?? "");
          break;
        case "decommission":
          updated = await api.decommissionRequest(record.request_id, actionReason ?? "");
          break;
        case "sync":
          updated = await api.syncRequestMetadata(record.request_id);
          break;
      }
      setRecord(updated);
      if (kind === "submit" && updated.validation_errors.length > 0) {
        setNotice({
          tone: "bad",
          text: `Validation failed — ${updated.validation_errors.join("; ")}. Correct the fields and resubmit.`,
        });
      } else if (kind === "submit") {
        setNotice({
          tone: "ok",
          text:
            "Registered. This camera is now in the central registry and visible " +
            "to other units. Footage stays with your unit — other units must " +
            "request it, and you decide.",
        });
      } else {
        setNotice({ tone: "ok", text: `${ACTION_LABEL[kind]} succeeded.` });
      }
    } catch (err) {
      const message =
        err instanceof ApiError && typeof err.detail === "object" && err.detail
          ? (err.detail as { message?: string }).message ?? err.message
          : err instanceof Error
            ? err.message
            : String(err);
      setNotice({ tone: "bad", text: message });
    } finally {
      setBusy(false);
      setPrompt(null);
      setReason("");
    }
  }

  if (loading) return <LoadingPanel label="Loading installation record" />;

  if (!record) {
    return (
      <>
        <PageHeader
          title="Installation record"
          breadcrumb={[{ label: "Installation requests", href: "/installations" }]}
        />
        <Notice tone="bad">{error ?? "Record unavailable"}</Notice>
      </>
    );
  }

  const actions: React.ReactNode[] = [];
  if (canEdit) {
    actions.push(
      <Link
        key="edit"
        className="btn"
        href={`/installations/${encodeURIComponent(record.request_id)}?edit=1`}
        onClick={(event) => {
          event.preventDefault();
          setNotice({
            tone: "warn",
            text:
              "Editing existing drafts from the console is not implemented in this demo build. " +
              "Raise a new form from the department system or through “New CCTV installation”.",
          });
        }}
      >
        Edit draft
      </Link>,
    );
  }
  if (canSubmit) actions.push(<button key="submit" className="btn btn-primary" onClick={() => act("submit")} disabled={busy}>{busy && <Spinner />} Submit &amp; register</button>);
  if (canSync) actions.push(<button key="sync" className="btn" onClick={() => act("sync")} disabled={busy}>{busy && <Spinner />} Synchronise metadata</button>);
  if (canSuspend) actions.push(<button key="suspend" className="btn" onClick={() => setPrompt({ kind: "suspend", requiresReason: false })} disabled={busy}>Suspend…</button>);
  if (canDecommission) actions.push(<button key="decommission" className="btn btn-danger" onClick={() => setPrompt({ kind: "decommission", requiresReason: false })} disabled={busy}>Decommission…</button>);

  return (
    <>
      <PageHeader
        breadcrumb={[
          { label: "Installation requests", href: "/installations" },
          { label: record.request_id },
        ]}
        title={record.camera_name ?? record.request_id}
        subtitle={`${record.owning_department} · ${record.owning_unit ?? ""} · ${record.request_id}`}
        actions={<div className="flex flex-wrap gap-2">{actions}</div>}
      />

      <div className="space-y-3">
        <FootageNotice compact custodian={record.owning_department} />
        {error && <Notice tone="bad">{error}</Notice>}
        {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}

        <div className="flex flex-wrap items-center gap-2">
          <DepartmentTag department={record.owning_department} />
          <RequestStatusPill status={record.status} />
          {record.validation_errors.length > 0 && (
            <Pill tone="bad">{record.validation_errors.length} validation issue(s)</Pill>
          )}
          <span className="text-2xs text-ink-500">Updated {ist(record.updated_at)}</span>
        </div>

        {record.validation_errors.length > 0 && (
          <Notice tone="bad" title="Returned by the department system">
            <ul className="list-disc space-y-0.5 pl-4">
              {record.validation_errors.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
            <div className="mt-1 text-2xs text-ink-500">
              Correct these fields and submit again. The record registers as soon
              as they pass.
            </div>
          </Notice>
        )}
        {record.withdrawal_reason && (
          <Notice tone="warn" title="Withdrawn">
            {record.withdrawal_reason}
          </Notice>
        )}
        {record.validation_errors.length > 0 && (
          <Notice tone="bad" title="Validation issues raised by the department system">
            <ul className="ml-4 mt-1 list-disc space-y-0.5">
              {record.validation_errors.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          </Notice>
        )}

        {prompt && (
          <Card title={`${ACTION_LABEL[prompt.kind]} — ${record.request_id}`}>
            <div className="space-y-2.5 px-4 py-3">
              <FloatTextarea
                label="Reason"
                required={prompt.requiresReason}
                rows={3}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                hint={
                  prompt.kind === "decommission"
                    ? "Why is this camera being retired? Written to the audit trail and shown to the raiser."
                    : "Written to the audit trail and shown to the raiser."
                }
              />
              <div className="flex justify-end gap-2">
                <button className="btn" onClick={() => setPrompt(null)} disabled={busy}>
                  Cancel
                </button>
                <button
                  className={`btn ${prompt.kind === "decommission" ? "btn-danger" : "btn-primary"}`}
                  disabled={busy || (prompt.requiresReason && reason.trim().length < 5)}
                  onClick={() => act(prompt.kind, reason.trim())}
                >
                  {busy && <Spinner />}
                  Confirm {ACTION_LABEL[prompt.kind].toLowerCase()}
                </button>
              </div>
            </div>
          </Card>
        )}

        {/* Timeline */}
        <Card title="Lifecycle">
          <div className="grid gap-x-5 gap-y-3.5 px-4 py-3.5 sm:grid-cols-2 lg:grid-cols-3">
            <Show label="Request ID">
              <span className="mono">{record.request_id}</span>
            </Show>
            <Show label="Source system">{record.source_system}</Show>
            <Show label="Current status">
              <RequestStatusPill status={record.status} />
            </Show>
            <Show label="Created by">{orDash(record.created_by)}</Show>
            <Show label="Created at">{ist(record.created_at)}</Show>
            <Show label="Submitted by">{orDash(record.submitted_by)}</Show>
            <Show label="Submitted at">{ist(record.submitted_at)}</Show>
            <Show label="Registered at">{ist(record.approved_at)}</Show>
            <Show label="Synchronised to Sentinel">{ist(record.synchronized_at)}</Show>
          </div>
        </Card>

        {/* Camera identity */}
        <Card title="Camera identity">
          <div className="grid gap-x-5 gap-y-3.5 px-4 py-3.5 sm:grid-cols-2 lg:grid-cols-3">
            <Show label="Camera name">{formValue(record.form, "camera_name")}</Show>
            <Show label="Department camera ID">
              <span className="mono">{formValue(record.form, "external_camera_id")}</span>
            </Show>
            <Show label="Camera type">{titleise(formValue(record.form, "camera_type"))}</Show>
            <Show label="Installation purpose">{formValue(record.form, "installation_purpose")}</Show>
            <Show label="Manufacturer">{formValue(record.form, "camera_vendor")}</Show>
            <Show label="Model">{formValue(record.form, "camera_model")}</Show>
            <Show label="Serial number (masked)">
              <span className="mono">{formValue(record.form, "camera_serial_number")}</span>
            </Show>
          </div>
        </Card>

        {/* Ownership + location */}
        <Card title="Ownership and location">
          <div className="grid gap-x-5 gap-y-3.5 px-4 py-3.5 sm:grid-cols-2 lg:grid-cols-3">
            <Show label="Owning department">{formValue(record.form, "owning_department")}</Show>
            <Show label="Owning unit">{formValue(record.form, "owning_unit")}</Show>
            <Show label="District">{formValue(record.form, "district")}</Show>
            <Show label="Police station / zone">
              {formValue(record.form, "police_station_or_zone")}
            </Show>
            <Show label="Local admin contact">
              <span className="mono">
                {formValue(record.form, "local_admin_contact_masked")}
              </span>
            </Show>
            <Show label="Maintenance agency">{formValue(record.form, "maintenance_agency")}</Show>
            <Show label="Installation vendor">{formValue(record.form, "installation_vendor")}</Show>
            <Show label="Road / junction">{formValue(record.form, "road_or_junction")}</Show>
            <Show label="Address / landmark">{formValue(record.form, "address_or_landmark")}</Show>
            <Show label="View direction">
              {titleise(formValue(record.form, "view_direction"))}
            </Show>
            <Show label="Coordinates" wide>
              <span className="mono">
                {coordinates(
                  Number(record.form.latitude ?? NaN) || null,
                  Number(record.form.longitude ?? NaN) || null,
                )}
              </span>
            </Show>
            <Show label="Coverage" wide>
              {formValue(record.form, "coverage_description")}
            </Show>
            <Show label="Entry / exit zone" wide>
              {formValue(record.form, "entry_exit_zone_description")}
            </Show>
          </div>
        </Card>

        {/* Technical + local capability */}
        <Card title="Technical metadata">
          <div className="grid gap-x-5 gap-y-3.5 px-4 py-3.5 sm:grid-cols-2 lg:grid-cols-3">
            <Show label="Feed type">{formValue(record.form, "source_type")}</Show>
            <Show label="VMS">{formValue(record.form, "vms_name")}</Show>
            <Show label="VMS vendor">{formValue(record.form, "vms_vendor")}</Show>
            <Show label="Resolution">{formValue(record.form, "resolution")}</Show>
            <Show label="Frame rate">
              {record.form.fps ? `${record.form.fps} fps` : "—"}
            </Show>
            <Show label="Codec">{formValue(record.form, "codec")}</Show>
            <Show label="Local retention">
              {record.form.retention_days ? `${record.form.retention_days} days` : "—"}
            </Show>
            <Show label="Timezone">{formValue(record.form, "timezone")}</Show>
            <Show label="Installed on">{calendarDate(formValue(record.form, "installation_date"))}</Show>
            <Show label="Commissioned on">
              {calendarDate(formValue(record.form, "commissioning_date"))}
            </Show>
            <Show label="Local live capability">
              {record.form.supports_live ? "Enabled locally" : "Disabled locally"}
            </Show>
            <Show label="Local playback capability">
              {record.form.supports_playback ? "Enabled locally" : "Disabled locally"}
            </Show>
          </div>
        </Card>

        {/* Access policy */}
        <Card title="Local access policy declared on this form">
          <div className="px-4 py-3.5">
            <Show label="Roles permitted to view footage in the owning department's VMS">
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {Array.isArray(record.form.permitted_local_roles) && record.form.permitted_local_roles.length > 0
                  ? (record.form.permitted_local_roles as string[]).map((role) => (
                      <Pill key={role} tone="idle">
                        {role.replace(/_/g, " ")}
                      </Pill>
                    ))
                  : "—"}
              </div>
            </Show>
            <div className="mt-3 border-t border-line pt-3">
              <FootageNotice compact custodian={record.owning_department} />
            </div>
          </div>
        </Card>

        {/* Attachments */}
        <Card title="Supporting document references">
          {record.attachments.length === 0 ? (
            <p className="px-4 py-4 text-[13px] text-ink-500">
              No documents recorded for this record.
            </p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Document type</th>
                  <th>Reference</th>
                  <th>Filename</th>
                  <th>Custodian</th>
                </tr>
              </thead>
              <tbody>
                {record.attachments.map((item) => (
                  <tr key={item.reference}>
                    <td>{titleise(item.document_type)}</td>
                    <td className="mono">{item.reference}</td>
                    <td className="text-ink-500">{orDash(item.filename)}</td>
                    <td className="text-ink-500">{orDash(item.custodian)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </>
  );
}
