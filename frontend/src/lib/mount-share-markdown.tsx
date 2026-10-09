import { createRoot } from "react-dom/client";

import { MarkdownBlock } from "@/lib/markdown";

/**
 * Ответы на странице шаринга приходят сырым текстом. Здесь их рисует тот же
 * `MarkdownBlock`, что и лента чата, — отдельного рендера у шаринга нет.
 */
export function mountShareMarkdown(root: ParentNode = document): void {
  root.querySelectorAll<HTMLElement>(".share-md").forEach((el) => {
    if (el.dataset.mounted === "1") return;
    const text = el.textContent ?? "";
    el.textContent = "";
    el.dataset.mounted = "1";
    createRoot(el).render(<MarkdownBlock>{text}</MarkdownBlock>);
  });
}
