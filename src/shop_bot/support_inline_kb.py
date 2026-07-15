"""Inline-клавиатуры для тикетов поддержки (общие для webapp и support-бота)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shop_bot.data_manager.remnawave_repository import get_ticket, get_user


def build_admin_dm_reply_kb(ticket_id: int) -> InlineKeyboardMarkup:
    """Кнопки под уведомлением в ЛС админу: ответить + закрыть/переоткрыть."""
    status = "open"
    try:
        t = get_ticket(ticket_id)
        if t:
            status = (t.get("status") or "open").strip().lower()
    except Exception:
        pass
    rows = [
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_reply_dm_{ticket_id}")],
    ]
    if status == "open":
        rows.append([InlineKeyboardButton(text="✅ Закрыть тикет", callback_data=f"admin_close_{ticket_id}")])
    else:
        rows.append([InlineKeyboardButton(text="🔓 Переоткрыть", callback_data=f"admin_reopen_{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_actions_kb(ticket_id: int) -> InlineKeyboardMarkup:
    """Полная панель управления тикетом (для темы в группе)."""
    status = "open"
    user_id = None
    is_banned = False
    try:
        t = get_ticket(ticket_id)
        if t:
            status = (t.get("status") or "open").strip().lower()
            user_id = t.get("user_id")
    except Exception:
        pass
    if user_id:
        try:
            info = get_user(int(user_id)) or {}
            is_banned = bool(info.get("is_banned"))
        except Exception:
            is_banned = False

    first_row = []
    if status == "open":
        first_row.append(InlineKeyboardButton(text="✅ Закрыть", callback_data=f"admin_close_{ticket_id}"))
    else:
        first_row.append(InlineKeyboardButton(text="🔓 Переоткрыть", callback_data=f"admin_reopen_{ticket_id}"))

    rows = [
        first_row,
        [InlineKeyboardButton(text="💬 Ответить", callback_data=f"admin_reply_dm_{ticket_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_delete_{ticket_id}")],
    ]
    if user_id:
        if is_banned:
            rows.append([InlineKeyboardButton(text="✅ Разбанить", callback_data=f"admin_unban_user_{ticket_id}")])
        else:
            rows.append([InlineKeyboardButton(text="🚫 Забанить", callback_data=f"admin_unban_user_{ticket_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
