import { useState } from "react";
import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
} from "@assistant-ui/react";
import { Archive, Plus, Trash2 } from "lucide-react";

function ThreadListItem() {
  const aui = useAui();
  const [confirm, setConfirm] = useState<null | "archive" | "delete">(null);

  const runConfirm = () => {
    if (confirm === "archive") aui.threadListItem.archive();
    if (confirm === "delete") aui.threadListItem.delete();
    setConfirm(null);
  };

  return (
    <ThreadListItemPrimitive.Root className="group flex items-center gap-1 rounded-lg px-2 py-1.5 data-[active]:bg-[color-mix(in_srgb,var(--chat-accent)_12%,transparent)] hover:bg-black/5">
      <ThreadListItemPrimitive.Trigger className="min-w-0 flex-1 truncate text-left text-sm">
        <ThreadListItemPrimitive.Title fallback="Новый чат" />
      </ThreadListItemPrimitive.Trigger>

      {confirm === null ? (
        <>
          <button
            type="button"
            onClick={() => setConfirm("archive")}
            className="rounded p-1 opacity-0 group-hover:opacity-100 hover:bg-black/10"
            title="Архивировать"
            aria-label="Архивировать"
          >
            <Archive className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setConfirm("delete")}
            className="rounded p-1 opacity-0 group-hover:opacity-100 hover:bg-black/10"
            title="Удалить"
            aria-label="Удалить"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </>
      ) : (
        <>
          <span className="text-xs text-[var(--chat-muted)]">
            {confirm === "archive" ? "Архивировать?" : "Удалить?"}
          </span>
          <button
            type="button"
            onClick={runConfirm}
            className="rounded bg-[var(--chat-accent)] px-1.5 py-0.5 text-xs font-medium text-white hover:opacity-90"
          >
            Да
          </button>
          <button
            type="button"
            onClick={() => setConfirm(null)}
            className="rounded px-1.5 py-0.5 text-xs text-[var(--chat-muted)] hover:bg-black/10"
          >
            Нет
          </button>
        </>
      )}
    </ThreadListItemPrimitive.Root>
  );
}

export function ThreadList() {
  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col gap-2 p-3">
      <ThreadListPrimitive.New className="inline-flex items-center justify-center gap-2 rounded-full bg-[var(--chat-accent)] px-3 py-2 text-sm font-medium text-white">
        <Plus className="h-4 w-4" />
        Новый чат
      </ThreadListPrimitive.New>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ThreadListPrimitive.Items components={{ ThreadListItem }} />
      </div>
    </ThreadListPrimitive.Root>
  );
}
