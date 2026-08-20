# План: заметки в чате

Заметка — markdown-документ пользователя. В UI первой версии у открытого чата видна **одна** заметка; в БД связь чат ↔ заметка **многие-ко-многим**, чтобы позже не мигрировать схему.

## Решения (зафиксировано)

| Тема | Решение v1 |
|------|------------|
| Связь в БД | M2M: `notes` (владелец — пользователь) + `conversation_notes` |
| UI на ПК | Две колонки: слева чат, справа заметка |
| Сколько заметок видно | Одна на открытый чат (вкладки/дерево/список — нет) |
| Редактор | Переключение «исходник» / «просмотр», без split колонки заметки |
| Скачать | `.md` из шапки колонки |
| Шаринг заметки | Нет |
| Список всех заметок | Нет |
| LLM | Tools `read_chat_note` / `write_chat_note` только в UI-чате; тело не класть в каждый system prompt |
| Публичный `/v1` | Заметки и note-tools не отдаём |

## Вне скоупа (отдельные задачи)

- Несколько вкладок / дерево заметок.
- Привязать ту же заметку к другому чату (схема уже позволит).
- Публичная ссылка на заметку.
- Глобальный список заметок.
- Split исходник+превью внутри правой колонки.
- Note-tools в `/v1`.
- Совместное редактирование, конфликт-merge.

## Схема БД

Новые таблицы. Ревизия **только Alembic** (`make migrate-rev`, не `create_all` / ручной `ALTER`).

```sql
CREATE TABLE notes (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    title       VARCHAR(200) NOT NULL DEFAULT 'Заметка',
    body        TEXT NOT NULL DEFAULT '',  -- MySQL: MEDIUMTEXT
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE conversation_notes (
    conversation_id INTEGER NOT NULL,
    note_id         INTEGER NOT NULL,
    created_at      DATETIME NOT NULL,
    PRIMARY KEY (conversation_id, note_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
    FOREIGN KEY (note_id)         REFERENCES notes (id) ON DELETE CASCADE
);
```

Инварианты в коде:

- Связь только если `notes.user_id == conversations.user_id`.
- Удаление чата снимает строки в `conversation_notes`, строка в `notes` остаётся.
- Удаление заметки снимает все её связи.
- Нет `UNIQUE(conversation_id)`: это сломало бы M2M. «Одна заметка на чат» — правило API/UI v1: брать последнюю связь или создавать одну при первом PUT.

После моделей: `make migrate-rev m="add notes"` → просмотреть `migrations/versions/` → `make migrate`. В `tests/test_migrations.py` добавить `notes` и `conversation_notes` в `EXPECTED_TABLES`.

## Порядок работ

### 1. Модели и миграция

Файлы: `app/db.py`, `migrations/versions/…`, `tests/test_migrations.py`.

- `Note`: `user_id`, `title`, `body`, timestamps; relationship на `User` и на association.
- `ConversationNote`: составной PK `(conversation_id, note_id)`, `created_at`.
- Relationships на `Conversation` / `User` с понятным cascade (чат → orphan association, не orphan note).

Проверка: `make migrate` на пустой SQLite; `pytest tests/test_migrations.py`.

### 2. HTTP API (сессия cookie)

Файлы: `app/routes/conversations.py` (или новый `app/routes/notes.py` + include в `app/main.py`), `tests/test_notes.py` по образцу `tests/test_conversations.py`.

Контракт v1 (одна заметка на чат):

| Метод | Путь | Поведение |
|-------|------|-----------|
| `GET` | `/api/conversations/{id}/note` | Заметка, привязанная к чату. Нет связи → `404` или `{ "note": null }` (выбрать одно и покрыть тестом). |
| `PUT` | `/api/conversations/{id}/note` | Создать заметку + связь, если нет; иначе обновить `title`/`body` существующей. Тело: `{ "title"?, "body" }`. |
| `GET` | `/api/conversations/{id}/note/download` | `text/markdown`, `Content-Disposition: attachment`, имя `{title}.md`. |

Доступ: тот же `_require_user` + владение чатом, что у conversations. Чужой id → 404. Лимит длины `body` (ориентир: сотни KB). Автосейв на клиенте = PUT с debounce.

Не делать в этом шаге: `GET /api/notes`, attach/detach, share.

### 3. UI ПК: колонка заметки

Файлы: `frontend/src/lib/api.ts`, `frontend/src/App.tsx`, новые компоненты в `frontend/src/components/` (например `NotePane.tsx`). Не Jinja, не `@assistant-ui` runtime.

Поведение:

- Выбран диалог, заметка пустая/отсутствует → чат на всю ширину, в шапке кнопка «Заметка».
- Есть текст **или** пользователь открыл колонку → split: слева `Thread`, справа панель.
- Шапка панели: title (editable), переключатель «Текст» / «Просмотр», скачать, свернуть.
- «Текст» — `textarea`. «Просмотр» — `react-markdown` + `remark-gfm` (как в `Thread.tsx`).
- Debounce PUT ~500–1000 ms.
- Мобилка: панель поверх чата (грубо), не двухколоночный split.

После UI: `make frontend-build`, `cd frontend && npm run test`.

### 4. Tools для модели

Файлы: `app/tools.py`, `app/routes/api.py` (прокинуть `conversation_id` в `call_tool`), `tests/test_tools.py`.

- Только `source == "chat"`, не `/v1`.
- `read_chat_note` — title + body или «заметки нет».
- `write_chat_note` — `title?`, `body`, `mode: replace | append`; создаёт связь, если её не было.
- Коротко в system/tool description: у чата есть заметка, читать/писать через эти tools — **без** вставки полного body в каждый запрос.
- После стрима UI делает `GET` заметки, чтобы колонка не отстала от записи модели.
- Last-write-wins при гонке человек/модель.

`call_tool` сейчас не знает чат: расширить сигнатуру (`conversation_id` из body `/api/chat`).

### 5. Документация и агентские правила

- `README.md`: коротко про заметки (API + UI).
- `AGENTS.md`: строка в таблице путей (`app/routes/notes.py` если вынесен; tools).
- Этот документ оставить как спецификацию v1; по факту реализации поправить расхождения.

## Критерии готовности v1

- Пустая БД и legacy-БД проходят `tests/test_migrations.py` с новыми таблицами.
- Pytest: CRUD заметки, 401, чужой чат, download, удаление чата не удаляет `notes`.
- На ПК при непустой заметке экран делится; переключение исходник/просмотр; файл скачивается.
- В UI-чате модель может прочитать и перезаписать заметку текущего диалога.
- Нет UI списка, шаринга, вкладок.

## Рекомендуемый порядок коммитов / PR

Один PR допустим, если куски независимы по файлам; иначе три: (1) миграция+модели, (2) API+тесты, (3) UI+tools.

Не смешивать с шарингом чатов и не трогать OpenAI-контракт `/v1/models` и `/v1/chat/completions`.
