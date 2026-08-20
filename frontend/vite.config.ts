import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.dirname(fileURLToPath(import.meta.url));
const apiOrigin = process.env.AICHAT_API_ORIGIN ?? "http://127.0.0.1:20000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
      output: {
        entryFileNames: "assets/chat.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": apiOrigin,
      "/v1": apiOrigin,
      "/login": apiOrigin,
      "/logout": apiOrigin,
      "/settings": apiOrigin,
      "/tokens": apiOrigin,
      "/static": apiOrigin,
    },
  },
});
