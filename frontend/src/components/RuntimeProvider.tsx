import type { ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useRemoteThreadListRuntime,
} from "@assistant-ui/react";
import { createChatModelAdapter } from "@/lib/chat-model-adapter";
import { useAichatThreadListAdapter } from "@/lib/thread-list-adapter";
import { useMemo, useRef } from "react";

export function RuntimeProvider({ children }: { children: ReactNode }) {
  const conversationIdRef = useRef<string | null>(null);
  const chatModel = useMemo(
    () =>
      createChatModelAdapter(() => conversationIdRef.current),
    [],
  );
  const adapter = useAichatThreadListAdapter();

  const runtime = useRemoteThreadListRuntime({
    adapter,
    onThreadIdChange: (threadId) => {
      conversationIdRef.current = threadId ?? null;
    },
    runtimeHook: function RuntimeHook() {
      return useLocalRuntime(chatModel);
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
