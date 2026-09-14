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

## PWA

Чат-UI устанавливается на телефон и десктоп: `frontend/src/sw.ts` (service worker) собирается
`vite-plugin-pwa` в режиме `injectManifest` в `dist/sw.js`, манифест — `frontend/public/manifest.webmanifest`.

Что важно знать:

- **Устанавливается только чат.** PWA-теги живут в `templates/chat.html`, а не в `base.html`:
  остальные страницы сайта установить нельзя.
- **`start_url` = `/chat`**, а service worker лежит в корне сайта (`/sw.js`), потому что настоящая
  страница чата — Jinja-шаблон, а не `dist/index.html` (он в проде не отдаётся). Из `/chat-ui/sw.js`
  scope был бы ограничен `/chat-ui/` и навигацию на `/chat` SW не контролировал бы. Роуты `/sw.js`
  и `/manifest.webmanifest` — в `app/routes/pages.py`.
- **В `npm run dev` service worker'а нет** (регистрация под `import.meta.env.PROD`), поэтому PWA
  проверяется только на собранной версии: `make frontend-build`, затем `make run`.
- **Офлайн читаются прошлые диалоги.** GET-ответы `/api/(conversations|settings|skills)` кешируются
  (`NetworkFirst`, 7 дней, до 300 записей), поэтому открытый офлайн чат показывает то, что уже
  загружалось. Персональный HTML `/chat` (в нём email пользователя) в кеш **не** попадает: офлайн
  навигация отдаёт статическую оболочку `dist/index.html`.
- **Кеш чистится на выходе** — по `/logout` и по любому 401. Но если закрыть браузер без выхода,
  данные остаются на устройстве до истечения 7 дней: на общем компьютере выходите из аккаунта.

### Иконки

`scripts/generate_pwa_icons.py` (стандартная библиотека, без Pillow) рисует иконки в брендинге
сайта — зелёный `#0f7a5f` и знак «ai» — и кладёт PNG в `frontend/public/`:

```bash
python3 scripts/generate_pwa_icons.py   # из корня репозитория
```

Файлы коммитятся (Vite копирует их в `dist`, оттуда они уезжают по FTP). Меняете `BG`/`FG` или
геометрию — пересоберите и проверьте глазами все размеры, включая `favicon-32.png`.
