/**
 * Два исследования, которые превращают расчёт в рекомендацию.
 *
 * Перебор отвечает на вопрос «есть ли конфигурация лучше этой», пробуя около сотни и
 * нанося их все на график: доступность против самого долгого непрерывного перерыва, с
 * пометкой тех, кто не проигрывает сразу по обоим. Его победителя можно применить к
 * рабочему проекту одним нажатием, и в этом весь смысл: подборщик, результат которого
 * приходится перенабирать руками, — это отчёт, а не инструмент.
 *
 * Выбивание отвечает на вопрос «без какого аппарата хуже всего», убирая каждый на
 * целые сутки. На выданной группировке интересный результат оказывается отрицательным:
 * столбцы выходят почти вровень, то есть проект не держится ни на одном аппарате, и
 * уязвимость находится в другом месте.
 */

import { useState } from "react";

import { api, type DesignRef } from "../api";
import type { StudyResults } from "../App";
import { duration, percent } from "../lib/format";
import { StaleNotice } from "./StaleNotice";
import type { CriticalityReport, Scenario, SweepReport } from "../types";

export function AnalysisPanel({
  design,
  scenario,
  results,
  stale,
  onResult,
  onApply,
  onError,
}: {
  design: DesignRef | null;
  scenario: Scenario | null;
  results: StudyResults;
  stale: boolean;
  onResult: (patch: Partial<StudyResults>) => void;
  onApply: (scenario: Scenario) => void;
  onError: (message: string) => void;
}) {
  // Посчитанное хранит оболочка: иначе переключение вкладки стирало бы результат, и
  // эксперту приходилось бы нажимать кнопку заново.
  const { sweep, criticality } = results;
  const [busy, setBusy] = useState<string | null>(null);

  async function runSweep(mode: "spacing" | "refine") {
    if (!design) return;
    setBusy("Перебор конфигураций");
    try {
      onResult({ sweep: await api.sweep(design, mode) });
    } catch {
      onError("Перебор не выполнен");
    } finally {
      setBusy(null);
    }
  }

  async function runCriticality() {
    if (!design) return;
    setBusy("Анализ критичности");
    try {
      onResult({ criticality: await api.criticality(design) });
    } catch {
      onError("Анализ критичности не выполнен");
    } finally {
      setBusy(null);
    }
  }

  function applyBest() {
    if (!sweep || !scenario) return;
    const next = structuredClone(scenario);
    next.design.planes.forEach((plane, index) => {
      plane.raan_deg = sweep.best.raan_deg[index];
      plane.phase_deg = sweep.best.phase_deg[index];
    });
    onApply(next);
  }

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <button disabled={!design || !!busy} onClick={() => void runSweep("spacing")}>
          Подобрать разнос плоскостей
        </button>
        <button disabled={!design || !!busy} onClick={() => void runSweep("refine")}>
          Уточнить по одной плоскости
        </button>
        <button disabled={!design || !!busy} onClick={() => void runCriticality()}>
          Критичность аппаратов
        </button>
        {busy && <span className="busy">{busy}…</span>}
      </div>

      {stale && (sweep || criticality) && <StaleNotice />}

      {sweep && (
        <div style={{ marginBottom: 20 }}>
          <h2>Подбор конфигурации</h2>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Вариант</th>
                  <th>RAAN</th>
                  <th>Фазирование</th>
                  <th className="num">Худший пункт</th>
                  <th className="num">Макс. перерыв</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>текущий</td>
                  <td className="num">{angles(sweep.baseline.raan_deg)}</td>
                  <td className="num">{angles(sweep.baseline.phase_deg)}</td>
                  <td className="num">{percent(sweep.baseline.worst_availability)}</td>
                  <td className="num">{duration(sweep.baseline.worst_max_gap_s)}</td>
                </tr>
                <tr className="selected">
                  <td>
                    <strong>лучший</strong>
                  </td>
                  <td className="num">{angles(sweep.best.raan_deg)}</td>
                  <td className="num">{angles(sweep.best.phase_deg)}</td>
                  <td className="num">{percent(sweep.best.worst_availability)}</td>
                  <td className="num">{duration(sweep.best.worst_max_gap_s)}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" onClick={applyBest}>
              Применить к текущему проекту
            </button>
            <span className="hint">
              {sweep.best.worst_availability > sweep.baseline.worst_availability
                ? `выигрыш ${(
                    (sweep.best.worst_availability - sweep.baseline.worst_availability) *
                    100
                  ).toFixed(2)} п.п. у худшего пункта`
                : "текущая конфигурация уже лучшая среди перебранных"}
            </span>
          </div>

          <ParetoPlot report={sweep} />
        </div>
      )}

      {criticality && (
        <div>
          <h2>Критичность аппаратов</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Каждый аппарат по очереди выключается на весь горизонт. Столбец — насколько
            падает доступность худшего пункта. Разброс между самым и наименее важным —{" "}
            {criticality.spread_pp.toFixed(2)} п.п.
            {criticality.spread_pp < 1.5 &&
              " Разброс мал: аппарата, на котором держится группировка, нет."}
          </p>
          <KnockoutChart report={criticality} />
        </div>
      )}

      {!sweep && !criticality && !busy && (
        <p className="empty">
          Перебор конфигураций и анализ критичности считаются на сервере за секунды.
        </p>
      )}
    </>
  );
}

