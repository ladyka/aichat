import type {
  ChatModelAdapter,
  ThreadAssistantMessagePart,
  ThreadMessage,
} from "@assistant-ui/react";
import { fetchSettings } from "@/lib/api";
import { notifyNoteChanged } from "@/lib/note-events";
import type { ResponseInfo } from "@/lib/response-info";

type Location = { lat: number; lon: number };

type OpenAIToolCall = {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
};

type OpenAIMessage = {
  role: string;
  content?: string | null;
  tool_calls?: OpenAIToolCall[];
  tool_call_id?: string;
};

type LocationRequestEvent = {
  type: "location_request";
  assistant_tool_call: OpenAIMessage;
};

type UsageInfo = NonNullable<ResponseInfo["usage"]>;

const LOCATION_KEY = "aichat_location_v1";
const LOCATION_TTL_MS = 2 * 60 * 60 * 1000;

function readLocation(): Location | null {
  try {
    const raw = localStorage.getItem(LOCATION_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (
      v &&
      typeof v.lat === "number" &&
      typeof v.lon === "number" &&
      Date.now() - v.ts < LOCATION_TTL_MS
    ) {
      return { lat: v.lat, lon: v.lon };
    }
  } catch {
    /* ignore */
  }
  return null;
}

function saveLocation(loc: Location): void {
  try {
    localStorage.setItem(LOCATION_KEY, JSON.stringify({ ...loc, ts: Date.now() }));
  } catch {
    /* ignore */
  }
}

function askGeolocation(): Promise<Location | string> {
  return new Promise((resolve) => {
    if (!("geolocation" in navigator)) {
      resolve("unsupported");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      (err) =>
        resolve(
          err.code === 1
            ? "permission_denied"
            : err.code === 2
              ? "position_unavailable"
              : "timeout",
        ),
      { timeout: 10000, maximumAge: 60000 },
    );
  });
}

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

function toOpenAIMessages(messages: readonly ThreadMessage[]): OpenAIMessage[] {
  return messages
    .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "system")
    .map((m) => ({
      role: m.role,
      content: textFromMessage(m),
    }));
}

type StreamState = { locationRequest: LocationRequestEvent | null };

type PostOptions = {
  model: string;
  conversationId: string | null;
  history: OpenAIMessage[];
  abortSignal: AbortSignal;
  state: StreamState;
  location?: Location | null;
};

function errorMessage(raw: unknown): string {
  if (typeof raw === "string") return raw;
  if (raw && typeof raw === "object" && "message" in raw) {
    return String((raw as { message: unknown }).message);
  }
  if (raw && typeof raw === "object") return JSON.stringify(raw);
  return String(raw);
}

function buildParts(
  reasoning: string,
  text: string,
): ThreadAssistantMessagePart[] {
  const content: ThreadAssistantMessagePart[] = [];
  if (reasoning) content.push({ type: "reasoning" as const, text: reasoning });
  if (text) content.push({ type: "text" as const, text });
  return content;
}

function parseUsage(raw: unknown): UsageInfo | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const u = raw as Record<string, unknown>;
  const usage: UsageInfo = {};
  if (typeof u.prompt_tokens === "number") usage.prompt_tokens = u.prompt_tokens;
  if (typeof u.completion_tokens === "number") {
    usage.completion_tokens = u.completion_tokens;
  }
  if (typeof u.total_tokens === "number") usage.total_tokens = u.total_tokens;
  if (typeof u.cost === "number") usage.cost = u.cost;
  return Object.keys(usage).length > 0 ? usage : undefined;
}

function buildResponseInfo(
  requestedModel: string,
  reportedModel: string | null,
  usage: UsageInfo | undefined,
): ResponseInfo | null {
  const model =
    reportedModel && reportedModel !== "default"
      ? reportedModel
      : requestedModel !== "default"
        ? requestedModel
        : reportedModel || undefined;
  if (!model && !usage) return null;
  return { ...(model ? { model } : {}), ...(usage ? { usage } : {}) };
}

function runResult(
  content: ThreadAssistantMessagePart[],
  info: ResponseInfo | null,
) {
  if (!info) return { content };
  return {
    content,
    metadata: {
      custom: { response: info },
    },
  };
}

