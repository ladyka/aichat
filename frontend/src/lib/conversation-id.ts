import { createContext, useContext } from "react";

export const ConversationIdContext = createContext<string | null>(null);

export function useConversationId(): string | null {
  return useContext(ConversationIdContext);
}
