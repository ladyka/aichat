## Agent Notes

Правила для Cursor и других ИИ-агентов по репозиторию **aichat**.

### Non-negotiables

- Перед правками **читай релевантный код** (`app/`, `frontend/`, `templates/`, `static/`), не угадывай API и пути.
- Не коммить и не пушь, пока пользователь явно не попросил; при необходимости предложи текст commit message.
- **Никогда** не добавляй в git commit trailer `Co-authored-by: Cursor <cursoragent@cursor.com>` (и любые другие Cursor/AI co-authored trailers).
- Секреты только в `.env` (не коммитить). `.env` не заливается по FTP (см. `.uploadignore`).
- Вопросы **«почему?» / «why?»** — сначала объяснение; код не менять, пока не попросят исправить.

### Product facts (не ломать без явной просьбы)

- Бэкенд LLM: **OpenRouter**, один серверный `OPENROUTER_API_KEY`.
- Пользователям показываем только **free**-модели; в публичных id **нет** суффикса `:free`.
- Публичная модель `default` → upstream `openrouter/free`.
- UI и API не должны светить `:free` / `openrouter/free` как имя модели наружу.
- Модель чата выбирается в **/settings** (`User.preferred_model`), не на странице чата.
- Истории чатов хранятся в БД (`Conversation` / `Message`).
- Биллинга и лимитов в MVP нет.

### Where things live

| Путь | Назначение |
|------|------------|
| `app/main.py` | FastAPI app, static + `/chat-ui` mount |
| `app/config.py` | env / settings |
| `app/db.py` | SQLAlchemy models, init DB |
| `app/auth.py` | пароли, cookie-сессии, API tokens |
| `app/models_catalog.py` | кеш `/v1/models`, маппинг public ↔ upstream |
| `app/openrouter.py` | HTTP-прокси к OpenRouter |
| `app/tools.py` | инструменты чата: погода (OpenWeatherMap), дата/время, `download_file`, заказ с pzz.by |
| `app/pzz.py` | клиент публичного API pzz.by (меню, адрес, корзина) |
| `app/oauth.py` | OAuth2/OIDC Google + Apple (authorize-URL, token exchange, проверка id_token) |
| `app/telemetry.py` | Arize/Phoenix OTLP tracing |
| `app/newrelic_telemetry.py` | New Relic agent: APM + авто-форвардинг логов (`NEW_RELIC_*` из `.env`) |
| `app/routes/pages.py` | лендинг, login/register, chat shell, settings, tokens |
| `app/routes/oauth.py` | `/auth/google`, `/auth/apple` и callback'и |
| `app/routes/api.py` | `/api/chat`, `/v1/*` |
| `app/routes/conversations.py` | `/api/conversations*`, `/api/settings` |
| `app/og.py` | Open Graph: абсолютные URL превью, сниппет описания |
| `app/visitors.py` | классификация User-Agent: human / crawler / bot |
| `frontend/` | React + assistant-ui (чат) |
| `frontend/src/tests/` | Vitest-тесты фронтенда (адаптер модели, геолокация) |
| `templates/`, `static/` | Jinja лендинг/auth/tokens/settings |
| `server.py` | entrypoint (uvicorn; `PORT` или `SOCKET`) |
| `scripts/deploy_ftp.py` | `make update-prod` |
| `api_check.py` | проверка API-токена против хоста |
| `tests/` | pytest + `integration_weather.py` (интеграционный тест погоды) |
| `docs/`, `mkdocs.yml` | продуктовая документация (MkDocs Material) |

### Stack

- Python, **FastAPI**, Jinja2 (лендинг / auth / tokens / settings)
- Чат UI: **React + Vite + @assistant-ui/react** в `frontend/`
- SQLAlchemy + SQLite (dev) / MySQL (prod при `MYSQL_HOST` + `MYSQL_PASSWORD`)
- Деплой: FTP на shared hosting (ISPmanager), unix socket возможен через `SOCKET`

### Как работать

- Лендинг/auth/tokens/settings — простые Jinja-страницы; чат — React в `frontend/` (assistant-ui).
- Не тащи React/Next на весь сайт без явной просьбы.
- Сохраняй OpenAI-совместимый контракт для `/v1/models` и `/v1/chat/completions`.
- При completions в лог пиши оба имени: `aichat_model=…` и `openrouter_model=…`.
- После изменений API/конфига/запуска — обнови `README.md` (и этот файл, если меняются правила агента).
- Перед проверкой `/chat`: `make frontend-build` (ассеты в `frontend/dist`, отдаются как `/chat-ui/`).

### Getting started

```bash
cp .env.example .env   # OPENROUTER_API_KEY
make venv
make frontend-install && make frontend-build
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
