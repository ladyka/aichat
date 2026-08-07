import { useEffect, useState } from "react";
import { PanelLeftClose, PanelLeftOpen, Settings } from "lucide-react";
import { RuntimeProvider } from "@/components/RuntimeProvider";
import { Thread } from "@/components/Thread";
import { ThreadList } from "@/components/ThreadList";

const SIDEBAR_KEY = "aichat.sidebarOpen";

export default function App() {
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      const raw = localStorage.getItem(SIDEBAR_KEY);
      return raw === null ? true : raw === "1";
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, sidebarOpen ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [sidebarOpen]);

  return (
    <RuntimeProvider>
      <div className="flex h-full min-h-0 bg-[var(--chat-bg)] text-[var(--chat-ink)]">
        <aside
          className={`flex shrink-0 flex-col border-r border-[var(--chat-line)] bg-[var(--chat-panel)] transition-[width] duration-200 ${
            sidebarOpen ? "w-64" : "w-0 overflow-hidden border-r-0"
          }`}
        >
          <div className="flex items-center justify-between border-b border-[var(--chat-line)] px-3 py-2">
            <span className="text-sm font-medium">Истории</span>
            <a
              href="/settings"
              className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--chat-muted)] hover:bg-black/5 hover:text-[var(--chat-ink)]"
              title="Свойства"
            >
              <Settings className="h-3.5 w-3.5" />
              Модель
            </a>
          </div>
          <div className="min-h-0 flex-1">
            <ThreadList />
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-[var(--chat-line)] bg-[var(--chat-panel)] px-3 py-2">
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-lg border border-[var(--chat-line)] px-2.5 py-1.5 text-sm hover:bg-black/5"
              onClick={() => setSidebarOpen((v) => !v)}
              aria-label={sidebarOpen ? "Скрыть истории" : "Показать истории"}
            >
              {sidebarOpen ? (
                <PanelLeftClose className="h-4 w-4" />
              ) : (
                <PanelLeftOpen className="h-4 w-4" />
              )}
              {sidebarOpen ? "Скрыть" : "Истории"}
            </button>
            <a
              href="/settings"
              className="ml-auto text-sm text-[var(--chat-muted)] hover:text-[var(--chat-accent)]"
            >
              Свойства
            </a>
            <a
              href="/tokens"
              className="text-sm text-[var(--chat-muted)] hover:text-[var(--chat-accent)]"
            >
              API
            </a>
          </div>
          <div className="min-h-0 flex-1">
            <Thread />
          </div>
        </div>
      </div>
    </RuntimeProvider>
  );
}
