from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def current_content_week(settings) -> date:
    today = datetime.now(settings.tz).date()
    return today - timedelta(days=today.weekday())


def parse_week_start(value: str) -> date:
    return date.fromisoformat(value)


def analytics_week_for(content_week_start: date) -> tuple[date, date]:
    start = content_week_start - timedelta(days=7)
    return start, start + timedelta(days=6)


def content_week_for(content_week_start: date) -> tuple[date, date]:
    return content_week_start, content_week_start + timedelta(days=6)


def period_text(start: date, end: date) -> str:
    return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"


def content_status_kb(client_id: int, week_start: date) -> InlineKeyboardMarkup:
    suffix = f"{client_id}:{week_start.isoformat()}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Да, контент написан",
            callback_data=f"monday_content_yes:{suffix}",
        )],
        [InlineKeyboardButton(
            text="⏳ Нет, ещё пишу",
            callback_data=f"monday_content_no:{suffix}",
        )],
    ])


async def send_content_question(bot, db, settings, client_id: int, week_start: date) -> None:
    client = await db.get_client(client_id)
    if not client or not client["is_active"]:
        await send_next_monday_task(bot, db, settings, week_start)
        return

    clients = await db.list_clients(True)
    position = next((i for i, row in enumerate(clients, start=1) if row["id"] == client_id), 1)
    content_start, content_end = content_week_for(week_start)
    await bot.send_message(
        settings.admin_id,
        f"📝 <b>{position}/{len(clients)}. {client['name']}</b>\n\n"
        f"Статистика внесена. Теперь напиши контент на неделю "
        f"{period_text(content_start, content_end)}.\n\n"
        "Контент на неделю написан?",
        reply_markup=content_status_kb(client_id, week_start),
    )


async def send_next_monday_task(bot, db, settings, week_start: date | None = None) -> None:
    week_start = week_start or current_content_week(settings)
    clients = await db.list_clients(True)
    if not clients:
        await bot.send_message(settings.admin_id, "Активных клиентов пока нет.")
        return

    analytics_start, analytics_end = analytics_week_for(week_start)
    for position, client in enumerate(clients, start=1):
        stats_done = await db.has_weekly_stats(client["id"], analytics_start.isoformat())
        content_done = await db.weekly_content_done(client["id"], week_start.isoformat())
        if stats_done and content_done:
            continue

        if not stats_done:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📈 Внести статистику",
                    callback_data=f"monday_stats:{client['id']}:{week_start.isoformat()}",
                )]
            ])
            await bot.send_message(
                settings.admin_id,
                f"📊 <b>{position}/{len(clients)}. {client['name']}</b>\n\n"
                f"Сначала внеси статистику за прошедшую неделю "
                f"{period_text(analytics_start, analytics_end)}.\n"
                "После неё бот переведёт тебя к контенту этого клиента.",
                reply_markup=kb,
            )
            return

        await send_content_question(bot, db, settings, client["id"], week_start)
        return

    content_start, content_end = content_week_for(week_start)
    await bot.send_message(
        settings.admin_id,
        "✅ <b>План понедельника закрыт</b>\n\n"
        f"По всем {len(clients)} клиентам внесена статистика и готов контент "
        f"на неделю {period_text(content_start, content_end)}.",
    )
