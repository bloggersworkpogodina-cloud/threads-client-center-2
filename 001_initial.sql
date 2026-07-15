CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    telegram_id INTEGER,
    invite_code TEXT NOT NULL UNIQUE,
    threads_username_normalized TEXT NOT NULL,
    telegram_username TEXT,
    topic_id INTEGER,
    sheet_url TEXT,
    content_plan_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_active_threads ON clients(threads_username_normalized) WHERE is_active = 1;
CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_telegram_id ON clients(telegram_id) WHERE telegram_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_topic_id ON clients(topic_id) WHERE topic_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS daily_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    post_date TEXT NOT NULL,
    slot TEXT,
    body TEXT NOT NULL,
    source_row INTEGER,
    sent_at TEXT NOT NULL,
    UNIQUE(client_id, post_date, source_row)
);

CREATE TABLE IF NOT EXISTS publication_confirmations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    confirmation_date TEXT NOT NULL,
    total_posts INTEGER NOT NULL,
    published_posts INTEGER NOT NULL,
    status TEXT NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(client_id, confirmation_date)
);

CREATE TABLE IF NOT EXISTS client_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    responses_count INTEGER NOT NULL,
    leads_count INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    replies INTEGER NOT NULL DEFAULT 0,
    reposts INTEGER NOT NULL DEFAULT 0,
    quotes INTEGER NOT NULL DEFAULT 0,
    new_followers INTEGER NOT NULL DEFAULT 0,
    telegram_clicks INTEGER NOT NULL DEFAULT 0,
    best_post TEXT,
    manager_comment TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(client_id, week_start)
);

CREATE TABLE IF NOT EXISTS client_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
