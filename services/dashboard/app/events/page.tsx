"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  DepartmentTag,
  EmptyState,
  FloatInput,
  FloatSelect,
  Notice,
  PageHeader,
  Pill,
  Spinner,
} from "@/components/ui";
import { api } from "@/lib/api";
import { humanise, ist, orDash, relative, titleise } from "@/lib/format";
import type { CorrelationResult, EventRecord } from "@/lib/types";

const SEVERITY_TONE: Record<string, "ok" | "warn" | "bad" | "idle" | "info"> = {
  info: "info",
  warning: "warn",
  critical: "bad",
};

function eventTone(event: EventRecord): "ok" | "warn" | "bad" | "idle" | "info" {
  if (event.severity && SEVERITY_TONE[event.severity]) return SEVERITY_TONE[event.severity];
  if (event.event_type === "camera_tamper" || event.event_type === "device_offline") return "bad";
  if (event.event_type === "storage_warning") return "warn";
  return "idle";
}

export default function EventsPage() {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [correlation, setCorrelation] = useState<CorrelationResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sinceHours, setSinceHours] = useState(6);
  const [source, setSource] = useState<string>("");
  const [type, setType] = useState<string>("");

  const load = useCallback(
    async (opts: { refresh: boolean }) => {
      setBusy(true);
      setError(null);
      try {
        const [rows, pairs] = await Promise.all([
          api.events({
            since_hours: String(sinceHours),
            source_system: source || undefined,
            event_type: type || undefined,
            refresh: String(opts.refresh),
            limit: "200",
          }),
          api.correlation({ since_hours: String(sinceHours), refresh: "false" }),
        ]);
        setEvents(rows);
        setCorrelation(pairs);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
        setLoading(false);
      }
    },
    [sinceHours, source, type],
  );

  useEffect(() => {
    void load({ refresh: true });
  }, [load]);

  const options = useMemo(() => {
    const uniq = (values: (string | null)[]) =>
      Array.from(new Set(values.filter(Boolean) as string[])).sort();
    return {
      sources: uniq(events.map((e) => e.source_system)),
      types: uniq(events.map((e) => e.event_type)),
    };
  }, [events]);

  if (loading) return <LoadingPanel label="Loading federated events" />;

  return (
    <>
      <PageHeader
        title="Federated events"
        subtitle="Generic device and motion events from every department, normalised into one stream."
        actions={
          <>
            <button className="btn" onClick={() => load({ refresh: true })} disabled={busy}>
              {busy && <Spinner />} Refresh
            </button>
          </>
        }
      />

      <div className="space-y-3">
        {error && <Notice tone="bad">{error}</Notice>}

        <Notice tone="info" title="What lives here">
          Module 1 recognises motion, line-crossing, tamper and device-health events. There are no
          ANPR, plate, vehicle or identity fields — future detection modules extend the same
          record, they do not replace it. Every event carries provenance back to its source.
        </Notice>

        {/* Filters */}
        <div className="card flex flex-wrap items-end gap-3 px-3 py-2.5">
          <FloatInput
            label="Time window (hrs)"
            inputClassName="max-w-[7rem]"
              type="number"
              min={1}
              max={168}
              value={sinceHours}
              onChange={(event) => setSinceHours(Math.max(1, Number(event.target.value) || 1))}
          />
          <FloatSelect
            label="Source system"
            className="min-w-[10rem]"
              value={source}
              onChange={(event) => setSource(event.target.value)}
          >
              <option value="">All</option>
              {options.sources.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
          </FloatSelect>
          <FloatSelect
            label="Event type"
            className="min-w-[10rem]"
              value={type}
              onChange={(event) => setType(event.target.value)}
          >
              <option value="">All</option>
              {options.types.map((value) => (
                <option key={value} value={value}>
                  {humanise(value)}
                </option>
              ))}
          </FloatSelect>
          <div className="ml-auto text-2xs text-ink-500">
            <span className="tabular font-medium text-ink-900">{events.length}</span> event(s) in
            the last <span className="tabular font-medium text-ink-900">{sinceHours}</span>h
          </div>
        </div>

        {/* Correlation */}
        {correlation && correlation.pairs.length > 0 && (
          <Card
            title="Cross-camera correlation"
            action={
              <span className="text-2xs text-ink-500">
                Pairs within {correlation.window_seconds}s and {correlation.radius_m}m ·
                {" "}
                {correlation.considered} event(s) considered
              </span>
            }
          >
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>When (IST)</th>
                    <th>Event A</th>
                    <th>Event B</th>
                    <th className="text-right">Δ (s)</th>
                    <th className="text-right">Distance</th>
                    <th>Departments</th>
                  </tr>
                </thead>
                <tbody>
                  {correlation.pairs.slice(0, 30).map((pair, index) => (
                    <tr key={`${pair.a.event_id}-${pair.b.event_id}-${index}`}>
                      <td className="mono whitespace-nowrap text-ink-500">
                        {ist(pair.a.timestamp_utc)}
                      </td>
                      <td>
                        <Link
                          className="font-medium text-brand-600 hover:underline"
                          href={`/registry/${encodeURIComponent(pair.a.camera_id)}`}
                        >
                          {pair.a.camera_name ?? pair.a.camera_id}
                        </Link>
                        <div className="text-2xs text-ink-500">{humanise(pair.a.event_type)}</div>
                      </td>
                      <td>
                        <Link
                          className="font-medium text-brand-600 hover:underline"
                          href={`/registry/${encodeURIComponent(pair.b.camera_id)}`}
                        >
                          {pair.b.camera_name ?? pair.b.camera_id}
                        </Link>
                        <div className="text-2xs text-ink-500">{humanise(pair.b.event_type)}</div>
                      </td>
                      <td className="tabular text-right">{pair.delta_seconds.toFixed(1)}</td>
                      <td className="tabular text-right">
                        {pair.distance_m < 1000
                          ? `${Math.round(pair.distance_m)} m`
                          : `${(pair.distance_m / 1000).toFixed(2)} km`}
                      </td>
                      <td>
                        {pair.cross_department ? (
                          <Pill tone="info">cross-dept</Pill>
                        ) : (
                          <Pill tone="idle">same dept</Pill>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {/* Events */}
        <Card title="Event stream">
          {events.length === 0 ? (
            <EmptyState
              message="No events in this time window."
              hint="Increase the window or clear the filters."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>When (IST)</th>
                    <th>Camera</th>
                    <th>Department</th>
                    <th>District</th>
                    <th>Type</th>
                    <th>Severity</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((event) => (
                    <tr key={event.event_id}>
                      <td
                        className="mono whitespace-nowrap text-ink-500"
                        title={ist(event.timestamp_utc)}
                      >
                        {relative(event.timestamp_utc)}
                      </td>
                      <td>
                        <Link
                          className="font-medium text-brand-600 hover:underline"
                          href={`/registry/${encodeURIComponent(event.camera_id)}`}
                        >
                          {event.camera_name ?? event.camera_id}
                        </Link>
                        <div className="mono text-2xs text-ink-400">{event.camera_id}</div>
                      </td>
                      <td>
                        {event.owning_department ? (
                          <DepartmentTag department={event.owning_department} />
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>{orDash(event.district)}</td>
                      <td>{humanise(event.event_type)}</td>
                      <td>
                        <Pill tone={eventTone(event)}>{orDash(event.severity)}</Pill>
                      </td>
                      <td className="text-2xs text-ink-500">
                        {Object.entries((event.payload.attributes as Record<string, unknown>) ?? {})
                          .map(([key, value]) => `${key}: ${value}`)
                          .join(" · ") ||
                          titleise((event.payload.source_event_type as string) ?? "")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <p className="text-2xs leading-relaxed text-ink-400">
          Events are pulled from each department system on refresh. Every record is deterministic
          — the same source event always produces the same canonical event_id — so re-syncing
          updates rather than duplicates.
        </p>
      </div>
    </>
  );
}
