from __future__ import annotations

from datetime import date
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards import publication_kb
from services.topics import topic_log


async def send_today_posts(bot: Bot, db, sheets, settings, client, *, force: bool = False) -> tuple[bool, str]:
    if not client["telegram_id"]:
        return False, "Клиент не подключён"
    if not client["sheet_url"]:
        return False, "Таблица не подключена"
    today = date.today().isoformat()
    if not force and await db.posts_sent(client["id"], today):
        return False, "Ветки уже отправлялись сегодня"
    posts = sheets.read_posts(client["sheet_url"], date.today())
    if not posts:
        return False, "На сегодня нет готовых веток"
    rows = await db.save_posts(client["id"], today, posts)
    await bot.send_message(client["telegram_id"], "<b>📅 Ветки на сегодня</b>")
    for row in rows:
        prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
        await bot.send_message(client["telegram_id"], prefix + row["body"])
    await topic_log(bot, db, settings.work_group_id, client["id"], f"📤 Ветки отправлены клиенту. Количество: {len(rows)}")
    await db.log_event(client["id"], "posts_sent", {"date": today, "count": len(rows)})
    return True, f"Отправлено веток: {len(rows)}"


async def ask_publication_confirmation(bot: Bot, db, settings, client):
    if client["telegram_id"]:
        day = date.today().isoformat()
        await bot.send_message(client["telegram_id"], "Удалось опубликовать сегодняшние ветки?", reply_markup=publication_kb(day))
