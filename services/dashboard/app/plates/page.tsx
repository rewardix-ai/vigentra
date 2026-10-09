"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { usePersisted } from "@/lib/persist";
import { Search } from "lucide-react";

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
import { ist, relative } from "@/lib/format";
import type { PlateSearchHit, Pursuit, Track } from "@/lib/types";

/**
 * Trace a vehicle across the federated camera network.
 *
 * The most revealing page in this application, and it is built to behave like
 * it. Nothing loads on mount; a search runs only when asked; and a trace
 * requires a written reason that is recorded against the account before the
 * API will answer at all. That last one is enforced server-side — this form is
 * a convenience, not the control.
 *
 * Search is deliberately two-step. You look for plates the network actually
 * saw near the string you have, pick one, and then ask for its route. Going
 * straight from a typed registration to a map would encourage tracing a plate
 * nobody has seen, on the strength of a typo.
 */

const TrackMap = dynamic(() => import("@/components/TrackMap").then((mod) => mod.TrackMap), {
  ssr: false,
  loading: () => (
    <div className="card flex h-[460px] items-center justify-center text-body-sm text-muted">
      <Spinner /> Loading map…
    </div>
  ),
});

export default function PlatesPage() {
  const [query, setQuery] = usePersisted("trace.plate", "");
  const [reason, setReason] = usePersisted("trace.reason", "");
  const [sinceHours, setSinceHours] = usePersisted("trace.since", "168");
  const [maxDistance, setMaxDistance] = usePersisted("trace.distance", "1.0");

  const [hits, setHits] = useState<PlateSearchHit[] | null>(null);
  const [track, setTrack] = useState<Track | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pursuits, setPursuits] = useState<Pursuit[]>([]);

  // Active pursuits, refreshed while the page is open: which vehicles the cameras are following.
  useEffect(() => {
    let alive = true;
    const load = () => api.pursuits().then((r) => alive && setPursuits(r.pursuits)).catch(() => {});
    load();
    const id = window.setInterval(load, 10000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const follow = useCallback(
    async (plate: string) => {
      setError(null);
      try {
        await api.startPursuit(plate, reason.trim());
        setPursuits((await api.pursuits()).pursuits);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reason],
  );

  // Arriving from an alert pre-fills the plate but never the reason, and never
  // runs the trace. The reason has to be written by the person asking.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const initial = params.get("q");
    if (initial) setQuery(initial);
  }, []);

  const search = useCallback(async () => {
    setBusy(true);
    setError(null);
    setTrack(null);
    try {
      setHits(
        await api.searchPlates({
          q: query.trim(),
          max_distance: maxDistance,
          since_hours: sinceHours,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setHits(null);
    } finally {
      setBusy(false);
    }
  }, [query, maxDistance, sinceHours]);

  const trace = useCallback(
    async (plate: string) => {
      setBusy(true);
      setError(null);
      try {
        setTrack(
          await api.plateTrack(plate, {
            reason: reason.trim(),
            max_distance: maxDistance,
            since_hours: sinceHours,
          }),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setTrack(null);
      } finally {
        setBusy(false);
      }
    },
    [reason, maxDistance, sinceHours],
  );

  const reasonReady = reason.trim().length >= 8;

  return (
    <>
      <PageHeader
        title="Trace a vehicle"
        subtitle="Where a registration number has been seen, across every camera you may read."
      />

      <div className="space-y-4">
        {error && <Notice tone="bad">{error}</Notice>}

        {pursuits.length > 0 && (
          <Notice tone="info" title="Following live">
            <div className="space-y-1">
              {pursuits.map((p) => (
                <div key={p.id} className="flex flex-wrap items-center gap-2">
                  <span className="mono font-semibold">{p.plate}</span>
                  <span className="text-caption text-muted">
                    {p.hot_cameras.length > 0
                      ? `${p.hot_cameras.length} camera${p.hot_cameras.length === 1 ? "" : "s"} watched first (last seen and all it could reach)`
                      : "not seen yet: every camera checks each vehicle against it"}
                    {" · started by "}
                    {p.started_by} {relative(p.started_at)}
                  </span>
                  <button
                    className="btn btn-sm"
                    onClick={() => api.endPursuit(p.id).then(() => api.pursuits()).then((r) => setPursuits(r.pursuits))}
                  >
                    End
                  </button>
                </div>
              ))}
            </div>
          </Notice>
        )}

        <Notice tone="warn" title="This query is recorded against your account">
          Reconstructing a vehicle&apos;s movement is the most revealing thing this platform does.
          The reason you give is written to the audit log with your username, the plate and the
          time — and an{" "}
          <Link className="link" href="/audit">
            auditor
          </Link>{" "}
          can read it. Give a real one.
        </Notice>

        <Card title="Find the vehicle">
          <div className="space-y-3 px-6 py-5">
            <div className="flex flex-wrap items-start gap-3">
              <FloatInput
                label="Registration number"
                className="min-w-[16rem] flex-1"
                inputClassName="mono"
                  value={query}
                  onChange={(event) => setQuery(event.target.value.toUpperCase())}
                  hint="GJ01AB1234"
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && query.trim().length >= 3) void search();
                  }}
              />

              <FloatSelect
                label="Time window"
                  value={sinceHours}
                  onChange={(event) => setSinceHours(event.target.value)}
              >
                  <option value="24">Last 24 hours</option>
                  <option value="168">Last 7 days</option>
                  <option value="720">Last 30 days</option>
              </FloatSelect>

              <FloatSelect
                label="Match tolerance"
                title="Confusion-weighted edit distance. One classic OCR misread scores 0.35."
                value={maxDistance}
                onChange={(event) => setMaxDistance(event.target.value)}
              >
                <option value="0">Exact only</option>
                <option value="0.5">Tight (one misread)</option>
                <option value="1.0">Default (two misreads)</option>
                <option value="2.0">Wide — expect false matches</option>
              </FloatSelect>

              <button
                className="btn btn-primary btn-lg"
                onClick={() => void search()}
                disabled={busy || query.trim().length < 3}
              >
                {busy ? <Spinner /> : <Search className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />} Search sightings
              </button>
            </div>

            <div>
              <FloatInput
                label="Why are you tracing this vehicle? (required, recorded)"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                hint="e.g. FIR 118/2026 — stolen vehicle, last seen Sarkhej"
              />
              {reason.length > 0 && !reasonReady && (
                <span className="mt-1.5 block px-4 text-caption text-warn">
                  A little more detail — a case number or what happened.
                </span>
              )}
            </div>
          </div>
        </Card>

        {hits !== null && hits.length === 0 && (
          <Card title="No sightings">
            <div className="px-6 py-6 text-body-sm text-muted">
              <p>
                No camera you may read has seen a plate close to{" "}
                <span className="mono">{query}</span> in this window.
              </p>
              <p className="mt-2 text-caption">
                Widen the time window or the match tolerance. Remember that a vehicle whose plate
                was never read is not here at all — the network sees plates, not vehicles.
              </p>
            </div>
          </Card>
        )}

        {hits !== null && hits.length > 0 && (
          <Card title={`Plates seen near "${query}"`}>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Plate</th>
                    <th>Match</th>
                    <th>Sightings</th>
                    <th>Cameras</th>
                    <th>First seen</th>
                    <th>Last seen</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {hits.map((hit) => (
                    <tr key={hit.plate}>
                      <td className="mono font-semibold">{hit.plate}</td>
                      <td className="whitespace-nowrap">
                        {hit.exact ? (
                          <Pill tone="ok">exact</Pill>
                        ) : (
                          <>
                            <Pill tone="warn">near</Pill>
                            <span className="ml-1 text-caption text-muted">
                              {hit.distance.toFixed(2)}
                            </span>
                          </>
                        )}
                      </td>
                      <td className="tabular">{hit.sightings}</td>
                      <td className="tabular">{hit.camera_count}</td>
                      <td className="whitespace-nowrap text-caption">{ist(hit.first_seen)}</td>
                      <td className="whitespace-nowrap text-caption">{ist(hit.last_seen)}</td>
                      <td>
                        <button
                          className="btn btn-sm btn-primary"
                          disabled={busy || !reasonReady}
                          title={
                            reasonReady
                              ? "Reconstruct this vehicle's movement"
                              : "Give a reason first — it is recorded"
                          }
                          onClick={() => void trace(hit.plate)}
                        >
                          Trace movement
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {track && track.points.length === 0 && (
          <Card title="Nothing to plot">
            <div className="px-6 py-6 text-body-sm text-muted">
              No sightings of <span className="mono">{track.query}</span> in this window.
            </div>
          </Card>
        )}

        {track && track.points.length > 0 && (
          <>
            <div className="flex flex-wrap gap-2 text-body-sm">
              <Pill tone="info">{track.points.length} sightings</Pill>
              <Pill tone="info">{track.cameras_seen} cameras</Pill>
              <Pill tone={track.exact_reads === track.points.length ? "ok" : "warn"}>
                {track.exact_reads}/{track.points.length} exact
              </Pill>
              {track.total_distance_km > 0 && (
                <Pill tone="idle">{track.total_distance_km} km between cameras</Pill>
              )}
              {track.implausible_legs > 0 && (
                <Pill tone="bad">
                  {track.implausible_legs} impossible leg
                  {track.implausible_legs === 1 ? "" : "s"}
                </Pill>
              )}
            </div>

            <Notice tone="info">{track.caveat}</Notice>

            {!pursuits.some((p) => p.plate === track.query) && (
              <div className="flex flex-wrap items-center gap-3">
                <button className="btn btn-primary" disabled={!reasonReady} onClick={() => void follow(track.query)}>
                  Follow this vehicle live
                </button>
                <span className="text-caption text-muted">
                  The cameras where it was last seen, and every camera it could have reached since, get first claim on frames,
                  and every vehicle they see is checked against this plate. Matches appear here as
                  possible sightings, with the plate crop, for you to confirm.
                </span>
              </div>
            )}

            {track.implausible_legs > 0 && (
              <Notice tone="bad" title="This route contains a leg no road vehicle could drive">
                At least one pair of consecutive sightings is too far apart for the time between
                them. The usual cause is that one of the two reads belongs to a different vehicle
                whose plate is a near match. Check those frames before relying on this route.
              </Notice>
            )}

            <TrackMap points={track.points} plate={track.query} />

            <Card title="Movement history">
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Time</th>
                      <th>Camera</th>
                      <th>Location</th>
                      <th>Department</th>
                      <th>Read</th>
                      <th>Frames agreed</th>
                      <th>From previous</th>
                    </tr>
                  </thead>
                  <tbody>
                    {track.points.map((point, index) => (
                      <tr
                        key={point.sighting_id}
                        className={point.implausible_leg ? "bg-canvas-soft" : ""}
                      >
                        <td className="tabular">{index + 1}</td>
                        <td className="whitespace-nowrap">
                          <div>{ist(point.timestamp_utc)}</div>
                          {point.video_time && (
                            <div className="text-caption">
                              On footage {point.video_time.replace("T", " ").slice(0, 19)}
                            </div>
                          )}
                          <div className="text-caption text-muted">
                            {relative(point.timestamp_utc)}
                          </div>
                        </td>
                        <td>
                          <Link
                            className="link"
                            href={`/registry/${encodeURIComponent(point.camera_id)}`}
                          >
                            {point.camera_name ?? point.camera_id}
                          </Link>
                        </td>
                        <td className="text-caption">
                          {[point.road_or_junction, point.district, point.city]
                            .filter(Boolean)
                            .join(" · ") || "—"}
                          {point.latitude === null && (
                            <div
                              className="text-muted"
                              title="This camera has no recorded coordinates, so it is on the timeline but not on the map."
                            >
                              not plottable
                            </div>
                          )}
                        </td>
                        <td>
                          {point.owning_department && (
                            <DepartmentTag department={point.owning_department} />
                          )}
                        </td>
                        <td className="mono whitespace-nowrap">
                          {point.plate_read}
                          {!point.exact && (
                            <span className="ml-1 text-caption text-warn">
                              ~{point.match_distance.toFixed(2)}
                            </span>
                          )}
                          {point.possible && (
                            <div className="mt-1 font-sans">
                              <Pill tone="warn">possible: confirm from the crop</Pill>
                              <a href={api.pursuitEvidenceUrl(point.sighting_id)} target="_blank" rel="noreferrer">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                  alt="Plate crop behind this possible sighting"
                                  className="mt-1 max-h-12 rounded-sm"
                                  loading="lazy"
                                  src={api.pursuitEvidenceUrl(point.sighting_id)}
                                />
                              </a>
                            </div>
                          )}
                        </td>
                        <td className="tabular">
                          {point.observations}
                          {point.observations === 1 && (
                            <span
                              className="ml-1 text-caption text-warn"
                              title="A single-frame read has no agreement behind it."
                            >
                              single frame
                            </span>
                          )}
                        </td>
                        <td className="whitespace-nowrap text-caption">
                          {point.seconds_from_previous === null ? (
                            "—"
                          ) : (
                            <>
                              <div>{formatGap(point.seconds_from_previous)}</div>
                              {point.distance_from_previous_km !== null && (
                                <div
                                  className={point.implausible_leg ? "text-bad" : "text-muted"}
                                >
                                  {point.distance_from_previous_km} km
                                  {point.implied_speed_kmh !== null &&
                                    ` · ${point.implied_speed_kmh} km/h`}
                                </div>
                              )}
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        )}
      </div>
    </>
  );
}

function formatGap(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
}
