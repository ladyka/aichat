# Креды OAuth-провайдеров

Вход через Google, Apple, Яндекс, VK и GitHub включается **по отдельности**: кнопка на `/login` и `/register` появляется, только если заданы `PUBLIC_BASE_URL` и переменные **этого** провайдера (см. `.env.example`).

Общее:

1. Выставьте публичный HTTPS-origin без слэша в конце, например `https://aichat.example.com`.
2. Redirect URI должен **точно** совпасть с тем, что указано в кабинете провайдера (схема, хост, путь, без лишнего `/`).
3. Секреты только в `.env`, файл не коммитить и не заливать по FTP (`.uploadignore`).

Локально OAuth обычно не заводится на `http://127.0.0.1:8080`: у провайдеров нужен зарегистрированный HTTPS-домен (исключения — см. разделы Google, GitHub, VK).

Redirect URI aichat:

| Провайдер | Путь |
|-----------|------|
| Google | `{PUBLIC_BASE_URL}/auth/google/callback` |
| Apple | `{PUBLIC_BASE_URL}/auth/apple/callback` |
| Яндекс | `{PUBLIC_BASE_URL}/auth/yandex/callback` |
| VK | `{PUBLIC_BASE_URL}/auth/vk/callback` |
| GitHub | `{PUBLIC_BASE_URL}/auth/github/callback` |

После сохранения `.env` перезапустите процесс приложения (`get_settings()` кешируется на старте).

---

## Google

Кабинет: [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials).

Нужны: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

