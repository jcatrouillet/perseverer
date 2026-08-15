// Decodes the Google encoded-polyline algorithm format used by route_geom.encoded_polyline /
// simplified_polyline (encoded server-side by the Python `polyline` package, default precision
// 5 -- confirmed by reading src/sporthealth/adapters/fit_folder.py's own `polyline_codec.encode`
// call rather than assumed). Hand-rolled rather than a new dependency: this is ~25 lines of a
// well-known, stable algorithm, matching this project's existing bar for "small and stable
// enough to not need a package" (e.g. Icon.tsx's hand-rolled sprite).
export type LatLng = [number, number];

export function decodePolyline(encoded: string, precision = 5): LatLng[] {
  const factor = 10 ** precision;
  const points: LatLng[] = [];
  let index = 0;
  let lat = 0;
  let lng = 0;

  while (index < encoded.length) {
    lat += decodeSignedValue(encoded, index);
    index = advanceIndex(encoded, index);
    lng += decodeSignedValue(encoded, index);
    index = advanceIndex(encoded, index);
    points.push([lat / factor, lng / factor]);
  }

  return points;
}

function advanceIndex(encoded: string, index: number): number {
  let i = index;
  let byte: number;
  do {
    byte = encoded.charCodeAt(i++) - 63;
  } while (byte >= 0x20);
  return i;
}

function decodeSignedValue(encoded: string, index: number): number {
  let i = index;
  let shift = 0;
  let result = 0;
  let byte: number;
  do {
    byte = encoded.charCodeAt(i++) - 63;
    result |= (byte & 0x1f) << shift;
    shift += 5;
  } while (byte >= 0x20);
  return result & 1 ? ~(result >> 1) : result >> 1;
}
