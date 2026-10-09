"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  Card,
  DepartmentTag,
  EmptyState,
  FloatSelect,
  Notice,
  PageHeader,
  Pill,
  Spinner,
} from "@/components/ui";
import { ChallanDialog } from "@/components/ChallanDialog";
import { api } from "@/lib/api";
import { useBackCloses } from "@/lib/backCloses";
import { humanise, ist, relative } from "@/lib/format";
import type { Incident } from "@/lib/types";

/**
 * The incident review queue.
 *
 * Unlike the detections page, this loads on mount and is meant to be watched:
 * a wrong-way vehicle or a stopped car in a live lane is a safety event, not a
 * record to be browsed on request. But every row is a CANDIDATE, never a
 * finding — the edge matched a pattern in the tracking, and a person decides
 * what it was. The thresholds behind these are reasoned, not validated against
 * real incident footage, and the page says so rather than dressing a guess as
 * a result. Confirming or dismissing a candidate is the human-in-the-loop the
 * whole design exists to keep.
 */

const KIND_LABEL: Record<string, string> = {
  WRONG_WAY: "Wrong way",
  COLLISION_CANDIDATE: "Possible collision",
  PERSON_ON_CARRIAGEWAY: "Person in traffic",
  INTRUSION: "Intrusion in a restricted zone",
  CROWD_GATHERING: "Crowd gathering",
  NO_HELMET: "Rider without a helmet",
};

type Tone = "ok" | "warn" | "bad" | "idle" | "info";

const SEVERITY_TONE: Record<string, Tone> = {
  HIGH: "bad",
  MEDIUM: "warn",
  LOW: "idle",
};

const STATUS_TONE: Record<string, Tone> = {
  CANDIDATE: "info",
  REVIEWING: "warn",
  CONFIRMED: "bad",
  DISMISSED: "idle",
};

/** Offences: Confirm opens the e-challan popup (central routers/challans.py). */
const FINES: Record<string, { amount: string; law: string }> = {
  // shown on the card; the challan itself takes its figures from central (services/challan_service.py)
  NO_HELMET: { amount: "₹1,000", law: "MV Act s.129 r/w s.194D" },
  WRONG_WAY: { amount: "₹1,000", law: "MV Act s.184" },
};

const clock = (utc: string) =>
  new Date(utc).toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false });
const day = (utc: string) =>
  new Date(utc).toLocaleDateString("en-GB", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });
const CHALLAN_KINDS = new Set(["NO_HELMET", "WRONG_WAY"]);

