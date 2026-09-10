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
import { streamQueue } from "@/lib/streamQueue";
import type { Camera } from "@/lib/types";

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
  const [department, setDepartment] = useState("");
  const [district, setDistrict] = useState("");
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [started, setStarted] = useState(false);
  // Wall mode: every feed on one screen at once, nothing else. Tiles stay
  // mounted across the switch (same element, different classes), so the
  // sessions already open are kept rather than reopened.
  const [wall, setWall] = useState(false);
  const [viewport, setViewport] = useState({ w: 1920, h: 1080 });

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

  const columns = bestColumns(shown.length, viewport.w, viewport.h);

  if (error) return <Notice tone="bad">{error}</Notice>;
  if (!cameras) return <LoadingPanel />;

  return (
    <div className="space-y-3">
      <PageHeader
        title="Live wall"
        subtitle={`${watchable.length} camera${watchable.length === 1 ? "" : "s"} your account may watch`}
      />

      {!started ? (
        <Card>
          <div className="space-y-3 px-4 py-3">
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
            <p className="text-2xs text-ink-500">
              Every tile opens its own watermarked session, audited under this reason. Feeds start
              a few at a time and only while a tile is on screen.
            </p>
            <button
              className="btn btn-primary"
              disabled={
                reason.trim().length < 5 || password.length === 0 || watchable.length === 0
              }
              onClick={() => setStarted(true)}
            >
              Start {shown.length} feed{shown.length === 1 ? "" : "s"}
            </button>
          </div>
        </Card>
      ) : (
        <Card>
          <div className="flex flex-wrap items-end gap-3 px-4 py-3">
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
            <div className="ml-auto flex items-center gap-2">
              <span className="text-2xs text-ink-500">
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
                ? "fixed inset-0 z-[70] grid h-screen w-screen gap-0.5 bg-black p-0.5"
                : "grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4"
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
                onOpenFull={(id) => router.push(`/registry/${encodeURIComponent(id)}`)}
              />
            ))}
          </div>
        ))}

      {started && wall && (
        <div className="fixed right-2 top-2 z-[80] flex items-center gap-2 rounded bg-black/70 px-2 py-1 text-2xs text-white">
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
    <span className="rounded bg-brand-50 px-2 py-0.5 text-2xs text-ink-600">
      {state.starting} starting
      {state.pending > 0 ? ` · ${state.pending} queued` : ""}
    </span>
  );
}
