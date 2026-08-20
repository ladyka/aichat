# aichat

Веб-сервис: чат с LLM в браузере и OpenAI-совместимый API-прокси. Бэкенды: OpenRouter (free-модели) и опционально **e7.by** (Ollama).

## Возможности

- Публичный лендинг без авторизации
- Регистрация / вход (email + пароль, cookie-сессия)
- UI-чат на **assistant-ui** (React): streaming, сворачиваемый список историй
- Модель чата в **/settings** (не на экране чата)
- API-токены (`aichat_…`) для `POST /v1/chat/completions`
- `GET /v1/models` — список моделей (кеш): free-модели OpenRouter без суффикса `:free`, плюс модели e7.by как `e7.by/<имя>`
- Модель `default` → на OpenRouter уходит `openrouter/free`

## Quick Start

### Prerequisites

- Python 3.11+ (локально проверено на 3.13/3.14)
- Node.js 20+ (сборка чата)
- Ключ OpenRouter (`OPENROUTER_API_KEY`)
- Для моделей e7.by — `E7_BY_BASE_URL` (Ollama)

### Запуск

```bash
cp .env.example .env
# заполните OPENROUTER_API_KEY
# при необходимости: E7_BY_BASE_URL (Ollama e7.by)

make venv
make frontend-install
make frontend-build
make run
```

Приложение: http://127.0.0.1:20000/  (локальный порт — `~/development/PORTS.md`)

Локально по умолчанию используется SQLite (`aichat.db`). MySQL включается, если заданы `MYSQL_HOST` и `MYSQL_PASSWORD`.

Dev чата отдельно (прокси на FastAPI):

```bash
make run                 # :20000
cd frontend && npm run dev   # :5173, прокси на :20000
```

## Конфигурация

См. [`.env.example`](.env.example):

| Переменная | Назначение |
|------------|------------|
| `OPENROUTER_API_KEY` | Серверный ключ OpenRouter |
| `E7_BY_BASE_URL` | Ollama e7.by: хост или `.../v1`. Пусто — провайдер выключен |
| `E7_BY_API_KEY` | Опциональный ключ для e7.by |
| `E7_BY_TIMEOUT` | Таймаут completions e7.by (сек, по умолчанию 300) |
| `DEFAULT_MODEL` | Публичная модель по умолчанию (`default`) |
| `MODELS_CACHE_TTL` | TTL кеша `/v1/models` (сек) |
| `MYSQL_*` / `DATABASE_URL` | БД (иначе SQLite) |
| `INSTANCE_HOST` / `PORT` / `SOCKET` | Слушатель. Локально `PORT=20000` (см. `~/development/PORTS.md`); на хостинге часто `SOCKET` |
| `FTP_*` | Деплой через `make update-prod` |
| `ARIZE_SPACE_ID` / `ARIZE_API_KEY` | Включить OTLP-трейсы (Arize / Phoenix) |
| `ARIZE_PROJECT_NAME` | Имя проекта в коллекторе (по умолчанию `aichat`) |
| `ARIZE_OTLP_ENDPOINT` | OTLP endpoint, напр. `http://127.0.0.1:6006/v1/traces` для Phoenix |

## API (кратко)

| Метод | Путь | Auth |
|-------|------|------|
| `GET` | `/v1/models` | нет |
| `POST` | `/v1/chat/completions` | `Authorization: Bearer aichat_…` |
| `POST` | `/api/chat` | cookie-сессия (UI) |
| `GET` | `/api/models` | cookie-сессия (UI) |
| `GET/PUT` | `/api/settings` | cookie-сессия |
| `*` | `/api/conversations…` | cookie-сессия |

Проверка токена против хоста:

```bash
python3 api_check.py --host https://YOUR_HOST --token aichat_…
```

## Документация

Продуктовые заметки (видение, MVP): каталог [`docs/`](docs/), сборка MkDocs Material.

```bash
make docs-serve   # http://127.0.0.1:8000/  (нужен Docker)
make docs-build   # strict build в ./site/
```

## Структура

```
app/           # FastAPI: auth, DB, model_providers, routes
frontend/      # React + assistant-ui (сборка → frontend/dist → /chat-ui/)
templates/     # Jinja2: лендинг, auth, settings, tokens, chat shell
static/        # CSS
docs/          # MkDocs (продукт / видение)
server.py      # entrypoint (uvicorn, port или SOCKET)
scripts/       # FTP deploy
api_check.py   # smoke-тест API
mkdocs.yml     # конфиг документации
```

## Деплой (hoster / FTP)

```bash
make update-prod   # собирает frontend, затем FTP
```

На сервере: зависимости в `.venv`, `.env` с секретами (не заливается по FTP), перезапуск Python-приложения в панели хостинга.

## Для агентов

См. [`AGENTS.md`](AGENTS.md).