export default function IncidentsPage() {
  const [rows, setRows] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sinceHours, setSinceHours] = useState("24");
  const [acting, setActing] = useState<string | null>(null);
  const [challanFor, setChallanFor] = useState<Incident | null>(null);
  const [zoom, setZoom] = useState<Incident | null>(null);
  useBackCloses(zoom !== null, () => setZoom(null));
  useEffect(() => {
    if (!zoom) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setZoom(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoom]);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRows(
        await api.incidents({
          kind: kind || undefined,
          status: statusFilter || undefined,
          since_hours: sinceHours,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [kind, statusFilter, sinceHours]);

  useEffect(() => {
    void load();
  }, [load]);

  const review = useCallback(
    async (incidentId: string, status: string) => {
      setActing(incidentId);
      try {
        const updated = await api.reviewIncident(incidentId, status);
        setRows((prev) =>
          (prev ?? []).map((r) => (r.incident_id === incidentId ? updated : r)),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setActing(null);
      }
    },
    [],
  );

  const open = (rows ?? []).filter((r) => r.status === "CANDIDATE" || r.status === "REVIEWING");

  return (
    <div className="space-y-4">
      <PageHeader
        title="Incident review"
        subtitle="Traffic events detected by the cameras, for review."
      />

      <Notice tone="info">
        These are matched from vehicle motion alone (no extra model, no imagery). The
        thresholds are reasoned, <strong>not validated against real incident footage</strong>,
        so treat every row as “worth a look”, not “what happened”.
      </Notice>

      <Card>
        <div className="flex flex-wrap items-start gap-3 px-6 py-5">
          <FloatSelect label="Type" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">All types</option>
            {Object.entries(KIND_LABEL).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </FloatSelect>
          <FloatSelect
            label="Status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">All</option>
            <option value="CANDIDATE">Awaiting review</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="DISMISSED">Dismissed</option>
          </FloatSelect>
          <FloatSelect
            label="Window"
            value={sinceHours}
            onChange={(e) => setSinceHours(e.target.value)}
          >
            <option value="1">Last hour</option>
            <option value="24">Last 24 hours</option>
            <option value="168">Last 7 days</option>
            <option value="720">Last 30 days</option>
          </FloatSelect>
          <div className="ml-auto text-caption leading-[3rem] text-muted">
            {rows ? `${open.length} awaiting review · ${rows.length} shown` : "loading…"}
          </div>
        </div>
      </Card>

      {error && <Notice tone="bad">{error}</Notice>}

      {!rows && <div className="p-8 text-center"><Spinner /></div>}

      {rows && rows.length === 0 && (
        <EmptyState
          message="Nothing raised in this window"
          hint="The detector stays quiet on ordinary traffic by design. When it fires, the candidate appears here."
        />
      )}

      <div className="space-y-3">
        {(rows ?? []).map((incident) => {
          const reviewed = incident.status === "CONFIRMED" || incident.status === "DISMISSED";
          const label = KIND_LABEL[incident.kind] ?? humanise(incident.kind);
          const score = typeof incident.evidence?.no_helmet_score === "number" ? (incident.evidence.no_helmet_score as number) : null;
          const views = Array.isArray(incident.evidence?.view_scores) ? (incident.evidence.view_scores as unknown[]).length : null;
          const fine = FINES[incident.kind];
          const busy = acting === incident.incident_id;
          return (
            <Card key={incident.incident_id}>
              <div className="flex flex-col gap-6 p-5 md:flex-row">
                {/* The frame it was raised on: the thing a reviewer judges, so it leads. */}
                <button
                  type="button"
                  disabled={!incident.has_snapshot}
                  onClick={() => setZoom(incident)}
                  className="flex aspect-[16/10] w-full shrink-0 items-center justify-center overflow-hidden rounded-sm bg-canvas-soft md:w-[360px]"
                  title={incident.has_snapshot ? "Enlarge the frame" : undefined}
                >
                  {incident.has_snapshot ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      alt={`Frame of ${label}`}
                      className="h-full w-full object-contain"
                      loading="lazy"
                      src={api.incidentSnapshotUrl(incident.incident_id)}
                    />
                  ) : (
                    <span className="text-caption text-muted">No frame saved</span>
                  )}
                </button>

                <div className="flex min-w-0 flex-1 flex-col gap-2">
                  <div className="flex flex-wrap items-center gap-2 text-caption text-muted">
                    <Pill tone={SEVERITY_TONE[incident.severity] ?? "idle"}>{humanise(incident.severity)}</Pill>
                    <Pill tone={STATUS_TONE[incident.status] ?? "idle"}>{humanise(incident.status)}</Pill>
                    {incident.owning_department && <DepartmentTag department={incident.owning_department} />}
                  </div>

                  <h2 className="text-h4 text-ink">
                    {label}
                    <span className="text-muted"> at </span>
                    <Link className="hover:underline" href={`/registry/${encodeURIComponent(incident.camera_id)}`}>
                      {incident.camera_name ?? incident.camera_id}
                    </Link>
                  </h2>

                  {score != null && (
                    <div className="flex items-center gap-2 text-body-sm">
                      <span className="font-semibold text-ink">{score.toFixed(2)}</span>
                      <span className="flex gap-0.5" aria-label={`model confidence ${Math.round(score * 100)} %`}>
                        {[0.2, 0.4, 0.6, 0.8, 1].map((step) => (
                          <span key={step} className={`h-2 w-5 rounded-full ${score >= step - 0.1 ? "bg-ink" : "bg-hairline"}`} />
                        ))}
                      </span>
                      <span className="text-muted">model confidence{views ? ` over ${views} view${views > 1 ? "s" : ""}` : ""}</span>
                    </div>
                  )}

                  <div className="flex flex-wrap items-baseline gap-x-3">
                    <span className="text-h3 tabular-nums text-ink">{clock(incident.last_seen_utc)}</span>
                    <span className="text-body-sm text-muted">
                      {day(incident.last_seen_utc)} · {relative(incident.last_seen_utc)}
                    </span>
                  </div>

                  {incident.suggested_plate && (
                    <div className="text-body-sm text-ink-soft">
                      Plate read{" "}
                      {CHALLAN_KINDS.has(incident.kind) && !reviewed ? (
                        <button
                          type="button"
                          className="mono font-semibold text-ink underline decoration-dotted underline-offset-4 hover:decoration-solid"
                          title="Open the challan with this plate filled in"
                          onClick={() => setChallanFor(incident)}
                        >
                          {incident.suggested_plate}
                        </button>
                      ) : (
                        <span className="mono font-semibold text-ink">{incident.suggested_plate}</span>
                      )}{" "}
                      <span className="text-muted">
                        · {incident.suggested_plate_confirmed ? "confirmed by the reader" : "unconfirmed"}
                        {incident.suggested_plate_confidence != null && ` · score ${incident.suggested_plate_confidence.toFixed(2)}`}
                        {" "}· check before issuing
                      </span>
                    </div>
                  )}

                  {fine && (
                    <div className="text-body-sm text-ink-soft">
                      Fine if confirmed <strong>{fine.amount}</strong> <span className="text-muted">· {fine.law}</span>
                    </div>
                  )}

                  <p className="max-w-2xl text-body-sm text-muted">{incident.reason}</p>

                  {incident.reviewed_by && (
                    <div className="text-caption text-muted">
                      Reviewed by {incident.reviewed_by} · {ist(incident.reviewed_at)}
                      {incident.review_note ? ` · ${incident.review_note}` : ""}
                    </div>
                  )}

                  {!reviewed && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <button
                        className="btn btn-lg btn-primary min-w-[16rem]"
                        disabled={busy}
                        onClick={() =>
                          CHALLAN_KINDS.has(incident.kind)
                            ? setChallanFor(incident)
                            : review(incident.incident_id, "CONFIRMED")
                        }
                      >
                        {CHALLAN_KINDS.has(incident.kind) ? "Confirm & issue challan" : "Confirm"}
                      </button>
                      <button
                        className="btn btn-lg"
                        disabled={busy}
                        onClick={() => review(incident.incident_id, "DISMISSED")}
                      >
                        Dismiss
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      {zoom && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setZoom(null)}
          role="dialog"
          aria-label="Incident frame"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            alt={`Frame of ${KIND_LABEL[zoom.kind] ?? zoom.kind}`}
            className="max-h-full max-w-full rounded-sm object-contain"
            src={api.incidentSnapshotUrl(zoom.incident_id)}
          />
          <button className="btn absolute right-4 top-4" onClick={() => setZoom(null)}>
            Close (Esc)
          </button>
        </div>
      )}

      {challanFor && (
        <ChallanDialog
          incident={challanFor}
          snapshotUrl={challanFor.has_snapshot ? api.incidentSnapshotUrl(challanFor.incident_id) : null}
          onClose={() => setChallanFor(null)}
          onIssued={(c) =>
            setRows((prev) =>
              (prev ?? []).map((r) =>
                r.incident_id === challanFor.incident_id
                  ? { ...r, status: "CONFIRMED", review_note: `Challan ${c.challan_no} to ${c.plate}` }
                  : r,
              ),
            )
          }
        />
      )}
    </div>
  );
}
