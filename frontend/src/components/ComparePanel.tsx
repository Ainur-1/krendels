/**
 * Two or more runs side by side, and the parameters that differ between them.
 *
 * The table of numbers is the easy half. The harder and more useful half is the
 * list of changes underneath it: a run computed an hour ago differs from the
 * current one by some set of edits nobody wrote down, and a comparison that only
 * shows outcomes leaves the reader guessing which edit produced them. The service
 * diffs the two scenarios and names every field that moved.
 */

import { useState } from "react";

import { api } from "../api";
import type { SavedRun } from "../App";
import { decimal, duration, km, percent } from "../lib/format";
import type { Comparison } from "../types";

export function ComparePanel({
  saved,
  currentRunId,
  clients,
  onSaveCurrent,
  onError,
}: {
  saved: SavedRun[];
  currentRunId: string | null;
  clients: string[];
  onSaveCurrent: (label: string) => void;
  onError: (message: string) => void;
}) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [table, setTable] = useState<Comparison | null>(null);
  const [label, setLabel] = useState("");

  const selectable = saved;

  function toggle(runId: string) {
    setChosen((current) =>
      current.includes(runId)
        ? current.filter((id) => id !== runId)
        : current.length < 4
          ? [...current, runId]
          : current,
    );
  }

  async function run() {
    if (chosen.length < 2) return;
    try {
      const labels = chosen.map(
        (id) => selectable.find((entry) => entry.runId === id)?.label ?? id,
      );
      setTable(await api.compare(chosen, labels));
    } catch {
      onError("Сравнение не выполнено: возможно, расчёт устарел — запустите заново");
    }
  }

  return (
    <>
      <p className="hint" style={{ marginTop: 0 }}>
        Каждый запуск расчёта попадает в этот список. Отметьте два и более, чтобы
        сопоставить их на одном периоде.
      </p>

      <div className="row" style={{ marginBottom: 10 }}>
        <input
          placeholder="назвать текущий расчёт и запустить заново"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
        <button
          disabled={!label.trim()}
          onClick={() => {
            onSaveCurrent(label.trim());
            setLabel("");
          }}
        >
          Запустить и сохранить
        </button>
      </div>

      {selectable.length === 0 ? (
        <p className="empty">Ещё нет ни одного расчёта.</p>
      ) : (
        <div style={{ marginBottom: 10 }}>
          {selectable.map((entry) => (
            <label
              key={entry.runId}
              className="row"
              style={{ cursor: "pointer", padding: "2px 0" }}
            >
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={chosen.includes(entry.runId)}
                onChange={() => toggle(entry.runId)}
              />
              <span style={{ flex: 1 }}>
                {entry.label}
                {entry.runId === currentRunId && <span className="hint"> · текущий</span>}
              </span>
              <span className="hint" style={{ fontFamily: "var(--mono)" }}>
                {percent(entry.worstAvailability)}
              </span>
            </label>
          ))}
        </div>
      )}

      <button className="primary" disabled={chosen.length < 2} onClick={() => void run()}>
        Сравнить
      </button>

      {table && !table.comparable && (
        <p className="errors" style={{ marginTop: 12 }}>
          У выбранных расчётов разная сетка времени, сопоставлять их нельзя:{" "}
          {table.grids.map(([horizon, step]) => `${horizon} с / ${step} с`).join(", ")}.
        </p>
      )}

      {table && table.comparable && (
        <div style={{ marginTop: 14 }}>
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Показатель</th>
                  {table.runs.map((entry) => (
                    <th key={entry.label} className="num">
                      {entry.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Худший пункт</td>
                  {table.runs.map((entry) => (
                    <td key={entry.label} className="num">
                      {percent(entry.worst_availability)}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td>Самый долгий перерыв</td>
                  {table.runs.map((entry) => (
                    <td key={entry.label} className="num">
                      {duration(entry.worst_max_gap_s)}
                    </td>
                  ))}
                </tr>
                <tr>
                  <td>Цель</td>
                  {table.runs.map((entry) => (
                    <td key={entry.label} className="num">
                      <span className={entry.meets_target ? "pill ok" : "pill bad"}>
                        {entry.meets_target ? "да" : "нет"}
                      </span>
                    </td>
                  ))}
                </tr>

                {clients.length > 0 && (
                  <tr>
                    <td colSpan={table.runs.length + 1} style={{ paddingTop: 12 }}>
                      <strong style={{ fontSize: 12, color: "var(--text-faint)" }}>
                        ПО ПУНКТАМ
                      </strong>
                    </td>
                  </tr>
                )}

                {table.clients.map((row) => (
                  <tr key={row.client_id}>
                    <td>{row.client_id}</td>
                    {row.cells.map((cell, index) => (
                      <td key={index} className="num">
                        {cell ? (
                          <>
                            {percent(cell.availability_share)}
                            <span className="hint">
                              {" "}
                              · {duration(cell.max_gap_s)} · {decimal(cell.mean_hops)} хоп ·{" "}
                              {km(cell.mean_route_length_km)}
                            </span>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2 style={{ marginTop: 16 }}>Что изменилось в конфигурации</h2>
          {table.changes.length === 0 ? (
            <p className="empty">
              Параметры совпадают — различия в результатах объясняются не конфигурацией.
            </p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Поле</th>
                  <th className="num">Было</th>
                  <th className="num">Стало</th>
                </tr>
              </thead>
              <tbody>
                {table.changes.slice(0, 40).map((change) => (
                  <tr key={change.path}>
                    <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{change.path}</td>
                    <td className="num">{render(change.before)}</td>
                    <td className="num">{render(change.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {table.changes.length > 40 && (
            <p className="hint">…и ещё {table.changes.length - 40}.</p>
          )}
        </div>
      )}
    </>
  );
}

function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return String(value);
  if (typeof value === "object") return "объект";
  return String(value);
}
