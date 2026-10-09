import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExportedMessageRepositoryItem } from "@assistant-ui/react";
import { AichatHistoryAdapter, threadFromSummary } from "@/lib/thread-list-adapter";
import { listConversations } from "@/lib/api";

type Aui = Parameters<AichatHistoryAdapter["constructor"]>[0] extends () => infer R
  ? R
  : never;

function fakeAui(getRemoteId: () => string) {
  return {
    threadListItem: {
      getState: () => ({ remoteId: getRemoteId() }),
      initialize: async () => ({ remoteId: getRemoteId() }),
    },
  } as unknown as Aui;
}

function item(id: string, parentId: string | null = null) {
  return {
    parentId,
    message: { id, role: "assistant", content: [{ type: "text", text: "ответ" }] },
  } as unknown as ExportedMessageRepositoryItem;
}

function detailResponse(messages: { id: string; role: string; content: string }[]) {
  return new Response(
    JSON.stringify({ id: "7", title: "Чат", messages }),
    { headers: { "Content-Type": "application/json" } },
  );
}

function appendedResponse(id: string) {
  return new Response(JSON.stringify({ data: [{ id }] }), {
    headers: { "Content-Type": "application/json" },
  });
}

function deleteCall(remoteId: string, ids: string[]) {
  return [
    `/api/conversations/${remoteId}/messages`,
    {
      method: "DELETE",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    },
  ];
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  global.fetch = fetchMock as never;
});

describe("список чатов", () => {
  it("отличает архивный закреплённый чат от обычного", () => {
    const archived = threadFromSummary({
      id: "3",
      title: "Закреп",
      created_at: null,
      updated_at: "2026-10-09T12:00:00Z",
      archived_at: "2026-10-09T12:01:00Z",
      pinned_at: "2026-10-09T12:02:00Z",
    });
    expect(archived.status).toBe("archived");
    expect(archived.remoteId).toBe("3");
    expect(archived.custom).toEqual({ pinned: true });

    const live = threadFromSummary({
      id: "4",
      title: "Живой",
      created_at: null,
      updated_at: null,
      archived_at: null,
      pinned_at: null,
    });
    expect(live.status).toBe("regular");
    expect(live.custom).toEqual({ pinned: false });
    expect(live.lastMessageAt).toBeUndefined();
  });

  it("запрашивает живые чаты и архив одним списком", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ data: [] }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    await listConversations({ archived: "all" });
    expect(fetchMock).toHaveBeenCalledWith("/api/conversations?archived=all", {
      credentials: "include",
    });
  });
});

describe("история чата", () => {
  it("удаляет загруженное сообщение по его id в базе", async () => {
    fetchMock.mockResolvedValueOnce(
      detailResponse([
        { id: "12", role: "user", content: "вопрос" },
        { id: "13", role: "assistant", content: "ответ" },
      ]),
    );
    const adapter = new AichatHistoryAdapter(() => fakeAui(() => "7"));
    await adapter.load();

    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await adapter.delete([item("13", "12")]);

    expect(fetchMock).toHaveBeenLastCalledWith(...deleteCall("7", ["13"]));
  });

  it("удаляет только что отправленное сообщение по id, который вернул сервер", async () => {
    fetchMock.mockResolvedValueOnce(appendedResponse("42"));
    const adapter = new AichatHistoryAdapter(() => fakeAui(() => "7"));
    await adapter.append(item("клиентский-uuid"));

    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await adapter.delete([item("клиентский-uuid")]);

    expect(fetchMock).toHaveBeenLastCalledWith(...deleteCall("7", ["42"]));
  });

  it("не трогает сервер, если удалять нечего", async () => {
    const adapter = new AichatHistoryAdapter(() => fakeAui(() => "7"));
    await adapter.delete([item("чужой-uuid")]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("сохраняет сведения об ответе при дописывании сообщения", async () => {
    fetchMock.mockResolvedValueOnce(appendedResponse("55"));
    const adapter = new AichatHistoryAdapter(() => fakeAui(() => "7"));
    await adapter.append({
      parentId: null,
      message: {
        id: "клиентский",
        role: "assistant",
        createdAt: new Date(),
        content: [{ type: "text", text: "ответ" }],
        status: { type: "complete", reason: "stop" },
        metadata: {
          unstable_state: null,
          unstable_annotations: [],
          unstable_data: [],
          steps: [],
          custom: {
            response: {
              model: "google/gemini-2.0-flash-exp",
              usage: { total_tokens: 9 },
            },
          },
        },
      },
    } as unknown as ExportedMessageRepositoryItem);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.messages[0].meta).toEqual({
      model: "google/gemini-2.0-flash-exp",
      usage: { total_tokens: 9 },
    });
  });
});
