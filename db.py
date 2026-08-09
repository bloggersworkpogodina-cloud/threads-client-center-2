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
            contract_file_id TEXT,
            policy_file_id TEXT,
            services TEXT,
            service_price INTEGER,
            billing_start TEXT,
            legal_name TEXT,
            signer_name TEXT,
            customer_type TEXT,
            customer_inn TEXT,
            customer_tax_status TEXT,
            customer_ogrn TEXT,
            customer_kpp TEXT,
            customer_address TEXT,
            customer_email TEXT,
            customer_phone TEXT,
            signer_authority TEXT,
            contract_version TEXT,
            policy_version TEXT,
            publish_mode TEXT NOT NULL DEFAULT 'client',
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
            total_views INTEGER NOT NULL DEFAULT 0,
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

        CREATE TABLE IF NOT EXISTS client_baseline (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id),
            total_views INTEGER NOT NULL DEFAULT 0,
            threads_followers INTEGER NOT NULL DEFAULT 0,
            telegram_followers INTEGER NOT NULL DEFAULT 0,
            weekly_leads INTEGER NOT NULL DEFAULT 0,
            overview_file_id TEXT NOT NULL,
            content_file_id TEXT NOT NULL,
            telegram_file_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS service_acts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id),
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            act_number TEXT NOT NULL,
            services_text TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            results_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'draft',
            draft_file_id TEXT,
            signed_file_id TEXT,
            sent_at TEXT,
            signed_at TEXT,
            signer_name TEXT,
            signer_telegram_id INTEGER,
            signer_telegram_username TEXT,
            remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(client_id, period_start, period_end)
        );

        CREATE TABLE IF NOT EXISTS client_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL REFERENCES clients(id),
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS client_consents (
            client_id INTEGER PRIMARY KEY REFERENCES clients(id),
            signer_name TEXT NOT NULL,
            telegram_id INTEGER NOT NULL,
            telegram_username TEXT,
            contract_file_id TEXT NOT NULL,
            policy_file_id TEXT NOT NULL,
            accepted_at TEXT NOT NULL,
            contract_accepted_at TEXT,
            pd_consent_at TEXT
        );
        """
        async with self.connect() as conn:
            await conn.executescript(schema)
            client_columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(clients)")).fetchall()}
            if "publish_mode" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN publish_mode TEXT NOT NULL DEFAULT 'client'")

            if "contract_file_id" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN contract_file_id TEXT")
            if "policy_file_id" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN policy_file_id TEXT")
            if "services" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN services TEXT")
            if "service_price" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN service_price INTEGER")
            if "billing_start" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN billing_start TEXT")
            if "legal_name" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN legal_name TEXT")
            for column, definition in {
                "signer_name": "TEXT",
                "customer_type": "TEXT",
                "customer_inn": "TEXT",
                "customer_tax_status": "TEXT",
                "customer_ogrn": "TEXT",
                "customer_kpp": "TEXT",
                "customer_address": "TEXT",
                "customer_email": "TEXT",
                "customer_phone": "TEXT",
                "signer_authority": "TEXT",
            }.items():
                if column not in client_columns:
                    await conn.execute(f"ALTER TABLE clients ADD COLUMN {column} {definition}")
            if "contract_version" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN contract_version TEXT")
            if "policy_version" not in client_columns:
                await conn.execute("ALTER TABLE clients ADD COLUMN policy_version TEXT")

            consent_columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(client_consents)")).fetchall()}
            if "contract_accepted_at" not in consent_columns:
                await conn.execute("ALTER TABLE client_consents ADD COLUMN contract_accepted_at TEXT")
            if "pd_consent_at" not in consent_columns:
                await conn.execute("ALTER TABLE client_consents ADD COLUMN pd_consent_at TEXT")

            baseline_columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(client_baseline)")).fetchall()}
            if "total_views" not in baseline_columns:
                await conn.execute("ALTER TABLE client_baseline ADD COLUMN total_views INTEGER NOT NULL DEFAULT 0")

            weekly_columns = {row[1] for row in await (await conn.execute("PRAGMA table_info(weekly_stats)")).fetchall()}
            for column, definition in {
                "total_views": "INTEGER NOT NULL DEFAULT 0",
                "threads_followers": "INTEGER NOT NULL DEFAULT 0",
                "telegram_followers": "INTEGER NOT NULL DEFAULT 0",
                "applications": "INTEGER NOT NULL DEFAULT 0",
                "overview_file_id": "TEXT",
                "content_file_id": "TEXT",
                "telegram_file_id": "TEXT",
            }.items():
                if column not in weekly_columns:
                    await conn.execute(f"ALTER TABLE weekly_stats ADD COLUMN {column} {definition}")
            await conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                ("embedded_schema_v1", datetime.utcnow().isoformat()),
            )
            await conn.commit()
            required = {
                "clients", "daily_posts", "publication_confirmations",
                "client_results", "weekly_stats", "client_baseline", "service_acts", "client_events", "client_consents",
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

    async def create_client(self, name: str, threads_username: str, telegram_username: str | None, publish_mode: str = "client", services: str | None = None, service_price: int | None = None, billing_start: str | None = None) -> aiosqlite.Row:
        threads = self.normalize_threads(threads_username)
        telegram = self.normalize_telegram(telegram_username)
        invite_code = secrets.token_urlsafe(10)
        publish_mode = publish_mode if publish_mode in {"client", "team"} else "client"
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            try:
                cur = await conn.execute(
                    """
                    INSERT INTO clients(name, threads_username_normalized, telegram_username, invite_code, publish_mode, services, service_price, billing_start, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (name.strip(), threads, telegram, invite_code, publish_mode, (services or "").strip() or None, service_price, billing_start, now, now),
                )
                await conn.commit()
            except aiosqlite.IntegrityError as exc:
                raise ValueError("Активный клиент с таким Threads username уже существует") from exc
            row = await (await conn.execute("SELECT * FROM clients WHERE id = ?", (cur.lastrowid,))).fetchone()
            return row


    async def update_client_terms(self, client_id: int, services: str, service_price: int, billing_start: str) -> None:
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            await conn.execute(
                """UPDATE clients
                   SET services = ?, service_price = ?, billing_start = ?,
                       contract_file_id = NULL, policy_file_id = NULL,
                       contract_version = NULL, policy_version = NULL,
                       updated_at = ?
                   WHERE id = ?""",
                ((services or "").strip() or None, int(service_price), billing_start, now, client_id),
            )
            await conn.execute("DELETE FROM client_consents WHERE client_id = ?", (client_id,))
            await conn.commit()

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
            cur = await conn.execute(f"UPDATE clients SET {', '.join(fields)} WHERE id = ?", tuple(values))
            if cur.rowcount == 0:
                await conn.rollback()
                raise LookupError("Клиент больше не существует в текущей базе")
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

    async def save_baseline(self, client_id: int, data: dict[str, Any]):
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            await conn.execute(
                """INSERT INTO client_baseline(client_id, total_views, threads_followers, telegram_followers, weekly_leads, overview_file_id, content_file_id, telegram_file_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET total_views=excluded.total_views, threads_followers=excluded.threads_followers, telegram_followers=excluded.telegram_followers, weekly_leads=excluded.weekly_leads, overview_file_id=excluded.overview_file_id, content_file_id=excluded.content_file_id, telegram_file_id=excluded.telegram_file_id, updated_at=excluded.updated_at""",
                (client_id, data["total_views"], data["threads_followers"], data["telegram_followers"], data["weekly_leads"], data["overview_file_id"], data["content_file_id"], data.get("telegram_file_id"), now, now),
            )
            await conn.commit()

    async def get_baseline(self, client_id: int):
        async with self.connect() as conn:
            return await (await conn.execute("SELECT * FROM client_baseline WHERE client_id=?", (client_id,))).fetchone()

    async def clients_missing_baseline(self):
        async with self.connect() as conn:
            return await (await conn.execute("""SELECT c.* FROM clients c LEFT JOIN client_baseline b ON b.client_id=c.id WHERE c.is_active=1 AND b.client_id IS NULL ORDER BY c.name COLLATE NOCASE""")).fetchall()

    async def save_weekly_analytics(self, client_id: int, week_start: str, week_end: str, data: dict[str, Any]):
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            await conn.execute(
                """INSERT INTO weekly_stats(client_id, week_start, week_end, total_views, views, likes, replies, reposts, quotes, new_followers, telegram_clicks, best_post, manager_comment, created_at, updated_at, threads_followers, telegram_followers, applications, overview_file_id, content_file_id, telegram_file_id)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id, week_start) DO UPDATE SET week_end=excluded.week_end, total_views=excluded.total_views, views=excluded.views, threads_followers=excluded.threads_followers, telegram_followers=excluded.telegram_followers, applications=excluded.applications, overview_file_id=excluded.overview_file_id, content_file_id=excluded.content_file_id, telegram_file_id=excluded.telegram_file_id, updated_at=excluded.updated_at""",
                (client_id, week_start, week_end, data["total_views"], data["views"], now, now, data["threads_followers"], data["telegram_followers"], data["applications"], data["overview_file_id"], data["content_file_id"], data.get("telegram_file_id")),
            )
            await conn.commit()

    async def get_weekly_history(self, client_id: int, limit: int = 12):
        async with self.connect() as conn:
            return await (await conn.execute(
                "SELECT * FROM weekly_stats WHERE client_id=? ORDER BY week_start DESC LIMIT ?",
                (client_id, limit),
            )).fetchall()

    async def previous_total_views(self, client_id: int) -> int:
        totals = await self.previous_account_totals(client_id)
        return totals["total_views"]

    async def previous_account_totals(self, client_id: int) -> dict[str, int]:
        async with self.connect() as conn:
            latest = await (await conn.execute(
                """SELECT total_views, threads_followers, telegram_followers
                   FROM weekly_stats
                   WHERE client_id=?
                   ORDER BY week_start DESC
                   LIMIT 1""",
                (client_id,),
            )).fetchone()

            # A legacy weekly row may have been created by the old statistics
            # form without cumulative account values (they were stored as 0).
            # In that case the first comparison must come from the project
            # baseline, not from zero.
            if latest and any(int(latest[k] or 0) > 0 for k in ("total_views", "threads_followers", "telegram_followers")):
                return {
                    "total_views": int(latest["total_views"] or 0),
                    "threads_followers": int(latest["threads_followers"] or 0),
                    "telegram_followers": int(latest["telegram_followers"] or 0),
                }

            baseline = await (await conn.execute(
                """SELECT total_views, threads_followers, telegram_followers
                   FROM client_baseline
                   WHERE client_id=?""",
                (client_id,),
            )).fetchone()

            if baseline:
                return {
                    "total_views": int(baseline["total_views"] or 0),
                    "threads_followers": int(baseline["threads_followers"] or 0),
                    "telegram_followers": int(baseline["telegram_followers"] or 0),
                }

            return {
                "total_views": 0,
                "threads_followers": 0,
                "telegram_followers": 0,
            }

    async def clients_missing_weekly_stats(self, week_start: str):
        async with self.connect() as conn:
            return await (await conn.execute("""SELECT c.* FROM clients c LEFT JOIN weekly_stats w ON w.client_id=c.id AND w.week_start=? WHERE c.is_active=1 AND w.id IS NULL ORDER BY c.name COLLATE NOCASE""", (week_start,))).fetchall()

    async def analytics(self, client_id: int) -> dict[str, Any]:
        async with self.connect() as conn:
            sent = (await (await conn.execute("SELECT COUNT(*) FROM daily_posts WHERE client_id=?", (client_id,))).fetchone())[0]
            published = (await (await conn.execute("SELECT COALESCE(SUM(published_posts),0) FROM publication_confirmations WHERE client_id=?", (client_id,))).fetchone())[0]
            responses, leads = await (await conn.execute("SELECT COALESCE(SUM(responses_count),0), COALESCE(SUM(leads_count),0) FROM client_results WHERE client_id=?", (client_id,))).fetchone()
            latest = await (await conn.execute("SELECT * FROM weekly_stats WHERE client_id=? ORDER BY week_start DESC LIMIT 1", (client_id,))).fetchone()
            baseline = await (await conn.execute("SELECT * FROM client_baseline WHERE client_id=?", (client_id,))).fetchone()
            return {"sent": sent, "published": published, "discipline": round((published / sent * 100), 1) if sent else 0, "responses": responses, "leads": leads, "latest": latest, "baseline": baseline}

    async def log_event(self, client_id: int, event_type: str, payload: dict[str, Any] | None = None) -> bool:
        """Write an event only for an existing client.

        Telegram can keep old inline cards after a Railway redeploy. If the database
        was recreated, callbacks from such cards must not crash the bot with a
        FOREIGN KEY error.
        """
        async with self.connect() as conn:
            exists = await (await conn.execute("SELECT 1 FROM clients WHERE id = ?", (client_id,))).fetchone()
            if not exists:
                return False
            await conn.execute(
                "INSERT INTO client_events(client_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (client_id, event_type, json.dumps(payload or {}, ensure_ascii=False), datetime.utcnow().isoformat()),
            )
            await conn.commit()
            return True


    async def act_results(self, client_id: int, period_start: str, period_end: str) -> dict[str, int]:
        async with self.connect() as conn:
            publication = await (await conn.execute(
                """SELECT COALESCE(SUM(published_posts), 0) AS published
                   FROM publication_confirmations
                   WHERE client_id=? AND confirmation_date BETWEEN ? AND ?""",
                (client_id, period_start, period_end),
            )).fetchone()

            rows = await (await conn.execute(
                """SELECT * FROM weekly_stats
                   WHERE client_id=? AND week_end >= ? AND week_start <= ?
                   ORDER BY week_start ASC""",
                (client_id, period_start, period_end),
            )).fetchall()

            baseline = await (await conn.execute(
                "SELECT * FROM client_baseline WHERE client_id=?",
                (client_id,),
            )).fetchone()

            if rows:
                first = rows[0]
                last = rows[-1]
                first_views = int(baseline["total_views"] or 0) if baseline else max(int(first["total_views"] or 0) - int(first["views"] or 0), 0)
                first_threads = int(baseline["threads_followers"] or 0) if baseline else int(first["threads_followers"] or 0)
                first_telegram = int(baseline["telegram_followers"] or 0) if baseline else int(first["telegram_followers"] or 0)
                end_views = int(last["total_views"] or 0)
                end_threads = int(last["threads_followers"] or 0)
                end_telegram = int(last["telegram_followers"] or 0)
                applications = sum(int(row["applications"] or 0) for row in rows)
            else:
                first_views = int(baseline["total_views"] or 0) if baseline else 0
                first_threads = int(baseline["threads_followers"] or 0) if baseline else 0
                first_telegram = int(baseline["telegram_followers"] or 0) if baseline else 0
                end_views = first_views
                end_threads = first_threads
                end_telegram = first_telegram
                applications = 0

            return {
                "published_posts": int(publication["published"] or 0),
                "analytics_count": 1,
                "views_start": first_views,
                "views_end": end_views,
                "views_growth": end_views - first_views,
                "threads_start": first_threads,
                "threads_end": end_threads,
                "threads_growth": end_threads - first_threads,
                "telegram_start": first_telegram,
                "telegram_end": end_telegram,
                "telegram_growth": end_telegram - first_telegram,
                "applications": applications,
            }

    async def save_service_act(
        self,
        client_id: int,
        period_start: str,
        period_end: str,
        services_text: str,
        amount: int,
        results: dict,
    ):
        import json
        now = datetime.utcnow().isoformat()
        act_number = f"{client_id}-{period_end.replace('-', '')}"
        async with self.connect() as conn:
            await conn.execute(
                """INSERT INTO service_acts(
                    client_id, period_start, period_end, act_number,
                    services_text, amount, results_json, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                ON CONFLICT(client_id, period_start, period_end) DO UPDATE SET
                    services_text=excluded.services_text,
                    amount=excluded.amount,
                    results_json=excluded.results_json,
                    status='draft',
                    draft_file_id=NULL,
                    signed_file_id=NULL,
                    sent_at=NULL,
                    signed_at=NULL,
                    signer_name=NULL,
                    signer_telegram_id=NULL,
                    signer_telegram_username=NULL,
                    remarks=NULL,
                    updated_at=excluded.updated_at""",
                (
                    client_id, period_start, period_end, act_number,
                    services_text.strip(), int(amount), json.dumps(results, ensure_ascii=False),
                    now, now,
                ),
            )
            await conn.commit()
            return await (await conn.execute(
                "SELECT * FROM service_acts WHERE client_id=? AND period_start=? AND period_end=?",
                (client_id, period_start, period_end),
            )).fetchone()

    async def get_service_act(self, act_id: int):
        async with self.connect() as conn:
            return await (await conn.execute(
                "SELECT * FROM service_acts WHERE id=?",
                (act_id,),
            )).fetchone()

    async def get_service_act_for_period(self, client_id: int, period_start: str, period_end: str):
        async with self.connect() as conn:
            return await (await conn.execute(
                """SELECT * FROM service_acts
                   WHERE client_id=? AND period_start=? AND period_end=?""",
                (client_id, period_start, period_end),
            )).fetchone()

    async def set_service_act_sent(self, act_id: int, draft_file_id: str) -> None:
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            await conn.execute(
                """UPDATE service_acts
                   SET status='sent', draft_file_id=?, sent_at=?, updated_at=?
                   WHERE id=?""",
                (draft_file_id, now, now, act_id),
            )
            await conn.commit()

    async def sign_service_act(
        self,
        act_id: int,
        signer_name: str,
        telegram_id: int,
        telegram_username: str | None,
        signed_file_id: str | None = None,
    ) -> None:
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            await conn.execute(
                """UPDATE service_acts
                   SET status='signed', signed_file_id=?, signed_at=?,
                       signer_name=?, signer_telegram_id=?,
                       signer_telegram_username=?, remarks=NULL, updated_at=?
                   WHERE id=?""",
                (
                    signed_file_id, now, signer_name.strip(), telegram_id,
                    telegram_username, now, act_id,
                ),
            )
            await conn.commit()

    async def set_service_act_signed_file(self, act_id: int, signed_file_id: str) -> None:
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE service_acts SET signed_file_id=?, updated_at=? WHERE id=?",
                (signed_file_id, datetime.utcnow().isoformat(), act_id),
            )
            await conn.commit()

    async def set_service_act_remarks(self, act_id: int, remarks: str) -> None:
        async with self.connect() as conn:
            await conn.execute(
                """UPDATE service_acts
                   SET status='remarks', remarks=?, updated_at=?
                   WHERE id=?""",
                (remarks.strip(), datetime.utcnow().isoformat(), act_id),
            )
            await conn.commit()

    async def set_client_documents(
        self,
        client_id: int,
        contract_file_id: str | None = None,
        policy_file_id: str | None = None,
        contract_version: str | None = None,
        policy_version: str | None = None,
        reset_acceptance: bool = True,
    ) -> None:
        async with self.connect() as conn:
            row = await (await conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,))).fetchone()
            if not row:
                raise LookupError("Client not found")
            contract = contract_file_id if contract_file_id is not None else row["contract_file_id"]
            policy = policy_file_id if policy_file_id is not None else row["policy_file_id"]
            cver = contract_version if contract_version is not None else row["contract_version"]
            pver = policy_version if policy_version is not None else row["policy_version"]
            await conn.execute(
                """UPDATE clients
                   SET contract_file_id = ?, policy_file_id = ?,
                       contract_version = ?, policy_version = ?, updated_at = ?
                   WHERE id = ?""",
                (contract, policy, cver, pver, datetime.utcnow().isoformat(), client_id),
            )
            if reset_acceptance:
                await conn.execute("DELETE FROM client_consents WHERE client_id = ?", (client_id,))
            await conn.commit()

    async def save_client_legal_details(self, client_id: int, data: dict[str, Any]) -> None:
        fields = [
            "legal_name", "signer_name", "customer_type", "customer_inn",
            "customer_tax_status", "customer_ogrn", "customer_kpp",
            "customer_address", "customer_email", "customer_phone",
            "signer_authority",
        ]
        values = [data.get(field) for field in fields]
        async with self.connect() as conn:
            await conn.execute(
                """UPDATE clients SET
                    legal_name=?, signer_name=?, customer_type=?, customer_inn=?,
                    customer_tax_status=?, customer_ogrn=?, customer_kpp=?,
                    customer_address=?, customer_email=?, customer_phone=?,
                    signer_authority=?, contract_file_id=NULL, policy_file_id=NULL,
                    contract_version=NULL, policy_version=NULL, updated_at=?
                   WHERE id=?""",
                (*values, datetime.utcnow().isoformat(), client_id),
            )
            await conn.execute("DELETE FROM client_consents WHERE client_id=?", (client_id,))
            await conn.commit()

    async def set_client_legal_name(self, client_id: int, legal_name: str) -> None:
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE clients SET legal_name = ?, updated_at = ? WHERE id = ?",
                (legal_name.strip(), datetime.utcnow().isoformat(), client_id),
            )
            await conn.commit()

    async def invalidate_client_documents(self, client_id: int) -> None:
        async with self.connect() as conn:
            await conn.execute(
                """UPDATE clients
                   SET contract_file_id = NULL, policy_file_id = NULL,
                       contract_version = NULL, policy_version = NULL,
                       updated_at = ?
                   WHERE id = ?""",
                (datetime.utcnow().isoformat(), client_id),
            )
            await conn.execute("DELETE FROM client_consents WHERE client_id = ?", (client_id,))
            await conn.commit()

    async def get_client_consent(self, client_id: int):
        async with self.connect() as conn:
            return await (await conn.execute(
                "SELECT * FROM client_consents WHERE client_id = ?",
                (client_id,),
            )).fetchone()

    async def save_contract_acceptance(
        self,
        client_id: int,
        signer_name: str,
        telegram_id: int,
        telegram_username: str | None,
        contract_file_id: str,
        policy_file_id: str,
    ) -> None:
        now = datetime.utcnow().isoformat()
        async with self.connect() as conn:
            await conn.execute(
                """
                INSERT INTO client_consents(
                    client_id, signer_name, telegram_id, telegram_username,
                    contract_file_id, policy_file_id, accepted_at,
                    contract_accepted_at, pd_consent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(client_id) DO UPDATE SET
                    signer_name=excluded.signer_name,
                    telegram_id=excluded.telegram_id,
                    telegram_username=excluded.telegram_username,
                    contract_file_id=excluded.contract_file_id,
                    policy_file_id=excluded.policy_file_id,
                    accepted_at=excluded.accepted_at,
                    contract_accepted_at=excluded.contract_accepted_at,
                    pd_consent_at=NULL
                """,
                (
                    client_id, signer_name.strip(), telegram_id, telegram_username,
                    contract_file_id, policy_file_id, now, now,
                ),
            )
            await conn.commit()

    async def save_pd_consent(self, client_id: int) -> None:
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE client_consents SET pd_consent_at = ?, accepted_at = ? WHERE client_id = ?",
                (datetime.utcnow().isoformat(), datetime.utcnow().isoformat(), client_id),
            )
            await conn.commit()

    async def documents_fully_accepted(self, client_id: int) -> bool:
        row = await self.get_client_consent(client_id)
        return bool(row and row["contract_accepted_at"] and row["pd_consent_at"])
