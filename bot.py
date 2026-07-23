from __future__ import annotations

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import load_settings
from db import Database
import admin_handlers as admin
import client_handlers as client
from scheduler import build_scheduler
from google_sheets import GoogleSheetsService


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings=load_settings()
    db=Database(settings.db_path)
    await db.migrate()
    sheets=GoogleSheetsService(settings.google_service_account_json)
    bot=Bot(settings.bot_token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp=Dispatcher()
    admin.configure(db, settings, sheets)
    client.configure(db, settings, sheets)
    dp.include_router(admin.router)
    dp.include_router(client.router)
    scheduler=build_scheduler(bot,db,sheets,settings); scheduler.start()
    logging.info("Database migrated. Scheduler started. Polling begins.")
    await dp.start_polling(bot)


if __name__=="__main__":
    asyncio.run(main())
