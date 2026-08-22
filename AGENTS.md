## Agent Notes

Правила для Cursor и других ИИ-агентов по репозиторию **aichat**.

### Non-negotiables

- Перед правками **читай релевантный код** (`app/`, `frontend/`, `templates/`, `static/`), не угадывай API и пути.
- Не коммить и не пушь, пока пользователь явно не попросил; при необходимости предложи текст commit message.
- **Никогда** не добавляй в git commit trailer `Co-authored-by: Cursor <cursoragent@cursor.com>` (и любые другие Cursor/AI co-authored trailers).
- Секреты только в `.env` (не коммитить). `.env` не заливается по FTP (см. `.uploadignore`).
- Вопросы **«почему?» / «why?»** — сначала объяснение; код не менять, пока не попросят исправить.

### Product facts (не ломать без явной просьбы)

- Бэкенд LLM: **OpenRouter** (серверный `OPENROUTER_API_KEY`) и опционально **e7** (Ollama, `E7_BY_BASE_URL`).
- Пользователям показываем только **free**-модели OpenRouter; в публичных id **нет** суффикса `:free`.
- Публичная модель `default` → upstream `openrouter/free`.
- Модели e7 в публичных id: `e7/<ollama-model>` (`owned_by`: `e7`).
- UI и API не должны светить `:free` / `openrouter/free` как имя модели наружу.
- Модель чата выбирается в **/settings** (`User.preferred_model`), не на странице чата.
- Истории чатов хранятся в БД (`Conversation` / `Message`).
- У чата в UI одна markdown-заметка (в БД M2M `notes` ↔ `conversations`); шаринга заметок нет.
- Skills приватны по умолчанию. В каталоге только опубликованные. В чат и в дефолты — только свои (чужой — через копию). Набор чата можно менять по ходу диалога.
- Биллинга в MVP нет. У `generate_image` есть суточный лимит (`IMAGE_GENERATION_DAILY_LIMIT`), полный ledger — позже (`TODO.md`).

### Where things live

| Путь | Назначение |
|------|------------|
| `app/main.py` | FastAPI app, static + `/chat-ui` mount |
| `app/config.py` | env / settings |
| `app/db.py` | SQLAlchemy models; `init_db()` гоняет Alembic `upgrade head` |
| `skills` / `user_skill_defaults` / `conversation_skills` / `skill_shares` | Skills: свой markdown, копия с `parent_id`, дефолты новых чатов, набор чата (можно менять), шаринг в каталог |
| `alembic.ini`, `migrations/` | Ревизии схемы БД (не `create_all` / ручной `ALTER`) |
| `app/auth.py` | пароли, cookie-сессии, API tokens |
| `app/models_catalog.py` | кеш `/v1/models`, маппинг public ↔ upstream, маршрутизация провайдеров |
| `app/model_providers/` | HTTP-прокси к LLM: OpenRouter, e7 (Ollama); OpenRouter ещё `POST /images` |
| `app/storage.py` | S3 Cloud.ru: PutObject + публичный URL |
| `app/tools.py` | инструменты чата: погода, дата/время, `download_file`, заметка чата, pzz.by, `generate_image` |
| `app/notes.py` | CRUD markdown-заметки чата (M2M `notes` / `conversation_notes`) |
| `app/skills.py` | CRUD skills, публикация/каталог/копия, дефолты, набор чата |
| `app/routes/skills.py` | `/api/skills*`, `/api/catalog/skills*` |
| `app/pzz.py` | клиент публичного API pzz.by (меню, адрес, корзина) |
| `app/oauth.py` | OAuth2/OIDC Google + Apple (authorize-URL, token exchange, проверка id_token) |
| `app/telemetry.py` | Arize/Phoenix OTLP tracing |
| `app/newrelic_telemetry.py` | New Relic agent: APM + авто-форвардинг логов (`NEW_RELIC_*` из `.env`) |
| `app/routes/pages.py` | лендинг, login/register, chat shell, settings, tokens |
| `app/routes/skill_pages.py` | `/skills`, `/catalog` (Jinja) |
| `app/routes/oauth.py` | `/auth/google`, `/auth/apple` и callback'и |
| `app/routes/api.py` | `/api/chat`, `/v1/*` |
| `app/routes/conversations.py` | `/api/conversations*`, `/api/settings` (модель + `default_skill_ids`) |
| `app/routes/notes.py` | `/api/conversations/{id}/note` (GET/PUT) и download |
| `app/og.py` | Open Graph: абсолютные URL превью, сниппет описания |
| `app/visitors.py` | классификация User-Agent: human / crawler / bot |
| `frontend/` | React + assistant-ui (чат) |
| `frontend/src/tests/` | Vitest-тесты фронтенда (адаптер модели, геолокация) |
| `templates/`, `static/` | Jinja лендинг/auth/tokens/settings |
| `server.py` | entrypoint (uvicorn; `PORT` или `SOCKET`) |
| `scripts/deploy_ftp.py` | `make update-prod` |
| `api_check.py` | проверка API-токена против хоста |
| `tests/` | pytest + `integration_weather.py` (интеграционный тест погоды) |
| `docs/`, `mkdocs.yml` | документация (MkDocs Material): runtime + продукт |
| `.python-version` | pin CPython 3.13 |
| `.nvmrc` | pin Node.js 24 |

### Stack

- Python **3.13+**, **FastAPI**, Jinja2 (лендинг / auth / tokens / settings)
- Чат UI: **React + Vite + @assistant-ui/react** в `frontend/` (Node.js **24+**)
- SQLAlchemy + Alembic + SQLite (dev) / MySQL (prod при `MYSQL_HOST` + `MYSQL_PASSWORD`)
- Деплой: FTP на shared hosting (ISPmanager), unix socket возможен через `SOCKET`

### Как работать

- Лендинг/auth/tokens/settings — простые Jinja-страницы; чат — React в `frontend/` (assistant-ui).
- Не тащи React/Next на весь сайт без явной просьбы.
- Сохраняй OpenAI-совместимый контракт для `/v1/models` и `/v1/chat/completions`.
- При completions в лог пиши: `aichat_model=…` `provider=…` `upstream_model=…`.
- После изменений API/конфига/запуска — обнови `README.md` (и этот файл, если меняются правила агента).
- Перед проверкой `/chat`: `make frontend-build` (ассеты в `frontend/dist`, отдаются как `/chat-ui/`).

### Getting started

```bash
cp .env.example .env   # OPENROUTER_API_KEY; опционально E7_BY_BASE_URL
make venv              # python3.13 -m venv .venv
make frontend-install && make frontend-build   # Node 24+
make run  # http://127.0.0.1:8080/
```

Smoke API:

```bash
python3 api_check.py --host http://127.0.0.1:8080 --token aichat_…
```

### Post-task checks

После задач, затрагивающих код:

1. Синтаксис / импорт: `python -m py_compile` по изменённым `.py` или короткий smoke через `make run`.
2. Линтеры: `make lint` (flake8 + isort + black). Перед форматированием всего репозитория уточни у пользователя (или приведи `make format`).
3. Если трогали чат UI — `make frontend-build`.
4. Если трогали API — `api_check.py` или curl на `/v1/models` и completions.
5. В ответе пользователю кратко укажи, что проверено.

Автотесты: pytest (`make test-coverage`), Vitest во `frontend/src/tests/` (`cd frontend && npm run test`), интеграционный тест погоды (`tests/integration_weather.py`).
