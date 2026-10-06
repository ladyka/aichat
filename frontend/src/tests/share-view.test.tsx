import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vitest";

import { ShareMessages, type ShareMessage } from "@/share";

function html(messages: ShareMessage[]): string {
  return renderToStaticMarkup(<ShareMessages messages={messages} />);
}

describe("рендер шара (/s/<key>)", () => {
  it("markdown ответа рендерится: заголовки, таблица, списки", () => {
    const out = html([
      {
        role: "assistant",
        content: "## План\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- пункт",
      },
    ]);
    expect(out).toContain("<h2>");
    expect(out).toContain("<table>");
    expect(out).toContain("<li>");
  });

  it("ответ бота обёрнут в .md и подписан «Чатбот»", () => {
    const out = html([{ role: "assistant", content: "Ответ" }]);
    expect(out).toContain('class="md"');
    expect(out).toContain("Чатбот");
  });

  it("вопрос пользователя — обычный текст без подписи", () => {
    const out = html([
      { role: "user", content: "Вопрос\nсо строками" },
      { role: "assistant", content: "Ответ" },
    ]);
    const userBubble = out.split('<div class="bubble')[1] ?? "";
    expect(userBubble).toContain('class="share-user-text"');
    expect(userBubble).not.toContain("share-name");
    // текст пользователя не прогоняется через markdown
    expect(userBubble).not.toContain("<div");
  });

  it("ссылки бота безопасные — как в чате", () => {
    const out = html([
      { role: "assistant", content: "[пример](https://example.com/page)" },
    ]);
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener noreferrer"');
  });

  it("инъекции в markdown не выполняются", () => {
    const out = html([
      { role: "assistant", content: '<img src=x onerror="alert(1)">' },
    ]);
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img");
  });
});