import { describe, expect, it } from "vitest";

import { buildSavePayload, defaultModelOptions, formFromSnapshot, showAllowedList } from "./save";
import type { AdminSnapshot } from "./types";

const snapshot: AdminSnapshot = {
  env_default_model: "default",
  env_allowed_models: null,
  use_env_default: false,
  use_env_allowed: false,
  effective_default_model: "openrouter/auto",
  unrestricted: false,
  selected_model_ids: ["openrouter/auto"],
  catalog_ids: ["openrouter/auto", "vendor/other"],
};

describe("admin form", () => {
  it("берёт выбранные модели из снимка", () => {
    const form = formFromSnapshot(snapshot);
    expect(form.defaultModel).toBe("openrouter/auto");
    expect(form.allowed).toEqual(["openrouter/auto"]);
    expect(showAllowedList(form)).toBe(true);
  });

  it("прячет список, когда разрешены все или значение из окружения", () => {
    expect(showAllowedList({ useEnvAllowed: true, unrestricted: false })).toBe(false);
    expect(showAllowedList({ useEnvAllowed: false, unrestricted: true })).toBe(false);
  });

  it("не отправляет список, если он скрыт", () => {
    const form = formFromSnapshot({
      ...snapshot,
      use_env_allowed: true,
      unrestricted: true,
    });
    const body = buildSavePayload(form);
    expect(body.use_env_allowed).toBe(true);
    expect(body.unrestricted).toBe(false);
    expect(body.allowed_models).toEqual([]);
  });

  it("добавляет текущую модель в список, если её нет в каталоге", () => {
    expect(defaultModelOptions(["openrouter/auto"], "ol/demo")).toEqual([
      "default",
      "openrouter/auto",
      "ol/demo",
    ]);
  });
});
