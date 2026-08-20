# Стек и runtime

Текущий MVP `aichat` собирается и запускается на **Python 3.13+** и **Node.js 24+**. Более старые версии не поддерживаются.

## Python 3.13

| Pin | Где |
|-----|-----|
| `requires-python = ">=3.13"` | `pyproject.toml` |
| `3.13` | `.python-version` (pyenv / uv) |
| Black `target-version = py313` | `pyproject.toml` |
| `python3.13 -m venv` | `make venv` (`PYTHON_BIN`, по умолчанию `python3.13`) |

Локально:

```bash
python3.13 --version   # 3.13.x
make venv              # .venv на CPython 3.13
make lint
make test-coverage
```

На проде (shared hosting / ISPmanager): интерпретатор приложения и `.venv` должны быть **3.13+**. После смены версии Python окружение пересоздают, зависимости ставят заново, затем перезапускают процесс.

## Node.js 24

Нужен только для сборки и разработки чата (`frontend/`). В runtime приложения уходит статика из `frontend/dist` (`/chat-ui/`).

| Pin | Где |
|-----|-----|
| `24` | `.nvmrc` (nvm / fnm) |
| `engines.node = ">=24"` | `frontend/package.json` |

```bash
nvm use                 # читает .nvmrc → Node 24
node --version          # v24.x
make frontend-install
make frontend-build     # ассеты в frontend/dist
cd frontend && npm run test
```

Dev-сервер чата (прокси на FastAPI):

```bash
make run                    # :8080
cd frontend && npm run dev  # :5173
```

## Что не меняется

- Бэкенд LLM: OpenRouter (free-модели) и опционально e7 (Ollama).
- Публичные id моделей без суффикса `:free`; `default` → upstream `openrouter/free`.
- Модель чата выбирается в `/settings`, не на странице чата.

Подробности запуска, env и API — в `README.md` в корне репозитория.
