import type { AdminSaveBody, AdminSnapshot } from "./types";

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) {
      return body.detail;
    }
  } catch {
    // Тело не JSON — ниже общий текст.
  }
  return "Не удалось выполнить запрос.";
}

async function read(response: Response): Promise<AdminSnapshot> {
  if (response.status === 401) {
    window.location.assign("/login?next=/admin");
    throw new Error("Требуется вход");
  }
  if (response.status === 403) {
    window.location.assign("/chat");
    throw new Error("Нет доступа к админке");
  }
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as AdminSnapshot;
}

export function loadAdminModels(): Promise<AdminSnapshot> {
  return fetch("/api/admin/models", { credentials: "same-origin" }).then(read);
}

export function saveAdminModels(body: AdminSaveBody): Promise<AdminSnapshot> {
  return fetch("/api/admin/models", {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(read);
}

export function resetAdminModels(): Promise<AdminSnapshot> {
  return fetch("/api/admin/models/reset", {
    method: "POST",
    credentials: "same-origin",
  }).then(read);
}
