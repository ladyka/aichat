import { useEffect, useRef, useState } from "react";
import { Download, FileText, PanelRightClose, StickyNote } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  conversationNoteDownloadUrl,
  getConversationNote,
  putConversationNote,
} from "@/lib/api";
import { onNoteChanged } from "@/lib/note-events";

type NotePaneProps = {
  conversationId: string;
  onClose: () => void;
  onHasBody: (hasBody: boolean) => void;
};

const SAVE_MS = 800;

export function NotePane({ conversationId, onClose, onHasBody }: NotePaneProps) {
  const [title, setTitle] = useState("Заметка");
  const [body, setBody] = useState("");
  const [mode, setMode] = useState<"source" | "preview">("source");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const loadedFor = useRef<string | null>(null);
  const skipSave = useRef(true);
  const savedTitle = useRef("Заметка");
  const savedBody = useRef("");

  useEffect(() => {
    let cancelled = false;
    loadedFor.current = conversationId;
    skipSave.current = true;
    setStatus("idle");
    setError(null);
    void (async () => {
      try {
        const note = await getConversationNote(conversationId);
        if (cancelled || loadedFor.current !== conversationId) return;
        const nextTitle = note?.title || "Заметка";
        const nextBody = note?.body || "";
        savedTitle.current = nextTitle;
        savedBody.current = nextBody;
        setTitle(nextTitle);
        setBody(nextBody);
        onHasBody(nextBody.trim().length > 0);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить заметку");
      } finally {
        skipSave.current = false;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId, onHasBody]);

  useEffect(() => {
    return onNoteChanged(() => {
      skipSave.current = true;
      void getConversationNote(conversationId).then((note) => {
        if (loadedFor.current !== conversationId) return;
        const nextTitle = note?.title || "Заметка";
        const nextBody = note?.body || "";
        savedTitle.current = nextTitle;
        savedBody.current = nextBody;
        setTitle(nextTitle);
        setBody(nextBody);
        onHasBody(nextBody.trim().length > 0);
        skipSave.current = false;
      });
    });
  }, [conversationId, onHasBody]);

  useEffect(() => {
    if (skipSave.current) return;
    if (title === savedTitle.current && body === savedBody.current) return;
    const handle = window.setTimeout(() => {
      setStatus("saving");
      void putConversationNote(conversationId, { title, body })
        .then(() => {
          savedTitle.current = title;
          savedBody.current = body;
          setStatus("saved");
          onHasBody(body.trim().length > 0);
        })
        .catch((err: unknown) => {
          setStatus("error");
          setError(err instanceof Error ? err.message : "Не удалось сохранить");
        });
    }, SAVE_MS);
    return () => window.clearTimeout(handle);
  }, [title, body, conversationId, onHasBody]);

  return (
    <aside className="note-pane flex h-full min-h-0 w-full flex-col border-l border-[var(--chat-line)] bg-[var(--chat-panel)]">
      <div className="flex items-center gap-2 border-b border-[var(--chat-line)] px-2 py-2">
        <StickyNote className="h-4 w-4 shrink-0 text-[var(--chat-muted)]" />
        <input
          className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-sm font-medium outline-none hover:border-[var(--chat-line)] focus:border-[var(--chat-accent)]"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-label="Заголовок заметки"
        />
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className={`rounded px-2 py-1 text-xs ${
              mode === "source"
                ? "bg-black/10 text-[var(--chat-ink)]"
                : "text-[var(--chat-muted)] hover:bg-black/5"
            }`}
            onClick={() => setMode("source")}
          >
            Текст
          </button>
          <button
            type="button"
            className={`rounded px-2 py-1 text-xs ${
              mode === "preview"
                ? "bg-black/10 text-[var(--chat-ink)]"
                : "text-[var(--chat-muted)] hover:bg-black/5"
            }`}
            onClick={() => setMode("preview")}
          >
            Просмотр
          </button>
          <a
            className="inline-flex items-center rounded p-1.5 text-[var(--chat-muted)] hover:bg-black/5 hover:text-[var(--chat-ink)]"
            href={conversationNoteDownloadUrl(conversationId)}
            download
            title="Скачать markdown"
            aria-label="Скачать markdown"
          >
            <Download className="h-4 w-4" />
          </a>
          <button
            type="button"
            className="inline-flex items-center rounded p-1.5 text-[var(--chat-muted)] hover:bg-black/5 hover:text-[var(--chat-ink)]"
            onClick={onClose}
            aria-label="Скрыть заметку"
            title="Скрыть"
          >
            <PanelRightClose className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {mode === "source" ? (
          <textarea
            className="note-source h-full w-full resize-none border-0 bg-transparent p-3 text-sm leading-6 outline-none"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Markdown…"
            spellCheck
          />
        ) : (
          <div className="md note-preview h-full overflow-y-auto p-3 text-sm">
            {body.trim() ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
            ) : (
              <p className="text-[var(--chat-muted)]">Пока пусто</p>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2 border-t border-[var(--chat-line)] px-3 py-1 text-[0.7rem] text-[var(--chat-muted)]">
        <FileText className="h-3 w-3" />
        {status === "saving" && "Сохранение…"}
        {status === "saved" && "Сохранено"}
        {status === "error" && (error || "Ошибка сохранения")}
        {status === "idle" && (error || " ")}
      </div>
    </aside>
  );
}
