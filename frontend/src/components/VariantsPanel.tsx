/**
 * Saved designs: keep the one on screen, or go back to one kept earlier.
 *
 * Variants live in the service's database rather than in this tab, so a design
 * saved before lunch is still there after a redeploy. The case asks for exactly
 * that — save a variant and return to it for comparison — and a browser tab is the
 * wrong place to promise it from.
 */

import { useCallback, useEffect, useState } from "react";

import { api, type DesignRef } from "../api";
import type { Scenario, VariantListing } from "../types";

export function VariantsPanel({
  design,
  onLoad,
  onError,
}: {
  design: DesignRef | null;
  onLoad: (scenario: Scenario, label: string) => void;
  onError: (message: string) => void;
}) {
  const [variants, setVariants] = useState<VariantListing[]>([]);
  const [label, setLabel] = useState("");

  const refresh = useCallback(() => {
    api
      .variants()
      .then(setVariants)
      .catch(() => undefined);
  }, []);

  useEffect(refresh, [refresh]);

  async function save() {
    if (!design || !label.trim()) return;
    try {
      await api.saveVariant(label.trim(), design);
      setLabel("");
      refresh();
    } catch {
      onError("Не удалось сохранить вариант");
    }
  }

  async function open(variant: VariantListing) {
    try {
      const loaded = await api.variant(variant.id);
      onLoad(loaded.scenario, loaded.label);
    } catch {
      onError("Не удалось открыть вариант");
    }
  }

  async function remove(variant: VariantListing) {
    try {
      await api.deleteVariant(variant.id);
      refresh();
    } catch {
      onError("Не удалось удалить вариант");
    }
  }

  return (
    <section className="panel">
      <h2>Сохранённые варианты</h2>

      <div className="row" style={{ marginBottom: 8 }}>
        <input
          placeholder="название варианта"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && void save()}
        />
        <button disabled={!design || !label.trim()} onClick={() => void save()}>
          Сохранить
        </button>
      </div>

      {variants.length === 0 ? (
        <p className="empty">Пока ничего не сохранено.</p>
      ) : (
        variants.map((variant) => (
          <div className="row" key={variant.id} style={{ marginBottom: 4 }}>
            <button
              className="ghost"
              style={{ flex: 1, textAlign: "left", overflow: "hidden" }}
              onClick={() => void open(variant)}
              title={`очередь ${variant.launch_stage}, ${variant.satellites} аппаратов`}
            >
              {variant.label}
            </button>
            <button className="ghost" title="Удалить" onClick={() => void remove(variant)}>
              ✕
            </button>
          </div>
        ))
      )}
    </section>
  );
}
