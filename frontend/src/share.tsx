/* Страница шара `/s/<key>`: тот же рендер markdown, что и в чате.
   Сообщения приходят с бэкенда сырым markdown в `window.AICHAT_SHARE`
   (см. app/routes/share.py и templates/share.html). */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./share.css";
import { Markdown } from "@/lib/markdown";

type ShareMessage = {
  role: "user" | "assistant" | string;
  content: string;
};

declare global {
  interface Window {
    AICHAT_SHARE?: ShareMessage[];
  }
}

export function ShareMessages({ messages }: { messages: ShareMessage[] }) {
  return (
    <div className="share-log">
      {messages.map((m, index) => (
        <div
          key={index}
          className={
            m.role === "user" ? "bubble share-bubble user" : "bubble share-bubble bot"
          }
        >
          {m.role !== "user" ? <div className="share-name">Чатбот</div> : null}
          {m.role === "user" ? (
            <p className="share-user-text">{m.content}</p>
          ) : (
            <div className="md">
              <Markdown>{m.content}</Markdown>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Mount() {
  const messages = window.AICHAT_SHARE ?? [];
  if (messages.length === 0) {
    return <p className="muted">В этом чате пока нет сообщений.</p>;
  }
  return <ShareMessages messages={messages} />;
}

const root = document.getElementById("share-root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Mount />
    </StrictMode>,
  );
}