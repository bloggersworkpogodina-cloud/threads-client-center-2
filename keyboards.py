from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def admin_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="👥 Клиенты"), KeyboardButton(text="➕ Добавить клиента")], [KeyboardButton(text="📊 Аналитика"), KeyboardButton(text="🗂 Архив")]], resize_keyboard=True)


def client_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📅 Ветки"), KeyboardButton(text="📄 Контент-план")], [KeyboardButton(text="📊 Мои результаты"), KeyboardButton(text="💬 Связь с менеджером")]], resize_keyboard=True)


def confirm_client_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Создать", callback_data="client_confirm_create")], [InlineKeyboardButton(text="✏️ Изменить", callback_data="client_confirm_edit"), InlineKeyboardButton(text="❌ Отмена", callback_data="client_confirm_cancel")]])


def client_card_kb(client_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Таблица", callback_data=f"client_sheet:{client_id}"), InlineKeyboardButton(text="📄 Контент-план", callback_data=f"client_plan:{client_id}")],
        [InlineKeyboardButton(text="📤 Отправить ветки", callback_data=f"client_send_posts:{client_id}")],
        [InlineKeyboardButton(text="🔗 Invite", callback_data=f"client_invite:{client_id}"), InlineKeyboardButton(text="🧵 Тема", callback_data=f"client_topic:{client_id}")],
        [InlineKeyboardButton(text="📈 Внести статистику", callback_data=f"weekly_stats:{client_id}")],
        [InlineKeyboardButton(text="📊 Аналитика клиента", callback_data=f"client_analytics:{client_id}")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"client_view:{client_id}"), InlineKeyboardButton(text="🔴 Закрыть", callback_data=f"client_archive:{client_id}")],
    ])


def publication_kb(day: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Все опубликованы", callback_data=f"pub:all:{day}")], [InlineKeyboardButton(text="🟡 Опубликована часть", callback_data=f"pub:partial:{day}"), InlineKeyboardButton(text="❌ Не опубликованы", callback_data=f"pub:none:{day}")]])
