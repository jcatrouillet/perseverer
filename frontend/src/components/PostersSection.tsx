// Year view: generate printable posters of the year (grid of GPS traces, heatmap around a point,
// calendar, circular view). The picture is drawn on the server in the background
// (POST /posters), so a request returns at once and the list below polls until it is done.
import { Suspense, lazy, useEffect, useMemo, useState } from "react";

import { apiBlob } from "../api/client";
import {
  useCreatePoster,
  useDeletePoster,
  useHeatmapDefaults,
  usePosterSports,
  usePosters,
} from "../api/queries";
import type { PosterKind, PosterOut } from "../api/types";
import { useDistanceFormat } from "../formatDistance";
import { LoadingSpinner } from "./LoadingSpinner";
import { Modal } from "./Modal";
import "../styles/posters.css";

const PosterMapPicker = lazy(() => import("./PosterMapPicker"));

export const POSTER_KINDS: { kind: PosterKind; label: string; blurb: string }[] = [
  { kind: "grid", label: "Grid", blurb: "Every GPS trace of the year, one small tile each." },
  {
    kind: "heatmap",
    label: "Heatmap",
    blurb: "All your traces over one spot, glowing where you go most.",
  },
  { kind: "calendar", label: "Calendar", blurb: "Each day of the year, coloured by sport." },
  {
    kind: "circular",
    label: "Circular",
    blurb: "One bar per activity around the year, as long as its distance.",
  },
];

