"use client";

import { TileLayer } from "react-leaflet";

/**
 * Basemap styles, and why these ones.
 *
 * Each answers a different question. The street map is for "which junction is
 * this" and is the default because that is what an operator asks most.
 * Satellite is for "what is actually at that corner" - useful when placing a
 * camera or arguing about a coverage gap, where a road name is not enough.
 * Coverage exists because a coverage map is mostly about the overlay: thirty
 * wedges over full-colour cartography is unreadable, and dropping the
 * basemap's contrast puts the coverage back in front. Terrain and Dark are
 * there because a wall display and a daytime desk want different things, and
 * the wedges read very differently over each.
 *
 * All five are keyless.
 *
 * That is a requirement, not a preference. This first shipped using CARTO's
 * styled basemaps, which read closer to the road maps people are used to, and
 * they returned an API-key error in the field even though they answer keyless
 * from here - a provider whose terms or rate limits vary by network is not
 * something an operations console should depend on to draw a map. The muted
 * style is therefore a CSS filter over the SAME OpenStreetMap tiles rather
 * than a second provider: no extra request, nothing new to be refused, and
 * one fewer thing that can fail on a police network.
 *
 * Esri's imagery and dark canvas stay because there is no open-tile
 * equivalent for either. If one is ever refused, the map keeps working on the
 * OSM-backed styles - and everything drawn on top, markers, wedges, popups, is
 * ours and renders regardless of whether tiles arrive at all.
 */

export interface Basemap {
  id: string;
  label: string;
  /** One word on what it is for, shown under the picker. */
  purpose: string;
  url: string;
  attribution: string;
  maxZoom: number;
  /** Highest zoom the service actually serves; Leaflet upscales beyond it. */
  maxNativeZoom?: number;
  /** CSS filter applied to the tile layer, for styling without a new provider. */
  filter?: string;
  /**
   * Purely descriptive: this style's tiles are dark.
   *
   * Nothing recolours itself from it. It is kept so a future overlay that
   * genuinely cannot be read on both - a white halo behind a label, say - has
   * something to test, and so the flag is not silently re-derived from the id.
   */
  dark?: boolean;
  /**
   * Labels drawn ON TOP of the base tiles.
   *
   * Esri splits its cartography: `World_Imagery` and `Dark_Gray_Base` carry no
   * place names at all, and are meant to be paired with a reference layer.
   * Shipped without one, satellite and dark showed a city with nothing named
   * in it - you could see a junction and not know which junction.
   */
  labels?: string;
  labelsAttribution?: string;
}

const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export const BASEMAPS: Basemap[] = [
  {
    id: "streets",
    label: "Map",
    purpose: "roads, parks, water, place names",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: OSM_ATTRIBUTION,
    maxZoom: 19,
  },
  {
    id: "satellite",
    label: "Satellite",
    purpose: "real greenery and water, with names on top",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics",
    maxZoom: 19,
    labels:
      "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  },
  {
    id: "terrain",
    label: "Terrain",
    purpose: "relief, forest and rivers",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution:
      'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    maxZoom: 17,
  },
  {
    id: "dark",
    label: "Dark",
    purpose: "control-room lighting, wedges glow",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    attribution: "&copy; Esri",
    // Leaflet stops drawing a layer past its maxZoom while the map keeps
    // zooming, which leaves the basemap blank under the markers. The service
    // serves z18; maxNativeZoom below upscales the rest.
    maxZoom: 19,
    maxNativeZoom: 18,
    dark: true,
    labels:
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
  },
  {
    id: "muted",
    label: "Coverage",
    purpose: "calmed down, wedges in front",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: OSM_ATTRIBUTION,
    maxZoom: 19,
    // Desaturated rather than greyscaled. Full greyscale made a coverage map
    // that had lost its geography: parks, rivers and the Sabarmati all became
    // the same shade, and "is that camera pointed at water" stopped being
    // answerable. Keeping a third of the saturation leaves green as green and
    // blue as blue while still letting the wedges sit in front.
    filter: "saturate(0.35) brightness(1.06) contrast(0.92)",
  },
];

export function BasemapLayer({ basemap }: { basemap: Basemap }) {
  return (
    <>
      <TileLayer
        // Keyed so React swaps the layer instead of mutating one in place;
        // Leaflet caches tiles per layer and reusing it leaves the old style
        // showing until every tile happens to be re-requested.
        key={basemap.id}
        url={basemap.url}
        attribution={basemap.attribution}
        maxZoom={basemap.maxZoom}
        maxNativeZoom={basemap.maxNativeZoom}
        // Web Mercator repeats east-west forever. Without this the map draws
        // a fresh copy of India every 360 degrees, and panning finds cameras
        // plotted over an ocean that is really the Bay of Bengal three worlds
        // along.
        noWrap
        className={basemap.filter ? "vigentra-muted-tiles" : undefined}
      />
      {basemap.labels && (
        <TileLayer
          key={`${basemap.id}-labels`}
          url={basemap.labels}
          attribution={basemap.labelsAttribution ?? ""}
          maxZoom={basemap.maxZoom}
          maxNativeZoom={basemap.maxNativeZoom}
          noWrap
          // Above the imagery, below every marker and wedge Leaflet puts in
          // its own overlay pane.
          zIndex={2}
        />
      )}
    </>
  );
}

export function BasemapPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const active = BASEMAPS.find((b) => b.id === value) ?? BASEMAPS[0];
  // One chrome, whatever the basemap. The console is a light interface and
  // the picker is part of it, not part of the map: changing the basemap
  // changes the CARTOGRAPHY. Controls, department colours, health rings and
  // coverage wedges keep their meaning across every style, because a legend
  // that reads differently depending on which tiles loaded is not a legend.
  return (
    <div className="absolute right-3 top-3 z-[1000] rounded border border-line bg-white/95 p-1 shadow-sm backdrop-blur">
      <div className="flex gap-0.5">
        {BASEMAPS.map((basemap) => (
          <button
            key={basemap.id}
            type="button"
            onClick={() => onChange(basemap.id)}
            className={
              "rounded px-2 py-1 text-2xs font-medium transition " +
              (basemap.id === value
                ? "bg-navy-800 text-white"
                : "text-ink-600 hover:bg-brand-50")
            }
          >
            {basemap.label}
          </button>
        ))}
      </div>
      <p className="px-2 pb-0.5 pt-1 text-[10px] leading-tight text-ink-400">
        {active.purpose}
      </p>
    </div>
  );
}
