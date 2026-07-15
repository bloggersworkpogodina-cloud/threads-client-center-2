from __future__ import annotations

from aiogram import Bot


async def ensure_topic(bot: Bot, db, work_group_id: int | None, client_id: int) -> int | None:
    if not work_group_id:
        return None
    client = await db.get_client(client_id)
    if client["topic_id"]:
        return int(client["topic_id"])
    topic = await bot.create_forum_topic(work_group_id, name=f"{client['name']} | @{client['threads_username_normalized']}")
    await db.set_topic(client_id, topic.message_thread_id)
    await db.log_event(client_id, "topic_created", {"topic_id": topic.message_thread_id})
    return topic.message_thread_id


async def topic_log(bot: Bot, db, work_group_id: int | None, client_id: int, text: str):
    topic_id = await ensure_topic(bot, db, work_group_id, client_id)
    if work_group_id and topic_id:
        await bot.send_message(work_group_id, text, message_thread_id=topic_id)
