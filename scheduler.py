from __future__ import annotations

from datetime import date
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from posts import ask_publication_confirmation, send_today_posts


def build_scheduler(bot, db, sheets, settings):
    scheduler=AsyncIOScheduler(timezone=settings.timezone)

    async def daily_send():
        for c in await db.list_clients(True):
            try: await send_today_posts(bot,db,sheets,settings,c)
            except Exception as exc: await db.log_event(c["id"],"error",{"stage":"daily_send","error":str(exc)})

    async def evening_confirmation():
        for c in await db.list_clients(True):
            try: await ask_publication_confirmation(bot,db,settings,c)
            except Exception as exc: await db.log_event(c["id"],"error",{"stage":"confirmation","error":str(exc)})

    scheduler.add_job(daily_send,"cron",hour=settings.daily_send_hour,minute=0,id="daily_posts",replace_existing=True)
    scheduler.add_job(evening_confirmation,"cron",hour=settings.confirmation_hour,minute=0,id="publication_confirmation",replace_existing=True)
    return scheduler
