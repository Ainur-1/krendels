/**
 * Четыре исследования, которые отвечают на вопросы, не заданные постановкой напрямую.
 *
 * Постановка спрашивает про мгновенную доступность и про десять названных отказов.
 * Здесь спрашивается остальное: для какого класса трафика конфигурация провалена,
 * сколько произвольных отказов она переносит, из какого классического семейства
 * вырос выданный проект и где стоило бы поставить вторую точку приземления.
 *
 * Каждое считается по нажатию, а не при открытии вкладки. Самое дорогое из них —
 * кривая деградации, и это несколько секунд: показывать её всем, кто просто заглянул
 * на вкладку, значило бы тратить их время без спроса.
 */

import { useState } from "react";

import { api, type DesignRef } from "../api";
import { duration, percent } from "../lib/format";
import type {
  DegradationCurve,
  DeliveryReport,
  FamilyReport,
  PlacementReport,
} from "../types";

export function ResiliencePanel({
  design,
  onError,
}: {
  design: DesignRef | null;
  onError: (message: string) => void;
}) {
  const [delivery, setDelivery] = useState<DeliveryReport | null>(null);
  const [degradation, setDegradation] = useState<DegradationCurve | null>(null);
  const [families, setFamilies] = useState<FamilyReport | null>(null);
  const [placement, setPlacement] = useState<PlacementReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function load<T>(label: string, work: () => Promise<T>, keep: (value: T) => void) {
    if (!design) return;
    setBusy(label);
    try {
      keep(await work());
    } catch {
      onError(`${label}: расчёт не выполнен`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <button
          disabled={!design || !!busy}
          onClick={() =>
            void load("Допустимая задержка", () => api.delivery(design!), setDelivery)
          }
        >
          Допустимая задержка
        </button>
        <button
          disabled={!design || !!busy}
          onClick={() =>
            void load("Кривая деградации", () => api.degradation(design!), setDegradation)
          }
        >
          Кривая деградации
        </button>
        <button
          disabled={!design || !!busy}
          onClick={() => void load("Семейства разноса", () => api.families(design!), setFamilies)}
        >
          Семейства разноса
        </button>
        <button
          disabled={!design || !!busy}
          onClick={() => void load("Место для шлюза", () => api.placement(design!), setPlacement)}
        >
          Место для второго шлюза
        </button>
        {busy && <span className="busy">{busy}…</span>}
      </div>

      {delivery && <DeliverySection report={delivery} />}
      {degradation && <DegradationSection curve={degradation} />}
      {families && <FamiliesSection report={families} />}
      {placement && <PlacementSection report={placement} />}

      {!delivery && !degradation && !families && !placement && !busy && (
        <p className="empty">
          Четыре исследования сверх постановки: что даёт разрешение подождать, сколько
          произвольных отказов проект переносит, из какого семейства он вырос и где
          стоило бы поставить вторую точку приземления.
        </p>
      )}
    </>
  );
}

/**
 * Доля доставленного против допустимой задержки, с ползунком по порогам.
 *
 * Ползунок идёт по тем порогам, которые посчитаны, а не по секундам: промежуточных
 * значений никто не мерил, и давать их двигать значило бы показывать придуманное.
 */
function DeliverySection({ report }: { report: DeliveryReport }) {
  const [index, setIndex] = useState(0);
  const deadline = report.deadlines_s[index];
  const worst = report.worst_within[index]?.share ?? 0;
  const instant = report.worst_within[0]?.share ?? 0;

  const width = 520;
  const height = 190;
  const pad = { left: 46, right: 12, top: 12, bottom: 32 };
  const x = (i: number) =>
    pad.left + (i / Math.max(1, report.deadlines_s.length - 1)) * (width - pad.left - pad.right);
  const y = (share: number) =>
    height - pad.bottom - share * (height - pad.top - pad.bottom);

  return (
    <div style={{ marginBottom: 22 }}>
      <h2>Доставка с допустимой задержкой</h2>
      <p className="hint" style={{ marginTop: 0 }}>
        Аппарат может забрать данные над пунктом, увезти их на борту и сбросить над шлюзом
        позже. Нулевая задержка — это обычная доступность; остальные пороги показывают, для
        какого класса трафика конфигурация действительно провалена.
      </p>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", maxWidth: width }}
        role="img"
        aria-label="Доля доставленного против допустимой задержки"
      >
        <line x1={pad.left} y1={y(0)} x2={width - pad.right} y2={y(0)} stroke="var(--line)" />
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={y(0)} stroke="var(--line)" />
        <line
          x1={pad.left}
          y1={y(0.9)}
          x2={width - pad.right}
          y2={y(0.9)}
          stroke="#d29922"
          strokeDasharray="4 3"
        />
        <text x={width - pad.right} y={y(0.9) - 4} textAnchor="end" fontSize="10" fill="#d29922">
          цель 90 %
        </text>
        <text x={4} y={pad.top + 8} fontSize="10" fill="#6b7886">
          100 %
        </text>
        <text x={4} y={y(0) + 3} fontSize="10" fill="#6b7886">
          0 %
        </text>

        {report.clients.map((client, row) => (
          <polyline
            key={client.client_id}
            fill="none"
            stroke={["#4c9aff", "#3fb950", "#f0883e"][row % 3]}
            strokeWidth={1.6}
            points={client.within.map((point, i) => `${x(i)},${y(point.share)}`).join(" ")}
          />
        ))}

        <polyline
          fill="none"
          stroke="#e6edf3"
          strokeWidth={2.4}
          points={report.worst_within.map((point, i) => `${x(i)},${y(point.share)}`).join(" ")}
        />

        {report.deadlines_s.map((seconds, i) => (
          <g key={seconds}>
            <circle cx={x(i)} cy={y(report.worst_within[i].share)} r={i === index ? 5 : 3} fill="#e6edf3" />
            <text x={x(i)} y={height - 10} textAnchor="middle" fontSize="10" fill="#6b7886">
              {seconds === 0 ? "0" : duration(seconds)}
            </text>
          </g>
        ))}
      </svg>

      <div className="row" style={{ marginTop: 6 }}>
        <input
          type="range"
          min={0}
          max={report.deadlines_s.length - 1}
          value={index}
          onChange={(event) => setIndex(Number(event.target.value))}
          aria-label="допустимая задержка"
        />
        <span className="hint" style={{ whiteSpace: "nowrap" }}>
          {deadline === 0 ? "без задержки" : `до ${duration(deadline)}`} → худший пункт{" "}
          <strong>{percent(worst)}</strong>
          {deadline > 0 && ` (было ${percent(instant)})`}
        </span>
      </div>

      <div className="scroll-x" style={{ marginTop: 8 }}>
        <table>
          <thead>
            <tr>
              <th>Пункт</th>
              {report.deadlines_s.map((seconds) => (
                <th key={seconds} className="num">
                  {seconds === 0 ? "сразу" : duration(seconds)}
                </th>
              ))}
              <th className="num">Макс. задержка</th>
              <th className="num">Не дошло</th>
            </tr>
          </thead>
          <tbody>
            {report.clients.map((client) => (
              <tr key={client.client_id}>
                <td>{client.client_id}</td>
                {client.within.map((point) => (
                  <td key={point.deadline_s} className="num">
                    {percent(point.share)}
                  </td>
                ))}
                <td className="num">
                  {client.max_latency_s === null ? "—" : duration(client.max_latency_s)}
                </td>
                <td className="num">{percent(client.undelivered_share)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Что остаётся от доступности после k произвольных отказов, с полосой разброса наборов. */
function DegradationSection({ curve }: { curve: DegradationCurve }) {
  const width = 520;
  const height = 200;
  const pad = { left: 46, right: 12, top: 12, bottom: 30 };
  const maxFailures = Math.max(...curve.points.map((p) => p.failures), 1);
  const floor = Math.min(...curve.points.map((p) => p.worst_worst_availability), 0.9) - 0.03;

  const x = (failures: number) =>
    pad.left + (failures / maxFailures) * (width - pad.left - pad.right);
  const y = (share: number) =>
    height - pad.bottom - ((share - floor) / Math.max(1 - floor, 0.01)) * (height - pad.top - pad.bottom);

  return (
    <div style={{ marginBottom: 22 }}>
      <h2>Кривая деградации</h2>
      <p className="hint" style={{ marginTop: 0 }}>
        Кейс задаёт названные отказы; здесь разыгрываются случайные, по {curve.points[1]?.trials ?? 0}{" "}
        набора на каждое число. Проект переносит{" "}
        <strong>{curve.tolerated_failures}</strong>{" "}
        {plural(curve.tolerated_failures, "отказ", "отказа", "отказов")}, не теряя цель ни в одном
        наборе, и теряет по {curve.slope_pp_per_satellite.toFixed(1)} п.п. за каждый потерянный
        аппарат.
      </p>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", maxWidth: width }}
        role="img"
        aria-label="Доступность худшего пункта против числа случайных отказов"
      >
        <line x1={pad.left} y1={y(floor)} x2={width - pad.right} y2={y(floor)} stroke="var(--line)" />
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={y(floor)} stroke="var(--line)" />
        <line
          x1={pad.left}
          y1={y(curve.target_availability)}
          x2={width - pad.right}
          y2={y(curve.target_availability)}
          stroke="#d29922"
          strokeDasharray="4 3"
        />
        <text x={4} y={y(curve.target_availability) + 3} fontSize="10" fill="#d29922">
          {percent(curve.target_availability, 0)}
        </text>

        {/* Полоса между лучшим и худшим набором: видно, что важно количество, а не выбор. */}
        <polygon
          fill="rgba(76, 154, 255, 0.18)"
          points={[
            ...curve.points.map((p) => `${x(p.failures)},${y(p.best_worst_availability)}`),
            ...[...curve.points]
              .reverse()
              .map((p) => `${x(p.failures)},${y(p.worst_worst_availability)}`),
          ].join(" ")}
        />
        <polyline
          fill="none"
          stroke="#4c9aff"
          strokeWidth={2}
          points={curve.points
            .map((p) => `${x(p.failures)},${y(p.mean_worst_availability)}`)
            .join(" ")}
        />

        {curve.points.map((point) => (
          <g key={point.failures}>
            <circle
              cx={x(point.failures)}
              cy={y(point.mean_worst_availability)}
              r={3}
              fill={point.meets_target_share === 1 ? "#3fb950" : point.meets_target_share > 0 ? "#d29922" : "#f85149"}
            >
              <title>
                {point.failures} отказов → в среднем {percent(point.mean_worst_availability)},
                худший набор {percent(point.worst_worst_availability)}, цель удержана в{" "}
                {percent(point.meets_target_share, 0)} наборов
              </title>
            </circle>
            {point.failures % 2 === 0 && (
              <text x={x(point.failures)} y={height - 10} textAnchor="middle" fontSize="10" fill="#6b7886">
                {point.failures}
              </text>
            )}
          </g>
        ))}
      </svg>
      <p className="hint" style={{ marginTop: 2 }}>
        Полоса — разброс между лучшим и худшим разыгранным набором. Точка зелёная, пока цель
        удержана во всех наборах, жёлтая — пока в части, красная — когда ни в одном.
      </p>
    </div>
  );
}

/** Кривая по разносу плоскостей: два семейства как два максимума. */
function FamiliesSection({ report }: { report: FamilyReport }) {
  const width = 520;
  const height = 190;
  const pad = { left: 46, right: 12, top: 12, bottom: 30 };
  const shares = report.points.map((p) => p.worst_availability);
  const low = Math.min(...shares);
  const high = Math.max(...shares);

  const x = (spacing: number) => pad.left + (spacing / 180) * (width - pad.left - pad.right);
  const y = (share: number) =>
    height - pad.bottom - ((share - low) / Math.max(high - low, 0.01)) * (height - pad.top - pad.bottom);

  return (
    <div style={{ marginBottom: 22 }}>
      <h2>Семейства разноса плоскостей</h2>
      <p className="hint" style={{ marginTop: 0 }}>
        У звезды плоскости разносят по 180°, у дельты — по 360°. Для {report.planes}{" "}
        плоскостей это {report.star_spacing_deg.toFixed(0)}° и {report.delta_spacing_deg.toFixed(0)}°.
        {report.supplied_family
          ? ` Выданный проект стоит на ${report.supplied_spacing_deg?.toFixed(1)}°, то есть это ${
              report.supplied_family === "star" ? "звезда" : "дельта"
            }.`
          : " Плоскости разнесены неравномерно, поэтому относить проект не к чему."}
      </p>

      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", maxWidth: width }}
        role="img"
        aria-label="Доступность худшего пункта против разноса плоскостей"
      >
        <line x1={pad.left} y1={y(low)} x2={width - pad.right} y2={y(low)} stroke="var(--line)" />
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={y(low)} stroke="var(--line)" />
        <text x={4} y={pad.top + 8} fontSize="10" fill="#6b7886">
          {percent(high)}
        </text>

        {[
          { value: report.star_spacing_deg, label: "180/P" },
          { value: report.delta_spacing_deg, label: "360/P" },
        ].map((rule) => (
          <g key={rule.label}>
            <line
              x1={x(rule.value)}
              y1={pad.top}
              x2={x(rule.value)}
              y2={y(low)}
              stroke="#6b7886"
              strokeDasharray="3 3"
            />
            <text x={x(rule.value) + 3} y={pad.top + 8} fontSize="9" fill="#6b7886">
              {rule.label}
            </text>
          </g>
        ))}

        <polyline
          fill="none"
          stroke="#4c9aff"
          strokeWidth={1.8}
          points={report.points.map((p) => `${x(p.spacing_deg)},${y(p.worst_availability)}`).join(" ")}
        />

        {[report.star_best, report.delta_best].map(
          (peak, index) =>
            peak && (
              <g key={index}>
                <circle cx={x(peak.spacing_deg)} cy={y(peak.worst_availability)} r={5} fill="#3fb950">
                  <title>
                    разнос {peak.spacing_deg}° → {percent(peak.worst_availability)}
                  </title>
                </circle>
                <text
                  x={x(peak.spacing_deg)}
                  y={y(peak.worst_availability) - 9}
                  textAnchor="middle"
                  fontSize="10"
                  fill="#3fb950"
                >
                  {percent(peak.worst_availability)}
                </text>
              </g>
            ),
        )}

        {report.supplied_spacing_deg !== null && (
          <circle
            cx={x(report.supplied_spacing_deg)}
            cy={y(
              report.points.reduce((best, point) =>
                Math.abs(point.spacing_deg - report.supplied_spacing_deg!) <
                Math.abs(best.spacing_deg - report.supplied_spacing_deg!)
                  ? point
                  : best,
              ).worst_availability,
            )}
            r={5}
            fill="none"
            stroke="#d29922"
            strokeWidth={2}
          >
            <title>выданная конфигурация</title>
          </circle>
        )}

        <text x={width - pad.right} y={height - 10} textAnchor="end" fontSize="10" fill="#6b7886">
          разнос плоскостей, градусы →
        </text>
      </svg>
    </div>
  );
}

/**
 * Поверхность размещения второй точки приземления.
 *
 * Рисуется как сетка прямоугольников, а не как карта: сетка честно показывает, что
 * измерено ровно столько точек, сколько нарисовано, и не создаёт впечатления, будто
 * между ними что-то посчитано.
 */
function PlacementSection({ report }: { report: PlacementReport }) {
  const gains = report.points.map((p) => p.gain_pp);
  const best = Math.max(...gains, 0.01);
  // Ровная поверхность — это тоже ответ, и молчать о ней нельзя: почти одинаково
  // зелёная сетка иначе читается как «везде отлично», а означает «место почти ничего
  // не решает». Порог взят с запасом между измеренными разбросами: на целой сети это
  // 1.1 пункта, на распавшейся — 32.2.
  const spread = best - Math.min(...gains);
  const flat = spread < 5;
  const cell = { w: 18, h: 18 };
  const width = report.lon_deg.length * cell.w + 60;
  const height = report.lat_deg.length * cell.h + 34;

  return (
    <div>
      <h2>Место для второй точки приземления</h2>
      <p className="hint" style={{ marginTop: 0 }}>
        Каждая клетка — отдельный прогон с пробным шлюзом в этой точке. Выданные данные
        при этом не меняются: это измерение того, чего стоил бы второй шлюз, а не
        предложение переписать вход.
        {report.best && (
          <>
            {" "}
            Лучшая точка — {report.best.lat_deg.toFixed(0)}° с. ш.,{" "}
            {report.best.lon_deg.toFixed(0)}° д.: {percent(report.best.worst_availability)} против{" "}
            {percent(report.baseline_worst_availability)} без неё.
          </>
        )}
        {flat
          ? ` Поверхность почти ровная: между лучшим и худшим местом ${spread.toFixed(1)} п.п., то есть место почти ничего не решает. Так бывает, когда межспутниковая сеть цела и довозит трафик куда угодно.`
          : ` Чем темнее клетка, тем больше прирост; между лучшим и худшим местом ${spread.toFixed(1)} п.п. Неровная поверхность означает, что сеть не довозит трафик сама и шлюз должен стоять ближе к пунктам.`}
      </p>

      <div className="scroll-x">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: "100%", maxWidth: width }}
          role="img"
          aria-label="Прирост доступности от второй точки приземления по широте и долготе"
        >
          {report.points.map((point) => {
            const row = report.lat_deg.indexOf(point.lat_deg);
            const column = report.lon_deg.indexOf(point.lon_deg);
            const weight = Math.max(0, point.gain_pp) / best;
            return (
              <rect
                key={`${point.lat_deg}/${point.lon_deg}`}
                x={40 + column * cell.w}
                // Широты снизу вверх: север сверху, как на любой карте.
                y={(report.lat_deg.length - 1 - row) * cell.h}
                width={cell.w - 1}
                height={cell.h - 1}
                fill={`rgba(63, 185, 80, ${0.08 + 0.9 * weight})`}
              >
                <title>
                  {point.lat_deg}° / {point.lon_deg}° → {percent(point.worst_availability)} (+
                  {point.gain_pp.toFixed(1)} п.п.)
                </title>
              </rect>
            );
          })}

          {report.lat_deg.map((lat, row) => (
            <text
              key={lat}
              x={34}
              y={(report.lat_deg.length - 1 - row) * cell.h + 13}
              textAnchor="end"
              fontSize="10"
              fill="#6b7886"
            >
              {lat.toFixed(0)}°
            </text>
          ))}
          {report.lon_deg.map((lon, column) =>
            column % 4 === 0 ? (
              <text
                key={lon}
                x={40 + column * cell.w}
                y={height - 12}
                fontSize="10"
                fill="#6b7886"
              >
                {lon.toFixed(0)}
              </text>
            ) : null,
          )}
          <text x={40} y={height - 1} fontSize="9" fill="#6b7886">
            долгота, градусы
          </text>
        </svg>
      </div>
    </div>
  );
}

/** Русский счёт: 1 отказ, 2 отказа, 5 отказов. */
function plural(count: number, one: string, few: string, many: string): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return many;
  const last = count % 10;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}
