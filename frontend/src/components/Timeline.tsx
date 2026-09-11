/**
 * The availability diagram: one band per terminal, coloured by why there was no route.
 *
 * This is the most useful picture in the service. A single availability figure says
 * 80 % and stops; the band says *when* and *why*, and on the supplied data the four
 * scenarios produce four visibly different pictures — a coverage problem, a network
 * problem, a mixture, and one bad window per day that a percentage hides completely.
 *
 * Drawn on canvas because it is 720 cells per terminal and they change on every
 * recalculation. Clicking anywhere moves the clock there and selects that terminal,
 * so reading the picture and inspecting the moment are the same gesture.
 */

import { useEffect, useRef, useState } from "react";

import { CAUSE_LABEL, CAUSE_RGB, clock, duration, percent } from "../lib/format";
import type { OutageCause, Run } from "../types";

const BAND_HEIGHT = 26;
const BAND_GAP = 6;
const LABEL_WIDTH = 54;
const AXIS_HEIGHT = 18;

// Wall-clock milliseconds per step of the calculation grid. A day at 120 s steps is
// 720 of them, so 1x plays the whole horizon in about two minutes - slow enough to
// watch a pattern form, fast enough not to be a waiting room. The map interpolates
// between steps, so these are smooth rather than a slideshow.
const SPEEDS: { label: string; stepMs: number }[] = [
  { label: "0.5×", stepMs: 320 },
  { label: "1×", stepMs: 160 },
  { label: "2×", stepMs: 80 },
  { label: "4×", stepMs: 40 },
];

export function Timeline({
  run,
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
  const height = clients.length * (BAND_HEIGHT + BAND_GAP) + AXIS_HEIGHT;

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(360, Math.floor(entry.contentRect.width))),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Playback walks the grid at a fixed rate rather than in real time: a 24-hour run
  // at 120 s steps would take a day to watch honestly, and what the user wants is to
  // see the pattern move, not to wait for it.
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
      const top = row * (BAND_HEIGHT + BAND_GAP);
      const series = run.clients[clientId];

      context.fillStyle = clientId === client ? "#e6edf3" : "#9aa7b4";
      context.font = `${clientId === client ? "600 " : ""}12px system-ui`;
      context.fillText(clientId, 0, top + BAND_HEIGHT / 2 + 4);

      // Runs of the same cause are merged into one rectangle. At 720 steps over
      // maybe 900 pixels a per-step fill leaves seams where cells fall between
      // device pixels; a merged run is both faster and cleaner.
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
    });

    // Hour ticks, read from the grid rather than assumed: a judge's scenario may
    // run for twelve hours on a 60 s step.
    const axisTop = clients.length * (BAND_HEIGHT + BAND_GAP);
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
  }, [run, width, height, step, client, clients, steps]);

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
            const row = Math.floor(y / (BAND_HEIGHT + BAND_GAP));
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
            // Dragging the slider is navigation, not playback. Leaving it running
            // would fight the hand that is moving it.
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

      {series && (
        <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
          {client}: доступность {percent(series.metrics.availability_share)}, перерывов{" "}
          {series.metrics.gap_count}, самый долгий {duration(series.metrics.max_gap_s)}. В этот
          момент — {CAUSE_LABEL[currentCause]}.
        </p>
      )}
    </section>
  );
}
