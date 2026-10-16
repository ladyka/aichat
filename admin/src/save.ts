import type { AdminForm, AdminSaveBody, AdminSnapshot } from "./types";

export function formFromSnapshot(data: AdminSnapshot): AdminForm {
  const known = new Set(data.catalog_ids);
  return {
    useEnvDefault: data.use_env_default,
    defaultModel: data.effective_default_model || "default",
    useEnvAllowed: data.use_env_allowed,
    unrestricted: data.unrestricted && !data.use_env_allowed,
    allowed: data.selected_model_ids.filter((id) => known.has(id)),
  };
}

export function showAllowedList(form: Pick<AdminForm, "useEnvAllowed" | "unrestricted">): boolean {
  return !form.useEnvAllowed && !form.unrestricted;
}

export function buildSavePayload(form: AdminForm): AdminSaveBody {
  const hideList = !showAllowedList(form);
  return {
    use_env_default: form.useEnvDefault,
    use_env_allowed: form.useEnvAllowed,
    default_model: form.defaultModel,
    unrestricted: form.useEnvAllowed ? false : form.unrestricted,
    allowed_models: hideList ? [] : form.allowed,
  };
}

export function defaultModelOptions(catalogIds: string[], current: string): string[] {
  const options = ["default", ...catalogIds];
  if (current && !options.includes(current)) {
    options.push(current);
  }
  return options;
}
