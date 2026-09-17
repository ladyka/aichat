import { useState } from "react";
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
  type ReasoningMessagePartProps,
  type TextMessagePartProps,
} from "@assistant-ui/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type ThreadAui = ReturnType<typeof useAui>;

function lastIdOfRole(
  messages: readonly { id: string; role: string }[],
  role: string,
): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === role) return messages[i].id;
  }
  return null;
}

/**
 * Переспросить: убрать прежний ответ и попросить модель заново.
 *
 * Переписка в базе — плоский список, который только дописывается, а варианты
 * ответа (ветки) она не хранит. Поэтому «Повторить» именно заменяет ответ, а не
 * добавляет второй: иначе после перезагрузки страницы обе ветки склеились бы в
 * один ряд.
 */
async function repeatRequest(aui: ThreadAui, targetId: string): Promise<void> {
  const messages = aui.thread.getState().messages;
  const index = messages.findIndex((m) => m.id === targetId);
  if (index === -1) return;

  const isAnswer = messages[index].role === "assistant";
  const question = messages
    .slice(0, isAnswer ? index : index + 1)
    .reverse()
    .find((m) => m.role === "user");
  if (!question) return;

  for (const m of messages.slice(isAnswer ? index : index + 1)) {
    await aui.thread.deleteMessage(m.id);
  }
  aui.thread.startRun({ parentId: question.id });
}

function UserMessage() {
  const aui = useAui();
  const messageId = useAuiState((s) => s.message.id);
  const parentId = useAuiState((s) => s.message.parentId);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const lastUserId = useAuiState((s) => lastIdOfRole(s.thread.messages, "user"));
  const lastMessageId = useAuiState((s) => s.thread.messages.at(-1)?.id ?? null);
  const text = useAuiState((s) =>
    s.message.content
      .filter((part): part is { type: "text"; text: string } => part.type === "text")
      .map((part) => part.text)
      .join(""),
  );
  /** Черновик правки; `null` — сообщение не правим. */
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isLastUser = messageId === lastUserId;
  const unanswered = lastMessageId === messageId;

  async function saveEdited() {
    const next = (draft ?? "").trim();
    if (!next || busy) return;
    setBusy(true);
    try {
      const messages = aui.thread.getState().messages;
      const from = messages.findIndex((m) => m.id === messageId);
      // Правим последний вопрос: всё, что ниже него, — ответ на прежний вопрос,
      // и он тоже уходит.
      const tail = (from === -1 ? messages : messages.slice(from)).map((m) => m.id);
      for (const id of [...tail].reverse()) {
        await aui.thread.deleteMessage(id);
      }
      aui.thread.append({
        role: "user",
        content: [{ type: "text", text: next }],
        parentId,
        startRun: true,
      });
      setDraft(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <MessagePrimitive.Root className="flex w-full flex-col items-end">
      {draft === null ? (
        <>
          <div className="msg-bubble user">
            <MessagePrimitive.Content />
          </div>
          {isLastUser ? (
            <div className="msg-actions">
              <button
                type="button"
                className="msg-action"
                disabled={isRunning}
                onClick={() => setDraft(text)}
              >
                Изменить
              </button>
              {unanswered ? (
                <button
                  type="button"
                  className="msg-action"
                  disabled={isRunning}
                  onClick={() => {
                    setBusy(true);
                    void repeatRequest(aui, messageId).finally(() => setBusy(false));
                  }}
                >
                  Повторить
                </button>
              ) : null}
            </div>
          ) : null}
        </>
      ) : (
        <form
          className="msg-edit"
          onSubmit={(event) => {
            event.preventDefault();
            void saveEdited();
          }}
        >
          <textarea
            className="msg-edit-input"
            value={draft}
            rows={Math.min(8, draft.split("\n").length + 1)}
            autoFocus
            aria-label="Изменить сообщение"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setDraft(null);
            }}
          />
          <div className="msg-actions">
            <button
              type="button"
              className="msg-action"
              disabled={busy}
              onClick={() => setDraft(null)}
            >
              Отмена
            </button>
            <button
              type="submit"
              className="msg-action primary"
              disabled={busy || !draft.trim()}
            >
              Отправить заново
            </button>
          </div>
        </form>
      )}
    </MessagePrimitive.Root>
  );
}

