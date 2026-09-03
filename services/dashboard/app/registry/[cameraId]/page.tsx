"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import { VideoPlayer } from "@/components/VideoPlayer";
import { TrafficCount } from "@/components/TrafficCount";
import {
  Card,
  DepartmentTag,
  Field,
  FloatInput,
  FloatTextarea,
  FootageNotice,
  HealthPill,
  InstallationPill,
  Notice,
  PageHeader,
  Pill,
  Spinner,
  SyncPill,
  VideoStatePill,
} from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { calendarDate, coordinates, ist, latency, orDash, relative, titleise } from "@/lib/format";
import type {
  AuditEntry,
  CameraDetail,
  CameraHealthRecord,
  Operator,
  VideoMode,
} from "@/lib/types";

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-x-5 gap-y-3.5 px-4 py-3.5 sm:grid-cols-2 lg:grid-cols-3">{children}</div>;
}

export default function CameraDetailPage() {
  const params = useParams<{ cameraId: string }>();
  const search = useSearchParams();
  const cameraId = decodeURIComponent(params.cameraId);

  const [camera, setCamera] = useState<CameraDetail | null>(null);
  const [health, setHealth] = useState<CameraHealthRecord | null>(null);
  const [history, setHistory] = useState<AuditEntry[]>([]);
  const [operator, setOperator] = useState<Operator | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  // Asking the owning unit for footage. Kept on this page because the decision
  // to ask is made while looking at the camera, not from a separate form.
  const [asking, setAsking] = useState(false);
  const [askReason, setAskReason] = useState("");
  const [askCase, setAskCase] = useState("");
  const [askBusy, setAskBusy] = useState(false);
  const [askResult, setAskResult] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  // Which player, if any, is open. Null means the operator has not asked to
  // watch anything yet - a feed should never start on page load.
  // `?watch=live` arrives from the Watch button in the registry list, so a
  // feed is one click from the table rather than buried behind a page header.
  const requested = search.get("watch");
  const [watching, setWatching] = useState<VideoMode | null>(
    requested === "live" || requested === "playback" ? requested : null,
  );

  const load = useCallback(async () => {
    try {
      const record = await api.camera(cameraId);
      setCamera(record);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [cameraId]);

  async function submitAccessRequest() {
    if (!camera) return;
    setAskBusy(true);
    setAskResult(null);
    try {
      await api.requestVideoAccess({
        camera_id: camera.camera_id,
        reason: askReason.trim(),
        case_id: askCase.trim() || undefined,
      });
      setAsking(false);
      setAskReason("");
      setAskCase("");
      setAskResult({
        tone: "ok",
        text: `Sent to ${camera.owning_department}. Track it under Access requests.`,
      });
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : null;
      setAskResult({
        tone: "bad",
        text: typeof detail === "string" ? detail : err instanceof Error ? err.message : String(err),
      });
    } finally {
      setAskBusy(false);
    }
  }

  useEffect(() => {
    void load();
    api.me().then(setOperator).catch(() => undefined);
    api
      .audit({ resource_id: cameraId, limit: "25" })
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [cameraId, load]);

  const checkHealth = useCallback(async () => {
    setRefreshing(true);
    try {
      setHealth(await api.cameraHealth(cameraId));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, [cameraId, load]);

  if (loading) return <LoadingPanel label="Loading camera record" />;

  if (error && !camera) {
    return (
      <>
        <PageHeader title="Camera record" breadcrumb={[{ label: "Camera registry", href: "/registry" }]} />
        <Notice tone="bad">{error}</Notice>
      </>
    );
  }
  if (!camera) return null;

  const withheld = new Set(camera.redacted_fields);
  const canReadAudit = (operator?.permissions ?? []).includes("audit:read");
  const canReadPolicy = (operator?.permissions ?? []).includes("policy:read");
  const canRequestVideo = (operator?.permissions ?? []).includes("video:request");
  // Drive the buttons from the per-camera decision the API already made, not
  // from the role alone - the same role sees different answers on different
  // cameras, and the server is the one that knows.
  const videoState = camera?.video_access;
  const canWatchLive =
    videoState === "live_and_playback" || videoState === "live_only";
  const canWatchPlayback =
    videoState === "live_and_playback" || videoState === "playback_only";
  const effectiveHealth = health ?? camera.health;

  return (
    <>
      <PageHeader
        breadcrumb={[
          { label: "Camera registry", href: "/registry" },
          { label: camera.camera_id },
        ]}
        title={camera.name}
        subtitle={`${camera.owning_department} · ${camera.location.district} · ${camera.camera_id}`}
        actions={
          <>
            <button className="btn" onClick={checkHealth} disabled={refreshing}>
              {refreshing ? <Spinner /> : null}
              {refreshing ? "Checking…" : "Check health"}
            </button>
            {canWatchLive && (
              <button
                className="btn btn-primary"
                onClick={() => setWatching(watching === "live" ? null : "live")}
              >
                {watching === "live" ? "Close live" : "View live"}
              </button>
            )}
            {canWatchPlayback && (
              <button
                className="btn"
                onClick={() => setWatching(watching === "playback" ? null : "playback")}
              >
                {watching === "playback" ? "Close playback" : "View playback"}
              </button>
            )}
            {canReadPolicy && (
              <Link className="btn" href={`/registry/${encodeURIComponent(camera.camera_id)}/policy`}>
                View access policy
              </Link>
            )}
          </>
        }
      />

      <div className="space-y-3">
        <FootageNotice custodian={camera.owning_department} state={camera.video_access}>
          {!watching && (canWatchLive || canWatchPlayback) && (
            <div className="flex flex-wrap gap-2">
              {canWatchLive && (
                <button className="btn btn-primary" onClick={() => setWatching("live")}>
                  Watch live
                </button>
              )}
              {canWatchPlayback && (
                <button className="btn" onClick={() => setWatching("playback")}>
                  Watch playback
                </button>
              )}
            </div>
          )}
          {camera.video_access === "needs_unit_approval" &&
            (canRequestVideo ? (
              asking ? (
                <div className="space-y-2 rounded border border-line bg-[#f7f8fa] p-3 [--field-bg:#f7f8fa]">
                  <FloatTextarea
                    label="Why do you need this footage?"
                    required
                    rows={2}
                    value={askReason}
                    onChange={(event) => setAskReason(event.target.value)}
                    hint="e.g. Chain-snatching follow-up on the Vasna approach"
                  />
                  <FloatInput
                    label="Case / FIR reference (optional)"
                    className="max-w-xs"
                      value={askCase}
                      onChange={(event) => setAskCase(event.target.value)}
                      hint="FIR 214/2026"
                  />
                  <p className="text-2xs text-ink-500">
                    This reason is shown to {camera.owning_department} and written to the audit
                    trail. It is the record of why the footage was looked at.
                  </p>
                  <div className="flex gap-2">
                    <button
                      className="btn btn-primary"
                      disabled={askBusy || askReason.trim().length < 5}
                      onClick={submitAccessRequest}
                    >
                      {askBusy && <Spinner />} Send request
                    </button>
                    <button className="btn" disabled={askBusy} onClick={() => setAsking(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button className="btn btn-primary" onClick={() => setAsking(true)}>
                  Request access
                </button>
              )
            ) : (
              <p className="text-2xs text-ink-500">
                Your role cannot raise footage requests. An operator in your unit can.
              </p>
            ))}
        </FootageNotice>
        {askResult && <Notice tone={askResult.tone}>{askResult.text}</Notice>}

        {watching && (
          <VideoPlayer
            // Remount when the mode changes. Without this React keeps the
            // component instance, and the open session survives the switch -
            // "live" would happily go on showing a recorded segment.
            key={watching}
            cameraId={camera.camera_id}
            cameraName={camera.name}
            mode={watching}
            retentionDays={camera.technical_summary.retention_days}
            onClosed={() => setWatching(null)}
          />
        )}
        {error && <Notice tone="bad">{error}</Notice>}

        <div className="flex flex-wrap items-center gap-2">
          <DepartmentTag department={camera.owning_department} />
          <InstallationPill status={camera.installation.installation_status} />
          <SyncPill status={camera.vigentra_sync.status} />
          <HealthPill status={effectiveHealth.status} />
          <VideoStatePill state={camera.video_access} />
          <Pill tone="idle">{titleise(camera.camera_type)}</Pill>
          {camera.installation_purpose && <Pill tone="idle">{camera.installation_purpose}</Pill>}
          {camera.visibility_level !== "full" && (
            <Pill tone="warn">{camera.visibility_level} metadata view</Pill>
          )}
        </div>

        {withheld.size > 0 && (
          <Notice tone="warn" title="Some fields are withheld for your role">
            Your role ({operator?.role.replace(/_/g, " ")}) sees a {camera.visibility_level} metadata
            view. Withheld: {Array.from(withheld).map((f) => f.replace(/_/g, " ")).join(", ")}.
          </Notice>
        )}

        {/* What this camera has actually counted. Above the identity block
            because "is it earning its place" is the question people arrive
            with; the serial number is what they look up afterwards. */}
        <TrafficCount cameraId={camera.camera_id} />

        {/* Identity */}
        <Card title="Camera identity">
          <Grid>
            <Field label="Canonical camera ID" mono>
              {camera.camera_id}
            </Field>
            <Field label="Department camera ID" mono>
              {camera.external_camera_id}
            </Field>
            <Field label="Camera name">{camera.name}</Field>
            <Field label="Camera type">{titleise(camera.camera_type)}</Field>
            <Field label="Installation purpose">{orDash(camera.installation_purpose)}</Field>
            <Field label="Serial number" mono redacted={withheld.has("camera_serial_masked")}>
              {orDash(camera.camera_serial_masked)}
            </Field>
            <Field label="Manufacturer" redacted={withheld.has("vendor")}>
              {orDash(camera.vendor)}
            </Field>
            <Field label="Model" redacted={withheld.has("model")}>
              {orDash(camera.model)}
            </Field>
            <Field label="Installation request" mono>
              {camera.installation.installation_request_id ? (
                <Link
                  className="text-brand-600 hover:underline"
                  href={`/installations/${encodeURIComponent(camera.installation.installation_request_id)}`}
                >
                  {camera.installation.installation_request_id}
                </Link>
              ) : (
                "—"
              )}
            </Field>
          </Grid>
        </Card>

        {/* Ownership */}
        <Card title="Ownership and administration">
          <Grid>
            <Field label="Owning department">{camera.owning_department}</Field>
            <Field label="Owning unit">{orDash(camera.owning_unit)}</Field>
            <Field
              label="Police station / zone"
              redacted={withheld.has("police_station_or_zone")}
            >
              {orDash(camera.police_station_or_zone)}
            </Field>
            <Field label="Maintenance agency" redacted={withheld.has("maintenance_agency")}>
              {orDash(camera.maintenance_agency)}
            </Field>
            <Field label="Installation vendor" redacted={withheld.has("installation_vendor")}>
              {orDash(camera.installation_vendor)}
            </Field>
            <Field label="Footage custodian">{camera.access_policy_summary.footage_custodian}</Field>
          </Grid>
          <p className="border-t border-line px-4 py-2 text-2xs text-ink-500">
            Local administrator contact details are held by the owning department and are masked
            before they leave that system — Vigentra never receives them in full.
          </p>
        </Card>

        {/* Location */}
        <Card title="Location and coverage">
          <Grid>
            <Field label="District">{camera.location.district}</Field>
            <Field label="Road / junction">{orDash(camera.location.road_or_junction)}</Field>
            <Field label="Landmark" redacted={withheld.has("address_or_landmark")}>
              {orDash(camera.location.landmark)}
            </Field>
            <Field label="View direction">{titleise(camera.location.view_direction)}</Field>
            <Field label="Coordinates (demonstration)" mono>
              {coordinates(camera.location.latitude, camera.location.longitude)}
            </Field>
            <Field label="Coverage" redacted={withheld.has("coverage_description")}>
              {orDash(camera.coverage_description)}
            </Field>
            <Field label="Entry / exit zone" redacted={withheld.has("entry_exit_zone_description")}>
              {orDash(camera.entry_exit_zone_description)}
            </Field>
          </Grid>
        </Card>

        {/* Technical */}
        <Card title="Technical summary">
          <Grid>
            <Field label="Feed type (department side)">
              {titleise(camera.technical_summary.source_type ?? camera.source_type)}
            </Field>
            <Field label="VMS">{orDash(camera.vms_name)}</Field>
            <Field label="Resolution">{orDash(camera.technical_summary.resolution)}</Field>
            <Field label="Frame rate">
              {camera.technical_summary.fps ? `${camera.technical_summary.fps} fps` : "—"}
            </Field>
            <Field label="Codec">{orDash(camera.technical_summary.codec)}</Field>
            <Field label="Local retention">
              {camera.technical_summary.retention_days
                ? `${camera.technical_summary.retention_days} days`
                : "—"}
            </Field>
            <Field label="Timezone">{camera.technical_summary.timezone}</Field>
          </Grid>
          <p className="border-t border-line px-4 py-2 text-2xs text-ink-500">
            These describe how the <strong>owning department</strong> records and retains footage.
            Vigentra stores the description; it holds no address, credential or stream for any of it.
          </p>
        </Card>

        {/* Installation and approval */}
        <div className="grid gap-3 lg:grid-cols-2">
          <Card title="Installation and commissioning">
            <Grid>
              <Field label="Installed on">{calendarDate(camera.installation.installation_date)}</Field>
              <Field label="Commissioned on">
                {calendarDate(camera.installation.commissioning_date)}
              </Field>
              <Field label="Installation status">
                <InstallationPill status={camera.installation.installation_status} />
              </Field>
            </Grid>
          </Card>

          <Card title="Approval history">
            <Grid>
              <Field label="Approval status">
                <SyncPill status={camera.approval.status} />
              </Field>
              <Field label="Approved by role">{orDash(camera.approval.approved_by_role)}</Field>
              <Field label="Approved at">{ist(camera.approval.approved_at)}</Field>
            </Grid>
          </Card>
        </div>

        {/* Health and sync */}
        <div className="grid gap-3 lg:grid-cols-2">
          <Card title="Current health">
            <Grid>
              <Field label="Status">
                <HealthPill status={effectiveHealth.status} />
              </Field>
              <Field label="Last heartbeat">{relative(effectiveHealth.last_heartbeat_utc)}</Field>
              <Field label="Last frame">{relative(effectiveHealth.last_frame_utc)}</Field>
              <Field label="Latency">{latency(effectiveHealth.latency_ms)}</Field>
              <Field label="Reconnects">{orDash(effectiveHealth.reconnect_count)}</Field>
              <Field label="Checked at">{ist(health?.checked_at ?? null)}</Field>
            </Grid>
            {health && Object.keys(health.detail).length > 0 && (
              <div className="grid gap-x-5 gap-y-2 border-t border-line px-4 py-2.5 sm:grid-cols-3">
                {Object.entries(health.detail).map(([key, value]) => (
                  <div key={key} className="min-w-0">
                    <div className="field-label">{key.replace(/_/g, " ")}</div>
                    <div className="truncate text-2xs text-ink-700">{String(value)}</div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card title="Metadata synchronisation">
            <Grid>
              <Field label="Sync status">
                <SyncPill status={camera.vigentra_sync.status} />
              </Field>
              <Field label="Last synchronised">{ist(camera.vigentra_sync.synced_at_utc)}</Field>
              <Field label="First recorded">{ist(camera.first_synced_at)}</Field>
              <Field label="Source system">{camera.source_system}</Field>
              <Field label="Adapter">
                {orDash(camera.provenance?.adapter as string)}{" "}
                {camera.provenance?.adapter_version ? `v${camera.provenance.adapter_version}` : ""}
              </Field>
              <Field label="Payload">metadata only</Field>
            </Grid>
          </Card>
        </div>

        {/* Access policy summary */}
        <Card
          title="Access policy summary"
          action={
            canReadPolicy && (
              <Link
                className="text-2xs font-medium text-brand-600 hover:underline"
                href={`/registry/${encodeURIComponent(camera.camera_id)}/policy`}
              >
                Full policy →
              </Link>
            )
          }
        >
          <Grid>
            <Field label="Video access through Vigentra">
              {/* The owner's switch and this reader's own decision are two
                  different facts, and conflating them is how the page used to
                  claim NOT AVAILABLE at an operator who could watch. */}
              <div className="flex flex-wrap items-center gap-1.5">
                <Pill tone={camera.access_policy_summary.vigentra_video_access ? "ok" : "idle"}>
                  {camera.access_policy_summary.vigentra_video_access
                    ? "brokering permitted by owner"
                    : "brokering not permitted by owner"}
                </Pill>
                <VideoStatePill state={camera.video_access} />
              </div>
            </Field>
            <Field label="Local VMS video access">
              <Pill tone={camera.access_policy_summary.local_video_access ? "ok" : "idle"}>
                {camera.access_policy_summary.local_video_access ? "enabled" : "disabled"}
              </Pill>
            </Field>
            <Field label="Permitted local roles">
              {camera.access_policy_summary.permitted_local_roles.length
                ? camera.access_policy_summary.permitted_local_roles
                    .map((role) => role.replace(/_/g, " "))
                    .join(", ")
                : "—"}
            </Field>
          </Grid>
        </Card>

        {/* Attachments */}
        <Card title="Installation documents">
          {withheld.has("attachments") ? (
            <p className="px-4 py-4 text-[13px] italic text-ink-400">
              Document references are withheld for your role.
            </p>
          ) : camera.attachments.length === 0 ? (
            <p className="px-4 py-4 text-[13px] text-ink-500">
              No document references recorded for this camera.
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
                {camera.attachments.map((item) => (
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
          <p className="border-t border-line px-4 py-2 text-2xs text-ink-500">
            Vigentra records document <strong>references</strong> only. The documents themselves stay
            with the owning department, and no CCTV footage is ever attached to a registry record.
          </p>
        </Card>

        {/* Audit history */}
        {canReadAudit && (
          <Card title="Audit history for this camera">
            {history.length === 0 ? (
              <p className="px-4 py-4 text-[13px] text-ink-500">No recorded activity yet.</p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Time (IST)</th>
                    <th>User</th>
                    <th>Action</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((entry) => (
                    <tr key={entry.audit_id}>
                      <td className="whitespace-nowrap text-ink-500">{ist(entry.timestamp_utc)}</td>
                      <td>{entry.username}</td>
                      <td>{entry.action.replace(/_/g, " ")}</td>
                      <td>{entry.outcome}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        )}
      </div>
    </>
  );
}
