from __future__ import annotations

import asyncio
import logging
import os
from html import escape
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from db import ClientAlreadyExistsError, Database
from google_sheets import GoogleSheetsError, read_today_posts

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WORK_GROUP_ID = int(os.getenv("WORK_GROUP_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

router = Router()
db = Database(DB_PATH)


class AddClient(StatesGroup):
    name = State()
    threads = State()
    telegram_link = State()


class SetClientValue(StatesGroup):
    sheet_url = State()
    content_plan_url = State()


class ManagerMessage(StatesGroup):
    text = State()


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id == ADMIN_ID)


def valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Клиенты"), KeyboardButton(text="➕ Добавить клиента")],
        ],
        resize_keyboard=True,
    )


def client_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Ветки на сегодня")],
            [KeyboardButton(text="📄 Контент-план")],
            [KeyboardButton(text="💬 Связь с менеджером")],
        ],
        resize_keyboard=True,
    )


def client_card_keyboard(client_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Подключить таблицу", callback_data=f"sheet:{client_id}")],
            [InlineKeyboardButton(text="📄 Подключить контент-план", callback_data=f"plan:{client_id}")],
            [InlineKeyboardButton(text="💬 Создать / проверить тему", callback_data=f"topic:{client_id}")],
            [InlineKeyboardButton(text="🔴 Закрыть проект", callback_data=f"archive_confirm:{client_id}")],
        ]
    )


def client_card_text(client: dict) -> str:
    status = "подключён" if client.get("telegram_id") else "не подключён"
    username = client.get("threads_username_normalized") or "—"
    return (
        f"<b>{escape(client['name'])}</b>\n"
        f"Threads: @{escape(username)}\n"
        f"Telegram: {escape(client.get('telegram_link') or '—')}\n"
        f"Статус: <b>{status}</b>\n"
        f"Таблица: {'✅' if client.get('sheet_url') else '—'}\n"
        f"Контент-план: {'✅' if client.get('content_plan_url') else '—'}\n"
        f"Тема менеджера: {'✅' if client.get('topic_id') else '—'}"
    )


async def ensure_topic(bot: Bot, client: dict) -> int:
    if client.get("topic_id"):
        return int(client["topic_id"])
    if not WORK_GROUP_ID:
        raise RuntimeError("Не задан WORK_GROUP_ID")
    topic = await bot.create_forum_topic(
        chat_id=WORK_GROUP_ID,
        name=f"{client['name']} | @{client.get('threads_username_normalized') or 'threads'}"[:128],
    )
    await db.set_topic_id(client["id"], topic.message_thread_id)
    return topic.message_thread_id


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    if is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Админ-центр Threads Client Center 2.0", reply_markup=admin_menu())
        return

    current = await db.get_client_by_telegram(message.from_user.id)
    if current:
        await message.answer("Личный кабинет подключён ✅", reply_markup=client_menu())
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("invite_"):
        client = await db.bind_invite(parts[1][7:], message.from_user.id)
        if client:
            await message.answer(
                "<b>Добро пожаловать в личный кабинет 👋</b>\n\n"
                "Здесь находятся ваши ветки, контент-план и связь с менеджером.\n\n"
                "<b>Личный кабинет подключён ✅</b>",
                reply_markup=client_menu(),
            )
            return
    await message.answer("Ссылка подключения недействительна. Обратитесь к менеджеру.")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    markup = admin_menu() if is_admin(message.from_user.id) else client_menu()
    await message.answer("Действие отменено.", reply_markup=markup)


