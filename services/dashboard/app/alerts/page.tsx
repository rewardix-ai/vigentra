"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Card, DepartmentTag, Notice, PageHeader, Pill, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { ist, relative } from "@/lib/format";
import type { Alert } from "@/lib/types";

/**
 * Watchlist hits, newest first.
 *
 * This page DOES load on mount, and that is a deliberate departure from
 * `/detections`, which holds nothing until asked. The difference is who the
 * page is about. Browsing detections is idle surveillance of people who did
 * nothing; an alert is a vehicle someone has already, on the record, asked to
 * be told about — so leaving it behind a button would mean a stolen car passes
 * a camera and nobody hears until an operator thinks to look.
 *
 * Near matches are shown by default. Hiding them would keep the console tidy
 * and is exactly how a vehicle whose plate was read one character wrong gets
 * missed, so the filter to suppress them exists and is off.
 */

const CATEGORY_TONE: Record<string, "ok" | "warn" | "bad" | "idle" | "info"> = {
  stolen: "bad",
  wanted: "bad",
  blacklist: "warn",
  missing: "info",
  suspect: "warn",
};

const REFRESH_MS = 30_000;

export default function AlertsPage() {
  const [rows, setRows] = useState<Alert[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);

  const [unacknowledgedOnly, setUnacknowledgedOnly] = useState(true);
  const [exactOnly, setExactOnly] = useState(false);
  const [sinceHours, setSinceHours] = useState("24");

  const [dismissing, setDismissing] = useState<string | null>(null);
  const [dismissReason, setDismissReason] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const alerts = await api.alerts({
        unacknowledged_only: unacknowledgedOnly ? "true" : "false",
        exact_only: exactOnly ? "true" : "false",
        since_hours: sinceHours,
        limit: "500",
      });
      setRows(alerts);
      setRefreshedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [unacknowledgedOnly, exactOnly, sinceHours]);

  useEffect(() => {
    void load();
  }, [load]);

  // Polled rather than pushed. A websocket would be better and is a small
  // change; thirty seconds is the honest interval for what this is today, and
  // the page says so rather than implying it is live.
  useEffect(() => {
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const acknowledge = async (alert: Alert, reason?: string) => {
    setBusy(true);
    try {
      await api.acknowledgeAlert(alert.alert_id, reason);
      setDismissing(null);
      setDismissReason("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const open = (rows ?? []).filter((row) => !row.acknowledged);
  const nearMatches = open.filter((row) => !row.exact).length;

  return (
    <>
      <PageHeader
        title="Alerts"
        subtitle="Vehicles on the watchlist, seen by a camera on this network."
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}

        <Notice tone="warn" title="An alert is not an identification">
          Every row here is a probabilistic reading of a photograph, matched against a list.
          Before acting on one, look at the frame. A near match especially — that is a plate the
          reader could not agree on with the watchlist entry, and the distance says how far apart
          they were.
        </Notice>

        <Card title="Filter">
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

            <label className="flex items-center gap-2 pb-1 text-[13px]">
              <input
                type="checkbox"
                checked={unacknowledgedOnly}
                onChange={(event) => setUnacknowledgedOnly(event.target.checked)}
              />
              Open alerts only
            </label>

            <label
              className="flex items-center gap-2 pb-1 text-[13px]"
              title="Near matches are how a misread plate still reaches you. Hiding them is a choice, not a default."
            >
              <input
                type="checkbox"
                checked={exactOnly}
                onChange={(event) => setExactOnly(event.target.checked)}
              />
              Hide near matches
            </label>

            <button className="btn" onClick={() => void load()} disabled={busy}>
              {busy && <Spinner />} Refresh
            </button>

            {refreshedAt && (
              <span className="text-2xs text-ink-500">
                Updated {relative(refreshedAt)} · re-checks every 30s
              </span>
            )}
          </div>
        </Card>

        {rows !== null && (
          <div className="flex flex-wrap gap-2 text-[13px]">
            <Pill tone={open.length ? "bad" : "ok"}>
              {open.length} open {open.length === 1 ? "alert" : "alerts"}
            </Pill>
            {nearMatches > 0 && (
              <Pill tone="warn">{nearMatches} needing review (near match)</Pill>
            )}
          </div>
        )}

        {rows === null ? (
          <Card title="Loading">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              <Spinner /> Checking for watchlist hits…
            </div>
          </Card>
        ) : rows.length === 0 ? (
          <Card title="No alerts">
            <div className="px-4 py-6 text-[13px] text-ink-500">
              <p>Nothing on the watchlist has been seen in this window.</p>
              <p className="mt-2 text-2xs">
                That is the expected state. If you were expecting a hit, check that the vehicle is
                on the{" "}
                <Link className="link" href="/watchlist">
                  watchlist
                </Link>{" "}
                and that an edge worker with ANPR enabled has run against the cameras — an entry
                added after a vehicle passed does not raise an alert retroactively.
              </p>
            </div>
          </Card>
        ) : (
          <Card title="Watchlist hits">
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Seen</th>
                    <th>Category</th>
                    <th>Watched plate</th>
                    <th>Plate read</th>
                    <th>Match</th>
                    <th>Camera</th>
                    <th>Department</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.alert_id} className={row.acknowledged ? "opacity-60" : ""}>
                      <td className="whitespace-nowrap">
                        <div>{ist(row.timestamp_utc)}</div>
                        <div className="text-2xs text-ink-500">{relative(row.timestamp_utc)}</div>
                      </td>
                      <td>
                        <Pill tone={CATEGORY_TONE[row.category] ?? "idle"}>{row.category}</Pill>
                      </td>
                      <td className="mono">
                        {row.plate_withheld ? (
                          <span className="italic text-ink-400" title="Withheld for your role">
                            withheld
                          </span>
                        ) : (
                          row.watch_plate
                        )}
                      </td>
                      <td className="mono">
                        {row.plate_withheld ? (
                          <span className="italic text-ink-400">withheld</span>
                        ) : (
                          row.seen_plate
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        {row.exact ? (
                          <Pill tone="bad">exact</Pill>
                        ) : (
                          <>
                            <Pill tone="warn">near</Pill>
                            <span
                              className="ml-1 text-2xs text-ink-500"
                              title="Confusion-weighted edit distance. One classic misread scores 0.35."
                            >
                              {row.distance.toFixed(2)}
                            </span>
                          </>
                        )}
                      </td>
                      <td>
                        <Link className="link" href={`/registry/${encodeURIComponent(row.camera_id)}`}>
                          {row.camera_name ?? row.camera_id}
                        </Link>
                        <div className="text-2xs text-ink-500">
                          {[row.district, row.city].filter(Boolean).join(" · ") || row.camera_id}
                        </div>
                      </td>
                      <td>
                        {row.owning_department && (
                          <DepartmentTag department={row.owning_department} />
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        {row.acknowledged ? (
                          <>
                            <Pill tone={row.dismissed_reason ? "idle" : "ok"}>
                              {row.dismissed_reason ? "dismissed" : "acknowledged"}
                            </Pill>
                            <div className="text-2xs text-ink-500">
                              by {row.acknowledged_by}
                              {row.dismissed_reason ? ` — ${row.dismissed_reason}` : ""}
                            </div>
                          </>
                        ) : (
                          <Pill tone="warn">open</Pill>
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        {!row.acknowledged && (
                          <div className="flex flex-col gap-1">
                            <div className="flex gap-1">
                              <button
                                className="btn btn-sm"
                                onClick={() => void acknowledge(row)}
                                disabled={busy}
                                title="I have seen this and it is being dealt with"
                              >
                                Acknowledge
                              </button>
                              <button
                                className="btn btn-sm"
                                onClick={() =>
                                  setDismissing(dismissing === row.alert_id ? null : row.alert_id)
                                }
                                disabled={busy}
                                title="I looked at the frame and it is not the watched vehicle"
                              >
                                Not the vehicle
                              </button>
                            </div>
                            {dismissing === row.alert_id && (
                              <div className="flex gap-1">
                                <input
                                  className="input text-2xs"
                                  placeholder="What did the frame actually show?"
                                  value={dismissReason}
                                  onChange={(event) => setDismissReason(event.target.value)}
                                />
                                <button
                                  className="btn btn-sm btn-primary"
                                  disabled={busy || dismissReason.trim().length < 4}
                                  onClick={() => void acknowledge(row, dismissReason.trim())}
                                >
                                  Save
                                </button>
                              </div>
                            )}
                          </div>
                        )}
                        {!row.plate_withheld && row.seen_plate && (
                          <Link
                            className="link mt-1 block text-2xs"
                            href={`/plates?q=${encodeURIComponent(row.seen_plate)}`}
                          >
                            Trace this vehicle →
                          </Link>
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
