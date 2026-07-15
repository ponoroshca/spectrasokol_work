import logging
from aiogram import Bot, Router, F, types, html
from aiogram.types import FSInputFile
import os
import time
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

from shop_bot.data_manager.remnawave_repository import (
    get_setting,
    create_support_ticket,
    add_support_message,
    get_user_tickets,
    get_ticket,
    get_ticket_messages,
    set_ticket_status,
    set_ticket_open_feed_msg_id,
    update_ticket_thread_info,
    get_ticket_by_thread,
    get_or_create_open_ticket,
    update_ticket_subject,
    delete_ticket,
    is_admin,
    get_admin_ids,
    get_user,
    ban_user,
    unban_user,
)
from shop_bot.data_manager.notifications import get_notif_text, notif_enabled
from shop_bot.bot import keyboards

logger = logging.getLogger(__name__)

NEW_TICKET_PHOTO_URL = "https://github.com/CyberERROR/remnawave-shopbot/blob/main/docs/screenshots/suppshor.png?raw=true"

class SupportDialog(StatesGroup):
    waiting_for_subject = State()
    waiting_for_message = State()
    waiting_for_reply = State()
    faq_shown = State()  # показан FAQ-перехват (самопомощь), ждём «оператора» или текста


class AdminDialog(StatesGroup):
    waiting_for_note = State()
    waiting_for_reply = State()


def _get_username_display(user, user_id: int = None) -> str:
    if hasattr(user, 'username') and user.username:
        return f"@{user.username}"
    if hasattr(user, 'full_name') and user.full_name:
        return user.full_name
    return str(user_id if user_id else (user.id if hasattr(user, 'id') else 'Unknown'))


def _parse_star_subject(subject: str) -> tuple[bool, str]:
    if not subject:
        return False, 'Обращение без темы'
    is_star = subject.strip().startswith('⭐')
    display_subj = subject.lstrip('⭐️ ').strip() if is_star else subject
    return is_star, display_subj or 'Обращение без темы'


def _build_topic_name(ticket_id: int, subject: str, author_tag: str) -> str:
    is_star, display_subj = _parse_star_subject(subject)
    trimmed = display_subj[:40]
    important_prefix = '🔴 Важно: ' if is_star else ''
    return f"#{ticket_id} {important_prefix}{trimmed} • от {author_tag}"


def _get_author_tag(message: types.Message) -> str:
    if not message.from_user:
        return 'пользователь'
    return _get_username_display(message.from_user, message.from_user.id)


def _build_notification_text(ticket_id: int, user_id: int, username_display: str, subject: str, message_content: str, created_new: bool) -> str:
    subj_display = subject or "—"
    header = "🆘 <b>Новое обращение:</b>\n\n" if created_new else "✅ <b>Сообщение добавлено в тикет</b>\n\n"
    return (
        f"{header}"
        f"👤 <b>USER:</b> (<code>{user_id}</code> - {html.quote(username_display)})\n"
        f"📝 <b>ID тикета:</b> <code>#{ticket_id}</code>\n"
        f"💬 <b>Тема:</b> <i>{html.quote(subj_display)}</i>\n\n"
        f"💌 Сообщения:\n"
        f"<blockquote>{html.quote(message_content)}</blockquote>"
    )


