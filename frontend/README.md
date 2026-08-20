# frontend

Чат UI: React + Vite + [@assistant-ui/react](https://www.assistant-ui.com/). Сборка попадает в `frontend/dist` и отдаётся бэкендом как `/chat-ui/`.

## Требования

- **Node.js 24+** (pin в корне репозитория: `.nvmrc`, `engines.node` в `package.json`)

```bash
nvm use                 # из корня репозитория
cd frontend
npm install
```

Из корня:

```bash
make frontend-install
make frontend-build
```

## Скрипты

| Команда | Назначение |
|---------|------------|
| `npm run dev` | Vite на `:5173` (прокси к FastAPI `make run` на `:8080`) |
| `npm run build` | `tsc -b` + production-сборка в `dist/` |
| `npm run test` | Vitest (адаптер модели, геолокация) |
| `npm run test:cov` | то же с покрытием |
| `npm run lint` | oxlint |

Модель чата выбирается в **/settings**, не на странице чата.
