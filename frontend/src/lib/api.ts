export type ConversationSummary = {
  id: string;
  title: string;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
};

export type ConversationMessage = {
  id: string;
  role: string;
  content: string;
  created_at: string | null;
};

export type ConversationDetail = ConversationSummary & {
  messages: ConversationMessage[];
};

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.error === "string") return data.error;
    if (typeof data?.detail === "string") return data.detail;
  } catch {
    /* ignore */
  }
  return `HTTP ${res.status}`;
}

export async function fetchSettings(): Promise<{ preferred_model: string }> {
  const res = await fetch("/api/settings", { credentials: "include" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await fetch("/api/conversations", { credentials: "include" });
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return Array.isArray(data?.data) ? data.data : [];
}

export async function createConversation(
  title?: string,
): Promise<ConversationSummary> {
  const res = await fetch("/api/conversations", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? "Новый чат" }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getConversation(
  id: string,
): Promise<ConversationDetail> {
  const res = await fetch(`/api/conversations/${id}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function patchConversation(
  id: string,
  body: { title?: string; archived?: boolean },
): Promise<ConversationSummary> {
  const res = await fetch(`/api/conversations/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`/api/conversations/${id}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await parseError(res));
}

export async function appendMessages(
  id: string,
  messages: { role: string; content: string }[],
): Promise<void> {
  const res = await fetch(`/api/conversations/${id}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) throw new Error(await parseError(res));
}

export type ShareInfo = {
  shared: boolean;
  key?: string;
  created_at: string | null;
  expires_at: string | null;
};

export async function getShare(id: string): Promise<ShareInfo> {
  const res = await fetch(`/api/conversations/${id}/share`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createShare(id: string): Promise<ShareInfo> {
  const res = await fetch(`/api/conversations/${id}/share`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function revokeShare(id: string): Promise<void> {
  const res = await fetch(`/api/conversations/${id}/share/revoke`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(await parseError(res));
}
