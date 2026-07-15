from __future__ import annotations

from datetime import date, timedelta
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from states import AddClient, LinkPlan, LinkSheet, WeeklyStatsFlow
from keyboards import admin_menu, client_card_kb, confirm_client_kb
from topics import ensure_topic, topic_log
from posts import send_today_posts

router = Router()
DB = None
SETTINGS = None
SHEETS = None

def configure(db, settings, sheets):
    global DB, SETTINGS, SHEETS
    DB, SETTINGS, SHEETS = db, settings, sheets


def deps(router: Router):
    return DB, SETTINGS

async def is_admin(user_id: int, router: Router) -> bool:
    return user_id == SETTINGS.admin_id


def card_text(c):
    return (f"<b>{c['name']}</b>\n\nThreads: @{c['threads_username_normalized']}\nTelegram: @{c['telegram_username'] or '—'}\n"
            f"Кабинет: {'подключён' if c['telegram_id'] else 'не подключён'}\nТаблица: {'подключена' if c['sheet_url'] else 'не подключена'}\n"
            f"Контент-план: {'добавлен' if c['content_plan_url'] else 'не добавлен'}\nТема: {'создана' if c['topic_id'] else 'не создана'}\nСтатус: {'активен' if c['is_active'] else 'архив'}")

@router.message(F.text == "➕ Добавить клиента")
async def add_start(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id, router): return
    await state.clear(); await state.set_state(AddClient.name)
    await message.answer("Введите имя клиента:")

@router.message(AddClient.name)
async def add_name(message: Message, state: FSMContext):
    await state.update_data(name=(message.text or "").strip()); await state.set_state(AddClient.threads)
    await message.answer("Введите Threads username:")

@router.message(AddClient.threads)
async def add_threads(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if not value or value == "-":
        await message.answer("Threads username обязателен."); return
    await state.update_data(threads=value); await state.set_state(AddClient.telegram)
    await message.answer("Введите Telegram username: username, @username, ссылка t.me или -")

@router.message(AddClient.telegram)
async def add_telegram(message: Message, state: FSMContext):
    data = await state.get_data(); telegram = (message.text or "").strip()
    await state.update_data(telegram=telegram); await state.set_state(AddClient.confirm)
    await message.answer(f"Проверьте данные:\n\nИмя: {data['name']}\nThreads: @{DB.normalize_threads(data['threads'])}\nTelegram: @{DB.normalize_telegram(telegram) or '—'}", reply_markup=confirm_client_kb())

@router.callback_query(F.data == "client_confirm_create")
async def add_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await is_admin(callback.from_user.id, router): return
    data = await state.get_data()
    try:
        c = await DB.create_client(data["name"], data["threads"], data.get("telegram"))
    except ValueError as exc:
        await callback.message.answer(str(exc), reply_markup=admin_menu()); await state.clear(); await callback.answer(); return
    await ensure_topic(bot, DB, SETTINGS.work_group_id, c["id"])
    me = await bot.get_me(); invite = f"https://t.me/{me.username}?start=invite_{c['invite_code']}"
    c = await DB.get_client(c["id"])
    await DB.log_event(c["id"], "client_created")
    await state.clear(); await callback.message.answer(card_text(c) + f"\n\nСсылка подключения:\n{invite}", reply_markup=client_card_kb(c["id"])); await callback.answer()

@router.callback_query(F.data == "client_confirm_edit")
async def add_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddClient.name); await callback.message.answer("Введите имя заново:"); await callback.answer()

@router.callback_query(F.data == "client_confirm_cancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear(); await callback.message.answer("Создание отменено.", reply_markup=admin_menu()); await callback.answer()

@router.message(F.text == "👥 Клиенты")
async def clients(message: Message):
    if not await is_admin(message.from_user.id, router): return
    rows = await DB.list_clients(True)
    if not rows: await message.answer("Активных клиентов пока нет."); return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=c["name"], callback_data=f"client_view:{c['id']}")] for c in rows])
    await message.answer("Активные клиенты:", reply_markup=kb)

