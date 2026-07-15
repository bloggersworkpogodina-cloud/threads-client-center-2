from __future__ import annotations

import json
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    work_group_id: int | None
    db_path: str
    timezone: str
    daily_send_hour: int
    confirmation_hour: int
    google_service_account_json: str | None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required")
    admin_raw = os.getenv("ADMIN_ID", "").strip()
    if not admin_raw:
        raise RuntimeError("ADMIN_ID is required")
    work_group_raw = os.getenv("WORK_GROUP_ID", "").strip()
    google_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip() or None
    if google_json:
        json.loads(google_json)
    return Settings(
        bot_token=token,
        admin_id=int(admin_raw),
        work_group_id=int(work_group_raw) if work_group_raw else None,
        db_path=os.getenv("DB_PATH", "/data/bot.db").strip(),
        timezone=os.getenv("TIMEZONE", "Europe/Moscow").strip(),
        daily_send_hour=int(os.getenv("DAILY_SEND_HOUR", "8")),
        confirmation_hour=int(os.getenv("CONFIRMATION_HOUR", "20")),
        google_service_account_json=google_json,
    )
