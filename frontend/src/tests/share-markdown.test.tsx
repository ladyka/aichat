import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { act } from "react";

import { afterEach, describe, expect, it } from "vitest";

import { mountShareMarkdown } from "@/lib/mount-share-markdown";

const srcDir = join(dirname(fileURLToPath(import.meta.url)), "..");

afterEach(() => {
  document.body.innerHTML = "";
});

describe("markdown на странице шаринга", () => {
  it("рисует ответ тем же рендером, что и чат", () => {
    document.body.innerHTML = `<div class="share-md">${[
      "| Пицца | Цена |",
      "| --- | --- |",
      "| Пепперони | 28,9 |",
      "",
      "[меню](https://example.com/menu)",
    ].join("\n")}</div>`;

    act(() => {
      mountShareMarkdown();
    });

    const table = document.querySelector("table");
    expect(table).not.toBeNull();
    expect(table?.textContent).toContain("Пепперони");
    expect(table?.textContent).toContain("28,9");
    expect(document.querySelector(".md")).not.toBeNull();

    const link = document.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://example.com/menu");
    expect(link?.getAttribute("target")).toBe("_blank");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("не вставляет сырой html из текста ответа", () => {
    const el = document.createElement("div");
    el.className = "share-md";
    el.textContent = "<script>alert(1)</script>\n\n**жирный**";
    document.body.append(el);

    act(() => {
      mountShareMarkdown();
    });

    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("strong")?.textContent).toBe("жирный");
  });

  it("не монтирует один и тот же ответ дважды", () => {
    document.body.innerHTML = '<div class="share-md">**жирный**</div>';

    act(() => {
      mountShareMarkdown();
      mountShareMarkdown();
    });

    expect(document.querySelectorAll(".md")).toHaveLength(1);
  });

  it("чат подключает тот же файл стилей", () => {
    const css = readFileSync(join(srcDir, "index.css"), "utf8");
    const markdown = readFileSync(join(srcDir, "markdown.css"), "utf8");
    expect(css).toContain('@import "./markdown.css"');
    expect(markdown).toContain(".md table");
  });
});
