from __future__ import annotations
from datetime import datetime
import asyncio

from datetime import date, timedelta
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, InputMediaPhoto

from states import AddClient, LinkPlan, LinkSheet, WeeklyStatsFlow, BaselineFlow, WeeklyAnalyticsFlow, ClientDocsFlow, ClientTermsFlow, ActFlow, BroadcastFlow
from keyboards import admin_menu, client_card_kb, confirm_client_kb, skip_photo_kb
from topics import ensure_topic, topic_log
from documents import generate_contract_pdf, generate_policy_pdf, generate_act_pdf, temp_pdf
from billing import period, fmt, latest_completed_period
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


_content_album_tasks = {}
_content_album_files = {}


async def _finish_content_album(message: Message, state: FSMContext, task_key, callback_data: str):
    await asyncio.sleep(1.5)

    files = list(_content_album_files.pop(task_key, []))
    _content_album_tasks.pop(task_key, None)
    if not files:
        return

    # Only the last task for the album is allowed to advance the FSM.
    current = await state.get_state()
    expected = (
        BaselineFlow.content_screen.state
        if callback_data == "baseline_content_done"
        else WeeklyAnalyticsFlow.content_screen.state
    )
    if current != expected:
        return

    data = await state.get_data()
    existing = list(data.get("content_file_ids") or [])
    for fid in files:
        if fid not in existing:
            existing.append(fid)

    await state.update_data(content_file_ids=existing, content_file_id=existing)

    if callback_data == "baseline_content_done":
        await state.set_state(BaselineFlow.telegram_screen)
        skip_callback = "baseline_skip_tg"
    else:
        await state.set_state(WeeklyAnalyticsFlow.telegram_screen)
        skip_callback = "weekly_skip_tg"

    await message.answer(
        f"✅ Альбом сохранён. Лучших постов: {len(existing)}.\n\n"
        "Пришлите скрин Telegram или нажмите «Пропустить»:",
        reply_markup=skip_photo_kb(skip_callback),
    )


async def _store_content_photo(message: Message, state: FSMContext, callback_data: str):
    fid = message.photo[-1].file_id

    if message.media_group_id:
        key = (message.chat.id, str(message.media_group_id), callback_data)
        bucket = _content_album_files.setdefault(key, [])
        if fid not in bucket:
            bucket.append(fid)

        old = _content_album_tasks.get(key)
        if old and not old.done():
            old.cancel()

        _content_album_tasks[key] = asyncio.create_task(
            _finish_content_album(message, state, key, callback_data)
        )
        return

    # One standalone image is also accepted and advances once.
    current = await state.get_state()
    expected = (
        BaselineFlow.content_screen.state
        if callback_data == "baseline_content_done"
        else WeeklyAnalyticsFlow.content_screen.state
    )
    if current != expected:
        return

    await state.update_data(content_file_ids=[fid], content_file_id=[fid])
    if callback_data == "baseline_content_done":
        await state.set_state(BaselineFlow.telegram_screen)
        skip_callback = "baseline_skip_tg"
    else:
        await state.set_state(WeeklyAnalyticsFlow.telegram_screen)
        skip_callback = "weekly_skip_tg"

    await message.answer(
        "✅ Фото сохранено.\n\nПришлите скрин Telegram или нажмите «Пропустить»:",
        reply_markup=skip_photo_kb(skip_callback),
    )


def content_screens_done_kb(callback_data: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Все лучшие посты загружены", callback_data=callback_data)]
    ])


def card_text(c):
    publish_mode = c["publish_mode"] if "publish_mode" in c.keys() else "client"
    publish_label = "👩‍💻 мы публикуем" if publish_mode == "team" else "👤 клиент публикует сам"
    price_text = f"{c['service_price']:,}".replace(",", " ") + " ₽/мес." if c["service_price"] else "—"
    if c["billing_start"]:
        ps, pe, _ = period(c["billing_start"], 0)
        ns, ne, ndue = period(c["billing_start"], 1)
        billing_text = f"{fmt(ps)}–{fmt(pe)}; следующий {fmt(ns)}–{fmt(ne)}; оплатить до {fmt(ndue)}"
    else:
        billing_text = "—"
    return (f"<b>{c['name']}</b>\n\nThreads: @{c['threads_username_normalized']}\nTelegram: @{c['telegram_username'] or '—'}\n"
            f"Публикация: {publish_label}\n"
            f"Услуги: {c['services'] or '—'}\n"
            f"Стоимость: {price_text}\n"
            f"Расчётный период: {billing_text}\n"
            f"Кабинет: {'подключён' if c['telegram_id'] else 'не подключён'}\n"
            f"Контент-план: {'подключён' if c['sheet_url'] else 'не подключён'}\n"
            f"Документы: {'✅ загружены' if c['contract_file_id'] and c['policy_file_id'] else '⏳ не загружены'}\nТема: {'создана' if c['topic_id'] else 'не создана'}\nСтатус: {'активен' if c['is_active'] else 'архив'}")

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
        await message.answer("Threads username обязателен.")
        return

    await state.update_data(threads=value)
    await state.set_state(AddClient.publish_mode)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👤 Клиент публикует сам", callback_data="add_publish_mode:client"),
        InlineKeyboardButton(text="👩‍💻 Ведём мы", callback_data="add_publish_mode:team"),
    ]])
    await message.answer(
        "Кто ведёт публикацию в Threads для этого проекта?",
        reply_markup=kb,
    )


@router.callback_query(AddClient.publish_mode, F.data.startswith("add_publish_mode:"))
async def add_publish_mode(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":", 1)[1]
    if mode not in {"client", "team"}:
        await callback.answer("Неверный вариант", show_alert=True)
        return

    await state.update_data(publish_mode=mode)

    if mode == "client":
        await state.set_state(AddClient.telegram)
        await callback.message.answer(
            "Введите Telegram username клиента: username, @username, ссылка t.me или -"
        )
    else:
        # Для проекта, который ведём мы, Telegram клиента не обязателен.
        await state.update_data(telegram="-")
        await state.set_state(AddClient.services)
        await callback.message.answer(
            "👩‍💻 Проект ведём мы.\n\n"
            "Какие услуги оказываем клиенту?\n\n"
            "Напишите свободным текстом, например:\n"
            "Создание 4 веток ежедневно, кроме субботы; публикация контента; аналитика"
        )

    await callback.answer()


