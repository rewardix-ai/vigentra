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
import { api } from "@/lib/api";
import { ist, relative } from "@/lib/format";
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
  STOPPED_IN_LANE: "Stopped in lane",
  SUDDEN_STOP: "Sudden stop",
  COLLISION_CANDIDATE: "Possible collision",
  PERSON_ON_CARRIAGEWAY: "Person in traffic",
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

export default function IncidentsPage() {
  const [rows, setRows] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sinceHours, setSinceHours] = useState("24");
  const [acting, setActing] = useState<string | null>(null);

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
    <div className="space-y-3">
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
        <div className="flex flex-wrap items-end gap-3 px-4 py-3">
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
          <div className="ml-auto text-2xs text-ink-500">
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

      <div className="space-y-2">
        {(rows ?? []).map((incident) => {
          const reviewed = incident.status === "CONFIRMED" || incident.status === "DISMISSED";
          return (
            <Card key={incident.incident_id}>
              <div className="flex flex-wrap items-start gap-3 px-4 py-3">
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={SEVERITY_TONE[incident.severity] ?? "idle"}>
                      {incident.severity}
                    </Pill>
                    <span className="text-[13px] font-semibold text-ink-900">
                      {KIND_LABEL[incident.kind] ?? incident.kind}
                    </span>
                    <Pill tone={STATUS_TONE[incident.status] ?? "idle"}>{incident.status}</Pill>
                    {incident.owning_department && (
                      <DepartmentTag department={incident.owning_department} />
                    )}
                  </div>

                  <div className="text-2xs text-ink-600">
                    <Link
                      className="mono hover:underline"
                      href={`/registry/${encodeURIComponent(incident.camera_id)}`}
                    >
                      {incident.camera_name ?? incident.camera_id}
                    </Link>
                    {" · "}
                    {relative(incident.last_seen_utc)}
                    {incident.duration_s != null && ` · over ${incident.duration_s.toFixed(1)}s`}
                    {incident.track_ids.length > 0 &&
                      ` · vehicle track ${incident.track_ids.join(", ")}`}
                  </div>

                  <p className="text-2xs leading-relaxed text-ink-500">{incident.reason}</p>

                  {Object.keys(incident.evidence ?? {}).length > 0 && (
                    <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-ink-400">
                      {Object.entries(incident.evidence)
                        .filter(([k]) => k !== "vehicle" && k !== "vehicles")
                        .map(([k, v]) => (
                          <span key={k} className="mono">
                            {k.replace(/_/g, " ")}: {String(v)}
                          </span>
                        ))}
                    </div>
                  )}

                  {incident.reviewed_by && (
                    <div className="text-[10px] text-ink-400">
                      reviewed by {incident.reviewed_by} · {ist(incident.reviewed_at)}
                    </div>
                  )}
                </div>

                {!reviewed && (
                  <div className="flex shrink-0 gap-2">
                    <button
                      className="btn btn-sm"
                      disabled={acting === incident.incident_id}
                      onClick={() => review(incident.incident_id, "DISMISSED")}
                    >
                      Dismiss
                    </button>
                    <button
                      className="btn btn-sm btn-primary"
                      disabled={acting === incident.incident_id}
                      onClick={() => review(incident.incident_id, "CONFIRMED")}
                    >
                      Confirm
                    </button>
                  </div>
                )}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
