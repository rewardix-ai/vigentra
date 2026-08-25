"use client";

import Link from "next/link";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { LoadingPanel } from "@/components/Shell";
import {
  Card,
  DepartmentTag,
  EmptyState,
  FootageNotice,
  HealthPill,
  InstallationPill,
  Notice,
  PageHeader,
  SyncPill,
  VideoStatePill,
} from "@/components/ui";
import { api } from "@/lib/api";
import { cameraCsvHeader, cameraCsvRow, download, toCSV } from "@/lib/csv";
import { ist, relative } from "@/lib/format";
import type { Camera } from "@/lib/types";

/**
 * Leaflet needs `window`, so the map is loaded only in the browser. The stub
 * fallback keeps the layout tall enough that the switch to "Map" doesn't jump.
 */
const RegistryMap = dynamic(
  () => import("@/components/RegistryMap").then((mod) => mod.RegistryMap),
  {
    ssr: false,
    loading: () => (
      <div className="card flex h-[520px] items-center justify-center text-[13px] text-ink-500">
        Loading map…
      </div>
    ),
  },
);

interface Filters {
  q: string;
  owning_department: string;
  district: string;
  status: string;
  installation_status: string;
}

const EMPTY: Filters = {
  q: "",
  owning_department: "",
  district: "",
  status: "",
  installation_status: "",
};

/** States in which this account may actually open a session. */
const WATCHABLE_STATES = new Set<string>(["live_and_playback", "live_only", "playback_only"]);

type View = "table" | "map";

