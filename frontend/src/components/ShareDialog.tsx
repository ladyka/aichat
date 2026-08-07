import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, Link2, Loader2, X } from "lucide-react";
import { createShare, getShare, revokeShare } from "@/lib/api";

type ShareDialogProps = {
  conversationId: string;
  onClose: () => void;
};

type ShareState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "none" }
  | { status: "exists"; expiresAt: string | null }
  | {
      status: "shared";
      url: string;
      expiresAt: string | null;
    };

function shareUrl(key: string): string {
  return `${window.location.origin}/s/${key}`;
}

function formatExpiry(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("ru-RU");
}

async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to execCommand fallback */
    }
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.top = "0";
    textarea.style.left = "0";
    textarea.style.opacity = "0";
    textarea.style.pointerEvents = "none";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

export function ShareDialog({ conversationId, onClose }: ShareDialogProps) {
  const [share, setShare] = useState<ShareState>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const urlInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    getShare(conversationId)
      .then((data) => {
        if (cancelled) return;
        setShare({ status: "exists", expiresAt: data.expires_at });
      })
      .catch(() => {
        if (!cancelled) setShare({ status: "none" });
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const onCreate = useCallback(async () => {
    setBusy(true);
    try {
      const data = await createShare(conversationId);
      if (data.key) {
        setShare({
          status: "shared",
          url: shareUrl(data.key),
          expiresAt: data.expires_at,
        });
      } else {
        setShare({ status: "exists", expiresAt: data.expires_at });
      }
    } catch {
      setShare({
        status: "error",
        message: "Не удалось создать ссылку. Попробуйте ещё раз.",
      });
    } finally {
      setBusy(false);
    }
  }, [conversationId]);

  const onRevoke = useCallback(async () => {
    setBusy(true);
    try {
      await revokeShare(conversationId);
      setShare({ status: "none" });
    } catch {
      setShare({
        status: "error",
        message: "Не удалось отозвать ссылку. Попробуйте ещё раз.",
      });
    } finally {
      setBusy(false);
    }
  }, [conversationId]);

  const onRevokeAndCreate = useCallback(async () => {
    setBusy(true);
    try {
      await revokeShare(conversationId);
      const data = await createShare(conversationId);
      if (data.key) {
        setShare({
          status: "shared",
          url: shareUrl(data.key),
          expiresAt: data.expires_at,
        });
      } else {
        setShare({ status: "exists", expiresAt: data.expires_at });
      }
    } catch {
      setShare({
        status: "error",
        message: "Не удалось обновить ссылку. Попробуйте ещё раз.",
      });
    } finally {
      setBusy(false);
    }
  }, [conversationId]);

  const onCopy = useCallback(async () => {
    if (share.status !== "shared") return;
    setCopyFailed(false);
    const ok = await copyText(share.url);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
      return;
    }
    setCopyFailed(true);
    urlInputRef.current?.focus();
    urlInputRef.current?.select();
  }, [share]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Поделиться чатом"
    >
      <div
        className="w-full max-w-md rounded-xl border border-[var(--chat-line)] bg-[var(--chat-panel)] p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-base font-semibold text-[var(--chat-ink)]">
            <Link2 className="h-4 w-4 text-[var(--chat-muted)]" />
            Поделиться чатом
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-[var(--chat-muted)] hover:bg-black/5"
            aria-label="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="mt-2 text-sm text-[var(--chat-muted)]">
          По этой ссылке любой сможет посмотреть диалог. Модель и автор не
          показываются.
        </p>

        <div className="mt-4">
          {share.status === "loading" && (
            <div className="flex items-center gap-2 py-4 text-sm text-[var(--chat-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              Проверяем ссылку…
            </div>
          )}

          {share.status === "none" && (
            <button
              type="button"
              disabled={busy}
              onClick={onCreate}
              className="inline-flex w-full items-center justify-center gap-2 rounded-full bg-[var(--chat-accent)] px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              Создать ссылку
            </button>
          )}

          {share.status === "exists" && (
            <div className="grid gap-3">
              <p className="text-sm text-[var(--chat-muted)]">
                Ссылка на этот чат уже создана, но увидеть её снова нельзя —
                сохраните её при создании. Можно отозвать старую и создать новую.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={onRevokeAndCreate}
                className="inline-flex w-full items-center justify-center gap-2 rounded-full bg-[var(--chat-accent)] px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                Создать новую ссылку
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={onRevoke}
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-[var(--chat-line)] px-4 py-1.5 text-sm text-[var(--chat-ink)] hover:bg-black/5 disabled:opacity-60"
              >
                {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                Отозвать ссылку
              </button>
            </div>
          )}

          {share.status === "shared" && (
            <div className="grid gap-3">
              <div className="flex items-center gap-2 rounded-lg border border-[var(--chat-line)] bg-black/5 p-2">
                <input
                  ref={urlInputRef}
                  type="text"
                  readOnly
                  value={share.url}
                  onFocus={(e) => e.currentTarget.select()}
                  className="min-w-0 flex-1 bg-transparent text-sm text-[var(--chat-ink)] outline-none"
                />
                <button
                  type="button"
                  onClick={onCopy}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--chat-accent)] px-2.5 py-1.5 text-xs font-medium text-white"
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                  {copied ? "Скопировано" : "Копировать"}
                </button>
              </div>
              <p className="text-xs text-[var(--chat-muted)]">
                {share.expiresAt
                  ? `Ссылка действует до ${formatExpiry(share.expiresAt)}.`
                  : "Без срока действия."}{" "}
                Любой, у кого есть ссылка, может открыть её.
              </p>
              {copyFailed && (
                <p className="text-xs text-[var(--chat-danger)]">
                  Не удалось скопировать автоматически — ссылка выделена,
                  нажмите Ctrl+C.
                </p>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={onRevoke}
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-[var(--chat-line)] px-4 py-1.5 text-sm text-[var(--chat-ink)] hover:bg-black/5 disabled:opacity-60"
              >
                {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                Отозвать ссылку
              </button>
            </div>
          )}

          {share.status === "error" && (
            <p className="text-sm text-[var(--chat-danger)]">{share.message}</p>
          )}
        </div>
      </div>
    </div>
  );
}