@router.message(AddClient.telegram)
async def add_telegram(message: Message, state: FSMContext):
    telegram = (message.text or "").strip()
    await state.update_data(telegram=telegram)
    await state.set_state(AddClient.services)
    await message.answer(
        "Какие услуги оказываем клиенту?\n\n"
        "Напишите свободным текстом, например:\n"
        "Создание 4 веток ежедневно, кроме субботы; аналитика"
    )


@router.message(AddClient.services)
async def add_services(message: Message, state: FSMContext):
    services = (message.text or "").strip()
    if len(services) < 3:
        await message.answer("Опишите услуги чуть подробнее.")
        return

    await state.update_data(services=services)
    await state.set_state(AddClient.service_price)
    await message.answer(
        "💳 Стоимость услуг за расчётный период?\n\n"
        "Введите сумму, например: 30000"
    )


@router.message(AddClient.service_price)
async def add_service_price(message: Message, state: FSMContext):
    raw = (message.text or "").strip().lower().replace(" ", "").replace("₽", "").replace("рублей", "").replace("руб.", "").replace("руб", "")
    if not raw.isdigit():
        await message.answer("Введите стоимость числом, например: 30000")
        return
    price = int(raw)
    if price <= 0:
        await message.answer("Стоимость должна быть больше 0.")
        return
    await state.update_data(service_price=price)
    await state.set_state(AddClient.billing_start)
    await message.answer("📅 Когда начинается первый расчётный период клиента?\n\nВведите дату ДД.ММ.ГГГГ\nНапример: 15.08.2026")


@router.message(AddClient.billing_start)
async def add_billing_start(message: Message, state: FSMContext):
    try:
        dt = datetime.strptime((message.text or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Введите дату в формате ДД.ММ.ГГГГ, например: 15.08.2026")
        return
    iso=dt.isoformat()
    await state.update_data(billing_start=iso)
    await state.set_state(AddClient.confirm)
    data=await state.get_data()
    label="👩‍💻 Мы публикуем" if data.get("publish_mode")=="team" else "👤 Клиент сам"
    ps,pe,due=period(iso,0); ns,ne,ndue=period(iso,1)
    price_text=f"{data['service_price']:,}".replace(",", " ")
    await message.answer(
        f"Проверьте данные:\n\nИмя: {data['name']}\nThreads: @{DB.normalize_threads(data['threads'])}\n"
        f"Telegram: @{DB.normalize_telegram(data.get('telegram')) or '—'}\nПубликация: {label}\n"
        f"Услуги: {data['services']}\nСтоимость: {price_text} ₽/мес.\n"
        f"Первый период: {fmt(ps)}–{fmt(pe)}\nОплата до: {fmt(due)}\n"
        f"Следующий период: {fmt(ns)}–{fmt(ne)}\nСледующая оплата до: {fmt(ndue)}",
        reply_markup=confirm_client_kb()
    )


@router.callback_query(F.data == "client_confirm_create")
async def add_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await is_admin(callback.from_user.id, router): return
    data = await state.get_data()
    try:
        c = await DB.create_client(
            data["name"], data["threads"], data.get("telegram"),
            data.get("publish_mode", "client"), data.get("services"), data.get("service_price"), data.get("billing_start")
        )
    except ValueError as exc:
        await callback.message.answer(str(exc), reply_markup=admin_menu()); await state.clear(); await callback.answer(); return
    await ensure_topic(bot, DB, SETTINGS.work_group_id, c["id"])
    c = await DB.get_client(c["id"])
    await DB.log_event(c["id"], "client_created")
    await state.clear()
    me = await bot.get_me()
    invite = f"https://t.me/{me.username}?start=invite_{c['invite_code']}"
    if c["publish_mode"] == "team":
        extra = (
            "\n\nПроект ведём мы.\n"
            "Передайте клиенту эту ссылку: через бот он подпишет договор "
            "и затем сможет получать аналитику по проекту."
            f"\n\nСсылка клиента:\n{invite}"
        )
    else:
        extra = (
            "\n\nПередайте клиенту ссылку для подключения, подписания договора "
            "и получения материалов/аналитики:"
            f"\n\nСсылка клиента:\n{invite}"
        )
    await callback.message.answer(card_text(c) + extra, reply_markup=client_card_kb(c["id"], c["topic_id"], SETTINGS.work_group_id))
    await callback.message.answer("Не забудьте зафиксировать стартовые показатели клиента через кнопку «🚀 Старт проекта».")
    await callback.answer()

@router.callback_query(F.data == "client_confirm_edit")
async def add_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddClient.name); await callback.message.answer("Введите имя заново:"); await callback.answer()

@router.callback_query(F.data == "client_confirm_cancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear(); await callback.message.answer("Создание отменено.", reply_markup=admin_menu()); await callback.answer()


@router.message(F.text == "📣 Сообщение всем")
async def broadcast_start(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id, router):
        return

    rows = await DB.list_clients(True)
    connected = [c for c in rows if c["telegram_id"]]
    if not connected:
        await message.answer("Нет активных клиентов с подключённым кабинетом.")
        return

    await state.clear()
    await state.set_state(BroadcastFlow.text)
    await message.answer(
        f"📣 Сообщение получат <b>{len(connected)}</b> активных клиентов.\n\n"
        "Отправьте текст рассылки одним сообщением."
    )


@router.message(BroadcastFlow.text)
async def broadcast_preview(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id, router):
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("Отправьте текст сообщения.")
        return

    if len(text) > 4096:
        await message.answer("Сообщение слишком длинное. Максимум — 4096 символов.")
        return

    rows = await DB.list_clients(True)
    connected = [c for c in rows if c["telegram_id"]]
    if not connected:
        await state.clear()
        await message.answer("Нет активных клиентов с подключённым кабинетом.", reply_markup=admin_menu())
        return

    await state.update_data(broadcast_text=text)
    await message.answer(
        f"📣 <b>Предпросмотр рассылки</b>\n\n{text}\n\n"
        f"Получателей: <b>{len(connected)}</b>.\n"
        "Отправить всем?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить всем", callback_data="broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel")],
        ]),
    )


