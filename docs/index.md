# aichat Documentation

Документация продукта и направления развития **aichat**.

## Что это сейчас

Веб-сервис: чат с LLM в браузере и OpenAI-совместимый API-прокси. Бэкенды: OpenRouter (free-модели) и опционально e7 (Ollama). В чате доступны погодные инструменты (OpenWeatherMap) с геолокацией браузера и заказ с pzz.by (Пицца Лисицца); вход — email+пароль или OAuth (Google/Apple).

Подробности по запуску, API и деплою — в `README.md` в корне репозитория.

## Документы

| Раздел | Содержание |
|--------|------------|
| [Видение (Беларусь)](product/vision-belarus.md) | Зачем локальный LLM-сервис для РБ, сценарии, риски |
| [MVP и следующие шаги](product/mvp-next-steps.md) | Фокус первого релиза, чеклист, открытые вопросы |
| [План: заметки в чате](product/notes-plan.md) | M2M в БД, одна заметка в UI, редактор, download, tools |

## Локальный просмотр

```bash
make docs-serve
```

Сборка (strict):

```bash
make docs-build
```

Нужен Docker; образ — `squidfunk/mkdocs-material` (как в zdymak).

## Ccылки
Phoenix : https://app.ca-central-1a.arize.com/organizations/QWNjb3VudE9yZ2FuaXphdGlvbjoyMDc6eVdIVQ==/spaces/U3BhY2U6MjYyOk1oMGk=/projects/TW9kZWw6MTQwNzk3NjM6OWlaaw==?timeRangeKeyA=1h
