# aichat

Веб-сервис: чат с LLM в браузере и OpenAI-совместимый API-прокси. Бэкенды: OpenRouter (free-модели) и опционально **e7** (Ollama).

## Возможности

- Публичный лендинг без авторизации
- Регистрация / вход (email + пароль, cookie-сессия)
- Вход через **Google** и **Apple** (OAuth 2.0 / OIDC; включается через `.env`, см. ниже)
- UI-чат на **assistant-ui** (React): streaming, сворачиваемый список историй
- Модель чата в **/settings** (не на экране чата)
- API-токены (`aichat_…`) для `POST /v1/chat/completions`
- `GET /v1/models` — список моделей (кеш): free-модели OpenRouter без суффикса `:free`, плюс модели e7 как `e7/<имя>`
- Модель `default` → на OpenRouter уходит `openrouter/free`
- Погодные инструменты в чате (`get_weather` / `get_user_location` через OpenWeatherMap)
- Генерация картинок в чате (`generate_image` → OpenRouter Flux.2 Klein 4B, файлы в S3 Cloud.ru). Нужны `OPENROUTER_API_KEY` и настройки S3; в `/settings` это не модель чата
- Заказ еды с **pzz.by** (Пицца Лисицца): поиск меню, проверка адреса, оформление через чат
- Инструмент `download_file` в чате: скачивает страницы/текстовые файлы по URL (до 2 МБ, только http/https, с защитой от SSRF — недоступны адреса локальной сети), кеширует в `data/customers/<hash(user_id)>/`
- Шаринг чатов по ссылке `/s/<key>`: только просмотр, срок действия, отзыв и лог доступов (IP + время)
- Skills: свои markdown-навыки (приватные по умолчанию), публикация в каталог `/catalog`, копия чужого с `parent_id`, дефолты в `/settings`, набор чата в панели «Навыки»

## Quick Start

### Prerequisites

- Python **3.13+** (pin: `.python-version`, `make venv` → `python3.13`)
- Node.js **24+** (pin: `.nvmrc`, `engines` в `frontend/package.json`)
- Ключ OpenRouter (`OPENROUTER_API_KEY`)
- Для моделей e7 — `E7_BY_BASE_URL` (Ollama)

### Запуск

```bash
cp .env.example .env
# заполните OPENROUTER_API_KEY
# при необходимости: E7_BY_BASE_URL (Ollama e7)

# nvm use   # Node 24 из .nvmrc
make venv                 # python3.13 -m venv .venv
make frontend-install
make frontend-build
make run
```

Приложение: http://127.0.0.1:8080/

Локально по умолчанию используется SQLite (`aichat.db`). MySQL включается, если заданы `MYSQL_HOST` и `MYSQL_PASSWORD`.

Схема БД версионируется **Alembic** (`alembic.ini`, `migrations/`). При старте приложения `init_db()` выполняет `alembic upgrade head`. Новые таблицы и колонки не появляются из `create_all`.

После изменения моделей в `app/db.py`:

```bash
make migrate-rev m="add preferred_model to users"  # черновик ревизии; просмотреть migrations/versions/
make migrate                                       # применить к текущей DATABASE_URL
```

Тесты (pytest + coverage, падает при покрытии < 80 %):

```bash
make test-coverage
```

Тесты фронтенда (Vitest + jsdom, геолокация/SSE-поток адаптера):

```bash
cd frontend && npm run test       # прогон
cd frontend && npm run test:cov   # с покрытием
```

Интеграционный тест погоды (нужен запущенный сервер на `:8080`; запрашивает `Какая погода в Минске?`, проверяет атрибуцию OpenWeatherMap и наличие реальных данных; при ошибке показывает серверный лог, чтобы отличить сбой LLM от сбоя погодного сервиса):

```bash
PYTHONPATH=. .venv/bin/python tests/integration_weather.py
```

Линтеры (flake8 + isort + black):

