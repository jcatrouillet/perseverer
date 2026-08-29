// Adds CARTO's vector basemap (a MapLibre GL style, rendered onto its own WebGL canvas kept in
// sync with Leaflet's pan/zoom/resize) as a layer inside an existing react-leaflet map, via
// @maplibre/maplibre-gl-leaflet -- lets every map surface in this app (ActivityMap thumbnail,
// ActivityRouteMap detail view, MapExplorerPage) keep its own Polyline/Marker/CircleMarker/Popup
// components completely unchanged, swapping out only the basemap layer itself. Replaces CARTO's
// pre-rendered raster PNG tiles (docs/adr/0011-phase-7-map-recaps-pwa.md decision 1's original
// choice for MapExplorerPage; ActivityMap/ActivityRouteMap independently landed on the same
// raster CARTO tileset later) now that a real CARTO API key is configured -- crisper at high
// zoom, one shared vector tileset instead of per-zoom-level pre-rendered PNGs, and no more
// anonymous-tier rate-limiting risk. A deliberate revision of that ADR decision, not a new phase.
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
// requirements across three call sites.
import "maplibre-gl/dist/maplibre-gl.css";
import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
import { useEffect } from "react";
import { useMap } from "react-leaflet";

import { cartoStyleUrl, type CartoBasemapStyle } from "../mapBasemap";

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
