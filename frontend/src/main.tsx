import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

// PWA: `/sw.js` отдаётся бэкендом из корня (см. `sw.ts` и `app/routes/pages.py`),
// поэтому scope по умолчанию `/` — он же покрывает `/chat`. В dev сборки нет,
// как и service worker'а: регистрируемся только в проде.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  navigator.serviceWorker
    .register("/sw.js", { updateViaCache: "none" })
    .catch(() => {
      /* PWA — необязательное улучшение, без неё чат работает как раньше */
    });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
