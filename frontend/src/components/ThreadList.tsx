import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import { Pencil, Plus } from "lucide-react";

const TITLE_MAX = 200;

const ThreadListNavigateContext = createContext<(() => void) | undefined>(
  undefined,
);

function ThreadTitleEditor({
  title,
  onSave,
  onCancel,
  className,
}: {
  title: string;
  onSave: (next: string) => void;
  onCancel: () => void;
  className?: string;
}) {
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);
  const skipBlurSave = useRef(false);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  function commit() {
    const next = draft.trim().slice(0, TITLE_MAX);
    if (next) onSave(next);
    else onCancel();
  }

  return (
    <form
      className={className}
      onSubmit={(event) => {
        event.preventDefault();
        commit();
      }}
    >
      <input
        ref={inputRef}
        className="thread-title-input"
        value={draft}
        maxLength={TITLE_MAX}
        aria-label="Название чата"
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (skipBlurSave.current) {
            skipBlurSave.current = false;
            return;
          }
          commit();
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            skipBlurSave.current = true;
            onCancel();
          }
        }}
      />
    </form>
  );
}

function ThreadListItem() {
  const aui = useAui();
  const onNavigate = useContext(ThreadListNavigateContext);
  const title = useAuiState((s) => s.threadListItem.title?.trim() || "");
  const [editing, setEditing] = useState(false);

  function startEdit(event: MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
    setEditing(true);
  }

  if (editing) {
    return (
      <ThreadListItemPrimitive.Root className="group flex items-center gap-1 rounded-lg px-2 py-1.5 data-[active]:bg-[color-mix(in_srgb,var(--chat-accent)_12%,transparent)]">
        <ThreadTitleEditor
          title={title || "Новый чат"}
          className="min-w-0 flex-1"
          onSave={(next) => {
            if (next !== title) aui.threadListItem.rename(next);
            setEditing(false);
          }}
          onCancel={() => setEditing(false)}
        />
      </ThreadListItemPrimitive.Root>
    );
  }

  return (
    <ThreadListItemPrimitive.Root className="group flex items-center gap-1 rounded-lg px-2 py-1.5 data-[active]:bg-[color-mix(in_srgb,var(--chat-accent)_12%,transparent)] hover:bg-black/5">
      <ThreadListItemPrimitive.Trigger
        className="min-w-0 flex-1 truncate text-left text-sm"
        onClick={onNavigate}
        onDoubleClick={startEdit}
      >
        <ThreadListItemPrimitive.Title fallback="Новый чат" />
      </ThreadListItemPrimitive.Trigger>
      <button
        type="button"
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--chat-muted)] opacity-0 hover:bg-black/5 hover:text-[var(--chat-ink)] group-hover:opacity-100 focus-visible:opacity-100"
        aria-label="Переименовать чат"
        onClick={startEdit}
      >
        <Pencil className="h-3.5 w-3.5" aria-hidden />
      </button>
    </ThreadListItemPrimitive.Root>
  );
}

export function ThreadList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <ThreadListNavigateContext.Provider value={onNavigate}>
      <ThreadListPrimitive.Root className="flex h-full flex-col gap-2 p-3">
        <ThreadListPrimitive.New
          className="inline-flex items-center justify-center gap-2 rounded-full bg-[var(--chat-accent)] px-3 py-2 text-sm font-medium text-white"
          onClick={onNavigate}
        >
          <Plus className="h-4 w-4" />
          Новый чат
        </ThreadListPrimitive.New>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <ThreadListPrimitive.Items components={{ ThreadListItem }} />
        </div>
      </ThreadListPrimitive.Root>
    </ThreadListNavigateContext.Provider>
  );
}

/** Заголовок открытого чата в шапке: клик — переименовать. */
export function ActiveThreadTitle() {
  const aui = useAui();
  const title = useAuiState((s) => s.threadListItem.title?.trim() || "");
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const [editing, setEditing] = useState(false);

  if (!remoteId) return null;

  if (editing) {
    return (
      <ThreadTitleEditor
        title={title || "Новый чат"}
        className="min-w-0 flex-1"
        onSave={(next) => {
          if (next !== title) aui.threadListItem.rename(next);
          setEditing(false);
        }}
        onCancel={() => setEditing(false)}
      />
    );
  }

  return (
    <button
      type="button"
      className="thread-title-button min-w-0 flex-1 truncate text-left text-sm font-medium text-[var(--chat-ink)] hover:text-[var(--chat-accent)]"
      onClick={() => setEditing(true)}
      aria-label="Переименовать чат"
      title="Переименовать"
    >
      {title || "Новый чат"}
    </button>
  );
}