function MarkdownText({ text }: TextMessagePartProps) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function ReasoningText({ text }: ReasoningMessagePartProps) {
  return (
    <details className="msg-reasoning">
      <summary className="msg-reasoning-summary">Рассуждение</summary>
      <div className="msg-reasoning-body">{text}</div>
    </details>
  );
}

function MessageErrorText() {
  const error = useAuiState((s) => {
    const status = s.message.status;
    if (status?.type === "incomplete" && status.reason === "error") {
      return status.error;
    }
    return null;
  });
  if (!error) return null;
  const message =
    typeof error === "string"
      ? error
      : error && typeof error === "object" && "message" in error
        ? String((error as { message: unknown }).message)
        : "Произошла неизвестная ошибка";
  return <div className="msg-error">Не удалось получить ответ: {message}</div>;
}

function AssistantMessage() {
  const aui = useAui();
  const messageId = useAuiState((s) => s.message.id);
  const isLast = useAuiState((s) => s.message.isLast);
  const isRunning = useAuiState((s) => s.thread.isRunning);
  const [busy, setBusy] = useState(false);

  return (
    <MessagePrimitive.Root className="flex w-full flex-col items-start">
      <div className="msg-bubble assistant">
        <MessagePrimitive.Error>
          <MessageErrorText />
        </MessagePrimitive.Error>
        <MessagePrimitive.Content
          components={{ Text: MarkdownText, Reasoning: ReasoningText }}
        />
      </div>
      {isLast && !isRunning ? (
        <div className="msg-actions">
          <button
            type="button"
            className="msg-action"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void repeatRequest(aui, messageId).finally(() => setBusy(false));
            }}
          >
            Повторить
          </button>
        </div>
      ) : null}
    </MessagePrimitive.Root>
  );
}

export function Thread() {
  const isEmpty = useAuiState((s) => s.thread.messages.length === 0);

  return (
    <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col">
      <ThreadPrimitive.Viewport className="aui-thread-viewport flex flex-1 flex-col gap-3 px-4 py-4">
        {isEmpty ? (
          <div className="m-auto max-w-md text-center text-[var(--chat-muted)]">
            <p className="text-lg font-medium text-[var(--chat-ink)]">
              Новый диалог
            </p>
            <p className="mt-2 text-xs">
              Чатбот может ошибаться и галлюцинировать — проверяйте ответы на реальность.
            </p>
          </div>
        ) : null}
        <ThreadPrimitive.Messages
          components={{
            UserMessage,
            AssistantMessage,
          }}
        />
      </ThreadPrimitive.Viewport>

      <div className="border-t border-[var(--chat-line)] bg-[var(--chat-panel)] px-2 py-2 sm:px-4 sm:py-3">
        <ComposerPrimitive.Root className="mx-auto flex max-w-3xl items-end gap-1.5 sm:gap-2">
          <ComposerPrimitive.Input
            className="aui-composer-input"
            rows={2}
            placeholder="Напишите сообщение…"
          />
          <ComposerPrimitive.Send className="inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--chat-accent)] px-3 py-2.5 text-sm font-medium text-white disabled:opacity-50 sm:px-4">
            Отправить
          </ComposerPrimitive.Send>
          <ThreadPrimitive.If running>
            <ComposerPrimitive.Cancel className="inline-flex shrink-0 items-center justify-center rounded-full border border-[var(--chat-line)] px-3 py-2.5 text-sm font-medium sm:px-4">
              Стоп
            </ComposerPrimitive.Cancel>
          </ThreadPrimitive.If>
        </ComposerPrimitive.Root>
        <p className="pb-1 text-center text-[0.7rem] leading-tight text-[var(--chat-muted)]">
          Чатбот может ошибаться и галлюцинировать — проверяйте ответы на реальность.
        </p>
      </div>
    </ThreadPrimitive.Root>
  );
}
