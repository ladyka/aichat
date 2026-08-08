import { describe, it, expect, vi, beforeEach } from "vitest";
import { createChatModelAdapter } from "@/lib/chat-model-adapter";

describe("ChatModelAdapter Geolocation Flow", () => {
  const mockGetConversationId = () => "conv_123";

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // Mock fetch
    global.fetch = vi.fn();
    // Mock navigator.geolocation
    global.navigator.geolocation = {
      getCurrentPosition: vi.fn(),
    };
  });

  it("should lazy-request location when server returns location_request", async () => {
    const adapter = createChatModelAdapter(mockGetConversationId);

    // Mock 1st response: location_request
    const response1 = new Response(
      JSON.stringify({
        type: "location_request",
        assistant_tool_call: {
          role: "assistant",
          content: "",
          tool_calls: [{ id: "call_loc", type: "function", function: { name: "get_user_location", arguments: "{}" } }],
        },
      })
      .split("").map(c => `data: ${JSON.stringify({choices:[{delta:{content:''}}]})}\n\n`).join("") // Not exactly right but we mock the SSE
    );
    
    // Actually, simpler to mock the exact SSE stream the adapter expects
    const sse1 = new Response(
      `data: {"type": "location_request", "assistant_tool_call": {"role": "assistant", "content": "", "tool_calls": [{"id": "call_loc", "type": "function", "function": {"name": "get_user_location", "arguments": "{}"}}]}}\n\ndata: [DONE]\n\n`,
      { headers: { "Content-Type": "text/event-stream" } }
    );

    // Mock 2nd response: final weather answer
    const sse2 = new Response(
      `data: {"choices": [{"delta": {"content": "В Минске +15"}}]}\n\ndata: [DONE]\n\n`,
      { headers: { "Content-Type": "text/event-stream" } }
    );

    fetch
      .mockResolvedValueOnce(sse1)
      .mockResolvedValueOnce(sse2);

    // Mock geolocation success
    navigator.geolocation.getCurrentPosition.mockImplementationOnce((success) => 
      success({ coords: { latitude: 53.9, longitude: 27.5 } })
    );

    const messages = [{ role: "user", content: "Какая погода?" }] as any;
    const results = [];
    for await (const res of adapter.run({ messages, abortSignal: new AbortController().signal })) {
      results.push(res);
    }

    // Verify:
    // 1. Two fetches were made
    expect(fetch).toHaveBeenCalledTimes(2);
    // 2. Geolocation was requested
    expect(navigator.geolocation.getCurrentPosition).toHaveBeenCalled();
    // 3. Final answer is correct
    expect(results[results.length - 1].content[0].text).toContain("В Минске +15");
    // 4. Second fetch included the location in body
    const secondBody = JSON.parse(fetch.mock.calls[1][1].body);
    expect(secondBody.location).toEqual({ lat: 53.9, lon: 27.5 });
  });

  it("should handle geolocation permission denied", async () => {
    const adapter = createChatModelAdapter(mockGetConversationId);

    const sse1 = new Response(
      `data: {"type": "location_request", "assistant_tool_call": {"role": "assistant", "content": "", "tool_calls": [{"id": "call_loc", "type": "function", "function": {"name": "get_user_location", "arguments": "{}"}}]}}\n\ndata: [DONE]\n\n`,
      { headers: { "Content-Type": "text/event-stream" } }
    );
    const sse2 = new Response(
      `data: {"choices": [{"delta": {"content": "Пожалуйста, укажите город вручную"}}]}\n\ndata: [DONE]\n\n`,
      { headers: { "Content-Type": "text/event-stream" } }
    );

    fetch.mockResolvedValueOnce(sse1).mockResolvedValueOnce(sse2);

    // Mock geolocation denial
    navigator.geolocation.getCurrentPosition.mockImplementationOnce((success, error) => 
      error({ code: 1 }) // PERMISSION_DENIED
    );

    const messages = [{ role: "user", content: "Какая погода?" }] as any;
    const results = [];
    for await (const res of adapter.run({ messages, abortSignal: new AbortController().signal })) {
      results.push(res);
    }

    expect(results[results.length - 1].content[0].text).toContain("Укажите город");
    const secondBody = JSON.parse(fetch.mock.calls[1][1].body);
    // Should not include location coords
    expect(secondBody.location).toBeUndefined();
  });

  it("should use cached location without prompting browser", async () => {
    const adapter = createChatModelAdapter(mockGetConversationId);

    // Cache location manually
    localStorage.setItem("aichat_location_v1", JSON.stringify({ lat: 40.7, lon: -74.0, ts: Date.now() }));

    const sse1 = new Response(
      `data: {"type": "location_request", "assistant_tool_call": {"role": "assistant", "content": "", "tool_calls": [{"id": "call_loc", "type": "function", "function": {"name": "get_user_location", "arguments": "{}"}}]}}\n\ndata: [DONE]\n\n`,
      { headers: { "Content-Type": "text/event-stream" } }
    );
    const sse2 = new Response(
      `data: {"choices": [{"delta": {"content": "В Нью-Йорке +10"}}]}\n\ndata: [DONE]\n\n`,
      { headers: { "Content-Type": "text/event-stream" } }
    );

    fetch.mockResolvedValueOnce(sse1).mockResolvedValueOnce(sse2);

    const messages = [{ role: "user", content: "Погода?" }] as any;
    const results = [];
    for await (const res of adapter.run({ messages, abortSignal: new AbortController().signal })) {
      results.push(res);
    }

    // Geolocation API should NOT have been called
    expect(navigator.geolocation.getCurrentPosition).not.toHaveBeenCalled();
    expect(results[results.length - 1].content[0].text).toContain("В Нью-Йорке +10");
    
    const secondBody = JSON.parse(fetch.mock.calls[1][1].body);
    expect(secondBody.location).toEqual({ lat: 40.7, lon: -74.0 });
  });
});
