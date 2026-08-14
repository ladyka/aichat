# aichat Documentation

Документация продукта и направления развития **aichat**.

## Что это сейчас

Веб-сервис: чат с LLM в браузере и OpenAI-совместимый API-прокси на базе OpenRouter (только free-модели). В чате доступны погодные инструменты (OpenWeatherMap) с геолокацией браузера и заказ с pzz.by (Пицца Лисицца); вход — email+пароль или OAuth (Google/Apple).

Подробности по запуску, API и деплою — в `README.md` в корне репозитория.

## Документы

| Раздел | Содержание |
|--------|------------|
| [Видение (Беларусь)](product/vision-belarus.md) | Зачем локальный LLM-сервис для РБ, сценарии, риски |
| [MVP и следующие шаги](product/mvp-next-steps.md) | Фокус первого релиза, чеклист, открытые вопросы |

## Локальный просмотр

```bash
make docs-serve
```

Сборка (strict):

```bash
make docs-build
```

Нужен Docker; образ — `squidfunk/mkdocs-material` (как в zdymak).
