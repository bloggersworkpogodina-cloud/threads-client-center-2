import os
import aiosqlite

DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

def normalize_threads(username):
    if not username:
        return None
    return username.strip().lstrip("@").lower()

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            threads_username TEXT NOT NULL UNIQUE,
            telegram_link TEXT,
            telegram_id INTEGER UNIQUE,
            invite_code TEXT UNIQUE NOT NULL,
            sheet_url TEXT,
            content_plan_url TEXT,
            topic_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS delivery_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            post_date TEXT NOT NULL,
            post_time TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL,
            sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(client_id, post_date, post_time, fingerprint)
        );
        """)
        # Миграции существующей базы без удаления данных.
        cur = await db.execute("PRAGMA table_info(clients)")
        columns = {row[1] for row in await cur.fetchall()}
        if "topic_id" not in columns:
            await db.execute("ALTER TABLE clients ADD COLUMN topic_id INTEGER")
        await db.commit()

async def upsert_client(name, threads_username, telegram_link, invite_code):
    username = normalize_threads(threads_username)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM clients WHERE threads_username = ?", (username,))
        row = await cur.fetchone()
        if row:
            await db.execute("""
                UPDATE clients
                SET name=?, telegram_link=?, invite_code=?, is_active=1, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (name, telegram_link, invite_code, row["id"]))
            await db.commit()
            return row["id"]
        cur = await db.execute("""
            INSERT INTO clients(name, threads_username, telegram_link, invite_code)
            VALUES (?, ?, ?, ?)
        """, (name, username, telegram_link, invite_code))
        await db.commit()
        return cur.lastrowid

async def bind_client(invite_code, telegram_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT id FROM clients WHERE invite_code=? AND is_active=1",
            (invite_code,)
        )
        row = await cur.fetchone()
        if not row:
            await db.rollback()
            return False
        client_id = row[0]
        await db.execute("UPDATE clients SET telegram_id=NULL WHERE telegram_id=? AND id!=?", (telegram_id, client_id))
        await db.execute("UPDATE clients SET telegram_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (telegram_id, client_id))
        await db.commit()
        return True

async def get_client(client_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM clients WHERE id=?", (client_id,))
        return await cur.fetchone()

async def get_client_by_tg(telegram_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM clients WHERE telegram_id=? AND is_active=1", (telegram_id,))
        return await cur.fetchone()

async def list_clients():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM clients WHERE is_active=1 ORDER BY name")
        return await cur.fetchall()

async def set_sheet_url(client_id, url):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE clients SET sheet_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (url, client_id))
        await db.commit()

async def set_content_plan_url(client_id, url):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE clients SET content_plan_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (url, client_id))
        await db.commit()

async def set_invite_code(client_id, code):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE clients SET invite_code=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (code, client_id))
        await db.commit()

async def close_client(client_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE clients SET is_active=0, updated_at=CURRENT_TIMESTAMP WHERE id=?", (client_id,))
        await db.commit()

async def was_auto_sent(client_id, post_date, post_time, fingerprint):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT 1 FROM delivery_log
            WHERE client_id=? AND post_date=? AND post_time=? AND fingerprint=?
        """, (client_id, post_date, post_time or "", fingerprint))
        return await cur.fetchone() is not None

async def mark_auto_sent(client_id, post_date, post_time, fingerprint):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO delivery_log(client_id, post_date, post_time, fingerprint)
            VALUES (?, ?, ?, ?)
        """, (client_id, post_date, post_time or "", fingerprint))
        await db.commit()


async def set_client_topic(client_id, topic_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE clients SET topic_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (topic_id, client_id),
        )
        await db.commit()


async def get_client_by_topic(topic_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM clients WHERE topic_id=? AND is_active=1",
            (topic_id,),
        )
        return await cur.fetchone()
