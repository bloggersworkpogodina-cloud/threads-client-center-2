from __future__ import annotations

from datetime import timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from posts import ask_publication_confirmation, send_today_posts
from weekly_workflow import current_content_week, period_text, send_next_monday_task


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


    async def monday_workflow():
        week_start = current_content_week(settings)
        week_end = week_start + timedelta(days=6)
        await bot.send_message(
            settings.admin_id,
            "🗓 <b>Понедельник: аналитика → контент</b>\n\n"
            f"Сегодня закрываем статистику за прошлую неделю и пишем контент "
            f"на {period_text(week_start, week_end)}.\n"
            "Бот будет вести по одному клиенту.",
        )
        await send_next_monday_task(bot, db, settings, week_start)

    scheduler.add_job(daily_send,"cron",hour=settings.daily_send_hour,minute=0,id="daily_posts",replace_existing=True)
    scheduler.add_job(evening_confirmation,"cron",hour=settings.confirmation_hour,minute=0,id="publication_confirmation",replace_existing=True)
    scheduler.add_job(
        monday_workflow,
        "cron",
        day_of_week="mon",
        hour=settings.monday_workflow_hour,
        minute=0,
        id="monday_analytics_content_workflow",
        replace_existing=True,
    )
    return scheduler
