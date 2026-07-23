# Мединтернет MAX — документация проекта

Медицинский ИИ-поисковик для врачей и фармацевтов в мессенджере **MAX**: чат-бот
и встроенное мини-приложение (Mini App). Порт с Telegram-версии (`Medinternet_bot`)
на платформу MAX (dev.max.ru). Ответы даёт нейросеть **RX Code AI** (медицинский RAG).

Репозиторий: `Aldanto1/Medinternet_MAX`. Хостинг: **Railway** (один сервис). БД: **Neon** (PostgreSQL).

---

## 1. Структура репозитория

```
Medinternet_MAX/
├── max_bot.py          # точка входа: long polling MAX + запуск веб-сервера
├── max_client.py       # клиент MAX Bot API (aiohttp)
├── handlers.py         # обработка апдейтов бота, клавиатуры, навигация
├── server.py           # веб-сервер mini app + API (aiohttp) + подключение CRM
├── crm.py              # CRM-панель рассылок под /crm (в этом же процессе)
├── ai_client.py        # клиент нейросети RX Code AI (создать сессию, отправить, стрим)
├── db.py               # PostgreSQL (asyncpg): все таблицы и запросы
├── config.py           # конфигурация из переменных окружения
├── link_token.py       # подписанные одноразовые токены deep-link регистрации
├── requirements.txt    # зависимости (aiohttp, asyncpg, python-dotenv, PyJWT)
├── Dockerfile          # образ для Railway (CMD python max_bot.py)
├── run.ps1             # локальный запуск: cloudflared-туннель + бот
├── env/.env(.example)  # секреты (в .gitignore)
├── webapp/             # mini app (статика, отдаётся server.py)
│   ├── index.html      #   экраны: регистрация, поиск, история, просмотр чата
│   ├── app.js          #   вся логика mini app
│   ├── style.css       #   стили (светлая/тёмная тема)
│   ├── link.html       #   страница-«личный кабинет» с одноразовой ссылкой
│   ├── logo.png        #   логотип (mini app)
│   ├── logo_banner.png #   баннер логотипа для сообщения бота (2:1 с полями)
│   └── robot.png       #   иконка-робот у приветствия в поиске
├── crm-service/        # автономная версия CRM (не деплоится; см. §11)
└── docs/
    ├── DOCUMENTATION.md         # этот файл
    └── rxcode-ai-swagger.json  # спецификация API нейросети RX Code AI
```

---

## 2. Архитектура

Один процесс (`max_bot.py`) поднимает:
1. **Long polling** MAX Bot API (`GET /updates`) → раздаёт апдейты в `handlers.py`.
2. **Веб-сервер aiohttp** (`server.py`) на порту `PORT`:
   - отдаёт статику и API мини-аппа (`/`, `/api/*`);
   - под `/crm` — CRM-панель рассылок (`crm.py`), если заданы её переменные.

```
MAX ──updates──> max_bot.poll_loop ──> handlers ──> max_client ──> MAX Bot API
                                    │
Mini App (webapp) ──HTTPS──> server.py (aiohttp) ──> db (Neon) / ai_client (RX Code AI)
CRM-панель /crm  ──HTTPS──> server.py ──> crm.py ──> db (Neon) / max_client (рассылка)
```

Наследие модели MAX Bot API — TamTam: апдейты `message_created`, `message_callback`,
`bot_started`; клавиатуры через `attachments` типа `inline_keyboard`; long polling с `marker`.

---

## 3. Бот (MAX)

**Файлы:** `max_bot.py`, `handlers.py`, `max_client.py`.

- Приветственное «главное сообщение»: баннер-логотип + текст + inline-клавиатура.
  В чате всегда **одно** главное сообщение — прежнее удаляется при показе нового
  (id хранится в таблице `main_messages`).
- Кнопки главного меню: «🤝 Поделиться с другом», «📖 Как пользоваться»,
  «📄 Политика конфиденциальности», «🔍 Открыть Mini App».
- Открытие мини-аппа — кнопка-ссылка `https://max.ru/<botName>?startapp` (MAX
  открывает зарегистрированное в кабинете приложение нативно).
- Навигация по разделам — callback-кнопки (`nav:partners`, `nav:instruction`,
  `nav:home`); подтверждение нажатия — best-effort (ошибка ack не ломает переход).
- Регистрация:
  - **deep-link**: `bot_started` с `payload` (одноразовый подписанный токен из
    `link_token.py`, гасится в таблице `link_tokens`);
  - после регистрации бот шлёт поздравление и кнопку Mini App.
- **Апдейты, которые ловим:** `bot_started`, `message_created` (`/start`, `/help`),
  `message_callback`. Учёт активности: `db.touch_bot_action` на любой апдейт.

---

## 4. Mini App

**Файлы:** `webapp/index.html`, `webapp/app.js`, `webapp/style.css`.
SDK: MAX Bridge `https://st.max.ru/js/max-web-app.js` (глобальный `window.WebApp`).

Экраны (`showScreen` / `showSub`):
- **Регистрация** (`#screen-register`) — для незарегистрированных: инструкция и
  крупная кнопка «Зарегистрироваться» (открывает страницу `/link`).
