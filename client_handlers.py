from __future__ import annotations

from datetime import date, timedelta
from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from states import ManagerMessage, PartialPublication, ResultsFlow
from keyboards import admin_menu, client_menu
from posts import send_today_posts
from topics import ensure_topic, topic_log

router = Router()
DB = None
SETTINGS = None
SHEETS = None

def configure(db, settings, sheets):
    global DB, SETTINGS, SHEETS
    DB, SETTINGS, SHEETS = db, settings, sheets

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear(); db=DB; settings=SETTINGS
    if message.from_user.id == settings.admin_id:
        await message.answer("Админ-центр Threads Client Center 2.0", reply_markup=admin_menu()); return
    current = await db.get_client_by_tg(message.from_user.id)
    if current:
        await message.answer("""👋 <b>Добро пожаловать в личный кабинет!</b>

Здесь собраны все материалы для вашей работы и продвижения в Threads.

<b>Что доступно:</b>

📅 <b>Ветки</b> — готовые публикации на сегодня.
📄 <b>Контент-план</b> — стратегия и календарь контента.
📊 <b>Мои результаты</b> — фиксация статистики и прогресса.
💬 <b>Связь с менеджером</b> — помощь и ответы на вопросы.

🚀 Желаем продуктивной работы и отличных результатов!""", reply_markup=client_menu()); return
    parts=(message.text or "").split(maxsplit=1)
    if len(parts)==2 and parts[1].startswith("invite_"):
        c=await db.bind_client(parts[1][7:],message.from_user.id)
        if c:
            await db.log_event(c["id"],"client_bound"); await topic_log(message.bot,db,settings.work_group_id,c["id"],"✅ Клиент подключил личный кабинет."); await message.answer("""👋 <b>Добро пожаловать в личный кабинет!</b>

Здесь собраны все материалы для вашей работы и продвижения в Threads.

<b>Что доступно:</b>

📅 <b>Ветки</b> — готовые публикации на сегодня.
📄 <b>Контент-план</b> — стратегия и календарь контента.
📊 <b>Мои результаты</b> — фиксация статистики и прогресса.
💬 <b>Связь с менеджером</b> — помощь и ответы на вопросы.

🚀 Желаем продуктивной работы и отличных результатов!""",reply_markup=client_menu()); return
    await message.answer("Ссылка подключения недействительна или кабинет ещё не создан.")

@router.message(F.text == "📅 Ветки")
async def posts(message: Message):
    c=await DB.get_client_by_tg(message.from_user.id)
    if not c: await message.answer("Личный кабинет не найден."); return
    ok,text=await send_today_posts(message.bot,DB,SHEETS,SETTINGS,c,force=False)
    if not ok: await message.answer(text)

@router.message(F.text == "📄 Контент-план")
async def plan(message: Message):
    c=await DB.get_client_by_tg(message.from_user.id)
    if not c or not c["content_plan_url"]: await message.answer("Контент-план пока не добавлен."); return
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть контент-план",url=c["content_plan_url"])]])
    await message.answer("Ваш контент-план:",reply_markup=kb)

@router.message(F.text == "💬 Связь с менеджером")
async def manager(message: Message,state:FSMContext):
    c=await DB.get_client_by_tg(message.from_user.id)
    if not c: await message.answer("Личный кабинет не найден."); return
    await state.set_state(ManagerMessage.text); await message.answer("Напишите сообщение менеджеру:")

@router.message(ManagerMessage.text)
async def manager_send(message:Message,state:FSMContext):
    c=await DB.get_client_by_tg(message.from_user.id); settings=SETTINGS
    if not c: await state.clear(); return
    tid=await ensure_topic(message.bot,DB,settings.work_group_id,c["id"])
    if settings.work_group_id and tid:
        await message.bot.send_message(settings.work_group_id,"<b>Сообщение от клиента</b>",message_thread_id=tid)
        await message.bot.copy_message(settings.work_group_id,message.chat.id,message.message_id,message_thread_id=tid)
        await DB.log_event(c["id"],"client_message")
        await message.answer("Сообщение отправлено менеджеру ✅",reply_markup=client_menu())
    else: await message.answer("Рабочий чат пока не настроен.",reply_markup=client_menu())
    await state.clear()

