/**
 * Показатели, которые просит кейс, по каждому терминалу, плюс маршрут на текущем отсчёте.
 *
 * Доступность и видимость стоят рядом намеренно: промежуток между ними — это та часть
 * суток, когда аппарат над головой есть, а данные всё равно не уходят. Ровно об этом
 * кейс и говорит, различая покрытие и достижимость. На сценарии 04 этот промежуток —
 * тридцать пунктов.
 *
 * Задержка стоит в той же таблице по другой причине. Три стратегии маршрутизации на
 * доступность не влияют вовсе — путь либо есть, либо нет, — и различаются они только
 * здесь. Без этой колонки выбор стратегии в шапке выглядел бы переключателем, который
 * ничего не делает.
 */

import { CAUSE_LABEL, CAUSE_RGB, decimal, duration, km, ms, percent } from "../lib/format";
import type { OutageCause, Run } from "../types";

export function MetricsPanel({
  run,
  client,
  step,
  onClient,
}: {
  run: Run | null;
  client: string | null;
  step: number;
  onClient: (client: string) => void;
}) {
  if (!run) {
    return <p className="empty">Расчёт ещё не запускался.</p>;
  }

  const target = run.summary.target_availability;
  const series = client ? run.clients[client] : null;
  const path = series?.path[step] ?? [];

  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className={run.summary.meets_target ? "pill ok" : "pill bad"}>
          {run.summary.meets_target ? "цель достигнута" : "цель не достигнута"}
        </span>
        <span className="hint">
          ориентир {percent(target, 0)} для каждого пункта; худший результат{" "}
          {percent(run.summary.worst_availability)}, самый долгий перерыв{" "}
          {duration(run.summary.worst_max_gap_s)}
        </span>
      </div>

      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th>Пункт</th>
              <th className="num">Доступность</th>
              <th className="num">Видимость</th>
              <th className="num">Перерывов</th>
              <th className="num">Макс. перерыв</th>
              <th className="num">Переходов</th>
              <th className="num">Длина трассы</th>
              <th className="num" title="задержка распространения туда и обратно">
                Задержка
              </th>
              <th>Цель</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(run.clients).map(([id, data]) => (
              <tr
                key={id}
                className={id === client ? "selected" : ""}
                onClick={() => onClient(id)}
                style={{ cursor: "pointer" }}
              >
                <td>{id}</td>
                <td className="num">{percent(data.metrics.availability_share)}</td>
                <td className="num">{percent(data.metrics.visibility_share)}</td>
                <td className="num">{data.metrics.gap_count}</td>
                <td className="num">
                  {duration(data.metrics.max_gap_s)}
                  {data.gaps.some((gap) => gap.at_horizon_edge) && (
                    <span className="hint" title="есть перерыв на краю расчёта">
                      {" "}
                      *
                    </span>
                  )}
                </td>
                <td className="num">{decimal(data.metrics.mean_hops)}</td>
                <td className="num">{km(data.metrics.mean_route_length_km)}</td>
                <td className="num" title={`максимум за горизонт ${ms(data.metrics.max_rtt_ms)}`}>
                  {ms(data.metrics.mean_rtt_ms)}
                </td>
                <td>
                  <span className={data.metrics.meets_target ? "pill ok" : "pill bad"}>
                    {data.metrics.meets_target ? "да" : "нет"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {run.summary.mean_rtt_ms !== null && (
        <p className="hint" style={{ marginTop: 6 }}>
          Задержка — только распространение сигнала: обработка в аппарате и ожидание в
          очереди кейсом не заданы. В среднем по пунктам {ms(run.summary.mean_rtt_ms)},
          наибольшая за горизонт {ms(run.summary.max_rtt_ms)}. На доступность выбор
          стратегии не влияет — различаются стратегии именно здесь.
        </p>
      )}

      {Object.values(run.clients).some((data) =>
        data.gaps.some((gap) => gap.at_horizon_edge),
      ) && (
        <p className="hint" style={{ marginTop: 6 }}>
          * перерыв упирается в край расчёта — его настоящая длительность неизвестна,
          обрезана сетка, а не связь.
        </p>
      )}

      {series && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
          <div>
            <h2>Причины перерывов · {client}</h2>
            {Object.keys(series.metrics.causes).length === 0 ? (
              <p className="empty">Перерывов нет.</p>
            ) : (
              <table>
                <tbody>
                  {Object.entries(series.metrics.causes)
                    .sort((a, b) => b[1] - a[1])
                    .map(([cause, count]) => (
                      <tr key={cause}>
                        <td style={{ width: 18 }}>
                          <i
                            className="swatch"
                            style={{ background: CAUSE_RGB[cause as OutageCause] }}
                          />
                        </td>
                        <td>{CAUSE_LABEL[cause as OutageCause]}</td>
                        <td className="num">{count}</td>
                        <td className="num">
                          {percent(count / series.metrics.steps)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>

          <div>
            <h2>Маршрут в текущий момент</h2>
            {path.length === 0 ? (
              <p className="empty">
                Маршрута нет — {CAUSE_LABEL[series.cause[step]]}.
              </p>
            ) : (
              <>
                <p style={{ fontFamily: "var(--mono)", fontSize: 13, margin: "0 0 6px" }}>
                  {path.join(" → ")}
                </p>
                <p className="hint" style={{ margin: 0 }}>
                  {series.hops[step]} переходов, включая обе наземные линии ·{" "}
                  {km(series.length_km[step])} · шлюз {series.gateway[step]}
                </p>
              </>
            )}
          </div>
        </div>
      )}

      {series && series.gaps.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h2>Перерывы · {client}</h2>
          <div className="scroll-x" style={{ maxHeight: 220, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th className="num">Начало</th>
                  <th className="num">Длительность</th>
                  <th>Причина</th>
                </tr>
              </thead>
              <tbody>
                {series.gaps.map((gap) => (
                  <tr key={gap.start_s}>
                    <td className="num">{Math.round(gap.start_s / 60)} мин</td>
                    <td className="num">
                      {duration(gap.duration_s)}
                      {gap.at_horizon_edge && " *"}
                    </td>
                    <td>
                      <i className="swatch" style={{ background: CAUSE_RGB[gap.cause] }} />{" "}
                      {CAUSE_LABEL[gap.cause]}
                      {Object.keys(gap.causes).length > 1 && (
                        <span className="hint"> (смешанный)</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
