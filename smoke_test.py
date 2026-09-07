from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from db import Database
from admin_handlers import _means_no_posts, content_screens_done_kb, resend_posts_confirm_kb
from posts import POSTS_ALREADY_SENT, send_today_posts
from weekly_workflow import send_next_monday_task


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})


class FakeSheets:
    async def read_posts(self, sheet_url, target_date):
        return [{"time": "09:00", "text": "Тестовая ветка", "source_row": 2}]


async def main() -> None:
    assert all(_means_no_posts(value) for value in ("0", "нет", "Нет постов", "постов не было"))
    assert not _means_no_posts("есть посты")
    no_posts_keyboard = content_screens_done_kb("done", "no_posts")
    assert no_posts_keyboard.inline_keyboard[1][0].callback_data == "no_posts"
    resend_keyboard = resend_posts_confirm_kb(42)
    assert resend_keyboard.inline_keyboard[0][0].callback_data == "client_resend_posts:42"
    assert resend_keyboard.inline_keyboard[1][0].callback_data == "client_resend_cancel:42"

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
        fake_settings = SimpleNamespace(admin_id=1, tz=ZoneInfo("Europe/Moscow"), work_group_id=-100123)
        await send_next_monday_task(fake_bot, db, fake_settings, workflow_week)
        first_button = fake_bot.messages[-1]["reply_markup"].inline_keyboard[0][0]
        assert first_button.callback_data.startswith("monday_content_yes:")

        await db.mark_weekly_content_done(client["id"], workflow_week.isoformat())
        second = await db.create_client("Я-клиент", "@second_test", "@second")
        await send_next_monday_task(fake_bot, db, fake_settings, workflow_week)
        second_button = fake_bot.messages[-1]["reply_markup"].inline_keyboard[0][0]
        assert second_button.callback_data.startswith(f"monday_stats:{second['id']}:")

        # A normal second send is blocked, while the explicit force path resends.
        repeat_client = await db.create_client(
            "Повторная отправка", "@repeat_send_test", None, publish_mode="team"
        )
        await db.update_client_links(repeat_client["id"], sheet_url="https://example.test/sheet")
        await db.set_topic(repeat_client["id"], 77)
        repeat_client = await db.get_client(repeat_client["id"])
        repeat_bot = FakeBot()
        fake_sheets = FakeSheets()
        first_ok, _ = await send_today_posts(
            repeat_bot, db, fake_sheets, fake_settings, repeat_client
        )
        assert first_ok
        messages_after_first = len(repeat_bot.messages)
        second_ok, second_text = await send_today_posts(
            repeat_bot, db, fake_sheets, fake_settings, repeat_client
        )
        assert not second_ok and second_text == POSTS_ALREADY_SENT
        assert len(repeat_bot.messages) == messages_after_first
        forced_ok, _ = await send_today_posts(
            repeat_bot, db, fake_sheets, fake_settings, repeat_client, force=True
        )
        assert forced_ok
        assert len(repeat_bot.messages) == messages_after_first + 2

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
