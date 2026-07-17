# Medinternet MAX — перенос бота и mini app с Telegram на MAX

Это порт `Medinternet_bot` (Telegram) на платформу **MAX** (dev.max.ru).
Вся бизнес-логика сохранена; заменён транспорт (Telegram Bot API → MAX Bot API)
и SDK мини-аппа (Telegram WebApp → MAX Bridge). Папка `Medinternet_bot` не менялась.

## Что перенесено

| Telegram (`Medinternet_bot`) | MAX (`Medinternet_MAX`) | Изменения |
|---|---|---|
| `bot.py` (aiogram) | `max_bot.py` | Свой цикл long polling (`GET /updates` + marker), без aiogram |
| — | `max_client.py` (**новый**) | Тонкий async-клиент MAX Bot API на aiohttp |
| `handlers.py` (Router) | `handlers.py` | Ручной диспетчер апдейтов, клавиатуры = `inline_keyboard` attachments |
| `server.py` | `server.py` | Валидация `initData` под MAX, уведомления через `MaxClient` |
| `config.py` | `config.py` | Переменные MAX (`MAX_API_BASE`, `BOT_NAME`, `MAX_AUTH_QUERY`), убран Telegram-proxy |
| `db.py` | `db.py` | Почти как есть; `start_prompts.message_id` теперь `TEXT` (в MAX `mid` — строка) |
| `ai_client.py` | `ai_client.py` | Без изменений (RX Code AI не зависит от мессенджера) |
| `link_token.py` | `link_token.py` | Без изменений |
| `webapp/index.html` | `webapp/index.html` | SDK: `telegram-web-app.js` → `https://st.max.ru/js/max-web-app.js` |
| `webapp/app.js` | `webapp/app.js` | `window.Telegram.WebApp` → `window.WebApp` + фолбэки методов |
| `webapp/link.html` | `webapp/link.html` | Тексты «Telegram» → «MAX» |
| `webapp/style.css`, `logo.png` | без изменений | CSS собственный, без `--tg-theme-*` |

## Соответствие API

| Telegram | MAX |
|---|---|
| `getUpdates` (offset) | `GET /updates` (marker) |
| `sendMessage` | `POST /messages?chat_id=…` / `?user_id=…` |
| `deleteMessage` | `DELETE /messages?message_id=…` |
| `answerCallbackQuery` | `POST /answers?callback_id=…` |
| `InlineKeyboardButton(callback_data)` | кнопка `type: "callback"` (`payload`) |
| `InlineKeyboardButton(url)` | кнопка `type: "link"` |
| `InlineKeyboardButton(web_app)` | кнопка `type: "open_app"` |
| deep-link `t.me/bot?start=<t>` → `/start` | апдейт `bot_started` c полем `payload` |
| `initData` (HMAC, WebAppData) | `initData` MAX Bridge (см. ниже) |

## ⚠️ Что требует проверки на реальном боте

Документация MAX по некоторым местам неоднозначна/закрыта (нужна верификация
юрлица). Спорные значения вынесены в конфиг и помечены в коде комментариями
`⚠️ ТРЕБУЕТ ПРОВЕРКИ`:

1. **Базовый URL API** — `MAX_API_BASE`. По умолчанию `https://botapi.max.ru`;
   в части источников — `https://platform-api2.max.ru`. При 401/404 поменяйте в `.env`.
2. **Авторизация** — заголовок `Authorization: <token>` (по умолчанию) либо
   query `?access_token=` (`MAX_AUTH_QUERY=1`).
3. **Валидация `initData`** (`server.py::validate_init_data`) — реализована по
   Telegram-совместимой схеме (data_check_string + HMAC-SHA256, ключ `WebAppData`).
   В доке MAX Bridge встречается иной вариант `HMAC_SHA256(authDate+phone+userId, token)`.
   Для локальной отладки есть обход `SKIP_INITDATA_CHECK=1` (в проде — выключить!).
4. **Кнопка `open_app`** (`handlers.py::_btn_open_app`) — точная схема payload.
5. **Deep-link запуска бота с payload** (`server.py::handle_link`) — формат
   ссылки `https://max.ru/<botName>?start=<token>`.
6. **Загрузка логотипа** (`max_client.py::upload_image_token`) — двухшаговый
   `POST /uploads`. При неудаче главное сообщение уходит без картинки (не критично).

## Как запустить (локально)

```powershell
cd Medinternet_MAX
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy env\.env.example env\.env   # заполнить BOT_TOKEN, DATABASE_URL, NEURO_API_KEY
.\run.ps1                        # поднимет cloudflared-туннель и бота
```

`run.ps1` сам пропишет публичный `WEBAPP_URL` в `.env`. Для прод-деплоя MAX
рекомендует webhook (`POST /subscriptions`) вместо long polling — см. раздел
«Дальнейшие шаги».

## Ещё не перенесено

- **`crm-service/`** (рассылки/CRM-панель) — отдельный микросервис; его
  `bot_client.py` шлёт сообщения через Telegram Bot API. Порт под MAX не делал,
  чтобы не раздувать изменения. Скажите — перенесу отдельно.
- **Webhook-режим** для прода (сейчас long polling — подходит для разработки).
