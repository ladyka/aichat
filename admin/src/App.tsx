import { useEffect, useState, type FormEvent } from "react";

import { loadAdminModels, resetAdminModels, saveAdminModels } from "./api";
import { buildSavePayload, defaultModelOptions, formFromSnapshot, showAllowedList } from "./save";
import type { AdminForm, AdminSnapshot } from "./types";

function envAllowedText(ids: string[] | null): string {
  if (ids === null) {
    return "ALLOWED_MODELS не задан (без ограничения)";
  }
  return `ALLOWED_MODELS=${ids.join(",")}`;
}

function effectiveAllowedText(data: AdminSnapshot): string {
  if (data.unrestricted) {
    return "разрешены все модели каталога";
  }
  if (data.selected_model_ids.length === 0) {
    return "разрешено только default";
  }
  return `разрешено: ${data.selected_model_ids.join(", ")}`;
}

export function App() {
  const [data, setData] = useState<AdminSnapshot | null>(null);
  const [form, setForm] = useState<AdminForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadAdminModels()
      .then((snapshot) => {
        if (cancelled) return;
        setData(snapshot);
        setForm(formFromSnapshot(snapshot));
      })
      .catch((exc: unknown) => {
        if (cancelled) return;
        setError(exc instanceof Error ? exc.message : "Не удалось загрузить настройки.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function apply(snapshot: AdminSnapshot, notice: boolean) {
    setData(snapshot);
    setForm(formFromSnapshot(snapshot));
    setSaved(notice);
    setError(null);
  }

  async function onSave(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setBusy(true);
    setSaved(false);
    try {
      apply(await saveAdminModels(buildSavePayload(form)), true);
    } catch (exc: unknown) {
      setError(exc instanceof Error ? exc.message : "Не удалось сохранить.");
    } finally {
      setBusy(false);
    }
  }

  async function onReset() {
    setBusy(true);
    setSaved(false);
    try {
      apply(await resetAdminModels(), true);
    } catch (exc: unknown) {
      setError(exc instanceof Error ? exc.message : "Не удалось сбросить настройки.");
    } finally {
      setBusy(false);
    }
  }

  if (!data || !form) {
    return (
      <section className="auth-panel skill-card">
        <h1>Админка</h1>
        {error ? <p className="error">{error}</p> : <p className="muted">Загрузка…</p>}
      </section>
    );
  }

  const listVisible = showAllowedList(form);
  const options = defaultModelOptions(data.catalog_ids, form.defaultModel);

  return (
    <section className="auth-panel skill-card">
      <p className="back-link">
        <a href="/chat">← К чату</a> · <a href="/settings">Настройки</a> · <a href="/profile">Профиль</a>
      </p>
      <h1>Админка</h1>
      <p className="lede">
        Дефолтная модель и список разрешённых. Пока в базе ничего не сохранено — действуют переменные
        окружения.
      </p>
      {error ? <p className="error">{error}</p> : null}
      {saved ? <p className="ok">Сохранено.</p> : null}

      <section className="settings-section">
        <h2 className="section-title" id="models">
          Модели
        </h2>
        <p className="muted">
          Сейчас эффективно: дефолт <code>{data.effective_default_model}</code> · {effectiveAllowedText(data)}
        </p>
        <p className="muted">
          Окружение: <code>DEFAULT_MODEL={data.env_default_model}</code> · {envAllowedText(data.env_allowed_models)}
        </p>

        <form className="stack" id="admin-models-form" onSubmit={onSave}>
          <fieldset className="admin-fieldset">
            <legend>Дефолтная модель</legend>
            <p className="muted">
              Куда указывает публичный id <code>default</code>. То же, что переменная <code>DEFAULT_MODEL</code>.
            </p>
            <label className="check-row">
              <input
                type="checkbox"
                checked={form.useEnvDefault}
                onChange={(event) => setForm({ ...form, useEnvDefault: event.target.checked })}
              />
              Брать из переменной окружения
            </label>
            <label>
              Значение
              <select
                value={form.defaultModel}
                disabled={form.useEnvDefault || busy}
                onChange={(event) => setForm({ ...form, defaultModel: event.target.value })}
              >
                {options.map((id) => (
                  <option key={id} value={id}>
                    {id === "default" ? "default (→ openrouter/free)" : id}
                  </option>
                ))}
              </select>
            </label>
          </fieldset>

          <fieldset className="admin-fieldset">
            <legend>Разрешённые модели</legend>
            <p className="muted">
              Какие публичные id видны в настройках и принимаются API. Алиас <code>default</code> доступен всегда.
            </p>
            <label className="check-row">
              <input
                type="checkbox"
                checked={form.useEnvAllowed}
                onChange={(event) =>
                  setForm({
                    ...form,
                    useEnvAllowed: event.target.checked,
                    unrestricted: event.target.checked ? false : form.unrestricted,
                  })
                }
              />
              Брать из переменной окружения
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={form.unrestricted}
                disabled={form.useEnvAllowed || busy}
                onChange={(event) => setForm({ ...form, unrestricted: event.target.checked })}
              />
              Разрешить все модели каталога
            </label>
            {listVisible ? (
              <div className="check-list">
                {data.catalog_ids.length === 0 ? (
                  <p className="muted">Каталог моделей пуст — проверьте ключи провайдеров.</p>
                ) : (
                  data.catalog_ids.map((id) => (
                    <label className="check-row" key={id}>
                      <input
                        type="checkbox"
                        checked={form.allowed.includes(id)}
                        disabled={busy}
                        onChange={(event) => {
                          const allowed = event.target.checked
                            ? [...form.allowed, id]
                            : form.allowed.filter((item) => item !== id);
                          setForm({ ...form, allowed });
                        }}
                      />
                      {id}
                    </label>
                  ))
                )}
              </div>
            ) : null}
          </fieldset>

          <div className="cta-row">
            <button className="btn" type="submit" disabled={busy}>
              Сохранить
            </button>
            <button className="btn btn-ghost" type="button" disabled={busy} onClick={onReset}>
              Сбросить к окружению
            </button>
          </div>
        </form>
      </section>
    </section>
  );
}
