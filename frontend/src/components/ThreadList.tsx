import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
} from "@assistant-ui/react";
import { Plus } from "lucide-react";

function ThreadListItem() {
  return (
    <ThreadListItemPrimitive.Root className="group flex items-center gap-1 rounded-lg px-2 py-1.5 data-[active]:bg-[color-mix(in_srgb,var(--chat-accent)_12%,transparent)] hover:bg-black/5">
      <ThreadListItemPrimitive.Trigger className="min-w-0 flex-1 truncate text-left text-sm">
        <ThreadListItemPrimitive.Title fallback="Новый чат" />
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  );
}

export function ThreadList() {
  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col gap-2 p-3">
      <ThreadListPrimitive.New className="inline-flex items-center justify-center gap-2 rounded-full bg-[var(--chat-accent)] px-3 py-2 text-sm font-medium text-white">
        <Plus className="h-4 w-4" />
        Новый чат
      </ThreadListPrimitive.New>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ThreadListPrimitive.Items components={{ ThreadListItem }} />
      </div>
    </ThreadListPrimitive.Root>
  );
}
