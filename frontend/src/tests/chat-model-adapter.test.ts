import { describe, it, expect, vi, beforeEach } from "vitest";
import { createChatModelAdapter, invalidateModelCache } from "@/lib/chat-model-adapter";

function sseResponse(...events: string[]): Response {
  return new Response(events.join("\n\n") + "\n\ndata: [DONE]\n\n", {
    headers: { "Content-Type": "text/event-stream" },
  });
}

function locationRequestSse(): Response {
  return sseResponse(
    `data: ${JSON.stringify({
      type: "location_request",
      assistant_tool_call: {
        role: "assistant",
        content: "",
        tool_calls: [
          {
            id: "call_loc",
            type: "function",
            function: { name: "get_user_location", arguments: "{}" },
          },
        ],
      },
    })}`,
  );
}

function textSse(text: string): Response {
  return sseResponse(`data: ${JSON.stringify({ choices: [{ delta: { content: text } }] })}`);
}

function settingsResponse(): Response {
  return new Response(JSON.stringify({ preferred_model: "default" }), {
    headers: { "Content-Type": "application/json" },
  });
}

const runOptions = (messages: unknown[]) => ({
  messages,
  abortSignal: new AbortController().signal,
} as never);

function userMessage(text: string): unknown {
  return {
    id: "msg_" + Math.random(),
    createdAt: new Date(),
    role: "user",
    content: [{ type: "text", text }],
    attachments: [],
    metadata: { custom: {} },
  };
}

async function collect(
  adapter: ReturnType<typeof createChatModelAdapter>,
  messages: unknown[],
): Promise<string[]> {
  const texts: string[] = [];
  for await (const res of adapter.run(runOptions(messages))) {
    texts.push(res.content[0].text);
  }
  return texts;
}

describe("ChatModelAdapter Geolocation Flow", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let geoMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    invalidateModelCache();
    localStorage.clear();

    fetchMock = vi.fn();
    global.fetch = fetchMock as never;

    geoMock = vi.fn();
    Object.defineProperty(navigator, "geolocation", {
      value: { getCurrentPosition: geoMock },
      configurable: true,
    });
  });

  it("should lazy-request location when server returns location_request", async () => {
    const adapter = createChatModelAdapter(() => "conv_123");

    fetchMock
      .mockResolvedValueOnce(settingsResponse())
      .mockResolvedValueOnce(locationRequestSse())
      .mockResolvedValueOnce(textSse("В Минске +15"));

    geoMock.mockImplementationOnce((success: (pos: { coords: { latitude: number; longitude: number } }) => void) =>
      success({ coords: { latitude: 53.9, longitude: 27.5 } }),
    );

    const texts = await collect(adapter, [userMessage("Какая погода?")]);

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(geoMock).toHaveBeenCalledTimes(1);
    expect(texts[texts.length - 1]).toContain("В Минске +15");

    const secondBody = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(secondBody.location).toEqual({ lat: 53.9, lon: 27.5 });
  });

  it("should handle geolocation permission denied", async () => {
    const adapter = createChatModelAdapter(() => "conv_123");

    fetchMock
      .mockResolvedValueOnce(settingsResponse())
      .mockResolvedValueOnce(locationRequestSse())
      .mockResolvedValueOnce(textSse("Пожалуйста, укажите город вручную"));

    geoMock.mockImplementationOnce(
      (_success: unknown, error: (err: { code: number }) => void) => error({ code: 1 }),
    );

    const texts = await collect(adapter, [userMessage("Какая погода?")]);

    expect(texts[texts.length - 1]).toContain("укажите город");

    const secondBody = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(secondBody.location).toBeUndefined();
  });

  it("should use cached location without prompting browser", async () => {
    const adapter = createChatModelAdapter(() => "conv_123");

    localStorage.setItem(
      "aichat_location_v1",
      JSON.stringify({ lat: 40.7, lon: -74.0, ts: Date.now() }),
    );

    fetchMock
      .mockResolvedValueOnce(settingsResponse())
      .mockResolvedValueOnce(locationRequestSse())
      .mockResolvedValueOnce(textSse("В Нью-Йорке +10"));

    const texts = await collect(adapter, [userMessage("Погода?")]);

    expect(geoMock).not.toHaveBeenCalled();
    expect(texts[texts.length - 1]).toContain("В Нью-Йорке +10");

    const secondBody = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(secondBody.location).toEqual({ lat: 40.7, lon: -74.0 });
  });
});

describe("ChatModelAdapter reasoning and errors", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    invalidateModelCache();
    localStorage.clear();

    fetchMock = vi.fn();
    global.fetch = fetchMock as never;
  });

  const partsOf = async (
    adapter: ReturnType<typeof createChatModelAdapter>,
    messages: unknown[],
  ): Promise<{ type: string; text: string }[][]> => {
    const parts: { type: string; text: string }[][] = [];
    for await (const res of adapter.run(runOptions(messages))) {
      parts.push(res.content as never);
    }
    return parts;
  };

  it("should stream reasoning parts alongside text", async () => {
    const adapter = createChatModelAdapter(() => "conv_123");
    fetchMock
      .mockResolvedValueOnce(settingsResponse())
      .mockResolvedValueOnce(
        sseResponse(
          `data: ${JSON.stringify({ choices: [{ delta: { reasoning: "Сначала подумаю…" } }] })}`,
          `data: ${JSON.stringify({ choices: [{ delta: { content: "Вот ответ" } }] })}`,
        ),
      );

    const parts = await partsOf(adapter, [userMessage("hi")]);
    const last = parts[parts.length - 1];

    expect(last).toEqual([
      { type: "reasoning", text: "Сначала подумаю…" },
      { type: "text", text: "Вот ответ" },
    ]);
  });

  it("should surface stream errors to the chat", async () => {
    const adapter = createChatModelAdapter(() => "conv_123");
    fetchMock
      .mockResolvedValueOnce(settingsResponse())
      .mockResolvedValueOnce(
        sseResponse(`data: ${JSON.stringify({ error: { message: "upstream boom" } })}`),
      );

    await expect(collect(adapter, [userMessage("hi")])).rejects.toThrow(
      "upstream boom",
    );
  });

  it("should surface HTTP errors to the chat", async () => {
    const adapter = createChatModelAdapter(() => "conv_123");
    fetchMock
      .mockResolvedValueOnce(settingsResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: "Дневной лимит исчерпан" }), {
          status: 429,
        }),
      );

    await expect(collect(adapter, [userMessage("hi")])).rejects.toThrow(
      "Дневной лимит исчерпан",
    );
  });
});