def get_support_router() -> Router:
    router = Router()

    # ==========================================
    # 1) UNIVERSAL MESSAGES (CONSTANTS)
    # ==========================================
    TXT_TICKET_NOT_FOUND = "❌ <b>Тикет не найден.</b>"
    TXT_ACCESS_DENIED = "❌ <b>Тикет не найден или доступ запрещён.</b>"
    TXT_CANNOT_REPLY = "❌ <b>Нельзя ответить на этот тикет.</b>"
    TXT_ALREADY_CLOSED = "🔒 <b>Тикет уже закрыт.</b>"
    TXT_ALREADY_OPEN = "⚠️ <b>Тикет уже открыт.</b>"
    TXT_BAN_RESTRICTED = "<b>🚫 Доступ ограничен</b>\n\nВаш аккаунт заблокирован. Вы не можете обращаться в поддержку."
    TXT_BAN_ERROR = "❌ Не удалось изменить статус блокировки: {}"
    
    # ==========================================
    # 2) UNIVERSAL FUNCTIONS (HELPERS)
    # ==========================================
    
    async def _safe_edit(call: types.CallbackQuery, text: str, reply_markup=None):
        try:
            await call.message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            pass

    def _extract_content(message: types.Message) -> str:
        text = (message.text or message.caption or "").strip()
        if message.photo: return f"[Фото] {text}".strip()
        if message.video: return f"[Видео] {text}".strip()
        return text

    def _support_contact_markup() -> types.InlineKeyboardMarkup | None:
        support = (get_setting("support_bot_username") or get_setting("support_user") or "").strip()
        if not support:
            return None
        url: str | None = None
        if support.startswith("@"):
            url = f"tg://resolve?domain={support[1:]}"
        elif support.startswith("tg://"):
            url = support
        elif support.startswith("http://") or support.startswith("https://"):
            try:
                part = support.split("/")[-1].split("?")[0]
                if part:
                    url = f"tg://resolve?domain={part}"
                else:
                    url = support
            except Exception:
                url = support
        else:
            url = f"tg://resolve?domain={support}"
        if not url:
            return None
        return types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🆘 Написать в поддержку", url=url)]])

    def _user_main_reply_kb() -> types.ReplyKeyboardMarkup:
        return types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="✍️ Новое обращение")],
                [types.KeyboardButton(text="📨 Мои обращения")],
            ],
            resize_keyboard=True
        )

    def _admin_kb_build(status, ticket_id, user_id, is_banned) -> types.InlineKeyboardMarkup:
        first_row: list[types.InlineKeyboardButton] = []
        if status == 'open':
            first_row.append(types.InlineKeyboardButton(text="✅ Закрыть", callback_data=f"admin_close_{ticket_id}"))
        else:
            first_row.append(types.InlineKeyboardButton(text="🔓 Переоткрыть", callback_data=f"admin_reopen_{ticket_id}"))
        inline_kb = [
            first_row,
            [types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_delete_{ticket_id}")],
            [
                types.InlineKeyboardButton(text="⭐ Важно", callback_data=f"admin_star_{ticket_id}"),
                types.InlineKeyboardButton(text="👤 Пользователь", callback_data=f"admin_user_{ticket_id}"),
                types.InlineKeyboardButton(text="📝 Заметка", callback_data=f"admin_note_{ticket_id}"),
            ],
            [types.InlineKeyboardButton(text="🗒 Заметки", callback_data=f"admin_notes_{ticket_id}")],
        ]
        if user_id:
            if is_banned:
                inline_kb.append([
                    types.InlineKeyboardButton(text="✅ Разбанить", callback_data=f"admin_unban_user_{ticket_id}")
                ])
            else:
                inline_kb.append([
                    types.InlineKeyboardButton(text="🚫 Забанить", callback_data=f"admin_ban_user_{ticket_id}")
                ])
        return types.InlineKeyboardMarkup(inline_keyboard=inline_kb)

    def _admin_actions_kb(ticket_id: int) -> types.InlineKeyboardMarkup:
        try:
            t = get_ticket(ticket_id)
            status = (t and t.get('status')) or 'open'
        except Exception:
            status = 'open'
        try:
            user_id = int((t or {}).get('user_id')) if t else None
        except Exception:
            user_id = None
        is_banned = None
        if user_id:
            try:
                user_info = get_user(user_id) or {}
                is_banned = bool(user_info.get('is_banned'))
            except Exception:
                is_banned = None
        return _admin_kb_build(status, ticket_id, user_id, is_banned)

    def _admin_dm_reply_kb(ticket_id: int) -> types.InlineKeyboardMarkup:
        from shop_bot.support_inline_kb import build_admin_dm_reply_kb
        return build_admin_dm_reply_kb(ticket_id)

    def _is_user_banned(user_id: int) -> bool:
        if not user_id:
            return False
        try:
            user = get_user(int(user_id)) or {}
        except Exception:
            return False
        return bool(user.get('is_banned'))
    
    async def _check_banned(event: types.Message | types.CallbackQuery, state: FSMContext = None) -> bool:
        user_id = event.from_user.id
        if not _is_user_banned(user_id):
            return False
        
        markup = _support_contact_markup()
        if isinstance(event, types.CallbackQuery):
             try:
                 await event.answer(TXT_BAN_RESTRICTED, show_alert=True)
             except Exception:
                 pass
        else:
             if markup:
                 await event.answer(TXT_BAN_RESTRICTED, reply_markup=markup)
             else:
                 await event.answer(TXT_BAN_RESTRICTED)
        
        if state:
            await state.clear()
        return True

    def _get_latest_open_ticket(user_id: int) -> dict | None:
        try:
            tickets = get_user_tickets(user_id) or []
            open_tickets = [t for t in tickets if t.get('status') == 'open']
            if not open_tickets:
                return None
            return max(open_tickets, key=lambda t: int(t['ticket_id']))
        except Exception:
            return None

    async def _check_active_ticket(message: types.Message | types.CallbackQuery, user_id: int) -> bool:
        existing = _get_latest_open_ticket(user_id)
        if existing:
            tid = int(existing["ticket_id"])
            text = (
                f"У вас уже есть открытое обращение <b>#{tid}</b>.\n\n"
                f"Просто напишите сообщение в этот чат — оно попадёт в тикет.\n"
                f"Или закройте обращение, если вопрос решён."
            )
            kb = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✅ Закрыть обращение", callback_data=f"support_close_{tid}")],
            ])
            if isinstance(message, types.CallbackQuery):
                await message.message.edit_text(text, reply_markup=kb)
            else:
                await message.answer(text, reply_markup=kb)
            return True
        return False

    def _cancel_creation_kb() -> types.InlineKeyboardMarkup:
        return types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отменить создание", callback_data="support_cancel_creation")]
        ])

    async def _send_subject_prompt(message: types.Message | types.CallbackQuery, state: FSMContext):
        current_state = await state.get_state()
        if current_state in [SupportDialog.waiting_for_subject, SupportDialog.waiting_for_message]:
            text = "⚠️ <b>Вы уже создаете обращение.</b>\nПожалуйста, завершите текущий процесс или отмените его."
            if isinstance(message, types.CallbackQuery):
                await message.answer(text, show_alert=True)
            else:
                await message.answer(text, reply_markup=_cancel_creation_kb())
            return

        text = (
            "✉️ <b>Опишите проблему одним сообщением</b>\n\n"
            "Можно приложить фото или скриншот.\n"
            "<i>Например: «Оплатил, но ключ не пришёл»</i>"
        )
        if isinstance(message, types.CallbackQuery):
            await message.message.edit_text(text, reply_markup=_cancel_creation_kb())
        else:
            await message.answer(text, reply_markup=_cancel_creation_kb())
        await state.update_data(start_time=time.time())
        await state.set_state(SupportDialog.waiting_for_message)
    
    async def _send_user_tickets_list(event: types.Message | types.CallbackQuery, user_id: int):
        tickets = get_user_tickets(user_id)
        text = "<b>📨 Ваши обращения:</b>" if tickets else "<b>📂 У вас пока нет обращений.</b>"
        rows = []
        if tickets:
            for t in tickets:
                status_text = "🟢 Открыт" if t.get('status') == 'open' else "🔒 Закрыт"
                is_star = (t.get('subject') or '').startswith('⭐ ')
                star = '⭐ ' if is_star else ''
                title = f"{star}#{t['ticket_id']} • {status_text}"
                if t.get('subject'):
                    title += f" • {t['subject'][:20]}"
                rows.append([types.InlineKeyboardButton(text=title, callback_data=f"support_view_{t['ticket_id']}")])
        
        reply_markup = types.InlineKeyboardMarkup(inline_keyboard=rows)
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(text, reply_markup=reply_markup)
        else:
            await event.answer(text, reply_markup=reply_markup)

    async def _send_ticket_confirmation(message: types.Message, ticket_id: int, subject: str, content_text: str, created_new: bool):
        if not created_new:
            # Дальнейшие сообщения в открытый тикет — без простыней текста
            try:
                await message.react([types.ReactionTypeEmoji(emoji="✅")])
            except Exception:
                pass
            return
        subj = html.quote(subject or "—")
        text = (
            f"✅ <b>Обращение #{ticket_id} принято</b>\n\n"
            f"Тема: <i>{subj}</i>\n"
            f"Ответ поддержки придёт сюда в этот чат."
        )
        try:
            await message.answer(text, reply_markup=_user_main_reply_kb())
        except Exception:
            pass

    async def _send_ticket_closed_notification(bot: Bot, user_id: int, ticket_id: int, is_user_action: bool = False, message_obj: types.Message = None):
        text = (
            f"✅ <b>Ваш тикет #{ticket_id} был закрыт</b>\n\n"
            f"✉️ <i>Если у вас появятся другие вопросы или ваш вопрос не решен</i>\n\n"
            f"💌 <b>Вы можете создать новое обращение при необходимости.</b>"
        )
        
        try:
             if is_user_action and message_obj:
                 await message_obj.edit_text(
                     text,
                     reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ К списку", callback_data="support_my_tickets")]])
                 )
             else:
                 await bot.send_message(chat_id=user_id, text=text)
        except Exception:
             pass

    async def _send_admin_reply_to_user(bot: Bot, user_id: int, ticket_id: int, message: types.Message, content: str):
        header = f"👨‍💻 <b>Поддержка</b> · #{ticket_id}\n\n"
        try:
            if message.text and not (message.photo or message.video or message.document):
                await bot.send_message(chat_id=user_id, text=header + (content or ""))
            else:
                await bot.send_message(chat_id=user_id, text=header.rstrip())
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
        except Exception as e:
            logger.warning(f"Failed to send reply to user {user_id}: {e}")
            raise e

    async def _notify_admins(bot: Bot, message: types.Message, ticket_id: int, subject: str = None, created_new: bool = False):
        from shop_bot.support_user_card import send_support_user_card

        username_display = _get_username_display(message.from_user, message.from_user.id)
        message_content = message.text or message.caption or ("[Фото]" if message.photo else "[Видео]" if message.video else "")
        notification_text = _build_notification_text(ticket_id, message.from_user.id, username_display, subject, message_content, created_new)

        for aid in get_admin_ids():
            try:
                if message.text or (message.caption and not created_new):
                    send_method = bot.send_photo if created_new else bot.send_message
                    
                    photo_to_send = NEW_TICKET_PHOTO_URL
                    if created_new and message.photo:
                        photo_to_send = message.photo[-1].file_id

                    await send_method(
                        chat_id=int(aid),
                        **(({"photo": photo_to_send, "caption": notification_text} if created_new else {"text": notification_text})),
                        reply_markup=_admin_dm_reply_kb(ticket_id)
                    )
                else:
                    if created_new:
                        photo_to_send = NEW_TICKET_PHOTO_URL
                        if message.photo:
                            photo_to_send = message.photo[-1].file_id
                            
                        await bot.send_photo(
                            chat_id=int(aid),
                            photo=photo_to_send,
                            caption=notification_text,
                            reply_markup=_admin_dm_reply_kb(ticket_id)
                        )
                    else:
                        await bot.copy_message(
                            chat_id=int(aid),
                            from_chat_id=message.chat.id,
                            message_id=message.message_id,
                            caption=notification_text,
                            reply_markup=_admin_dm_reply_kb(ticket_id)
                        )
            except Exception as e:
                logger.warning(f"Не удалось уведомить админа {aid} о тикете {ticket_id}: {e}")
            if created_new:
                try:
                    await send_support_user_card(
                        bot,
                        int(aid),
                        message.from_user.id,
                        ticket_id=ticket_id,
                        tg_user=message.from_user,
                    )
                except Exception as e:
                    logger.warning(f"Не удалось отправить карточку клиента админу {aid}: {e}")

    async def _notify_user_about_ban(bot: Bot, user_id: int, text: str) -> None:
        try:
            markup = _support_contact_markup()
            if markup:
                await bot.send_message(user_id, text, reply_markup=markup)
            else:
                await bot.send_message(user_id, text)
        except Exception:
            pass

    async def _ensure_forum_topic(bot: Bot, ticket_id: int, subject: str, message_from: types.User) -> tuple[int | None, int | None]:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return None, None
            
        forum_chat_id = ticket.get('forum_chat_id')
        thread_id = ticket.get('message_thread_id')
        support_forum_chat_id = get_setting("support_forum_chat_id")
        
        if support_forum_chat_id and not (forum_chat_id and thread_id):
            try:
                chat_id = int(support_forum_chat_id)
                author_tag = _get_username_display(message_from, message_from.id)
                topic_name = _build_topic_name(ticket_id, subject or 'Обращение без темы', author_tag)
                
                forum_topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_name)
                thread_id = forum_topic.message_thread_id
                forum_chat_id = chat_id
                update_ticket_thread_info(ticket_id, str(chat_id), int(thread_id))
                return int(forum_chat_id), int(thread_id)
            except Exception as e:
                error_msg = str(e).lower()
                if 'not a forum' in error_msg or 'chat_not_found' in error_msg:
                    logger.debug(f"Форум не настроен: {error_msg}")
                else:
                    logger.warning(f"Не удалось создать тему форума: {e}")
                return None, None

        if forum_chat_id and thread_id:
            try:
                author_tag = _get_username_display(message_from, message_from.id)
                topic_name = _build_topic_name(ticket_id, subject or 'Обращение без темы', author_tag)
                await bot.edit_forum_topic(chat_id=int(forum_chat_id), message_thread_id=int(thread_id), name=topic_name)
                return int(forum_chat_id), int(thread_id)
            except Exception as e:
                logger.warning(f"Не удалось переименовать тему форума: {e}")
                return int(forum_chat_id), int(thread_id)
        
        return None, None

    async def _mirror_to_forum(bot: Bot, message: types.Message, ticket_id: int, forum_chat_id: int, thread_id: int, subject: str = None, created_new: bool = False):
        try:
            from shop_bot.support_user_card import send_support_user_card

            if created_new:
                await send_support_user_card(
                    bot,
                    int(forum_chat_id),
                    message.from_user.id,
                    message_thread_id=int(thread_id),
                    ticket_id=ticket_id,
                    tg_user=message.from_user,
                    pin=True,
                )
                subj = html.quote(subject or "—")
                await bot.send_message(
                    chat_id=int(forum_chat_id),
                    message_thread_id=int(thread_id),
                    text=f"🆕 <b>Обращение #{ticket_id}</b>\n💬 Тема: <i>{subj}</i>",
                    parse_mode="HTML",
                )
                await bot.send_message(
                    chat_id=int(forum_chat_id),
                    message_thread_id=int(thread_id),
                    text="Панель управления тикетом:",
                    reply_markup=_admin_actions_kb(ticket_id),
                )
            await bot.copy_message(
                chat_id=int(forum_chat_id),
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                message_thread_id=int(thread_id),
            )
        except Exception as e:
            logger.warning(f"Не удалось отзеркалить сообщение пользователя в форум: {e}")

    async def _manage_forum_topic(bot: Bot, ticket: dict, action: str):
        """Action: close, reopen, delete"""
        chat_id = ticket.get('forum_chat_id')
        tid = ticket.get('message_thread_id')
        if not (chat_id and tid): return
        try:
            if action == 'close': await bot.close_forum_topic(chat_id=int(chat_id), message_thread_id=int(tid))
            elif action == 'reopen': await bot.reopen_forum_topic(chat_id=int(chat_id), message_thread_id=int(tid))
            elif action == 'delete': await bot.delete_forum_topic(chat_id=int(chat_id), message_thread_id=int(tid))
        except Exception:
            pass

    async def _post_open_feed_card(bot: Bot, ticket_id: int, subject: str, forum_chat_id, thread_id):
        """Моментально кладёт карточку тикета в топик «🟢 Открытые» (без ожидания планировщика)."""
        try:
            open_topic = (get_setting("support_open_topic_id") or "").strip()
            if not open_topic:
                return
            internal = str(forum_chat_id).replace("-100", "", 1)
            # 3-сегментный формат t.me/c/<internal>/<topic_id>/<msg_id> — надёжно открывает ТЕМУ форума.
            # 2-сегментный (.../<topic_id>) Telegram трактует как сообщение в General → открывал главную группы.
            link = f"https://t.me/c/{internal}/{int(thread_id)}/{int(thread_id)}"
            txt = (f"🟢 <b>Тикет #{ticket_id}</b> — {(subject or 'без темы')[:60]}\n"
                   f"<a href=\"{link}\">Открыть переписку →</a>")
            sent = await bot.send_message(chat_id=int(forum_chat_id), message_thread_id=int(open_topic),
                                          text=txt, disable_web_page_preview=True)
            set_ticket_open_feed_msg_id(ticket_id, sent.message_id)
        except Exception as e:
            logger.warning("support: карточка тикета #%s в ленту не отправлена: %s", ticket_id, e)

    async def _process_ticket_message_flow(bot: Bot, message: types.Message, state: FSMContext, ticket_id: int, subject: str, created_new: bool):
        content = _extract_content(message)
        add_support_message(ticket_id, sender="user", content=content)
        
        forum_chat_id, thread_id = await _ensure_forum_topic(bot, ticket_id, subject, message.from_user)
        if forum_chat_id and thread_id:
             await _mirror_to_forum(bot, message, ticket_id, forum_chat_id, thread_id, subject=subject, created_new=created_new)
             if created_new:
                 await _post_open_feed_card(bot, ticket_id, subject, forum_chat_id, thread_id)

        await _send_ticket_confirmation(message, ticket_id, subject, content, created_new)
        await _notify_admins(bot, message, ticket_id, subject=subject, created_new=created_new)
        if state: await state.clear()

    async def _change_ticket_status_common(bot: Bot, call: types.CallbackQuery, ticket_id: int, new_status: str, is_admin: bool):
        action_name = "закрыть" if new_status == 'closed' else "переоткрыть"
        ticket = get_ticket(ticket_id)
        if not ticket:
             if is_admin: return 
             await _safe_edit(call, TXT_ACCESS_DENIED)
             return

        if not is_admin:
            if ticket.get('user_id') != call.from_user.id:
                await _safe_edit(call, TXT_ACCESS_DENIED)
                return
            if ticket.get('status') == new_status:
                await _safe_edit(call, TXT_ALREADY_CLOSED if new_status == 'closed' else TXT_ALREADY_OPEN)
                return

        if set_ticket_status(ticket_id, new_status):
            await _manage_forum_topic(bot, ticket, 'close' if new_status == 'closed' else 'reopen')
            if is_admin:
                 user_id = int(ticket.get('user_id'))
                 await _send_ticket_closed_notification(bot, user_id, ticket_id, is_user_action=False)
                 status_text = f"✅ <b>Тикет #{ticket_id} закрыт.</b>" if new_status == 'closed' else f"🔓 <b>Тикет #{ticket_id} переоткрыт.</b>"
                 try:
                    await call.message.edit_text(status_text, reply_markup=_admin_actions_kb(ticket_id))
                 except Exception:
                    await call.answer("Без изменений")
            else:
                 username = _get_username_display(call.from_user, call.from_user.id)
                 try:
                    if ticket.get('forum_chat_id') and ticket.get('message_thread_id'):
                        await bot.send_message(chat_id=int(ticket['forum_chat_id']), text=f"✅ Пользователь {username} закрыл тикет #{ticket_id}.", message_thread_id=int(ticket['message_thread_id']))
                        await bot.send_message(chat_id=int(ticket['forum_chat_id']), text="Панель управления тикетом:", message_thread_id=int(ticket['message_thread_id']), reply_markup=_admin_actions_kb(ticket_id))
                 except Exception: pass
                 
                 await _send_ticket_closed_notification(bot, call.from_user.id, ticket_id, is_user_action=True, message_obj=call.message)
                 try: await call.message.answer("Меню поддержки:", reply_markup=_user_main_reply_kb())
                 except Exception: pass
        else:
            if is_admin: await call.message.answer(f"❌ Не удалось {action_name} тикет.")
            else: await call.message.edit_text(f"<b>❌ Ошибка</b>\nНе удалось {action_name} тикет.")

    async def _get_ticket_and_check_admin(callback: types.CallbackQuery, bot: Bot) -> tuple[dict | None, int | None]:
        try:
            ticket_id = int(callback.data.split("_")[-1])
        except Exception:
            return None, None
        
        ticket = get_ticket(ticket_id)
        if not ticket:
            await _safe_edit(callback, TXT_TICKET_NOT_FOUND)
            return None, None
            
        forum_chat_id = int(ticket.get('forum_chat_id') or callback.message.chat.id)
        
        is_admin_by_setting = is_admin(callback.from_user.id)
        is_admin_in_chat = False
        try:
            member = await bot.get_chat_member(chat_id=forum_chat_id, user_id=callback.from_user.id)
            is_admin_in_chat = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
        except Exception:
            pass
            
        if not (is_admin_by_setting or is_admin_in_chat):
             return None, None
             
        return ticket, ticket_id

    async def _start_ticket_creation_flow(event: types.Message | types.CallbackQuery, state: FSMContext):
         if isinstance(event, types.CallbackQuery):
             await event.answer()
         if await _check_banned(event, state):
             return
         if await _check_active_ticket(event, event.from_user.id):
             return
         # Сначала FAQ-самопомощь (видео), потом оператор. Вошёл по кнопке — вопрос ещё не написан.
         await state.set_state(SupportDialog.faq_shown)
         await state.update_data(pending_question="", start_time=time.time())
         intro = (get_setting("support_faq_intro") or "❗️ <b>Актуальные вопросы</b> ❗️\\n\\nВозможно, ответ уже есть в коротком видео 👇\\nНе нашли? Нажмите «🆘 Позвать оператора».").replace("\\n", "\n")
         msg = event.message if isinstance(event, types.CallbackQuery) else event
         await msg.answer(intro, reply_markup=_build_faq_kb())

    def _get_faq_items() -> list:
        """Список FAQ [{title,url}] из настройки support_faq_json (редактируется в админке)."""
        import json as _json
        try:
            data = _json.loads(get_setting("support_faq_json") or "[]")
            return [{"title": str(i.get("title", "")).strip(), "url": str(i.get("url", "")).strip()}
                    for i in data if isinstance(i, dict) and i.get("title") and i.get("url")]
        except Exception:
            return []

    def _build_faq_kb():
        """Inline-меню: видео-вопросы по 2 в ряд (компактно на мобиле) + сайт + оператор."""
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.types import InlineKeyboardButton
        b = InlineKeyboardBuilder()
        for it in _get_faq_items():
            b.button(text="▶️ " + it["title"], url=it["url"])
        b.adjust(1)
        channel = (get_setting("channel_url") or "https://t.me/Info_Alma").strip()
        if not channel.startswith("http"):
            channel = "https://t.me/" + channel.lstrip("@")
        b.row(InlineKeyboardButton(text="🎥 Все видео инструкции", url=channel))
        _site = (get_setting("webapp_domen") or get_setting("domain") or "").strip()
        if _site:
            if not _site.startswith("http"): _site = "https://" + _site
            b.row(InlineKeyboardButton(text="🌐 Сайт (оплата без Telegram)", url=_site))
        b.row(InlineKeyboardButton(text="🆘 Позвать оператора", callback_data="support_call_operator"))
        return b.as_markup()

    @router.message(CommandStart(), F.chat.type == "private")
    async def start_handler(message: types.Message, state: FSMContext, bot: Bot):
        args = (message.text or "").split(maxsplit=1)
        arg = None
        if len(args) > 1:
            arg = args[1].strip()
        if arg == "new":
            await _start_ticket_creation_flow(message, state)
            return
        if await _check_banned(message, state):
            return

        support_text = get_setting("support_text") or (
            "<b>👨‍💻 Поддержка Alma</b>\n\n"
            "• <b>✍️ Новое обращение</b> — создать тикет\n"
            "• Напишите в чат — дополните открытый тикет\n"
            "• <b>📨 Мои обращения</b> — история и закрытие"
        )
        try:
            await message.answer(support_text, reply_markup=_user_main_reply_kb())
            # Сразу показываем FAQ-самопомощь (видео), если нет открытого тикета.
            if not _get_latest_open_ticket(message.from_user.id):
                await state.set_state(SupportDialog.faq_shown)
                await state.update_data(pending_question="", start_time=time.time())
                intro = (get_setting("support_faq_intro") or "❗️ <b>Актуальные вопросы</b> ❗️\\n\\nВозможно, ответ уже есть в коротком видео 👇\\nНе нашли? Нажмите «🆘 Позвать оператора».").replace("\\n", "\n")
                await message.answer(intro, reply_markup=_build_faq_kb())
        except Exception as e:
            logger.debug(f"Не удалось отправить приветственное сообщение: {e}")

    @router.callback_query(F.data == "support_new_ticket")
    async def support_new_ticket_handler(callback: types.CallbackQuery, state: FSMContext):
        await _start_ticket_creation_flow(callback, state)

    @router.callback_query(F.data == "support_call_operator")
    async def support_call_operator_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        # Кнопка «Позвать оператора» из FAQ-меню: создаём тикет из УЖЕ написанного вопроса (без повтора ввода).
        await callback.answer()
        if await _check_banned(callback, state):
            return
        data = await state.get_data()
        q = (data.get("pending_question") or "").strip()
        if not q:
            # Вошёл по кнопке «Новое обращение», вопрос ещё не написан → просим описать (обычный flow).
            await _send_subject_prompt(callback, state)
            return
        await state.clear()
        user_id = callback.from_user.id
        subject = q[:40]
        ticket_id, created_new = get_or_create_open_ticket(user_id, subject)
        if not ticket_id:
            await callback.message.answer("❌ Не удалось создать обращение. Попробуйте позже.")
            return
        content = q if q else "(пользователь вызвал оператора)"
        add_support_message(ticket_id, sender="user", content=content)
        forum_chat_id, thread_id = await _ensure_forum_topic(bot, ticket_id, subject, callback.from_user)
        if forum_chat_id and thread_id:
            try:
                author = _get_username_display(callback.from_user, user_id)
                await bot.send_message(int(forum_chat_id), f"🆘 <b>{author}</b> вызвал оператора:\n\n{html.quote(content)}", message_thread_id=int(thread_id))
            except Exception:
                pass
            if created_new:
                await _post_open_feed_card(bot, ticket_id, subject, forum_chat_id, thread_id)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await callback.message.answer(f"✅ Обращение <b>#{ticket_id}</b> создано. Оператор скоро ответит.", reply_markup=_user_main_reply_kb())

    @router.message(SupportDialog.faq_shown, F.chat.type == "private")
    async def faq_shown_message_handler(message: types.Message, state: FSMContext, bot: Bot):
        # Юзер пишет ещё (не нажав кнопку) — создаём тикет из этого сообщения (обычный flow).
        if await _check_banned(message, state):
            return
        text_content = (message.text or "").strip()
        if text_content == "📨 Мои обращения":
            await state.clear()
            await _send_user_tickets_list(message, message.from_user.id)
            return
        if text_content in ["✍️ Новое обращение", "▶️ Начать"]:
            await state.clear()
            await _start_ticket_creation_flow(message, state)
            return
        _src = text_content or (message.caption or "").strip()
        subject = _src[:40] if _src else "Обращение"
        ticket_id, created_new = get_or_create_open_ticket(message.from_user.id, subject)
        if not ticket_id:
            await message.answer("❌ Не удалось создать обращение.")
            await state.clear()
            return
        await _process_ticket_message_flow(bot, message, state, ticket_id, subject, created_new)

    @router.callback_query(F.data == "support_cancel_creation")
    async def support_cancel_creation_handler(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.answer("✅ Создание обращения отменено.")
        
        support_text = get_setting("support_text") or (
            "<b>👨‍💻 Поддержка Alma</b>\n\n"
            "• <b>✍️ Новое обращение</b> — создать тикет\n"
            "• Напишите в чат — дополните открытый тикет\n"
            "• <b>📨 Мои обращения</b> — история и закрытие"
        )
        await callback.message.answer(support_text, reply_markup=_user_main_reply_kb())
        try:
            await callback.message.delete()
        except Exception:
            pass

    @router.message(SupportDialog.waiting_for_subject, F.chat.type == "private")
    async def support_subject_received(message: types.Message, state: FSMContext):
        data = await state.get_data()
        start_time = data.get("start_time", 0)
        if time.time() - start_time > 900:
            await state.clear()
            await _start_ticket_creation_flow(message, state)
            return

        if await _check_banned(message, state):
            return

        subject = (message.text or "").strip()
        if subject in ["✍️ Новое обращение", "📨 Мои обращения"]:
            await _send_subject_prompt(message, state)
            return

        if len(subject) > 50:
            await message.answer(
                "⚠️ <b>Заголовок слишком длинный!</b>\n\n"
                "Пожалуйста, опишите тему кратко (до 50 символов).\n"
                "Подробности вы сможете написать на следующем шаге."
            )
            return

        await state.update_data(subject=subject, start_time=time.time())
        await message.answer(
            "✉️ <b>Опишите проблему</b>\n\n"
            "Одним сообщением, можно приложить фото.",
            reply_markup=_cancel_creation_kb()
        )
        await state.set_state(SupportDialog.waiting_for_message)

    @router.message(SupportDialog.waiting_for_message, F.chat.type == "private")
    async def support_message_received(message: types.Message, state: FSMContext, bot: Bot):
        data = await state.get_data()
        start_time = data.get("start_time", 0)
        if time.time() - start_time > 900:
            await state.clear()
            await _start_ticket_creation_flow(message, state)
            return

        if await _check_banned(message, state):
            return
        
        text_content = (message.text or "").strip()
        if text_content in ["✍️ Новое обращение", "📨 Мои обращения"]:
            await _send_subject_prompt(message, state)
            return

        user_id = message.from_user.id
        raw_subject = (data.get("subject") or "").strip()
        if raw_subject:
            subject = raw_subject
        else:
            _src = text_content or (message.caption or "").strip()
            subject = _src[:40] if _src else "Обращение"
        ticket_id, created_new = get_or_create_open_ticket(user_id, subject)
        if not ticket_id:
            await message.answer("❌ Не удалось создать обращение. Попробуйте позже.")
            await state.clear()
            return

        await _process_ticket_message_flow(bot, message, state, ticket_id, subject, created_new)
        
    @router.callback_query(F.data == "support_my_tickets")
    async def support_my_tickets_handler(callback: types.CallbackQuery, state: FSMContext):
        current_state = await state.get_state()
        if current_state in [SupportDialog.waiting_for_subject, SupportDialog.waiting_for_message]:
            await callback.answer("⚠️ Пожалуйста, сначала завершите создание обращения.", show_alert=True)
            return
        await callback.answer()
        await _send_user_tickets_list(callback, callback.from_user.id)

    @router.callback_query(F.data.startswith("support_view_"))
    async def support_view_ticket_handler(callback: types.CallbackQuery):
        await callback.answer()
        ticket_id = int(callback.data.split("_")[-1])
        ticket = get_ticket(ticket_id)
        if not ticket or ticket.get('user_id') != callback.from_user.id:
            await _safe_edit(callback, TXT_ACCESS_DENIED)
            return
        messages = get_ticket_messages(ticket_id)
        human_status = "🟢 Открыт" if ticket.get('status') == 'open' else "🔒 Закрыт"
        is_star = (ticket.get('subject') or '').startswith('⭐ ')
        star_line = "⭐ Важно" if is_star else "—"
        parts = [
            f"<b>🧾 Тикет #{ticket_id}</b>",
            f"<b>Статус:</b> {human_status}",
            f"<b>Тема:</b> <i>{html.quote(ticket.get('subject') or '—')}</i>",
            f"<b>Важность:</b> {star_line}",
            ""
        ]
        shown = 0
        for m in messages:
            if m.get('sender') == 'note':
                continue
            is_user = m.get('sender') == 'user'
            who = "Вы" if is_user else "Поддержка"
            content = (m.get('content') or '').strip()
            if not content:
                continue
            parts.append(f"<b>{who}:</b> {html.quote(content)}")
            shown += 1
            if shown >= 12:
                parts.append("<i>…показаны последние сообщения</i>")
                break
        final_text = "\n".join(parts) if parts else f"<b>🧾 Тикет #{ticket_id}</b>\n<i>Сообщений пока нет</i>"
        is_open = (ticket.get('status') == 'open')
        buttons = []
        if is_open:
            buttons.append([types.InlineKeyboardButton(text="💬 Ответить", callback_data=f"support_reply_{ticket_id}")])
            buttons.append([types.InlineKeyboardButton(text="✅ Закрыть", callback_data=f"support_close_{ticket_id}")])
        buttons.append([types.InlineKeyboardButton(text="⬅️ К списку", callback_data="support_my_tickets")])
        await callback.message.edit_text(final_text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith("support_reply_"))
    async def support_reply_prompt_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        ticket_id = int(callback.data.split("_")[-1])
        ticket = get_ticket(ticket_id)
        if await _check_banned(callback, state):
             markup = _support_contact_markup()
             if markup:
                await callback.message.edit_text(TXT_BAN_RESTRICTED, reply_markup=markup)
             else:
                await callback.message.edit_text(TXT_BAN_RESTRICTED)
             return
        if not ticket or ticket.get('user_id') != callback.from_user.id or ticket.get('status') != 'open':
            await _safe_edit(callback, TXT_CANNOT_REPLY)
            return
        await state.update_data(reply_ticket_id=ticket_id)
        await callback.message.edit_text(
            f"<b>💬 Ответ по тикету #{ticket_id}</b>\n\n"
            "Напишите сообщение (можно с фото)."
        )
        await state.set_state(SupportDialog.waiting_for_reply)

    @router.message(SupportDialog.waiting_for_reply, F.chat.type == "private")
    async def support_reply_received(message: types.Message, state: FSMContext, bot: Bot):
        if await _check_banned(message, state):
            return
        data = await state.get_data()
        ticket_id = data.get('reply_ticket_id')
        ticket = get_ticket(ticket_id)
        if not ticket or ticket.get('user_id') != message.from_user.id or ticket.get('status') != 'open':
            await message.answer(TXT_CANNOT_REPLY)
            await state.clear()
            return
        
        await _process_ticket_message_flow(bot, message, state, ticket_id, ticket.get('subject'), created_new=False)

    @router.message(F.is_topic_message == True)
    async def forum_thread_message_handler(message: types.Message, bot: Bot, state: FSMContext):
        try:
            if not message.message_thread_id:
                return
            forum_chat_id = message.chat.id
            thread_id = message.message_thread_id
            ticket = get_ticket_by_thread(str(forum_chat_id), int(thread_id))
            if not ticket:
                return
            user_id = int(ticket.get('user_id'))
            try:
                current_state = await state.get_state()
                if current_state == AdminDialog.waiting_for_note.state:
                    note_body = (message.text or message.caption or '').strip()
                    author_id = message.from_user.id if message.from_user else None
                    if author_id:
                        username = _get_username_display(message.from_user, author_id)
                        note_text = f"[Заметка от {username} (ID: {author_id})]\n{note_body}"
                    else:
                        note_text = note_body
                    add_support_message(int(ticket['ticket_id']), sender='note', content=note_text)
                    await message.answer("✅ <b>Внутренняя заметка сохранена.</b>")
                    await state.clear()
                    return
            except Exception:
                pass
            me = await bot.get_me()
            if message.from_user and message.from_user.id == me.id:
                return

            # Анонимный админ (владелец пишет «как группа»): from = GroupAnonymousBot (1087968824),
            # sender_chat = сама группа. Его ID не равен admin_telegram_id, поэтому разрешаем отдельно —
            # иначе ответы анонимного админа молча отсекались и не доходили клиенту.
            is_anon_admin = (
                (message.sender_chat is not None and message.sender_chat.id == message.chat.id)
                or (message.from_user is not None and message.from_user.id == 1087968824)
            )
            is_admin_by_setting = bool(message.from_user) and is_admin(message.from_user.id)
            is_admin_in_chat = False
            if message.from_user:
                try:
                    member = await bot.get_chat_member(chat_id=forum_chat_id, user_id=message.from_user.id)
                    is_admin_in_chat = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
                except Exception:
                    pass
            if not (is_admin_by_setting or is_admin_in_chat or is_anon_admin):
                return
            content = (message.text or message.caption or "").strip()
            if content.lower() in ("/close", "/закрыть", "закрыть", "close"):
                tid = int(ticket["ticket_id"])
                if (ticket.get("status") or "").strip().lower() == "closed":
                    await message.answer(f"Тикет #{tid} уже закрыт.")
                    return
                if set_ticket_status(tid, "closed"):
                    await _manage_forum_topic(bot, ticket, "close")
                    await _send_ticket_closed_notification(bot, user_id, tid, is_user_action=False)
                    await message.answer(
                        f"✅ <b>Тикет #{tid} закрыт.</b>",
                        reply_markup=_admin_actions_kb(tid),
                    )
                else:
                    await message.answer(f"❌ Не удалось закрыть тикет #{tid}.")
                return
            # Медиа оператора (фото/видео) → скачиваем в support-media и пишем в сообщение,
            # чтобы веб-чат (браузер/мобилка, без Telegram) показал его через опрос /api/support/status.
            media_json = None
            try:
                _fid = None; _mtype = None; _ext = None
                if message.photo:
                    _fid = message.photo[-1].file_id; _mtype = 'image'; _ext = '.jpg'
                elif message.video:
                    _fid = message.video.file_id; _mtype = 'video'; _ext = '.mp4'
                elif message.animation:
                    _fid = message.animation.file_id; _mtype = 'video'; _ext = '.mp4'
                elif message.document and (message.document.mime_type or '').startswith(('image/', 'video/')):
                    _fid = message.document.file_id
                    _mtype = 'video' if (message.document.mime_type or '').startswith('video/') else 'image'
                    _ext = os.path.splitext(message.document.file_name or '')[1].lower()
                if _fid:
                    import uuid as _uuid, json as _json
                    if not _ext or len(_ext) > 6 or '/' in _ext or '\\' in _ext:
                        _ext = '.mp4' if _mtype == 'video' else '.jpg'
                    _dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp", "module", "support-media")
                    os.makedirs(_dir, exist_ok=True)
                    _fname = f"{_uuid.uuid4().hex}{_ext}"
                    await bot.download(_fid, destination=os.path.join(_dir, _fname))
                    media_json = _json.dumps({"type": _mtype, "url": f"/module/support-media/{_fname}"})
            except Exception as _e:
                logger.warning(f"Не удалось сохранить медиа оператора для веб-чата: {_e}")
            if content or media_json:
                add_support_message(ticket_id=int(ticket['ticket_id']), sender='admin', content=content, media=media_json)
            await _send_admin_reply_to_user(bot, user_id, int(ticket['ticket_id']), message, content)
        except Exception as e:
            logger.warning(f"Не удалось передать сообщение темы форума: {e}")

    @router.message(F.forum_topic_closed)
    async def forum_topic_closed_sync_handler(message: types.Message, bot: Bot):
        """Синхронизация: закрытие темы в Telegram → закрытие тикета в БД."""
        try:
            if not message.message_thread_id:
                return
            ticket = get_ticket_by_thread(str(message.chat.id), int(message.message_thread_id))
            if not ticket or (ticket.get("status") or "").strip().lower() == "closed":
                return
            ticket_id = int(ticket["ticket_id"])
            if set_ticket_status(ticket_id, "closed"):
                user_id = int(ticket.get("user_id"))
                await _send_ticket_closed_notification(bot, user_id, ticket_id, is_user_action=False)
                await bot.send_message(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    text=f"✅ Тикет #{ticket_id} закрыт (синхронизировано с темой).",
                    reply_markup=_admin_actions_kb(ticket_id),
                )
        except Exception as e:
            logger.warning(f"forum_topic_closed sync: {e}")

    @router.message(F.forum_topic_reopened)
    async def forum_topic_reopened_sync_handler(message: types.Message, bot: Bot):
        """Синхронизация: открытие темы в Telegram → переоткрытие тикета."""
        try:
            if not message.message_thread_id:
                return
            ticket = get_ticket_by_thread(str(message.chat.id), int(message.message_thread_id))
            if not ticket or (ticket.get("status") or "").strip().lower() == "open":
                return
            ticket_id = int(ticket["ticket_id"])
            if set_ticket_status(ticket_id, "open"):
                await bot.send_message(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    text=f"🔓 Тикет #{ticket_id} снова открыт.",
                    reply_markup=_admin_actions_kb(ticket_id),
                )
        except Exception as e:
            logger.warning(f"forum_topic_reopened sync: {e}")

    @router.callback_query(F.data.startswith("support_close_"))
    async def support_close_ticket_handler(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket_id = int(callback.data.split("_")[-1])
        await _change_ticket_status_common(bot, callback, ticket_id, 'closed', is_admin=False)

    @router.callback_query(F.data.startswith("admin_close_"))
    async def admin_close_ticket(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket: return
        await _change_ticket_status_common(bot, callback, ticket_id, 'closed', is_admin=True)

    @router.callback_query(F.data.startswith("admin_reopen_"))
    async def admin_reopen_ticket(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket: return
        await _change_ticket_status_common(bot, callback, ticket_id, 'open', is_admin=True)

    @router.callback_query(F.data.startswith("admin_delete_"))
    async def admin_delete_ticket(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket: return
        
        await _safe_edit(callback, f"🗑 Удаляю тикет #{ticket_id}...")
        await _manage_forum_topic(bot, ticket, 'delete')
        
        if delete_ticket(ticket_id):
            await callback.answer(f"🗑 Тикет #{ticket_id} удалён.", show_alert=False)
        else:
            await callback.answer("❌ Не удалось удалить тикет.", show_alert=True)

    @router.callback_query(F.data.startswith("admin_star_"))
    async def admin_toggle_star(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket:
            return
        forum_chat_id = int(ticket.get('forum_chat_id') or callback.message.chat.id)
        subject = (ticket.get('subject') or '').strip()
        is_starred = subject.startswith("⭐ ")
        if is_starred:
            base_subject = subject[2:].strip()
            new_subject = base_subject if base_subject else "Обращение без темы"
        else:
            base_subject = subject if subject else "Обращение без темы"
            new_subject = f"⭐ {base_subject}"
        if update_ticket_subject(ticket_id, new_subject):
            try:
                thread_id = ticket.get('message_thread_id')
                if thread_id and ticket.get('forum_chat_id'):
                    user_id = int(ticket.get('user_id')) if ticket.get('user_id') else None
                    author_tag = None
                    if user_id:
                        try:
                            user = await bot.get_chat(user_id)
                            username = getattr(user, 'username', None)
                            author_tag = f"@{username}" if username else f"ID {user_id}"
                        except Exception:
                            author_tag = f"ID {user_id}"
                    else:
                        author_tag = "пользователь"
                    is_star2, display_subj2 = _parse_star_subject(new_subject)
                    topic_name = _build_topic_name(ticket_id, new_subject, author_tag)
                    await bot.edit_forum_topic(chat_id=int(ticket['forum_chat_id']), message_thread_id=int(thread_id), name=topic_name)
            except Exception:
                pass
            try:
                thread_id = ticket.get('message_thread_id')
                forum_chat_id = ticket.get('forum_chat_id')
                if thread_id and forum_chat_id:
                    state_text = "включена" if not is_starred else "снята"
                    msg = await bot.send_message(
                        chat_id=int(forum_chat_id),
                        message_thread_id=int(thread_id),
                        text=f"⭐ Важность {state_text} для тикета #{ticket_id}."
                    )
                    if not is_starred:
                        try:
                            await bot.pin_chat_message(chat_id=int(forum_chat_id), message_id=msg.message_id, disable_notification=True)
                        except Exception:
                            pass
                    else:
                        try:
                            await bot.unpin_all_forum_topic_messages(chat_id=int(forum_chat_id), message_thread_id=int(thread_id))
                        except Exception:
                            pass
            except Exception:
                pass
            state_text = "включена" if not is_starred else "снята"
            await callback.message.answer(f"✅ <b>Пометка важности {state_text}.</b>\nНазвание темы обновлено.")
        else:
            await callback.message.answer("❌ Не удалось обновить тему тикета.")

    @router.callback_query(F.data.startswith("admin_user_"))
    async def admin_show_user(callback: types.CallbackQuery, bot: Bot):
        await callback.answer("Загружаю карточку…")
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket:
            return

        user_id_val = int(ticket.get("user_id") or 0)
        if not user_id_val:
            await callback.message.answer("❌ У тикета нет user_id")
            return

        tg_user = None
        try:
            tg_user = await bot.get_chat(user_id_val)
        except Exception:
            pass

        from shop_bot.support_user_card import send_support_user_card

        thread_id = ticket.get("message_thread_id")
        forum_chat_id = ticket.get("forum_chat_id")
        target_chat = int(forum_chat_id) if forum_chat_id else callback.message.chat.id
        thread = int(thread_id) if thread_id else None

        await send_support_user_card(
            bot,
            target_chat,
            user_id_val,
            message_thread_id=thread,
            ticket_id=ticket_id,
            tg_user=tg_user,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=_admin_actions_kb(ticket_id))
        except Exception:
            pass

    @router.callback_query(F.data.startswith("admin_reply_dm_"))
    async def admin_reply_dm_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket:
             return
             
        await state.update_data(admin_reply_ticket_id=ticket_id)
        await callback.message.answer(
            f"💬 Введите ответ для пользователя по тикету #{ticket_id}:",
            reply_markup=types.ForceReply(selective=True)
        )
        await state.set_state(AdminDialog.waiting_for_reply)

    @router.message(AdminDialog.waiting_for_reply)
    async def admin_reply_message_handler(message: types.Message, state: FSMContext, bot: Bot):
        data = await state.get_data()
        ticket_id = data.get('admin_reply_ticket_id')
        if not ticket_id:
            await message.answer("❌ <b>Ошибка: контекст ответа потерян.</b>")
            await state.clear()
            return
            
        content = (message.text or message.caption or "").strip()
        if not content:
            await message.answer("⚠️ <b>Сообщение не может быть пустым.</b>")
            return
            
        ticket = get_ticket(ticket_id)
        if not ticket:
            await message.answer(TXT_TICKET_NOT_FOUND)
            await state.clear()
            return

        user_id = int(ticket['user_id'])
        
        add_support_message(ticket_id=ticket_id, sender='admin', content=content)
        
        try:
            await _send_admin_reply_to_user(bot, user_id, ticket_id, message, content)
        except Exception as e:
            logger.warning(f"Failed to send reply to user {user_id}: {e}")
            await message.answer("❌ Не удалось доставить сообщение пользователю (возможно, он заблокировал бота).")
            
        try:
            forum_chat_id = ticket.get('forum_chat_id')
            thread_id = ticket.get('message_thread_id')
            if forum_chat_id and thread_id:
                 admin_tag = _get_username_display(message.from_user, message.from_user.id)
                 await bot.send_message(
                    chat_id=int(forum_chat_id),
                    text=f"👨‍💻 Ответ администратора {admin_tag} через ЛС бота:\n\n{content}",
                    message_thread_id=int(thread_id)
                 )
        except Exception as e:
            logger.warning(f"Failed to mirror admin reply to forum: {e}")

        await message.answer("✅ <b>Сообщение успешно отправлено пользователю.</b>")
        await state.clear()

    @router.callback_query(F.data.startswith("admin_ban_user_"))
    async def admin_ban_user(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket:
            return
        forum_chat_id = int(ticket.get('forum_chat_id') or callback.message.chat.id)
        try:
            user_id = int(ticket.get('user_id'))
        except Exception:
            await callback.message.answer("❌ Не удалось определить пользователя тикета.")
            return
        try:
            ban_user(user_id)
        except Exception as e:
            await callback.message.answer(f"❌ Не удалось забанить пользователя: {e}")
            return
        await callback.message.answer(f"🚫 <b>Пользователь {user_id} забанен.</b>")

        if notif_enabled("support_ban"):
            try:
                await bot.send_message(user_id, get_notif_text("support_ban"), reply_markup=keyboards.build_notif_keyboard("support_ban"))
            except Exception:
                pass
        try:
            await callback.message.edit_reply_markup(reply_markup=_admin_actions_kb(ticket_id))
        except TelegramBadRequest:
            pass

    @router.callback_query(F.data.startswith("admin_unban_user_"))
    async def admin_unban_user(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket:
             return
        forum_chat_id = int(ticket.get('forum_chat_id') or callback.message.chat.id)
        try:
            user_id = int(ticket.get('user_id'))
        except Exception:
            await callback.message.answer("❌ Не удалось определить пользователя тикета.")
            return
        try:
            unban_user(user_id)
        except Exception as e:
            await callback.message.answer(f"❌ Не удалось разбанить пользователя: {e}")
            return
        await callback.message.answer(f"✅ <b>Пользователь {user_id} разбанен.</b>")

        try:
            if notif_enabled("support_unban"):
                await bot.send_message(user_id, get_notif_text("support_unban"), reply_markup=keyboards.build_notif_keyboard("support_unban"))
        except Exception:
            pass
        try:
            await callback.message.edit_reply_markup(reply_markup=_admin_actions_kb(ticket_id))
        except TelegramBadRequest:
            pass

    @router.callback_query(F.data.startswith("admin_note_"))
    async def admin_note_prompt(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket:
             return
        forum_chat_id = int(ticket.get('forum_chat_id') or callback.message.chat.id)
        await state.update_data(note_ticket_id=ticket_id)
        await callback.message.answer("📝 <b>Отправьте внутреннюю заметку одним сообщением.</b>\nОна не будет отправлена пользователю.")
        await state.set_state(AdminDialog.waiting_for_note)

    @router.callback_query(F.data.startswith("admin_notes_"))
    async def admin_list_notes(callback: types.CallbackQuery, bot: Bot):
        await callback.answer()
        ticket, ticket_id = await _get_ticket_and_check_admin(callback, bot)
        if not ticket:
            return
        forum_chat_id = int(ticket.get('forum_chat_id') or callback.message.chat.id)
        notes = [m for m in get_ticket_messages(ticket_id) if m.get('sender') == 'note']
        if not notes:
            await callback.message.answer("🗒 <b>Внутренних заметок пока нет.</b>")
            return
        lines = [f"🗒 Заметки по тикету #{ticket_id}:"]
        for m in notes:
            created = m.get('created_at')
            content = (m.get('content') or '').strip()
            lines.append(f"— ({created})\n{content}")
        text = "\n\n".join(lines)
        await callback.message.answer(text)

    @router.message(AdminDialog.waiting_for_note, F.is_topic_message == True)
    async def admin_note_receive(message: types.Message, state: FSMContext):
        data = await state.get_data()
        ticket_id = data.get('note_ticket_id')
        if not ticket_id:
            await message.answer("❌ Не найден контекст тикета для заметки.")
            await state.clear()
            return
        author_id = message.from_user.id if message.from_user else None
        username = _get_username_display(message.from_user, author_id) if message.from_user else None
        note_body = (message.text or message.caption or '').strip()
        note_text = f"[Заметка от {username} (ID: {author_id})]\n{note_body}" if author_id else note_body
        add_support_message(int(ticket_id), sender='note', content=note_text)
        await message.answer("✅ <b>Внутренняя заметка сохранена.</b>")
        await state.clear()

    @router.message(F.text == "▶️ Начать", F.chat.type == "private")
    async def start_text_button(message: types.Message, state: FSMContext):
        await _start_ticket_creation_flow(message, state)

    @router.message(F.text == "✍️ Новое обращение", F.chat.type == "private")
    async def new_ticket_text_button(message: types.Message, state: FSMContext):
        await _start_ticket_creation_flow(message, state)

    @router.message(F.text == "📨 Мои обращения", F.chat.type == "private")
    async def my_tickets_text_button(message: types.Message, state: FSMContext):
        current_state = await state.get_state()
        if current_state in [SupportDialog.waiting_for_subject, SupportDialog.waiting_for_message]:
            await message.answer("⚠️ <b>Вы уже создаете обращение.</b>\nПожалуйста, завершите текущий процесс.")
            return
        await _send_user_tickets_list(message, message.from_user.id)

    @router.message(F.chat.type == "private")
    async def relay_user_message_to_forum(message: types.Message, bot: Bot, state: FSMContext):
        current_state = await state.get_state()
        if current_state is not None:
            return

        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return

        if await _check_banned(message, state):
            return

        existing = _get_latest_open_ticket(user_id)
        if not existing:
            # Первое свободное сообщение — сначала самопомощь (FAQ-видео), потом оператор.
            q = (message.text or message.caption or "").strip()
            await state.set_state(SupportDialog.faq_shown)
            await state.update_data(pending_question=q, start_time=time.time())
            intro = (get_setting("support_faq_intro") or "❗️ <b>Актуальные вопросы</b> ❗️\\n\\nВозможно, ответ уже есть в коротком видео 👇\\nНе нашли? Нажмите «🆘 Позвать оператора».").replace("\\n", "\n")
            await message.answer(intro, reply_markup=_build_faq_kb())
            return

        ticket_id = int(existing["ticket_id"])
        subject = existing.get("subject") or "Обращение"
        await _process_ticket_message_flow(bot, message, state, ticket_id, subject, created_new=False)

    return router
