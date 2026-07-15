from datetime import datetime, timedelta, timezone
import os
import time

# --- TIME CONFIGURATION ---
# Force MSK (UTC+3)
os.environ['TZ'] = 'Etc/GMT-3'
if hasattr(time, 'tzset'):
    time.tzset()

def get_msk_time():
    """Returns current time in MSK (UTC+3)"""
    return datetime.now(timezone(timedelta(hours=3), name='MSK'))
# --------------------------

from aiogram import html

CHOOSE_PLAN_MESSAGE = "Выберите подходящий тариф:"
CHOOSE_PAYMENT_METHOD_MESSAGE = "Выберите удобный способ оплаты:"
VPN_INACTIVE_TEXT = "❌ <b>Статус VPN:</b> Неактивен (срок истек)"
VPN_NO_DATA_TEXT = "ℹ️ <b>Статус VPN:</b> У вас пока нет активных ключей."


def get_profile_text(username, user_id, total_spent, total_months, vpn_status, vpn_remaining, main_balance, referral_count, total_ref_earned, seller_info=None):
    # Base Layout
    text = (
        f"<b>👤 ПРОФИЛЬ:</b> {username} / <b>iD:</b> <code>{user_id}</code>\n\n"
        f"<b>💎 ПОДПИСКА</b>\n"
        f"<b>🛡 Статус VPN:</b> {vpn_status} ✅\n"
        f"<b>⏳ Осталось:</b> {vpn_remaining}\n"
        f"<b>💲 Потрачено всего:</b> {total_spent:.0f} RUB\n"
        f"<b>📅 Приобретено месяцев:</b> {total_months}\n\n"
        f"<b>💼 ФИНАНСЫ</b>\n"
        f"<b>💳 Основной баланс:</b> {main_balance:.0f} RUB\n"
        f"<b>🤝 Рефералов:</b> {referral_count}\n"
        f"<b>💰 Заработано:</b> {total_ref_earned:.2f} RUB"
    )

    # Partner Program Section (Only if seller_active)
    if seller_info:
         # seller_info dict keys expected: 'sale', 'ref', 'squad_uuid'
         s_sale = seller_info.get('sale', 0)
         s_ref = seller_info.get('ref', 0)
         s_squad = seller_info.get('squad_uuid')
         
         text += "\n\n<b>👑 ПАРТНЕРСКАЯ ПРОГРАММА</b>\n"
         if s_ref and float(s_ref) > 0:
             text += f"<b>👥 Реферальный бонус:</b> +{s_ref}%\n"
         if s_sale and float(s_sale) > 0:
             text += f"<b>🛍 Персональная скидка:</b> -{s_sale}%\n"
         if s_squad and str(s_squad) != '0' and str(s_squad).strip():
             text += f"<b>🛰 Индивидуальный Сквад:</b> ✅"

    return text

def get_vpn_active_text(days_left, hours_left):
    return f"{days_left} д. {hours_left} ч."

def _get_status_text(remaining):
    total_seconds = int(remaining.total_seconds())
    if total_seconds < 0:
        return "Не активен (Истек)"
    return "Активен"

def _format_remaining_details(remaining: timedelta) -> str:
    total_seconds = int(remaining.total_seconds())
    if total_seconds <= 0:
        return "0мин"

    minutes = (total_seconds // 60) % 60
    hours = (total_seconds // 3600) % 24
    days = remaining.days % 365
    years = remaining.days // 365

    parts = []
    if years > 0:
        parts.append(f"{years}г.")
    if days > 0:
        parts.append(f"{days}д.")
    if hours > 0:
        parts.append(f"{hours}ч.")
    if minutes > 0:
        parts.append(f"{minutes}мин")

    return " ".join(parts) if parts else "меньше минуты"

def get_key_info_text(key_number, expiry_date, created_date, connection_string, email=None, hwid_limit=None, hwid_usage=None, traffic_limit=None, traffic_used=None, comment=None):
    now = get_msk_time().replace(tzinfo=None)
    
    # Ensure expiry_date is comparable (naive vs naive)
    if expiry_date.tzinfo:
        expiry_date = expiry_date.astimezone(get_msk_time().tzinfo).replace(tzinfo=None)
        
    remaining = expiry_date - now
    days_left = remaining.days
    
    status_icon = "🟢"
    status_text = _get_status_text(remaining)
    remaining_str = _format_remaining_details(remaining)
    
    if days_left <= 10:
        status_icon = "🟡"
    
    if days_left < 0:
        status_icon = "🔴"
        remaining_str = "0мин"

    traffic_block = ""
    if traffic_limit:
        t_lim_str = str(traffic_limit).strip()
        t_lim_display = "∞" if t_lim_str == "0" or t_lim_str.startswith("0 ") else t_lim_str
        traffic_block = f"{traffic_used} / {t_lim_display}"

    hwid_block = ""
    if hwid_limit is not None:
        limit_str = str(hwid_limit)
        limit_display = "∞" if limit_str == "0" or (limit_str.isdigit() and int(limit_str) > 98) else limit_str
        hwid_block = f"{hwid_usage} / {limit_display}"

    if email and str(email).endswith("@bot.local"):
        email = str(email).replace("@bot.local", "@bot")

    comment_block = ""
    if comment:
        comment_block = f"💬 <b>Комментарий:</b> <blockquote>{html.quote(comment)}</blockquote>\n"

    return (
        f"🔑 <b>Информация о ключе #{key_number}</b>\n\n"
        f"📅 <b>Сроки действия:</b>\n"
        f"{status_icon} <b>Статус:</b> {status_text}\n"
        f"➕ <b>Куплен:</b> {created_date.strftime('%d.%m.%Y')}\n"
        f"🕙 <b>Истекает:</b> {expiry_date.strftime('%d.%m.%Y %H:%M')}\n"
        f"⏳ <b>Осталось:</b> {remaining_str}\n"
        f"💌 <b>ID ключа:</b> <code>{email}</code>\n\n"
        f"📉 <b>Использование:</b>\n"
        f"🛰 <b>Лимит трафика:</b> {traffic_block}\n" 
        f"📱 <b>Лимит устройств:</b> {hwid_block}\n"
        f"🗽 <b>Ваш ключ:</b>\n<code>{connection_string}</code>"
        f"\n\n{comment_block}"
    )


def get_purchase_success_text(action: str, key_number: int, expiry_date, connection_string: str, email: str = None):
    action_text = "продлен" if action == "extend" else "готов"
    expiry_date_str = expiry_date.strftime('%d.%m %H:%M')
    
    # Обработка email для скрытия служебного суффикса @bot.local
    if email and str(email).endswith("@bot.local"):
        email = str(email).replace("@bot.local", "@bot")
    email_display = email if email else "Не указан"

    # Текст управляется из админки (вкладка «Уведомления», ключ purchase_success).
    # Дефолт в реестре = прежний текст, поэтому поведение не меняется, пока не отредактируешь.
    from shop_bot.data_manager.notifications import get_notif_text
    return get_notif_text(
        "purchase_success",
        key_number=key_number,
        action_text=action_text,
        expiry=expiry_date_str,
        email=email_display,
        connection=connection_string,
    )