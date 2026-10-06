from __future__ import annotations

import asyncio
import re
from datetime import datetime
from collections import defaultdict
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from aiogram import Bot
from aiogram.types import BufferedInputFile

from keyboards import publication_kb
from topics import topic_log


POSTS_ALREADY_SENT = "Ветки на сегодня уже отправлены"


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
    text = (f"{row['slot']}\n\n" if row["slot"] else "") + row["body"]
    image_url = (row["image_url"] or "").strip() if "image_url" in row.keys() else ""
    if image_url:
        try:
            data, filename = await asyncio.to_thread(_download_image_sync, image_url)
            photo = BufferedInputFile(data, filename=filename)
            if len(text.encode('utf-16-le')) // 2 <= 1024:
                await bot.send_photo(chat_id, photo=photo, caption=text, parse_mode=None, message_thread_id=thread_id)
                return
            await bot.send_photo(chat_id, photo=photo, message_thread_id=thread_id)
        except Exception:
            import logging
            logging.exception("Photo delivery failed for row %s; sending text", row['source_row'])
    # Conservative chunks also accommodate non-BMP characters in Telegram limits.
    for offset in range(0, len(text), 2000):
        await bot.send_message(chat_id, text[offset:offset+2000], parse_mode=None, message_thread_id=thread_id)


_send_locks = defaultdict(asyncio.Lock)


async def send_today_posts(bot: Bot, db, sheets, settings, client, *, force: bool = False) -> tuple[bool, str]:
    if not client:
        return False, "Клиент не найден"
    async with _send_locks[client['id']]:
        client = await db.get_client(client['id'])
        return await _send_today_posts(bot, db, sheets, settings, client, force=force)


async def _send_today_posts(bot: Bot, db, sheets, settings, client, *, force: bool = False) -> tuple[bool, str]:
    if not client:
        return False, "Клиент не найден"
    if not client["sheet_url"]:
        return False, "Таблица клиента не подключена"

    publish_mode = client["publish_mode"] if "publish_mode" in client.keys() else "client"
    if publish_mode != "team" and not client["telegram_id"]:
        return False, "Клиент ещё не подключил личный кабинет"

    target_date = today_for(settings)
    day = target_date.isoformat()
    posts = await sheets.read_posts(client["sheet_url"], target_date)
    if not posts:
        return False, "На сегодня нет строк со статусом «Готово»"

    rows = await db.save_posts(client["id"], day, posts)

    if publish_mode == "team":
        if not client['topic_id'] or not settings.work_group_id:
            return False, "Для клиента ещё не создана тема в рабочем чате"
        targets = [(settings.work_group_id, client['topic_id'])]
    else:
        members = await db.list_members(client['id'])
        targets = [(m['telegram_id'], None) for m in members
                   if client['posts_audience'] == 'all' or m['telegram_id'] == client['responsible_id']]
        if not targets:
            return False, "Нет получателей. Подключите участников и назначьте ответственного."

    # Send only the rows currently marked ready, not obsolete cached rows.
    sources = {int(post['source_row']) for post in posts}
    rows = [row for row in rows if row['source_row'] in sources]
    delivered, failures = 0, []
    for uid, thread_id in targets:
        pending = [row for row in rows if force or not await db.post_delivered(client['id'], day, uid, row['source_row'])]
        if not pending:
            continue
        try:
            await bot.send_message(uid, f"📅 Ветки на {target_date.strftime('%d.%m.%Y')}\nК отправке: {len(pending)}", message_thread_id=thread_id)
            for row in pending:
                await _send_post(bot, uid, row, thread_id=thread_id)
                await db.mark_post_delivered(client['id'], day, uid, row['source_row'])
                delivered += 1
        except Exception as exc:
            failures.append(f"{uid}: {exc}")
    if failures:
        await db.log_event(client['id'], 'error', {'stage': 'posts_delivery', 'errors': failures})
        return False, f"Доставлено веток: {delivered}. Ошибки получателей: " + "; ".join(failures)
    if not delivered:
        return False, POSTS_ALREADY_SENT
    await db.log_event(client['id'], 'posts_sent', {'date': day, 'count': delivered, 'recipients': len(targets)})
    return True, f"Доставлено веток: {delivered}. Получателей в выбранном режиме: {len(targets)}"


async def ask_publication_confirmation(bot: Bot, db, settings, client):
    publish_mode = client["publish_mode"] if "publish_mode" in client.keys() else "client"
    if publish_mode == "team":
        return
    if not client["responsible_id"]:
        return
    target_date = today_for(settings)
    if not await db.posts_sent(client["id"], target_date.isoformat()):
        return
    await bot.send_message(
        client["responsible_id"],
        "Удалось опубликовать сегодняшние ветки?",
        reply_markup=publication_kb(target_date.isoformat()),
    )
