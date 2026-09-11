/**
 * Диаграмма доступности: по полосе на терминал, раскрашенной причиной отсутствия маршрута.
 *
 * Это самая полезная картинка в сервисе. Одна цифра доступности говорит «80 %» и на
 * этом заканчивается; полоса говорит **когда** и **почему**, и на выданных данных
 * четыре сценария дают четыре заметно разных картины: нехватка покрытия, проблема
 * сети, смесь и одно неудачное окно в сутки, которое процент прячет целиком.
 *
 * Рисуется на canvas, потому что это 720 ячеек на терминал и они меняются при каждом
 * пересчёте. Клик в любом месте переносит туда часы и выбирает этот терминал, так что
 * прочитать картинку и рассмотреть момент — это одно движение.
 *
 * Под каждой полосой идёт узкая лента запаса маршрутов. Она отвечает на другой вопрос:
 * основная полоса говорит, есть ли связь, лента — на скольких аппаратах она держится.
 * На выданных данных разница разительная: полоса почти сплошь зелёная при 96.7 %
 * доступности, а лента под ней жёлтая на четырёх пятых суток, потому что маршрут всё
 * это время ровно один. Показывать это отдельной лентой, а не заменой цвета, —
 * сознательное решение: причина перерыва остаётся главной картинкой.
 */

import { useEffect, useRef, useState } from "react";

import { CAUSE_LABEL, CAUSE_RGB, clock, duration, percent } from "../lib/format";
import type { OutageCause, RedundancyReport, Run } from "../types";

const BAND_HEIGHT = 26;
const BAND_GAP = 6;
const SPARE_HEIGHT = 6;
const SPARE_GAP = 2;

/** Ноль маршрутов, один, два и больше. Ноль совпадает с отсутствием связи на полосе выше. */
const SPARE_RGB = ["#3a2226", "#d29922", "#3fb950"] as const;
const LABEL_WIDTH = 54;
const AXIS_HEIGHT = 18;

// Сколько реальных миллисекунд приходится на один отсчёт сетки. Сутки при шаге 120 с
// — это 720 отсчётов, поэтому на скорости 1× весь горизонт проигрывается примерно за
// две минуты: достаточно медленно, чтобы заметить закономерность, и достаточно
// быстро, чтобы не быть залом ожидания. Карта интерполирует между отсчётами, поэтому
// это плавное движение, а не показ слайдов.
const SPEEDS: { label: string; stepMs: number }[] = [
  { label: "0.5×", stepMs: 320 },
  { label: "1×", stepMs: 160 },
  { label: "2×", stepMs: 80 },
  { label: "4×", stepMs: 40 },
];