- **Поиск** (`#screen-search`) — основной экран после регистрации:
  - приветствие с иконкой робота;
  - чат с нейросетью (потоковый ответ, построчное появление);
  - под каждым ответом — **копировать / лайк / дизлайк** (оценка → `/api/ai/feedback`);
  - **чипсы-подсказки** внизу диалога: до первого вопроса — 3 статичных,
    после ответа — динамические уточняющие вопросы, сгенерированные нейросетью;
  - «Новый чат» сбрасывает сессию.
- **История** (`#screen-history`) — кнопка 🕘: список чатов (название = первый
  запрос). Запрашивается **только по нажатию**, не при старте.
- **Просмотр чата** (`#screen-chat`) — переписка выбранного чата, «← Назад» к списку.

Аутентификация всех запросов mini app — по `WebApp.initData` (проверяется на бэкенде).

---

## 5. Backend: HTTP-эндпоинты (`server.py`)

Статика: `GET /`, `/app.js`, `/style.css`, `/logo.png`, `/robot.png`, `/link`.

Mini app API (POST, тело содержит `initData`):
| Метод/путь | Назначение |
|---|---|
| `POST /api/register` | Регистрация по MedID (проверка initData) |
| `POST /api/me` | Статус: зарегистрирован/активен, доступность ИИ, профиль |
| `POST /api/logout` | «Выйти из аккаунта»: `active=false` (данные сохраняются) |
| `POST /api/ai/message` | Ответ ИИ (не потоковый; в mini app не используется) |
| `POST /api/ai/message/stream` | Потоковый ответ (SSE): `text`/`action`/`suggestions`/`done` |
| `POST /api/ai/reset` | Сброс текущей сессии (новый чат) |
| `POST /api/ai/feedback` | Оценка ответа: таблица `ai_feedback` + отправка в RX Code AI (§10) |
| `POST /api/chats` | Список чатов пользователя (для истории) |
| `POST /api/chat/messages` | Сообщения выбранного чата |

Проверка **initData** (`validate_init_data`): HMAC-SHA256 по data_check_string
(ключ на основе токена бота). ⚠️ Требует подтверждения на боевом MAX; для локальной
отладки есть обход `SKIP_INITDATA_CHECK=1`.

---

## 6. База данных (Neon / PostgreSQL)

Все таблицы создаются автоматически в `db.init()` при старте. `telegram_id` во всех
таблицах — это **MAX user_id** (историческое имя колонки, оставлено для совместимости).

| Таблица | Назначение |
|---|---|
| `users` | Пользователи: med_id, username, full_name, specialty, position, `active`, метки активности |
| `ai_sessions` | Активная сессия RX Code AI (`chat_id`) + ссылка на текущий чат (`chat_row_id`) |
| `chats` | Чаты истории: `title` = первый запрос |
| `chat_messages` | Сообщения чатов: `role` (user/ai), `content` |
| `ai_feedback` | Оценки ответов (like/dislike) |
| `main_messages` | id текущего главного сообщения бота (чтобы держать одно) |
| `start_prompts` | id стартового сообщения (удалить после регистрации) |
| `link_tokens` | Одноразовые токены deep-link регистрации |
| `crm.blocked_users` | Заблокировавшие бота (исключаются из рассылки) |
| `crm.broadcast_log` | Лог доставки рассылок |

Ручное удаление пользователей — см. Neon SQL Editor:
```sql
DELETE FROM ai_sessions; DELETE FROM start_prompts;
DELETE FROM main_messages; DELETE FROM users;
```

---

## 7. Конфигурация (переменные окружения)

| Переменная | Назначение |
|---|---|
| `BOT_TOKEN` | Токен MAX-бота |
| `BOT_NAME` | Ник бота (для ссылок `max.ru/<BOT_NAME>?startapp`) |
| `MAX_API_BASE` | База MAX Bot API (по умолчанию `https://botapi.max.ru`) |
| `MAX_AUTH_QUERY` | `1` — токен query-параметром вместо заголовка |
| `DATABASE_URL` | Строка подключения Neon |
| `WEBAPP_URL` | Публичный HTTPS-адрес mini app (домен Railway) |
| `WEBAPP_PORT`/`PORT` | Порт веб-сервера (Railway задаёт `PORT`) |
| `NEURO_API_URL` | База API нейросети (напр. `https://qa.rxcode.pro`) |
| `NEURO_API_KEY` | Ключ нейросети |
| `NEURO_CHANNEL` | Канал RX Code AI (по умолчанию `michat`) |
| `JWT_SECRET`, `CRM_LOGIN_EMAIL`, `CRM_LOGIN_PASSWORD` | Включают CRM-панель `/crm` |
| `SEND_RATE_PER_SEC` | Троттлинг рассылки (по умолчанию 25/сек) |
| `SKIP_INITDATA_CHECK` | `1` — обход проверки подписи (только отладка!) |

---

## 8. Деплой (Railway)

