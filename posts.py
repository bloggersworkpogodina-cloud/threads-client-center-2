from __future__ import annotations

from datetime import datetime

from aiogram import Bot

from keyboards import publication_kb
from topics import topic_log


def today_for(settings):
    return datetime.now(settings.tz).date()


async def send_today_posts(bot: Bot, db, sheets, settings, client, *, force: bool = False) -> tuple[bool, str]:
    if not client:
        return False, "Клиент не найден"
    if not client["telegram_id"]:
        return False, "Клиент ещё не подключил личный кабинет"
    if not client["sheet_url"]:
        return False, "Таблица клиента не подключена"

    target_date = today_for(settings)
    day = target_date.isoformat()
    if not force and await db.posts_sent(client["id"], day):
        return False, "Ветки на сегодня уже отправлены"

    posts = await sheets.read_posts(client["sheet_url"], target_date)
    if not posts:
        return False, "На сегодня нет строк со статусом «Готово»"

    rows = await db.save_posts(client["id"], day, posts)
    await bot.send_message(
        client["telegram_id"],
        f"<b>📅 Ветки на {target_date.strftime('%d.%m.%Y')}</b>\n\nГотово к публикации: {len(rows)}",
    )
    for row in rows:
        prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
        await bot.send_message(client["telegram_id"], prefix + row["body"])

    await topic_log(
        bot,
        db,
        settings.work_group_id,
        client["id"],
        f"📤 <b>Ветки отправлены клиенту</b>\nДата: {target_date.strftime('%d.%m.%Y')}\nКоличество: {len(rows)}",
    )
    await db.log_event(client["id"], "posts_sent", {"date": day, "count": len(rows)})
    return True, f"Клиенту отправлено веток: {len(rows)}"


async def ask_publication_confirmation(bot: Bot, db, settings, client):
    if not client["telegram_id"]:
        return
    target_date = today_for(settings)
    if not await db.posts_sent(client["id"], target_date.isoformat()):
        return
    await bot.send_message(
        client["telegram_id"],
        "Удалось опубликовать сегодняшние ветки?",
        reply_markup=publication_kb(target_date.isoformat()),
    )
