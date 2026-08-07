import type { ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useLocalRuntime,
  useRemoteThreadListRuntime,
} from "@assistant-ui/react";
import { createChatModelAdapter } from "@/lib/chat-model-adapter";
import { ConversationIdContext } from "@/lib/conversation-id";
import { useAichatThreadListAdapter } from "@/lib/thread-list-adapter";
import { useMemo, useRef, useState } from "react";

export function RuntimeProvider({ children }: { children: ReactNode }) {
  const conversationIdRef = useRef<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
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
      setConversationId(threadId ?? null);
    },
    runtimeHook: function RuntimeHook() {
      return useLocalRuntime(chatModel);
    },
  });

  return (
    <ConversationIdContext.Provider value={conversationId}>
      <AssistantRuntimeProvider runtime={runtime}>
        {children}
      </AssistantRuntimeProvider>
    </ConversationIdContext.Provider>
  );
}