/**
 * Доступность против самого долгого перерыва, каждый кандидат — точка.
 *
 * Рисуется как SVG, а не canvas: сотню точек разложить ничего не стоит, а фронт должен
 * отзываться на наведение, иначе он бесполезен.
 *
 * Вертикальная ось начинается не с худшего кандидата. В переборе разноса всегда есть
 * несколько вырожденных проектов — все плоскости на одном RAAN это одна орбита трижды,
 * — и на сценарии 01 они набирают 12 %, что сжимает интересную полосу между 96 и 100 %
 * в несколько пикселей. Ось обрезается на десять пунктов ниже меньшего из двух —
 * базовой конфигурации и лучшей, — а то, что ушло вниз, пересчитывается и называется, а
 * не выбрасывается молча.
 *
 * Закрашенные точки измерены на полной сетке; полые отранжированы по прореженной и
 * нарисованы бледно, потому что это места, куда поиск заглядывал, а не то, что он
 * нашёл. Различие вынесено в подпись по той же причине, по которой оно есть в данных:
 * процент, которого никто не мерил, не должен выглядеть как измеренный.
 */
function ParetoPlot({ report }: { report: SweepReport }) {
  const width = 520;
  const height = 200;
  const pad = { left: 46, right: 12, top: 12, bottom: 30 };

  const floor = Math.max(
    0,
    Math.min(report.baseline.worst_availability, report.best.worst_availability) - 0.1,
  );
  const points = report.candidates.filter((c) => c.worst_availability >= floor);
  const hidden = report.candidates.length - points.length;

  const gaps = points.map((c) => c.worst_max_gap_s);
  const shares = points.map((c) => c.worst_availability);
  const maxGap = Math.max(...gaps, 1);
  const minShare = Math.min(...shares, floor);
  const maxShare = Math.max(...shares);
  const span = Math.max(maxShare - minShare, 0.01);

  const x = (gap: number) =>
    pad.left + (gap / maxGap) * (width - pad.left - pad.right);
  const y = (share: number) =>
    height - pad.bottom - ((share - minShare) / span) * (height - pad.top - pad.bottom);

  const onFront = new Set(report.frontier.map(key));

  return (
    <>
    <svg
      viewBox={`0 0 ${width} ${height}`}
      style={{ width: "100%", maxWidth: width, marginTop: 12 }}
      role="img"
      aria-label="Фронт Парето: доступность против максимального перерыва"
    >
      <line
        x1={pad.left}
        y1={height - pad.bottom}
        x2={width - pad.right}
        y2={height - pad.bottom}
        stroke="var(--line)"
      />
      <line
        x1={pad.left}
        y1={pad.top}
        x2={pad.left}
        y2={height - pad.bottom}
        stroke="var(--line)"
      />
      <text x={width - pad.right} y={height - 8} textAnchor="end" fontSize="10" fill="#6b7886">
        максимальный перерыв →
      </text>
      <text x={4} y={pad.top + 8} fontSize="10" fill="#6b7886">
        {percent(maxShare)}
      </text>
      <text x={4} y={height - pad.bottom} fontSize="10" fill="#6b7886">
        {percent(minShare)}
      </text>

      {points.map((candidate, index) => {
        const front = onFront.has(key(candidate));
        return (
          <circle
            key={index}
            cx={x(candidate.worst_max_gap_s)}
            cy={y(candidate.worst_availability)}
            r={front ? 4 : 2.2}
            fill={front ? "#3fb950" : candidate.approximate ? "none" : "#4c9aff"}
            stroke={front ? "#0e1116" : candidate.approximate ? "#3a4757" : "none"}
            strokeWidth={candidate.approximate ? 1 : 0}
          >
            <title>
              RAAN {angles(candidate.raan_deg)} · фаза {angles(candidate.phase_deg)} →{" "}
              {percent(candidate.worst_availability)}, перерыв{" "}
              {duration(candidate.worst_max_gap_s)}
              {candidate.approximate ? " (оценка по прореженной сетке)" : ""}
            </title>
          </circle>
        );
      })}

      <circle
        cx={x(report.baseline.worst_max_gap_s)}
        cy={y(report.baseline.worst_availability)}
        r={5}
        fill="none"
        stroke="#d29922"
        strokeWidth={2}
      >
        <title>текущая конфигурация</title>
      </circle>
    </svg>
    <p className="hint" style={{ marginTop: 2 }}>
      Зелёным — конфигурации, которые не проигрывают сразу по обоим показателям.
      Жёлтым обведена текущая. Закрашенные точки посчитаны на полной сетке времени,
      полые — ранжированы по прореженной: это места, куда поиск заглядывал, а не
      измеренные значения.
      {hidden > 0 &&
        ` Ниже шкалы осталось ${hidden} вариантов с доступностью хуже ${percent(floor)}.`}
    </p>
    </>
  );
}