1. Создайте (или выберите) проект в [Google Cloud](https://console.cloud.google.com/).
2. **APIs & Services → OAuth consent screen**: тип External (или Internal для Workspace). Укажите имя приложения, email поддержки, затем **Scopes** — как минимум `openid`, `email`, `profile` (их запрашивает aichat).
   - Пока статус *Testing*, войти могут только пользователи из списка Test users.
   - Для публичного входа приложение нужно опубликовать (верификация Google, если запросите чувствительные scopes; для openid/email/profile обычно достаточно публикации).
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
4. Application type: **Web application**.
5. **Authorized JavaScript origins**: `https://ваш-домен` (тот же origin, что `PUBLIC_BASE_URL`).
6. **Authorized redirect URIs**: `https://ваш-домен/auth/google/callback`.
7. Скопируйте **Client ID** → `GOOGLE_CLIENT_ID`, **Client secret** → `GOOGLE_CLIENT_SECRET`.

Документация Google: [Setting up OAuth 2.0](https://developers.google.com/identity/protocols/oauth2).

---

## Apple (Sign in with Apple)

Кабинет: [Apple Developer → Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/identifiers/list).

Нужны: `APPLE_CLIENT_ID`, `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`.

Нужен **платный** [Apple Developer Program](https://developer.apple.com/programs/). Веб-вход завязан на **Services ID** и ключ; одного «OAuth client secret» у Apple нет — aichat сам подписывает JWT ключом `.p8`.

1. **Identifiers → App IDs**: создайте App ID (Bundle ID вида `com.example.aichat`). В Capabilities включите **Sign in with Apple** (Primary App ID). Без этого Services ID не привязать.
2. **Identifiers → Services IDs** → «+»: description и идентификатор вида `com.example.aichat.web`. Это значение → **`APPLE_CLIENT_ID`**.
3. Откройте созданный Services ID, включите **Sign in with Apple → Configure**:
   - Primary App ID — тот, что в шаге 1.
   - Domains: хост без схемы, например `aichat.example.com`.
   - Return URLs: `https://aichat.example.com/auth/apple/callback` (полный URL).
4. **Keys → «+»**: имя ключа, включите **Sign in with Apple**, привяжите к тому же Primary App ID. Скачайте `.p8` **сразу** — повторно файл не отдают.
5. **Key ID** (10 символов на странице ключа) → `APPLE_KEY_ID`.
6. **Membership** (или шапка аккаунта) → **Team ID** → `APPLE_TEAM_ID`.
7. Содержимое `.p8` → `APPLE_PRIVATE_KEY`. В одной строке `.env` переносы можно записать как `\n` (приложение развернёт их само):

```bash
APPLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nMIGT...\n-----END PRIVATE KEY-----"
```

Официально: [Configure Sign in with Apple for the web](https://developer.apple.com/help/account/configure-app-capabilities/configure-sign-in-with-apple-for-the-web/), [Create a Sign in with Apple private key](https://developer.apple.com/help/account/capabilities/create-a-sign-in-with-apple-private-key/).

---

## Яндекс ID

Кабинет: [oauth.yandex.ru](https://oauth.yandex.ru/) (или [oauth.yandex.com](https://oauth.yandex.com/)).

Нужны: `YANDEX_CLIENT_ID`, `YANDEX_CLIENT_SECRET`.

1. Войдите тем Яндекс ID, на который регистрируете приложение.
2. **Создать новое приложение** (или [прямая форма](https://oauth.yandex.ru/client/new)).
3. Платформы: **Веб-сервисы**. Redirect URI: `https://ваш-домен/auth/yandex/callback`.
4. Доступы (права API Яндекс ID / «Для авторизации»):
   - доступ к адресу электронной почты — scope `login:email`;
   - доступ к логину, имени, полу — scope `login:info`.
5. Сохраните приложение. **ClientID** → `YANDEX_CLIENT_ID`, **Client secret** → `YANDEX_CLIENT_SECRET` (секрет можно перевыпустить в карточке приложения).

Документация: [Регистрация приложения](https://yandex.ru/dev/id/doc/ru/register-client), [получение кода](https://yandex.ru/dev/id/doc/ru/codes/code-url).

---

## VK ID

Кабинет: [сервис авторизации VK ID](https://id.vk.ru/about/business/go) → приложения. Создание: [документация](https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/create-application).

Нужны: `VK_CLIENT_ID` (ID приложения) и `VK_CLIENT_SECRET` (**сервисный ключ**, не «защищённый ключ»). Синоним секрета: `VK_SERVICE_TOKEN`.

1. Войдите через **VK Бизнес ID** и подтвердите профиль бизнеса (без этого ключи и настройки ограничивают / приложения могут отключить).
2. **Мои приложения → Добавить приложение**.
3. Платформа **Web**. Название и иконка — то, что увидит пользователь.
4. **Базовый домен**: хост сайта, например `aichat.example.com` (без `https://`; для локальных проб VK допускает `localhost` без порта).
5. **Доверенный redirect URL**: `https://ваш-домен/auth/vk/callback` — тот же URL, что в `PUBLIC_BASE_URL`. Домен в этом URL должен совпадать с базовым (регистр важен). Для localhost в доке VK: `http://localhost` без порта.
6. В доступах / scopes включите **email** (aichat запрашивает `email`; без почты вход отклоняется).
7. После создания в параметрах приложения:
   - **ID приложения** (`client_id` / `app_id`) → `VK_CLIENT_ID`;
   - **сервисный ключ доступа** → `VK_CLIENT_SECRET`.
   - «Защищённый ключ» (`client_secret` для клиентских SDK) в `.env` **не** кладите: обмен кода на бэкенде идёт с `service_token`.

aichat ходит в VK ID без JS SDK (`/authorize` + PKCE + `POST /oauth2/auth`).

---

## GitHub

Кабинет: [GitHub → Settings → Developer settings → OAuth Apps](https://github.com/settings/developers) (организация: Settings организации → Developer settings).

Нужны: `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`.

1. **New OAuth App**.
2. Application name — имя на экране согласия.
3. **Homepage URL**: `https://ваш-домен` (тот же origin, что `PUBLIC_BASE_URL`).
4. **Authorization callback URL**: `https://ваш-домен/auth/github/callback`.
5. Создайте приложение: **Client ID** → `GITHUB_CLIENT_ID`.
6. **Generate a new client secret** → `GITHUB_CLIENT_SECRET` (секрет показывают один раз; потеряли — выпустите новый).

Scopes aichat запрашивает сам: `read:user` и `user:email`. Отдельно в кабинете их включать не нужно. Почта берётся из API `/user/emails` (в т.ч. скрытая в профиле).

Документация: [Creating an OAuth app](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app).

Для организации секрет могут выдавать только владельцы; пользователь должен разрешить приложению доступ к org, если это требуется политикой org.

---

## Проверка

1. В `.env` заданы `PUBLIC_BASE_URL` и полный набор переменных выбранного провайдера.
2. Процесс перезапущен.
3. На `/login` видна кнопка этого провайдера, остальные без кредов скрыты.
4. После согласия в кабинете провайдера браузер возвращается на `/auth/<provider>/callback` и уходит на `/chat` с cookie-сессией.

Если callback с ошибкой `state` — повторный вход (state одноразовый, хранится в памяти процесса). На нескольких воркерах один и тот же запрос authorize/callback должен попасть в один процесс.
