# aichat Documentation

Документация продукта **aichat** — не справочник API, а место, где сказано, зачем сервис и куда он идёт.

## Как это выглядит сейчас

aichat — чат в браузере и, заодно, ключ к тому же разговору по API. Модели берутся у OpenRouter (только бесплатные с витрины) и, если настроен, у e7 на Ollama. В разговоре можно спросить погоду, заказать пиццу с pzz.by, войти почтой или через Google, Apple, Яндекс, VK, GitHub. Это лицо сервиса для людей; как устроены версии и аудитории — в таблицах ниже.

**Runtime:** Python **3.13+**, Node.js **24+** (сборка чата). См. [Стек и runtime](dev/runtime.md).

Подробности по запуску, API и деплою — в `README.md` в корне репозитория.

## Документы

| Раздел | Содержание |
|--------|------------|
| [О сервисе](product/about.md) | Что такое aichat и чем не является |
| [Roadmap](product/roadmap.md) | Версия 1 (витрина) и версия 2 (LibreChat) |
| [Версия 1: план и факт](product/v1.md) | Текущий функционал и единственное новое — skills |
| [Стек и runtime](dev/runtime.md) | Python 3.13, Node.js 24, pin-файлы, прод |
| [OAuth-провайдеры](dev/oauth-providers.md) | Как получить креды Google, Apple, Яндекс, VK, GitHub |
| [Видение (Беларусь)](product/vision-belarus.md) | Зачем локальный LLM-сервис для РБ, сценарии, риски |
| [Две аудитории](product/audiences.md) | Витрина `aichat` для людей; LibreChat для компаний |
| [MVP и следующие шаги](product/mvp-next-steps.md) | Длинный горизонт (egov); не backlog v1 |

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
