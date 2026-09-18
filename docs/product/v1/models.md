# Модели

**Состояние: реализовано.**

## Что это для человека

Модель выбирают не под каждым сообщением, а один раз — на `/settings`, в разделе «Чат» (прямая ссылка — `/settings#chat`), и дальше разговор идёт выбранной. Человек видит привычные имена, а не служебные: бесплатные модели OpenRouter показываются без хвоста `:free`, публичное имя `default` наверх уходит как `openrouter/free`. Поднятый рядом e7 на Ollama виден как `e7/…`, облачный ollama.com — как `ol/…`.

## Что умеет

- Выбор модели в разделе «Чат» на `/settings` (`User.preferred_model`); то же имя принимают `/api/chat` и `/v1/chat/completions`.
- Список моделей кешируется на `MODELS_CACHE_TTL` и отдаётся наружу через `GET /v1/models` и `GET /api/models`.
- Провайдеры: OpenRouter (серверный ключ), e7 на Ollama (`E7_BY_BASE_URL`), облачный ollama.com (`OLLAMA_API_KEY`). Выключенный провайдер просто не появляется в списке — молчащей модели не бывает.
- Тема диалога считается отдельной моделью (`TITLE_MODEL`).

## Где в коде

`app/models_catalog.py` — кеш, маппинг публичных имён в upstream, маршрутизация по провайдерам; `app/model_providers/openrouter.py`, `app/model_providers/e7by.py`, `app/model_providers/ol.py` — HTTP-прокси к LLM; `app/routes/api.py` — `/v1/models`, `/api/models`; `app/routes/conversations.py` — `/api/settings`; `templates/settings.html` — раздел «Чат» (якорь `#chat`): выбор модели и навыки для новых чатов одной формой.

## Что осталось

Ничего. Новый провайдер — отдельный модуль в `app/model_providers/` плюс свои переменные в конфиге; витрину это не трогает.
