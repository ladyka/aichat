import type { ReactNode } from "react";

import ReactMarkdown, { type Components, type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";

type AnchorProps = React.AnchorHTMLAttributes<HTMLAnchorElement> &
  ExtraProps & {
    children?: ReactNode;
  };

/**
 * Ссылки в чате и заметке: внешние (http/https/mailto и любые другие схемы)
 * всегда открываются в новом окне и без window.opener, чтобы открытая страница
 * не могла добраться до окна чата. Относительные ссылки (если появятся)
 * остаются в текущем окне.
 */
function SafeAnchor({ children, href, node: _node, ...rest }: AnchorProps) {
  const url = href ?? "";
  const external = /^[a-z][a-z0-9+.-]*:/i.test(url);
  if (!external) {
    return (
      <a href={url} {...rest}>
        {children}
      </a>
    );
  }
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" {...rest}>
      {children}
    </a>
  );
}

export const mdComponents = {
  a: SafeAnchor,
} satisfies Components;

/** Единый рендер markdown: GFM + безопасные ссылки (см. mdComponents). */
export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
      {children}
    </ReactMarkdown>
  );
}