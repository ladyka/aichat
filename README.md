# aichat

Веб-сервис: чат с LLM в браузере и OpenAI-совместимый API-прокси на базе OpenRouter (только free-модели).

## Возможности

- Публичный лендинг без авторизации
- Регистрация / вход (email + пароль, cookie-сессия)
- UI-чат на **assistant-ui** (React): streaming, сворачиваемый список историй
- Модель чата в **/settings** (не на экране чата)
- API-токены (`aichat_…`) для `POST /v1/chat/completions`
- `GET /v1/models` — список free-моделей (кеш), публичные id без суффикса `:free`
- Модель `default` → на OpenRouter уходит `openrouter/free`
- Шаринг чатов по ссылке `/s/<key>`: только просмотр, срок действия, отзыв и лог доступов (IP + время)

## Quick Start

### Prerequisites

- Python 3.11+ (локально проверено на 3.13/3.14)
- Node.js 20+ (сборка чата)
- Ключ OpenRouter (`OPENROUTER_API_KEY`)

### Запуск

```bash
cp .env.example .env
# заполните OPENROUTER_API_KEY

make venv
make frontend-install
make frontend-build
make run
```

Приложение: http://127.0.0.1:8080/

Локально по умолчанию используется SQLite (`aichat.db`). MySQL включается, если заданы `MYSQL_HOST` и `MYSQL_PASSWORD`.

Тесты (pytest + coverage, падает при покрытии < 80 %):

```bash
make test-coverage
```

Dev чата отдельно (прокси на FastAPI):

```bash
make run                 # :8080
cd frontend && npm run dev   # :5173
```

## Конфигурация

См. [`.env.example`](.env.example):

| Переменная | Назначение |
|------------|------------|
| `OPENROUTER_API_KEY` | Серверный ключ OpenRouter |
| `DEFAULT_MODEL` | Публичная модель по умолчанию (`default`) |
| `MODELS_CACHE_TTL` | TTL кеша `/v1/models` (сек) |
| `API_DAILY_LIMIT` | Дневной лимит `/v1/chat/completions` на один API-токен (по умолчанию `10`) |
| `MAX_TOKENS_PER_USER` | Максимум активных API-токенов на пользователя (по умолчанию `10`) |
| `SHARE_TTL_DAYS` | Срок действия ссылки на общий чат `/s/<key>` (по умолчанию `30`) |
| `OPENWEATHER_API_KEY` | Ключ OpenWeatherMap: включает инструменты погоды `get_weather` и `get_user_location` в `/api/chat`. Пусто — инструменты отключены |
| `MYSQL_*` / `DATABASE_URL` | БД (иначе SQLite) |
| `INSTANCE_HOST` / `PORT` / `SOCKET` | Слушатель (порт или unix socket для хостинга) |
| `FTP_*` | Деплой через `make update-prod` |
| `ARIZE_SPACE_ID` / `ARIZE_API_KEY` | Включить OTLP-трейсы (Arize / Phoenix) |
| `ARIZE_PROJECT_NAME` | Имя проекта в коллекторе (по умолчанию `aichat`) |
| `ARIZE_OTLP_ENDPOINT` | OTLP endpoint. Arize cloud gRPC: `https://otlp.<region>.arize.com/v1`; HTTPS: `https://otlp.<region>.arize.com/v1/traces`; локальный Phoenix: `http://127.0.0.1:6006/v1/traces`. Транспорт (gRPC/HTTP) выбирается автоматически: HTTP — для `http://` и путей `/v1/traces`, иначе gRPC (как в `example/aichat`) |

Трейсы смотрим в [Arize app](https://app.ca-central-1a.arize.com/).

Погода в чате (`/api/chat`): модель может запросить `get_weather` по городу или координатам. Если локация не указана — модель вызывает `get_user_location`, и фронтенд запрашивает доступ к геолокации браузера (`navigator.geolocation`); координаты после разрешения передаются в чат и кешируются на 2 часа. **Геолокация работает только по HTTPS** (или `localhost`) — иначе браузер не даст доступ, и модель попросит назвать город текстом.

## API (кратко)

| Метод | Путь | Auth |
|-------|------|------|
| `GET` | `/v1/models` | нет |
| `POST` | `/v1/chat/completions` | `Authorization: Bearer aichat_…` |

Ограничения: каждый API-токен — до `API_DAILY_LIMIT` запросов к `/v1/chat/completions` в сутки (UTC); при превышении возвращается `429` с текстом ошибки. Запросы из чата (`/api/chat`) в этот лимит не входят. На пользователя — не более `MAX_TOKENS_PER_USER` активных токенов.
| `POST` | `/api/chat` | cookie-сессия (UI) |
| `GET` | `/api/models` | cookie-сессия (UI) |
| `GET/PUT` | `/api/settings` | cookie-сессия |
| `*` | `/api/conversations…` | cookie-сессия |
| `GET/POST` | `/api/conversations/{id}/share` | cookie-сессия |
| `POST` | `/api/conversations/{id}/share/revoke` | cookie-сессия |
| `GET` | `/s/{key}` | нет (публичная страница чтения) |

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
app/           # FastAPI: auth, DB, OpenRouter, routes
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
