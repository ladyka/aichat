/** Сведения об ответе модели, которые показывают в UI и кладут в Message.meta. */
export type ResponseInfo = {
  model?: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    cost?: number;
  };
};

export function isResponseInfo(value: unknown): value is ResponseInfo {
  if (!value || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  if (obj.model != null && typeof obj.model !== "string") return false;
  if (obj.usage != null && typeof obj.usage !== "object") return false;
  return true;
}

export function responseInfoFromCustom(
  custom: Record<string, unknown> | undefined | null,
): ResponseInfo | null {
  if (!custom) return null;
  const raw = custom.response;
  return isResponseInfo(raw) && (raw.model || raw.usage) ? raw : null;
}
