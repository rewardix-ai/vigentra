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

const CLASS_COLOUR: Record<string, string> = {
  car: "#4ade80",
  motorcycle: "#22d3ee",
  bus: "#c084fc",
  truck: "#fb7185",
  "auto-rickshaw": "#fbbf24",
  person: "#f59e0b",
  bicycle: "#a3e635",
};

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
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <div>
          <div className="text-[13px] font-semibold">Vehicles counted</div>
          <p className="mt-0.5 text-2xs text-ink-500">
            Everything the edge worker has recognised at this camera. Counts only —
            no registration numbers are shown here.
          </p>
        </div>
        <div className="flex gap-0.5">
          {WINDOWS.map((w) => (
            <button
              key={w.hours}
              type="button"
              onClick={() => setHours(w.hours)}
              className={
                "rounded px-2 py-1 text-2xs font-medium transition " +
                (w.hours === hours ? "bg-navy-800 text-white" : "text-ink-600 hover:bg-brand-50")
              }
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4">
        {error && <p className="text-2xs text-ink-500">{error}</p>}

        {!error && data && data.total_detections === 0 && (
          <p className="text-2xs text-ink-500">
            Nothing counted here in this window. The edge worker records what it sees
            while it is running against this camera; if it has not run, there is
            nothing to show rather than a zero that means something.
          </p>
        )}

        {!error && data && data.total_detections > 0 && (
          <>
            <div className="mb-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <div>
                <div className="tabular text-2xl font-semibold text-ink-900">
                  {data.total_vehicles.toLocaleString()}
                </div>
                <div className="text-2xs text-ink-500">vehicles</div>
              </div>
              <div>
                <div className="tabular text-base font-medium text-ink-700">
                  {data.total_detections.toLocaleString()}
                </div>
                <div className="text-2xs text-ink-500">all objects</div>
              </div>
              <div>
                <div className="tabular text-base font-medium text-ink-700">
                  {data.plates_read.toLocaleString()}
                </div>
                <div className="text-2xs text-ink-500">distinct plates</div>
              </div>
            </div>

            <div className="space-y-1.5">
              {[...vehicles, ...others].map((row) => (
                <div key={row.class_name} className="flex items-center gap-2">
                  <div className="w-24 shrink-0 text-2xs text-ink-600">{row.class_name}</div>
                  <div className="h-3 flex-1 overflow-hidden rounded-sm bg-[#eef1f5]">
                    <div
                      className="h-full rounded-sm"
                      style={{
                        width: `${(row.count / peak) * 100}%`,
                        background: CLASS_COLOUR[row.class_name] ?? "#94a3b8",
                      }}
                    />
                  </div>
                  <div className="tabular w-14 shrink-0 text-right font-mono text-2xs text-ink-700">
                    {row.count.toLocaleString()}
                  </div>
                </div>
              ))}
            </div>

            {data.last_seen_utc && (
              <p className="mt-3 border-t border-line pt-2 text-2xs text-ink-400">
                Counted between {ist(data.first_seen_utc)} and {ist(data.last_seen_utc)}.
                Stored centrally, so these totals survive a reload.
              </p>
            )}
          </>
        )}

        {busy && !data && <p className="text-2xs text-ink-500">Counting…</p>}
      </div>
    </div>
  );
}
