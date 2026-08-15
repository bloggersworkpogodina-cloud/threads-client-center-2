from __future__ import annotations

import asyncio
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from aiogram import Bot
from aiogram.types import BufferedInputFile

from keyboards import publication_kb
from topics import topic_log


def today_for(settings):
    return datetime.now(settings.tz).date()


def _google_drive_download_url(url: str) -> str:
    url = (url or "").strip()
    if not url or "drive.google.com" not in url:
        return url
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
    if not match:
        match = re.search(r"/d/([A-Za-z0-9_-]+)", url)
    file_id = match.group(1) if match else None
    if not file_id:
        file_id = parse_qs(urlparse(url).query).get("id", [None])[0]
    return f"https://drive.google.com/uc?export=download&id={file_id}" if file_id else url


def _download_image_sync(url: str) -> tuple[bytes, str]:
    download_url = _google_drive_download_url(url)
    req = Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=30) as response:
        data = response.read(20 * 1024 * 1024 + 1)
        content_type = (response.headers.get("Content-Type") or "").lower()
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("Изображение больше 20 МБ")
    if not data:
        raise ValueError("Пустой файл изображения")
    if "text/html" in content_type:
        raise ValueError("По ссылке Google Drive вернулась веб-страница, а не изображение. Проверьте доступ «Все, у кого есть ссылка». ")
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    return data, "image" + ext


async def _send_post(bot: Bot, chat_id: int, row, *, thread_id: int | None = None) -> None:
    prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
    text = prefix + row["body"]
    image_url = (row["image_url"] or "").strip() if "image_url" in row.keys() else ""

    if not image_url:
        await bot.send_message(chat_id, text, message_thread_id=thread_id)
        return

    data, filename = await asyncio.to_thread(_download_image_sync, image_url)
    photo = BufferedInputFile(data, filename=filename)
    if len(text) <= 1024:
        await bot.send_photo(chat_id, photo=photo, caption=text, message_thread_id=thread_id)
    else:
        await bot.send_photo(chat_id, photo=photo, message_thread_id=thread_id)
        await bot.send_message(chat_id, text, message_thread_id=thread_id)


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
            try:
                await _send_post(bot, settings.work_group_id, row, thread_id=topic_id)
            except Exception as exc:
                prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
                await bot.send_message(settings.work_group_id, prefix + row["body"], message_thread_id=topic_id)
                await bot.send_message(settings.work_group_id, f"⚠️ Фото не загрузилось: {exc}", message_thread_id=topic_id)
        await db.log_event(client["id"], "posts_sent", {"date": day, "count": len(rows), "destination": "admin_topic"})
        return True, f"В тему клиента отправлено веток: {len(rows)}"

    await bot.send_message(
        client["telegram_id"],
        f"<b>📅 Ветки на {target_date.strftime('%d.%m.%Y')}</b>\n\nГотово к публикации: {len(rows)}",
    )
    media_errors = 0
    for row in rows:
        try:
            await _send_post(bot, client["telegram_id"], row)
        except Exception as exc:
            media_errors += 1
            prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
            await bot.send_message(client["telegram_id"], prefix + row["body"])
            await topic_log(
                bot, db, settings.work_group_id, client["id"],
                f"⚠️ <b>Фото к ветке не отправлено</b>\nСтрока таблицы: {row['source_row']}\nОшибка: {exc}",
            )
    await topic_log(
        bot, db, settings.work_group_id, client["id"],
        f"📤 <b>Ветки отправлены клиенту</b>\nДата: {target_date.strftime('%d.%m.%Y')}\nКоличество: {len(rows)}"
        + (f"\nОшибок фото: {media_errors}" if media_errors else ""),
    )
    await db.log_event(client["id"], "posts_sent", {"date": day, "count": len(rows), "destination": "client", "media_errors": media_errors})
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