@router.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id, router):
        return

    await state.clear()
    await callback.message.answer("Рассылка отменена.", reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id, router):
        return

    data = await state.get_data()
    text = (data.get("broadcast_text") or "").strip()
    if not text:
        await state.clear()
        await callback.answer("Текст рассылки не найден", show_alert=True)
        return

    rows = await DB.list_clients(True)
    connected = [c for c in rows if c["telegram_id"]]

    # Clear first so a repeated callback cannot accidentally reuse the draft.
    await state.clear()
    await callback.answer("Отправляю…")

    sent = 0
    failed = 0
    failed_names = []

    for client in connected:
        try:
            await callback.bot.send_message(
                chat_id=client["telegram_id"],
                text=text,
            )
            sent += 1
            try:
                await DB.log_event(
                    client["id"],
                    "broadcast_sent",
                    {"text": text},
                )
            except Exception:
                pass
        except Exception:
            failed += 1
            failed_names.append(client["name"])

    result = (
        f"📣 Рассылка завершена.\n\n"
        f"✅ Отправлено: {sent}\n"
        f"⚠️ Не доставлено: {failed}"
    )
    if failed_names:
        result += "\n\nНе доставлено:\n" + "\n".join(f"• {name}" for name in failed_names[:20])
        if len(failed_names) > 20:
            result += f"\n…и ещё {len(failed_names) - 20}"

    await callback.message.answer(result, reply_markup=admin_menu())


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
    await callback.message.answer(card_text(c), reply_markup=client_card_kb(c["id"], c["topic_id"], SETTINGS.work_group_id))
    await callback.answer()

@router.callback_query(F.data.startswith("client_invite:"))
async def invite(callback: CallbackQuery, bot: Bot):
    c = await DB.get_client(int(callback.data.split(":")[1])); me = await bot.get_me(); await callback.message.answer(f"https://t.me/{me.username}?start=invite_{c['invite_code']}"); await callback.answer()

@router.callback_query(F.data.startswith("client_topic:"))
async def topic(callback: CallbackQuery, bot: Bot):
    cid = int(callback.data.split(":")[1]); tid = await ensure_topic(bot, DB, SETTINGS.work_group_id, cid); await callback.answer("Тема создана ✅" if tid else "WORK_GROUP_ID не настроен", show_alert=True)