export function Timeline({
  run,
  redundancy,
  step,
  client,
  playing,
  stepMs,
  onStep,
  onClient,
  onPlaying,
  onStepMs,
}: {
  run: Run | null;
  redundancy: RedundancyReport | null;
  step: number;
  client: string | null;
  playing: boolean;
  stepMs: number;
  onStep: (step: number) => void;
  onClient: (client: string) => void;
  onPlaying: (playing: boolean) => void;
  onStepMs: (stepMs: number) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);

  const clients = run ? Object.keys(run.clients) : [];
  const steps = run?.times_s.length ?? 0;
  // Лента запаса добавляет высоты только тогда, когда есть что показать: до её
  // прихода шкала не должна дёргаться, а после — не должна налезать сама на себя.
  const spare = redundancy?.series ?? null;
  const rowHeight =
    BAND_HEIGHT + BAND_GAP + (spare ? SPARE_HEIGHT + SPARE_GAP : 0);
  const height = clients.length * rowHeight + AXIS_HEIGHT;

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(360, Math.floor(entry.contentRect.width))),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Проигрывание идёт по сетке с постоянной скоростью, а не в реальном времени:
  // честно смотреть суточный прогон пришлось бы сутки, а нужно увидеть, как движется
  // картина, а не дождаться её.
  useEffect(() => {
    if (!playing || !steps) return;
    const timer = window.setInterval(() => {
      onStep((step + 1) % steps);
    }, stepMs);
    return () => window.clearInterval(timer);
  }, [playing, step, steps, stepMs, onStep]);

  useEffect(() => {
    const context = canvas.current?.getContext("2d");
    if (!context || !run) return;

    const ratio = window.devicePixelRatio || 1;
    canvas.current!.width = width * ratio;
    canvas.current!.height = height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const plotWidth = width - LABEL_WIDTH;
    const cell = plotWidth / steps;

    clients.forEach((clientId, row) => {
      const top = row * rowHeight;
      const series = run.clients[clientId];

      context.fillStyle = clientId === client ? "#e6edf3" : "#9aa7b4";
      context.font = `${clientId === client ? "600 " : ""}12px system-ui`;
      context.fillText(clientId, 0, top + BAND_HEIGHT / 2 + 4);

      // Идущие подряд отсчёты с одной причиной сливаются в один прямоугольник. При
      // 720 отсчётах на примерно 900 пикселей заливка по отсчёту оставляет швы там,
      // где ячейка попадает между физическими пикселями; слитая полоса и быстрее, и
      // чище.
      let runStart = 0;
      let runCause: OutageCause = series.cause[0];
      for (let index = 1; index <= steps; index += 1) {
        const cause = index < steps ? series.cause[index] : ("__end__" as OutageCause);
        if (cause === runCause) continue;
        context.fillStyle = CAUSE_RGB[runCause] ?? CAUSE_RGB.none;
        context.fillRect(
          LABEL_WIDTH + runStart * cell,
          top,
          Math.max(cell, (index - runStart) * cell),
          BAND_HEIGHT,
        );
        runStart = index;
        runCause = cause;
      }

      context.strokeStyle = clientId === client ? "#4c9aff" : "#2a3441";
      context.lineWidth = 1;
      context.strokeRect(LABEL_WIDTH + 0.5, top + 0.5, plotWidth - 1, BAND_HEIGHT - 1);

      const counts = spare?.[clientId];
      if (!counts) return;

      // Та же склейка подряд идущих одинаковых значений, что и у основной полосы, и
      // по той же причине: 720 ячеек на 900 пикселей поштучно оставляют швы.
      const spareTop = top + BAND_HEIGHT + SPARE_GAP;
      let spareStart = 0;
      let spareValue = Math.min(2, counts[0] ?? 0);
      for (let index = 1; index <= steps; index += 1) {
        const value = index < steps ? Math.min(2, counts[index] ?? 0) : -1;
        if (value === spareValue) continue;
        context.fillStyle = SPARE_RGB[spareValue];
        context.fillRect(
          LABEL_WIDTH + spareStart * cell,
          spareTop,
          Math.max(cell, (index - spareStart) * cell),
          SPARE_HEIGHT,
        );
        spareStart = index;
        spareValue = value;
      }
    });

    // Часовые засечки берутся из сетки, а не предполагаются: сценарий жюри может идти
    // двенадцать часов с шагом 60 с.
    const axisTop = clients.length * rowHeight;
    context.fillStyle = "#6b7886";
    context.strokeStyle = "#2a3441";
    context.font = "10px var(--mono, monospace)";
    const horizon = run.summary.horizon_s;
    const hourStep = horizon > 12 * 3600 ? 3 * 3600 : 3600;
    for (let t = 0; t <= horizon; t += hourStep) {
      const x = LABEL_WIDTH + (t / horizon) * plotWidth;
      context.beginPath();
      context.moveTo(x, axisTop);
      context.lineTo(x, axisTop + 4);
      context.stroke();
      context.fillText(`${Math.round(t / 3600)}ч`, x - 6, axisTop + 15);
    }

    const cursorX = LABEL_WIDTH + (step + 0.5) * cell;
    context.strokeStyle = "#ffffff";
    context.lineWidth = 1.5;
    context.beginPath();
    context.moveTo(cursorX, 0);
    context.lineTo(cursorX, axisTop);
    context.stroke();
  }, [run, width, height, step, client, clients, steps, spare, rowHeight]);

  if (!run) {
    return (
      <section className="panel">
        <h2>Диаграмма доступности</h2>
        <p className="empty">Запустите расчёт, чтобы увидеть интервалы связи и перерывы.</p>
      </section>
    );
  }

  const series = client ? run.clients[client] : null;
  const currentCause = series?.cause[step] ?? "none";
  const spareNow = client && spare?.[client] ? spare[client][step] : null;
  const spareShare =
    client && redundancy
      ? (redundancy.clients.find((c) => c.client_id === client)?.single_path_share ?? null)
      : null;

  return (
    <section className="panel">
      <div className="row" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>Диаграмма доступности</h2>
        <span style={{ flex: 1 }} />
        <span className="hint" style={{ fontFamily: "var(--mono)" }}>
          {clock(run.times_s[step])} · шаг {step + 1} из {steps}
        </span>
      </div>

      <div ref={wrapper}>
        <canvas
          ref={canvas}
          style={{ display: "block", width: "100%", height, cursor: "pointer" }}
          onClick={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            const x = event.clientX - box.left - LABEL_WIDTH;
            const y = event.clientY - box.top;
            const plotWidth = width - LABEL_WIDTH;
            if (x >= 0 && plotWidth > 0) {
              onStep(Math.min(steps - 1, Math.max(0, Math.floor((x / plotWidth) * steps))));
            }
            const row = Math.floor(y / rowHeight);
            if (row >= 0 && row < clients.length) onClient(clients[row]);
          }}
        />
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <button className="ghost" onClick={() => onPlaying(!playing)}>
          {playing ? "❚❚" : "▶"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, steps - 1)}
          value={step}
          onChange={(event) => {
            // Перетаскивание ползунка — это навигация, а не проигрывание. Оставить
            // его запущенным значило бы бороться с рукой, которая его двигает.
            if (playing) onPlaying(false);
            onStep(Number(event.target.value));
          }}
        />
        <select
          aria-label="скорость проигрывания"
          style={{ width: "auto", flexShrink: 0 }}
          value={stepMs}
          onChange={(event) => onStepMs(Number(event.target.value))}
        >
          {SPEEDS.map((speed) => (
            <option key={speed.stepMs} value={speed.stepMs}>
              {speed.label}
            </option>
          ))}
        </select>
      </div>

      <div className="legend" style={{ marginTop: 8 }}>
        {(["none", ...run.outage_causes] as OutageCause[]).map((cause) => (
          <span key={cause}>
            <i className="swatch" style={{ background: CAUSE_RGB[cause] }} />
            {CAUSE_LABEL[cause]}
          </span>
        ))}
      </div>

      {spare && (
        <div className="legend" style={{ marginTop: 4 }}>
          <span className="hint">узкая лента — запас маршрутов:</span>
          <span>
            <i className="swatch" style={{ background: SPARE_RGB[1] }} />
            маршрут один
          </span>
          <span>
            <i className="swatch" style={{ background: SPARE_RGB[2] }} />
            два и больше
          </span>
        </div>
      )}

      {series && (
        <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
          {client}: доступность {percent(series.metrics.availability_share)}, перерывов{" "}
          {series.metrics.gap_count}, самый долгий {duration(series.metrics.max_gap_s)}. В этот
          момент — {CAUSE_LABEL[currentCause]}
          {spareNow !== null && `, независимых маршрутов ${spareNow}`}.
          {spareShare !== null && (
            <>
              {" "}
              Связь держится на одном аппарате {percent(spareShare)} времени.
            </>
          )}
        </p>
      )}
    </section>
  );
}
