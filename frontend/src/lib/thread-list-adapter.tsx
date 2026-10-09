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
  deleteMessages,
  getConversation,
  listConversations,
  patchConversation,
  type ConversationSummary,
} from "@/lib/api";
import { isResponseInfo, type ResponseInfo } from "@/lib/response-info";

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

function responseMetaFromMessage(message: ThreadMessage): ResponseInfo | null {
  if (message.role !== "assistant") return null;
  const custom = message.metadata?.custom as Record<string, unknown> | undefined;
  const raw = custom?.response;
  return isResponseInfo(raw) && (raw.model || raw.usage) ? raw : null;
}

function assistantMetadata(meta: Record<string, unknown> | null | undefined) {
  const response =
    isResponseInfo(meta) && (meta.model || meta.usage) ? meta : null;
  return {
    unstable_state: null,
    unstable_annotations: [],
    unstable_data: [],
    steps: [],
    custom: response ? { response } : {},
  };
}

export class AichatHistoryAdapter implements ThreadHistoryAdapter {
  private getAui: () => ReturnType<typeof useAui>;
  /**
   * id сообщения на экране → id строки в базе (вместе с диалогом, которому она
   * принадлежит: адаптер живёт дольше одного диалога). У загруженных сообщений
   * это один и тот же id, а у только что отправленных клиент придумывает свой —
   * сервер возвращает настоящий, и без этой карты удалять было бы нечего.
   */
  private stored = new Map<string, { remoteId: string; dbId: string }>();

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
          metadata: assistantMetadata(m.meta),
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
      this.stored.set(m.id, { remoteId, dbId: m.id });
    }
    return { messages };
  }

  async append(item: ExportedMessageRepositoryItem): Promise<void> {
    const { remoteId } = await this.getAui().threadListItem.initialize();
    const role = item.message.role;
    if (role !== "user" && role !== "assistant" && role !== "system") return;
    const content = textFromThreadMessage(item.message);
    const response = responseMetaFromMessage(item.message);
    const [created] = await appendMessages(remoteId, [
      {
        role,
        content,
        ...(response ? { meta: response } : {}),
      },
    ]);
    if (created) this.stored.set(item.message.id, { remoteId, dbId: created.id });
  }

  async delete(items: ExportedMessageRepositoryItem[]): Promise<void> {
    const remoteId = this.getAui().threadListItem.getState().remoteId;
    if (!remoteId) return;

    const ids = new Set<string>();
    for (const item of items) {
      const known = this.stored.get(item.message.id);
      if (!known || known.remoteId !== remoteId) continue;
      ids.add(known.dbId);
      this.stored.delete(item.message.id);
    }
    await deleteMessages(remoteId, [...ids]);
  }
}

export function threadFromSummary(c: ConversationSummary) {
  return {
    status: c.archived_at ? ("archived" as const) : ("regular" as const),
    remoteId: c.id,
    title: c.title,
    lastMessageAt: c.updated_at ? new Date(c.updated_at) : undefined,
    custom: { pinned: Boolean(c.pinned_at) },
  };
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
        const rows = await listConversations({ archived: "all" });
        return { threads: rows.map(threadFromSummary) };
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
        return threadFromSummary(await getConversation(threadId));
      },

      unstable_Provider,
    }),
    [unstable_Provider],
  );
}