@router.callback_query(F.data.startswith("client_sheet:"))
async def sheet_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(client_id=int(callback.data.split(":")[1])); await state.set_state(LinkSheet.url); await callback.message.answer("Пришлите ссылку на Google-таблицу контент-плана:"); await callback.answer()

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
        await message.answer(f"Не удалось подключить контент-план:\n{exc}\n\nИсправьте доступ или ссылку и пришлите её ещё раз.")
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
    await topic_log(message.bot, DB, SETTINGS.work_group_id, client_id, "📄 Контент-план подключён и проверен.")
    await state.clear()
    client = await DB.get_client(client_id)
    await message.answer(
        f"Контент-план подключён ✅\nСтрок на первом листе: {check['rows']}",
        reply_markup=client_card_kb(client["id"], client["topic_id"], SETTINGS.work_group_id),
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



async def _client_analytics_text(client_id: int) -> str:
    a = await DB.analytics(client_id)
    baseline = await DB.get_baseline(client_id)
    history = await DB.get_weekly_history(client_id, limit=12)
    latest = history[0] if history else None

    text = "<b>📊 Статистика аккаунта</b>"

    if latest:
        previous_row = history[1] if len(history) > 1 else baseline
        previous_threads = int(previous_row["threads_followers"] or 0) if previous_row else 0
        previous_telegram = int(previous_row["telegram_followers"] or 0) if previous_row else 0
        threads_growth = int(latest["threads_followers"] or 0) - previous_threads
        telegram_growth = int(latest["telegram_followers"] or 0) - previous_telegram

        text += (
            f"\n\n👀 Общие просмотры аккаунта: <b>{latest['total_views']:,}</b>"
            f"\n📈 За последнюю неделю: <b>+{latest['views']:,}</b>"
            f"\n👥 Подписчиков Threads: <b>{latest['threads_followers']:,}</b> "
            f"(<b>{threads_growth:+,}</b>)"
            f"\n📣 Подписчиков Telegram: <b>{latest['telegram_followers']:,}</b> "
            f"(<b>{telegram_growth:+,}</b>)"
            f"\n🎯 Заявок за неделю: <b>{latest['applications']:,}</b>"
        )
    elif baseline:
        text += (
            f"\n\n👀 Общие просмотры аккаунта: <b>{baseline['total_views']:,}</b>"
            f"\n👥 Подписчиков Threads: <b>{baseline['threads_followers']:,}</b>"
            f"\n📣 Подписчиков Telegram: <b>{baseline['telegram_followers']:,}</b>"
            "\n\nНедельная статистика ещё не внесена."
        )
    else:
        text += "\n\nСтартовые показатели ещё не внесены."

    text += (
        f"\n\n<b>Работа с контентом</b>"
        f"\nОтправлено веток: {a['sent']}"
        f"\nОпубликовано: {a['published']}"
        f"\nДисциплина: {a['discipline']}%"
        f"\nОтклики: {a['responses']}"
        f"\nЗаявки: {a['leads']}"
    )

    if history:
        text += "\n\n<b>История роста</b>"
        ordered = list(reversed(history))
        previous_threads = int(baseline["threads_followers"] or 0) if baseline else 0
        previous_telegram = int(baseline["telegram_followers"] or 0) if baseline else 0

        for row in ordered:
            threads_growth = int(row["threads_followers"] or 0) - previous_threads
            telegram_growth = int(row["telegram_followers"] or 0) - previous_telegram
            text += (
                f"\n\n{row['week_start']}–{row['week_end']}"
                f"\nОбщие просмотры: {row['total_views']:,}"
                f"\nПросмотры за неделю: +{row['views']:,}"
                f"\nThreads: {row['threads_followers']:,} ({threads_growth:+,})"
                f"\nTelegram: {row['telegram_followers']:,} ({telegram_growth:+,})"
                f"\nЗаявки: {row['applications']:,}"
            )
            previous_threads = int(row["threads_followers"] or 0)
            previous_telegram = int(row["telegram_followers"] or 0)

        if len(history) >= 2:
            newest = history[0]["views"]
            prior = history[1]["views"]
            if newest > prior:
                text += "\n\n🟢 Темп роста ускоряется"
            elif newest < prior:
                text += "\n\n🟡 Темп роста замедляется"
            else:
                text += "\n\n⚪ Темп роста без изменений"

    return text


@router.callback_query(F.data.startswith("client_send_analytics:"))
async def client_send_analytics(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, router):
        return

    cid = int(callback.data.split(":")[1])
    c = await DB.get_client(cid)
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    if not c["telegram_id"]:
        me = await callback.bot.get_me()
        invite = f"https://t.me/{me.username}?start=invite_{c['invite_code']}"
        await callback.message.answer(
            "Клиент ещё не подключён к боту.\n\n"
            "Передайте ему ссылку — после подключения он сможет подписать договор "
            "и получать аналитику:\n"
            f"{invite}"
        )
        await callback.answer()
        return

    if not await DB.documents_fully_accepted(cid):
        await callback.message.answer(
            "Клиент подключён, но ещё не завершил оформление документов. "
            "Аналитику отправим после подписания договора и согласия на обработку персональных данных."
        )
        await callback.answer()
        return

    text = await _client_analytics_text(cid)
    await callback.bot.send_message(c["telegram_id"], text)
    await DB.log_event(cid, "analytics_sent_to_client")
    await topic_log(
        callback.bot, DB, SETTINGS.work_group_id, cid,
        "📊 Клиенту отправлена актуальная аналитика по проекту."
    )
    await callback.answer("Аналитика отправлена ✅")

@router.callback_query(F.data.startswith("client_screens:"))
async def client_screens(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, router):
        return

    cid = int(callback.data.split(":")[1])
    client = await DB.get_client(cid)
    if not client:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    screenshots = await DB.get_client_screenshots(cid)
    await callback.answer()

    if not screenshots:
        await callback.message.answer(
            f"🖼 У клиента «{client['name']}» пока нет сохранённых скринов."
        )
        return

    await callback.message.answer(
        f"🖼 <b>Все сохранённые скрины: {client['name']}</b>\n"
        f"Всего: {len(screenshots)}"
    )

    # Telegram media groups support up to 10 items.
    for offset in range(0, len(screenshots), 10):
        batch = screenshots[offset:offset + 10]
        media = [
            InputMediaPhoto(
                media=item["file_id"],
                caption=item["label"],
            )
            for item in batch
        ]
        try:
            if len(media) == 1:
                await callback.bot.send_photo(
                    callback.message.chat.id,
                    photo=batch[0]["file_id"],
                    caption=batch[0]["label"],
                )
            else:
                await callback.bot.send_media_group(
                    callback.message.chat.id,
                    media=media,
                )
        except Exception:
            # If one old Telegram file_id is no longer available,
            # send the remaining screenshots individually so one broken item
            # does not block the whole archive.
            for item in batch:
                try:
                    await callback.bot.send_photo(
                        callback.message.chat.id,
                        photo=item["file_id"],
                        caption=item["label"],
                    )
                except Exception:
                    await callback.message.answer(
                        f"⚠️ Не удалось открыть скрин: {item['label']}"
                    )


@router.callback_query(F.data.startswith("client_analytics:"))
async def analytics(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, router):
        return
    cid = int(callback.data.split(":")[1])
    await callback.message.answer(await _client_analytics_text(cid))
    await callback.answer()


@router.callback_query(F.data.startswith("baseline_start:"))
async def baseline_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id, router): return
    cid = int(callback.data.split(":")[1])
    if not await DB.get_client(cid):
        await callback.answer("Клиент не найден", show_alert=True); return
    await state.clear(); await state.update_data(client_id=cid); await state.set_state(BaselineFlow.total_views)
    await callback.message.answer(
        "👀 Общие просмотры аккаунта на старте проекта:\n\n"
        "Введите число с верхней панели статистики Threads. Например: 78000"
    )
    await callback.answer()

async def _analytics_num(message: Message, state: FSMContext, key: str, next_state, prompt: str):
    try: value = int((message.text or "").replace(" ", ""))
    except ValueError: await message.answer("Введите целое число:"); return
    if value < 0: await message.answer("Число не может быть отрицательным:"); return
    await state.update_data(**{key:value}); await state.set_state(next_state); await message.answer(prompt)

@router.message(BaselineFlow.total_views)
async def baseline_total_views(message: Message, state: FSMContext):
    await _analytics_num(
        message, state, "total_views", BaselineFlow.threads_followers,
        "Количество подписчиков Threads:"
    )

@router.message(BaselineFlow.threads_followers)
async def baseline_1(message: Message, state: FSMContext): await _analytics_num(message, state,"threads_followers",BaselineFlow.telegram_followers,"Количество подписчиков Telegram (если канала нет — 0):")
@router.message(BaselineFlow.telegram_followers)
async def baseline_2(message: Message, state: FSMContext): await _analytics_num(message, state,"telegram_followers",BaselineFlow.overview_screen,'Пришлите скрин «Обзор» из статистики Threads:')
@router.message(BaselineFlow.overview_screen, F.photo)
async def baseline_4(message: Message, state: FSMContext):
    await state.update_data(overview_file_id=message.photo[-1].file_id, content_file_ids=[])
    await state.set_state(BaselineFlow.content_screen)
    await message.answer(
        "Выберите сразу все скрины лучших постов и отправьте их одним альбомом.\n\n"
        "Отправьте их одним альбомом — бот сохранит все фото разом и сам перейдёт дальше."
    )
