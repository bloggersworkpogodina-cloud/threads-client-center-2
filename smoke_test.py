from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from db import Database
from admin_handlers import _means_no_posts, content_screens_done_kb
from weekly_workflow import send_next_monday_task


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})


async def main() -> None:
    assert all(_means_no_posts(value) for value in ("0", "нет", "Нет постов", "постов не было"))
    assert not _means_no_posts("есть посты")
    no_posts_keyboard = content_screens_done_kb("done", "no_posts")
    assert no_posts_keyboard.inline_keyboard[1][0].callback_data == "no_posts"

    path = tempfile.mktemp(suffix=".db")
    try:
        db = Database(path)
        await db.migrate()
        client = await db.create_client("Тест", "@analytics_test", "@tester")
        await db.save_baseline(client["id"], {
            "total_views": 5000,
            "threads_followers": 100,
            "telegram_followers": 20,
            "weekly_leads": 2,
            "overview_file_id": "overview",
            "content_file_id": "content",
            "telegram_file_id": None,
        })
        today = date.today()
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        await db.save_weekly_analytics(client["id"], start.isoformat(), end.isoformat(), {
            "total_views": 6000,
            "views": 1000,
            "threads_followers": 120,
            "telegram_followers": 25,
            "applications": 4,
            "overview_file_id": "weekly_overview",
            "content_file_id": "weekly_content",
            "telegram_file_id": None,
        })
        analytics = await db.analytics(client["id"])
        assert analytics["baseline"]["threads_followers"] == 100
        assert analytics["latest"]["threads_followers"] == 120
        assert await db.has_weekly_stats(client["id"], start.isoformat())
        assert not await db.weekly_content_done(client["id"], start.isoformat())
        await db.mark_weekly_content_done(client["id"], start.isoformat())
        assert await db.weekly_content_done(client["id"], start.isoformat())

        # The Monday workflow must persist progress and move to the next client.
        workflow_week = start + timedelta(days=7)
        fake_bot = FakeBot()
        fake_settings = SimpleNamespace(admin_id=1, tz=ZoneInfo("Europe/Moscow"))
        await send_next_monday_task(fake_bot, db, fake_settings, workflow_week)
        first_button = fake_bot.messages[-1]["reply_markup"].inline_keyboard[0][0]
        assert first_button.callback_data.startswith("monday_content_yes:")

        await db.mark_weekly_content_done(client["id"], workflow_week.isoformat())
        second = await db.create_client("Я-клиент", "@second_test", "@second")
        await send_next_monday_task(fake_bot, db, fake_settings, workflow_week)
        second_button = fake_bot.messages[-1]["reply_markup"].inline_keyboard[0][0]
        assert second_button.callback_data.startswith(f"monday_stats:{second['id']}:")

        await db.archive_client(client["id"])
        archived = await db.get_client(client["id"])
        assert archived["is_active"] == 0
        restored = await db.restore_client(client["id"])
        assert restored["is_active"] == 1
        await db.migrate()
        assert await db.get_client(client["id"])
        print("ANALYTICS SMOKE TEST: OK")
    finally:
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    asyncio.run(main())
