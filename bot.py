import asyncio, logging, os, secrets
from datetime import date
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

import db, sheets

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
DAILY_SEND_HOUR = int(os.getenv("DAILY_SEND_HOUR", "9"))
WORK_GROUP_ID = int(os.getenv("WORK_GROUP_ID", "0"))
TZ = ZoneInfo(TIMEZONE)

logging.basicConfig(level=logging.INFO)
router = Router()

def admin_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👥 Клиенты"), KeyboardButton(text="➕ Добавить клиента")]
    ], resize_keyboard=True)

def client_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📅 Ветки на сегодня")],
        [KeyboardButton(text="📄 Контент-план")],
        [KeyboardButton(text="💬 Связь с менеджером")]
    ], resize_keyboard=True)

def card_kb(client_id, has_sheet, has_plan):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("✅ Таблица подключена" if has_sheet else "📊 Подключить таблицу"), callback_data=f"sheet:{client_id}")],
        [InlineKeyboardButton(text=("✅ Контент-план добавлен" if has_plan else "📄 Добавить контент-план"), callback_data=f"plan:{client_id}")],
        [InlineKeyboardButton(text="🔗 Новая ссылка подключения", callback_data=f"invite:{client_id}")],
        [InlineKeyboardButton(text="🔴 Закрыть проект", callback_data=f"close:{client_id}")]
    ])

async def ensure_client_topic(bot: Bot, client_id: int):
    """Return client's forum topic, creating it once when necessary."""
    client = await db.get_client(client_id)
    if not client or not WORK_GROUP_ID:
        return None

    if client["topic_id"]:
        return client["topic_id"]

    topic = await bot.create_forum_topic(
        chat_id=WORK_GROUP_ID,
        name=client["name"][:128],
    )
    await db.set_client_topic(client_id, topic.message_thread_id)

    await bot.send_message(
        WORK_GROUP_ID,
        f"<b>Клиент: {client['name']}</b>\n"
        f"Threads: @{client['threads_username']}\n"
        f"Telegram: {client['telegram_link'] or '—'}",
        message_thread_id=topic.message_thread_id,
    )
    return topic.message_thread_id


async def send_client_message_to_topic(message: Message, bot: Bot, client):
    """Copy any Telegram message type into this client's personal topic."""
    topic_id = await ensure_client_topic(bot, client["id"])
    if not topic_id:
        raise RuntimeError("WORK_GROUP_ID не настроен или тему создать не удалось")

    await bot.copy_message(
        chat_id=WORK_GROUP_ID,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        message_thread_id=topic_id,
    )


class AddClientFlow(StatesGroup):
    name = State(); threads = State(); telegram_link = State()

class SheetFlow(StatesGroup):
    waiting_url = State()

class PlanFlow(StatesGroup):
    waiting_url = State()

class ManagerFlow(StatesGroup):
    waiting_message = State()

class AdminReplyFlow(StatesGroup):
    waiting_message = State()

async def send_posts(bot, client, auto=False):
    if not client["sheet_url"]:
        return False, "Таблица с ветками пока не подключена."
    posts = sheets.read_posts_for_date(client["sheet_url"], date.today())
    if not posts:
        return False, "На сегодня готовых веток нет."
    count = 0
    for p in posts:
        if auto and await db.was_auto_sent(client["id"], date.today().isoformat(), p["time"], p["fingerprint"]):
            continue
        prefix = f"<b>{p['time']}</b>\n\n" if p["time"] else ""
        await bot.send_message(client["telegram_id"], prefix + p["text"])
        count += 1
        if auto:
            await db.mark_auto_sent(client["id"], date.today().isoformat(), p["time"], p["fingerprint"])
    return True, f"Отправлено: {count}"

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == ADMIN_ID:
        await message.answer("Threads Client Center 2.0 готов ✅", reply_markup=admin_menu())
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("invite_"):
        code = parts[1].replace("invite_", "", 1)
        if await db.bind_client(code, message.from_user.id):
            client = await db.get_client_by_tg(message.from_user.id)
            if client and WORK_GROUP_ID:
                try:
                    await ensure_client_topic(message.bot, client["id"])
                except Exception:
                    logging.exception("Не удалось создать ветку клиента при подключении")
            await message.answer(
                "<b>Добро пожаловать в личный кабинет 👋</b>\n\n"
                "Каждый день в 09:00 сюда будут приходить готовые ветки.\n\n"
                "<b>Как общаться с менеджером:</b>\n"
                "Просто пишите сообщения прямо в этот чат в любое время — текст, фото, файл или голосовое. "
                "Все сообщения автоматически попадут менеджеру. Нажимать кнопку «Связь с менеджером» перед каждым сообщением не нужно.",
                reply_markup=client_menu()
            )
            return
    client = await db.get_client_by_tg(message.from_user.id)
    if client:
        await message.answer("Личный кабинет открыт ✅", reply_markup=client_menu())
    else:
        await message.answer("Ссылка подключения недействительна или проект закрыт.")

