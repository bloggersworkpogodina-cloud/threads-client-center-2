from __future__ import annotations

import asyncio
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ClientAlreadyExistsError(ValueError):
    def __init__(self, client_id: int):
        super().__init__("Активный клиент с таким Threads username уже существует")
        self.client_id = client_id


class ClientNotFoundError(LookupError):
    pass


def normalize_threads_username(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lstrip("@").strip().lower()
    return normalized or None


@dataclass(slots=True)
class Database:
    path: str
    migrations_dir: str | None = None

    def _connect(self) -> sqlite3.Connection:
        db_path = Path(self.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        migrations_dir = Path(self.migrations_dir or Path(__file__).with_name("migrations"))
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for migration in sorted(migrations_dir.glob("*.sql")):
                if migration.name in applied:
                    continue
                conn.executescript(migration.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)",
                    (migration.name,),
                )
            conn.commit()

    async def add_client(
        self,
        name: str,
        threads_username: str | None,
        telegram_link: str | None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._add_client_sync, name, threads_username, telegram_link
        )

    def _add_client_sync(
        self, name: str, threads_username: str | None, telegram_link: str | None
    ) -> dict[str, Any]:
        normalized = normalize_threads_username(threads_username)
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Имя клиента не может быть пустым")
        with self._connect() as conn:
            if normalized:
                existing = conn.execute(
                    """
                    SELECT id FROM clients
                    WHERE threads_username_normalized = ? AND is_active = 1
                    """,
                    (normalized,),
                ).fetchone()
                if existing:
                    raise ClientAlreadyExistsError(existing["id"])
            invite_code = secrets.token_urlsafe(18)
            cursor = conn.execute(
                """
                INSERT INTO clients(
                    name, invite_code, threads_username_normalized, telegram_link
                ) VALUES (?, ?, ?, ?)
                """,
                (clean_name, invite_code, normalized, telegram_link),
            )
            conn.commit()
            return self._get_client_sync(cursor.lastrowid)

    async def get_client(self, client_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_client_sync, client_id)

    def _get_client_sync(self, client_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
            return dict(row) if row else None

    async def get_client_by_telegram(self, telegram_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_client_by_telegram_sync, telegram_id)

    def _get_client_by_telegram_sync(self, telegram_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clients WHERE telegram_id = ? AND is_active = 1",
                (telegram_id,),
            ).fetchone()
            return dict(row) if row else None

    async def get_client_by_topic(self, topic_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_client_by_topic_sync, topic_id)

    def _get_client_by_topic_sync(self, topic_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clients WHERE topic_id = ? AND is_active = 1",
                (topic_id,),
            ).fetchone()
            return dict(row) if row else None

    async def list_active_clients(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_active_clients_sync)

    def _list_active_clients_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM clients WHERE is_active = 1 ORDER BY name COLLATE NOCASE"
            ).fetchall()
            return [dict(row) for row in rows]

    async def bind_invite(self, invite_code: str, telegram_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._bind_invite_sync, invite_code, telegram_id)

    def _bind_invite_sync(self, invite_code: str, telegram_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            target = conn.execute(
                "SELECT id FROM clients WHERE invite_code = ? AND is_active = 1",
                (invite_code,),
            ).fetchone()
            if not target:
                return None
            # Один Telegram-аккаунт может быть привязан только к одной актуальной записи.
            conn.execute(
                "UPDATE clients SET telegram_id = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE telegram_id = ? AND id != ?",
                (telegram_id, target["id"]),
            )
            conn.execute(
                "UPDATE clients SET telegram_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (telegram_id, target["id"]),
            )
            conn.commit()
            return self._get_client_sync(target["id"])

    async def set_sheet_url(self, client_id: int, url: str | None) -> None:
        await asyncio.to_thread(self._update_field_sync, client_id, "sheet_url", url)

    async def set_content_plan_url(self, client_id: int, url: str | None) -> None:
        await asyncio.to_thread(self._update_field_sync, client_id, "content_plan_url", url)

    async def set_topic_id(self, client_id: int, topic_id: int | None) -> None:
        await asyncio.to_thread(self._update_field_sync, client_id, "topic_id", topic_id)

    def _update_field_sync(self, client_id: int, field: str, value: Any) -> None:
        allowed = {"sheet_url", "content_plan_url", "topic_id"}
        if field not in allowed:
            raise ValueError("Недопустимое поле")
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE clients SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (value, client_id),
            )
            if cursor.rowcount == 0:
                raise ClientNotFoundError(client_id)
            conn.commit()

    async def archive_client(self, client_id: int) -> None:
        await asyncio.to_thread(self._archive_client_sync, client_id)

    def _archive_client_sync(self, client_id: int) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE clients SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (client_id,),
            )
            if cursor.rowcount == 0:
                raise ClientNotFoundError(client_id)
            conn.commit()
