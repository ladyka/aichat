# aichat Documentation

Документация продукта **aichat** — не справочник API, а место, где сказано, зачем сервис и куда он идёт.

## Как это выглядит сейчас

aichat — чат в браузере и, заодно, ключ к тому же разговору по API. Модели берутся у OpenRouter (только бесплатные с витрины) и, если настроен, у e7 на Ollama. В разговоре можно спросить погоду, заказать пиццу с pzz.by, пользоваться markdown-навыками (`/skills`, каталог `/catalog`), войти почтой или через Google, Apple, Яндекс, GitHub, а сам чат — поставить приложением на телефон и открывать без сети. Это лицо сервиса для людей; как устроены версии и аудитории — в таблицах ниже.

**Runtime:** Python **3.13+**, Node.js **24+** (сборка чата). См. [Стек и runtime](dev/runtime.md).

Подробности по запуску, API и деплою — в `README.md` в корне репозитория.

## Документы

| Раздел | Содержание |
|--------|------------|
| [О сервисе](product/about.md) | Что такое aichat и чем не является |
| [Дорожная карта](product/roadmap.md) | Карта и состояния: выпускаемая первая версия, планы второй, бэклог |
| [Версия 1 (выпускается)](product/v1/index.md) | Восемь функций витрины по отдельности: чат, модели, вход, API, руки, навыки, шаринг, телефон |
| [Версия 2 (в планах)](product/v2/index.md) | Диктофон, голосовой чат, заметки 2, веб-поиск, биллинг, приложение на телефон, LibreChat |
| [999. Бэклог](product/v999.backlog/index.md) | Видение и размышления: вдохновение, а не обещания |
| [Две аудитории](product/audiences.md) | Витрина `aichat` для людей; LibreChat для компаний |
| [Стек и runtime](dev/runtime.md) | Python 3.13, Node.js 24, pin-файлы, прод |
| [OAuth-провайдеры](dev/oauth-providers.md) | Как получить креды Google, Apple, Яндекс, GitHub |

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
