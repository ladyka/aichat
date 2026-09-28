import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const srcDir = join(dirname(fileURLToPath(import.meta.url)), "..");

function readSrc(name: string): string {
  return readFileSync(join(srcDir, name), "utf8");
}

describe("мобильная оболочка чата", () => {
  it("не даёт странице скроллиться вместо ленты", () => {
    const css = readSrc("index.css");
    expect(css).toContain("overflow: hidden");
    expect(css).toContain("overscroll-behavior: none");
    expect(css).toMatch(/\.aui-thread-viewport[\s\S]*overscroll-behavior: contain/);
    expect(css).not.toMatch(/\.aui-thread-viewport\s*\{[^}]*height:\s*100%/);
  });

  it("на узком экране прячет подписи кнопок шапки", () => {
    const app = readSrc("App.tsx");
    expect(app).toContain('<span className="hidden md:inline">{label}</span>');
    expect(app).toContain("min-h-0 min-w-0 flex-1 flex-col overflow-hidden");
  });

  it("после выбора диалога закрывает выезжающий список на телефоне", () => {
    const app = readSrc("App.tsx");
    const list = readSrc("components/ThreadList.tsx");
    expect(app).toContain("onNavigate={closeMobileSidebar}");
    expect(app).toContain("if (!isMobileViewport()) return;");
    expect(list).toContain("onClick={onNavigate}");
  });
});
