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
    if not client["sheet_url"]:
        return False, "Таблица клиента не подключена"

    publish_mode = client["publish_mode"] if "publish_mode" in client.keys() else "client"
    if publish_mode != "team" and not client["telegram_id"]:
        return False, "Клиент ещё не подключил личный кабинет"

    target_date = today_for(settings)
    day = target_date.isoformat()
    if not force and await db.posts_sent(client["id"], day):
        return False, "Ветки на сегодня уже отправлены"

    posts = await sheets.read_posts(client["sheet_url"], target_date)
    if not posts:
        return False, "На сегодня нет строк со статусом «Готово»"

    rows = await db.save_posts(client["id"], day, posts)

    if publish_mode == "team":
        fresh_client = await db.get_client(client["id"])
        topic_id = fresh_client["topic_id"] if fresh_client else client["topic_id"]
        if not topic_id:
            return False, "Для клиента ещё не создана тема в рабочем чате"
        await bot.send_message(
            settings.work_group_id,
            f"<b>📅 Ветки на {target_date.strftime('%d.%m.%Y')}</b>\n\nГотово к публикации: {len(rows)}",
            message_thread_id=topic_id,
        )
        for row in rows:
            prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
            await bot.send_message(settings.work_group_id, prefix + row["body"], message_thread_id=topic_id)
        await db.log_event(client["id"], "posts_sent", {"date": day, "count": len(rows), "destination": "admin_topic"})
        return True, f"В тему клиента отправлено веток: {len(rows)}"

    await bot.send_message(
        client["telegram_id"],
        f"<b>📅 Ветки на {target_date.strftime('%d.%m.%Y')}</b>\n\nГотово к публикации: {len(rows)}",
    )
    for row in rows:
        prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
        await bot.send_message(client["telegram_id"], prefix + row["body"])
    await topic_log(
        bot, db, settings.work_group_id, client["id"],
        f"📤 <b>Ветки отправлены клиенту</b>\nДата: {target_date.strftime('%d.%m.%Y')}\nКоличество: {len(rows)}",
    )
    await db.log_event(client["id"], "posts_sent", {"date": day, "count": len(rows), "destination": "client"})
    return True, f"Клиенту отправлено веток: {len(rows)}"


async def ask_publication_confirmation(bot: Bot, db, settings, client):
    publish_mode = client["publish_mode"] if "publish_mode" in client.keys() else "client"
    if publish_mode == "team":
        return
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
