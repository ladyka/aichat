import { beforeEach, describe, expect, it, vi } from "vitest";
import { listSkills, putConversationSkills } from "@/lib/api";

describe("skills API client", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    global.fetch = fetchMock as never;
  });

  it("lists owned skills", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ data: [{ id: "1", title: "Налог" }] }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    const skills = await listSkills();
    expect(skills).toEqual([{ id: "1", title: "Налог" }]);
    expect(fetchMock).toHaveBeenCalledWith("/api/skills", {
      credentials: "include",
    });
  });

  it("saves conversation skill ids", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ skill_ids: [2, "3"] }), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    const ids = await putConversationSkills("42", ["2", "3"]);
    expect(ids).toEqual(["2", "3"]);
    expect(fetchMock).toHaveBeenCalledWith("/api/conversations/42/skills", {
      method: "PUT",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_ids: ["2", "3"] }),
    });
  });

  it("surfaces API errors", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "Слишком много навыков" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(putConversationSkills("1", ["1"])).rejects.toThrow(
      "Слишком много навыков",
    );
  });
});
