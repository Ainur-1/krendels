/**
 * The network at the selected moment, drawn on a world map.
 *
 * Two decisions worth stating.
 *
 * Positions come from the server, not from a second copy of the orbital model in
 * JavaScript. A snapshot is about a millisecond to compute and 20 kB to send, and
 * the alternative — reimplementing the geometry here — is how a picture starts
 * disagreeing with the numbers under it. Frames are cached by step and the fetch is
 * debounced, so dragging the slider stays smooth without a request per pixel.
 *
 * The projection is plain equirectangular. A globe looks better in a screenshot and
 * is worse to work with: half the constellation is behind it, and the clients here
 * are at 65–72° north, where a rectangular map keeps every one of them and their
 * gateway visible at once.
 */

import { geoEquirectangular, geoGraticule10, geoPath } from "d3-geo";
import { useEffect, useMemo, useRef, useState } from "react";
import { feature } from "topojson-client";
import landTopology from "world-atlas/land-110m.json";

import { api } from "../api";
import { clock, planeColour } from "../lib/format";
import type { Run, Snapshot } from "../types";

// Resolved once: topojson → GeoJSON is pure work that does not depend on anything
// changing, and it is the most expensive thing on this path.
const LAND = feature(
  landTopology as never,
  (landTopology as never as { objects: { land: never } }).objects.land,
) as never;

const SCRUB_DEBOUNCE_MS = 60;

