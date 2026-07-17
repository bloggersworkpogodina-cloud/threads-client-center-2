from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta

from db import Database


async def main() -> None:
    path = tempfile.mktemp(suffix=".db")
    try:
        db = Database(path)
        await db.migrate()
        client = await db.create_client("Тест", "@analytics_test", "@tester")
        await db.save_baseline(client["id"], {
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
            "threads_followers": 120,
            "telegram_followers": 25,
            "views": 1000,
            "applications": 4,
            "overview_file_id": "weekly_overview",
            "content_file_id": "weekly_content",
            "telegram_file_id": None,
        })
        analytics = await db.analytics(client["id"])
        assert analytics["baseline"]["threads_followers"] == 100
        assert analytics["latest"]["threads_followers"] == 120
        await db.migrate()
        assert await db.get_client(client["id"])
        print("ANALYTICS SMOKE TEST: OK")
    finally:
        if os.path.exists(path):
            os.remove(path)


if __name__ == "__main__":
    asyncio.run(main())
