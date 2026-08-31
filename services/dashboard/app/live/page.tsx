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
              Confirmed once for the wall, then re-checked by the API on every session it opens.
              Each tile opens its own short-lived, watermarked session and is written to the audit
              trail under this reason. Feeds start only when a tile is on screen, and stop when it
              scrolls away.
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
              <button
                className="btn"
                onClick={() => {
                  setStarted(false);
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
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
            {shown.map((camera) => (
              <LiveTile
                key={camera.camera_id}
                camera={camera}
                reason={reason.trim()}
                password={password}
                onOpenFull={(id) => router.push(`/registry/${encodeURIComponent(id)}`)}
              />
            ))}
          </div>
        ))}
    </div>
  );
}
