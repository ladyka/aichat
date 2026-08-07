import { useEffect, useState } from "react";
import { useAui } from "@assistant-ui/react";
import {
  Archive,
  ChevronDown,
  ChevronUp,
  Home,
  Link2,
  PanelLeftClose,
  PanelLeftOpen,
  Trash2,
  UserRound,
} from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { RuntimeProvider } from "@/components/RuntimeProvider";
import { ShareDialog } from "@/components/ShareDialog";
import { Thread } from "@/components/Thread";
import { ThreadList } from "@/components/ThreadList";
import { useConversationId } from "@/lib/conversation-id";

const SIDEBAR_KEY = "aichat.sidebarOpen";
const MOBILE_QUERY = "(max-width: 768px)";

const userEmail: string | undefined =
  typeof window !== "undefined"
    ? (window as unknown as { AICHAT_USER?: string }).AICHAT_USER
    : undefined;

function isMobileViewport(): boolean {
  return typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches;
}

function ChatLayout() {
  const conversationId = useConversationId();
  const aui = useAui();
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try {
      const raw = localStorage.getItem(SIDEBAR_KEY);
      if (raw !== null) return raw === "1";
    } catch {
      /* ignore */
    }
    return !isMobileViewport();
  });
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [confirm, setConfirm] = useState<null | "archive" | "delete">(null);

  const runConfirm = () => {
    if (confirm === "archive") aui.threadListItem.archive();
    if (confirm === "delete") aui.threadListItem.delete();
    setConfirm(null);
  };

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, sidebarOpen ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [sidebarOpen]);

  return (
    <div className="relative flex h-full min-h-0 bg-[var(--chat-bg)] text-[var(--chat-ink)]">
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/25 md:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col border-r border-[var(--chat-line)] bg-[var(--chat-panel)] shadow-xl transition-transform duration-200 md:static md:z-auto md:shadow-none md:transition-[width] ${
          sidebarOpen
            ? "translate-x-0 md:w-64"
            : "-translate-x-full md:w-0 md:translate-x-0 md:overflow-hidden md:border-r-0"
        }`}
      >
        <div className="flex items-center justify-between border-b border-[var(--chat-line)] px-3 py-2">
          <a
            href="/"
            className="inline-flex items-center gap-2 rounded px-2 py-1 text-sm text-[var(--chat-muted)] hover:bg-black/5 hover:text-[var(--chat-ink)]"
            title="На главную"
          >
            <Home className="h-4 w-4" />
            На главную
          </a>
        </div>
        <div className="min-h-0 flex-1">
          <ThreadList />
        </div>
        <div className="relative border-t border-[var(--chat-line)] p-2">
          <button
            type="button"
            onClick={() => setUserMenuOpen((v) => !v)}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-[var(--chat-ink)] hover:bg-black/5"
            aria-haspopup="menu"
            aria-expanded={userMenuOpen}
          >
            <UserRound className="h-4 w-4 shrink-0 text-[var(--chat-muted)]" />
            <span className="min-w-0 flex-1 truncate text-left">
              {userEmail ?? "Аккаунт"}
            </span>
            {userMenuOpen ? (
              <ChevronUp className="h-4 w-4 shrink-0 text-[var(--chat-muted)]" />
            ) : (
              <ChevronDown className="h-4 w-4 shrink-0 text-[var(--chat-muted)]" />
            )}
          </button>
          {userMenuOpen && (
            <div className="absolute bottom-full left-2 right-2 z-50 mb-2 overflow-hidden rounded-lg border border-[var(--chat-line)] bg-[var(--chat-panel)] shadow-xl">
              <a
                href="/profile"
                className="block px-3 py-2 text-sm text-[var(--chat-ink)] hover:bg-black/5"
              >
                Профиль
              </a>
              <a
                href="/settings"
                className="block px-3 py-2 text-sm text-[var(--chat-ink)] hover:bg-black/5"
              >
                Настройки
              </a>
              <a
                href="/privacy"
                className="block px-3 py-2 text-sm text-[var(--chat-muted)] hover:bg-black/5"
              >
                Политика конфиденциальности
              </a>
              <a
                href="/terms"
                className="block px-3 py-2 text-sm text-[var(--chat-muted)] hover:bg-black/5"
              >
                Пользовательское соглашение
              </a>
              <form
                action="/logout"
                method="post"
                className="border-t border-[var(--chat-line)]"
              >
                <button
                  type="submit"
                  className="w-full px-3 py-2 text-left text-sm text-[var(--chat-ink)] hover:bg-black/5"
                >
                  Выйти
                </button>
              </form>
            </div>
          )}
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
          {conversationId && (
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--chat-line)] px-2.5 py-1.5 text-sm hover:bg-black/5"
                onClick={() => setConfirm("archive")}
                aria-label="Архивировать диалог"
              >
                <Archive className="h-4 w-4" />
                Архив
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--chat-line)] px-2.5 py-1.5 text-sm hover:bg-black/5"
                onClick={() => setConfirm("delete")}
                aria-label="Удалить диалог"
              >
                <Trash2 className="h-4 w-4" />
                Удалить
              </button>
              <button
                type="button"
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--chat-line)] px-2.5 py-1.5 text-sm hover:bg-black/5"
                onClick={() => setShareOpen(true)}
                aria-label="Поделиться диалогом"
              >
                <Link2 className="h-4 w-4" />
                Поделиться
              </button>
            </div>
          )}
        </div>
        <div className="min-h-0 flex-1">
          <Thread />
        </div>
      </div>

      {shareOpen && conversationId && (
        <ShareDialog
          conversationId={conversationId}
          onClose={() => setShareOpen(false)}
        />
      )}

      {confirm && (
        <ConfirmDialog
          title={confirm === "archive" ? "Архивировать диалог" : "Удалить диалог"}
          message={
            confirm === "archive"
              ? "Диалог уйдёт в архив. Его можно восстановить позже."
              : "Диалог будет удалён без возможности восстановления."
          }
          confirmLabel={confirm === "archive" ? "Архивировать" : "Удалить"}
          danger={confirm === "delete"}
          onConfirm={runConfirm}
          onClose={() => setConfirm(null)}
        />
      )}
    </div>
  );
}

export default function App() {
  return (
    <RuntimeProvider>
      <ChatLayout />
    </RuntimeProvider>
  );
}
