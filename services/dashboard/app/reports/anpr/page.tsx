"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { Card, Notice, PageHeader, Pill, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { ist } from "@/lib/format";
import type { AnprReportRow } from "@/lib/types";

/**
 * The ANPR output report.
 *
 * This is the artefact the challenge asks to be submitted alongside the
 * government-feed demonstration: "detected vehicles or number plates with
 * corresponding timestamps". It carries the camera and its location too,
 * because a list of plates with no places is not evidence that anything was
 * integrated — it is a list of plates.
 *
 * Nothing loads on mount. This is a report you generate for a specific window,
 * not a live feed of who drove past.
 */

export default function AnprReportPage() {
  const [rows, setRows] = useState<AnprReportRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);

  const [cameraId, setCameraId] = useState("");
  const [sinceHours, setSinceHours] = useState("24");

  const generate = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setRows(
        await api.anprReport({
          camera_id: cameraId.trim() || undefined,
          since_hours: sinceHours,
          limit: "2000",
        }),
      );
      setGeneratedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRows(null);
    } finally {
      setBusy(false);
    }
  }, [cameraId, sinceHours]);

  const hits = (rows ?? []).filter((row) => row.watchlist_hit).length;
  const withheld = (rows ?? []).filter((row) => row.plate === null).length;

  return (
    <>
      <PageHeader
        title="ANPR output report"
        subtitle="Plates read, with the camera, the place and the timestamp."
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}

        <Card title="Generate">
          <div className="flex flex-wrap items-end gap-3 px-3 py-3">
            <label className="block">
              <span className="field-label">Time window</span>
              <select
                className="input mt-1"
                value={sinceHours}
                onChange={(event) => setSinceHours(event.target.value)}
              >
                <option value="1">Last hour</option>
                <option value="6">Last 6 hours</option>
                <option value="24">Last 24 hours</option>
                <option value="168">Last 7 days</option>
                <option value="720">Last 30 days</option>
              </select>
            </label>

            <label className="block min-w-[18rem] flex-1">
              <span className="field-label">Camera ID (optional)</span>
              <input
                className="input mt-1"
                value={cameraId}
                onChange={(event) => setCameraId(event.target.value)}
                placeholder="SENTINEL-TRAFFIC-AHM-0001"
              />
            </label>

            <button className="btn btn-primary" onClick={() => void generate()} disabled={busy}>
              {busy && <Spinner />} Generate report
            </button>

            {rows !== null && rows.length > 0 && (
              <a
                className="btn"
                href={api.anprReportCsvUrl({
                  camera_id: cameraId.trim() || undefined,
                  since_hours: sinceHours,
                })}
              >
                Download CSV
              </a>
            )}

            {generatedAt && (
              <span className="text-2xs text-ink-500">Generated {ist(generatedAt)}</span>
            )}
          </div>
        </Card>

        {rows !== null && rows.length > 0 && (
          <div className="flex flex-wrap gap-2 text-[13px]">
            <Pill tone="info">{rows.length} reads</Pill>
            <Pill tone={hits ? "bad" : "ok"}>{hits} watchlist hits</Pill>
            {withheld > 0 && (
              <Pill tone="idle" >
                {withheld} past retention — plate withheld
              </Pill>
            )}
          </div>
        )}

        {rows === null ? (
          <Card title="No report yet">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              Choose a window and generate. Nothing is loaded until you ask.
            </div>
          </Card>
        ) : rows.length === 0 ? (
          <Card title="No plates read">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              <p>No plates were read in that window.</p>
              <p className="mt-2 text-2xs">
                Check that an edge worker ran with <span className="mono">ANPR_ENABLE=true</span>{" "}
                against a camera in scope. On wide overview footage a low yield is expected — see{" "}
                <span className="mono">docs/anpr.md</span>.
              </p>
            </div>
          </Card>
        ) : (
          <Card title="Detected plates">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Timestamp (IST)</th>
                    <th>Plate</th>
                    <th>Camera</th>
                    <th>Location</th>
                    <th>Coordinates</th>
                    <th>Score</th>
                    <th>Frames agreed</th>
                    <th>Watchlist</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={`${row.camera_id}-${row.timestamp_utc}-${index}`}>
                      <td className="whitespace-nowrap">{ist(row.timestamp_utc)}</td>
                      <td className="mono font-semibold">
                        {row.plate ?? (
                          <span
                            className="italic font-normal text-ink-400"
                            title="Past this deployment's plate retention period"
                          >
                            withheld
                          </span>
                        )}
                      </td>
                      <td>
                        <Link
                          className="link"
                          href={`/registry/${encodeURIComponent(row.camera_id)}`}
                        >
                          {row.camera_name ?? row.camera_id}
                        </Link>
                      </td>
                      <td className="text-2xs">{row.location ?? "—"}</td>
                      <td className="mono text-2xs">
                        {row.latitude !== null && row.longitude !== null
                          ? `${row.latitude.toFixed(5)}, ${row.longitude.toFixed(5)}`
                          : "—"}
                      </td>
                      <td className="tabular">{row.confidence.toFixed(2)}</td>
                      <td className="tabular">
                        {row.observations}
                        {row.observations === 1 && (
                          <span className="ml-1 text-2xs text-warn">single frame</span>
                        )}
                      </td>
                      <td>
                        {row.watchlist_hit ? (
                          <Pill tone="bad">{row.watchlist_category}</Pill>
                        ) : (
                          <span className="text-2xs text-ink-400">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
