# Структура базы данных

SQLite:
`DB_PATH=/data/bot.db`

## clients
- id
- name
- telegram_id
- invite_code
- threads_username_normalized
- telegram_username
- topic_id
- sheet_url
- content_plan_url
- is_active
- created_at
- updated_at

Ограничения:
- уникальный активный Threads username;
- telegram_id может быть привязан только к актуальной записи;
- архивирование не удаляет запись.

## daily_posts
- id
- client_id
- post_date
- slot
- body
- source_row
- sent_at
- delivery_status

## publication_confirmations
- id
- client_id
- confirmation_date
- total_posts
- published_posts
- status
- comment
- created_at

Статусы:
- all
- partial
- none

## client_results
- id
- client_id
- period_start
- period_end
- responses_count
- leads_count
- comment
- created_at

## weekly_stats
- id
- client_id
- week_start
- week_end
- views
- likes
- replies
- reposts
- quotes
- new_followers
- telegram_clicks
- best_post
- manager_comment
- created_at
- updated_at

## client_events
- id
- client_id
- event_type
- payload_json
- created_at

Типы событий:
- client_created
- client_bound
- topic_created
- posts_sent
- publication_confirmed
- results_submitted
- weekly_stats_added
- manager_message
- client_message
- client_archived
- error

## migrations
Миграции:
- не удаляют данные;
- не меняют массово `is_active`;
- не обнуляют `telegram_id`;
- применяются последовательно;
- фиксируются в таблице `schema_migrations`.