@router.message(F.text == "➕ Добавить клиента")
async def add_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.set_state(AddClientFlow.name)
    await message.answer("Введите имя клиента:")

@router.message(AddClientFlow.name)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AddClientFlow.threads)
    await message.answer("Введите username Threads без @:")

@router.message(AddClientFlow.threads)
async def add_threads(message: Message, state: FSMContext):
    username = (message.text or "").strip().lstrip("@")
    await state.update_data(threads=username)
    await state.set_state(AddClientFlow.telegram_link)
    await message.answer("Введите ссылку/username Telegram или «-»:")

@router.message(AddClientFlow.telegram_link)
async def add_finish(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tg = (message.text or "").strip()
    if tg == "-": tg = None
    code = secrets.token_urlsafe(10)
    cid = await db.upsert_client(data["name"], data["threads"], tg, code)
    me = await bot.get_me()
    await state.clear()
    await message.answer(f"Клиент сохранён ✅\n\nСсылка:\nhttps://t.me/{me.username}?start=invite_{code}", reply_markup=admin_menu())

@router.message(F.text == "👥 Клиенты")
async def clients(message: Message):
    if message.from_user.id != ADMIN_ID: return
    rows = await db.list_clients()
    if not rows:
        await message.answer("Активных клиентов пока нет.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c["name"], callback_data=f"client:{c['id']}")] for c in rows
    ])
    await message.answer("Клиенты:", reply_markup=kb)

@router.callback_query(F.data.startswith("client:"))
async def client_card(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return

    try:
        cid = int(callback.data.split(":", 1)[1])
        c = await db.get_client(cid)

        if not c:
            await callback.answer("Клиент не найден", show_alert=True)
            return

        # Безопасно читаем поля: это защищает карточку при миграции старой Railway БД.
        keys = set(c.keys())
        def value(key, default=None):
            return c[key] if key in keys else default

        status = "подключён" if value("telegram_id") else "не подключён"

        await callback.message.answer(
            (
                f"<b>{value('name', 'Клиент')}</b>\n"
                f"Threads: @{value('threads_username') or '—'}\n"
                f"Telegram: {value('telegram_link') or '—'}\n"
                f"Статус: {status}"
            ),
            reply_markup=card_kb(
                cid,
                bool(value("sheet_url")),
                bool(value("content_plan_url")),
            ),
        )
        await callback.answer()

    except Exception as exc:
        logging.exception("Ошибка открытия карточки клиента: %s", exc)
        await callback.answer(
            "Ошибка открытия карточки. Проверьте Deploy Logs.",
            show_alert=True,
        )

@router.callback_query(F.data.startswith("sheet:"))
async def sheet_start(callback: CallbackQuery, state: FSMContext):
    cid = int(callback.data.split(":")[1])
    await state.update_data(client_id=cid)
    await state.set_state(SheetFlow.waiting_url)
    await callback.message.answer("Пришлите ссылку на Google Таблицу:")
    await callback.answer()

@router.message(SheetFlow.waiting_url)
async def sheet_save(message: Message, state: FSMContext):
    url = (message.text or "").strip()
    if not sheets.extract_sheet_id(url):
        await message.answer("Некорректная ссылка Google Таблицы.")
        return
    data = await state.get_data()
    await db.set_sheet_url(data["client_id"], url)
    await state.clear()
    await message.answer("Google Таблица подключена ✅", reply_markup=admin_menu())

@router.callback_query(F.data.startswith("plan:"))
async def plan_start(callback: CallbackQuery, state: FSMContext):
    cid = int(callback.data.split(":")[1])
    await state.update_data(client_id=cid)
    await state.set_state(PlanFlow.waiting_url)
    await callback.message.answer("Пришлите ссылку на контент-план:")
    await callback.answer()

@router.message(PlanFlow.waiting_url)
async def plan_save(message: Message, state: FSMContext):
    data = await state.get_data()
    await db.set_content_plan_url(data["client_id"], (message.text or "").strip())
    await state.clear()
    await message.answer("Контент-план добавлен ✅", reply_markup=admin_menu())

@router.callback_query(F.data.startswith("invite:"))
async def invite(callback: CallbackQuery, bot: Bot):
    cid = int(callback.data.split(":")[1])
    code = secrets.token_urlsafe(10)
    await db.set_invite_code(cid, code)
    me = await bot.get_me()
    await callback.message.answer(f"https://t.me/{me.username}?start=invite_{code}")
    await callback.answer()

@router.callback_query(F.data.startswith("close:"))
async def close(callback: CallbackQuery):
    cid = int(callback.data.split(":")[1])
    await db.close_client(cid)
    await callback.message.answer("Проект закрыт ✅")
    await callback.answer()

@router.message(F.text == "📅 Ветки на сегодня")
async def today_posts(message: Message, bot: Bot):
    client = await db.get_client_by_tg(message.from_user.id)
    if not client:
        await message.answer("Личный кабинет не найден.")
        return
    try:
        ok, text = await send_posts(bot, client, auto=False)
        if not ok: await message.answer(text)
    except Exception:
        logging.exception("Manual sheet read failed")
        await message.answer("Не удалось загрузить ветки.")

@router.message(F.text == "📄 Контент-план")
async def plan(message: Message):
    c = await db.get_client_by_tg(message.from_user.id)
    if not c: return
    if not c["content_plan_url"]:
        await message.answer("Контент-план пока не добавлен.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть контент-план", url=c["content_plan_url"])
    ]])
    await message.answer("Ваш контент-план:", reply_markup=kb)