@router.callback_query(F.data.startswith("client_view:"))
async def view_client(callback: CallbackQuery):
    c = await DB.get_client(int(callback.data.split(":")[1]))
    if not c:
        await callback.answer("Карточка устарела. Обновите список клиентов.", show_alert=True)
        return
    await callback.message.answer(card_text(c), reply_markup=client_card_kb(c["id"]))
    await callback.answer()

@router.callback_query(F.data.startswith("client_invite:"))
async def invite(callback: CallbackQuery, bot: Bot):
    c = await DB.get_client(int(callback.data.split(":")[1])); me = await bot.get_me(); await callback.message.answer(f"https://t.me/{me.username}?start=invite_{c['invite_code']}"); await callback.answer()

@router.callback_query(F.data.startswith("client_topic:"))
async def topic(callback: CallbackQuery, bot: Bot):
    cid = int(callback.data.split(":")[1]); tid = await ensure_topic(bot, DB, SETTINGS.work_group_id, cid); await callback.answer("Тема создана ✅" if tid else "WORK_GROUP_ID не настроен", show_alert=True)

@router.callback_query(F.data.startswith("client_sheet:"))
async def sheet_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(client_id=int(callback.data.split(":")[1])); await state.set_state(LinkSheet.url); await callback.message.answer("Пришлите ссылку Google Sheets:"); await callback.answer()

@router.message(LinkSheet.url)
async def sheet_save(message: Message, state: FSMContext):
    data = await state.get_data()
    client_id = data.get("client_id")
    client = await DB.get_client(client_id) if client_id else None
    if not client:
        await state.clear()
        await message.answer(
            "Эта карточка клиента устарела после обновления базы.\n"
            "Откройте «👥 Клиенты» и выберите клиента заново.",
            reply_markup=admin_menu(),
        )
        return

    url = (message.text or "").strip()
    try:
        check = await SHEETS.validate(url)
    except Exception as exc:
        await message.answer(f"Не удалось подключить таблицу:\n{exc}\n\nИсправьте доступ или ссылку и пришлите её ещё раз.")
        return

    try:
        await DB.update_client_links(client_id, sheet_url=url)
    except LookupError:
        await state.clear()
        await message.answer(
            "Клиент больше не найден в текущей базе. Создайте его заново или откройте актуальную карточку.",
            reply_markup=admin_menu(),
        )
        return

    await DB.log_event(client_id, "sheet_connected", {"rows": check["rows"]})
    await topic_log(message.bot, DB, SETTINGS.work_group_id, client_id, "📊 Google-таблица подключена и проверена.")
    await state.clear()
    client = await DB.get_client(client_id)
    await message.answer(
        f"Таблица подключена ✅\nСтрок на первом листе: {check['rows']}",
        reply_markup=client_card_kb(client["id"]),
    )

@router.callback_query(F.data.startswith("client_send_posts:"))
async def send_posts_now(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, router):
        return
    client = await DB.get_client(int(callback.data.split(":")[1]))
    try:
        ok, text = await send_today_posts(callback.bot, DB, SHEETS, SETTINGS, client, force=False)
    except Exception as exc:
        await callback.message.answer(f"Не удалось отправить ветки:\n{exc}")
        await callback.answer()
        return
    await callback.message.answer(("✅ " if ok else "ℹ️ ") + text)
    await callback.answer()

@router.callback_query(F.data.startswith("client_plan:"))
async def plan_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(client_id=int(callback.data.split(":")[1])); await state.set_state(LinkPlan.url); await callback.message.answer("Пришлите ссылку на контент-план:"); await callback.answer()

@router.message(LinkPlan.url)
async def plan_save(message: Message, state: FSMContext):
    data = await state.get_data(); await DB.update_client_links(data["client_id"], content_plan_url=(message.text or "").strip()); await state.clear(); await message.answer("Контент-план подключён ✅", reply_markup=admin_menu())

