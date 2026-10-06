import { defineConfig, type Plugin } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * Страница шаринга не грузит index.css (там оболочка чата и Tailwind).
 * Стили `.md` лежат в `src/markdown.css` и в чат попадают через `@import`
 * в index.css. Сюда же кладём отдельный файл с тем же именем, без хеша.
 */
function copyMarkdownCss(): Plugin {
  return {
    name: "copy-markdown-css",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "assets/markdown.css",
        source: fs.readFileSync(path.resolve(rootDir, "src/markdown.css")),
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [
    react(),
    tailwindcss(),
    copyMarkdownCss(),
    // Service worker чата: `src/sw.ts` → `dist/sw.js`, отдаётся бэкендом из корня
    // как `/sw.js` (scope `/`, иначе `/chat` вне зоны контроля SW).
    // Манифест — версионируемый `public/manifest.webmanifest`, плагин его не генерит.
    // В тестовом режиме плагин выключен, в dev его нет: регистрация только в PROD.
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectRegister: false,
      manifest: false,
      disable: mode === "test",
      injectManifest: {
        globPatterns: ["index.html", "assets/*.{js,css}", "*.png"],
        // Ассеты сборки отдаются из-под `/chat-ui/`, а SW живёт в корне: без этого
        // префикса Workbox разрешал бы относительные URL от `/` и ассеты дали бы 404.
        modifyURLPrefix: { "": "/chat-ui/" },
        // Имена ассетов здесь намеренно без content-hash (`assets/chat.js`), поэтому
        // revision-хеши Workbox обязательны: с `revision: null` precache навсегда
        // залип бы на первой версии бандла.
        dontCacheBustURLsMatching: /^$/,
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
      },
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(rootDir, "./src"),
    },
  },
  base: "/chat-ui/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsDir: "assets",
    rollupOptions: {
      input: {
        // Чат: index.html → assets/chat.js (имя зафиксировано в templates/chat.html).
        index: path.resolve(rootDir, "index.html"),
        // Страница шаринга: тот же Markdown, отдельная точка входа без оболочки чата.
        share: path.resolve(rootDir, "src/share.tsx"),
      },
      output: {
        entryFileNames: (chunkInfo) =>
          chunkInfo.name === "index" ? "assets/chat.js" : "assets/[name].js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/v1": "http://127.0.0.1:8080",
      "/login": "http://127.0.0.1:8080",
      "/logout": "http://127.0.0.1:8080",
      "/settings": "http://127.0.0.1:8080",
      "/tokens": "http://127.0.0.1:8080",
      "/static": "http://127.0.0.1:8080",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
}));
