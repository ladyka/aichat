import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex w-full justify-end">
      <div className="msg-bubble user">
        <MessagePrimitive.Content />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="flex w-full justify-start">
      <div className="msg-bubble assistant">
        <MessagePrimitive.Content />
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
            <p className="mt-1 text-sm">
              Напишите сообщение. Модель задаётся в настройках.
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

      <div className="border-t border-[var(--chat-line)] bg-[var(--chat-panel)] px-4 py-3">
        <ComposerPrimitive.Root className="mx-auto flex max-w-3xl items-end gap-2">
          <ComposerPrimitive.Input
            className="aui-composer-input"
            rows={2}
            placeholder="Напишите сообщение…"
          />
          <ComposerPrimitive.Send className="inline-flex shrink-0 items-center justify-center rounded-full bg-[var(--chat-accent)] px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
            Отправить
          </ComposerPrimitive.Send>
          <ThreadPrimitive.If running>
            <ComposerPrimitive.Cancel className="inline-flex shrink-0 items-center justify-center rounded-full border border-[var(--chat-line)] px-4 py-2.5 text-sm font-medium">
              Стоп
            </ComposerPrimitive.Cancel>
          </ThreadPrimitive.If>
        </ComposerPrimitive.Root>
      </div>
    </ThreadPrimitive.Root>
  );
}
