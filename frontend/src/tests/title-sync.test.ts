import { beforeEach, describe, expect, it, vi } from "vitest";

import { TITLE_POLL_DELAYS_MS, syncThreadTitle } from "@/lib/title-sync";

vi.mock("@/lib/api", () => ({ getConversation: vi.fn() }));

import { getConversation } from "@/lib/api";

const getConv = vi.mocked(getConversation);
const FAST = [1, 1, 1];

beforeEach(() => {
  getConv.mockReset();
});

function aui(title?: string) {
  const rename = vi.fn();
  return {
    aui: {
      threadListItem: {
        getState: () => ({ title }),
        rename,
      },
    } as never,
    rename,
  };
}

describe("синхронизация авто-темы диалога", () => {
  it("серверная тема отличается — чат переименован на первом опросе", async () => {
    const { aui: client, rename } = aui("Новый чат");
    getConv.mockResolvedValueOnce({ title: "Курсы валют" } as never);
    const renamed = await syncThreadTitle(client, "42", FAST);
    expect(renamed).toBe(true);
    expect(rename).toHaveBeenCalledWith("Курсы валют");
    expect(getConv).toHaveBeenCalledWith("42");
  });

  it("тема совпадает — опрос продолжается до контрольных точек", async () => {
    const { aui: client, rename } = aui("Курсы валют");
    getConv.mockResolvedValue({ title: "Курсы валют" } as never);
    const renamed = await syncThreadTitle(client, "42", FAST);
    expect(renamed).toBe(false);
    expect(rename).not.toHaveBeenCalled();
    expect(getConv).toHaveBeenCalledTimes(TITLE_POLL_DELAYS_MS.length);
  });

  it("ошибка сети — тихий выход без rename", async () => {
    const { aui: client, rename } = aui();
    getConv.mockRejectedValueOnce(new Error("offline"));
    const renamed = await syncThreadTitle(client, "42", FAST);
    expect(renamed).toBe(false);
    expect(rename).not.toHaveBeenCalled();
  });

  it("сервер прислал пустую тему — переименования нет", async () => {
    const { aui: client, rename } = aui("Старое");
    getConv.mockResolvedValueOnce({ title: "  " } as never);
    const renamed = await syncThreadTitle(client, "42", FAST);
    expect(renamed).toBe(false);
    expect(rename).not.toHaveBeenCalled();
  });
});