@router.message(F.text == "📊 Мои результаты")
async def results_start(message:Message,state:FSMContext):
    c=await DB.get_client_by_tg(message.from_user.id)
    if not c: return
    await state.set_state(ResultsFlow.responses); await message.answer("Сколько было откликов за последние 2 дня?")

@router.message(ResultsFlow.responses)
async def res1(m:Message,s:FSMContext):
    try:v=int(m.text or "")
    except ValueError: await m.answer("Введите число:"); return
    await s.update_data(responses=v); await s.set_state(ResultsFlow.leads); await m.answer("Сколько было заявок?")

@router.message(ResultsFlow.leads)
async def res2(m:Message,s:FSMContext):
    try:v=int(m.text or "")
    except ValueError: await m.answer("Введите число:"); return
    await s.update_data(leads=v); await s.set_state(ResultsFlow.comment); await m.answer("Комментарий или -:")

@router.message(ResultsFlow.comment)
async def res3(m:Message,s:FSMContext):
    c=await DB.get_client_by_tg(m.from_user.id); d=await s.get_data(); end=date.today(); start=end-timedelta(days=1); comment=None if m.text=="-" else m.text
    await DB.save_client_result(c["id"],start.isoformat(),end.isoformat(),d["responses"],d["leads"],comment)
    await topic_log(m.bot,DB,SETTINGS.work_group_id,c["id"],f"📊 Результаты за 2 дня\nОтклики: {d['responses']}\nЗаявки: {d['leads']}\nКомментарий: {comment or '—'}")
    await DB.log_event(c["id"],"results_submitted",d); await s.clear(); await m.answer("Результаты сохранены ✅",reply_markup=client_menu())

@router.callback_query(F.data.startswith("pub:"))
async def publication(callback:CallbackQuery,state:FSMContext):
    _,status,day=callback.data.split(":"); c=await DB.get_client_by_tg(callback.from_user.id)
    if not c: return
    a=await DB.analytics(c["id"]); total=max(a["sent"],0)
    if status=="partial":
        await state.update_data(day=day,total=total,client_id=c["id"]); await state.set_state(PartialPublication.count); await callback.message.answer("Сколько веток опубликовано?"); await callback.answer(); return
    published=total if status=="all" else 0
    await DB.save_publication_confirmation(c["id"],day,total,published,status)
    await topic_log(callback.bot,DB,SETTINGS.work_group_id,c["id"],f"✅ Подтверждение публикации: {published} из {total}")
    await callback.answer("Сохранено ✅")

@router.message(PartialPublication.count)
async def partial(m:Message,s:FSMContext):
    d=await s.get_data()
    try:v=int(m.text or "")
    except ValueError: await m.answer("Введите число:"); return
    v=max(0,min(v,d["total"])); await DB.save_publication_confirmation(d["client_id"],d["day"],d["total"],v,"partial"); await topic_log(m.bot,DB,SETTINGS.work_group_id,d["client_id"],f"🟡 Опубликована часть: {v} из {d['total']}"); await s.clear(); await m.answer("Сохранено ✅",reply_markup=client_menu())

@router.message(F.chat.type.in_({"supergroup","group"}))
async def manager_reply(message:Message):
    settings=SETTINGS
    if not settings.work_group_id or message.chat.id!=settings.work_group_id or not message.message_thread_id or message.from_user.is_bot: return
    c=await DB.get_client_by_topic(message.message_thread_id)
    if not c or not c["telegram_id"]: return
    await message.bot.copy_message(c["telegram_id"],message.chat.id,message.message_id)
    await DB.log_event(c["id"],"manager_message")
