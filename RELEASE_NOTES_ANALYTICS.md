# Release: Analytics module

Added without replacing the existing client, Google Sheets, topics, posts, or Railway logic.

## New
- Start-project metrics: Threads followers, Telegram followers, average weekly leads, required Threads screenshots, optional Telegram screenshot.
- Weekly metrics: current Threads/Telegram followers, views, applications, required Threads screenshots, optional Telegram screenshot.
- Client analytics comparison: baseline vs latest week.
- Admin reminders every Friday at 12:00 and 18:00 for missing data.
- Safe SQLite migration that creates `client_baseline` and extends `weekly_stats` without deleting existing records.

## Verification
- Python compilation passed.
- Analytics SQLite smoke test passed.


## FIX 2026-07-23 — сообщения клиентов
- Обычные сообщения клиента теперь автоматически копируются в его рабочую тему без обязательного нажатия «Связь с менеджером».
- Сохранена работа кнопки «Связь с менеджером» и всех FSM-сценариев.
- Поддерживаются текст, фото, документы, голосовые и другие типы сообщений через copy_message.
- Сообщения администратора и ответы внутри активных сценариев не перехватываются.
