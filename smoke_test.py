from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from db import ClientAlreadyExistsError, Database, normalize_threads_username


async def run() -> None:
    assert normalize_threads_username("  @Test_User  ") == "test_user"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        migrations = str(Path(__file__).with_name("migrations"))
        db = Database(db_path, migrations)
        await db.initialize()

        client = await db.add_client("Тест", "@Test_User", "https://t.me/test")
        assert client["threads_username_normalized"] == "test_user"
        assert len(await db.list_active_clients()) == 1

        try:
            await db.add_client("Дубль", " test_user ", None)
        except ClientAlreadyExistsError as exc:
            assert exc.client_id == client["id"]
        else:
            raise AssertionError("Дубль активного username не заблокирован")

        bound = await db.bind_invite(client["invite_code"], 123456)
        assert bound and bound["telegram_id"] == 123456
        rebound = await db.bind_invite(client["invite_code"], 123456)
        assert rebound and rebound["telegram_id"] == 123456

        await db.set_sheet_url(client["id"], "https://docs.google.com/spreadsheets/d/test")
        await db.set_content_plan_url(client["id"], "https://docs.google.com/document/d/test")
        await db.set_topic_id(client["id"], 777)
        assert (await db.get_client_by_topic(777))["id"] == client["id"]

        await db.archive_client(client["id"])
        assert await db.list_active_clients() == []
        archived = await db.get_client(client["id"])
        assert archived and archived["is_active"] == 0

        replacement = await db.add_client("Новый тест", "@TEST_USER", None)
        assert replacement["id"] != client["id"]

    print("SMOKE TEST: OK")


if __name__ == "__main__":
    asyncio.run(run())