@router.message(F.text == "💬 Связь с менеджером")
async def manager_info(message: Message):
    client = await db.get_client_by_tg(message.from_user.id)
    if not client:
        await message.answer("Личный кабинет не найден.")
        return
    await message.answer(
        "<b>Связь с менеджером 💬</b>\n\n"
        "Просто напишите сообщение прямо сюда.\n"
        "Можно отправлять текст, фото, документы и голосовые.\n\n"
        "Все ваши обычные сообщения автоматически передаются менеджеру — "
        "эту кнопку каждый раз нажимать не нужно."
    )


@router.message(
    F.chat.type == "private",
    ~F.text.in_({
        "/start",
        "📅 Ветки на сегодня",
        "📄 Контент-план",
        "💬 Связь с менеджером",
    }),
)
async def client_message_bridge(message: Message, state: FSMContext, bot: Bot):
    # Не перехватываем шаги админских сценариев добавления клиента/таблицы/плана.
    if message.from_user.id == ADMIN_ID:
        return
    if await state.get_state():
        return

    client = await db.get_client_by_tg(message.from_user.id)
    if not client:
        return

    try:
        await send_client_message_to_topic(message, bot, client)
        await message.answer("Сообщение передано менеджеру ✅")
    except Exception:
        logging.exception("Не удалось передать сообщение клиента в его ветку")
        await message.answer("Не удалось передать сообщение менеджеру. Попробуйте ещё раз чуть позже.")


@router.message(F.chat.id == WORK_GROUP_ID)
async def topic_reply_bridge(message: Message, bot: Bot):
    """Anything the team writes inside a client's topic is copied to that client."""
    if not message.message_thread_id:
        return
    if message.from_user and message.from_user.is_bot:
        return
    if message.text and message.text.startswith("/"):
        return

    client = await db.get_client_by_topic(message.message_thread_id)
    if not client or not client["telegram_id"]:
        return

    try:
        await bot.copy_message(
            chat_id=client["telegram_id"],
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        await message.reply("Отправлено клиенту ✅")
    except Exception:
        logging.exception("Не удалось отправить сообщение из ветки клиенту")
        await message.reply("Не удалось отправить клиенту.")


async def daily_send_job(bot: Bot):
    for c in await db.list_clients():
        if not c["telegram_id"] or not c["sheet_url"]:
            continue
        try:
            await send_posts(bot, c, auto=True)
        except Exception:
            logging.exception("Auto send failed for client %s", c["id"])

async def main():
    await db.init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(daily_send_job, "cron", hour=DAILY_SEND_HOUR, minute=0, args=[bot], id="daily_send", replace_existing=True)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
