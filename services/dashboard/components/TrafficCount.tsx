"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { ist } from "@/lib/format";
import type { CameraTrafficSummary } from "@/lib/types";

/**
 * How much traffic this camera has counted, and of what.
 *
 * Deliberately loaded on mount, unlike the detections list.
 *
 * That page holds nothing until asked, because browsing who was where is the
 * habit a surveillance system should not encourage. A count is a different
 * object: it is an aggregate with no registration numbers and no individual in
 * it, and it answers "is this camera earning its place at this junction",
 * which is an asset-management question rather than an investigative one. The
 * plate figure here is a COUNT of distinct plates, never the plates
 * themselves - reading those still means the detections page, a reason, and an
 * audit entry.
 *
 * The numbers come from the database, not from this component tallying rows it
 * fetched. That is what makes them survive a reload: nothing about the count
 * lives in the page, so there is nothing to lose.
 */

const WINDOWS: { hours: number; label: string }[] = [
  { hours: 1, label: "1h" },
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
];

/** Vehicles first, biggest first; people and bicycles after. */
const VEHICLES = new Set(["car", "motorcycle", "bus", "truck", "auto-rickshaw"]);

export function TrafficCount({ cameraId }: { cameraId: string }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<CameraTrafficSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (window: number) => {
      setBusy(true);
      setError(null);
      try {
        setData(await api.cameraTraffic(cameraId, window));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [cameraId],
  );

  useEffect(() => {
    void load(hours);
  }, [load, hours]);

  const vehicles = (data?.by_class ?? []).filter((c) => VEHICLES.has(c.class_name));
  const others = (data?.by_class ?? []).filter((c) => !VEHICLES.has(c.class_name));
  const peak = Math.max(1, ...(data?.by_class ?? []).map((c) => c.count));

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">Vehicles counted</div>
          <p className="mt-1 max-w-[70ch] text-caption text-muted">
            Everything the edge worker has recognised at this camera. Counts only —
            no registration numbers are shown here.
          </p>
        </div>
        <div role="group" aria-label="Counting window" className="segmented">
          {WINDOWS.map((w) => (
            <button
              key={w.hours}
              type="button"
              aria-pressed={w.hours === hours}
              onClick={() => setHours(w.hours)}
              className="segmented-item"
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      <div className="px-6 py-5">
        {error && <p className="text-caption text-muted">{error}</p>}

        {!error && data && data.total_detections === 0 && (
          <p className="text-caption text-muted">
            Nothing counted here in this window. The edge worker records what it sees
            while it is running against this camera; if it has not run, there is
            nothing to show rather than a zero that means something.
          </p>
        )}

        {!error && data && data.total_detections > 0 && (
          <>
            <div className="mb-6 flex flex-wrap items-baseline gap-x-10 gap-y-3">
              <div>
                <div className="tabular text-h3 text-ink">
                  {data.total_vehicles.toLocaleString()}
                </div>
                <div className="text-caption text-muted">vehicles</div>
              </div>
              <div>
                <div className="tabular text-title text-ink">
                  {data.total_detections.toLocaleString()}
                </div>
                <div className="text-caption text-muted">all objects</div>
              </div>
              <div>
                <div className="tabular text-title text-ink">
                  {data.plates_read.toLocaleString()}
                </div>
                <div className="text-caption text-muted">distinct plates</div>
              </div>
            </div>

            {/* One ink for every class. Each bar is labelled, so a colour per
                class would be saying what the label already says. */}
            <div className="space-y-2">
              {[...vehicles, ...others].map((row) => (
                <div key={row.class_name} className="flex items-center gap-3">
                  <div className="w-24 shrink-0 text-caption text-muted">{row.class_name}</div>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-canvas-soft">
                    <div
                      className="h-full rounded-full bg-ink"
                      style={{ width: `${(row.count / peak) * 100}%` }}
                    />
                  </div>
                  <div className="tabular w-14 shrink-0 text-right font-mono text-caption text-ink-soft">
                    {row.count.toLocaleString()}
                  </div>
                </div>
              ))}
            </div>

            {data.last_seen_utc && (
              <p className="mt-6 border-t border-hairline-soft pt-4 text-caption text-muted">
                Counted between {ist(data.first_seen_utc)} and {ist(data.last_seen_utc)}.
                Stored centrally, so these totals survive a reload.
              </p>
            )}
          </>
        )}

        {busy && !data && <p className="text-caption text-muted">Counting…</p>}
      </div>
    </div>
  );
}
