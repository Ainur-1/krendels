/**
 * What is wrong with the file that was just loaded, field by field.
 *
 * The case asks the service to say which data needs correcting after a bad upload,
 * and the API answers with every problem at once rather than the first. Showing
 * them as a list addressed by path is the whole point: a user with three mistakes
 * fixes three, instead of discovering them one reload at a time.
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