@router.callback_query(F.data.startswith("client_archive:"))
async def archive(callback: CallbackQuery):
    cid = int(callback.data.split(":")[1]); await DB.archive_client(cid); await DB.log_event(cid, "client_archived"); await callback.message.answer("Клиент перемещён в архив.", reply_markup=admin_menu()); await callback.answer()

@router.callback_query(F.data.startswith("client_analytics:"))
async def analytics(callback: CallbackQuery):
    cid = int(callback.data.split(":")[1]); a = await DB.analytics(cid); latest = a["latest"]
    text = f"<b>Аналитика</b>\n\nОтправлено веток: {a['sent']}\nОпубликовано: {a['published']}\nДисциплина: {a['discipline']}%\nОтклики: {a['responses']}\nЗаявки: {a['leads']}"
    if latest: text += f"\n\nПоследняя неделя:\nПросмотры: {latest['views']}\nПодписчики: {latest['new_followers']}\nПереходы в Telegram: {latest['telegram_clicks']}"
    await callback.message.answer(text); await callback.answer()

@router.callback_query(F.data.startswith("weekly_stats:"))
async def weekly_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(client_id=int(callback.data.split(":")[1])); await state.set_state(WeeklyStatsFlow.views); await callback.message.answer("Просмотры за неделю:"); await callback.answer()

async def _num(message: Message, state: FSMContext, key: str, next_state, prompt: str):
    try: value = int((message.text or "").replace(" ", ""))
    except ValueError: await message.answer("Введите целое число:"); return
    await state.update_data(**{key: value}); await state.set_state(next_state); await message.answer(prompt)

@router.message(WeeklyStatsFlow.views)
async def ws1(m: Message,s: FSMContext): await _num(m,s,"views",WeeklyStatsFlow.likes,"Лайки:")
@router.message(WeeklyStatsFlow.likes)
async def ws2(m: Message,s: FSMContext): await _num(m,s,"likes",WeeklyStatsFlow.replies,"Ответы:")
@router.message(WeeklyStatsFlow.replies)
async def ws3(m: Message,s: FSMContext): await _num(m,s,"replies",WeeklyStatsFlow.reposts,"Репосты:")
@router.message(WeeklyStatsFlow.reposts)
async def ws4(m: Message,s: FSMContext): await _num(m,s,"reposts",WeeklyStatsFlow.quotes,"Цитирования:")
@router.message(WeeklyStatsFlow.quotes)
async def ws5(m: Message,s: FSMContext): await _num(m,s,"quotes",WeeklyStatsFlow.new_followers,"Новые подписчики:")
@router.message(WeeklyStatsFlow.new_followers)
async def ws6(m: Message,s: FSMContext): await _num(m,s,"new_followers",WeeklyStatsFlow.telegram_clicks,"Переходы в Telegram:")
@router.message(WeeklyStatsFlow.telegram_clicks)
async def ws7(m: Message,s: FSMContext): await _num(m,s,"telegram_clicks",WeeklyStatsFlow.best_post,"Лучшая ветка или -:")
@router.message(WeeklyStatsFlow.best_post)
async def ws8(m: Message,s: FSMContext): await s.update_data(best_post=None if m.text=="-" else m.text); await s.set_state(WeeklyStatsFlow.manager_comment); await m.answer("Комментарий менеджера или -:")
@router.message(WeeklyStatsFlow.manager_comment)
async def ws9(m: Message,s: FSMContext):
    d=await s.get_data(); d["manager_comment"]=None if m.text=="-" else m.text; today=date.today(); start=today-timedelta(days=today.weekday()); end=start+timedelta(days=6); await DB.save_weekly_stats(d["client_id"],start.isoformat(),end.isoformat(),d); await topic_log(m.bot,DB,SETTINGS.work_group_id,d["client_id"],"📈 Администратор внёс недельную статистику."); await s.clear(); await m.answer("Статистика сохранена ✅",reply_markup=admin_menu())
