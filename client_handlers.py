from __future__ import annotations

from datetime import date, timedelta
from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from states import ManagerMessage, PartialPublication, ResultsFlow, ConsentFlow
from keyboards import admin_menu, client_menu, docs_begin_kb, contract_accept_kb, pd_consent_kb
from posts import send_today_posts
from topics import ensure_topic, topic_log
from documents import generate_contract_pdf, generate_policy_pdf, contract_version, policy_version, temp_pdf

router = Router()
DB = None
SETTINGS = None
SHEETS = None

def configure(db, settings, sheets):
    global DB, SETTINGS, SHEETS
    DB, SETTINGS, SHEETS = db, settings, sheets


async def _documents_gate(message: Message, client) -> bool:
    """Return True only after contract acceptance and separate PD consent."""
    if await DB.documents_fully_accepted(client["id"]):
        return True
    await message.answer(
        "Перед продолжением нужно оформить документы.",
        reply_markup=docs_begin_kb(),
    )
    return False


async def _show_client_cabinet(message: Message):
    await message.answer("""👋 <b>Добро пожаловать в личный кабинет!</b>

Здесь собраны все материалы для вашей работы и продвижения в Threads.

<b>Что доступно:</b>

📅 <b>Ветки</b> — готовые публикации на сегодня.
📄 <b>Контент-план</b> — стратегия и календарь контента.
📊 <b>Мои результаты</b> — фиксация статистики и прогресса.
💬 <b>Связь с менеджером</b> — помощь и ответы на вопросы.

🚀 Желаем продуктивной работы и отличных результатов!""", reply_markup=client_menu())


async def _send_current_documents(message: Message, client):
    signer = client["legal_name"]
    if not signer:
        raise RuntimeError("legal_name is required")

    expected_contract_version = contract_version(client, signer, SETTINGS)
    expected_policy_version = policy_version(SETTINGS)

    # Reuse Telegram files if terms/version have not changed.
    if (
        client["contract_file_id"] and client["policy_file_id"]
        and client["contract_version"] == expected_contract_version
        and client["policy_version"] == expected_policy_version
    ):
        await message.answer_document(
            client["contract_file_id"],
            caption="📄 <b>Договор оказания услуг</b>"
        )
        await message.answer_document(
            client["policy_file_id"],
            caption="🔐 <b>Политика обработки персональных данных</b>"
        )
        return

    contract_path = temp_pdf(f"contract_{client['id']}_")
    policy_path = temp_pdf("policy_")
    try:
        generate_contract_pdf(client, signer, SETTINGS, contract_path, draft=False)
        generate_policy_pdf(SETTINGS, policy_path)

        sent_contract = await message.answer_document(
            document=__import__("aiogram.types", fromlist=["FSInputFile"]).FSInputFile(contract_path),
            caption="📄 <b>Договор оказания услуг</b>\nПроверьте услуги, стоимость и ваши ФИО."
        )
        sent_policy = await message.answer_document(
            document=__import__("aiogram.types", fromlist=["FSInputFile"]).FSInputFile(policy_path),
            caption="🔐 <b>Политика обработки персональных данных</b>"
        )

        await DB.set_client_documents(
            client["id"],
            contract_file_id=sent_contract.document.file_id,
            policy_file_id=sent_policy.document.file_id,
            contract_version=expected_contract_version,
            policy_version=expected_policy_version,
            reset_acceptance=True,
        )
    finally:
        for path in (contract_path, policy_path):
            try:
                __import__("os").remove(path)
            except OSError:
                pass


