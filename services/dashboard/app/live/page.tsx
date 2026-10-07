"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { LiveTile } from "@/components/LiveTile";
import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  EmptyState,
  FloatInput,
  FloatSelect,
  FloatTextarea,
  Notice,
  PageHeader,
} from "@/components/ui";
import { api } from "@/lib/api";
import { usePersisted } from "@/lib/persist";
import { relative } from "@/lib/format";
import { streamQueue } from "@/lib/streamQueue";
import type { Camera, Sighting } from "@/lib/types";

/**
 * The live wall: every camera this account may watch, on one screen.
 *
 * A reason is mandatory before anything opens, exactly as on the single-camera
 * player. Opening thirty feeds is thirty audited accesses, and the audit trail
 * is worth no less here just because the tiles are small.
 *
 * Tiles hold a session only while on screen — see LiveTile.
 */
export default function LiveWallPage() {
  const router = useRouter();
  const [cameras, setCameras] = useState<Camera[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Everything but the password survives a reload of the tab.
  const [department, setDepartment] = usePersisted("live.department", "");
  const [district, setDistrict] = usePersisted("live.district", "");
  const [reason, setReason] = usePersisted("live.reason", "");
  const [password, setPassword] = useState("");
  const [startedBefore, setStarted] = usePersisted("live.started", false);
  // Wall mode: every feed on one screen at once, nothing else. Tiles stay
  // mounted across the switch (same element, different classes), so the
  // sessions already open are kept rather than reopened.
  const [wall, setWall] = usePersisted("live.wall", false);
  // Snapshot mode: the wall shows server-decoded still frames instead of HLS.
  // The grid's HLS CDN is far too slow to feed a browser player (a 6 s segment
  // takes 15-80 s and 403s under load), so an HLS wall of thirty tiles blacks
  // out; snapshots come off the fast RTSP path and always render. On by
  // default because it is the only path that works on this network.
  const [snapshot, setSnapshot] = usePersisted("live.snapshot", true);
  // A snapshot wall opens no session, so it can come back on its own after a
  // reload; a full-motion one needs the password typed again.
  const started = startedBefore && (snapshot || password.length > 0);
  const [viewport, setViewport] = useState({ w: 1920, h: 1080 });
  const [plates, setPlates] = useState<Sighting[]>([]);

  useEffect(() => {
    const measure = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  useEffect(() => {
    if (!wall) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setWall(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [wall]);

  useEffect(() => {
    void api
      .cameras({})
      .then(setCameras)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  // Plate reads for the whole wall, polled once rather than per tile: one
  // request and one audited disclosure per interval. A read lands when a
  // vehicle's track closes at the edge, so polling faster would not make it
  // any fresher.
  useEffect(() => {
    if (!started) return;
    let alive = true;
    const load = () => {
      void api
        .sightings({ since_hours: "1", limit: "500" })
        .then((rows) => {
          // Under 10% is noise on a glance view; the report still lists every read.
          if (alive) setPlates(rows.filter((p) => p.confidence >= 0.1));
        })
        .catch(() => undefined);
    };
    load();
    const timer = setInterval(load, 20_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [started]);

  const watchable = useMemo(
    () =>
      (cameras ?? []).filter(
        (c) => c.video_access === "live_and_playback" || c.video_access === "live_only",
      ),
    [cameras],
  );

  const departments = useMemo(
    () => [...new Set(watchable.map((c) => c.owning_department))].sort(),
    [watchable],
  );
  const districts = useMemo(
    () =>
      [...new Set(watchable.map((c) => c.location.district).filter(Boolean))].sort(),
    [watchable],
  );

  const shown = useMemo(
    () =>
      watchable.filter(
        (c) =>
          (!department || c.owning_department === department) &&
          (!district || c.location.district === district),
      ),
    [watchable, department, district],
  );

  const platesByCamera = useMemo(() => {
    const out: Record<string, Sighting[]> = {};
    for (const p of plates) (out[p.camera_id] ??= []).push(p);
    return out;
  }, [plates]);

  const columns = bestColumns(shown.length, viewport.w, viewport.h);
  const canStart =
    reason.trim().length >= 5 && (snapshot || password.length > 0) && watchable.length > 0;

  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!cameras) return <LoadingPanel />;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Live wall"
        subtitle={`${watchable.length} camera${watchable.length === 1 ? "" : "s"} your account may watch`}
      />

      {!started ? (
        <Card>
          <form
            className="space-y-3 px-6 py-5"
            onSubmit={(event) => {
              event.preventDefault(); // Enter in the password starts the wall
              if (canStart) setStarted(true);
            }}
          >
            <FloatTextarea
              label="Why are you viewing these feeds?"
              required
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              hint="e.g. Evening peak monitoring across the Ahmedabad corridor"
            />
            <FloatInput
              label="Confirm your password"
              required
              className="max-w-xs"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              hint="Re-enter to open the wall"
            />
            <p className="text-caption text-muted">
              Every tile opens its own watermarked session, audited under this reason. Feeds start
              a few at a time and only while a tile is on screen.
            </p>
            <label className="flex items-start gap-3 text-body-sm text-ink-soft">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={snapshot}
                onChange={(event) => setSnapshot(event.target.checked)}
              />
              Snapshot wall (recommended) — every camera at once, as live frames
              with the edge&apos;s vehicle detection drawn in. Uncheck for full-motion
              video, which the grid CDN is currently too slow to serve.
            </label>
            <button className="btn btn-primary btn-lg" type="submit" disabled={!canStart}>
              Start {shown.length} feed{shown.length === 1 ? "" : "s"}
            </button>
          </form>
        </Card>
      ) : (
        <Card>
          <div className="flex flex-wrap items-start gap-3 px-6 py-5">
            <FloatSelect
              label="Department"
                value={department}
                onChange={(event) => setDepartment(event.target.value)}
            >
                <option value="">All</option>
                {departments.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
            </FloatSelect>
            <FloatSelect
              label="District"
                value={district}
                onChange={(event) => setDistrict(event.target.value)}
            >
                <option value="">All</option>
                {districts.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
            </FloatSelect>
            <div className="ml-auto flex h-12 items-center gap-3">
              <span className="text-caption text-muted">
                showing {shown.length} of {watchable.length}
              </span>
              <QueueStatus />
              <button
                className="btn btn-primary"
                disabled={shown.length === 0}
                onClick={() => setWall(true)}
                title="Every feed on one screen at once (Esc to leave)"
              >
                Fit all on screen
              </button>
              <button
                className="btn"
                onClick={() => {
                  setStarted(false);
                  setWall(false);
                  setPassword("");
                }}
              >
                Stop all
              </button>
            </div>
          </div>
        </Card>
      )}

      {started && !wall && plates.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-caption">
          <span className="font-semibold text-ink">Latest plate reads</span>
          {plates.slice(0, 8).map((p) => (
            <span key={p.sighting_id} className="rounded-full bg-canvas-soft px-3 py-1">
              <span className="font-mono font-semibold">
                {p.plate_withheld ? "withheld" : p.plate_text}
              </span>{" "}
              <span className="text-ink/65">
                {p.camera_name ?? p.camera_id} · {Math.round(p.confidence * 100)}% ·{" "}
                {relative(p.timestamp_utc)}
              </span>
            </span>
          ))}
        </div>
      )}

      {started &&
        (shown.length === 0 ? (
          <EmptyState
            message="Nothing to show"
            hint="No camera matches these filters that this account may watch."
          />
        ) : (
          <div
            className={
              wall
                ? "fixed inset-0 z-[70] !mt-0 grid h-screen w-screen gap-0.5 bg-black p-0.5"
                : "grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4"
            }
            style={
              wall
                ? {
                    gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                    gridAutoRows: "minmax(0, 1fr)",
                  }
                : undefined
            }
          >
            {shown.map((camera) => (
              <LiveTile
                key={camera.camera_id}
                camera={camera}
                reason={reason.trim()}
                password={password}
                compact={wall}
                snapshot={snapshot}
                plates={platesByCamera[camera.camera_id]}
                onOpenFull={(id) => router.push(`/registry/${encodeURIComponent(id)}`)}
              />
            ))}
          </div>
        ))}

      {started && wall && (
        <div className="fixed right-3 top-3 z-[80] !mt-0 flex items-center gap-3 rounded-full bg-black/70 py-1 pl-4 pr-1 text-caption text-white">
          <span>
            {shown.length} feed{shown.length === 1 ? "" : "s"} · {columns} per row
          </span>
          <QueueStatus />
          <button className="btn btn-sm" onClick={() => setWall(false)}>
            Exit wall (Esc)
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * The column count that gives `n` 16:9 tiles the largest size on a `w`×`h`
 * viewport with none of them scrolled off screen. Brute force: n is thirty.
 */
function bestColumns(n: number, w: number, h: number): number {
  if (n <= 1) return 1;
  let best = 1;
  let bestSize = 0;
  for (let cols = 1; cols <= n; cols += 1) {
    const rows = Math.ceil(n / cols);
    const size = Math.min(w / cols, (h / rows) * (16 / 9));
    if (size > bestSize) {
      bestSize = size;
      best = cols;
    }
  }
  return best;
}

/**
 * How far through the start queue the wall is.
 *
 * Without it, a wall filling in waves is indistinguishable from a wall that
 * has quietly given up on the tiles below the fold.
 */
function QueueStatus() {
  const [state, setState] = useState({ starting: 0, pending: 0 });
  useEffect(() => {
    const tick = setInterval(
      () => setState({ starting: streamQueue.starting, pending: streamQueue.pending }),
      400,
    );
    return () => clearInterval(tick);
  }, []);

  if (state.starting === 0 && state.pending === 0) return null;
  return (
    <span className="pill text-ink/65">
      {state.starting} starting
      {state.pending > 0 ? ` · ${state.pending} queued` : ""}
    </span>
  );
}