@router.message(BaselineFlow.overview_screen)
async def baseline_4_bad(message: Message): await message.answer("Нужно отправить изображение.")
@router.message(BaselineFlow.content_screen, F.photo)
async def baseline_5(message: Message, state: FSMContext):
    await _store_content_photo(message, state, "baseline_content_done")

@router.callback_query(F.data == "baseline_content_done")
async def baseline_content_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    files = list(data.get("content_file_ids") or [])
    if not files:
        await callback.answer("Сначала загрузите хотя бы один скрин.", show_alert=True)
        return
    await state.update_data(content_file_id=files)
    await state.set_state(BaselineFlow.telegram_screen)
    await callback.message.answer(
        f"Лучшие посты сохранены: {len(files)} ✅\n\n"
        "Пришлите скрин Telegram или нажмите «Пропустить»:",
        reply_markup=skip_photo_kb("baseline_skip_tg"),
    )
    await callback.answer()

@router.message(BaselineFlow.content_screen)
async def baseline_5_bad(message: Message):
    await message.answer("Отправьте скрин лучшего поста или нажмите кнопку завершения после загрузки фотографий.")

async def _finish_baseline(message: Message, state: FSMContext, telegram_file_id=None):
    d=await state.get_data()
    d["telegram_file_id"]=telegram_file_id
    # Legacy DB compatibility only; applications/leads are no longer part of analytics.
    d["weekly_leads"]=0
    await DB.save_baseline(d["client_id"],d); await DB.log_event(d["client_id"],"baseline_saved")
    await topic_log(message.bot,DB,SETTINGS.work_group_id,d["client_id"],"🚀 Стартовые показатели клиента зафиксированы.")
    await state.clear(); await message.answer("Стартовые показатели сохранены ✅",reply_markup=admin_menu())

@router.message(BaselineFlow.telegram_screen, F.photo)
async def baseline_6(message: Message, state: FSMContext): await _finish_baseline(message, state, message.photo[-1].file_id)
@router.callback_query(F.data=="baseline_skip_tg")
async def baseline_skip(callback: CallbackQuery,state:FSMContext):
    await _finish_baseline(callback.message,state,None); await callback.answer()
@router.message(BaselineFlow.telegram_screen)
async def baseline_6_bad(message: Message): await message.answer("Отправьте изображение или нажмите «Пропустить».",reply_markup=skip_photo_kb("baseline_skip_tg"))

@router.callback_query(F.data.startswith("weekly_analytics:"))
async def weekly_analytics_start(callback: CallbackQuery,state:FSMContext):
    if not await is_admin(callback.from_user.id, router): return
    cid=int(callback.data.split(":")[1])
    if not await DB.get_client(cid): await callback.answer("Клиент не найден",show_alert=True); return
    previous = await DB.previous_account_totals(cid)
    await state.clear()
    await state.update_data(
        client_id=cid,
        previous_total_views=previous["total_views"],
        previous_threads_followers=previous["threads_followers"],
        previous_telegram_followers=previous["telegram_followers"],
    )
    await state.set_state(WeeklyAnalyticsFlow.total_views)
    await callback.message.answer(
        "👀 Текущие общие просмотры аккаунта:\n\n"
        f"Предыдущее значение: {previous['total_views']:,}\n"
        "Введите новое число с верхней панели статистики Threads."
    )
    await callback.answer()
@router.message(WeeklyAnalyticsFlow.total_views)
async def weekly_total_views(message: Message, state: FSMContext):
    try:
        current = int((message.text or "").replace(" ", ""))
    except ValueError:
        await message.answer("Введите целое число, например: 89000")
        return
    if current < 0:
        await message.answer("Число не может быть отрицательным.")
        return

    data = await state.get_data()
    previous = int(data.get("previous_total_views", 0))
    if previous and current < previous:
        await message.answer(
            f"Новое значение меньше предыдущего ({previous:,}). "
            "Проверьте число и отправьте ещё раз."
        )
        return

    weekly_growth = max(current - previous, 0)
    await state.update_data(total_views=current, views=weekly_growth)
    await state.set_state(WeeklyAnalyticsFlow.threads_followers)
    await message.answer(
        f"За неделю получилось: +{weekly_growth:,} просмотров ✅\n\n"
        "Текущее количество подписчиков Threads:"
    )

@router.message(WeeklyAnalyticsFlow.threads_followers)
async def wa1(message: Message, state: FSMContext):
    try:
        current = int((message.text or "").replace(" ", ""))
    except ValueError:
        await message.answer("Введите целое число, например: 1493")
        return
    if current < 0:
        await message.answer("Число не может быть отрицательным.")
        return

    data = await state.get_data()
    previous = int(data.get("previous_threads_followers", 0))
    growth = current - previous
    await state.update_data(
        threads_followers=current,
        threads_followers_growth=growth,
    )
    await state.set_state(WeeklyAnalyticsFlow.telegram_followers)
    await message.answer(
        f"Изменение подписчиков Threads: {growth:+,} ✅\n\n"
        "Текущее количество подписчиков Telegram (если канала нет — 0):"
    )


@router.message(WeeklyAnalyticsFlow.telegram_followers)
async def wa2(message: Message, state: FSMContext):
    try:
        current = int((message.text or "").replace(" ", ""))
    except ValueError:
        await message.answer("Введите целое число, например: 401")
        return
    if current < 0:
        await message.answer("Число не может быть отрицательным.")
        return

    data = await state.get_data()
    previous = int(data.get("previous_telegram_followers", 0))
    growth = current - previous
    await state.update_data(
        telegram_followers=current,
        telegram_followers_growth=growth,
    )
    await state.set_state(WeeklyAnalyticsFlow.overview_screen)
    await message.answer(
        f"Изменение подписчиков Telegram: {growth:+,} ✅\n\n"
        'Пришлите скрин «Обзор» из статистики Threads:'
    )
