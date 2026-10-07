"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Notice, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import { ist, relative } from "@/lib/format";
import type { HealthAlert } from "@/lib/types";

/**
 * Cameras and department systems that stopped answering the health monitor.
 *
 * Raised after two failed checks in a row (about 40 s) and closed by the monitor itself when
 * the camera answers again, so this list needs no clearing: acknowledging only says someone
 * is on it. A whole department system that is unreachable is one row, not one per camera.
 */
export function HealthAlerts({ canAcknowledge }: { canAcknowledge: boolean }) {
  const [rows, setRows] = useState<HealthAlert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.healthAlerts({ since_hours: "24" }));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5_000);
    return () => clearInterval(timer);
  }, [load]);

  const take = async (row: HealthAlert) => {
    setBusy(row.alert_id);
    try {
      await api.acknowledgeHealthAlert(row.alert_id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const down = (rows ?? []).filter((row) => row.open);
  return (
    <Card title={`Camera health — ${down.length ? `${down.length} down` : "all answering"}`}>
      {error && <Notice tone="bad">{error}</Notice>}
      {rows && rows.length === 0 ? (
        <p className="px-6 py-5 text-body-sm text-muted">
          No camera or department system has stopped answering in the last 24 hours.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>State</th>
                <th>What</th>
                <th>Since</th>
                <th>Taken up</th>
              </tr>
            </thead>
            <tbody>
              {(rows ?? []).map((row) => (
                <tr key={row.alert_id} className={row.open ? "" : "opacity-60"}>
                  <td className="whitespace-nowrap">
                    <Pill tone={row.open ? "bad" : "ok"}>{row.open ? "down" : "recovered"}</Pill>
                  </td>
                  <td>
                    <div className="font-semibold">
                      {row.kind === "SOURCE_UNREACHABLE"
                        ? `Department system ${row.source_system} unreachable`
                        : row.camera_name ?? row.camera_id}
                    </div>
                    <div className="text-muted">{row.detail}</div>
                  </td>
                  <td className="whitespace-nowrap" title={ist(row.raised_at)}>
                    {relative(row.raised_at)}
                    {row.recovered_at && <div className="text-muted">back {relative(row.recovered_at)}</div>}
                  </td>
                  <td className="whitespace-nowrap">
                    {row.acknowledged ? (
                      <span className="text-muted">by {row.acknowledged_by}</span>
                    ) : row.open && canAcknowledge ? (
                      <button className="btn btn-sm" disabled={busy === row.alert_id} onClick={() => void take(row)}>
                        I&apos;m on it
                      </button>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
