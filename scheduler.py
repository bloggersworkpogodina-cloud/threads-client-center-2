from __future__ import annotations

from datetime import date, timedelta
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


    async def analytics_reminder():
        today=date.today(); start=today-timedelta(days=today.weekday())
        missing_baseline=await db.clients_missing_baseline()
        missing_weekly=await db.clients_missing_weekly_stats(start.isoformat())
        lines=["📊 <b>Пора внести аналитику</b>"]
        if missing_baseline:
            lines.append("\nНе заполнен старт проекта: " + ", ".join(c["name"] for c in missing_baseline))
        if missing_weekly:
            lines.append("\nНе заполнена недельная статистика: " + ", ".join(c["name"] for c in missing_weekly))
        if len(lines)>1:
            await bot.send_message(settings.admin_id,"".join(lines))

    scheduler.add_job(daily_send,"cron",hour=settings.daily_send_hour,minute=0,id="daily_posts",replace_existing=True)
    scheduler.add_job(evening_confirmation,"cron",hour=settings.confirmation_hour,minute=0,id="publication_confirmation",replace_existing=True)
    scheduler.add_job(analytics_reminder,"cron",day_of_week="fri",hour=12,minute=0,id="analytics_reminder_day",replace_existing=True)
    scheduler.add_job(analytics_reminder,"cron",day_of_week="fri",hour=18,minute=0,id="analytics_reminder_evening",replace_existing=True)
    return scheduler