```bash
make lint      # проверка (flake8, isort --check, black --check)
make format    # автоформатирование (isort + black)
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
| `E7_BY_BASE_URL` | Ollama e7: хост или `.../v1`. Пусто — провайдер выключен |
| `E7_BY_API_KEY` | Опциональный ключ для e7 |
| `E7_BY_TIMEOUT` | Таймаут completions e7 (сек, по умолчанию 300) |
| `DEFAULT_MODEL` | Публичная модель по умолчанию (`default`) |
| `MODELS_CACHE_TTL` | TTL кеша `/v1/models` (сек) |
| `API_DAILY_LIMIT` | Дневной лимит `/v1/chat/completions` на один API-токен (по умолчанию `10`) |
| `MAX_TOKENS_PER_USER` | Максимум активных API-токенов на пользователя (по умолчанию `10`) |
| `SHARE_TTL_DAYS` | Срок действия ссылки на общий чат `/s/<key>` (по умолчанию `30`) |
| `OPENWEATHER_API_KEY` | Ключ OpenWeatherMap: включает инструменты погоды `get_weather` и `get_user_location` в `/api/chat`. Пусто — инструменты отключены |
| `PZZ_ENABLED` | Инструменты pzz.by в `/api/chat` (`pzz_search_menu`, `pzz_lookup_address`, `pzz_place_order`). По умолчанию включены (`1`) |
| `PZZ_ORDERS_ENABLED` | Разрешить реальную отправку заказа на pzz.by (`confirm=true`). `0` — только черновик и ссылка на сайт |
| `DOWNLOADS_MAX_BYTES` | Лимит размера файла для `download_file` (по умолчанию `2097152` = 2 МБ) |
| `S3_ENDPOINT` | S3 API, прод Cloud.ru: `https://s3.cloud.ru`. Вместе с bucket и ключами включает `generate_image` |
| `S3_REGION` | Регион SigV4 (по умолчанию `ru-central-1`) |
| `S3_BUCKET` | Имя bucket (создать заранее) |
| `S3_PATH_STYLE` | `1` — path-style (`{endpoint}/{bucket}/{key}`), как у Cloud.ru |
| `S3_PUBLIC_BASE_URL` | Префикс публичных URL (без повторного имени bucket), например `https://<bucket>.s3.cloud.ru` |
| `S3_SA_KEY_ID` / `S3_SA_KEY_SECRET` | Ключи Cloud.ru как есть (не `AWS_ACCESS_KEY_*`) |
| `IMAGE_GENERATION_MODEL` | Модель OpenRouter Images (по умолчанию `black-forest-labs/flux.2-klein-4b`) |
| `IMAGE_GENERATION_DAILY_LIMIT` | Картинок на пользователя в сутки UTC (по умолчанию `5`) |
| `MYSQL_*` / `DATABASE_URL` | БД (иначе SQLite) |
| `INSTANCE_HOST` / `PORT` / `SOCKET` | Слушатель (порт или unix socket для хостинга) |
| `PUBLIC_BASE_URL` | Публичный https-адрес сервиса (например `https://aichat.example.com`); redirect URI OAuth и абсолютные URL превью ссылок (`og:image`, `og:url`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth-клиент Google (выкл., пока не заполнены оба) |
| `APPLE_CLIENT_ID` / `APPLE_TEAM_ID` / `APPLE_KEY_ID` / `APPLE_PRIVATE_KEY` | «Sign in with Apple» (выкл., пока не заполнены все) |
| `FTP_*` | Деплой через `make update-prod` |
| `ARIZE_SPACE_ID` / `ARIZE_API_KEY` | Включить OTLP-трейсы (Arize / Phoenix) |
| `ARIZE_PROJECT_NAME` | Имя проекта в коллекторе (по умолчанию `aichat`) |
| `ARIZE_OTLP_ENDPOINT` | OTLP endpoint. Arize cloud gRPC: `https://otlp.<region>.arize.com/v1`; HTTPS: `https://otlp.<region>.arize.com/v1/traces`; локальный Phoenix: `http://127.0.0.1:6006/v1/traces`. Транспорт (gRPC/HTTP) выбирается автоматически: HTTP — для `http://` и путей `/v1/traces`, иначе gRPC (как в `example/aichat`) |
| `NEW_RELIC_LICENSE_KEY` | Включить New Relic: APM-метрики + автоматический форвардинг логов (`logging`) |
| `NEW_RELIC_USER_KEY` | Ключ пользователя New Relic для запросов к API (GraphQL); самому агенту не нужен |
| `NEW_RELIC_APP_NAME` | Имя приложения в New Relic (по умолчанию `aichat`) |

### OAuth-вход (Google / Apple)

Фича отключена, пока в `.env` не заполнены все соответствующие переменные. Обязателен `PUBLIC_BASE_URL` — по нему строятся redirect URI:

- Google: `{PUBLIC_BASE_URL}/auth/google/callback` → указать в OAuth-клиенте Google (Authorized redirect URIs).
- Apple: `{PUBLIC_BASE_URL}/auth/apple/callback` → указать в «Sign in with Apple» Service ID. Для Apple нужен платный Developer Account: Service ID + Team ID + Key ID + содержимое `.p8`-файла ключа (в `APPLE_PRIVATE_KEY`).

Новые пользователи создаются автоматически по email из провайдера; если email совпадает с существующим — вход в тот же аккаунт. Привязка провайдера хранится в таблице `oauth_identities`. OAuth-аккаунты пароль не имеют — вход по email+пароль для них недоступен.

Трейсы смотрим в [Arize app](https://app.ca-central-1a.arize.com/).

Погода в чате (`/api/chat`): модель может запросить `get_weather` по городу или координатам. Если локация не указана — модель вызывает `get_user_location`, и фронтенд запрашивает доступ к геолокации браузера (`navigator.geolocation`); координаты после разрешения передаются в чат и кешируются на 2 часа. **Геолокация работает только по HTTPS** (или `localhost`) — иначе браузер не даст доступ, и модель попросит назвать город текстом.

Пицца Лисицца (`pzz.by`) в том же `/api/chat`: модель ищет меню (`pzz_search_menu`), проверяет улицу/дом в их справочнике (`pzz_lookup_address`) и может отправить заказ (`pzz_place_order`). Официального партнёрского API нет — используется тот же JSON, что и у сайта (каталог `GET /api/v1/{pizzas|snacks|…}`, заказ через cookie-сессию, корзину и `POST /api/v1/basket/save`). Через чат уходит только оплата **наличными курьеру**; онлайн-оплата bePaid в боте не проводится. Отправка на кухню — только после явного согласия пользователя (`confirm=true`). Имя, телефон и адрес передаются на pzz.by. Выключить меню: `PZZ_ENABLED=0`; запретить отправку, оставив подбор состава: `PZZ_ORDERS_ENABLED=0`.

Заметка чата: на ПК экран делится (чат слева, markdown справа: исходник / просмотр, скачивание `.md`). Модель в `/api/chat` может читать и писать заметку текущего диалога (`read_chat_note`, `write_chat_note`); в публичный `/v1` эти tools не попадают.

Картинки (`generate_image`) только в `/api/chat`: модель вызывает tool, бэкенд ходит в OpenRouter `POST /api/v1/images` (`black-forest-labs/flux.2-klein-4b`) и кладёт PNG в S3. В ответ пользователю — markdown с публичным URL. Без S3 tool не рекламируется. Биллинга нет; есть суточный лимит. API-токены (`/v1/chat/completions`) этот tool не получают.

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
| `GET/PUT` | `/api/conversations/{id}/note` | cookie-сессия |
| `GET` | `/api/conversations/{id}/note/download` | cookie-сессия (файл `.md`) |
| `GET/POST` | `/api/conversations/{id}/share` | cookie-сессия |
| `POST` | `/api/conversations/{id}/share/revoke` | cookie-сессия |
| `GET` | `/s/{key}` | нет (публичная страница чтения; Open Graph для превью в мессенджерах; заходы пишутся в `share_accesses` с `visitor_kind`: human / crawler / bot) |

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
app/           # FastAPI: auth, DB, model_providers, tools, routes
migrations/    # Alembic: ревизии схемы (alembic.ini в корне)
frontend/      # React + assistant-ui (сборка → frontend/dist → /chat-ui/; тесты в frontend/src/tests)
templates/     # Jinja2: лендинг, auth, settings, tokens, skills, catalog, chat shell
static/        # CSS
docs/          # MkDocs: стек/runtime + продукт / видение
tests/         # pytest + integration_weather.py (интеграционный тест погоды)
server.py      # entrypoint (uvicorn, port или SOCKET)
scripts/       # FTP deploy
api_check.py   # smoke-тест API
mkdocs.yml     # конфиг документации
.flake8        # flake8 (100 символов; E203/W503 выключены — конфликт с black)
pyproject.toml # requires-python 3.13+, конфиг black + isort
.python-version # pin CPython 3.13 (pyenv / uv)
.nvmrc         # pin Node.js 24 (nvm / fnm)
requirements-dev.txt # инструменты разработки (линтеры)
```

## Деплой (hoster / FTP)

```bash
make update-prod   # собирает frontend, затем FTP
```

Сборка чата (`make update-prod` / `make frontend-build`) — на **Node.js 24+**. На сервере: **Python 3.13+**, зависимости в `.venv` (включая Alembic), `.env` с секретами (не заливается по FTP), перезапуск Python-приложения в панели хостинга — при старте применятся миграции.

## Для агентов

См. [`AGENTS.md`](AGENTS.md).