export function sportLabel(sport: string): string {
  const s = sport.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Where the heatmap's centre came from, in words. */
export function sourceLabel(source: string | null): string {
  if (source === "home_address") return "your home address";
  if (source === "profile_home") return "your home";
  if (source === "activities") return "where your activities start";
  return "the map";
}

function posterTitle(p: PosterOut): string {
  const sports = p.sports.length === 0 ? "All sports" : p.sports.map(sportLabel).join(", ");
  const kind = POSTER_KINDS.find((k) => k.kind === p.kind)?.label ?? p.kind;
  return `${kind} · ${sports}`;
}

function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function PosterPreview({ poster, onClose }: { poster: PosterOut; onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    apiBlob(`/api/v1/posters/${poster.id}/file`)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [poster.id]);
  return (
    <Modal open onClose={onClose} title={posterTitle(poster)} panelClassName="poster-preview">
      {failed && <p role="alert">Could not load the poster.</p>}
      {!url && !failed && <LoadingSpinner />}
      {url && <img className="poster-preview__image" src={url} alt={posterTitle(poster)} />}
    </Modal>
  );
}

function PosterRow({ poster }: { poster: PosterOut }) {
  const del = useDeletePoster();
  const [preview, setPreview] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const download = async () => {
    setDownloading(true);
    setError(null);
    try {
      save(
        await apiBlob(`/api/v1/posters/${poster.id}/file`),
        poster.filename ?? `${poster.id}.png`,
      );
    } catch {
      setError("Could not download the poster.");
    } finally {
      setDownloading(false);
    }
  };

  const active = poster.status === "queued" || poster.status === "running";
  return (
    <li className="poster-row">
      <div className="poster-row__info">
        <strong>{posterTitle(poster)}</strong>
        <span className="poster-row__meta">
          {active && (poster.status === "queued" ? "Waiting to start…" : "Drawing…")}
          {poster.status === "done" &&
            `${poster.activity_count ?? 0} activities · ${((poster.size_bytes ?? 0) / 1e6).toFixed(1)} MB`}
          {poster.status === "failed" && (
            <span role="alert">{poster.error ?? "The poster could not be drawn."}</span>
          )}
          {error && <span role="alert"> {error}</span>}
        </span>
      </div>
      <div className="poster-row__actions">
        {poster.status === "done" && (
          <>
            <button type="button" className="button" onClick={() => setPreview(true)}>
              Preview
            </button>
            <button
              type="button"
              className="button button--primary"
              onClick={() => void download()}
              disabled={downloading}
            >
              {downloading ? "Downloading…" : "Download"}
            </button>
          </>
        )}
        <button
          type="button"
          className="button"
          onClick={() => del.mutate(poster.id)}
          disabled={del.isPending || active}
          aria-label={`Delete ${posterTitle(poster)}`}
        >
          Delete
        </button>
      </div>
      {preview && <PosterPreview poster={poster} onClose={() => setPreview(false)} />}
    </li>
  );
}

export function PostersSection({ year }: { year: number }) {
  const { unitLabel, metersToDisplay } = useDistanceFormat();
  const [kind, setKind] = useState<PosterKind>("grid");
  const [sports, setSports] = useState<string[]>([]);
  const [includeVirtual, setIncludeVirtual] = useState(false);
  const [center, setCenter] = useState<{ lat: number; lon: number } | null>(null);
  const [radiusKm, setRadiusKm] = useState<number | null>(null);
  const [picked, setPicked] = useState(false);

  const available = usePosterSports(year, includeVirtual);
  const posters = usePosters(year);
  const create = useCreatePoster();
  const routeKind = kind === "grid" || kind === "heatmap";
  const multi = kind === "calendar" || kind === "circular";

  // The sports the year has (and, for the route posters, that have a trace).
  const choices = useMemo(
    () => (available.data ?? []).filter((s) => (routeKind ? s.with_route > 0 : s.activities > 0)),
    [available.data, routeKind],
  );
  // A selection that no longer exists (another year, virtual off) drops out.
  const selected = useMemo(
    () => sports.filter((s) => choices.some((c) => c.sport === s)),
    [sports, choices],
  );
  const selectedKey = selected.join(",");

  const defaults = useHeatmapDefaults(year, selected, includeVirtual);
  const d = defaults.data;
  // The defaults follow the selection until the athlete picks a point or a range themselves.
  useEffect(() => {
    setPicked(false);
  }, [year, selectedKey, includeVirtual]);
  const effCenter =
    picked && center
      ? center
      : d?.available && d.center_lat != null && d.center_lon != null
        ? { lat: d.center_lat, lon: d.center_lon }
        : null;
  const effRadius = picked && radiusKm != null ? radiusKm : (d?.radius_km ?? 1);

  const toggle = (sport: string) => {
    const next = selected.includes(sport)
      ? selected.filter((s) => s !== sport)
      : [...selected, sport];
    // Grid and heatmap take one sport or all; the other two a list.
    setSports(multi ? next : next.slice(-1));
  };

  const generate = () => {
    create.mutate({
      kind,
      year,
      sports: selected,
      include_virtual: includeVirtual,
      ...(kind === "heatmap" && effCenter
        ? { center_lat: effCenter.lat, center_lon: effCenter.lon, radius_km: effRadius }
        : {}),
    });
  };

  const radiusDisplay = metersToDisplay(effRadius * 1000);
  const blocked = kind === "heatmap" && !effCenter;

  return (
    <section className="card posters">
      <h2>Posters</h2>
      <p className="chart-note">
        Turn {year} into a picture to print or share. It is drawn on the server, so you can leave
        the page and come back to it.
      </p>

      <div className="posters__kinds" role="radiogroup" aria-label="Poster type">
        {POSTER_KINDS.map((k) => (
          <button
            key={k.kind}
            type="button"
            role="radio"
            aria-checked={kind === k.kind}
            className={`posters__kind${kind === k.kind ? " posters__kind--on" : ""}`}
            onClick={() => {
              setKind(k.kind);
              if (k.kind === "grid" || k.kind === "heatmap") setSports((s) => s.slice(-1));
            }}
          >
            <strong>{k.label}</strong>
            <span>{k.blurb}</span>
          </button>
        ))}
      </div>

      <fieldset className="posters__sports">
        <legend>{multi ? "Sports (pick any, none means all)" : "Sport (none means all)"}</legend>
        {available.isLoading && <LoadingSpinner />}
        {!available.isLoading && choices.length === 0 && (
          <p className="chart-note">
            {routeKind ? "No activity with a GPS trace this year." : "No activity this year."}
          </p>
        )}
        {choices.map((s) => (
          <label key={s.sport} className="posters__sport">
            <input
              type="checkbox"
              checked={selected.includes(s.sport)}
              onChange={() => toggle(s.sport)}
            />
            {sportLabel(s.sport)} ({routeKind ? s.with_route : s.activities})
          </label>
        ))}
        <label className="posters__sport">
          <input
            type="checkbox"
            checked={includeVirtual}
            onChange={(e) => setIncludeVirtual(e.target.checked)}
          />
          Include virtual activities
        </label>
      </fieldset>

      {kind === "heatmap" && (
        <div className="posters__heatmap">
          {d && !d.available && (
            <p className="chart-note">No activity with a start point to centre the heatmap on.</p>
          )}
          {effCenter && (
            <>
              <p className="chart-note">
                {picked
                  ? "Centred on the point you chose. "
                  : `Centred on ${sourceLabel(d?.source ?? null)}, reaching ${radiusDisplay.toFixed(1)} ${unitLabel}: at least half of the ${d?.activities ?? 0} activities start inside. `}
                Click the map to move the centre.
              </p>
              <Suspense fallback={<LoadingSpinner />}>
                <PosterMapPicker
                  center={effCenter}
                  radiusKm={effRadius}
                  onPick={(p) => {
                    setCenter(p);
                    setRadiusKm(effRadius);
                    setPicked(true);
                  }}
                />
              </Suspense>
              <label className="posters__radius">
                Range ({unitLabel})
                <input
                  type="number"
                  min={0.1}
                  step={0.5}
                  value={Number(radiusDisplay.toFixed(1))}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (!(v > 0) || radiusDisplay <= 0) return;
                    // Back to kilometres from the display unit.
                    setCenter(effCenter);
                    setRadiusKm((v / radiusDisplay) * effRadius);
                    setPicked(true);
                  }}
                />
              </label>
            </>
          )}
        </div>
      )}

      <div className="posters__actions">
        <button
          type="button"
          className="button button--primary"
          onClick={generate}
          disabled={create.isPending || blocked || choices.length === 0}
        >
          {create.isPending ? "Starting…" : "Generate poster"}
        </button>
        {create.isError && <span role="alert">Could not start the poster.</span>}
      </div>

      {posters.data && posters.data.length > 0 && (
        <ul className="posters__list">
          {posters.data.map((p) => (
            <PosterRow key={p.id} poster={p} />
          ))}
        </ul>
      )}
    </section>
  );
}
