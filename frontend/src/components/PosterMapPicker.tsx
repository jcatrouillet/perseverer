// The heatmap poster's map: click to choose the centre, the circle shows how far the poster
// reaches. Lazy-loaded by PostersSection (Leaflet is heavy and only this card needs it) and on
// CARTO's raster tiles, like the other small maps, since a card needs no WebGL basemap.
import "leaflet/dist/leaflet.css";
import type { LatLngBoundsExpression } from "leaflet";
import { useEffect } from "react";
import { Circle, CircleMarker, MapContainer, TileLayer, useMap, useMapEvents } from "react-leaflet";

import { cartoRasterTileUrlTemplate } from "../mapBasemap";

export interface PickedPoint {
  lat: number;
  lon: number;
}

function ClickToPick({ onPick }: { onPick: (p: PickedPoint) => void }) {
  useMapEvents({ click: (e) => onPick({ lat: e.latlng.lat, lon: e.latlng.lng }) });
  return null;
}

/** Keeps the whole circle in view when the centre or the radius changes. */
function FitCircle({ center, radiusKm }: { center: PickedPoint; radiusKm: number }) {
  const map = useMap();
  useEffect(() => {
    const dLat = radiusKm / 111.32;
    const dLon = radiusKm / (111.32 * Math.max(Math.cos((center.lat * Math.PI) / 180), 0.01));
    const bounds: LatLngBoundsExpression = [
      [center.lat - dLat, center.lon - dLon],
      [center.lat + dLat, center.lon + dLon],
    ];
    map.fitBounds(bounds, { padding: [16, 16], animate: false });
  }, [map, center.lat, center.lon, radiusKm]);
  return null;
}

export default function PosterMapPicker({
  center,
  radiusKm,
  onPick,
}: {
  center: PickedPoint;
  radiusKm: number;
  onPick: (p: PickedPoint) => void;
}) {
  return (
    <div className="poster-map" data-testid="poster-map">
      <MapContainer center={[center.lat, center.lon]} zoom={12} scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
          url={cartoRasterTileUrlTemplate()}
        />
        <Circle
          center={[center.lat, center.lon]}
          radius={radiusKm * 1000}
          pathOptions={{ color: "#ff7a1a", weight: 2, fillOpacity: 0.08 }}
        />
        <CircleMarker
          center={[center.lat, center.lon]}
          radius={6}
          pathOptions={{ color: "#ff7a1a", fillColor: "#ff7a1a", fillOpacity: 1 }}
        />
        <FitCircle center={center} radiusKm={radiusKm} />
        <ClickToPick onPick={onPick} />
      </MapContainer>
    </div>
  );
}
