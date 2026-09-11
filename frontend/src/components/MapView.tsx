/**
 * Состояние сети в выбранный момент, нарисованное на карте мира.
 *
 * Три решения, которые стоит проговорить.
 *
 * Положения приходят с сервера, а не из второй копии орбитальной модели на
 * JavaScript: именно так картинка начинает расходиться с числами под ней. Но
 * приходят они на **весь прогон** одним ответом, а не по отсчёту за раз. Вариант «по
 * отсчёту» был сделан первым и оказался неверным: при проигрывании запросы не
 * успевали и отменяли друг друга, поэтому аппараты стояли на устаревшем кадре, а
 * линия маршрута, которая берётся из памяти, уже уехала вперёд. 247 КБ один раз
 * лучше, чем 20 КБ семьсот двадцать раз.
 *
 * Между двумя отсчётами положения интерполируются. Отсчёт — это 120 с, то есть 908 км
 * пути; проигранное без интерполяции это стробоскоп, а не движение. Интерполяция
 * гринвичских векторов с переводом в широту и долготу при отрисовке избавляет от всех
 * особых случаев, которые понадобились бы при интерполяции самих углов на 180-м
 * меридиане и у полюсов. Ошибка хорды против дуги — 15 км, здесь это около половины
 * пикселя.
 *
 * Проекция простая прямоугольная. Глобус лучше смотрится на скриншоте и хуже подходит
 * для работы: половина группировки оказывается позади него, а клиенты здесь на
 * 65–72° северной широты, где прямоугольная карта держит в поле зрения сразу всех и
 * их шлюз.
 */

import { geoEquirectangular, geoGraticule10, geoPath } from "d3-geo";
import { useEffect, useMemo, useRef, useState } from "react";
import { feature } from "topojson-client";
import landTopology from "world-atlas/land-110m.json";

import { api } from "../api";
import { clock, planeColour } from "../lib/format";
import type { Run, Trajectory } from "../types";

// Считается один раз: перевод topojson → GeoJSON ни от чего не зависит, и это самая
// дорогая операция на этом пути.
const LAND = feature(
  landTopology as never,
  (landTopology as never as { objects: { land: never } }).objects.land,
) as never;

// Дальше этого расстояния часы перетащили рукой, а не проиграли, и плавно следовать
// за ними значило бы анимировать полсуток. Вместо этого прыгаем сразу.
const SNAP_DISTANCE_STEPS = 1.8;

export function MapView({
  run,
  step,
  client,
  stepMs,
  playing,
}: {
  run: Run | null;
  step: number;
  client: string | null;
  stepMs: number;
  playing: boolean;
}) {
  const wrapper = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [trajectory, setTrajectory] = useState<Trajectory | null>(null);
  const [size, setSize] = useState({ width: 960, height: 420 });

  const runId = run?.run_id ?? null;

  useEffect(() => {
    if (!runId) {
      setTrajectory(null);
      return;
    }
    let cancelled = false;
    setTrajectory(null);
    api
      .trajectory(runId)
      .then((fetched) => !cancelled && setTrajectory(fetched))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [runId]);

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

  // Всё, что читает цикл отрисовки, лежит в ref: кадр анимации не должен зависеть от
  // того, случилась ли с прошлого раза перерисовка React.
  const frame = useRef({
    trajectory,
    step,
    routePath,
    client,
    planeIds,
    size,
    stepMs,
    playing,
    times: run?.times_s ?? [],
  });
  frame.current = {
    trajectory,
    step,
    routePath,
    client,
    planeIds,
    size,
    stepMs,
    playing,
    times: run?.times_s ?? [],
  };

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;

    // Показываемое положение — это дробное число, догоняющее целый номер отсчёта.
    // При проигрывании оно отстаёт не больше чем на один отсчёт и заполняет разрыв
    // интерполяцией; при перетаскивании ползунка оно прыгает, потому что плавно
    // следовать за скачком значило бы соврать о том, где была группировка.
    let shown = frame.current.step;
    let previous = performance.now();
    let raf = 0;

    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const state = frame.current;
      const elapsed = now - previous;
      previous = now;

      const target = state.step;
      const gap = target - shown;
      if (Math.abs(gap) > SNAP_DISTANCE_STEPS || !state.playing) {
        shown = target;
      } else if (gap !== 0) {
        // Один отсчёт проходится ровно за столько реального времени, сколько длится
        // отсчёт на шкале, — движение идёт с той же скоростью, что и часы.
        const move = (elapsed / Math.max(state.stepMs, 1)) * Math.sign(gap);
        shown = Math.abs(move) >= Math.abs(gap) ? target : shown + move;
      }

      draw(element, state, shown);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

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
        {trajectory && (
          <span style={{ marginLeft: "auto" }}>
            связей: {trajectory.links[step]?.length ?? 0} · в строю:{" "}
            {trajectory.active[step]?.filter(Boolean).length ?? 0}
          </span>
        )}
      </div>
    </section>
  );
}

interface Frame {
  trajectory: Trajectory | null;
  step: number;
  routePath: string[];
  client: string | null;
  planeIds: string[];
  size: { width: number; height: number };
  stepMs: number;
  playing: boolean;
  times: number[];
}

/** Широта и долгота гринвичского вектора. Важно только его направление. */
function toLonLat(x: number, y: number, z: number): [number, number] {
  const r = Math.hypot(x, y, z) || 1;
  return [(Math.atan2(y, x) * 180) / Math.PI, (Math.asin(z / r) * 180) / Math.PI];
}

