import {
  type FC,
  type PropsWithChildren,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  type RemoteThreadListAdapter,
  type ThreadHistoryAdapter,
  type ThreadMessage,
  type ExportedMessageRepository,
  type ExportedMessageRepositoryItem,
  RuntimeAdapterProvider,
  useAui,
} from "@assistant-ui/react";
import type { AssistantStreamChunk } from "assistant-stream";
import {
  appendMessages,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  patchConversation,
} from "@/lib/api";

function emptyTitleStream(): ReadableStream<AssistantStreamChunk> {
  return new ReadableStream<AssistantStreamChunk>({
    start(controller) {
      controller.close();
    },
  });
}

function textFromThreadMessage(message: ThreadMessage): string {
  if (message.role === "system") {
    const part = message.content[0];
    return part && "text" in part ? String(part.text ?? "") : "";
  }
  return message.content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

class AichatHistoryAdapter implements ThreadHistoryAdapter {
  private getAui: () => ReturnType<typeof useAui>;

  constructor(getAui: () => ReturnType<typeof useAui>) {
    this.getAui = getAui;
  }

  async load(): Promise<ExportedMessageRepository> {
    const remoteId = this.getAui().threadListItem.getState().remoteId;
    if (!remoteId) return { messages: [] };

    const detail = await getConversation(remoteId);
    const messages: ExportedMessageRepositoryItem[] = [];
    let parentId: string | null = null;
    for (const m of detail.messages) {
      const createdAt = m.created_at ? new Date(m.created_at) : new Date();
      const textPart = { type: "text" as const, text: m.content };
      let message: ThreadMessage;
      if (m.role === "assistant") {
        message = {
          id: m.id,
          role: "assistant",
          createdAt,
          content: [textPart],
          status: { type: "complete", reason: "stop" },
          metadata: {
            unstable_state: null,
            unstable_annotations: [],
            unstable_data: [],
            steps: [],
            custom: {},
          },
        };
      } else if (m.role === "system") {
        message = {
          id: m.id,
          role: "system",
          createdAt,
          content: [textPart],
          metadata: { custom: {} },
        } as ThreadMessage;
      } else {
        message = {
          id: m.id,
          role: "user",
          createdAt,
          content: [textPart],
          attachments: [],
          metadata: { custom: {} },
        };
      }
      messages.push({ parentId, message });
      parentId = m.id;
    }
    return { messages };
  }

  async append(item: ExportedMessageRepositoryItem): Promise<void> {
    const { remoteId } = await this.getAui().threadListItem.initialize();
    const role = item.message.role;
    if (role !== "user" && role !== "assistant" && role !== "system") return;
    const content = textFromThreadMessage(item.message);
    await appendMessages(remoteId, [{ role, content }]);
  }
}

export function useAichatThreadListAdapter(): RemoteThreadListAdapter {
  const unstable_Provider = useCallback<FC<PropsWithChildren>>(
    function Provider({ children }) {
      const aui = useAui();
      const auiRef = useRef(aui);
      useEffect(() => {
        auiRef.current = aui;
      });
      const [history] = useState(
        () => new AichatHistoryAdapter(() => auiRef.current),
      );
      const adapters = useMemo(() => ({ history }), [history]);
      return (
        <RuntimeAdapterProvider adapters={adapters}>
          {children}
        </RuntimeAdapterProvider>
      );
    },
    [],
  );

  return useMemo<RemoteThreadListAdapter>(
    () => ({
      async list() {
        const rows = await listConversations();
        return {
          threads: rows.map((c) => ({
            status: "regular" as const,
            remoteId: c.id,
            title: c.title,
            lastMessageAt: c.updated_at ? new Date(c.updated_at) : undefined,
          })),
        };
      },

      async initialize() {
        const created = await createConversation();
        return { remoteId: created.id };
      },

      async rename(remoteId, newTitle) {
        await patchConversation(remoteId, { title: newTitle });
      },

      async archive(remoteId) {
        await patchConversation(remoteId, { archived: true });
      },

      async unarchive(remoteId) {
        await patchConversation(remoteId, { archived: false });
      },

      async delete(remoteId) {
        await deleteConversation(remoteId);
      },

      async generateTitle() {
        return emptyTitleStream();
      },

      async fetch(threadId) {
        const detail = await getConversation(threadId);
        return {
          status: detail.archived_at ? ("archived" as const) : ("regular" as const),
          remoteId: detail.id,
          title: detail.title,
          lastMessageAt: detail.updated_at
            ? new Date(detail.updated_at)
            : undefined,
        };
      },

      unstable_Provider,
    }),
    [unstable_Provider],
  );
}
