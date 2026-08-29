// Adds CARTO's vector basemap (a MapLibre GL style, rendered onto its own WebGL canvas kept in
// sync with Leaflet's pan/zoom/resize) as a layer inside an existing react-leaflet map, via
// @maplibre/maplibre-gl-leaflet -- lets ActivityRouteMap.tsx's detail view and MapExplorerPage
// keep their own Polyline/Marker/CircleMarker/Popup components completely unchanged, swapping out
// only the basemap layer itself. Replaces CARTO's pre-rendered raster PNG tiles
// (docs/adr/0011-phase-7-map-recaps-pwa.md decision 1's original choice for MapExplorerPage;
// ActivityRouteMap independently landed on the same raster CARTO tileset later) now that a real
// CARTO API key is configured -- crisper at high zoom, one shared vector tileset instead of
// per-zoom-level pre-rendered PNGs, and no more anonymous-tier rate-limiting risk. A deliberate
// revision of that ADR decision, not a new phase.
//
// Deliberately NOT used by ActivityMap.tsx's route thumbnail, even though it renders the exact
// same Positron style: a WebGL context is a scarce, page-wide resource (Chrome caps live
// contexts at ~16), and ActivityRouteMap/MapExplorerPage are each the only map on their page,
// while ActivityMap renders once per activity in a list -- 31 of them on a real activity list
// page. Confirmed live that mounting this many WebGL canvases at once makes Chrome silently evict
// the oldest contexts to free budget for new ones, and an evicted canvas never recovers (no
// error, it just renders nothing forever) -- see ActivityMap.tsx's own docstring and
// mapBasemap.ts's `cartoRasterTileUrlTemplate`, which is what that component uses instead.
//
// Deliberately a small hand-rolled useMap()+useEffect component (matching this same file's
// MapResizeHandler precedent in ActivityRouteMap.tsx) rather than a full declarative
// react-leaflet layer built on @react-leaflet/core's createTileLayerComponent -- nothing in this
// app ever changes a mounted map's basemap style at runtime, so there's no reactive-prop-update
// behaviour worth the extra dependency and indirection.
//
// Attribution is deliberately NOT passed as an explicit option: verified directly against the
// plugin's own source (unpkg.com/@maplibre/maplibre-gl-leaflet) that omitting it makes
// getAttribution() derive the correct string from the CARTO style JSON's own embedded source
// metadata once the style finishes loading, then feed it into Leaflet's own attribution control
// automatically -- one fewer hardcoded string to keep in sync with CARTO's actual attribution
// requirements across both call sites.
//
// setWorkerUrl() is mandatory as of maplibre-gl v6 (ESM-only): the library no longer resolves
// its own background-thread worker script via import.meta.url inside a bundler's module graph
// (confirmed the hard way -- without this, every style/sprite/tiles.json request succeeds but
// the worker script request itself hangs forever and no vector tile ever actually decodes, so
// the map mounts with a blank white background and no error anywhere). `?worker&url` (not plain
// `?url`) is required too -- the worker's own dist file imports a sibling
// maplibre-gl-shared.mjs that a plain `?url` re-export drops in a production build. Per
// MapLibre's own Vite installation docs (maplibre.org/maplibre-gl-js/docs/).
import "maplibre-gl/dist/maplibre-gl.css";
import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
import { setWorkerUrl } from "maplibre-gl";
import { useEffect } from "react";
import { useMap } from "react-leaflet";
// Vite's `?worker&url` suffix (declared by vite/client.d.ts) -- not a real ES module, resolved
// by Vite's own dev server / build pipeline into a same-origin URL string for the worker chunk.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

import { cartoStyleUrl, type CartoBasemapStyle } from "../mapBasemap";

setWorkerUrl(maplibreWorkerUrl);

export function CartoBasemapLayer({ style }: { style: CartoBasemapStyle }) {
  const map = useMap();

  useEffect(() => {
    const layer = maplibreGL({ style: cartoStyleUrl(style) }).addTo(map);
    return () => {
      map.removeLayer(layer);
    };
  }, [map, style]);

  return null;
}
