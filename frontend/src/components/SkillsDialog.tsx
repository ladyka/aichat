import { useCallback, useEffect, useState } from "react";
import { Loader2, Sparkles, X } from "lucide-react";
import {
  getConversation,
  listSkills,
  putConversationSkills,
  type SkillSummary,
} from "@/lib/api";

const MAX_ATTACHED = 10;

type SkillsDialogProps = {
  conversationId: string;
  onClose: () => void;
  onChange: (count: number) => void;
};

export function SkillsDialog({
  conversationId,
  onClose,
  onChange,
}: SkillsDialogProps) {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    void (async () => {
      try {
        const [owned, conv] = await Promise.all([
          listSkills(),
          getConversation(conversationId),
        ]);
        if (cancelled) return;
        setSkills(owned);
        setSelected(new Set(conv.skill_ids ?? []));
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Не удалось загрузить навыки");
        setSkills([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < MAX_ATTACHED) next.add(id);
      return next;
    });
  }, []);

  const onSave = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const ids = await putConversationSkills(conversationId, [...selected]);
      onChange(ids.length);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  }, [conversationId, onChange, onClose, selected]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Навыки чата"
    >
      <div
        className="flex max-h-[min(32rem,90vh)] w-full max-w-md flex-col rounded-xl border border-[var(--chat-line)] bg-[var(--chat-panel)] p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-base font-semibold text-[var(--chat-ink)]">
            <Sparkles className="h-4 w-4 text-[var(--chat-muted)]" />
            Навыки этого чата
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-[var(--chat-muted)] hover:bg-black/5"
            aria-label="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mt-2 text-sm text-[var(--chat-muted)]">
          Включённые навыки подмешиваются в запрос модели. Можно менять набор
          по ходу диалога (до {MAX_ATTACHED} сразу).
        </p>

        <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
          {skills === null && (
            <div className="flex items-center gap-2 py-4 text-sm text-[var(--chat-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              Загружаем…
            </div>
          )}
          {skills && skills.length === 0 && !error && (
            <p className="py-3 text-sm text-[var(--chat-muted)]">
              Пока нет навыков.{" "}
              <a className="underline" href="/skills">
                Создайте навык
              </a>{" "}
              или возьмите из{" "}
              <a className="underline" href="/catalog">
                каталога
              </a>
              .
            </p>
          )}
          {skills && skills.length > 0 && (
            <ul className="grid gap-1.5">
              {skills.map((skill) => (
                <li key={skill.id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 hover:bg-black/5">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={selected.has(skill.id)}
                      disabled={
                        selected.size >= MAX_ATTACHED &&
                        !selected.has(skill.id)
                      }
                      onChange={() => toggle(skill.id)}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm text-[var(--chat-ink)]">
                        {skill.title}
                      </span>
                      {skill.description ? (
                        <span className="block text-xs text-[var(--chat-muted)]">
                          {skill.description}
                        </span>
                      ) : null}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>

        {error && (
          <p className="mt-2 text-sm text-[var(--chat-danger)]">{error}</p>
        )}

        <div className="mt-4 flex items-center justify-between gap-2">
          <a
            href="/skills"
            className="text-sm text-[var(--chat-muted)] underline hover:text-[var(--chat-ink)]"
          >
            Мои навыки
          </a>
          <button
            type="button"
            disabled={busy || skills === null}
            onClick={() => void onSave()}
            className="inline-flex items-center justify-center gap-2 rounded-full bg-[var(--chat-accent)] px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}
