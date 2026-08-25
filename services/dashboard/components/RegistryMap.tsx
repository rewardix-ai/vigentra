"use client";

/**
 * Interactive registry map.
 *
 * Leaflet + OpenStreetMap tiles. No API key, no vendor account, and no data
 * leaves the browser except the tile requests to openstreetmap.org.
 *
 * Marker semantics: shape (circle) is constant, fill = department, ring = health
 * status. Withdrawn cameras use a hatched fill so an operator can spot
 * suspended / decommissioned assets at a glance.
 *
 * Coordinates are synthetic demo values, labelled as such in the popup.
 */
import { useEffect, useMemo, useRef } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from "react-leaflet";
import Link from "next/link";
import type { LatLngExpression, LatLngBoundsExpression } from "leaflet";

// Leaflet's CSS is handled by the framework bundler and has no TypeScript declarations.
// @ts-expect-error -- side-effect CSS import
import "leaflet/dist/leaflet.css";
import type { Camera, CameraHealthStatus } from "@/lib/types";
import { calendarDate, relative, titleise } from "@/lib/format";
import { HealthPill, InstallationPill, DepartmentTag } from "./ui";

const AHMEDABAD: LatLngExpression = [23.033, 72.585];

const DEPARTMENT_FILL: Record<string, string> = {
  "Traffic Police": "#1b4f9c",
  "Municipal Corporation": "#7a3fa1",
};

const HEALTH_RING: Record<CameraHealthStatus, string> = {
  online: "#1a7f47",
  degraded: "#a2680a",
  offline: "#b3261e",
  unavailable: "#5b6670",
  unknown: "#7b858f",
};

interface Placed {
  camera: Camera;
  lat: number;
  lng: number;
}

function placeable(cameras: Camera[]): Placed[] {
  return cameras
    .filter(
      (camera) =>
        typeof camera.location.latitude === "number" &&
        typeof camera.location.longitude === "number",
    )
    .map((camera) => ({
      camera,
      lat: camera.location.latitude as number,
      lng: camera.location.longitude as number,
    }));
}

/** Recompute bounds whenever the visible list changes. */
function FitToPoints({ points }: { points: Placed[] }) {
  const map = useMap();
  const fittedSignature = useRef<string | null>(null);
  useEffect(() => {
    if (points.length === 0) return;
    const signature = points
      .map((point) => `${point.camera.camera_id}:${point.lat}:${point.lng}`)
      .join("|");
    if (signature === fittedSignature.current) return;
    fittedSignature.current = signature;
    if (points.length === 1) {
      map.setView([points[0].lat, points[0].lng], 14);
      return;
    }
    const bounds: LatLngBoundsExpression = points.map((p) => [p.lat, p.lng]);
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
  }, [map, points]);
  return null;
}

export function RegistryMap({ cameras }: { cameras: Camera[] }) {
  const points = useMemo(() => placeable(cameras), [cameras]);
  const containerRef = useRef<HTMLDivElement>(null);

  // Leaflet ships icon URLs that break under Webpack; we use CircleMarker
  // instead of the default marker, so no icon-path patch is needed.

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div>
          <div className="text-[13px] font-semibold">
            Registry map ·{" "}
            <span className="tabular font-mono text-ink-500">
              {points.length}/{cameras.length}
            </span>{" "}
            plotted
          </div>
          <p className="mt-0.5 text-2xs text-ink-500">
            Cameras with recorded coordinates. Fill by department, ring by health.
            Synthetic demonstration coordinates around Ahmedabad.
          </p>
        </div>
        <MapLegend />
      </div>
      <div ref={containerRef} className="relative h-[520px] w-full bg-[#eaeef3]">
        <MapContainer
          center={AHMEDABAD}
          zoom={12}
          scrollWheelZoom
          style={{ height: "100%", width: "100%" }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FitToPoints points={points} />
          {points.map(({ camera, lat, lng }) => {
            const isWithdrawn = camera.installation.installation_status !== "COMMISSIONED";
            const fill = DEPARTMENT_FILL[camera.owning_department] ?? "#5b6670";
            const ring = HEALTH_RING[camera.health.status as CameraHealthStatus] ?? "#7b858f";
            return (
              <CircleMarker
                key={camera.camera_id}
                center={[lat, lng]}
                radius={9}
                pathOptions={{
                  color: ring,
                  weight: 3,
                  fillColor: fill,
                  fillOpacity: isWithdrawn ? 0.35 : 0.85,
                  dashArray: isWithdrawn ? "3 3" : undefined,
                }}
              >
                <Popup>
                  <div className="min-w-[240px] font-sans">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="text-[13px] font-semibold text-ink-900">
                          {camera.name}
                        </div>
                        <div className="mono text-2xs text-ink-500">{camera.camera_id}</div>
                      </div>
                      <HealthPill status={camera.health.status} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <DepartmentTag department={camera.owning_department} />
                      <InstallationPill status={camera.installation.installation_status} />
                    </div>
                    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-2xs text-ink-700">
                      <dt className="text-ink-400">Type</dt>
                      <dd>{titleise(camera.camera_type)}</dd>
                      <dt className="text-ink-400">Location</dt>
                      <dd>{camera.location.road_or_junction ?? "—"}</dd>
                      <dt className="text-ink-400">Coordinates</dt>
                      <dd className="mono">
                        {lat.toFixed(5)}, {lng.toFixed(5)}
                      </dd>
                      <dt className="text-ink-400">Commissioned</dt>
                      <dd>{calendarDate(camera.installation.commissioning_date)}</dd>
                      <dt className="text-ink-400">Last frame</dt>
                      <dd>{relative(camera.health.last_frame_utc)}</dd>
                    </dl>
                    <Link
                      href={`/registry/${encodeURIComponent(camera.camera_id)}`}
                      className="mt-3 inline-block text-2xs font-semibold text-brand-600 hover:underline"
                    >
                      Open full record →
                    </Link>
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </div>
      {points.length === 0 && (
        <div className="border-t border-line px-4 py-6 text-center text-[13px] text-ink-500">
          No cameras with coordinates in the current filter.
        </div>
      )}
    </div>
  );
}

function MapLegend() {
  return (
    <div className="flex flex-wrap items-center gap-3 text-2xs">
      <span className="flex items-center gap-1.5 text-ink-500">
        <span className="mono uppercase tracking-wider text-ink-400">Dept:</span>
        <Dot fill="#1b4f9c" /> Traffic
        <Dot fill="#7a3fa1" /> Municipal
      </span>
      <span className="flex items-center gap-1.5 text-ink-500">
        <span className="mono uppercase tracking-wider text-ink-400">Health:</span>
        <Ring color="#1a7f47" /> online
        <Ring color="#a2680a" /> degraded
        <Ring color="#b3261e" /> offline
      </span>
    </div>
  );
}

function Dot({ fill }: { fill: string }) {
  return (
    <span
      aria-hidden
      className="inline-block h-2.5 w-2.5 rounded-full"
      style={{ background: fill }}
    />
  );
}

function Ring({ color }: { color: string }) {
  return (
    <span
      aria-hidden
      className="inline-block h-2.5 w-2.5 rounded-full"
      style={{ border: `2px solid ${color}` }}
    />
  );
}

export default RegistryMap;
