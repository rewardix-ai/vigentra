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
import { useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, CircleMarker, Popup, useMap } from "react-leaflet";
import Link from "next/link";
import type { LatLngExpression, LatLngBoundsExpression } from "leaflet";

// Leaflet's CSS is handled by the framework bundler. Next's own ambient types
// already declare side-effect CSS imports, so no suppression is needed here -
// and a `@ts-expect-error` that suppresses nothing is itself a build error
// under `next build`, which checks unused directives.
// Leaflet does not expose a TypeScript declaration for its stylesheet.
// Next.js handles the CSS import at build time.

import "leaflet/dist/leaflet.css";
import type { Camera, CameraHealthStatus } from "@/lib/types";
import { calendarDate, relative, titleise } from "@/lib/format";
import { HealthPill, InstallationPill, DepartmentTag } from "./ui";
import { BASEMAPS, BasemapLayer, BasemapPicker } from "./BasemapPicker";
import { INDIA_BOUNDS, INDIA_MIN_ZOOM } from "@/lib/mapBounds";
import { CameraFieldOfView, bearingOf, isOmnidirectional } from "./CameraFieldOfView";

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
/**
 * Tell Leaflet how big it actually is.
 *
 * Leaflet measures its container once, at construction. The registry map is
 * built inside a tab that is display:none until "Map" is pressed, so it
 * measures zero, decides one tile covers the planet, and renders the whole
 * world repeated sideways with every camera collapsed onto one dot - which is
 * exactly what it did. Nothing about the data or the basemap is wrong; the
 * map simply never learned its own size.
 *
 * `invalidateSize` re-measures. It runs once on mount, and again on any
 * container resize, because the sidebar collapsing or the window changing
 * shape has the same effect on a smaller scale.
 */
function KeepMapSized({ containerRef }: { containerRef: React.RefObject<HTMLDivElement | null> }) {
  const map = useMap();
  useEffect(() => {
    const element = containerRef.current;
    // A frame's delay: on the render that reveals the tab the element has its
    // final height, but layout has not flushed when the effect first runs.
    const raf = requestAnimationFrame(() => map.invalidateSize());
    if (!element || typeof ResizeObserver === "undefined") {
      return () => cancelAnimationFrame(raf);
    }
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(element);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [map, containerRef]);
  return null;
}

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
    // Deferred a frame for the same reason as invalidateSize above: fitting
    // bounds to a container Leaflet still believes is 0x0 produces a
    // world-level zoom that then never corrects itself.
    requestAnimationFrame(() => {
      map.invalidateSize();
      map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
      if (map.getZoom() < INDIA_MIN_ZOOM) map.setZoom(INDIA_MIN_ZOOM);
    });
  }, [map, points]);
  return null;
}

export function RegistryMap({ cameras }: { cameras: Camera[] }) {
  const points = useMemo(() => placeable(cameras), [cameras]);
  const containerRef = useRef<HTMLDivElement>(null);
  const [basemapId, setBasemapId] = useState(BASEMAPS[0].id);
  const [showCoverage, setShowCoverage] = useState(true);
  const basemap = BASEMAPS.find((b) => b.id === basemapId) ?? BASEMAPS[0];

  // How many of the plotted cameras can say anything about where they look.
  // Worth stating rather than leaving the map to imply full coverage: a
  // bearing nobody surveyed is the difference between a coverage map and a
  // decorative one.
  const aimed = useMemo(
    () =>
      points.filter(
        ({ camera }) =>
          isOmnidirectional(camera) || bearingOf(camera.location?.view_direction) !== null,
      ).length,
    [points],
  );

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
            Wedges show where a camera looks; a dashed ring means steerable, so it
            points nowhere in particular. Range is indicative, not surveyed.{" "}
            <span className="tabular font-mono">{aimed}</span> of{" "}
            <span className="tabular font-mono">{points.length}</span> have a
            recorded bearing.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-2xs text-ink-600">
            <input
              type="checkbox"
              checked={showCoverage}
              onChange={(event) => setShowCoverage(event.target.checked)}
            />
            Show coverage
          </label>
          <MapLegend />
        </div>
      </div>
      <div ref={containerRef} className="relative h-[520px] w-full bg-[#eaeef3]">
        <BasemapPicker value={basemapId} onChange={setBasemapId} />
        <MapContainer
          center={AHMEDABAD}
          zoom={12}
          scrollWheelZoom
          // India only, as a hard stop. maxBoundsViscosity 1 refuses the drag
          // outright rather than letting it rubber-band: a map you can pull
          // off the country still shows the wrong thing while you are pulling.
          maxBounds={INDIA_BOUNDS}
          maxBoundsViscosity={1}
          minZoom={INDIA_MIN_ZOOM}
          style={{ height: "100%", width: "100%" }}
        >
          <BasemapLayer basemap={basemap} />
          <KeepMapSized containerRef={containerRef} />
          <FitToPoints points={points} />
          {showCoverage &&
            points.map(({ camera, lat, lng }) => (
              <CameraFieldOfView
                key={`fov-${camera.camera_id}`}
                camera={camera}
                lat={lat}
                lng={lng}
                colour={DEPARTMENT_FILL[camera.owning_department] ?? "#5b6670"}
                dimmed={camera.installation.installation_status !== "COMMISSIONED"}
              />
            ))}
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

