import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  type ReasoningMessagePartProps,
  type TextMessagePartProps,
} from "@assistant-ui/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex w-full justify-end">
      <div className="msg-bubble user">
        <MessagePrimitive.Content />
      </div>
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
  return (
    <MessagePrimitive.Root className="flex w-full justify-start">
      <div className="msg-bubble assistant">
        <MessagePrimitive.Error>
          <MessageErrorText />
        </MessagePrimitive.Error>
        <MessagePrimitive.Content
          components={{ Text: MarkdownText, Reasoning: ReasoningText }}
        />
      </div>
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