export function MapView({
  run,
  step,
  client,
}: {
  run: Run | null;
  step: number;
  client: string | null;
}) {
  const wrapper = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const cache = useRef(new Map<number, Snapshot>());
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [size, setSize] = useState({ width: 960, height: 420 });

  const runId = run?.run_id ?? null;
  const tSeconds = run ? run.times_s[step] : 0;

  // A new run invalidates every cached frame: same step, different design.
  useEffect(() => {
    cache.current.clear();
    setSnapshot(null);
  }, [runId]);

  useEffect(() => {
    if (!runId) return;

    const cached = cache.current.get(step);
    if (cached) {
      setSnapshot(cached);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      api
        .snapshot(runId, tSeconds)
        .then((fetched) => {
          if (cancelled) return;
          cache.current.set(step, fetched);
          setSnapshot(fetched);
        })
        .catch(() => undefined);
    }, SCRUB_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [runId, step, tSeconds]);

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(320, Math.floor(entry.contentRect.width));
      setSize({ width, height: Math.round(width / 2.35) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const planeIds = useMemo(
    () => run?.scenario.design.planes.map((plane) => plane.id) ?? [],
    [run],
  );

  const routePath = useMemo(() => {
    if (!run || !client) return [] as string[];
    return run.clients[client]?.path[step] ?? [];
  }, [run, client, step]);

  useEffect(() => {
    const context = canvas.current?.getContext("2d");
    if (!context) return;

    const ratio = window.devicePixelRatio || 1;
    const { width, height } = size;
    canvas.current!.width = width * ratio;
    canvas.current!.height = height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);

    const projection = geoEquirectangular().fitSize([width, height], { type: "Sphere" });
    const path = geoPath(projection, context);
    const place = (lon: number, lat: number) => projection([lon, lat]);

    context.clearRect(0, 0, width, height);
    context.fillStyle = "#0b1017";
    context.fillRect(0, 0, width, height);

    context.beginPath();
    path(geoGraticule10());
    context.strokeStyle = "#18202b";
    context.lineWidth = 1;
    context.stroke();

    context.beginPath();
    path(LAND);
    context.fillStyle = "#18222e";
    context.fill();
    context.strokeStyle = "#28323f";
    context.lineWidth = 0.6;
    context.stroke();

    if (!snapshot) {
      context.fillStyle = "#6b7886";
      context.font = "13px system-ui";
      context.fillText(run ? "Загрузка кадра…" : "Запустите расчёт", 14, 22);
      return;
    }

    const byId = new Map(snapshot.satellites.map((satellite) => [satellite.id, satellite]));
    const onRoute = new Set(routePath);

    // Inter-satellite links first and dimmest: they are context for the route, and
    // there are up to a few hundred of them. Segments that would wrap across the
    // date line are dropped rather than drawn as a line across the whole map.
    context.strokeStyle = "rgba(76,154,255,0.32)";
    context.lineWidth = 1;
    context.beginPath();
    for (const link of snapshot.links) {
      const a = byId.get(link.a);
      const b = byId.get(link.b);
      if (!a || !b || Math.abs(a.lon_deg - b.lon_deg) > 170) continue;
      const from = place(a.lon_deg, a.lat_deg);
      const to = place(b.lon_deg, b.lat_deg);
      if (!from || !to) continue;
      context.moveTo(from[0], from[1]);
      context.lineTo(to[0], to[1]);
    }
    context.stroke();

    const sites = new Map(snapshot.ground_sites.map((site) => [site.id, site]));

    // Ground links for the selected terminal: what it could use, against what it does.
    if (client) {
      context.strokeStyle = "rgba(154,167,180,0.28)";
      context.setLineDash([3, 3]);
      context.beginPath();
      for (const link of snapshot.ground_links) {
        if (link.site !== client) continue;
        const site = sites.get(link.site);
        const satellite = byId.get(link.satellite);
        if (!site || !satellite) continue;
        const from = place(site.lon_deg, site.lat_deg);
        const to = place(satellite.lon_deg, satellite.lat_deg);
        if (!from || !to) continue;
        context.moveTo(from[0], from[1]);
        context.lineTo(to[0], to[1]);
      }
      context.stroke();
      context.setLineDash([]);
    }

    // The route, drawn last and brightest — it is the answer the user asked for.
    if (routePath.length > 1) {
      context.strokeStyle = "#3fb950";
      context.lineWidth = 2.5;
      context.beginPath();
      let started = false;
      for (const id of routePath) {
        const node = byId.get(id);
        const site = sites.get(id);
        const point = node
          ? place(node.lon_deg, node.lat_deg)
          : site
            ? place(site.lon_deg, site.lat_deg)
            : null;
        if (!point) continue;
        if (started) context.lineTo(point[0], point[1]);
        else context.moveTo(point[0], point[1]);
        started = true;
      }
      context.stroke();
      context.lineWidth = 1;
    }

    for (const satellite of snapshot.satellites) {
      const point = place(satellite.lon_deg, satellite.lat_deg);
      if (!point) continue;
      const [x, y] = point;
      const highlighted = onRoute.has(satellite.id);

      if (!satellite.active) {
        // Out of service craft keep their computed position, as the case requires,
        // so they are shown where they are and marked as unusable rather than hidden.
        context.strokeStyle = "#4a5462";
        context.beginPath();
        context.moveTo(x - 3, y - 3);
        context.lineTo(x + 3, y + 3);
        context.moveTo(x + 3, y - 3);
        context.lineTo(x - 3, y + 3);
        context.stroke();
        continue;
      }

      context.fillStyle = planeColour(planeIds, satellite.plane_id);
      context.beginPath();
      context.arc(x, y, highlighted ? 5 : 2.6, 0, Math.PI * 2);
      context.fill();

      if (highlighted) {
        context.strokeStyle = "#3fb950";
        context.lineWidth = 2;
        context.stroke();
        context.lineWidth = 1;
        context.fillStyle = "#e6edf3";
        context.font = "600 11px var(--mono, monospace)";
        context.fillText(satellite.id, x + 8, y - 6);
      }
    }

    for (const site of snapshot.ground_sites) {
      const point = place(site.lon_deg, site.lat_deg);
      if (!point) continue;
      const [x, y] = point;
      const isGateway = site.role === "gateway";
      const selected = site.id === client;

      context.beginPath();
      if (isGateway) {
        context.moveTo(x, y - 6);
        context.lineTo(x + 6, y);
        context.lineTo(x, y + 6);
        context.lineTo(x - 6, y);
        context.closePath();
      } else {
        context.rect(x - 4, y - 4, 8, 8);
      }
      context.fillStyle = isGateway ? "#d29922" : selected ? "#3fb950" : "#9aa7b4";
      context.fill();
      context.strokeStyle = "#0b1017";
      context.lineWidth = 1.5;
      context.stroke();
      context.lineWidth = 1;

      context.fillStyle = selected || isGateway ? "#e6edf3" : "#9aa7b4";
      context.font = `${selected ? "600 " : ""}11px system-ui`;
      context.fillText(site.id, x + 9, y + 4);
    }

    context.fillStyle = "#9aa7b4";
    context.font = "12px var(--mono, monospace)";
    context.fillText(clock(snapshot.t_s), 12, height - 12);
  }, [snapshot, size, routePath, planeIds, client, run]);

  return (
    <section className="panel" ref={wrapper} style={{ padding: 0, overflow: "hidden" }}>
      <canvas
        ref={canvas}
        style={{ display: "block", width: "100%", height: size.height }}
      />
      <div className="legend" style={{ padding: "8px 12px" }}>
        {planeIds.map((id) => (
          <span key={id}>
            <i className="swatch" style={{ background: planeColour(planeIds, id) }} />
            {id}
          </span>
        ))}
        <span>
          <i className="swatch" style={{ background: "#d29922" }} />
          шлюз
        </span>
        <span>
          <i className="swatch" style={{ background: "#3fb950" }} />
          маршрут
        </span>
        <span>
          <i className="swatch" style={{ background: "#4a5462" }} />
          вне строя
        </span>
        {snapshot && (
          <span style={{ marginLeft: "auto" }}>
            связей: {snapshot.links.length} · в строю:{" "}
            {snapshot.satellites.filter((s) => s.active).length}
          </span>
        )}
      </div>
    </section>
  );
}
