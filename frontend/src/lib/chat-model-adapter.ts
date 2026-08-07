import type { ChatModelAdapter, ThreadMessage } from "@assistant-ui/react";
import { fetchSettings } from "@/lib/api";

function textFromMessage(message: ThreadMessage): string {
  if (message.role === "system") {
    const part = message.content[0];
    return part && "text" in part ? String(part.text ?? "") : "";
  }
  return message.content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

function toOpenAIMessages(messages: readonly ThreadMessage[]) {
  return messages
    .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "system")
    .map((m) => ({
      role: m.role,
      content: textFromMessage(m),
    }));
}

let cachedModel: string | null = null;
let cachedModelAt = 0;

async function preferredModel(): Promise<string> {
  const now = Date.now();
  if (cachedModel && now - cachedModelAt < 30_000) return cachedModel;
  try {
    const settings = await fetchSettings();
    cachedModel = settings.preferred_model || "default";
  } catch {
    cachedModel = "default";
  }
  cachedModelAt = now;
  return cachedModel;
}

export function invalidateModelCache() {
  cachedModel = null;
  cachedModelAt = 0;
}

export function createChatModelAdapter(
  getConversationId: () => string | null,
): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const model = await preferredModel();
      const conversationId = getConversationId();
      const res = await fetch("/api/chat", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        signal: abortSignal,
        body: JSON.stringify({
          model,
          stream: true,
          messages: toOpenAIMessages(messages),
          ...(conversationId ? { conversation_id: conversationId } : {}),
        }),
      });

      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const data = await res.json();
          detail = data.error || data.detail || detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }

      if (!res.body) {
        throw new Error("Empty response body");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let text = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith("data:")) continue;
          const data = trimmed.slice(5).trim();
          if (!data || data === "[DONE]") continue;
          try {
            const obj = JSON.parse(data);
            const delta = obj?.choices?.[0]?.delta?.content;
            if (typeof delta === "string" && delta) {
              text += delta;
              yield { content: [{ type: "text" as const, text }] };
            }
          } catch {
            /* ignore partial JSON */
          }
        }
      }

      if (!text) {
        yield { content: [{ type: "text" as const, text: "" }] };
      }
    },
  };
}
