"use client";

/**
 * A reconstructed vehicle route, on the map.
 *
 * Leaflet + OpenStreetMap, the same stack as the registry map and for the same
 * reasons: no API key, no vendor account, nothing leaves the browser but tile
 * requests.
 *
 * Three things this drawing is careful about, because a map is persuasive in a
 * way a table is not and it would be easy to draw something more certain than
 * the evidence:
 *
 * **The line is not the route.** It joins cameras that read the plate, in time
 * order. What the vehicle did between two cameras is unknown, so the line is
 * dashed — it is an inference, not a trace.
 *
 * **A near match is drawn differently.** A camera that read the plate exactly
 * gets a solid marker; one that matched fuzzily gets a hollow one. Both belong
 * on the map; presenting them identically would not.
 *
 * **An impossible leg is drawn in red and labelled.** If two reads imply
 * 400 km/h, one of them is almost certainly a different vehicle. Hiding that
 * leg would make the route look cleaner and be less true.
 */
import { useEffect, useMemo, useRef } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap } from "react-leaflet";
import type { LatLngBoundsExpression, LatLngExpression } from "leaflet";

import "leaflet/dist/leaflet.css";
import type { TrackPoint } from "@/lib/types";
import { ist } from "@/lib/format";

const GUJARAT: LatLngExpression = [22.66, 71.75];

const EXACT_COLOUR = "#1b4f9c";
const NEAR_COLOUR = "#a2680a";
const IMPLAUSIBLE_COLOUR = "#b3261e";

interface Placed {
  point: TrackPoint;
  lat: number;
  lng: number;
  order: number;
}

function placeable(points: TrackPoint[]): Placed[] {
  return points
    .map((point, index) => ({ point, order: index + 1 }))
    .filter(
      (entry) =>
        typeof entry.point.latitude === "number" &&
        typeof entry.point.longitude === "number",
    )
    .map((entry) => ({
      ...entry,
      lat: entry.point.latitude as number,
      lng: entry.point.longitude as number,
    }));
}

function FitToRoute({ points }: { points: Placed[] }) {
  const map = useMap();
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    if (points.length === 0) return;
    const signature = points.map((p) => `${p.point.sighting_id}`).join("|");
    if (signature === fitted.current) return;
    fitted.current = signature;
    if (points.length === 1) {
      map.setView([points[0].lat, points[0].lng], 14);
      return;
    }
    const bounds: LatLngBoundsExpression = points.map((p) => [p.lat, p.lng]);
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
  }, [map, points]);
  return null;
}

export function TrackMap({ points, plate }: { points: TrackPoint[]; plate: string }) {
  const placed = useMemo(() => placeable(points), [points]);
  const unplaced = points.length - placed.length;

  // One segment per leg rather than a single polyline, so a leg that implies an
  // impossible speed can be coloured on its own.
  const legs = useMemo(
    () =>
      placed.slice(1).map((entry, index) => ({
        from: placed[index],
        to: entry,
        implausible: entry.point.implausible_leg,
      })),
    [placed],
  );

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div>
          <div className="text-[13px] font-semibold">
            Movement of <span className="mono">{plate}</span> ·{" "}
            <span className="tabular font-mono text-ink-500">
              {placed.length}/{points.length}
            </span>{" "}
            plotted
          </div>
          <p className="mt-0.5 text-2xs text-ink-500">
            The dashed line joins cameras that read the plate, in time order. It is not the path
            the vehicle drove — what happened between two cameras is unknown.
            {unplaced > 0 && (
              <>
                {" "}
                {unplaced} sighting{unplaced === 1 ? "" : "s"} could not be plotted: those cameras
                have no recorded coordinates.
              </>
            )}
          </p>
        </div>
        <TrackLegend />
      </div>

      <div className="relative h-[460px] w-full bg-[#eaeef3]">
        <MapContainer
          center={GUJARAT}
          zoom={7}
          scrollWheelZoom
          style={{ height: "100%", width: "100%" }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <FitToRoute points={placed} />

          {legs.map((leg) => (
            <Polyline
              key={`${leg.from.point.sighting_id}-${leg.to.point.sighting_id}`}
              positions={[
                [leg.from.lat, leg.from.lng],
                [leg.to.lat, leg.to.lng],
              ]}
              pathOptions={{
                color: leg.implausible ? IMPLAUSIBLE_COLOUR : EXACT_COLOUR,
                weight: leg.implausible ? 3 : 2,
                opacity: 0.75,
                dashArray: "6 6",
              }}
            />
          ))}

          {placed.map((entry) => (
            <CircleMarker
              key={entry.point.sighting_id}
              center={[entry.lat, entry.lng]}
              radius={entry.order === 1 || entry.order === placed.length ? 9 : 7}
              pathOptions={{
                color: entry.point.exact ? EXACT_COLOUR : NEAR_COLOUR,
                weight: 2.5,
                // Hollow for a fuzzy match: the map should not present a
                // corrected reading with the same weight as a clean one.
                fillColor: entry.point.exact ? EXACT_COLOUR : "#ffffff",
                fillOpacity: entry.point.exact ? 0.85 : 1,
              }}
            >
              <Popup>
                <div className="space-y-1 text-[12px]">
                  <div className="font-semibold">
                    {entry.order}. {entry.point.camera_name ?? entry.point.camera_id}
                  </div>
                  <div>{ist(entry.point.timestamp_utc)}</div>
                  <div>
                    Read <span className="mono">{entry.point.plate_read}</span>{" "}
                    {entry.point.exact ? (
                      <span>(exact)</span>
                    ) : (
                      <span>(near, distance {entry.point.match_distance.toFixed(2)})</span>
                    )}
                  </div>
                  <div>
                    {entry.point.observations} frame
                    {entry.point.observations === 1 ? "" : "s"} agreed
                    {entry.point.observations === 1 && " — a single-frame read, treat with care"}
                  </div>
                  {entry.point.road_or_junction && <div>{entry.point.road_or_junction}</div>}
                  {[entry.point.district, entry.point.city].filter(Boolean).length > 0 && (
                    <div className="text-ink-500">
                      {[entry.point.district, entry.point.city].filter(Boolean).join(" · ")}
                    </div>
                  )}
                  {entry.point.implied_speed_kmh !== null && (
                    <div className={entry.point.implausible_leg ? "text-bad" : ""}>
                      {entry.point.distance_from_previous_km} km from the previous camera,
                      implying {entry.point.implied_speed_kmh} km/h
                      {entry.point.implausible_leg && " — not possible by road"}
                    </div>
                  )}
                  {entry.point.coverage_description && (
                    <div className="text-ink-500">{entry.point.coverage_description}</div>
                  )}
                </div>
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
    </div>
  );
}

function TrackLegend() {
  return (
    <div className="flex flex-wrap items-center gap-3 text-2xs text-ink-600">
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-3 w-3 rounded-full"
          style={{ background: EXACT_COLOUR, border: `2px solid ${EXACT_COLOUR}` }}
        />
        exact read
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-3 w-3 rounded-full bg-white"
          style={{ border: `2px solid ${NEAR_COLOUR}` }}
        />
        near match
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block h-0.5 w-5"
          style={{ background: IMPLAUSIBLE_COLOUR }}
        />
        impossible leg
      </span>
    </div>
  );
}