@router.message(WeeklyAnalyticsFlow.overview_screen,F.photo)
async def wa5(message: Message, state: FSMContext):
    await state.update_data(overview_file_id=message.photo[-1].file_id, content_file_ids=[])
    await state.set_state(WeeklyAnalyticsFlow.content_screen)
    await message.answer(
        "Выберите сразу все скрины лучших постов за неделю и отправьте их одним альбомом.\n\n"
        "Отправьте их одним альбомом — бот сохранит все фото разом и сам перейдёт дальше."
    )
@router.message(WeeklyAnalyticsFlow.overview_screen)
async def wa5_bad(message: Message): await message.answer("Нужно отправить изображение.")
@router.message(WeeklyAnalyticsFlow.content_screen, F.photo)
async def wa6(message: Message, state: FSMContext):
    await _store_content_photo(message, state, "weekly_content_done")

@router.callback_query(F.data == "weekly_content_done")
async def weekly_content_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    files = list(data.get("content_file_ids") or [])
    if not files:
        await callback.answer("Сначала загрузите хотя бы один скрин.", show_alert=True)
        return
    await state.update_data(content_file_id=files)
    await state.set_state(WeeklyAnalyticsFlow.telegram_screen)
    await callback.message.answer(
        f"Лучшие посты сохранены: {len(files)} ✅\n\n"
        "Пришлите скрин Telegram или нажмите «Пропустить»:",
        reply_markup=skip_photo_kb("weekly_skip_tg"),
    )
    await callback.answer()

@router.message(WeeklyAnalyticsFlow.content_screen)
async def wa6_bad(message: Message):
    await message.answer("Отправьте скрин лучшего поста или нажмите кнопку завершения после загрузки фотографий.")

async def _finish_weekly(message:Message,state:FSMContext,telegram_file_id=None):
    d=await state.get_data()
    d["telegram_file_id"]=telegram_file_id
    # Legacy DB compatibility only; applications are no longer requested or reported.
    d["applications"]=0
    today=date.today(); start=today-timedelta(days=today.weekday()); end=start+timedelta(days=6)
    await DB.save_weekly_analytics(d["client_id"],start.isoformat(),end.isoformat(),d); await DB.log_event(d["client_id"],"weekly_analytics_saved",{"week_start":start.isoformat()})
    await topic_log(message.bot,DB,SETTINGS.work_group_id,d["client_id"],"📈 Администратор внёс недельную статистику.")
    views_growth = int(d.get("views", 0))
    total_views = int(d.get("total_views", 0))
    threads_growth = int(d.get("threads_followers_growth", 0))
    telegram_growth = int(d.get("telegram_followers_growth", 0))
    await state.clear()
    await message.answer(
        f"Статистика сохранена ✅\n\n"
        f"👀 Общие просмотры: {total_views:,}\n"
        f"📈 Просмотры за неделю: +{views_growth:,}\n"
        f"👥 Threads: {d['threads_followers']:,} ({threads_growth:+,})\n"
        f"📣 Telegram: {d['telegram_followers']:,} ({telegram_growth:+,})",
        reply_markup=admin_menu(),
    )
@router.message(WeeklyAnalyticsFlow.telegram_screen,F.photo)
async def wa7(message: Message, state: FSMContext): await _finish_weekly(message, state, message.photo[-1].file_id)
@router.callback_query(F.data=="weekly_skip_tg")
async def weekly_skip(callback:CallbackQuery,state:FSMContext): await _finish_weekly(callback.message,state,None); await callback.answer()
@router.message(WeeklyAnalyticsFlow.telegram_screen)
async def wa7_bad(message: Message): await message.answer("Отправьте изображение или нажмите «Пропустить».",reply_markup=skip_photo_kb("weekly_skip_tg"))

@router.callback_query(F.data.startswith("weekly_stats:"))
async def weekly_start(callback: CallbackQuery, state: FSMContext):
    # Legacy callback: use the same cumulative-account flow as the current
    # "Обновить статистику" button, so old Telegram cards cannot reset comparisons to zero.
    cid = int(callback.data.split(":")[1])
    if not await DB.get_client(cid):
        await callback.answer("Клиент не найден", show_alert=True)
        return
    previous = await DB.previous_account_totals(cid)
    await state.clear()
    await state.update_data(
        client_id=cid,
        previous_total_views=previous["total_views"],
        previous_threads_followers=previous["threads_followers"],
        previous_telegram_followers=previous["telegram_followers"],
    )
    await state.set_state(WeeklyAnalyticsFlow.total_views)
    await callback.message.answer(
        "👀 Текущие общие просмотры аккаунта:\n\n"
        f"Предыдущее значение: {previous['total_views']:,}\n"
        "Введите новое число с верхней панели статистики Threads."
    )
    await callback.answer()

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




@router.callback_query(F.data.startswith("client_terms:"))
async def client_terms_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id, router):
        return
    cid = int(callback.data.split(":")[1])
    c = await DB.get_client(cid)
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    price_text = f"{c['service_price']:,}".replace(",", " ") + " ₽/мес." if c["service_price"] else "—"
    await state.clear()
    await state.update_data(client_id=cid)
    await state.set_state(ClientTermsFlow.services)
    await callback.message.answer(
        "<b>Услуги и стоимость</b>\n\n"
        f"Сейчас:\nУслуги: {c['services'] or '—'}\nСтоимость: {price_text}\n\n"
        "Введите новый список услуг:"
    )
    await callback.answer()


@router.message(ClientTermsFlow.services)
async def client_terms_services(message: Message, state: FSMContext):
    services = (message.text or "").strip()
    if len(services) < 3:
        await message.answer("Опишите услуги чуть подробнее.")
        return
    await state.update_data(services=services)
    await state.set_state(ClientTermsFlow.service_price)
    await message.answer("Введите новую стоимость в месяц, например: 30000")


@router.message(ClientTermsFlow.service_price)
async def client_terms_price(message: Message, state: FSMContext):
    raw=(message.text or "").strip().lower().replace(" ","").replace("₽","").replace("рублей","").replace("руб.","").replace("руб","")
    if not raw.isdigit() or int(raw)<=0:
        await message.answer("Введите стоимость числом, например: 30000")
        return
    await state.update_data(service_price=int(raw))
    await state.set_state(ClientTermsFlow.billing_start)
    await message.answer("📅 Введите дату начала первого расчётного периода клиента.\nФормат: ДД.ММ.ГГГГ\nНапример: 15.08.2026")