async function* postAndStream(
  options: PostOptions,
): AsyncGenerator<ReturnType<typeof runResult>> {
  const { model, conversationId, location, history, abortSignal, state } = options;
  const res = await fetch("/api/chat", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    signal: abortSignal,
    body: JSON.stringify({
      model,
      stream: true,
      messages: history,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(location ? { location } : {}),
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
  let reasoning = "";
  let reportedModel: string | null = null;
  let usage: UsageInfo | undefined;
  let failed: Error | null = null;

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
      let obj: Record<string, unknown>;
      try {
        obj = JSON.parse(data);
      } catch {
        /* ignore partial JSON */
        continue;
      }
      if (obj?.type === "location_request") {
        state.locationRequest = obj as LocationRequestEvent;
        continue;
      }
      if (obj?.error != null) {
        failed = new Error(errorMessage(obj.error));
        continue;
      }
      if (typeof obj.model === "string" && obj.model) {
        reportedModel = obj.model;
      }
      const parsedUsage = parseUsage(obj.usage);
      if (parsedUsage) usage = parsedUsage;

      const delta = (obj?.choices as { delta?: Record<string, unknown> }[] | undefined)?.[0]
        ?.delta;
      if (!delta) {
        const info = buildResponseInfo(model, reportedModel, usage);
        if (info && (text || reasoning)) {
          yield runResult(buildParts(reasoning, text), info);
        }
        continue;
      }
      const contentDelta = delta.content;
      if (typeof contentDelta === "string" && contentDelta) {
        text += contentDelta;
      }
      const reasoningDelta =
        typeof delta.reasoning === "string"
          ? delta.reasoning
          : typeof delta.reasoning_content === "string"
            ? delta.reasoning_content
            : undefined;
      if (reasoningDelta) reasoning += reasoningDelta;
      if (text || reasoning) {
        yield runResult(
          buildParts(reasoning, text),
          buildResponseInfo(model, reportedModel, usage),
        );
      }
    }
  }

  if (failed) throw failed;
  const info = buildResponseInfo(model, reportedModel, usage);
  if (!text && !reasoning && !state.locationRequest) {
    yield runResult([{ type: "text" as const, text: "" }], info);
  } else if (info && (text || reasoning)) {
    // Финальный кадр с полным usage (часто приходит после последнего текста).
    yield runResult(buildParts(reasoning, text), info);
  }
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
      const state: StreamState = { locationRequest: null };

      const history = toOpenAIMessages(messages);
      let yielded = false;

      // First pass: standard request
      for await (const value of postAndStream({
        model,
        conversationId,
        history,
        abortSignal,
        state,
      })) {
        yielded = true;
        yield value;
      }

      if (!state.locationRequest) {
        if (!yielded) {
          yield { content: [{ type: "text" as const, text: "" }] };
        }
        notifyNoteChanged();
        return;
      }

      // The model asked for the user's location.
      // Try to use cached location first, then fall back to browser prompt.
      let coords = readLocation();
      if (!coords) {
        const res = await askGeolocation();
        if (typeof res === "object") {
          coords = res;
          saveLocation(coords);
        } else {
          // Use the error reason (e.g. 'permission_denied') as the tool result
          const toolCallId = state.locationRequest.assistant_tool_call.tool_calls?.[0]?.id;
          const nextHistory: OpenAIMessage[] = [
            ...history,
            state.locationRequest.assistant_tool_call,
            {
              role: "tool",
              tool_call_id: toolCallId,
              content: JSON.stringify({ error: "location_unavailable", reason: res }),
            },
          ];
          for await (const value of postAndStream({
            model,
            conversationId,
            history: nextHistory,
            abortSignal,
            state,
          })) {
            yielded = true;
            yield value;
          }
          if (!yielded) yield { content: [{ type: "text" as const, text: "" }] };
          notifyNoteChanged();
          return;
        }
      }

      // Use obtained coordinates to continue the conversation
      const assistant = state.locationRequest.assistant_tool_call;
      const toolCallId = assistant.tool_calls?.[0]?.id;
      const nextHistory: OpenAIMessage[] = [
        ...history,
        assistant,
        {
          role: "tool",
          tool_call_id: toolCallId,
          content: JSON.stringify(coords),
        },
      ];

      for await (const value of postAndStream({
        model,
        conversationId,
        location: coords,
        history: nextHistory,
        abortSignal,
        state,
      })) {
        yielded = true;
        yield value;
      }

      if (!yielded) {
        yield { content: [{ type: "text" as const, text: "" }] };
      }
      notifyNoteChanged();
    },
  };
}
