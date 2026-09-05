"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import {
  Card,
  DepartmentTag,
  FloatInput,
  FloatSelect,
  Notice,
  PageHeader,
  Pill,
  Spinner,
} from "@/components/ui";
import { api } from "@/lib/api";
import { footageTime, ist, relative } from "@/lib/format";
import type { Detection, DetectorHealth } from "@/lib/types";

/**
 * What the edge workers saw — on request, and not before.
 *
 * The page deliberately does not load anything on mount, and shows no running
 * totals. A surveillance record is not a dashboard metric to glance at: you
 * come here with a question, state it as a filter, and get the rows that
 * answer it. Idle browsing of who was where is exactly the habit this system
 * should not encourage, and a live counter ticking up invites it.
 *
 * Detections are department-scoped, not registry-scoped: "two motorcycles and
 * a person at this junction at 21:14" describes a place at a time, so it
 * follows the footage rules rather than the asset-record ones.
 */

const CLASSES = ["person", "car", "motorcycle", "bus", "truck", "bicycle", "auto-rickshaw"];

const CLASS_TONE: Record<string, "ok" | "warn" | "bad" | "idle" | "info"> = {
  person: "info",
  car: "ok",
  motorcycle: "warn",
  bus: "idle",
  truck: "idle",
  bicycle: "idle",
  "auto-rickshaw": "idle",
};

