import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vitest";

import { Markdown } from "@/lib/markdown";

function html(markdown: string): string {
  return renderToStaticMarkup(<Markdown>{markdown}</Markdown>);
}

describe("ссылки в markdown чата", () => {
  it("внешняя ссылка открывается в новом окне без opener", () => {
    const out = html("[пример](https://example.com/page)");
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener noreferrer"');
    expect(out).toContain('href="https://example.com/page"');
    expect(out).toContain(">пример</a>");
  });

  it("mailto-ссылка тоже в новом окне", () => {
    const out = html("[почта](mailto:someone@example.com)");
    expect(out).toContain('target="_blank"');
  });

  it("автосвязанный URL тоже в новом окне", () => {
    const out = html("https://example.com/auto");
    expect(out).toContain('target="_blank"');
  });

  it("относительная ссылка остаётся в текущем окне", () => {
    const out = html("[внутри](/api/models)");
    expect(out).toContain('href="/api/models"');
    expect(out).not.toContain('target="_blank"');
  });
});