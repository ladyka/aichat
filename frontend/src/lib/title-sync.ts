import { useEffect, useRef } from "react";

import { useAui, useAuiState } from "@assistant-ui/react";

import { getConversation } from "@/lib/api";

/**
 * Сервер формулирует тему диалога фоном (после 1-го, 2-го и 5-го ответа
 * ассистента), поэтому заголовок в UI без поллинга устаревает до
 * перезагрузки страницы. После завершения прогона делаем несколько
 * контрольных опросов; если серверная тема отличается — переименовываем
 * чат (PATCH с тем же значением, title_locked не поднимается).
 */
export const TITLE_POLL_DELAYS_MS = [1500, 3500, 7000];

type AuiLike = {
  threadListItem: {
    getState: () => { title?: string | undefined };
    rename: (title: string) => unknown;
  };
};

/** Несколько контрольных опросов темы после конца прогона; применяем отличившуюся. */
export async function syncThreadTitle(
  aui: AuiLike,
  remoteId: string,
  delays: readonly number[] = TITLE_POLL_DELAYS_MS,
): Promise<boolean> {
  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
  for (const delay of delays) {
    await sleep(delay);
    try {
      const conv = await getConversation(remoteId);
      const current = (aui.threadListItem.getState().title ?? "").trim();
      const server = conv.title.trim();
      if (server && server !== current) {
        aui.threadListItem.rename(server);
        return true;
      }
    } catch {
      return false;
    }
  }
  return false;
}

export function useTitleSync(): void {
  const aui = useAui();
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const wasRunningRef = useRef(false);

  useEffect(() => {
    if (isRunning) {
      wasRunningRef.current = true;
      return;
    }
    if (!wasRunningRef.current || !remoteId) return;
    wasRunningRef.current = false;

    let cancelled = false;
    const run = (async () => {
      const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
      for (const delay of TITLE_POLL_DELAYS_MS) {
        await sleep(delay);
        if (cancelled) return;
        try {
          const conv = await getConversation(remoteId);
          const current = (aui.threadListItem.getState().title ?? "").trim();
          const server = conv.title.trim();
          if (server && server !== current) {
            aui.threadListItem.rename(server);
            return;
          }
        } catch {
          return;
        }
      }
    })();
    void run;

    return () => {
      cancelled = true;
    };
  }, [aui, isRunning, remoteId]);
}