async def _begin_documents(message: Message, state: FSMContext, client):
    if await DB.documents_fully_accepted(client["id"]):
        await _show_client_cabinet(message)
        return

    if not client["services"] or not client["service_price"] or not client["billing_start"]:
        await message.answer(
            "Менеджер ещё не заполнил услуги, стоимость и дату расчётного периода проекта. "
            "Документы будут доступны после заполнения карточки."
        )
        return

    if not client["legal_name"]:
        await state.set_state(ConsentFlow.signer_name)
        await message.answer(
            "Для формирования договора введите ваши ФИО полностью.\n\n"
            "Например: Иванов Иван Иванович."
        )
        return

    await _send_current_documents(message, client)
    await message.answer(
        "Пожалуйста, ознакомьтесь с договором.\n\n"
        "Нажимая «Подписать договор», вы подтверждаете, что прочитали документ "
        "и принимаете его условия.",
        reply_markup=contract_accept_kb(),
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    db = DB
    settings = SETTINGS

    if message.from_user.id == settings.admin_id:
        await message.answer("Админ-центр Threads Client Center 2.0", reply_markup=admin_menu())
        return

    current = await db.get_client_by_tg(message.from_user.id)
    if current:
        await _begin_documents(message, state, current)
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("invite_"):
        c = await db.bind_client(parts[1][7:], message.from_user.id)
        if c:
            await db.log_event(c["id"], "client_bound")
            await topic_log(
                message.bot,
                db,
                settings.work_group_id,
                c["id"],
                "✅ Клиент подключил личный кабинет.",
            )
            c = await db.get_client(c["id"])
            await _begin_documents(message, state, c)
            return

    await message.answer("Ссылка подключения недействительна или кабинет ещё не создан.")


@router.callback_query(F.data == "docs_begin")
async def docs_begin(callback: CallbackQuery, state: FSMContext):
    c = await DB.get_client_by_tg(callback.from_user.id)
    if not c:
        await callback.answer("Кабинет не найден", show_alert=True)
        return
    await _begin_documents(callback.message, state, c)
    await callback.answer()


@router.message(ConsentFlow.signer_name)
async def docs_signer_name(message: Message, state: FSMContext):
    c = await DB.get_client_by_tg(message.from_user.id)
    if not c:
        await state.clear()
        return
    signer = (message.text or "").strip()
    if len(signer.split()) < 2 or len(signer) < 5:
        await message.answer("Введите ФИО полностью, например: Иванов Иван Иванович.")
        return

    await DB.set_client_legal_name(c["id"], signer)
    await DB.invalidate_client_documents(c["id"])
    await state.clear()
    c = await DB.get_client(c["id"])
    await _send_current_documents(message, c)
    await message.answer(
        "Пожалуйста, ознакомьтесь с договором.\n\n"
        "Нажимая «Подписать договор», вы подтверждаете, что прочитали документ "
        "и принимаете его условия.",
        reply_markup=contract_accept_kb(),
    )


@router.callback_query(F.data == "contract_accept")
async def contract_accept(callback: CallbackQuery, state: FSMContext):
    c = await DB.get_client_by_tg(callback.from_user.id)
    if not c or not c["legal_name"]:
        await callback.answer("Сначала укажите ФИО", show_alert=True)
        return
    if not c["contract_file_id"] or not c["policy_file_id"]:
        await callback.answer("Документы нужно сформировать заново", show_alert=True)
        await _begin_documents(callback.message, state, c)
        return

    await DB.save_contract_acceptance(
        c["id"],
        c["legal_name"],
        callback.from_user.id,
        callback.from_user.username,
        c["contract_file_id"],
        c["policy_file_id"],
    )
    await DB.log_event(
        c["id"],
        "contract_accepted",
        {
            "signer_name": c["legal_name"],
            "telegram_id": callback.from_user.id,
            "contract_version": c["contract_version"],
        },
    )
    await topic_log(
        callback.bot, DB, SETTINGS.work_group_id, c["id"],
        f"✍️ Клиент подписал договор.\nФИО: {c['legal_name']}\nTelegram ID: {callback.from_user.id}\nВерсия: {c['contract_version']}"
    )

    await callback.message.answer(
        "<b>Согласие на обработку персональных данных</b>\n\n"
        "Я свободно, своей волей и в своем интересе даю согласие "
        f"{SETTINGS.executor_name} на обработку моих персональных данных "
        "для заключения и исполнения договора, коммуникации по проекту, "
        "ведения статистики и предоставления материалов. "
        f"С Политикой обработки персональных данных я ознакомлен(а). "
        f"Отозвать согласие можно через {SETTINGS.executor_email}.",
        reply_markup=pd_consent_kb(),
    )
    await callback.answer("Договор подтверждён ✅")


@router.callback_query(F.data == "pd_consent_accept")
async def pd_consent_accept(callback: CallbackQuery, state: FSMContext):
    c = await DB.get_client_by_tg(callback.from_user.id)
    if not c:
        await callback.answer("Кабинет не найден", show_alert=True)
        return
    consent = await DB.get_client_consent(c["id"])
    if not consent or not consent["contract_accepted_at"]:
        await callback.answer("Сначала подпишите договор", show_alert=True)
        return

    await DB.save_pd_consent(c["id"])
    await DB.log_event(
        c["id"],
        "pd_consent_accepted",
        {
            "signer_name": c["legal_name"],
            "telegram_id": callback.from_user.id,
            "policy_version": c["policy_version"],
        },
    )
    await topic_log(
        callback.bot, DB, SETTINGS.work_group_id, c["id"],
        f"✅ Клиент дал согласие на обработку персональных данных.\n"
        f"ФИО: {c['legal_name']}\nTelegram ID: {callback.from_user.id}\n"
        f"Версия политики: {c['policy_version']}"
    )
    await callback.answer("Согласие сохранено ✅")
    await callback.message.answer(
        "✅ Документы оформлены. Личный кабинет открыт.",
        reply_markup=client_menu(),
    )

@router.message(F.text == "📅 Ветки")
async def posts(message: Message):
    c=await DB.get_client_by_tg(message.from_user.id)
    if not c: await message.answer("Личный кабинет не найден."); return
    if not await _documents_gate(message, c): return
    ok,text=await send_today_posts(message.bot,DB,SHEETS,SETTINGS,c,force=False)
    if not ok: await message.answer(text)

@router.message(F.text == "📄 Контент-план")
async def plan(message: Message):
    c=await DB.get_client_by_tg(message.from_user.id)
    if not c:
        await message.answer("Личный кабинет не найден.")
        return
    if not await _documents_gate(message, c):
        return
    if not c["sheet_url"]:
        await message.answer("Контент-план пока не добавлен.")
        return
    kb=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть контент-план", url=c["sheet_url"])]]
    )
    await message.answer("Ваш контент-план:", reply_markup=kb)

