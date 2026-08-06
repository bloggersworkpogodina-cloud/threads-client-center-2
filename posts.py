from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile

from keyboards import publication_kb
from topics import topic_log


MAX_IMAGE_BYTES = 20 * 1024 * 1024


def today_for(settings):
    return datetime.now(settings.tz).date()


def _direct_image_url(url: str) -> str:
    """Convert a public Google Drive link to a direct download URL."""
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if "drive.google.com" not in parsed.netloc:
        return value

    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", parsed.path)
    file_id = match.group(1) if match else None
    if not file_id:
        file_id = parse_qs(parsed.query).get("id", [None])[0]
    if not file_id:
        return value
    return f"https://drive.google.com/uc?export=download&id={file_id}"


async def _download_image(url: str) -> BufferedInputFile:
    direct_url = _direct_image_url(url)
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {"User-Agent": "ThreadsClientCenterBot/2.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(direct_url, allow_redirects=True) as response:
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            data = await response.read()

    if not data:
        raise ValueError("Изображение пустое")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Изображение больше 20 МБ")
    # Google Drive may return application/octet-stream for valid images.
    if content_type and not (
        content_type.startswith("image/")
        or content_type.startswith("application/octet-stream")
    ):
        raise ValueError("Ссылка ведёт не на изображение")

    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    return BufferedInputFile(data, filename=f"post_image{ext}")


async def _send_post(bot: Bot, chat_id: int, row, *, message_thread_id: int | None = None) -> bool:
    prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
    text = prefix + row["body"]
    image_url = row["image_url"] if "image_url" in row.keys() else None

    if not image_url:
        await bot.send_message(chat_id, text, message_thread_id=message_thread_id)
        return True

    image = await _download_image(image_url)
    # Telegram captions are limited to 1024 characters. For longer posts,
    # send the image first and the complete text as a separate message.
    if len(text) <= 1024:
        await bot.send_photo(
            chat_id,
            image,
            caption=text,
            message_thread_id=message_thread_id,
        )
    else:
        await bot.send_photo(chat_id, image, message_thread_id=message_thread_id)
        await bot.send_message(chat_id, text, message_thread_id=message_thread_id)
    return True


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
        media_errors = 0
        for row in rows:
            try:
                await _send_post(bot, settings.work_group_id, row, message_thread_id=topic_id)
            except Exception as exc:
                media_errors += 1
                prefix = f"<b>{row['slot']}</b>\n\n" if row["slot"] else ""
                await bot.send_message(settings.work_group_id, prefix + row["body"], message_thread_id=topic_id)
                await bot.send_message(
                    settings.work_group_id,
                    f"⚠️ Изображение из строки {row['source_row'] or '—'} не загрузилось: {exc}",
                    message_thread_id=topic_id,
                )
        await db.log_event(client["id"], "posts_sent", {"date": day, "count": len(rows), "destination": "admin_topic", "media_errors": media_errors})
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
                f"⚠️ Не удалось загрузить изображение из строки {row['source_row'] or '—'} Google Sheets: {exc}",
            )
    await topic_log(
        bot, db, settings.work_group_id, client["id"],
        f"📤 <b>Ветки отправлены клиенту</b>\nДата: {target_date.strftime('%d.%m.%Y')}\nКоличество: {len(rows)}"
        + (f"\nОшибок изображений: {media_errors}" if media_errors else ""),
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
