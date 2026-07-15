from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite


class Database:
    def __init__(self, path: str, migrations_dir: str | None = None):
        self.path = path
        self.migrations_dir = migrations_dir or str(Path(__file__).parent)

    @asynccontextmanager
    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            await conn.close()

    async def migrate(self) -> None:
        """Create the full schema before Telegram polling starts.

        The schema is embedded intentionally so Railway does not depend on an
        external SQL file being present in the deploy root.
        """
        schema = r"""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

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
        CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_active_threads
            ON clients(threads_username_normalized) WHERE is_active = 1;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_telegram_id
            ON clients(telegram_id) WHERE telegram_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_topic_id
            ON clients(topic_id) WHERE topic_id IS NOT NULL;

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
        """
        async with self.connect() as conn:
            await conn.executescript(schema)
            await conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                ("embedded_schema_v1", datetime.utcnow().isoformat()),
            )
            await conn.commit()
            required = {
                "clients", "daily_posts", "publication_confirmations",
                "client_results", "weekly_stats", "client_events",
            }
            rows = await (await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )).fetchall()
            present = {row[0] for row in rows}
            missing = required - present
            if missing:
                raise RuntimeError(f"Database schema initialization failed: {sorted(missing)}")

    @staticmethod
    def normalize_threads(value: str) -> str:
        return value.strip().lstrip("@").lower()

    @staticmethod
    def normalize_telegram(value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip()
        if value == "-":
            return None
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if value.lower().startswith(prefix):
                value = value[len(prefix):]
                break
        value = value.strip().lstrip("@").split("?")[0].strip("/")
        return value or None

    async def create_client(self, name: str, threads_username: str, telegram_username: str | None) -> aiosqlite.Row:
        threads = self.normalize_threads(threads_username)
        telegram = self.normalize_telegram(telegram_username)
        invite_code = secrets.token_urlsafe(10)
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            try:
                cur = await conn.execute(
                    """
                    INSERT INTO clients(name, threads_username_normalized, telegram_username, invite_code, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (name.strip(), threads, telegram, invite_code, now, now),
                )
                await conn.commit()
            except aiosqlite.IntegrityError as exc:
                raise ValueError("Активный клиент с таким Threads username уже существует") from exc
            row = await (await conn.execute("SELECT * FROM clients WHERE id = ?", (cur.lastrowid,))).fetchone()
            return row

    async def list_clients(self, active_only: bool = True):
        q = "SELECT * FROM clients"
        params: tuple[Any, ...] = ()
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY name COLLATE NOCASE"
        async with self.connect() as conn:
            return await (await conn.execute(q, params)).fetchall()

    async def get_client(self, client_id: int):
        async with self.connect() as conn:
            return await (await conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,))).fetchone()

    async def get_client_by_tg(self, telegram_id: int):
        async with self.connect() as conn:
            return await (await conn.execute("SELECT * FROM clients WHERE telegram_id = ? AND is_active = 1", (telegram_id,))).fetchone()

    async def get_client_by_topic(self, topic_id: int):
        async with self.connect() as conn:
            return await (await conn.execute("SELECT * FROM clients WHERE topic_id = ? AND is_active = 1", (topic_id,))).fetchone()

    async def bind_client(self, invite_code: str, telegram_id: int):
        async with self.connect() as conn:
            row = await (await conn.execute("SELECT * FROM clients WHERE invite_code = ? AND is_active = 1", (invite_code,))).fetchone()
            if not row:
                return None
            await conn.execute("UPDATE clients SET telegram_id = NULL WHERE telegram_id = ? AND id <> ?", (telegram_id, row["id"]))
            await conn.execute("UPDATE clients SET telegram_id = ?, updated_at = ? WHERE id = ?", (telegram_id, datetime.utcnow().isoformat(), row["id"]))
            await conn.commit()
            return await (await conn.execute("SELECT * FROM clients WHERE id = ?", (row["id"],))).fetchone()

    async def update_client_links(self, client_id: int, *, sheet_url: str | None = None, content_plan_url: str | None = None):
        fields, values = [], []
        if sheet_url is not None:
            fields.append("sheet_url = ?"); values.append(sheet_url)
        if content_plan_url is not None:
            fields.append("content_plan_url = ?"); values.append(content_plan_url)
        fields.append("updated_at = ?"); values.append(datetime.utcnow().isoformat())
        values.append(client_id)
        async with self.connect() as conn:
            await conn.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", tuple(values))
            await conn.commit()

    async def set_topic(self, client_id: int, topic_id: int):
        async with self.connect() as conn:
            await conn.execute("UPDATE clients SET topic_id = ?, updated_at = ? WHERE id = ?", (topic_id, datetime.utcnow().isoformat(), client_id))
            await conn.commit()

    async def archive_client(self, client_id: int):
        async with self.connect() as conn:
            await conn.execute("UPDATE clients SET is_active = 0, updated_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), client_id))
            await conn.commit()

    async def save_posts(self, client_id: int, post_date: str, posts: list[dict[str, str]]) -> list[aiosqlite.Row]:
        async with self.connect() as conn:
            for idx, post in enumerate(posts):
                await conn.execute(
                    """INSERT OR IGNORE INTO daily_posts(client_id, post_date, slot, body, source_row, sent_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (client_id, post_date, post.get("time"), post["text"], int(post.get("source_row", idx)), datetime.utcnow().isoformat()),
                )
            await conn.commit()
            return await (await conn.execute("SELECT * FROM daily_posts WHERE client_id=? AND post_date=? ORDER BY slot", (client_id, post_date))).fetchall()

    async def posts_sent(self, client_id: int, post_date: str) -> bool:
        async with self.connect() as conn:
            row = await (await conn.execute("SELECT 1 FROM daily_posts WHERE client_id=? AND post_date=? LIMIT 1", (client_id, post_date))).fetchone()
            return bool(row)

    async def save_publication_confirmation(self, client_id: int, day: str, total: int, published: int, status: str, comment: str | None = None):
        async with self.connect() as conn:
            await conn.execute(
                """INSERT INTO publication_confirmations(client_id, confirmation_date, total_posts, published_posts, status, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id, confirmation_date) DO UPDATE SET total_posts=excluded.total_posts, published_posts=excluded.published_posts, status=excluded.status, comment=excluded.comment, created_at=excluded.created_at""",
                (client_id, day, total, published, status, comment, datetime.utcnow().isoformat()),
            )
            await conn.commit()

    async def save_client_result(self, client_id: int, start: str, end: str, responses: int, leads: int, comment: str | None):
        async with self.connect() as conn:
            await conn.execute(
                """INSERT INTO client_results(client_id, period_start, period_end, responses_count, leads_count, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (client_id, start, end, responses, leads, comment, datetime.utcnow().isoformat()),
            )
            await conn.commit()

    async def save_weekly_stats(self, client_id: int, week_start: str, week_end: str, data: dict[str, Any]):
        async with self.connect() as conn:
            await conn.execute(
                """INSERT INTO weekly_stats(client_id, week_start, week_end, views, likes, replies, reposts, quotes, new_followers, telegram_clicks, best_post, manager_comment, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id, week_start) DO UPDATE SET week_end=excluded.week_end, views=excluded.views, likes=excluded.likes, replies=excluded.replies, reposts=excluded.reposts, quotes=excluded.quotes, new_followers=excluded.new_followers, telegram_clicks=excluded.telegram_clicks, best_post=excluded.best_post, manager_comment=excluded.manager_comment, updated_at=excluded.updated_at""",
                (client_id, week_start, week_end, data["views"], data["likes"], data["replies"], data["reposts"], data["quotes"], data["new_followers"], data["telegram_clicks"], data.get("best_post"), data.get("manager_comment"), datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
            )
            await conn.commit()

    async def analytics(self, client_id: int) -> dict[str, Any]:
        async with self.connect() as conn:
            sent = (await (await conn.execute("SELECT COUNT(*) FROM daily_posts WHERE client_id=?", (client_id,))).fetchone())[0]
            published = (await (await conn.execute("SELECT COALESCE(SUM(published_posts),0) FROM publication_confirmations WHERE client_id=?", (client_id,))).fetchone())[0]
            responses, leads = await (await conn.execute("SELECT COALESCE(SUM(responses_count),0), COALESCE(SUM(leads_count),0) FROM client_results WHERE client_id=?", (client_id,))).fetchone()
            latest = await (await conn.execute("SELECT * FROM weekly_stats WHERE client_id=? ORDER BY week_start DESC LIMIT 1", (client_id,))).fetchone()
            return {"sent": sent, "published": published, "discipline": round((published / sent * 100), 1) if sent else 0, "responses": responses, "leads": leads, "latest": latest}

    async def log_event(self, client_id: int, event_type: str, payload: dict[str, Any] | None = None):
        async with self.connect() as conn:
            await conn.execute("INSERT INTO client_events(client_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)", (client_id, event_type, json.dumps(payload or {}, ensure_ascii=False), datetime.utcnow().isoformat()))
            await conn.commit()
