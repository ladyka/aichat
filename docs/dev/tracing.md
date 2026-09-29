# Трейсы Phoenix / Arize

Как агент и человек разбирают сбой чата по трейсу, какие поля писать и как их прочитать.

Проект в Arize AX (регион CA): [aichat](https://app.ca-central-1a.arize.com/organizations/QWNjb3VudE9yZ2FuaXphdGlvbjoyMDc6eVdIVQ==/spaces/U3BhY2U6MjYyOk1oMGk=/projects/TW9kZWw6MTQwNzk3NjM6OWlaaw==). Данные уходят на OTLP `https://otlp.ca-central-1a.arize.com/v1` (gRPC) или `/v1/traces` (HTTP).

## Как агенту работать с трейсом

Три уровня, от простого к полному. Секреты не класть в репозиторий и не печатать в ответ.

1. **Вставка из UI.** Ссылка на трейс или спан, JSON `input.value` / `output.value`, дерево CHAIN → LLM → TOOL. Этого достаточно для большинства правок рук и петли `/api/chat`.
2. **Идентификаторы.** `session.id` (у нас это id диалога), `user.id`, `aichat.conversation_id`, публичная модель `aichat.model`, провайдер `aichat.provider`. По ним находят соседние спаны того же хода.
3. **Выгрузка REST.** `POST https://api.ca-central-1a.arize.com/v2/spans` с `Authorization: Bearer …`. Ключ OTLP (`ARIZE_API_KEY`) шлёт данные; для чтения списка спанов может понадобиться отдельный developer key (`ARIZE_REST_API_KEY`). Фильтр — SQL-подобная строка, например `name = 'download_file'` или `status_code = 'ERROR'`. Скрипт: `python scripts/arize_spans.py --hours 2 --filter "aichat.tool.known = false"`.

Data Plane (ingest) и REST (чтение) — разные двери. Flight (`flight.ca-central-1a.arize.com`) нужен для выгрузки датасетов, не для разбора одного трейса в чате.

## Что смотреть на каждом виде спана

| Спан | Зачем | Поля |
|------|--------|------|
| CHAIN `chat.tool_loop` | Один ход петли рук | `aichat.conversation_id`, `aichat.model`, `aichat.provider`, `aichat.upstream_model`, `session.id`, `user.id` |
| LLM (`*.chat.completions.stream`) | Что модель попросила | `output.value` (текст и собранные tool_calls с `id`/`name`/`arguments`), `aichat.llm.tool_call_count`, `aichat.llm.reasoning`, `aichat.llm.sse_bytes`, список имён в `input` → `tools` |
| TOOL | Что сервер исполнил | `tool.name`, `tool.id` (id вызова модели), `input.value` (`id`/`name`/`arguments`), `aichat.tool.known`, `aichat.tool.arguments_json_valid`, `output.value`, при ошибке — статус ERROR и `aichat.tool.error` |

Сигналы «сломалась сборка потока», как `download_filedownload_file` и два JSON встык:

- `aichat.tool.known = false` — имени нет среди рук в коде;
- `aichat.tool.arguments_json_valid = false` — `arguments` не один JSON-объект;
- статус TOOL = ERROR и `aichat.tool.error` начинается с `Unknown tool:`.

## Чего в трейс не кладём

Полное сырое SSE, фото меню pzz, тело скачанной страницы, секреты. Tool-результаты сжимает `compact_tool_content` (для pzz — названия и счётчик).

## Код

`app/telemetry.py` — CHAIN/LLM/TOOL и OTLP. `app/tools.py` — `call_tool` открывает TOOL span. `app/routes/api.py` — `chat.tool_loop` и id вызова. Настройки: `ARIZE_SPACE_ID`, `ARIZE_API_KEY`, `ARIZE_PROJECT_NAME`, `ARIZE_OTLP_ENDPOINT`, опционально `ARIZE_REST_BASE_URL`, `ARIZE_REST_API_KEY`.