function draw(element: HTMLCanvasElement, state: Frame, shown: number) {
  const context = element.getContext("2d");
  if (!context) return;

  const ratio = window.devicePixelRatio || 1;
  const { width, height } = state.size;
  if (element.width !== width * ratio || element.height !== height * ratio) {
    element.width = width * ratio;
    element.height = height * ratio;
  }
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

  const trajectory = state.trajectory;
  if (!trajectory) {
    context.fillStyle = "#6b7886";
    context.font = "13px system-ui";
    context.fillText(state.times.length ? "Загрузка траектории…" : "Запустите расчёт", 14, 22);
    return;
  }

  const last = trajectory.times_s.length - 1;
  const position = Math.min(Math.max(shown, 0), last);
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, last);
  const phase = position - lower;

  // Дискретное состояние — кто в строю, какие связи подняты, каков маршрут —
  // принадлежит тому отсчёту, о котором говорит остальной интерфейс. Между отсчётами
  // двигаются только положения.
  const discrete = state.step;
  const active = trajectory.active[discrete] ?? [];
  const links = trajectory.links[discrete] ?? [];

  const lonLat: [number, number][] = trajectory.satellite_ids.map((_, index) => {
    const a = trajectory.ecef_km[lower][index];
    const b = trajectory.ecef_km[upper][index];
    return toLonLat(
      a[0] + (b[0] - a[0]) * phase,
      a[1] + (b[1] - a[1]) * phase,
      a[2] + (b[2] - a[2]) * phase,
    );
  });
  const points = lonLat.map(([lon, lat]) => place(lon, lat));

  const indexById = new Map(trajectory.satellite_ids.map((id, index) => [id, index]));
  const onRoute = new Set(state.routePath);

  // Межспутниковые связи рисуются первыми и самыми тусклыми: они фон для маршрута, и
  // их бывает несколько сотен. Отрезки, которые перешли бы через 180-й меридиан,
  // отбрасываются, а не рисуются линией через всю карту.
  context.strokeStyle = "rgba(76,154,255,0.32)";
  context.lineWidth = 1;
  context.beginPath();
  for (const [i, j] of links) {
    const from = points[i];
    const to = points[j];
    if (!from || !to) continue;
    if (Math.abs(lonLat[i][0] - lonLat[j][0]) > 170) continue;
    context.moveTo(from[0], from[1]);
    context.lineTo(to[0], to[1]);
  }
  context.stroke();

  const sitePoint = new Map<string, [number, number] | null>();
  for (const site of trajectory.ground_sites) {
    sitePoint.set(site.id, place(site.lon_deg, site.lat_deg));
  }

  // Что выбранный терминал мог бы использовать — на фоне того, что он использует.
  if (state.client) {
    const visible = trajectory.ground_visible[state.client]?.[discrete] ?? [];
    const from = sitePoint.get(state.client);
    if (from) {
      context.strokeStyle = "rgba(154,167,180,0.28)";
      context.setLineDash([3, 3]);
      context.beginPath();
      for (const index of visible) {
        const to = points[index];
        if (!to) continue;
        context.moveTo(from[0], from[1]);
        context.lineTo(to[0], to[1]);
      }
      context.stroke();
      context.setLineDash([]);
    }
  }

  // Маршрут рисуется последним и самым ярким: это ответ, за которым пришли.
  if (state.routePath.length > 1) {
    context.strokeStyle = "#3fb950";
    context.lineWidth = 2.5;
    context.beginPath();
    let started = false;
    for (const id of state.routePath) {
      const index = indexById.get(id);
      const point = index === undefined ? sitePoint.get(id) : points[index];
      if (!point) continue;
      if (started) context.lineTo(point[0], point[1]);
      else context.moveTo(point[0], point[1]);
      started = true;
    }
    context.stroke();
    context.lineWidth = 1;
  }

  trajectory.satellite_ids.forEach((satelliteId, index) => {
    const point = points[index];
    if (!point) return;
    const [x, y] = point;

    if (!active[index]) {
      // Аппараты вне строя сохраняют расчётное положение, как требует кейс, поэтому
      // показываются там, где они есть, и помечаются как непригодные, а не прячутся.
      context.strokeStyle = "#4a5462";
      context.beginPath();
      context.moveTo(x - 3, y - 3);
      context.lineTo(x + 3, y + 3);
      context.moveTo(x + 3, y - 3);
      context.lineTo(x - 3, y + 3);
      context.stroke();
      return;
    }

    const highlighted = onRoute.has(satelliteId);
    context.fillStyle = planeColour(state.planeIds, trajectory.plane_ids[index]);
    context.beginPath();
    context.arc(x, y, highlighted ? 5 : 2.6, 0, Math.PI * 2);
    context.fill();

    if (highlighted) {
      context.strokeStyle = "#3fb950";
      context.lineWidth = 2;
      context.stroke();
      context.lineWidth = 1;
      context.fillStyle = "#e6edf3";
      context.font = "600 11px ui-monospace, monospace";
      context.fillText(satelliteId, x + 8, y - 6);
    }
  });

  for (const site of trajectory.ground_sites) {
    const point = sitePoint.get(site.id);
    if (!point) continue;
    const [x, y] = point;
    const isGateway = site.role === "gateway";
    const selected = site.id === state.client;

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
  context.font = "12px ui-monospace, monospace";
  context.fillText(clock(trajectory.times_s[discrete] ?? 0), 12, height - 12);
}