@router.message(F.text == "👥 Клиенты")
async def clients_list(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    clients = await db.list_active_clients()
    if not clients:
        await message.answer("Активных клиентов пока нет.")
        return
    keyboard = [
        [InlineKeyboardButton(text=c["name"], callback_data=f"client:{c['id']}")]
        for c in clients
    ]
    await message.answer("Активные клиенты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))


@router.message(F.text == "➕ Добавить клиента")
async def add_client_start(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddClient.name)
    await message.answer("Введите имя клиента. Для отмены: /cancel")


@router.message(AddClient.name)
async def add_client_name(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AddClient.threads)
    await message.answer("Введите Threads username без @:")


@router.message(AddClient.threads)
async def add_client_threads(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(threads=(message.text or "").strip())
    await state.set_state(AddClient.telegram_link)
    await message.answer("Введите Telegram username клиента без @ или отправьте «-»:")


@router.message(AddClient.telegram_link)
async def add_client_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    value = (message.text or "").strip()
    if value == "-":
        link = None
    else:
        username = value.lstrip("@").strip()
        if username.startswith("https://t.me/") or username.startswith("http://t.me/"):
            username = username.rstrip("/").rsplit("/", 1)[-1].lstrip("@")
        if not username or any(char.isspace() for char in username):
            await message.answer("Введите Telegram username без @ или отправьте «-».")
            return
        link = f"https://t.me/{username}"
    try:
        client = await db.add_client(data["name"], data["threads"], link)
    except ClientAlreadyExistsError as exc:
        await state.clear()
        existing = await db.get_client(exc.client_id)
        await message.answer(
            "Клиент с таким Threads username уже существует. Новая запись не создана."
        )
        if existing:
            await message.answer(client_card_text(existing), reply_markup=client_card_keyboard(existing["id"]))
        return
    await state.clear()
    me = await bot.get_me()
    invite = f"https://t.me/{me.username}?start=invite_{client['invite_code']}"
    await message.answer(
        f"Клиент создан ✅\n\nСсылка подключения:\n<code>{escape(invite)}</code>",
        reply_markup=admin_menu(),
    )
    await message.answer(client_card_text(client), reply_markup=client_card_keyboard(client["id"]))


@router.callback_query(F.data.startswith("client:"))
async def show_client(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    client = await db.get_client(int(callback.data.split(":", 1)[1]))
    if not client or not client["is_active"]:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    await callback.message.answer(client_card_text(client), reply_markup=client_card_keyboard(client["id"]))
    await callback.answer()


@router.callback_query(F.data.startswith("sheet:"))
async def sheet_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    client_id = int(callback.data.split(":", 1)[1])
    await state.set_state(SetClientValue.sheet_url)
    await state.update_data(client_id=client_id)
    await callback.message.answer("Пришлите полную ссылку Google Sheets. Для отмены: /cancel")
    await callback.answer()


@router.message(SetClientValue.sheet_url)
async def sheet_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    url = (message.text or "").strip()
    if not valid_url(url) or "docs.google.com/spreadsheets" not in url:
        await message.answer("Это не похоже на ссылку Google Sheets. Пришлите полную ссылку.")
        return
    data = await state.get_data()
    await db.set_sheet_url(data["client_id"], url)
    await state.clear()
    await message.answer("Google-таблица подключена ✅", reply_markup=admin_menu())


@router.callback_query(F.data.startswith("plan:"))
async def plan_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(SetClientValue.content_plan_url)
    await state.update_data(client_id=int(callback.data.split(":", 1)[1]))
    await callback.message.answer("Пришлите ссылку на контент-план. Для отмены: /cancel")
    await callback.answer()


@router.message(SetClientValue.content_plan_url)
async def plan_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    url = (message.text or "").strip()
    if not valid_url(url):
        await message.answer("Нужна полная ссылка http/https.")
        return
    data = await state.get_data()
    await db.set_content_plan_url(data["client_id"], url)
    await state.clear()
    await message.answer("Контент-план подключён ✅", reply_markup=admin_menu())


@router.callback_query(F.data.startswith("topic:"))
async def topic_create(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        return
    client = await db.get_client(int(callback.data.split(":", 1)[1]))
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    try:
        topic_id = await ensure_topic(bot, client)
        await callback.message.answer(f"Тема клиента готова ✅ ID: <code>{topic_id}</code>")
    except Exception:
        logger.exception("Не удалось создать тему клиента")
        await callback.message.answer("Не удалось создать тему. Проверьте WORK_GROUP_ID и права бота.")
    await callback.answer()


@router.callback_query(F.data.startswith("archive_confirm:"))
async def archive_confirm(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    client_id = int(callback.data.split(":", 1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, закрыть", callback_data=f"archive:{client_id}")],
        [InlineKeyboardButton(text="Отмена", callback_data=f"client:{client_id}")],
    ])
    await callback.message.answer("Закрыть проект? История останется в базе.", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("archive:"))
async def archive(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        return
    await db.archive_client(int(callback.data.split(":", 1)[1]))
    await callback.message.answer("Проект закрыт и перемещён в архив ✅")
    await callback.answer()


@router.message(F.text == "📅 Ветки на сегодня")
async def today_posts(message: Message) -> None:
    client = await db.get_client_by_telegram(message.from_user.id)
    if not client:
        await message.answer("Личный кабинет не найден.")
        return
    if not client.get("sheet_url"):
        await message.answer("Таблица с ветками пока не подключена.")
        return
    try:
        posts = await asyncio.to_thread(read_today_posts, client["sheet_url"])
    except GoogleSheetsError as exc:
        logger.exception("Google Sheets error for client %s", client["id"])
        await message.answer(f"Не удалось загрузить ветки. {escape(str(exc))}")
        return
    if not posts:
        await message.answer("На сегодня готовых веток в таблице нет.")
        return
    await message.answer("<b>📅 Ветки на сегодня</b>")
    for post in posts:
        prefix = f"<b>{escape(post.time)}</b>\n\n" if post.time else ""
        await message.answer(prefix + escape(post.text))


@router.message(F.text == "📄 Контент-план")
async def content_plan(message: Message) -> None:
    client = await db.get_client_by_telegram(message.from_user.id)
    if not client:
        await message.answer("Личный кабинет не найден.")
        return
    if not client.get("content_plan_url"):
        await message.answer("Контент-план пока не добавлен.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть контент-план", url=client["content_plan_url"])
    ]])
    await message.answer("Ваш контент-план:", reply_markup=kb)


@router.message(F.text == "💬 Связь с менеджером")
async def manager_start(message: Message, state: FSMContext) -> None:
    if not await db.get_client_by_telegram(message.from_user.id):
        await message.answer("Личный кабинет не найден.")
        return
    await state.set_state(ManagerMessage.text)
    await message.answer("Напишите сообщение менеджеру. Для отмены: /cancel")


@router.message(ManagerMessage.text)
async def manager_send(message: Message, state: FSMContext, bot: Bot) -> None:
    client = await db.get_client_by_telegram(message.from_user.id)
    if not client:
        await state.clear()
        await message.answer("Личный кабинет не найден.")
        return
    try:
        topic_id = await ensure_topic(bot, client)
        await bot.send_message(
            WORK_GROUP_ID,
            f"<b>Сообщение от {escape(client['name'])}</b>",
            message_thread_id=topic_id,
        )
        await bot.copy_message(
            chat_id=WORK_GROUP_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=topic_id,
        )
        await message.answer("Сообщение отправлено менеджеру ✅", reply_markup=client_menu())
    except Exception:
        logger.exception("Не удалось отправить сообщение менеджеру")
        await message.answer("Не удалось отправить сообщение. Проверьте настройки рабочей группы.")
    finally:
        await state.clear()


@router.message(F.chat.id == WORK_GROUP_ID, F.message_thread_id, ~F.from_user.is_bot)
async def manager_reply_to_client(message: Message, bot: Bot) -> None:
    # Служебные команды и сообщения создания темы клиенту не пересылаем.
    if message.text and message.text.startswith("/"):
        return
    client = await db.get_client_by_topic(message.message_thread_id)
    if not client or not client.get("telegram_id"):
        return
    try:
        await bot.copy_message(
            chat_id=client["telegram_id"],
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception:
        logger.exception("Не удалось переслать ответ клиенту %s", client["id"])


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN")
    if not ADMIN_ID:
        raise RuntimeError("Не задан ADMIN_ID")
    await db.initialize()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