@router.message(ClientTermsFlow.billing_start)
async def client_terms_billing(message: Message, state: FSMContext):
    try:
        dt=datetime.strptime((message.text or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Введите дату в формате ДД.ММ.ГГГГ, например: 15.08.2026")
        return
    data=await state.get_data(); cid=int(data["client_id"]); iso=dt.isoformat(); price=int(data["service_price"])
    await DB.update_client_terms(cid,data["services"],price,iso)
    await DB.log_event(cid,"client_terms_updated",{"services":data["services"],"service_price":price,"billing_start":iso})
    c=await DB.get_client(cid)
    if c["telegram_id"]:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📑 Открыть документы",callback_data="docs_begin")]])
        try:
            await message.bot.send_message(c["telegram_id"],"📑 <b>Обновлены условия проекта</b>\n\nСформирован новый договор. Ознакомьтесь и подтвердите документы.",reply_markup=kb)
        except Exception:
            import logging; logging.exception("Не удалось отправить документы")
    await state.clear()
    await message.answer("Условия клиента обновлены ✅\n\n"+card_text(c),reply_markup=client_card_kb(c["id"],c["topic_id"],SETTINGS.work_group_id))


@router.callback_query(F.data.startswith("client_contract_preview:"))
async def client_contract_preview(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, router):
        return
    cid = int(callback.data.split(":")[1])
    c = await DB.get_client(cid)
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    if not c["services"] or not c["service_price"] or not c["billing_start"]:
        await callback.answer("Сначала заполните услуги и стоимость", show_alert=True)
        return

    signer = c["legal_name"] or c["name"]
    path = temp_pdf(f"contract_preview_{cid}_")
    try:
        generate_contract_pdf(c, signer, SETTINGS, path, draft=True)
        await callback.message.answer_document(
            __import__("aiogram.types", fromlist=["FSInputFile"]).FSInputFile(path),
            caption="📄 Предварительный просмотр договора"
        )
    finally:
        try:
            __import__("os").remove(path)
        except OSError:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("client_docs_send:"))
async def client_docs_send(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, router):
        return
    cid = int(callback.data.split(":")[1])
    c = await DB.get_client(cid)
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    if not c["telegram_id"]:
        await callback.answer("Клиент ещё не подключён к боту", show_alert=True)
        return
    if not c["services"] or not c["service_price"] or not c["billing_start"]:
        await callback.answer("Сначала заполните услуги и стоимость", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📑 Открыть документы", callback_data="docs_begin")
    ]])
    await callback.bot.send_message(
        c["telegram_id"],
        "📑 Для вас подготовлены документы по проекту. "
        "Откройте их, проверьте условия и подтвердите.",
        reply_markup=kb,
    )
    await topic_log(
        callback.bot, DB, SETTINGS.work_group_id, cid,
        "📨 Документы вручную направлены клиенту на подтверждение."
    )
    await callback.answer("Отправлено ✅")



@router.callback_query(F.data.startswith("client_act:"))
async def client_act_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id, router):
        return

    cid = int(callback.data.split(":")[1])
    c = await DB.get_client(cid)
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return
    if not c["billing_start"]:
        await callback.answer("Не указана дата расчётного периода", show_alert=True)
        return

    completed = latest_completed_period(c["billing_start"])
    if not completed:
        await callback.answer("Первый расчётный период ещё не завершён", show_alert=True)
        return

    start_date, end_date, _ = completed
    await state.clear()
    await state.update_data(
        client_id=cid,
        period_start=start_date.isoformat(),
        period_end=end_date.isoformat(),
    )
    await state.set_state(ActFlow.content)
    await callback.message.answer(
        f"<b>Акт за период {fmt(start_date)}–{fmt(end_date)}</b>\n\n"
        "Напишите, какие услуги фактически указываем в акте.\n\n"
        "Например:\n"
        "1. Подготовка и публикация веток (постов) в Threads.\n"
        "2. Подготовка и предоставление ежемесячной аналитики."
    )
    await callback.answer()


@router.message(ActFlow.content)
async def client_act_content(message: Message, state: FSMContext):
    services_text = (message.text or "").strip()
    if len(services_text) < 10:
        await message.answer("Опишите содержание акта чуть подробнее.")
        return

    data = await state.get_data()
    cid = int(data["client_id"])
    c = await DB.get_client(cid)
    if not c:
        await state.clear()
        await message.answer("Клиент не найден.")
        return

    results = await DB.act_results(cid, data["period_start"], data["period_end"])
    act = await DB.save_service_act(
        cid,
        data["period_start"],
        data["period_end"],
        services_text,
        int(c["service_price"] or 0),
        results,
    )
    await state.clear()

    path = temp_pdf(f"act_preview_{act['id']}_")
    try:
        generate_act_pdf(c, act, SETTINGS, path, signed=False)
        FSInputFile = __import__("aiogram.types", fromlist=["FSInputFile"]).FSInputFile
        await message.answer_document(
            FSInputFile(path),
            caption=(
                f"🧾 <b>Предварительный просмотр акта</b>\n"
                f"Период: {act['period_start']}–{act['period_end']}\n"
                f"Стоимость: {act['amount']:,} ₽"
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📨 Отправить клиенту", callback_data=f"act_send:{act['id']}")],
                [InlineKeyboardButton(text="✏️ Изменить содержание", callback_data=f"act_edit:{act['id']}")],
            ]),
        )
    finally:
        try:
            __import__("os").remove(path)
        except OSError:
            pass


