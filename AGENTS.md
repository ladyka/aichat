## Agent Notes

Правила для Cursor и других ИИ-агентов по репозиторию **aichat**.

### Non-negotiables

- Перед правками **читай релевантный код** (`app/`, `templates/`, `static/`), не угадывай API и пути.
- Не коммить и не пушь, пока пользователь явно не попросил; при необходимости предложи текст commit message.
- **Никогда** не добавляй в git commit trailer `Co-authored-by: Cursor <cursoragent@cursor.com>` (и любые другие Cursor/AI co-authored trailers).
- Секреты только в `.env` (не коммитить). `.env` не заливается по FTP (см. `.uploadignore`).
- Вопросы **«почему?» / «why?»** — сначала объяснение; код не менять, пока не попросят исправить.

### Product facts (не ломать без явной просьбы)

- Бэкенд LLM: **OpenRouter**, один серверный `OPENROUTER_API_KEY`.
- Пользователям показываем только **free**-модели; в публичных id **нет** суффикса `:free`.
- Публичная модель `default` → upstream `openrouter/free`.
- UI и API не должны светить `:free` / `openrouter/free` как имя модели наружу.
- Биллинга и лимитов в MVP нет.

### Where things live

| Путь | Назначение |
|------|------------|
| `app/main.py` | FastAPI app, static mount |
| `app/config.py` | env / settings |
| `app/db.py` | SQLAlchemy models, init DB |
| `app/auth.py` | пароли, cookie-сессии, API tokens |
| `app/models_catalog.py` | кеш `/v1/models`, маппинг public ↔ upstream |
| `app/openrouter.py` | HTTP-прокси к OpenRouter |
| `app/routes/pages.py` | лендинг, login/register, chat, tokens |
| `app/routes/api.py` | `/api/chat`, `/v1/*` |
| `templates/`, `static/` | UI |
| `server.py` | entrypoint (uvicorn; `PORT` или `SOCKET`) |
| `scripts/deploy_ftp.py` | `make update-prod` |
| `api_check.py` | проверка API-токена против хоста |

### Stack

- Python, **FastAPI**, Jinja2, vanilla JS
- SQLAlchemy + SQLite (dev) / MySQL (prod при `MYSQL_HOST` + `MYSQL_PASSWORD`)
- Деплой: FTP на shared hosting (ISPmanager), unix socket возможен через `SOCKET`

### Как работать

- Предпочитай простые изменения в духе текущего MVP (без React/Next, без тяжёлого SPA).
- Сохраняй OpenAI-совместимый контракт для `/v1/models` и `/v1/chat/completions`.
- При completions в лог пиши оба имени: `aichat_model=…` и `openrouter_model=…`.
- После изменений API/конфига/запуска — обнови `README.md` (и этот файл, если меняются правила агента).

### Getting started

```bash
cp .env.example .env   # OPENROUTER_API_KEY
make venv && make run  # http://127.0.0.1:8080/
```

Smoke API:

```bash
python3 api_check.py --host http://127.0.0.1:8080 --token aichat_…
```

### Post-task checks

После задач, затрагивающих код:

1. Синтаксис / импорт: `python -m py_compile` по изменённым `.py` или короткий smoke через `make run`.
2. Если трогали API — `api_check.py` или curl на `/v1/models` и completions.
3. В ответе пользователю кратко укажи, что проверено.

Автотестов в репозитории пока нет; не раздувай инфраструктуру тестов без запроса.