/**
 * Список выбиваний, самые дорогие первыми.
 *
 * Показывается по дюжине, а не во вложенном прокручиваемом окне. Сорок восемь строк в
 * окне высотой 260 пикселей на странице, которая и так прокручивается, читать неудобно,
 * и оно само уезжает на середину, как только кнопка, его породившая, получает фокус, —
 * с этого всё и началось.
 */
function KnockoutChart({ report }: { report: CriticalityReport }) {
  const [all, setAll] = useState(false);
  const worst = Math.max(...report.knockouts.map((k) => k.drop_pp), 0.01);
  const shown = all ? report.knockouts : report.knockouts.slice(0, 12);

  return (
    <div>
      <table>
        <thead>
          <tr>
            <th>Аппарат</th>
            <th>Плоскость</th>
            <th className="num">Просадка</th>
            <th style={{ width: "45%" }} />
          </tr>
        </thead>
        <tbody>
          {shown.map((knockout) => (
            <tr key={knockout.satellite_id}>
              <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {knockout.satellite_id}
              </td>
              <td className="hint">{knockout.plane_id}</td>
              <td className="num">{knockout.drop_pp.toFixed(2)} п.п.</td>
              <td>
                <div
                  style={{
                    height: 8,
                    borderRadius: 2,
                    background: "var(--accent-dim)",
                    width: `${Math.max(2, (knockout.drop_pp / worst) * 100)}%`,
                  }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {report.knockouts.length > 12 && (
        <button className="ghost" onClick={() => setAll((value) => !value)}>
          {all
            ? "Свернуть"
            : `Показать все ${report.knockouts.length} · сейчас 12 самых критичных`}
        </button>
      )}
    </div>
  );
}

const angles = (values: number[]) =>
  values.map((value) => Math.round(value * 100) / 100).join(" / ");

const key = (candidate: { raan_deg: number[]; phase_deg: number[] }) =>
  `${candidate.raan_deg.join(",")}|${candidate.phase_deg.join(",")}`;