@router.callback_query(F.data.startswith("act_edit:"))
async def act_edit(callback: CallbackQuery, state: FSMContext):
    act_id = int(callback.data.split(":")[1])
    act = await DB.get_service_act(act_id)
    if not act:
        await callback.answer("Акт не найден", show_alert=True)
        return

    await state.clear()
    await state.update_data(
        client_id=act["client_id"],
        period_start=act["period_start"],
        period_end=act["period_end"],
    )
    await state.set_state(ActFlow.content)
    await callback.message.answer(
        "Введите новое содержание акта:\n\n"
        f"Сейчас:\n{act['services_text']}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("act_send:"))
async def act_send(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, router):
        return

    act_id = int(callback.data.split(":")[1])
    act = await DB.get_service_act(act_id)
    if not act:
        await callback.answer("Акт не найден", show_alert=True)
        return
    c = await DB.get_client(act["client_id"])
    if not c or not c["telegram_id"]:
        await callback.answer("Клиент ещё не подключён к боту", show_alert=True)
        return
    if not await DB.documents_fully_accepted(c["id"]):
        await callback.answer("Сначала клиент должен подписать договор", show_alert=True)
        return

    path = temp_pdf(f"act_{act_id}_")
    try:
        generate_act_pdf(c, act, SETTINGS, path, signed=False)
        FSInputFile = __import__("aiogram.types", fromlist=["FSInputFile"]).FSInputFile
        sent = await callback.bot.send_document(
            chat_id=c["telegram_id"],
            document=FSInputFile(path),
            caption=(
                f"🧾 <b>Акт оказанных услуг</b>\n"
                f"Период: {act['period_start']}–{act['period_end']}\n\n"
                "Ознакомьтесь с PDF и подтвердите приёмку услуг."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подписать акт", callback_data=f"act_accept:{act_id}")],
                [InlineKeyboardButton(text="💬 Есть замечания", callback_data=f"act_remark:{act_id}")],
            ]),
        )
        await DB.set_service_act_sent(act_id, sent.document.file_id)
        await DB.log_event(c["id"], "service_act_sent", {"act_id": act_id})
        await topic_log(
            callback.bot, DB, SETTINGS.work_group_id, c["id"],
            f"🧾 Клиенту отправлен акт № {act['act_number']} за период "
            f"{act['period_start']}–{act['period_end']}."
        )
    finally:
        try:
            __import__("os").remove(path)
        except OSError:
            pass

    await callback.answer("Акт отправлен ✅")


@router.callback_query(F.data.startswith("client_docs:"))
async def client_docs_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id, router):
        return
    cid = int(callback.data.split(":")[1])
    c = await DB.get_client(cid)
    if not c:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    consent = await DB.get_client_consent(cid)
    contract_ok = bool(consent and consent["contract_accepted_at"])
    pd_ok = bool(consent and consent["pd_consent_at"])

    status = (
        f"Услуги: {c['services'] or '—'}\n"
        f"Стоимость: {f'{c['service_price']:,}'.replace(',', ' ') + ' ₽/мес.' if c['service_price'] else '—'}\n"
        f"ФИО клиента: {c['legal_name'] or 'клиент ещё не подтвердил'}\n\n"
        f"Договор: {'✅ подписан' if contract_ok else '⏳ не подписан'}\n"
        f"Согласие ПД: {'✅ получено' if pd_ok else '⏳ не получено'}"
    )

    buttons = [
        [InlineKeyboardButton(text="👁 Просмотреть договор", callback_data=f"client_contract_preview:{cid}")],
        [InlineKeyboardButton(text="📨 Отправить на подписание", callback_data=f"client_docs_send:{cid}")],
        [InlineKeyboardButton(text="📄 Загрузить договор вручную", callback_data=f"client_docs_contract:{cid}")],
        [InlineKeyboardButton(text="🔐 Загрузить политику вручную", callback_data=f"client_docs_policy:{cid}")],
    ]
    await callback.message.answer(
        f"<b>Документы клиента</b>\n\n{status}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("client_docs_contract:"))
async def client_docs_contract(callback: CallbackQuery, state: FSMContext):
    cid = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(client_id=cid)
    await state.set_state(ClientDocsFlow.contract)
    await callback.message.answer("Пришлите договор PDF-файлом:")
    await callback.answer()


@router.message(ClientDocsFlow.contract, F.document)
async def client_docs_contract_save(message: Message, state: FSMContext):
    doc = message.document
    if not doc or (doc.mime_type and doc.mime_type != "application/pdf"):
        await message.answer("Нужен PDF-файл договора.")
        return
    data = await state.get_data()
    cid = data["client_id"]
    await DB.set_client_documents(cid, contract_file_id=doc.file_id)
    await DB.log_event(cid, "contract_uploaded")
    await topic_log(message.bot, DB, SETTINGS.work_group_id, cid, "📄 Договор загружен/обновлён. Требуется подтверждение клиента.")
    await state.clear()
    await message.answer("Договор сохранён ✅\nПредыдущее подтверждение клиента, если было, сброшено.", reply_markup=admin_menu())


@router.message(ClientDocsFlow.contract)
async def client_docs_contract_bad(message: Message):
    await message.answer("Пришлите договор именно PDF-файлом.")


@router.callback_query(F.data.startswith("client_docs_policy:"))
async def client_docs_policy(callback: CallbackQuery, state: FSMContext):
    cid = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(client_id=cid)
    await state.set_state(ClientDocsFlow.policy)
    await callback.message.answer("Пришлите политику обработки персональных данных PDF-файлом:")
    await callback.answer()


@router.message(ClientDocsFlow.policy, F.document)
async def client_docs_policy_save(message: Message, state: FSMContext):
    doc = message.document
    if not doc or (doc.mime_type and doc.mime_type != "application/pdf"):
        await message.answer("Нужен PDF-файл политики.")
        return
    data = await state.get_data()
    cid = data["client_id"]
    await DB.set_client_documents(cid, policy_file_id=doc.file_id)
    await DB.log_event(cid, "policy_uploaded")
    await topic_log(message.bot, DB, SETTINGS.work_group_id, cid, "🔐 Политика загружена/обновлена. Требуется подтверждение клиента.")
    await state.clear()
    await message.answer("Политика сохранена ✅\nПредыдущее подтверждение клиента, если было, сброшено.", reply_markup=admin_menu())


@router.message(ClientDocsFlow.policy)
async def client_docs_policy_bad(message: Message):
    await message.answer("Пришлите политику именно PDF-файлом.")
