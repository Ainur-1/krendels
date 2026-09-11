/**
 * Проект в виде форм: какой сценарий, насколько развёрнут, куда смотрят плоскости, что сломано.
 *
 * Ничто здесь не привязано к выданным данным. Плоскости рисуются из сценария,
 * аппараты для редактора отказов берутся из сценария, а очереди запуска предлагаются
 * те, которые в нём действительно встречаются. Файл жюри с четырьмя плоскостями и
 * очередями поперёк них получит четыре строки и правильные очереди без всякой особой
 * обработки.
 *
 * Углы задаются на полуоткрытом промежутке до 360°: эталонная проверка отвергает 360,
 * поэтому поля останавливаются на 359.9, а не дают пользователю сделать файл, который
 * инструмент организаторов забракует.
 */

import { useMemo, useRef, useState } from "react";

import { planeColour } from "../lib/format";
import type { Scenario, ScenarioSummary } from "../types";

interface Props {
  catalogue: ScenarioSummary[];
  scenario: Scenario | null;
  changed: boolean;
  onOpenBundled: (name: string) => void;
  onUpload: (text: string, filename: string) => void;
  onEdit: (scenario: Scenario) => void;
  onRevert: () => void;
}

export function ConfigPanel({
  catalogue,
  scenario,
  changed,
  onOpenBundled,
  onUpload,
  onEdit,
  onRevert,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [outageSatellite, setOutageSatellite] = useState("");
  const [outageFrom, setOutageFrom] = useState("21600");
  const [outageTo, setOutageTo] = useState("86400");

  const stages = useMemo(() => {
    if (!scenario) return [] as number[];
    return [...new Set(scenario.design.satellites.map((s) => s.launch_batch))].sort();
  }, [scenario]);

  const inService = useMemo(() => {
    if (!scenario) return 0;
    return scenario.design.satellites.filter(
      (s) => s.launch_batch <= scenario.design.launch_stage,
    ).length;
  }, [scenario]);

  const planeIds = scenario?.design.planes.map((p) => p.id) ?? [];

  function patch(change: (draft: Scenario) => void) {
    if (!scenario) return;
    const draft = structuredClone(scenario);
    change(draft);
    onEdit(draft);
  }

  return (
    <>
      <section className="panel">
        <h2>Сценарий</h2>
        <div className="field">
          <select
            value=""
            onChange={(event) => event.target.value && onOpenBundled(event.target.value)}
          >
            <option value="">выбрать из выданных…</option>
            {catalogue.map((item) => (
              <option key={item.source} value={item.source}>
                {item.title || item.id}
              </option>
            ))}
          </select>
        </div>
        <div className="row">
          <button style={{ flex: 1 }} onClick={() => fileInput.current?.click()}>
            Загрузить файл
          </button>
          <button disabled={!changed} onClick={onRevert}>
            Сбросить
          </button>
        </div>
        <input
          ref={fileInput}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            onUpload(await file.text(), file.name);
            event.target.value = "";
          }}
        />
        {scenario && (
          <p className="hint" style={{ marginBottom: 0 }}>
            {scenario.design.planes.length}{" "}
            {plural(scenario.design.planes.length, "плоскость", "плоскости", "плоскостей")},{" "}
            {scenario.design.satellites.length} аппаратов, {scenario.ground_sites.length}{" "}
            наземных пунктов. Шаг {scenario.environment.step_s} с, горизонт{" "}
            {(scenario.environment.horizon_s / 3600).toFixed(0)} ч.
          </p>
        )}
      </section>

      {scenario && (
        <>
          <section className="panel">
            <h2>Очередь развёртывания</h2>
            <div className="row">
              {stages.map((stage) => (
                <button
                  key={stage}
                  style={{ flex: 1 }}
                  className={scenario.design.launch_stage === stage ? "primary" : ""}
                  onClick={() =>
                    patch((draft) => {
                      draft.design.launch_stage = stage as 1 | 2 | 3;
                    })
                  }
                >
                  {stage}
                </button>
              ))}
            </div>
            <p className="hint" style={{ marginBottom: 0 }}>
              В расчёте участвуют {inService} из {scenario.design.satellites.length} аппаратов.
            </p>
          </section>

          <section className="panel">
            <h2>Орбитальные плоскости</h2>
            {scenario.design.planes.map((plane, index) => (
              <div key={plane.id} style={{ marginBottom: 10 }}>
                <div className="row" style={{ marginBottom: 4 }}>
                  <span
                    className="swatch"
                    style={{ background: planeColour(planeIds, plane.id) }}
                  />
                  <strong style={{ fontSize: 13 }}>{plane.id}</strong>
                  <span className="spacer" style={{ flex: 1 }} />
                  <span className="hint">
                    {
                      scenario.design.satellites.filter((s) => s.plane_id === plane.id).length
                    }{" "}
                    ап.
                  </span>
                </div>
                <AngleField
                  label="RAAN"
                  value={plane.raan_deg}
                  onChange={(value) =>
                    patch((draft) => {
                      draft.design.planes[index].raan_deg = value;
                    })
                  }
                />
                <AngleField
                  label="Фазирование"
                  value={plane.phase_deg}
                  onChange={(value) =>
                    patch((draft) => {
                      draft.design.planes[index].phase_deg = value;
                    })
                  }
                />
              </div>
            ))}
          </section>

          <section className="panel">
            <h2>Периоды недоступности</h2>
            {scenario.failures.length === 0 && (
              <p className="empty">Отказы не заданы.</p>
            )}
            {scenario.failures.map((failure, index) => (
              <div className="row" key={`${failure.satellite_id}-${index}`}>
                <span style={{ flex: 1, fontFamily: "var(--mono)", fontSize: 12 }}>
                  {failure.satellite_id}
                </span>
                <span className="hint">
                  {Math.round(failure.start_s / 60)}–{Math.round(failure.end_s / 60)} мин
                </span>
                <button
                  className="ghost"
                  title="Убрать"
                  onClick={() =>
                    patch((draft) => {
                      draft.failures.splice(index, 1);
                    })
                  }
                >
                  ✕
                </button>
              </div>
            ))}

            <div style={{ borderTop: "1px solid var(--line)", marginTop: 8, paddingTop: 8 }}>
              <div className="field">
                <label htmlFor="outage-sat">Добавить отказ</label>
                <select
                  id="outage-sat"
                  value={outageSatellite}
                  onChange={(event) => setOutageSatellite(event.target.value)}
                >
                  <option value="">аппарат…</option>
                  {scenario.design.satellites.map((satellite) => (
                    <option key={satellite.id} value={satellite.id}>
                      {satellite.id} · {satellite.plane_id}
                    </option>
                  ))}
                </select>
              </div>
              <div className="row">
                <input
                  aria-label="начало, с"
                  value={outageFrom}
                  onChange={(event) => setOutageFrom(event.target.value)}
                />
                <input
                  aria-label="конец, с"
                  value={outageTo}
                  onChange={(event) => setOutageTo(event.target.value)}
                />
                <button
                  disabled={!outageSatellite}
                  onClick={() => {
                    const start = Number(outageFrom);
                    const end = Number(outageTo);
                    if (!Number.isFinite(start) || !Number.isFinite(end)) return;
                    patch((draft) => {
                      draft.failures.push({
                        satellite_id: outageSatellite,
                        start_s: start,
                        end_s: end,
                      });
                    });
                    setOutageSatellite("");
                  }}
                >
                  +
                </button>
              </div>
              <p className="hint">
                Секунды от начала расчёта, конец не включается. Горизонт{" "}
                {scenario.environment.horizon_s} с.
              </p>
            </div>
          </section>
        </>
      )}
    </>
  );
}

/**
 * Ползунок и число, идущие синхронно, в границах [0, 360).
 *
 * Поле ввода делает конфигурацию воспроизводимой: набранные ровно 65 — это не то же
 * самое, что 65, пойманные перетаскиванием. А ползунок делает действие RAAN видимым,
 * пока смотришь на карту.
 */
function AngleField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="row" style={{ marginBottom: 4 }}>
      <label className="hint" style={{ width: 84, flexShrink: 0 }}>
        {label}
      </label>
      <input
        type="range"
        min={0}
        max={359.9}
        step={0.1}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <input
        style={{ width: 68, flexShrink: 0, fontFamily: "var(--mono)", fontSize: 12 }}
        value={value}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isFinite(next)) onChange(Math.min(Math.max(next, 0), 359.9));
        }}
      />
    </div>
  );
}

function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
