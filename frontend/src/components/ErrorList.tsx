/**
 * Что не так с только что загруженным файлом, поле за полем.
 *
 * Кейс требует, чтобы после неудачной загрузки сервис сказал, какие данные надо
 * исправить, и API отвечает всеми проблемами сразу, а не первой. Смысл в том, чтобы
 * показать их списком с адресами: пользователь с тремя ошибками исправляет три, а не
 * обнаруживает их по одной за перезагрузку.
 */

import { describeError } from "../api";
import type { FieldError } from "../types";

export function ErrorList({
  errors,
  onDismiss,
}: {
  errors: FieldError[];
  onDismiss: () => void;
}) {
  return (
    <div className="errors">
      <div className="row">
        <h3>
          Сценарий не принят: {errors.length}{" "}
          {errors.length === 1 ? "замечание" : "замечаний"}
        </h3>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="ghost" onClick={onDismiss}>
          Скрыть
        </button>
      </div>
      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {errors.map((error, index) => (
          <li key={`${error.field}-${index}`}>
            {error.field && <code>{error.field}</code>} {describeError(error)}
          </li>
        ))}
      </ul>
    </div>
  );
}