Один сервис из репозитория (Dockerfile, `CMD python max_bot.py`):
1. Переменные из §7 (минимум: `BOT_TOKEN`, `BOT_NAME`, `DATABASE_URL`,
   `NEURO_API_URL`, `NEURO_API_KEY`; для CRM — `JWT_SECRET`, `CRM_LOGIN_EMAIL`,
   `CRM_LOGIN_PASSWORD`).
2. Settings → Networking → Generate Domain → вписать домен в `WEBAPP_URL` и в
   кабинет MAX (адрес мини-приложения).
3. Таблицы создаются сами; Redis/Postgres на Railway не нужны (БД внешняя — Neon).

CRM-панель после деплоя: `https://<домен>/crm/`.

---

## 9. CRM-панель рассылок

Встроена в сервис бота под `/crm` (`crm.py`): вход по логину/паролю (JWT), выбор
получателей (поиск по MAX ID/нику, карточка пользователя), конструктор сообщения,
прикрепление файла, живой статус. Очередь рассылки — внутри процесса (asyncio,
троттлинг), отправка токеном бота через `max_client`. Автономная копия сервиса —
в папке `crm-service/` (на отдельный деплой; в текущей схеме не используется).

---

## 10. Нейросеть RX Code AI (поисковик)

Полная спецификация — в файле **[`rxcode-ai-swagger.json`](rxcode-ai-swagger.json)**
(OpenAPI/Swagger 2.0, «RXCode AI API»). Клиент — `ai_client.py`.

**База:** `NEURO_API_URL` (напр. `https://qa.rxcode.pro`), пути под `/api`.
**Авторизация:** заголовок (в спецификации — `Authorization`, схема Bearer). ⚠️ Текущий
клиент шлёт ключ заголовком `X-API-Key` — работает в текущем окружении; при смене
окружения свериться со схемой.

### Используемые эндпоинты
| Эндпоинт | Тело / параметры | Ответ | Где в коде |
|---|---|---|---|
| `POST /api/chats` | `{ UserId, Channel }` | `{ SessionId }` | `ai_client.create_session` |
| `POST /api/chats/{chatId}/messages` | `{ Message, RemovePersonalData? }` | `{ SummaryHTML, Summary, Notes, Sources[] }` | `ai_client.send_message` |
| `POST /api/chats/{chatId}/messages/stream` | `{ Message }` | поток строк `data: {Text|Action}` | `ai_client.stream_message` |
| `GET /api/chats/{chatId}/messages` | — | `ChatMessageItem[]` | `ai_client.list_messages` / `last_ai_message_id` |
| `POST /api/chats/{chatId}/messages/{messageId}/like` \| `/dislike` | — | — | `ai_client.rate_message` |

`Sources[]` = `{ Title, Url }`. Ответ: `Summary` (Markdown) / `SummaryHTML` (HTML).
`ChatMessageItem` = `Id`, `Direction` (0=пользователь, 1=ИИ), `Text`, `Question`,
`Sources`, `Created`, `Rate`, `RagBased`.

### Как работает оценка ответа (лайк/дизлайк)
1. После ответа сервер запрашивает `GET /api/chats/{chatId}/messages` и берёт `Id`
   последнего сообщения с `Direction == 1` (`ai_client.last_ai_message_id`).
2. Этот `Id` вместе с id сессии уходит в mini app SSE-событием `answer_ref`;
   фронт кладёт их в `dataset` пузыря ответа.
3. При нажатии лайка/дизлайка mini app шлёт `POST /api/ai/feedback`
   с `chat_id`/`message_id`; бэкенд пишет оценку в таблицу `ai_feedback` **и**
   вызывает `POST /api/chats/{chatId}/messages/{messageId}/like|dislike` в RX Code AI.
4. Всё best-effort: если `Id` получить не удалось, оценка сохраняется только у нас.

### Доступные, но пока НЕ используемые эндпоинты
- `GET /api/chats?userId=` — список сессий пользователя (`SessionItem`).
- `PATCH /api/chats/{id}` — изменить `Title` сессии.
- `GET /api/chats/{chatId}/messages/count` — число сообщений.
- `GET /api/health` — статус сервиса (`Healthy`, `Database`, `OpenRouter`).

### Как устроен поток ответа
`stream_message` читает построчно `data: {...}` и отдаёт кортежи:
`("action", "статус…")` — этап обработки, `("text", "кусок")` — часть ответа
(Markdown). Бэкенд `server.py` пересылает их в mini app как SSE-события
`action`/`text`, копит текст, после ответа генерирует **подсказки-продолжения**
(`ai_client.generate_followups` — отдельная эфемерная сессия) и шлёт событие
`suggestions`, затем `done`.

---

## 11. Что требует проверки на боевом MAX

Помечено в коде `⚠️ ТРЕБУЕТ ПРОВЕРКИ` (см. `max_client.py`, `server.py`, `handlers.py`):
- базовый URL MAX Bot API (`botapi.max.ru` vs `platform-api2.max.ru`) и способ авторизации;
- точная схема валидации `initData`;
- схема кнопки `open_app` / формат deep-link `bot_started`;
- загрузка медиа (`/uploads`) для баннера и рассылки;
- извлечение `mid` из ответа отправки (для «одного главного сообщения»).