@router.message(F.text == "💬 Связь с менеджером")
async def manager(message: Message,state:FSMContext):
    c=await DB.get_client_by_tg(message.from_user.id)
    if not c: await message.answer("Личный кабинет не найден."); return
    if not await _documents_gate(message, c): return
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
    if not await _documents_gate(message, c): return
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


# ---------------------------------------------------------------------------
# Direct client -> manager bridge
# Registered clients can write to the bot at any time without first pressing
# "Связь с менеджером". Existing menu/FSM handlers above keep priority.
# ---------------------------------------------------------------------------
@router.message(F.chat.type == "private")
async def client_direct_message_bridge(message: Message, state: FSMContext):
    # Admin messages are handled only by admin flows.
    if message.from_user.id == SETTINGS.admin_id:
        return

    # Do not steal messages from an active FSM scenario
    # (results, manager-message flow, publication confirmation, etc.).
    if await state.get_state():
        return

    client = await DB.get_client_by_tg(message.from_user.id)
    if not client:
        return
    if not await DB.documents_fully_accepted(client["id"]):
        return

    # Menu buttons are already handled by the dedicated handlers above.
    if message.text in {
        "/start",
        "📅 Ветки",
        "📄 Контент-план",
        "📊 Мои результаты",
        "💬 Связь с менеджером",
    }:
        return

    try:
        topic_id = await ensure_topic(
            message.bot,
            DB,
            SETTINGS.work_group_id,
            client["id"],
        )
        if not SETTINGS.work_group_id or not topic_id:
            await message.answer("Рабочий чат пока не настроен.")
            return

        await message.bot.send_message(
            SETTINGS.work_group_id,
            "<b>Сообщение от клиента</b>",
            message_thread_id=topic_id,
        )
        await message.bot.copy_message(
            chat_id=SETTINGS.work_group_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            message_thread_id=topic_id,
        )
        await DB.log_event(client["id"], "client_message")
        await message.answer(
            "Сообщение отправлено менеджеру ✅",
            reply_markup=client_menu(),
        )
    except Exception:
        import logging
        logging.exception(
            "Не удалось передать прямое сообщение клиента в рабочую тему"
        )
        await message.answer(
            "Не удалось передать сообщение менеджеру. Попробуйте ещё раз чуть позже."
        )