export default function DetectionsPage() {
  const [rows, setRows] = useState<Detection[] | null>(null);
  const [health, setHealth] = useState<DetectorHealth | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [askedAt, setAskedAt] = useState<string | null>(null);

  const [className, setClassName] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [sinceHours, setSinceHours] = useState("24");
  const [minConfidence, setMinConfidence] = useState("0.45");

  const query = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [detections, detector] = await Promise.all([
        api.detections({
          camera_id: cameraId.trim() || undefined,
          class_name: className || undefined,
          since_hours: sinceHours,
          min_confidence: minConfidence,
          limit: "500",
        }),
        api.detectorHealth().catch(() => null),
      ]);
      setRows(detections);
      if (detector) setHealth(detector);
      setAskedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRows(null);
    } finally {
      setBusy(false);
    }
  }, [cameraId, className, sinceHours, minConfidence]);

  const clear = () => {
    setRows(null);
    setAskedAt(null);
    setError(null);
  };

  return (
    <>
      <PageHeader
        title="Object detections"
        subtitle="Objects recognised at the edge, with number plates where ANPR is enabled."
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}

        <Card title="Ask for detections">
          <div className="space-y-3 px-3 py-3">
            <div className="flex flex-wrap items-end gap-3">
              <FloatSelect
                label="Class"
                  value={className}
                  onChange={(event) => setClassName(event.target.value)}
              >
                  <option value="">Any class</option>
                  {CLASSES.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
              </FloatSelect>
              <FloatSelect
                label="Time window"
                  value={sinceHours}
                  onChange={(event) => setSinceHours(event.target.value)}
              >
                  <option value="1">Last hour</option>
                  <option value="6">Last 6 hours</option>
                  <option value="24">Last 24 hours</option>
                  <option value="168">Last 7 days</option>
                  <option value="720">Last 30 days</option>
              </FloatSelect>
              <FloatSelect
                label="Minimum confidence"
                  value={minConfidence}
                  onChange={(event) => setMinConfidence(event.target.value)}
              >
                  <option value="0">Everything stored</option>
                  <option value="0.45">0.45 (edge floor)</option>
                  <option value="0.6">0.60</option>
                  <option value="0.8">0.80</option>
              </FloatSelect>
              <FloatInput
                label="Camera ID (optional)"
                className="min-w-[18rem] flex-1"
                  value={cameraId}
                  onChange={(event) => setCameraId(event.target.value)}
                  hint="VIGENTRA-TRAFFIC-AHM-0001"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-primary" onClick={() => void query()} disabled={busy}>
                {busy && <Spinner />} Show detections
              </button>
              {rows !== null && (
                <button className="btn" onClick={clear} disabled={busy}>
                  Clear
                </button>
              )}
              {askedAt && (
                <span className="text-2xs text-ink-500">Retrieved {ist(askedAt)}</span>
              )}
            </div>
          </div>
        </Card>

        {rows === null ? (
          <Card title="Nothing retrieved">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              <p>
                Set a filter and choose <strong>Show detections</strong>. This page holds nothing
                until you ask for it.
              </p>
              <p className="mt-2 text-2xs">
                Detections describe what a camera saw at a place and time. They are department
                scoped, and reading them is not a substitute for viewing the footage — which is a
                separate, audited decision.
              </p>
            </div>
          </Card>
        ) : rows.length === 0 ? (
          <Card title="No detections match">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              Nothing in that window matched. Widen the time range, drop the confidence floor, or
              check that an edge worker has run against the camera.
            </div>
          </Card>
        ) : (
          <Card title="Detections">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    {/* Media time, not arrival time - the one you can scrub to. */}
                    <th>In footage</th>
                    <th>Camera</th>
                    <th>Class</th>
                    <th>Plate</th>
                    <th>Confidence</th>
                    <th>Box (x1,y1,x2,y2)</th>
                    <th>Quality</th>
                    <th>Model</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.detection_id}>
                      <td className="whitespace-nowrap" title={ist(row.timestamp_utc)}>
                        {relative(row.timestamp_utc)}
                      </td>
                      <td className="mono whitespace-nowrap tabular-nums">
                        {footageTime(row.provenance?.pts_seconds) ?? (
                          <span className="text-ink-400">—</span>
                        )}
                        {typeof row.provenance?.frame_index === "number" && (
                          <div className="text-2xs text-ink-500">
                            frame {row.provenance.frame_index as number}
                          </div>
                        )}
                      </td>
                      <td>
                        <Link
                          className="font-medium text-brand-600 hover:underline"
                          href={`/registry/${encodeURIComponent(row.camera_id)}`}
                        >
                          {row.camera_name ?? row.camera_id}
                        </Link>
                        {row.owning_department && (
                          <div className="mt-0.5">
                            <DepartmentTag department={row.owning_department} />
                          </div>
                        )}
                      </td>
                      <td>
                        <Pill tone={CLASS_TONE[row.class_name] ?? "idle"}>{row.class_name}</Pill>
                      </td>
                      <td className="whitespace-nowrap">
                        {row.plate_text ? (
                          <>
                            <span className="mono font-semibold tracking-wide text-ink-900">
                              {row.plate_text}
                            </span>
                            {(row.plate_district || row.plate_rto) && (
                              <div className="text-2xs text-ink-500">
                                {row.plate_district
                                  ? `${row.plate_district} · ${row.plate_rto}`
                                  : `${row.plate_state ?? ""} ${row.plate_rto ?? ""}`.trim()}
                              </div>
                            )}
                            {row.plate_confidence != null && (
                              <div className="text-2xs tabular-nums text-ink-500">
                                {row.plate_confidence.toFixed(2)}
                              </div>
                            )}
                          </>
                        ) : row.plate_withheld ? (
                          <span className="text-2xs italic text-ink-400">
                            withheld for your role
                          </span>
                        ) : (
                          <span className="text-2xs text-ink-400">&mdash;</span>
                        )}
                      </td>
                      <td className="tabular-nums">{row.confidence.toFixed(2)}</td>
                      <td className="mono whitespace-nowrap text-2xs text-ink-500">
                        {row.bbox_xyxy.map((value) => Math.round(value)).join(", ")}
                      </td>
                      <td className="text-2xs text-ink-500">{row.frame_quality ?? "normal"}</td>
                      <td className="mono text-2xs text-ink-500">{row.model_version}</td>
                      <td className="text-2xs text-ink-500">{row.source_mode}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {health && rows !== null && (
          <Card title="Detector">
            <div className="grid gap-x-5 gap-y-2 px-4 py-3 text-[13px] sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <div className="field-label">Model</div>
                <div className="mono field-value">{health.model_version ?? health.model_name}</div>
              </div>
              <div>
                <div className="field-label">Device</div>
                <div className="field-value">{health.device}</div>
              </div>
              <div>
                <div className="field-label">Confidence floor</div>
                <div className="field-value tabular-nums">{health.confidence_threshold}</div>
              </div>
              <div>
                <div className="field-label">Frame sampling</div>
                <div className="field-value">every {health.frame_sample_interval} frames</div>
              </div>
            </div>
            <p className="border-t border-line px-4 py-2 text-2xs leading-relaxed text-ink-500">
              {health.accuracy_disclaimer}
            </p>
          </Card>
        )}

        <p className="text-2xs leading-relaxed text-ink-400">
          Detections are generic objects — person, vehicle class, bicycle — plus a registration
          number where the owning unit has enabled ANPR. A plate is personal data: it is readable
          only with the <span className="mono">plate:read</span> permission, is retained on a
          shorter clock than the detection carrying it, and every disclosure is written to the
          audit trail. Plates that do not parse as a valid registration are discarded at the edge
          rather than stored as a guess. There is still no face recognition, no vehicle
          re-identification and no cross-camera identity association: a plate here is one
          observation at one camera, not a track. Inference runs on the edge worker beside the
          camera; the central API stores the result and never sees a frame.
        </p>
      </div>
    </>
  );
}