function Registry() {
  const params = useSearchParams();
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>((params.get("view") as View) === "map" ? "map" : "table");
  const [filters, setFilters] = useState<Filters>({
    ...EMPTY,
    installation_status: params.get("installation_status") ?? "",
    owning_department: params.get("owning_department") ?? "",
  });

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .cameras({})
        .then((rows) => {
          if (!cancelled) {
            setCameras(rows);
            setError(null);
          }
        })
        .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)))
        .finally(() => !cancelled && setLoading(false));

    void load();
    const timer = setInterval(load, 15_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // If this account can watch even one camera in the list, the custody
  // notice is answering a question it did not ask.
  const anyWatchable = useMemo(
    () => cameras.some((camera) => WATCHABLE_STATES.has(camera.video_access)),
    [cameras],
  );

  const options = useMemo(() => {
    const unique = (values: (string | null | undefined)[]) =>
      Array.from(new Set(values.filter(Boolean) as string[])).sort();
    return {
      departments: unique(cameras.map((c) => c.owning_department)),
      districts: unique(cameras.map((c) => c.location.district)),
      statuses: unique(cameras.map((c) => c.health.status)),
      installation: unique(cameras.map((c) => c.installation.installation_status)),
    };
  }, [cameras]);

  const visible = useMemo(() => {
    const needle = filters.q.trim().toLowerCase();
    return cameras.filter((camera) => {
      if (filters.owning_department && camera.owning_department !== filters.owning_department) return false;
      if (filters.district && camera.location.district !== filters.district) return false;
      if (filters.status && camera.health.status !== filters.status) return false;
      if (
        filters.installation_status &&
        camera.installation.installation_status !== filters.installation_status
      ) {
        return false;
      }
      if (
        needle &&
        !camera.name.toLowerCase().includes(needle) &&
        !camera.camera_id.toLowerCase().includes(needle) &&
        !camera.external_camera_id.toLowerCase().includes(needle)
      ) {
        return false;
      }
      return true;
    });
  }, [cameras, filters]);

  const dirty = Object.values(filters).some(Boolean);
  const set = (patch: Partial<Filters>) => setFilters((current) => ({ ...current, ...patch }));

  function exportCSV() {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const contents = toCSV(visible.map(cameraCsvRow), cameraCsvHeader());
    download(`sentinel-cameras-${stamp}.csv`, contents);
  }

  if (loading) return <LoadingPanel label="Loading camera registry" />;

  return (
    <>
      <PageHeader
        title="Camera registry"
        subtitle="Every department's cameras in one place. Footage stays with the unit that owns it."
        actions={
          <>
            <div
              role="group"
              aria-label="Registry view"
              className="inline-flex overflow-hidden rounded border border-line"
            >
              <button
                type="button"
                aria-pressed={view === "table"}
                onClick={() => setView("table")}
                className={`px-3 py-1.5 text-[13px] transition ${
                  view === "table"
                    ? "bg-brand-600 text-white"
                    : "bg-white text-ink-700 hover:bg-brand-50"
                }`}
              >
                Table
              </button>
              <button
                type="button"
                aria-pressed={view === "map"}
                onClick={() => setView("map")}
                className={`border-l border-line px-3 py-1.5 text-[13px] transition ${
                  view === "map"
                    ? "bg-brand-600 text-white"
                    : "bg-white text-ink-700 hover:bg-brand-50"
                }`}
              >
                Map
              </button>
            </div>
            <button
              className="btn"
              onClick={exportCSV}
              disabled={visible.length === 0}
              title="Download the currently visible rows as CSV"
            >
              Export CSV
            </button>
          </>
        }
      />

      <div className="space-y-3">
        {!anyWatchable && <FootageNotice compact />}
        {error && <Notice tone="bad">{error}</Notice>}

        {/* Filters */}
        <div className="card px-3 py-2.5">
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-5">
            <label className="block lg:col-span-1">
              <span className="field-label">Search</span>
              <input
                className="input mt-1"
                placeholder="Name or camera ID…"
                value={filters.q}
                onChange={(event) => set({ q: event.target.value })}
              />
            </label>
            <label className="block">
              <span className="field-label">Department</span>
              <select
                className="select mt-1"
                value={filters.owning_department}
                onChange={(event) => set({ owning_department: event.target.value })}
              >
                <option value="">All</option>
                {options.departments.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="field-label">District</span>
              <select
                className="select mt-1"
                value={filters.district}
                onChange={(event) => set({ district: event.target.value })}
              >
                <option value="">All</option>
                {options.districts.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="field-label">Installation status</span>
              <select
                className="select mt-1"
                value={filters.installation_status}
                onChange={(event) => set({ installation_status: event.target.value })}
              >
                <option value="">All</option>
                {options.installation.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="field-label">Health</span>
              <select
                className="select mt-1"
                value={filters.status}
                onChange={(event) => set({ status: event.target.value })}
              >
                <option value="">All</option>
                {options.statuses.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="mt-2.5 flex items-center justify-between border-t border-line pt-2">
            <span className="text-2xs text-ink-500">
              Showing <span className="tabular font-medium text-ink-900">{visible.length}</span> of{" "}
              <span className="tabular font-medium text-ink-900">{cameras.length}</span> registered
              cameras
            </span>
            {dirty && (
              <button className="btn btn-sm" onClick={() => setFilters(EMPTY)}>
                Clear filters
              </button>
            )}
          </div>
        </div>

        {view === "map" ? (
          <RegistryMap cameras={visible} />
        ) : (
          <Card>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Camera ID</th>
                    <th>Camera name</th>
                    <th>Department</th>
                    <th>District</th>
                    <th>Source system</th>
                    <th>Installation</th>
                    <th>Approval</th>
                    <th>Health</th>
                    <th>Last metadata sync</th>
                    <th>Video</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((camera) => (
                    <tr key={camera.camera_id}>
                      <td className="mono whitespace-nowrap">{camera.camera_id}</td>
                      <td>
                        <Link
                          href={`/registry/${encodeURIComponent(camera.camera_id)}`}
                          className="font-medium text-brand-600 hover:underline"
                        >
                          {camera.name}
                        </Link>
                        <div className="mono text-ink-400">{camera.external_camera_id}</div>
                      </td>
                      <td>
                        <DepartmentTag department={camera.owning_department} />
                      </td>
                      <td>{camera.location.district}</td>
                      <td className="text-ink-500">{camera.source_system}</td>
                      <td>
                        <InstallationPill status={camera.installation.installation_status} />
                      </td>
                      <td>
                        <SyncPill status={camera.sentinel_sync.status} />
                      </td>
                      <td>
                        <HealthPill status={camera.health.status} />
                      </td>
                      <td>
                        <VideoStatePill state={camera.video_access} />
                      </td>
                      <td
                        className="whitespace-nowrap text-ink-500"
                        title={ist(camera.sentinel_sync.synced_at_utc)}
                      >
                        {relative(camera.sentinel_sync.synced_at_utc)}
                      </td>
                      <td className="whitespace-nowrap">
                        <div className="flex gap-1.5">
                          {WATCHABLE_STATES.has(camera.video_access) && (
                            <Link
                              className="btn btn-sm btn-primary"
                              href={`/registry/${encodeURIComponent(camera.camera_id)}?watch=${
                                camera.video_access === "playback_only" ? "playback" : "live"
                              }`}
                            >
                              Watch
                            </Link>
                          )}
                          <Link
                            className="btn btn-sm"
                            href={`/registry/${encodeURIComponent(camera.camera_id)}`}
                          >
                            Details
                          </Link>
                          <Link
                            className="btn btn-sm"
                            href={`/registry/${encodeURIComponent(camera.camera_id)}/policy`}
                          >
                            Policy
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {visible.length === 0 && (
              <EmptyState
                message="No cameras match the current filters."
                hint="Records appear here once their own department's validation passes."
              />
            )}
          </Card>
        )}

        <p className="text-2xs leading-relaxed text-ink-400">
          Every field in this table is asset metadata. The <strong>Video</strong> column is the
          one thing computed for you personally: it says what you may do with that camera&rsquo;s
          footage right now. <em>Request from owner</em> means the camera belongs to another unit
          &mdash; open it and ask, with a reason. Every session you open is watermarked and
          audited. Map tiles courtesy of OpenStreetMap contributors; coordinates are
          clearly-labelled demonstration values.
        </p>
      </div>
    </>
  );
}

export default function RegistryPage() {
  return (
    <Suspense fallback={<LoadingPanel label="Loading camera registry" />}>
      <Registry />
    </Suspense>
  );
}
