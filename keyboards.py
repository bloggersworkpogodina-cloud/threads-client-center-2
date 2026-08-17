from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def admin_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👥 Клиенты"), KeyboardButton(text="➕ Добавить клиента")], [KeyboardButton(text="📊 Аналитика"), KeyboardButton(text="🗂 Архив")], [KeyboardButton(text="📣 Сообщение всем")]], resize_keyboard=True)


def client_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📅 Ветки"), KeyboardButton(text="📄 Контент-план")], [KeyboardButton(text="📊 Мои результаты"), KeyboardButton(text="💬 Связь с менеджером")]], resize_keyboard=True)


def confirm_client_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Создать", callback_data="client_confirm_create")], [InlineKeyboardButton(text="✏️ Изменить", callback_data="client_confirm_edit"), InlineKeyboardButton(text="❌ Отмена", callback_data="client_confirm_cancel")]])


def client_card_kb(client_id: int, topic_id: int | None = None, work_group_id: int | None = None):
    topic_button = InlineKeyboardButton(text="🧵 Тема", callback_data=f"client_topic:{client_id}")

    # Если тема уже создана — кнопка сразу открывает нужную тему Telegram.
    # Для приватных supergroup-ссылок Telegram использует формат:
    # https://t.me/c/<internal_chat_id>/<message_thread_id>
    if topic_id and work_group_id:
        group_str = str(abs(int(work_group_id)))
        if group_str.startswith("100"):
            group_str = group_str[3:]
        topic_button = InlineKeyboardButton(
            text="🧵 Тема",
            url=f"https://t.me/c/{group_str}/{int(topic_id)}",
        )

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Контент-план", callback_data=f"client_sheet:{client_id}"), InlineKeyboardButton(text="📤 Отправить ветки", callback_data=f"client_send_posts:{client_id}")],
        [InlineKeyboardButton(text="🔗 Invite", callback_data=f"client_invite:{client_id}"), topic_button],
        [InlineKeyboardButton(text="🚀 Старт проекта", callback_data=f"baseline_start:{client_id}"), InlineKeyboardButton(text="📈 Обновить статистику", callback_data=f"weekly_analytics:{client_id}")],
        [InlineKeyboardButton(text="📊 История роста", callback_data=f"client_analytics:{client_id}"), InlineKeyboardButton(text="🖼 Скрины клиента", callback_data=f"client_screens:{client_id}")],
        [InlineKeyboardButton(text="📤 Отправить аналитику", callback_data=f"client_send_analytics:{client_id}")],
        [InlineKeyboardButton(text="📑 Документы", callback_data=f"client_docs:{client_id}"), InlineKeyboardButton(text="🧾 Акт за месяц", callback_data=f"client_act:{client_id}")],
        [InlineKeyboardButton(text="💼 Услуги и стоимость", callback_data=f"client_terms:{client_id}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"client_view:{client_id}"), InlineKeyboardButton(text="🔴 Закрыть", callback_data=f"client_archive:{client_id}")],
    ])


def publication_kb(day: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Все опубликованы", callback_data=f"pub:all:{day}")], [InlineKeyboardButton(text="🟡 Опубликована часть", callback_data=f"pub:partial:{day}"), InlineKeyboardButton(text="❌ Не опубликованы", callback_data=f"pub:none:{day}")]])


def skip_photo_kb(callback_data: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏭ Пропустить", callback_data=callback_data)]])

def analytics_clients_kb(clients, prefix: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=c["name"], callback_data=f"{prefix}:{c['id']}")] for c in clients])


def docs_begin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📑 Перейти к документам", callback_data="docs_begin")]
    ])


def contract_accept_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Подписать договор", callback_data="contract_accept")]
    ])


def pd_consent_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Даю согласие на обработку персональных данных", callback_data="pd_consent_accept")]
    ])
