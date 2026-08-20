const NOTE_CHANGED = "aichat:note-changed";

export function notifyNoteChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(NOTE_CHANGED));
}

export function onNoteChanged(handler: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  window.addEventListener(NOTE_CHANGED, handler);
  return () => window.removeEventListener(NOTE_CHANGED, handler);
}
