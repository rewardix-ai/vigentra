"use client";

import { useEffect, useState } from "react";
import { Circle, Polygon, Tooltip, useMap } from "react-leaflet";

import type { Camera } from "@/lib/types";

/**
 * What each camera can actually see, drawn on the map.
 *
 * A dot says a camera exists somewhere. It does not say whether the junction
 * to its east is covered, which is the question a coverage map is for - and
 * the one the gap-analysis report answers in prose while the map stays silent.
 * A wedge pointed the way the camera faces answers it at a glance, and two
 * wedges that overlap show duplicated spend the same way.
 *
 * The wedge is a claim about coverage, so it is drawn only where there is
 * evidence for one. A camera whose survey never recorded a bearing gets no
 * wedge at all rather than one pointing north by default: an invented
 * direction on a map is indistinguishable from a surveyed one, and somebody
 * will plan around it.
 *
 * PTZ cameras get a ring instead. They are not pointed anywhere in
 * particular - the operator moves them - so a wedge would be the most
 * confident and least true thing on the screen.
 *
 * Range is a drawing convention, not a measurement. Nothing in the registry
 * records how far any of these cameras usefully sees, and it varies with lens,
 * mounting height and what is parked in front of it. They are labelled as
 * indicative in the legend; treating them as a survey product would be the
 * error this comment exists to prevent.
 *
 * Because the range is a convention rather than a fact, it is drawn at a
 * constant size ON SCREEN rather than a constant size on the ground. A fixed
 * 120 m wedge is invisible at the zoom that shows all of Gujarat - which is
 * the zoom the map opens at - so the coverage layer was only visible to
 * someone who already knew to go looking for it. Scaling with zoom keeps it
 * legible from the view you land on, and it stops growing once it would
 * overstate what a junction camera can see.
 */

/** Screen pixels the wedge should occupy, at any zoom. */
const WEDGE_PX = 34;
/** Degrees. Typical fixed-lens horizontal field of view. */
const FOV_DEG = 70;
/** Rings read a touch smaller than wedges at the same pixel length. */
const RING_PX = 26;
/**
 * Metres the drawn range is clamped to.
 *
 * The lower bound stops the wedge shrinking below a clickable size when zoomed
 * right in; the upper bound stops a state-wide view drawing a cone that
 * implies a camera covers three districts.
 */
const MIN_RANGE_M = 70;
const MAX_RANGE_M = 2500;

/**
 * Metres per screen pixel at this latitude and zoom.
 *
 * Web Mercator: one tile is 256 px and covers 360/2^zoom degrees of longitude,
 * narrowing by cos(latitude) as you leave the equator.
 */
function metresPerPixel(lat: number, zoom: number): number {
  return (156543.03392 * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom);
}

/** Re-render this overlay whenever the map's zoom changes. */
function useZoom(): number {
  const map = useMap();
  const [zoom, setZoom] = useState(() => map.getZoom());
  useEffect(() => {
    const update = () => setZoom(map.getZoom());
    map.on("zoomend", update);
    update();
    return () => {
      map.off("zoomend", update);
    };
  }, [map]);
  return zoom;
}

const BEARINGS: Record<string, number> = {
  north: 0,
  northeast: 45,
  east: 90,
  southeast: 135,
  south: 180,
  southwest: 225,
  west: 270,
  northwest: 315,
};

/**
 * Degrees clockwise from north, or null when nothing was recorded.
 *
 * Tolerant of spelling because this value has three origins: a surveyed CSV
 * ("North-East"), the API's canonical form ("northeastbound"), and older rows
 * predating either. Stripping the "bound" suffix and any separators collapses
 * all three onto one key - and a heading that arrives as `north-east` while
 * the table holds `northeast` silently draws no wedge, which looks identical
 * to a camera nobody ever surveyed.
 */
export function bearingOf(direction: string | null | undefined): number | null {
  if (!direction) return null;
  const key = direction
    .trim()
    .toLowerCase()
    .replace(/[\s_-]/g, "")
    .replace(/bound$/, "");
  return BEARINGS[key] ?? null;
}

/** True when this camera points nowhere in particular. */
export function isOmnidirectional(camera: Camera): boolean {
  const type = String(camera.camera_type ?? "").toLowerCase();
  return type === "ptz" || type === "dome" || type === "360";
}

/**
 * Offset a lat/lng by a distance and bearing.
 *
 * Flat-earth approximation, deliberately. Over 120 m the error is far below a
 * pixel at any zoom this map uses, and the alternative pulls in a geodesy
 * dependency to draw a shape whose range is a convention anyway.
 */
function offset(lat: number, lng: number, metres: number, bearingDeg: number): [number, number] {
  const rad = (bearingDeg * Math.PI) / 180;
  const dLat = (metres * Math.cos(rad)) / 111_320;
  const dLng = (metres * Math.sin(rad)) / (111_320 * Math.cos((lat * Math.PI) / 180));
  return [lat + dLat, lng + dLng];
}

function wedge(
  lat: number,
  lng: number,
  bearing: number,
  rangeM: number,
): [number, number][] {
  const points: [number, number][] = [[lat, lng]];
  const half = FOV_DEG / 2;
  // Enough segments that the arc reads as curved rather than faceted.
  for (let a = -half; a <= half; a += FOV_DEG / 12) {
    points.push(offset(lat, lng, rangeM, bearing + a));
  }
  return points;
}

export function CameraFieldOfView({
  camera,
  lat,
  lng,
  colour,
  dimmed = false,
}: {
  camera: Camera;
  lat: number;
  lng: number;
  colour: string;
  /** Withdrawn cameras still occupy the map; they just do not claim coverage. */
  dimmed?: boolean;
}) {
  const label = camera.name ?? camera.camera_id;
  const zoom = useZoom();
  const perPixel = metresPerPixel(lat, zoom);
  const clamp = (px: number) =>
    Math.min(MAX_RANGE_M, Math.max(MIN_RANGE_M, px * perPixel));
  const range = clamp(WEDGE_PX);

  if (isOmnidirectional(camera)) {
    return (
      <Circle
        center={[lat, lng]}
        radius={clamp(RING_PX)}
        pathOptions={{
          color: colour,
          weight: 1,
          opacity: dimmed ? 0.25 : 0.55,
          fillColor: colour,
          fillOpacity: dimmed ? 0.04 : 0.1,
          dashArray: "4 4",
        }}
      >
        <Tooltip direction="top" offset={[0, -6]} opacity={0.95}>
          <span className="text-2xs">
            {label} · {camera.camera_type} · steerable, no fixed bearing
          </span>
        </Tooltip>
      </Circle>
    );
  }

  const bearing = bearingOf(camera.location?.view_direction);
  if (bearing === null) return null;

  return (
    <Polygon
      positions={wedge(lat, lng, bearing, range)}
      pathOptions={{
        color: colour,
        weight: 1,
        opacity: dimmed ? 0.2 : 0.5,
        fillColor: colour,
        fillOpacity: dimmed ? 0.05 : 0.18,
      }}
    >
      <Tooltip direction="top" offset={[0, -6]} opacity={0.95}>
        <span className="text-2xs">
          {label} · facing {camera.location?.view_direction} · indicative extent
        </span>
      </Tooltip>
    </Polygon>
  );
}
