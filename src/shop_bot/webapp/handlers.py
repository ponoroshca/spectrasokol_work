from typing import Any
from fastapi import FastAPI, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response
import aiohttp
from shop_bot.data_manager.remnawave_repository import get_setting, get_user_keys, get_msk_time, get_webapp_settings, get_user, get_referral_count, get_all_hosts, list_squads, get_plans_for_host
import os
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
import uuid
import asyncio
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, FSInputFile, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder
import json
import traceback
from shop_bot.bot.keyboards import (
    create_payment_keyboard, 
    create_yoomoney_payment_keyboard, 
    create_cryptobot_payment_keyboard
)
from shop_bot.data_manager.remnawave_repository import (
    create_payload_pending, get_plan_by_id,
    deduct_from_balance, check_transaction_exists, add_to_balance, log_transaction,
    add_to_referral_balance_all, get_balance, get_all_users, is_admin, update_user_stats,
    redeem_promo_code, update_promo_code_status, record_key_from_payload, get_key_by_id,
    update_key, get_key_by_email
)
import shop_bot.data_manager.remnawave_repository as rw_repo
from shop_bot.data_manager.database import get_seller_user, get_device_tiers, get_host
from shop_bot.modules import remnawave_api
from shop_bot.config import get_purchase_success_text
import re
from decimal import Decimal
import logging
from urllib.parse import urlencode


logger = logging.getLogger(__name__)

# In-memory storage for temporary auth tokens: {token: user_id}
TEMP_AUTH_TOKENS = {}


# ===== Utility Functions =====
def get_transaction_comment(user_data: dict, action_type: str, value: any, host_name: str = None) -> str:
    from shop_bot.bot.handlers import get_transaction_comment as bot_get_comment
    from aiogram.types import User
    
    # Adapt dictionary to types.User if needed by bot function
    tg_user = User(
        id=user_data.get('id', 0),
        is_bot=False,
        first_name=user_data.get('first_name', 'User'),
        username=user_data.get('username')
    )
    return bot_get_comment(tg_user, action_type, value, host_name)

def calculate_webapp_price(price: float, user_id: int) -> float:
    try:
        if not user_id or int(user_id) == 0:
            return round(price, 2)

        user = get_user(user_id)
        if not user:
            return price
        
        if user.get('seller_active'):
            seller = get_seller_user(user_id)
            if seller and seller.get('seller_sale'):
                discount_percent = float(seller['seller_sale'])
                price -= price * (discount_percent / 100)
                logger.info(f"[WEBAPP] - Применена скидка продавца {discount_percent}% для {user_id}")
        
        if user.get('referred_by') and user.get('total_spent', 0) == 0 and not [k for k in get_user_keys(user_id) if not (k.get('key_email') or '').startswith('trial_')]:
            ref_discount = get_setting("referral_discount")
            if ref_discount:
                try:
                    d_val = float(ref_discount)
                    if d_val > 0:
                        price -= price * (d_val / 100)
                        logger.info(f"[WEBAPP] - Применена реферальная скидка {d_val}% для {user_id}")
                except: pass
                
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка расчета цены для {user_id}: {e}")
        
    return round(price, 2)

# ===== HELPER FUNCTIONS FOR PAYMENT PROCESS =====
async def notify_admin_of_purchase(bot: Bot, metadata: dict):
    from shop_bot.bot.handlers import notify_admin_of_purchase as bot_notify
    await bot_notify(bot, metadata)

async def process_successful_payment(bot: Bot, metadata: dict):
    from shop_bot.bot.handlers import process_successful_payment as bot_process
    await bot_process(bot, metadata)

async def _send_telegram_message(user_id: int, text: str, reply_markup=None, photo=None):
    token = get_setting("telegram_bot_token")
    if not token:
        logger.error("[WEBAPP] - Токен бота не найден в настройках")
        return False
    bot = Bot(token=token)
    try:
        if photo:
            await bot.send_photo(chat_id=user_id, photo=photo, caption=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="HTML")
        logger.info(f"[WEBAPP] - Сообщение успешно отправлено пользователю {user_id}")
        return True
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка отправки сообщения {user_id}: {e}")
        return False
    finally:
        await bot.session.close()

async def _send_invoice_stars(user_id: int, title: str, description: str, payload: str, amount: int):
    token = get_setting("telegram_bot_token")
    if not token:
        logger.error("[WEBAPP] - Токен бота не найден для Stars")
        return False
    bot = Bot(token=token)
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="", 
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=amount)]
        )
        logger.info(f"[WEBAPP] - Счет Stars отправлен пользователю {user_id} на сумму {amount}")
        return True
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка отправки счета Stars {user_id}: {e}")
        return False
    finally:
        await bot.session.close()


from shop_bot.modules.platega_api import PlategaAPI
from shop_bot.modules.heleket_api import create_heleket_payment_request
from shop_bot.bot.keyboards import (
    create_payment_keyboard, create_cryptobot_payment_keyboard,
    create_yoomoney_payment_keyboard
)
from shop_bot.bot.handlers import create_cryptobot_api_invoice, process_successful_payment
from yookassa import Configuration as YookassaConfiguration, Payment as YookassaPayment
from aiogram.types import BufferedInputFile
import io
import qrcode
from urllib.parse import urlencode

def _build_yoomoney_link(receiver: str, amount_rub: Decimal, label: str, description: str) -> str:
    base = "https://yoomoney.ru/quickpay/confirm.xml"
    params = {
        "receiver": (receiver or "").strip(),
        "quickpay-form": "donate",
        "targets": description[:50],
        "formcomment": description,
        "short-dest": description,
        "sum": f"{amount_rub:.2f}",
        "label": label,
        "successURL": f"https://t.me/{get_setting('telegram_bot_username')}",
    }
    return base + "?" + urlencode(params)

app = FastAPI()

ico_dir = os.path.join(os.path.dirname(__file__), "module", "ico")
if os.path.exists(ico_dir):
    app.mount("/module/ico", StaticFiles(directory=ico_dir), name="ico")

# Вложения веб-чата поддержки (скрин/фото/видео) — отдаём со своего origin (узкий mount, не весь /module).
support_media_dir = os.path.join(os.path.dirname(__file__), "module", "support-media")
os.makedirs(support_media_dir, exist_ok=True)
app.mount("/module/support-media", StaticFiles(directory=support_media_dir), name="support_media")

# Звуки колеса фортуны: владелец кладёт лицензированные spin.mp3 / win.mp3 сюда,
# веб-апп сам их подхватит (иначе играет синтезированный фолбэк).
wheel_media_dir = os.path.join(os.path.dirname(__file__), "module", "wheel")
os.makedirs(wheel_media_dir, exist_ok=True)
app.mount("/module/wheel", StaticFiles(directory=wheel_media_dir), name="wheel_media")

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

    # Берем только первые две значимые части для краткости
    result_parts = parts[:2]
    return " ".join(result_parts) if result_parts else "меньше минуты"

def _format_bytes(size: Any) -> str:
    if size is None: return "0 B"
    if isinstance(size, str):
        if any(x in size for x in ['B', 'KB', 'MB', 'GB', 'TB', 'iB']):
            return size
        try: size = float(size)
        except: return "0 B"
    
    if size <= 0: return "0 B"
    power = 1024
    n = 0
    power_labels = {0 : 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < 4:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"


def _parse_key_datetime(value: Any) -> datetime | None:
    """Parse datetime from DB/API values including milliseconds and ISO formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        dt = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
        ):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except Exception:
                return None

    if dt.tzinfo is not None:
        try:
            dt = dt.astimezone(get_msk_time().tzinfo).replace(tzinfo=None)
        except Exception:
            dt = dt.replace(tzinfo=None)
    return dt

def _build_support_faq_html(webapp_config: dict | None) -> str:
    support_cfg = (webapp_config or {}).get("support") if isinstance(webapp_config, dict) else {}
    if not isinstance(support_cfg, dict):
        support_cfg = {}
    raw = str(support_cfg.get("faq_raw") or "").strip()
    if not raw:
        return ""

    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    blocks = re.split(r"(?m)^\s*---\s*$", raw)
    items = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [ln for ln in block.split("\n")]
        question = lines[0].strip()
        answer = "\n".join(lines[1:]).strip()
        if not question:
            continue
        items.append((question, answer))

    if not items:
        return ""

    html = ""
    for question, answer in items:
        answer_html = _esc(answer).replace("\n", "<br>")
        html += f"""
            <div class="feature-card overflow-hidden">
                <button type="button" onclick="toggleFaqItem(this)" class="w-full p-3 text-left flex items-center gap-2">
                    <span class="material-icons-round text-primary text-[18px] shrink-0">help_outline</span>
                    <span class="flex-1 text-[12px] font-bold text-white leading-snug">{_esc(question)}</span>
                    <span class="faq-item-chevron material-icons-round text-gray-400 text-[18px] shrink-0 transition-transform">expand_more</span>
                </button>
                <div class="hidden px-3 pb-3 text-[11px] text-gray-300 leading-relaxed">{answer_html}</div>
            </div>
        """
    return html


def _process_template_placeholders(html: str, user_id: int, webapp_settings: dict, context_data: dict) -> str:
    title = webapp_settings.get("webapp_title") or get_setting("panel_brand_title") or "CABINET VPN"
    support_username = (get_setting("support_bot_username") or "").strip().lstrip("@")
    support_bot_tme_url = f"https://t.me/{support_username}" if support_username else ""
    webapp_cfg = webapp_settings.get("webapp_config") if isinstance(webapp_settings, dict) else {}
    if not isinstance(webapp_cfg, dict):
        webapp_cfg = {}

    def _cfg(path: str, default: str = "") -> str:
        node = webapp_cfg
        for part in path.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(part)
        if node is None:
            return default
        return str(node)

    # Метка аккаунта для десктоп-плашки (email веб-юзера или @username)
    try:
        _acc_user = get_user(user_id) if user_id else None
    except Exception:
        _acc_user = None
    _acc_email = ((_acc_user or {}).get("auth_email") or "").strip()
    _acc_uname = ((_acc_user or {}).get("username") or "").strip()
    _acc_label = _acc_email or (("@" + _acc_uname) if _acc_uname else "Аккаунт")
    _acc_initials = (_acc_email[:2] or _acc_uname[:2] or "AL").upper()

    replacements = {
        "{{ panel_brand_title }}": title,
        "{{ webapp_account_label }}": _acc_label,
        "{{ webapp_account_initials }}": _acc_initials,
        "{{ panel_miniapp_subtitle }}": _cfg("common.miniapp_subtitle", "мини-приложение"),
        "{{ common_back_text }}": _cfg("common.back_text", "Назад"),
        "{{ user_profile_card }}": context_data.get("profile_card", ""),
        "{{ profile_referral_block }}": context_data.get("profile_referral_block", ""),
        "{{ profile_install_section }}": context_data.get("profile_install_section", ""),
        "{{ profile_devices_section }}": context_data.get("profile_devices_section", ""),
        "{{ home_trial_cta }}": context_data.get("home_trial_cta", ""),
        "{{ key_info_section }}": context_data.get("key_section", ""),
        "{{ profile_keys_list }}": context_data.get("profile_keys_list", ""),
        "{{ setup_keys_list }}": context_data.get("setup_keys_list", ""),
        "{{ renew_keys_dropdown_options }}": context_data.get("renew_keys_options", ""),
        "{{ renew_plans_grid }}": context_data.get("renew_plans_html_data", ""),
        "{{ support_bot_username }}": support_username,
        "{{ support_bot_tme_url }}": support_bot_tme_url,
        "{{ support_inapp_hidden_class }}": "hidden" if (support_username or "").strip() else "",
        "{{ support_bot_panel_hidden_class }}": "" if (support_username or "").strip() else "hidden",
        "{{ support_bot_cta_title }}": _cfg("support.bot_cta_title", "Чат с поддержкой в Telegram"),
        "{{ support_bot_cta_subtitle }}": _cfg(
            "support.bot_cta_subtitle",
            "Тикеты ведутся в @SpectraSokol_Support_bot — быстрее и удобнее, чем в мини-приложении.",
        ),
        "{{ support_bot_open_button_text }}": _cfg("support.bot_open_button_text", "Открыть чат поддержки"),
        "{{ min_price }}": context_data.get("min_price", "0 ₽"),
        "{{ webapp_logo }}": context_data.get("webapp_logo", ""),
        "{{ webapp_icon }}": context_data.get("webapp_icon", ""),
        "{{ logo_hidden }}": "hidden" if not context_data.get("webapp_logo") else "",
        "{{ user_id }}": str(user_id),
        "{{ home_buy_button_prefix }}": _cfg("home.buy_button_prefix", "Купить подписку"),
        "{{ home_buy_button_price_prefix }}": _cfg("home.buy_button_price_prefix", "от"),
        "{{ home_renew_button_text }}": _cfg("home.renew_button_text", "Продлить ключ VPN"),
        "{{ home_setup_button_text }}": _cfg("home.setup_button_text", "Установка и настройка"),
        "{{ home_setup_tooltip_text }}": _cfg("home.setup_tooltip_text", "Сначала нужно настроить VPN на вашем устройстве"),
        "{{ home_profile_button_text }}": _cfg("home.profile_button_text", "Профиль"),
        "{{ home_support_button_text }}": _cfg("home.support_button_text", "Поддержка"),
        "{{ home_renew_visible_class }}": context_data.get("home_renew_visible_class", "hidden"),
        "{{ profile_keys_hidden_class }}": context_data.get("profile_keys_hidden_class", "hidden-empty"),
        "{{ support_telegram_link_hidden_class }}": "hidden-empty" if not (support_username or "").strip() else "",
        "{{ purchase_title }}": _cfg("purchase.title", "Покупка подписки"),
        "{{ purchase_subtitle }}": _cfg("purchase.subtitle", "Подключайте больше устройств и пользуйтесь сервисом вместе с друзьями и близкими"),
        "{{ purchase_pay_button_text }}": _cfg("purchase.pay_button_text", "Оплатить подписку"),
        "{{ terms_url }}": (get_setting("terms_url") or "https://teletype.in/@spectrasokol/7KqBCZUPDAX"),
        "{{ privacy_url }}": (get_setting("privacy_url") or "https://teletype.in/@spectrasokol/S-GEwiM8CIE"),
        "{{ purchase_server_label_text }}": _cfg("purchase.server_label_text", "Локация сервера"),
        "{{ purchase_server_placeholder_text }}": _cfg("purchase.server_placeholder_text", "Выберите сервер"),
        "{{ purchase_devices_label_text }}": _cfg("purchase.devices_label_text", "Устройств"),
        "{{ purchase_devices_hint_text }}": _cfg("purchase.devices_hint_text", "Одновременно в подписке"),
        "{{ purchase_info_title }}": _cfg("purchase.info_title", "Информация"),
        "{{ purchase_info_card_hidden_class }}": "hidden" if str(_cfg("purchase.show_info_card", "0")).strip().lower() not in ("1", "true", "yes", "on") else "",
        "{{ purchase_device_card_title }}": _cfg("purchase.device_card_title", "Устройство"),
        "{{ purchase_device_card_title_one }}": _cfg("purchase.device_card_title_one", _cfg("purchase.device_card_title", "Устройство")),
        "{{ purchase_device_card_title_few }}": _cfg("purchase.device_card_title_few", "Устройства"),
        "{{ purchase_device_card_title_many }}": _cfg("purchase.device_card_title_many", "Устройств"),
        "{{ purchase_device_card_subtitle }}": _cfg("purchase.device_card_subtitle", "Одновременно в подписке"),
        "{{ purchase_discount_badges_json }}": context_data.get("purchase_discount_badges_json", "{}"),
        "{{ device_tiers_bootstrap_json }}": context_data.get("device_tiers_bootstrap_json", "{}"),
        "{{ renew_pay_button_text }}": _cfg("renew.pay_button_text", "Продлить подписку"),
        "{{ renew_info_title }}": _cfg("renew.info_title", "Информация"),
        "{{ renew_selected_key_label_text }}": _cfg("renew.selected_key_label_text", "Выбранный ключ"),
        "{{ renew_devices_label_text }}": _cfg("renew.devices_label_text", "Устройств"),
        "{{ renew_devices_hint_text }}": _cfg("renew.devices_hint_text", "Одновременно в подписке"),
        "{{ setup_title }}": _cfg("setup.title", "Настройка устройства"),
        "{{ setup_subtitle }}": _cfg("setup.subtitle", "Настройка VPN происходит в 3 шага и занимает пару минут"),
        "{{ setup_active_keys_title }}": _cfg("setup.active_keys_title", "Ваши действующие ключи"),
        "{{ setup_footer_text }}": _cfg("setup.footer_text", "Нужна помощь? Обратитесь в поддержку"),
        "{{ setup_instruction_title }}": _cfg("setup.instruction_title", "Инструкция"),
        "{{ setup_instruction_body }}": _cfg("setup.instruction_body", "Нажмите «Открыть инструкцию» для получения персонального руководства по настройке на вашем устройстве."),
        "{{ setup_wizard_primary_button }}": _cfg("setup.wizard_primary_button", "Начать настройку на этом устройстве"),
        "{{ setup_wizard_secondary_button }}": _cfg("setup.wizard_secondary_button", "Установить на другом устройстве"),
        "{{ setup_empty_title }}": _cfg("setup.empty_title", "Подготовьте подключение"),
        "{{ setup_empty_subtitle }}": _cfg("setup.empty_subtitle", "Откройте ключ, установите приложение и подключитесь за пару минут."),
        "{{ setup_wizard_config_json }}": context_data.get("setup_wizard_config_json", "{}"),
        "{{ profile_keys_title }}": _cfg("profile.keys_title", "Ваши ключи"),
        "{{ profile_promo_button_text }}": _cfg("profile.promo_button_text", "Ввести промокод"),
        "{{ profile_logout_button_text }}": _cfg("profile.logout_button_text", "Выйти из аккаунта"),
        "{{ profile_install_hero_title }}": _cfg("profile.install_hero_title", "Установка на другом устройстве"),
        "{{ profile_install_hero_subtitle }}": _cfg("profile.install_hero_subtitle", "Выберите платформу для подключения на другом устройстве"),
        "{{ profile_subscription_link_label }}": _cfg("profile.subscription_link_label", "Ваша ссылка на подписку"),
        "{{ support_header_title }}": _cfg("support.header_title", "Служба заботы"),
        "{{ support_create_title }}": _cfg("support.create_title", "Нужна помощь?"),
        "{{ support_create_subtitle }}": _cfg("support.create_subtitle", "Опишите кратко вашу проблему в форме ниже, чтобы мы могли быстрее вам помочь."),
        "{{ support_subject_placeholder }}": _cfg("support.subject_placeholder", "Например: Не работает VPN"),
        "{{ support_message_placeholder }}": _cfg("support.message_placeholder", "Сообщение..."),
        "{{ support_start_chat_button_text }}": _cfg("support.start_chat_button_text", "Начать чат"),
        "{{ support_telegram_button_text }}": _cfg("support.telegram_button_text", "Написать в Telegram"),
        "{{ support_response_time_text }}": _cfg("support.response_time_text", "Среднее время ответа: 50 минут"),
        "{{ support_loading_text }}": _cfg("support.loading_text", "Загрузка информации..."),
        "{{ support_faq_title }}": _cfg("support.faq_title", "Часто задаваемые вопросы"),
        "{{ support_faq_subtitle }}": _cfg("support.faq_subtitle", "Ответы на часто задаваемые вопросы"),
        "{{ support_faq_list }}": _build_support_faq_html(webapp_cfg),
        "{{ support_other_device_title }}": _cfg("support.other_device_title", "Установка на другом устройстве"),
        "{{ support_other_device_subtitle }}": _cfg("support.other_device_subtitle", "Подробная инструкция для установки"),
        "{{ support_contact_title }}": _cfg("support.contact_title", "Поддержка"),
        "{{ support_contact_subtitle }}": _cfg("support.contact_subtitle", "Связаться с поддержкой"),
        "{{ support_quick_section_title }}": _cfg("support.quick_section_title", "Связаться с поддержкой"),
        "{{ support_quick_section_subtitle }}": _cfg(
            "support.quick_section_subtitle",
            "Ответы на частые вопросы ниже. Переписка с поддержкой — в Telegram-боте."
            if (support_username or "").strip()
            else "Получите ответы на популярные вопросы или обратитесь к нам за помощью",
        ),
        "{{ support_closed_ticket_text }}": _cfg("support.closed_ticket_text", "Это обращение закрыто."),
        "{{ support_empty_thread_text }}": _cfg("support.empty_thread_text", "Опишите проблему — оператор скоро ответит."),
        "{{ support_open_ticket_title }}": _cfg("support.open_ticket_title", "Открыть текущее обращение"),
        "{{ support_open_ticket_subtitle }}": _cfg("support.open_ticket_subtitle", "У вас есть открытый диалог с поддержкой"),
        "{{ support_new_ticket_button_text }}": _cfg("support.new_ticket_button_text", "Создать новое обращение"),
        "{{ support_close_ticket_button_text }}": _cfg("support.close_ticket_button_text", "Закрыть обращение"),
        "{{ support_close_ticket_confirm_text }}": _cfg("support.close_ticket_confirm_text", "Закрыть это обращение? Вы сможете создать новое, если вопрос не решён."),
        "{{ common_or_separator_text }}": _cfg("common.or_separator", "Или"),
        "{{ common_expand_text }}": _cfg("common.expand_text", "Развернуть ▼"),
        "{{ common_collapse_text }}": _cfg("common.collapse_text", "Свернуть ▲"),
        "{{ common_devices_modal_title }}": _cfg("common.devices_modal_title", "Устройства"),
        "{{ common_comment_modal_title }}": "Название ключа",
        "{{ common_comment_placeholder }}": "Напр.: Мама, Папа, Брат",
        "{{ common_menu_refresh_text }}": _cfg("common.menu_refresh_text", "Обновить"),
        "{{ common_menu_logout_text }}": _cfg("common.menu_logout_text", "Выйти"),
        "{{ common_devices_empty_text }}": _cfg("common.devices_empty_text", "Нет активных устройств"),
        "{{ common_device_rename_title }}": _cfg("common.device_rename_title", "Название устройства"),
        "{{ common_device_rename_placeholder }}": _cfg("common.device_rename_placeholder", "Напр: iPhone мамы"),
        "{{ common_device_rename_save_text }}": _cfg("common.device_rename_save_text", "Сохранить"),
        "{{ common_device_renamed_text }}": _cfg("common.device_renamed_text", "Название сохранено"),
        "{{ common_trial_activated_text }}": _cfg("common.trial_activated_text", "Пробный доступ активирован!"),
        "{{ common_payment_check_text }}": _cfg("common.payment_check_text", "Я оплатил — проверить"),
        "{{ common_payment_not_yet_text }}": _cfg("common.payment_not_yet_text", "Оплата ещё не поступила. Если вы только что оплатили — подождите минуту и проверьте снова."),
        "{{ common_network_error_text }}": _cfg("common.network_error_text", "Ошибка сети"),
        "{{ common_comment_save_button_text }}": _cfg("common.comment_save_button_text", "Сохранить"),
        "{{ common_error_text }}": _cfg("common.error_text", "Ошибка"),
        "{{ common_connection_error_text }}": _cfg("common.connection_error_text", "Ошибка связи"),
        "{{ common_promo_hint_text }}": _cfg("common.promo_hint_text", "Введите бонусный промокод на баланс или дни"),
        "{{ common_promo_placeholder_text }}": _cfg("common.promo_placeholder_text", "Напр: BONUS2024"),
        "{{ common_promo_apply_text }}": _cfg("common.promo_apply_text", "Применить"),
        "{{ common_promo_activate_text }}": _cfg("common.promo_activate_text", "Активировать"),
        "{{ common_promo_applied_text }}": _cfg("common.promo_applied_text", "Промокод применен!"),
        "{{ common_promo_not_found_text }}": _cfg("common.promo_not_found_text", "Промокод не найден"),
        "{{ common_promo_activated_text }}": _cfg("common.promo_activated_text", "Промокод активирован!"),
        "{{ common_payment_select_method_text }}": _cfg("common.payment_select_method_text", "Выберите способ оплаты"),
        "{{ common_payment_confirm_title }}": _cfg("common.payment_confirm_title", "Подтверждение оплаты"),
        "{{ common_payment_methods_title }}": _cfg("common.payment_methods_title", "Изменить способ оплаты"),
        "{{ common_payment_new_card_text }}": _cfg("common.payment_new_card_text", "Оплата новой картой"),
        "{{ common_payment_devices_count_prefix }}": _cfg("common.payment_devices_count_prefix", "Количество устройств:"),
        "{{ common_payment_subscription_prefix }}": _cfg("common.payment_subscription_prefix", "Подписка до"),
        "{{ common_payment_pay_prefix }}": _cfg("common.payment_pay_prefix", "Оплатить"),
        "{{ common_payment_waiting_title }}": _cfg("common.payment_waiting_title", "Ожидаем оплату"),
        "{{ common_payment_waiting_desc }}": _cfg("common.payment_waiting_desc", "Завершите оплату в открывшемся окне..."),
        "{{ common_payment_go_to_pay_text }}": _cfg("common.payment_go_to_pay_text", "Перейти к оплате"),
        "{{ common_payment_cancel_text }}": _cfg("common.payment_cancel_text", "Отменить"),
        "{{ common_device_deleted_text }}": _cfg("common.device_deleted_text", "Устройство удалено"),
        "{{ common_comment_saved_text }}": "Название сохранено!",
        "{{ common_support_subject_required_text }}": _cfg("common.support_subject_required_text", "Укажите причину обращения"),
        "{{ common_payment_link_missing_text }}": _cfg("common.payment_link_missing_text", "Ссылка на оплату не получена."),
        "{{ tg_fullscreen_css }}": """
    <style>
        .tg-miniapp #main-page {
            padding-top: max(env(safe-area-inset-top), 2px) !important;
        }
        .tg-miniapp #purchase-page,
        .tg-miniapp #renew-page,
        .tg-miniapp #setup-page,
        .tg-miniapp #profile-page,
        .tg-miniapp #support-page {
            padding-top: env(safe-area-inset-top, 0px) !important;
        }
    </style>
        """ if webapp_settings.get("tg_fullscreen") else "",
    }
    
    # Selected key display variants
    display_val = context_data.get("renew_selected_display", "Нет активных ключей")
    replacements["{{ renew_selected_key_display }}"] = display_val
    replacements["{{\n                                renew_selected_key_display }}"] = display_val

    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    
    server_options, server_plans = _get_servers_and_plans_html(user_id, webapp_cfg)
    html = html.replace("{{ server_dropdown_options }}", server_options)
    html = html.replace("{{ server_plans_grid }}", server_plans)
    
    return html

def _process_key_data(key: dict) -> dict:
    # 1. Calculate expiry
    now = get_msk_time().replace(tzinfo=None)
    expire_raw = key.get('expiry_date') or key.get('expire_at')
    created_raw = key.get('created_at') or expire_raw

    expire_dt = _parse_key_datetime(expire_raw)
    created_dt = _parse_key_datetime(created_raw)

    if expire_dt is None:
        expire_dt = now
        expire_date_str = "Unknown"
    else:
        expire_date_str = expire_dt.strftime("%d.%m.%Y")

    if created_dt is None:
        created_dt = expire_dt
    
    # 2. Days left & Detailed remaining
    delta = expire_dt - now
    days_left = delta.days
    if days_left < 0:
        days_left = 0
    # Ключ ещё активен, но остаток < суток → delta.days == 0. Раньше это давало
    # «Истек / подписка истекла / offline» у ЖИВОГО ключа (days_left==0 трактовался как истёк).
    # Показываем минимум 1 день, пока ключ реально не истёк (по времени, а не по целым суткам).
    if days_left == 0 and delta.total_seconds() > 0:
        days_left = 1

    remaining_str = _format_remaining_details(delta) if delta.total_seconds() > 0 else "Истек"

    # 3. Progress
    total_duration = (expire_dt - created_dt).total_seconds()
    elapsed_delta = now - created_dt
    elapsed = elapsed_delta.total_seconds()
    elapsed_str = _format_remaining_details(elapsed_delta) if elapsed > 0 else "0мин"
    
    if total_duration > 0:
        percent = (elapsed / total_duration) * 100
    else:
        percent = 100
        
    percent = max(0, min(100, percent))
    percent_str = f"{percent:.1f}%"
    
    # 4. Display Name
    # Имя по умолчанию «Ключ #N» (по порядку списка), иначе старый фолбэк
    num = key.get('display_number')
    if num:
        default_name = f"Ключ #{num}"
    else:
        email = key.get('email') or key.get('key_email') or ""
        if email.endswith("@bot.local"):
            email = email[:-10]
        if email:
            default_name = f"Ключ #{email}"
        elif key.get('short_uuid'):
            default_name = f"Ключ #{key.get('short_uuid')}"
        else:
            default_name = f"Ключ #{key.get('key_id')}"
    # Заголовок ключа = пользовательское имя (comment_key) ИЛИ «Ключ #N». comment_key экранируем (ввод юзера).
    custom = (key.get('comment_key') or '').strip()
    if custom:
        custom = custom.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    key_name = custom or key.get('name') or default_name
        
    # 5. Subscription URL
    sub_url = key.get('subscription_url') or key.get('key') or ""

    # 6. Limits
    traffic_limit = key.get('limit_bytes')
    traffic_used = key.get('used_bytes', 0)
    
    formatted_used = _format_bytes(traffic_used)
    
    traffic_str = "∞"
    if traffic_limit:
        try:
            t_lim_float = float(traffic_limit)
            if t_lim_float > 0:
                traffic_str = _format_bytes(t_lim_float)
            else:
                traffic_str = "∞"
        except (ValueError, TypeError):
            traffic_str = "∞"
    
    hwid_limit = key.get('limit_ips')
    hwid_usage = key.get('used_ips', 0)
    
    limit_display = "∞"
    if hwid_limit is not None:
        try:
            limit_val = int(hwid_limit)
            if limit_val > 0 and limit_val < 99:
                 limit_display = str(limit_val)
            else:
                 limit_display = "∞"
        except (ValueError, TypeError):
            limit_display = "∞"

    hwid_str = f"{hwid_usage} / {limit_display}"

    # Числовые значения для авто-подстановки тарифа при продлении (и хинта «📱 N»).
    try:
        hwid_limit_num = int(hwid_limit) if hwid_limit is not None else 0
    except (ValueError, TypeError):
        hwid_limit_num = 0
    if not (0 < hwid_limit_num < 99):
        hwid_limit_num = 0
    try:
        hwid_usage_num = int(hwid_usage or 0)
    except (ValueError, TypeError):
        hwid_usage_num = 0

    # Safety: Created Date String
    created_date_str = created_dt.strftime("%d.%m.%Y")

    if days_left > 5:
        status_text = "Активен"
        status_color = "text-purple-500"
        status_bg = "bg-purple-500/10"
    elif days_left > 0:
        status_text = "Скоро"
        status_color = "text-yellow-500"
        status_bg = "bg-yellow-500/10"
    else:
        status_text = "Истек"
        status_color = "text-red-500"
        status_bg = "bg-red-500/10"

    return {
        "key_id": key.get('key_id'),
        "name": key_name,
        "default_name": default_name,
        "expire_date_str": expire_date_str,
        "days_left": days_left,
        "percent_str": percent_str,
        "sub_url": sub_url,
        "expiry_dt": expire_dt,
        "remaining_str": remaining_str,
        "created_date_str": created_date_str,
        "elapsed_str": elapsed_str,
        "traffic_info": f"{formatted_used} / {traffic_str}", 
        "hwid_info": f"{hwid_str} уст.",
        "hwid_usage": hwid_usage_num,
        "hwid_limit_num": hwid_limit_num,
        "status_text": status_text,
        "status_color": status_color,
        "status_bg": status_bg,
        "comment_key": key.get('comment_key') or "",
        "host_name": key.get('host_name') or "",
    }

def _cfg_text(webapp_config: dict | None, path: str, default: str = "") -> str:
    node = webapp_config if isinstance(webapp_config, dict) else {}
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    if node is None:
        return default
    return str(node)


def _build_setup_wizard_config_json(webapp_config: dict | None, sub_url: str = "") -> str:
    setup_cfg = (webapp_config or {}).get("setup") if isinstance(webapp_config, dict) else {}
    if not isinstance(setup_cfg, dict):
        setup_cfg = {}

    def _s(key: str, default: str = "") -> str:
        val = setup_cfg.get(key)
        return str(val) if val is not None and str(val).strip() else default

    payload = {
        "subscriptionUrl": sub_url or "",
        "platformTitles": {
            "ios": _s("platform_title_ios", _s("title", "Настройка на iOS")),
            "android": _s("platform_title_android", "Настройка на Android"),
            "macos": _s("platform_title_macos", "Настройка на macOS"),
            "windows": _s("platform_title_windows", "Настройка на Windows"),
        },
        "storeUrls": {
            "ios": _s("step_app_store_url_ios", "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"),
            "android": _s("step_app_store_url_android", "https://play.google.com/store/apps/details?id=com.happproxy"),
            "macos": _s("step_app_store_url_macos", "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"),
            "windows": _s("step_app_store_url_windows", "https://github.com/Happ-proxy/happ-desktop/releases/latest"),
        },
        "appName": _s("app_name", "Happ"),
        "stepAppTitle": _s("step_app_title", "Приложение"),
        "stepAppSubtitle": _s("step_app_subtitle", "Установите приложение Happ из магазина приложений"),
        "stepAppButton": _s("step_app_button", "Скачать Happ"),
        "stepSubTitle": _s("step_sub_title", "Подписка"),
        "stepSubSubtitle": _s("step_sub_subtitle", "Добавьте подписку в приложение Happ одним нажатием"),
        "stepSubButton": _s("step_sub_button", "Добавить подписку"),
        "buySubButton": _s("buy_subscription_button", "Купить подписку"),
        "noSubscriptionSubtitle": _s("no_subscription_subtitle", "У вас пока нет активной подписки. Оформите её, чтобы продолжить настройку."),
        "noSubscriptionNotice": _s("no_subscription_notice", "Сначала оформите подписку"),
        "stepDoneTitle": _s("step_done_title", "Готово!"),
        "stepDoneSubtitle": _s("step_done_subtitle", "Подписка добавлена. Откройте Happ и подключитесь к VPN."),
        "stepDoneButton": _s("step_done_button", "Открыть Happ"),
        "stepDoneHomeButton": _s("step_done_home_button", "На главную"),
        "instructions": {
            "ios": _s("instruction_ios",
                "Установите приложение Happ из App Store\n"
                "Вернитесь сюда и нажмите «Добавить подписку»\n"
                "Разрешите добавление VPN-конфигурации\n"
                "Откройте Happ и нажмите «Подключиться»"),
            "android": _s("instruction_android",
                "Установите приложение Happ из Google Play\n"
                "Вернитесь сюда и нажмите «Добавить подписку»\n"
                "Разрешите добавление VPN-конфигурации\n"
                "Откройте Happ и нажмите «Подключиться»"),
            "macos": _s("instruction_macos",
                "Установите приложение Happ из App Store\n"
                "Вернитесь сюда и нажмите «Добавить подписку»\n"
                "Разрешите добавление VPN-конфигурации\n"
                "Откройте Happ и нажмите «Подключиться»"),
            "windows": _s("instruction_windows",
                "Скачайте Happ для Windows по кнопке ниже\n"
                "Установите и запустите приложение\n"
                "Скопируйте ссылку на подписку и импортируйте её в Happ\n"
                "Нажмите «Подключиться»"),
        },
        "instructionStoreButton": _s("instruction_store_button", "Скачать приложение"),
        "instructionAddSubButton": _s("instruction_add_sub_button", "Добавить подписку"),
        "instructionCopyLinkButton": _s("instruction_copy_link_button", "Скопировать ссылку"),
        "instructionNoSubText": _s("instruction_no_sub_text", "Сначала оформите подписку, чтобы получить ссылку."),
    }
    return json.dumps(payload, ensure_ascii=False)


def _format_expire_date_ru(expire_dt, webapp_config: dict | None = None) -> str:
    months_ru = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    prefix = _cfg_text(webapp_config, "home.date_prefix", "до").strip() or "до"
    if expire_dt:
        return f"{prefix} {expire_dt.day} {months_ru[expire_dt.month - 1]} {expire_dt.year}"
    return f"{prefix} —"


def _get_simple_empty_html(title: str, subtitle: str = "") -> str:
    subtitle_html = f'<p class="text-[10px] text-gray-400 font-medium leading-snug max-w-[220px] mx-auto">{subtitle}</p>' if subtitle else ""
    return f"""
    <div class="feature-card p-4 flex flex-col items-center justify-center text-center mb-2">
        <div class="w-12 h-12 bg-white/5 rounded-2xl flex items-center justify-center mb-3">
            <span class="material-icons-round text-2xl text-gray-500">vpn_key_off</span>
        </div>
        <h3 class="text-sm font-black text-white mb-1 tracking-tight">{title}</h3>
        {subtitle_html}
    </div>
    """


def _render_home_hero(key: dict | None = None, webapp_config: dict | None = None) -> str:
    is_expired = True
    if key:
        data = _process_key_data(key)
        is_expired = data["days_left"] <= 0
        date_display = _format_expire_date_ru(data.get("expiry_dt"), webapp_config)
        shield_class = "home-shield-wrap" + (" is-expired" if is_expired else "")
        grad_id = "spectraShieldGradExpired" if is_expired else "spectraShieldGrad"
    else:
        date_display = _cfg_text(webapp_config, "home.status_no_subscription", "нет подписки")
        shield_class = "home-shield-wrap is-empty"
        grad_id = "spectraShieldGradEmpty"

    status_line = _cfg_text(
        webapp_config,
        "home.status_offline" if is_expired else "home.status_online",
        "не в сети" if is_expired else "в сети",
    )
    status_line_class = "home-status-line is-offline" if is_expired else "home-status-line is-online"
    badge_text = _cfg_text(
        webapp_config,
        "home.status_badge_expired" if is_expired else "home.status_badge_active",
        "подписка истекла" if is_expired else "подписка активна",
    )
    badge_class = "home-badge-expired" if is_expired else "home-badge-active"

    # Данные для desktop-кольца (рисует JS-энхансер в app.html):
    # >=1 суток → дни (teal); <1 суток, но ключ ещё активен → часы (amber); истёк → 0 (red).
    ring_num, ring_unit, ring_state = "0", "дней", "expired"
    if key and not is_expired:
        exp = data.get("expiry_dt")
        if exp:
            _secs = (exp - get_msk_time().replace(tzinfo=None)).total_seconds()
            if _secs >= 86400:
                _d = int(_secs // 86400)
                ring_num, ring_unit, ring_state = str(_d), _ru_days_word(_d), "active"
            elif _secs > 0:
                _h = max(1, int((_secs + 3599) // 3600))
                ring_num, ring_unit, ring_state = str(_h), _ru_hours_word(_h), "soon"

    return f"""
    <section class="home-hero">
        <div class="home-hero-visual" data-ring-num="{ring_num}" data-ring-unit="{ring_unit}" data-ring-state="{ring_state}">
            <div class="home-rings" aria-hidden="true"></div>
            <div class="home-hero-logo" style="position:relative;z-index:2;display:flex;align-items:center;justify-content:center;">
                <img src="/module/ico/SpectraSokol-VPN-icon.png" alt="SpectraSokol"
                    style="width:108px;height:108px;border-radius:24px;object-fit:cover;display:block;filter:drop-shadow(0 0 20px rgba(192, 132, 252,0.30)) drop-shadow(0 10px 26px rgba(0,0,0,0.55));" />
            </div>
        </div>
        <div class="home-status-row">
            <div class="home-status-info">
                <div class="home-status-eyebrow">Статус подписки</div>
                <div class="home-status-title">{badge_text}</div>
                <div class="home-status-date">{date_display}</div>
                <div class="{status_line_class}">{status_line}</div>
            </div>
            <div class="home-status-badge {badge_class}">{badge_text}</div>
            <button type="button" class="home-hero-cta" onclick="document.getElementById('renew-btn').click()">
                <span class="material-icons-round">autorenew</span><span>Продлить ключ</span>
            </button>
        </div>
    </section>
    """


def _get_key_html(key: dict, webapp_config: dict | None = None) -> str:
    return _render_home_hero(key, webapp_config)


def _is_trial_enabled() -> bool:
    return str(get_setting("trial_enabled") or "true").strip().lower() not in ("false", "0", "no", "off")


def _webapp_trial_available(user_id: int | None) -> bool:
    if not user_id or not _is_trial_enabled():
        return False
    user = get_user(user_id)
    return bool(user) and not bool(user.get("trial_used"))


def _build_device_tiers_bootstrap_json() -> str:
    from shop_bot.data_manager import database

    try:
        hosts = get_all_hosts(visible_only=True)
    except Exception:
        hosts = []

    payload: dict[str, dict] = {}
    for host in hosts or []:
        host_name = host.get("host_name")
        if not host_name:
            continue
        host_data = get_host(host_name) or host
        mode = host_data.get("device_mode", "plan")
        lock = int(host_data.get("tier_lock_extend", 0) or 0)
        base_devices = int(database.get_setting(f"base_device_{host_name}", "1"))
        tiers: list[dict] = []
        if mode == "tiers":
            raw = get_device_tiers(host_name)
            tiers = [
                {
                    "tier_id": t["tier_id"],
                    "device_count": t["device_count"],
                    "price": float(t["price"]),
                }
                for t in raw
            ]
        # Матрица точных цен: {device_count: {months: price}} из тарифов с hwid_limit
        price_matrix: dict[str, dict[str, int]] = {}
        try:
            for p in get_plans_for_host(host_name):
                if not p.get("is_active"):
                    continue
                hw = int(p.get("hwid_limit") or 0)
                if hw <= 0:
                    continue
                mm = int(p.get("months") or 1)
                price_matrix.setdefault(str(hw), {})[str(mm)] = int(round(float(p.get("price") or 0)))
        except Exception:
            price_matrix = {}

        payload[host_name] = {
            "device_mode": mode,
            "tiers": tiers,
            "tier_lock_extend": lock,
            "base_device_count": base_devices,
            "price_matrix": price_matrix,
        }
    return json.dumps(payload, ensure_ascii=False)


def _ru_days_word(days: int) -> str:
    """Склонение слова «день» под число дней (1 день, 2 дня, 5 дней)."""
    return "день" if days % 10 == 1 and days % 100 != 11 else (
        "дня" if 2 <= days % 10 <= 4 and (days % 100 < 10 or days % 100 >= 20) else "дней"
    )


def _ru_hours_word(hours: int) -> str:
    """Склонение слова «час» под число часов (1 час, 2 часа, 5 часов)."""
    return "час" if hours % 10 == 1 and hours % 100 != 11 else (
        "часа" if 2 <= hours % 10 <= 4 and (hours % 100 < 10 or hours % 100 >= 20) else "часов"
    )


def _trial_duration_days() -> int:
    try:
        return int(get_setting("trial_duration_days") or 0) or 1
    except (TypeError, ValueError):
        return 1


def _build_trial_plan_card_html(webapp_config: dict | None = None) -> str:
    purchase_cfg = webapp_config.get("purchase") if isinstance(webapp_config, dict) else {}
    if not isinstance(purchase_cfg, dict):
        purchase_cfg = {}
    days = _trial_duration_days()
    days_word = _ru_days_word(days)
    title = str(purchase_cfg.get("trial_plan_title") or f"Пробный период {days} {days_word}")
    subtitle = str(purchase_cfg.get("trial_plan_subtitle") or "Бесплатный доступ, без оплаты")
    duration_hint = f"{days} {days_word} бесплатно"
    return f"""
            <button type="button"
                class="plan-btn trial-plan-card spectra-plan-card glass-card border-2 border-purple-400/50 rounded-[22px] p-3 flex flex-col text-left transition-all active:scale-[0.98] hover:border-purple-300/60 group relative overflow-hidden col-span-2"
                data-is-trial="1"
                onclick="claimTrial(this)">
                <div class="absolute inset-0 bg-purple-500/10 plan-card-glow"></div>
                <div class="relative z-10 flex items-start justify-between gap-1.5 w-full min-h-[22px]">
                    <div class="plan-label text-[15px] font-extrabold text-purple-50 leading-tight min-w-0 flex-1 pr-1">{title}</div>
                    <span class="trial-plan-badge">ТЕСТ · FREE</span>
                </div>
                <div class="relative z-10 mt-2 flex items-end gap-0.5">
                    <span class="plan-price text-[22px] font-black text-purple-300 leading-none">0</span>
                    <span class="text-[14px] font-semibold text-purple-200/90 leading-none">₽</span>
                </div>
                <div class="relative z-10 mt-1 text-[11px] font-medium text-purple-200/80">{subtitle} · {duration_hint}</div>
            </button>
    """


def _get_home_trial_cta_html(webapp_config: dict | None = None) -> str:
    days = _trial_duration_days()
    days_word = _ru_days_word(days)
    label = _cfg_text(webapp_config, "home.trial_button_text", f"Пробный период {days} {days_word}")
    hint = _cfg_text(webapp_config, "home.trial_button_hint", "Бесплатный доступ · без оплаты")
    return f"""
            <button id="trial-btn" type="button" onclick="claimTrial(this)"
                class="home-btn-trial w-full transition-transform active:scale-[0.98]">
                <span class="home-trial-shine" aria-hidden="true"></span>
                <span class="home-trial-icon" aria-hidden="true">
                    <span class="material-icons-round">card_giftcard</span>
                </span>
                <span class="home-trial-text">
                    <span class="home-trial-title">{label}</span>
                    <span class="home-trial-sub">{hint}</span>
                </span>
                <span class="home-trial-badge">FREE</span>
            </button>
    """


def _get_profile_install_section_html(sub_url: str = "", webapp_config: dict | None = None) -> str:
    profile_cfg = (webapp_config or {}).get("profile") if isinstance(webapp_config, dict) else {}
    if not isinstance(profile_cfg, dict):
        profile_cfg = {}
    hero_title = str(profile_cfg.get("install_hero_title") or "Установка на другом устройстве")
    hero_subtitle = str(profile_cfg.get("install_hero_subtitle") or "Выберите платформу для подключения на другом устройстве")
    link_label = str(profile_cfg.get("subscription_link_label") or "Ваша ссылка на подписку")
    safe_url = (sub_url or "").replace('"', "&quot;")
    display_url = safe_url or "—"
    copy_disabled = "" if sub_url else " opacity-50 pointer-events-none"

    platforms = [
        ("ios", "apple", str(profile_cfg.get("platform_ios_label") or "Инструкция для iOS")),
        ("android", "android", str(profile_cfg.get("platform_android_label") or "Инструкция для Android")),
        ("macos", "laptop_mac", str(profile_cfg.get("platform_macos_label") or "Инструкция для macOS")),
        ("windows", "grid_view", str(profile_cfg.get("platform_windows_label") or "Инструкция для Windows")),
    ]
    platform_cards = ""
    for slug, icon, label in platforms:
        platform_cards += f"""
        <button type="button" onclick="window.openPlatformInstruction && window.openPlatformInstruction('{slug}')"
            class="profile-platform-card">
            <span class="material-icons-round profile-platform-icon">{icon}</span>
            <span class="profile-platform-label">{label}</span>
        </button>
        """

    return f"""
    <div class="profile-install-section">
        <div class="feature-card profile-install-hero p-4 sm:p-5">
            <div class="w-10 h-10 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center mb-3">
                <span class="material-icons-round text-purple-200 text-xl">settings</span>
            </div>
            <h2 class="text-[22px] sm:text-[26px] leading-tight font-black text-white tracking-tight">{hero_title}</h2>
            <p class="mt-1.5 text-[12px] sm:text-[13px] text-purple-100/75 leading-snug">{hero_subtitle}</p>
        </div>
        <div class="profile-platform-grid">{platform_cards}</div>
        <div class="profile-sub-link-bar{copy_disabled}">
            <div class="min-w-0 flex-1">
                <div class="profile-sub-link-url truncate">{display_url}</div>
                <div class="profile-sub-link-label">{link_label}</div>
            </div>
            <button type="button" onclick="copyKey(this, '{safe_url}')"
                class="profile-sub-link-copy shrink-0">
                <span class="material-icons-round text-[18px]">content_copy</span>
            </button>
        </div>
    </div>
    """

def _get_profile_devices_section_html(keys: list, webapp_config: dict | None = None) -> str:
    if not keys:
        return ""

    title = _cfg_text(webapp_config, "profile.devices_section_title", "Мои устройства")
    subtitle = _cfg_text(webapp_config, "profile.devices_section_subtitle", "Устройства, подключённые к вашей подписке")
    manage_label = _cfg_text(webapp_config, "profile.devices_manage_button", "Управление")
    unit = _cfg_text(webapp_config, "profile.devices_count_unit", "уст.")
    empty_text = _cfg_text(webapp_config, "profile.devices_section_empty", "Нет активных подписок")

    rows = ""
    for key in keys:
        try:
            key_id = key.get("key_id")
            if key_id is None:
                continue
            host = (key.get("host_name") or "").replace("'", "\\'")
            name = (key.get("comment_key") or (f"Ключ #{key.get('display_number')}" if key.get('display_number') else (key.get("host_name") or "Подписка"))).replace("<", "&lt;").replace(">", "&gt;")

            used = key.get("used_ips", 0) or 0
            try:
                used = int(used)
            except (ValueError, TypeError):
                used = 0

            limit_raw = key.get("limit_ips")
            limit_display = "∞"
            if limit_raw is not None:
                try:
                    lv = int(limit_raw)
                    if 0 < lv < 99:
                        limit_display = str(lv)
                except (ValueError, TypeError):
                    limit_display = "∞"

            rows += f"""
            <button type="button" onclick="openActionModal('devices', {key_id}, '{host}')" class="profile-device-row">
                <span class="profile-device-row-icon material-icons-round">devices</span>
                <span class="profile-device-row-body">
                    <span class="profile-device-row-name">{name}</span>
                    <span class="profile-device-row-count">{used} / {limit_display} {unit}</span>
                </span>
                <span class="profile-device-row-action">
                    <span>{manage_label}</span>
                    <span class="material-icons-round text-[16px]">chevron_right</span>
                </span>
            </button>
            """
        except Exception:
            continue

    if not rows.strip():
        rows = f'<div class="text-center text-gray-500 py-3 text-xs font-medium">{empty_text}</div>'

    return f"""
    <div class="profile-devices-section">
        <div class="feature-card p-4 sm:p-5">
            <div class="flex items-center gap-3 mb-3">
                <div class="w-10 h-10 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
                    <span class="material-icons-round text-primary text-xl">devices</span>
                </div>
                <div class="min-w-0">
                    <h2 class="text-[16px] font-black text-white tracking-tight leading-tight">{title}</h2>
                    <p class="text-[11px] text-gray-400 leading-snug truncate">{subtitle}</p>
                </div>
            </div>
            <div class="flex flex-col gap-2">{rows}</div>
        </div>
    </div>
    """

def _get_profile_card_html(
    user: dict | None,
    referral_count: int,
    keys_count: int,
    referral_earned: float = 0.0,
    webapp_config: dict | None = None,
    tg_profile: dict | None = None,
) -> str:
    if not user:
        return ""

    from html import escape as html_escape

    lbl_user_id = _cfg_text(webapp_config, "profile.user_id_label", "ID пользователя")
    lbl_balance = _cfg_text(webapp_config, "profile.balance_label", "Баланс")
    lbl_referrals = _cfg_text(webapp_config, "profile.referrals_label", "Рефералы")
    lbl_income = _cfg_text(webapp_config, "profile.income_label", "Доход")
    lbl_keys = _cfg_text(webapp_config, "profile.keys_stat_label", "Ключи")
    lbl_referrals_unit = _cfg_text(webapp_config, "profile.referrals_unit", "чел.")
    lbl_keys_unit = _cfg_text(webapp_config, "profile.keys_unit", "шт.")
        
    user_id = user.get("telegram_id")
    balance = user.get("balance") or 0.0
    reg_date = user.get("registration_date")
    db_username = (user.get("username") or "").strip().lstrip("@")
    tg_profile = tg_profile if isinstance(tg_profile, dict) else {}
    tg_display_name = (tg_profile.get("display_name") or "").strip()
    tg_username = (tg_profile.get("username") or "").strip().lstrip("@")
    photo_url = (tg_profile.get("photo_url") or "").strip()

    if tg_display_name:
        fallback_display_name = tg_display_name
    elif db_username:
        fallback_display_name = f"@{db_username}"
    else:
        fallback_display_name = f"#{user_id}"

    profile_username = tg_username or db_username
    if profile_username:
        fallback_subtitle = f"@{profile_username} · #{user_id}"
    else:
        fallback_subtitle = f"{lbl_user_id} · #{user_id}"

    if photo_url:
        avatar_src = f"/api/profile-avatar?user_id={user_id}"
        avatar_block = f"""
                                <img id="profile-user-avatar" alt=""
                                    src="{html_escape(avatar_src, quote=True)}"
                                    class="w-10 h-10 rounded-full object-cover border border-primary/30" />
                                <div id="profile-user-avatar-fallback"
                                    class="w-10 h-10 bg-primary/10 rounded-full flex items-center justify-center border border-primary/20 hidden">
                                    <span class="material-icons-round text-primary text-[20px]">person</span>
                                </div>
        """
    else:
        avatar_block = """
                                <img id="profile-user-avatar" alt=""
                                    class="w-10 h-10 rounded-full object-cover border border-primary/30 hidden" />
                                <div id="profile-user-avatar-fallback"
                                    class="w-10 h-10 bg-primary/10 rounded-full flex items-center justify-center border border-primary/20">
                                    <span class="material-icons-round text-primary text-[20px]">person</span>
                                </div>
        """
    
    # Format currency: 1 240,50 ₽
    balance_str = f"{balance:,.2f}".replace(",", " ").replace(".", ",") + " ₽"
    earned_str = f"{referral_earned:,.2f}".replace(",", " ").replace(".", ",") + " ₽"
    bot_username_raw = (get_setting("telegram_bot_username") or "bot").strip().lstrip("@")
    bot_username = re.sub(r"[^A-Za-z0-9_]", "", bot_username_raw) or "bot"
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    referrals_on = str(get_setting("enable_referrals") or "true").strip().lower() not in ("false", "0", "no", "off")
    ref_share_text = _cfg_text(webapp_config, "profile.referral_share_text", "Подключайся к быстрому VPN — заходи по моей ссылке:")
    referral_block = ""
    if referrals_on:
        referral_block = f"""
                    <div class="flex items-center gap-1.5">
                        <button type="button" data-ref-link="{referral_link}" onclick="copyReferralLink(this)"
                            class="flex-1 min-w-0 bg-primary/5 border border-primary/10 rounded-xl p-2 flex items-center gap-2 hover:bg-primary/10 active:scale-[0.99] transition-all">
                            <span class="material-icons-round text-[15px] text-primary shrink-0">ios_share</span>
                            <div class="min-w-0 flex-1 text-left">
                                <div class="text-[8px] text-gray-500 uppercase font-black tracking-widest">Реферальная ссылка</div>
                                <div class="text-[10px] text-gray-300 font-mono truncate">{referral_link}</div>
                            </div>
                            <span class="material-icons-round text-[14px] text-gray-500 shrink-0">content_copy</span>
                        </button>
                        <button type="button" data-ref-link="{referral_link}" data-share-text="{ref_share_text}" onclick="shareReferralLink(this)"
                            class="shrink-0 h-full px-3 py-2 bg-purple-500/15 border border-purple-500/40 rounded-xl flex items-center justify-center text-purple-200 hover:bg-purple-500/25 active:scale-[0.97] transition-all">
                            <span class="material-icons-round text-[18px]">send</span>
                        </button>
                    </div>
        """
    
    # Format date and calculate time since
    reg_date_str = "Unknown"
    time_since_str = ""
    if reg_date:
        try:
             if isinstance(reg_date, str):
                 try:
                    dt = datetime.strptime(reg_date, "%Y-%m-%d %H:%M:%S")
                 except ValueError:
                    dt = datetime.fromisoformat(reg_date)
             else:
                 dt = reg_date
                 
             reg_date_str = dt.strftime("%d.%m.%Y")
             
             # Calculate relative time
             now = get_msk_time().replace(tzinfo=None)
             diff = now - dt.replace(tzinfo=None)
             days = max(0, diff.days)
             
             if days < 31:
                 time_since_str = f"{days} д."
             elif days < 365:
                 m = days // 30
                 d = days % 30
                 time_since_str = f"{m}м. {d}д." if d > 0 else f"{m}м."
             else:
                 y = days // 365
                 rem = days % 365
                 m = rem // 30
                 d = rem % 30
                 bits = [f"{y}г."]
                 if m > 0: bits.append(f"{m}м.")
                 if d > 0: bits.append(f"{d}д.")
                 time_since_str = " ".join(bits)
        except:
             pass

    sync_btn_html = ""
    if isinstance(user_id, int) and str(user_id).startswith("999"):
         bot_username = get_setting("telegram_bot_username") or "bot"
         sync_btn_html = f'''
                    <button onclick="syncTelegram('{bot_username}')" class="mt-2 w-full bg-[#0088cc]/20 hover:bg-[#0088cc]/30 text-[#00aaff] border border-[#0088cc]/30 font-bold py-3 rounded-xl text-xs uppercase tracking-wider transition-all flex items-center justify-center gap-2 shadow-sm">
                        <span class="material-icons-round text-base">sync</span>
                        <span>Синхронизовать с Telegram</span>
                    </button>
         '''

    return f"""
            <!-- Modern Balanced User Card -->
            <div class="glass-card border border-white/10 rounded-[1.6rem] p-4 relative overflow-hidden shadow-xl">
                <!-- Decoration -->
                <div class="absolute -top-10 -right-10 w-32 h-32 bg-primary/5 rounded-full blur-3xl"></div>

                <div class="flex flex-col gap-3.5 relative z-10">
                    <!-- Top: Telegram profile and balance -->
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2.5 min-w-0">
                            <div id="profile-user-avatar-wrap" class="w-10 h-10 shrink-0 relative">
                                {avatar_block}
                            </div>
                            <div class="min-w-0">
                                <div id="profile-user-display-name"
                                    class="text-sm font-black text-white tracking-tight truncate">{html_escape(fallback_display_name)}</div>
                                <div id="profile-user-subtitle"
                                    class="text-[9px] text-gray-500 uppercase font-black tracking-widest truncate">{html_escape(fallback_subtitle)}</div>
                            </div>
                        </div>
                        <div class="text-right">
                            <div class="text-[9px] text-gray-500 uppercase font-black tracking-widest">{lbl_balance}</div>
                            <div class="text-base font-black text-primary tracking-tighter">{balance_str}</div>
                        </div>
                    </div>

                    <!-- Middle: Main Stats -->
                    <div class="grid grid-cols-3 gap-1.5">
                        <div
                            class="bg-white/5 border border-white/5 rounded-xl p-2 flex flex-col items-center justify-center text-center transition-all hover:bg-white/[0.08]">
                            <span class="material-icons-round text-purple-400 text-[13px] mb-0.5 opacity-80">group</span>
                            <div class="text-[8px] text-gray-400 uppercase font-black tracking-tight leading-none mb-0.5">{lbl_referrals}</div>
                            <div class="text-[10px] font-black text-white">{referral_count} {lbl_referrals_unit}</div>
                        </div>
                        <div
                            class="bg-white/5 border border-white/5 rounded-xl p-2 flex flex-col items-center justify-center text-center transition-all hover:bg-white/[0.08]">
                            <span class="material-icons-round text-yellow-400 text-[13px] mb-0.5 opacity-80">payments</span>
                            <div class="text-[8px] text-gray-400 uppercase font-black tracking-tight leading-none mb-0.5">{lbl_income}</div>
                            <div class="text-[10px] font-black text-white truncate w-full px-1">{earned_str}</div>
                        </div>
                        <div
                            class="bg-white/5 border border-white/5 rounded-xl p-2 flex flex-col items-center justify-center text-center transition-all hover:bg-white/[0.08]">
                            <span class="material-icons-round text-primary text-[13px] mb-0.5 opacity-80">vpn_key</span>
                            <div class="text-[8px] text-gray-400 uppercase font-black tracking-tight leading-none mb-0.5">{lbl_keys}</div>
                            <div class="text-[10px] font-black text-white">{keys_count} {lbl_keys_unit}</div>
                        </div>
                    </div>

                    <!-- Bottom: дата регистрации. Реф-ссылка и реф-программа вынесены ВНИЗ
                         профиля (под ключи/устройства) — см. _get_profile_referral_block_html -->
                    <div class="flex items-center justify-center gap-1.5">
                        <span class="material-icons-round text-[11px] text-gray-600">calendar_today</span>
                        <span class="text-[9px] text-gray-500 font-bold uppercase tracking-widest">Дата
                            регистрации:</span>
                        <span class="text-[9px] text-gray-300 font-black">{reg_date_str} ({time_since_str})</span>
                    </div>
                    {sync_btn_html}
                </div>
            </div>
    """

def _get_profile_referral_block_html(user: dict | None, webapp_config: dict | None = None) -> str:
    """Реферальный блок (ссылка + программа) — вынесен ВНИЗ профиля, под ключи и устройства,
    чтобы юзер сразу видел свои ключи/устройства. Возвращает '' если рефералка отключена."""
    if not user:
        return ""
    referrals_on = str(get_setting("enable_referrals") or "true").strip().lower() not in ("false", "0", "no", "off")
    if not referrals_on:
        return ""
    user_id = user.get("telegram_id")
    bot_username_raw = (get_setting("telegram_bot_username") or "bot").strip().lstrip("@")
    bot_username = re.sub(r"[^A-Za-z0-9_]", "", bot_username_raw) or "bot"
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    ref_share_text = _cfg_text(webapp_config, "profile.referral_share_text", "Подключайся к быстрому VPN — заходи по моей ссылке:")
    try:
        import html as _html
        from shop_bot.data_manager.database import referral_terms
        _rt = referral_terms()
        _ref_you, _ref_friend = _html.escape(_rt["you"]), _html.escape(_rt["friend"])
    except Exception:
        _ref_you, _ref_friend = "бонус с покупок друга", "скидку на первую подписку"
    return f"""
            <div class="glass-card border border-white/10 rounded-[1.6rem] p-4 relative overflow-hidden shadow-xl flex flex-col gap-3">
                <div class="flex items-center gap-1.5">
                    <button type="button" data-ref-link="{referral_link}" onclick="copyReferralLink(this)"
                        class="flex-1 min-w-0 bg-primary/5 border border-primary/10 rounded-xl p-2 flex items-center gap-2 hover:bg-primary/10 active:scale-[0.99] transition-all">
                        <span class="material-icons-round text-[15px] text-primary shrink-0">ios_share</span>
                        <div class="min-w-0 flex-1 text-left">
                            <div class="text-[8px] text-gray-500 uppercase font-black tracking-widest">Реферальная ссылка</div>
                            <div class="text-[10px] text-gray-300 font-mono truncate">{referral_link}</div>
                        </div>
                        <span class="material-icons-round text-[14px] text-gray-500 shrink-0">content_copy</span>
                    </button>
                    <button type="button" data-ref-link="{referral_link}" data-share-text="{ref_share_text}" onclick="shareReferralLink(this)"
                        class="shrink-0 h-full px-3 py-2 bg-purple-500/15 border border-purple-500/40 rounded-xl flex items-center justify-center text-purple-200 hover:bg-purple-500/25 active:scale-[0.97] transition-all">
                        <span class="material-icons-round text-[18px]">send</span>
                    </button>
                </div>
                <div class="bg-purple-500/5 border border-purple-500/15 rounded-xl p-3">
                    <div class="text-[11px] font-black text-purple-300 mb-1.5 text-center">🤝 Пригласи друга — выгодно обоим</div>
                    <div class="flex items-start gap-2 text-[10px] text-gray-200 leading-snug mb-1">
                        <span class="shrink-0">🎁</span><div><b>Ты получаешь:</b> {_ref_you}</div>
                    </div>
                    <div class="flex items-start gap-2 text-[10px] text-gray-200 leading-snug">
                        <span class="shrink-0">👋</span><div><b>Друг получает:</b> {_ref_friend}</div>
                    </div>
                </div>
            </div>
    """


def _should_hide_key(key: dict) -> bool:
    """Истёкший триал/тест-ключ скрываем из ЛК: истёк И (tag=TRIAL ИЛИ срок жизни ≤3 дней)."""
    try:
        data = _process_key_data(key)
        if data['days_left'] > 0:
            return False
        cr = _parse_key_datetime(key.get('created_at'))
        exp = data.get('expiry_dt')
        life = (exp - cr).days if (exp and cr) else 999
        return (key.get('tag') or '') == 'TRIAL' or life <= 3
    except Exception:
        return False


def _get_profile_keys_html(keys: list, webapp_config: dict | None = None) -> str:
    if not keys:
        profile_cfg = (webapp_config or {}).get("profile") if isinstance(webapp_config, dict) else {}
        if not isinstance(profile_cfg, dict):
            profile_cfg = {}
        return _get_simple_empty_html(
            str(profile_cfg.get("empty_keys_title") or "Нет ключей"),
            str(profile_cfg.get("empty_keys_subtitle") or "Купите ключ, чтобы начать пользоваться VPN"),
        )
    
    html = ""

    for key in keys:
        if _should_hide_key(key):
            continue
        data = _process_key_data(key)

        html += f"""
        <div class="glass-card border border-white/10 rounded-2xl relative overflow-hidden shadow-lg transition-all hover:border-primary/30 group mb-3">
            <div class="absolute inset-0 bg-gradient-to-r from-primary/0 via-primary/5 to-primary/0 translate-x-[-100%] group-hover:translate-x-[100%] transition-transform duration-700 pointer-events-none"></div>

            <button class="key-toggle w-full p-3 flex items-center justify-between relative z-10 transition-colors hover:bg-white/5">
                <div class="flex items-center gap-3">
                    <div class="w-9 h-9 bg-white/5 rounded-xl flex items-center justify-center group-hover:bg-primary/10 transition-colors shrink-0">
                        <span class="material-icons-round text-gray-400 group-hover:text-primary transition-colors text-lg">vpn_key</span>
                    </div>
                    
                    <div class="text-left overflow-hidden">
                        <div id="key-name-{data['key_id']}" data-default="{data['default_name']}" class="text-xs font-bold text-white group-hover:text-primary transition-colors truncate">{data['name']}</div>
                        <div class="text-[9px] text-gray-500 font-medium uppercase tracking-wider truncate">
                           До {data['expire_date_str']} ({data['remaining_str']})
                        </div>
                    </div>
                </div>

                <div class="flex items-center gap-2 shrink-0">
                     <span class="text-[9px] {data['status_bg']} {data['status_color']} px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">{data['status_text']}</span>
                     <div class="w-7 h-7 rounded-full bg-white/5 flex items-center justify-center group-hover:bg-primary/20 transition-colors">
                        <span class="material-icons-round text-gray-500 text-sm group-hover:text-white transition-colors rotate-icon">expand_more</span>
                     </div>
                </div>
            </button>

            <div class="key-content px-3 relative z-10 transition-all duration-300"> 
                 <div class="pb-3 pt-2 flex flex-col gap-2 border-t border-white/5">
                 
                     <!-- KEY INFO BLOCK -->
                     <div class="flex flex-col gap-1 px-1 py-1 text-[10px]">
                        <!-- Row 1: Time -->
                        <div class="flex flex-wrap justify-between items-center gap-x-2 gap-y-1 border-b border-white/5 pb-1.5 mb-1.5 opacity-90">
                            <div class="flex items-center gap-1">
                                <span class="text-gray-500 font-medium shrink-0">⏳ Осталось:</span>
                                <span class="text-gray-200 font-mono tracking-tight whitespace-nowrap">{data['remaining_str']}</span>
                            </div>
                            <div class="w-px h-3 bg-white/10"></div>
                            <div class="flex items-center gap-1">
                                <span class="text-gray-500 font-medium shrink-0">➕ Куплен:</span>
                                <span class="text-gray-200 font-mono tracking-tight whitespace-nowrap">{data['elapsed_str']}</span>
                            </div>
                        </div>
                        
                        <!-- Row 2: Limits -->
                        <div class="flex justify-between items-center opacity-90">
                            <div class="flex items-center gap-1.5">
                                <span class="text-gray-500 whitespace-nowrap">🛰 Лимит:</span>
                                <span class="text-gray-300 font-mono whitespace-nowrap">{data['traffic_info']}</span>
                            </div>
                            <div class="w-px h-3 bg-white/10 mx-1"></div>
                            <div class="flex items-center gap-1.5">
                                <span class="text-gray-500 whitespace-nowrap">📱 Лимит:</span>
                                <span class="text-gray-300 font-mono whitespace-nowrap">{data['hwid_info']}</span>
                            </div>
                        </div>
                     </div>
                 
                     <!-- COMMENTS BLOCK -->
                     <div id="comment-block-{data['key_id']}" class="hidden items-center opacity-90 px-1 py-1 mb-2 mt-1 relative">
                         <div class="w-1/2 flex items-center pr-2">
                             <span class="text-[9px] text-gray-500 font-bold uppercase tracking-wider whitespace-nowrap">Название:</span>
                         </div>
                         <div class="absolute left-1/2 -translate-x-1/2 w-px h-3 bg-white/10 shrink-0"></div>
                         <div class="w-1/2 pl-2 text-right overflow-hidden flex justify-end">
                             <span id="comment-text-{data['key_id']}" class="text-[10px] text-gray-300 break-words">{data.get('comment_key', '')}</span>
                         </div>
                     </div>

                     <div class="flex items-center gap-2 bg-black/20 rounded-xl p-2 border border-white/5 group/copy hover:border-primary/30 transition-colors">
                         <div class="flex-1 min-w-0">
                             <div class="text-[9px] text-gray-500 font-bold uppercase tracking-wider mb-0.5">Ссылка</div>
                             <div class="text-[10px] text-gray-300 font-mono truncate transition-colors group-hover/copy:text-white">{data['sub_url']}</div>
                         </div>
                         <button onclick="copyKey(this, '{data['sub_url']}')" 
                            class="w-7 h-7 rounded-lg bg-white/5 text-white flex items-center justify-center hover:bg-white/10 transition-all active:scale-95 shrink-0 shadow-sm">
                             <span class="material-icons-round text-sm">content_copy</span>
                         </button>
                     </div>

                     <button onclick="openLinkSafe('{data['sub_url']}')"
                        class="w-full bg-white text-black py-2.5 rounded-xl font-bold text-[10px] uppercase tracking-wider shadow-[0_4px_15px_rgba(255,255,255,0.1)] hover:shadow-[0_6px_20px_rgba(255,255,255,0.2)] active:scale-[0.98] transition-all flex items-center justify-center gap-2">
                         <span class="material-icons-round text-sm">bolt</span>
                         <span>Подключить</span>
                     </button>
                     
                     <div class="grid grid-cols-2 gap-2 mt-1">
                         <button onclick="openActionModal('devices', {data['key_id']}, '{data.get('host_name', '')}')"
                             class="w-full bg-white/5 text-white py-2 rounded-xl font-bold text-[10px] uppercase tracking-wider hover:bg-white/10 active:scale-[0.98] transition-all flex items-center justify-center gap-1.5 border border-white/5 hover:border-white/10">
                             <span class="material-icons-round text-sm">devices</span>
                             <span>Устройства</span>
                         </button>
                         <button onclick="openActionModal('comment', {data['key_id']}, '{data.get('comment_key', '')}')"
                             class="w-full bg-white/5 text-white py-2 rounded-xl font-bold text-[10px] uppercase tracking-wider hover:bg-white/10 active:scale-[0.98] transition-all flex items-center justify-center gap-1.5 border border-white/5 hover:border-white/10">
                             <span class="material-icons-round text-sm">edit_note</span>
                             <span>Название</span>
                         </button>
                     </div>
                     <button onclick="confirmDeleteKey({data['key_id']})"
                         class="w-full mt-2 bg-red-500/5 border border-red-500/15 text-red-400/90 py-2 rounded-xl font-bold text-[10px] uppercase tracking-wider hover:bg-red-500/15 active:scale-[0.98] transition-all flex items-center justify-center gap-1.5">
                         <span class="material-icons-round text-sm">delete_outline</span>
                         <span>Удалить ключ</span>
                     </button>
                </div>
            </div>
        </div>
        """
    return html

def _get_setup_keys_html(keys: list, webapp_config: dict | None = None) -> str:
    if not keys:
        setup_cfg = (webapp_config or {}).get("setup") if isinstance(webapp_config, dict) else {}
        if not isinstance(setup_cfg, dict):
            setup_cfg = {}
        return _get_simple_empty_html(
            str(setup_cfg.get("empty_keys_title") or "Нет активных ключей"),
            str(setup_cfg.get("empty_keys_subtitle") or "Купите подписку, чтобы начать настройку"),
        )

    setup_cfg = webapp_config.get("setup") if isinstance(webapp_config, dict) else {}
    if not isinstance(setup_cfg, dict):
        setup_cfg = {}
    open_btn_text = str(setup_cfg.get("open_button_text") or "Открыть инструкцию")
    devices_btn_text = str(setup_cfg.get("devices_button_text") or "Устройства")
    comments_btn_text = "Название"
        
    html = ""
    for key in keys:
        data = _process_key_data(key)
        
        if data['days_left'] <= 0:
            continue
            
        html += f"""
        <div class="glass-card border border-white/10 rounded-2xl relative overflow-hidden shadow-lg transition-all hover:border-primary/30 group mb-3">
            <div class="absolute inset-0 bg-gradient-to-r from-primary/0 via-primary/5 to-primary/0 translate-x-[-100%] group-hover:translate-x-[100%] transition-transform duration-700 pointer-events-none"></div>

            <button class="key-toggle w-full p-3 flex items-center justify-between relative z-10 transition-colors hover:bg-white/5">
                <div class="flex items-center gap-3">
                    <div class="w-9 h-9 bg-white/5 rounded-xl flex items-center justify-center group-hover:bg-primary/10 transition-colors shrink-0">
                        <span class="material-icons-round text-gray-400 group-hover:text-primary transition-colors text-lg">vpn_key</span>
                    </div>
                    
                    <div class="text-left overflow-hidden">
                        <div id="key-name-{data['key_id']}" data-default="{data['default_name']}" class="text-xs font-bold text-white group-hover:text-primary transition-colors truncate">{data['name']}</div>
                        <div class="text-[9px] text-gray-500 font-medium uppercase tracking-wider truncate">
                           До {data['expire_date_str']} ({data['remaining_str']})
                        </div>
                    </div>
                </div>

                <div class="flex items-center gap-2 shrink-0">
                     <span class="text-[9px] {data['status_bg']} {data['status_color']} px-2 py-0.5 rounded-full font-bold uppercase tracking-wider">{data['status_text']}</span>
                     <div class="w-7 h-7 rounded-full bg-white/5 flex items-center justify-center group-hover:bg-primary/20 transition-colors">
                        <span class="material-icons-round text-gray-500 text-sm group-hover:text-white transition-colors rotate-icon">expand_more</span>
                     </div>
                </div>
            </button>

            <div class="key-content px-3 relative z-10 transition-all duration-300"> 
                 <div class="pb-3 pt-2 flex flex-col gap-2 border-t border-white/5">
                 
                     <!-- COMMENTS BLOCK -->
                     <div id="comment-block-{data['key_id']}" class="hidden items-center opacity-90 px-1 py-1 mb-2 mt-1 relative">
                         <div class="w-1/2 flex items-center pr-2">
                             <span class="text-[9px] text-gray-500 font-bold uppercase tracking-wider whitespace-nowrap">Название:</span>
                         </div>
                         <div class="absolute left-1/2 -translate-x-1/2 w-px h-3 bg-white/10 shrink-0"></div>
                         <div class="w-1/2 pl-2 text-right overflow-hidden flex justify-end">
                             <span id="comment-text-{data['key_id']}" class="text-[10px] text-gray-300 break-words">{data.get('comment_key', '')}</span>
                         </div>
                     </div>

                     <div class="flex items-center gap-2 bg-black/20 rounded-xl p-2 border border-white/5 group/copy hover:border-primary/30 transition-colors">
                         <div class="flex-1 min-w-0">
                             <div class="text-[9px] text-gray-500 font-bold uppercase tracking-wider mb-0.5">Ссылка</div>
                             <div class="text-[10px] text-gray-300 font-mono truncate transition-colors group-hover/copy:text-white">{data['sub_url']}</div>
                         </div>
                         <button onclick="copyKey(this, '{data['sub_url']}')" 
                            class="w-7 h-7 rounded-lg bg-white/5 text-white flex items-center justify-center hover:bg-white/10 transition-all active:scale-95 shrink-0 shadow-sm">
                             <span class="material-icons-round text-sm">content_copy</span>
                         </button>
                     </div>

                     <button onclick="openLinkSafe('{data['sub_url']}')"
                        class="w-full bg-white text-black py-2.5 rounded-xl font-bold text-[10px] uppercase tracking-wider shadow-[0_4px_15px_rgba(255,255,255,0.1)] hover:shadow-[0_6px_20px_rgba(255,255,255,0.2)] active:scale-[0.98] transition-all flex items-center justify-center gap-2">
                         <span class="material-icons-round text-sm">bolt</span>
                         <span>{open_btn_text}</span>
                     </button>
                     
                     <div class="grid grid-cols-2 gap-2 mt-1">
                         <button onclick="openActionModal('devices', {data['key_id']}, '{data.get('host_name', '')}')"
                             class="w-full bg-white/5 text-white py-2 rounded-xl font-bold text-[10px] uppercase tracking-wider hover:bg-white/10 active:scale-[0.98] transition-all flex items-center justify-center gap-1.5 border border-white/5 hover:border-white/10">
                             <span class="material-icons-round text-sm">devices</span>
                             <span>{devices_btn_text}</span>
                         </button>
                         <button onclick="openActionModal('comment', {data['key_id']}, '{data.get('comment_key', '')}')"
                             class="w-full bg-white/5 text-white py-2 rounded-xl font-bold text-[10px] uppercase tracking-wider hover:bg-white/10 active:scale-[0.98] transition-all flex items-center justify-center gap-1.5 border border-white/5 hover:border-white/10">
                             <span class="material-icons-round text-sm">edit_note</span>
                             <span>{comments_btn_text}</span>
                         </button>
                     </div>
                </div>
            </div>
        </div>
        """
    return html

def _get_renew_keys_html(keys: list, user_id: int | None = None, webapp_config: dict | None = None) -> tuple[str, str, str]:
    if not keys:
        return "", "Нет активных ключей", _get_no_key_html()
        
    options_html = '<div class="p-1 flex flex-col gap-0.5">'
    selected_text = ""
    renew_plans_html = ""
    
    for index, key in enumerate(keys):
        data = _process_key_data(key)
        host_name = key.get('host_name', '')
        # data['name'] уже = имя ключа (comment_key) или «Ключ #N»
        disp_name = data['name']

        is_selected = (index == 0)
        check_class = "text-primary" if is_selected else "text-transparent"
        text_color = "text-white" if is_selected else "text-gray-300"
        icon_color = "text-primary" if is_selected else "text-gray-500"

        if is_selected:
            selected_text = f"{disp_name} • До {data['expire_date_str']}"

        options_html += f"""
        <button
            class="dropdown-option w-full p-2.5 flex items-center justify-between rounded-lg hover:bg-white/5 transition-colors"
            data-key="#{data['key_id']}" data-name="{disp_name}" data-date="{data['expire_date_str']}" data-host="{host_name}" data-index="{index}" data-hwid-usage="{data.get('hwid_usage', 0)}" data-hwid-limit="{data.get('hwid_limit_num', 0)}">
            <div class="flex items-center gap-2.5 overflow-hidden">
                <span class="material-icons-round {icon_color} text-sm shrink-0">vpn_key</span>
                <div class="text-left overflow-hidden">
                    <div class="text-xs font-bold {text_color} truncate">{disp_name}</div>
                    <div class="flex items-center gap-2">
                        <div class="text-[9px] text-gray-400">До {data['expire_date_str']}</div>
                        <span class="text-[8px] {data['status_bg']} {data['status_color']} px-1.5 py-0.5 rounded-full font-bold uppercase tracking-wider shrink-0">{data['status_text']}</span>
                        {f'<span class="text-[8px] text-purple-300/80 whitespace-nowrap shrink-0" title="Подключено устройств">📱 {data.get("hwid_usage", 0)}</span>' if data.get("hwid_usage", 0) else ''}
                    </div>
                </div>
            </div>
            <span class="material-icons-round {check_class} text-xs selected-icon shrink-0">check</span>
        </button>
        """
        
        display_style = "grid" if is_selected else "none"
        desc, grid_html = _build_plans_grid_html(host_name, user_id, f"renew-plans-{index}", display_style, webapp_config)
        
        renew_plans_html += f'<div id="renew-desc-content-{index}" style="display: none;">{desc}</div>'
        renew_plans_html += grid_html
    
    options_html += '</div>'
    
    return options_html, selected_text, renew_plans_html

def _get_no_key_html(webapp_config: dict | None = None) -> str:
    return _render_home_hero(None, webapp_config)


def _build_plans_grid_html(host_name: str, user_id: int | None, container_id: str, display_style: str = "grid", webapp_config: dict | None = None) -> str:
    import re
    try:
        hosts = get_all_hosts(visible_only=True)
        host = next((h for h in (hosts or []) if h['host_name'] == host_name), None)
    except:
        host = None

    desc = ""
    if host:
        desc = host.get('description') or "Выберите подходящий тариф:"
        desc = re.sub(r'(\s*\n\s*){2,}', '\n', desc).strip()

    try:
        plans = get_plans_for_host(host_name)
    except:
        plans = []

    active_plans = [p for p in plans if p.get('is_active')]
    # Матрица цен по устройствам: в сетке показываем только тарифы базового числа устройств,
    # остальные (hwid_limit > base) используются как ценовые ячейки при движении слайдера устройств.
    if any(int(p.get('hwid_limit') or 0) > 0 for p in active_plans):
        try:
            _base_dev = int(get_setting(f"base_device_{host_name}", "1") or 1)
        except (TypeError, ValueError):
            _base_dev = 1
        _filtered = [p for p in active_plans if int(p.get('hwid_limit') or 0) == _base_dev]
        if _filtered:
            active_plans = _filtered
    active_plans = sorted(active_plans, key=lambda p: (int(p.get('months') or 1), float(p.get('price') or 0)))
    purchase_cfg = webapp_config.get("purchase") if isinstance(webapp_config, dict) else {}
    if not isinstance(purchase_cfg, dict):
        purchase_cfg = {}
    recommended_raw = str(purchase_cfg.get("recommended_months_csv") or "6")
    recommended_months = {
        int(part.strip()) for part in recommended_raw.split(",")
        if part.strip().isdigit()
    } or {6}
    try:
        discount_min_percent = int(str(purchase_cfg.get("discount_badge_min_percent") or "10"))
    except ValueError:
        discount_min_percent = 10

    trial_available = _webapp_trial_available(user_id)
    html = f'<div id="{container_id}" class="server-plans-container grid grid-cols-2 gap-3 mt-2" style="display: {display_style};">'

    if trial_available:
        html += _build_trial_plan_card_html(webapp_config)

    if not active_plans and not trial_available:
        html += '<div class="col-span-2 text-center text-[10px] text-gray-500 py-3 glass-card border border-white/5 rounded-xl">Нет доступных тарифов</div>'
    elif active_plans:
        base_monthly_price = None
        for plan in active_plans:
            try:
                months_i = int(plan.get('months') or 1)
                if months_i == 1:
                    price_i = float(plan.get('price', 0))
                    base_monthly_price = calculate_webapp_price(price_i, user_id)
                    break
            except (ValueError, TypeError):
                continue

        plan_count = len(active_plans)
        for plan_idx, plan in enumerate(active_plans):
            try:
                raw_price = float(plan.get('price', 0))
                final_price = int(calculate_webapp_price(raw_price, user_id))
                months = int(plan.get('months') or 1)
                duration_days = int(plan.get('duration_days') or 0) or (months * 30)
                month_factor = round(duration_days / 30.0, 4)
                month_price = int(round(final_price / month_factor)) if month_factor > 0 else final_price
            except (ValueError, TypeError):
                continue

            month_label = _cfg_text(webapp_config, "purchase.month_label_one", "месяц") if months == 1 else (
                _cfg_text(webapp_config, "purchase.month_label_few", "месяца") if 1 < months < 5 else _cfg_text(webapp_config, "purchase.month_label_many", "месяцев")
            )
            if months == 12:
                month_label = _cfg_text(webapp_config, "purchase.year_label", "год")
                plan_title = f"1 {month_label}"
            else:
                plan_title = f"{months} {month_label}"
            monthly_label = _cfg_text(webapp_config, "purchase.per_month_label", "в месяц")

            old_price = None
            discount_percent = 0
            if base_monthly_price and month_factor > 1:
                old_price = int(round(base_monthly_price * month_factor))
                if old_price > final_price:
                    discount_percent = int(round((1 - (final_price / old_price)) * 100))
                else:
                    old_price = None

            is_featured = months in recommended_months or "best" in str(plan.get("plan_name", "")).lower()
            featured_badge = '<span class="material-icons-round plan-star-badge">star</span>' if is_featured else ''
            discount_badge = f'<span class="plan-discount-badge">-{discount_percent}%</span>' if discount_percent >= discount_min_percent else ''
            selected_cls = " border-primary bg-primary/10 border-2" if is_featured else ""

            is_last_odd = (plan_idx == plan_count - 1) and (plan_count % 2 == 1)
            span_class = " col-span-2" if is_last_odd else ""

            html += f"""
            <button
                class="plan-btn spectra-plan-card glass-card border border-white/10 rounded-[22px] p-3 flex flex-col text-left transition-all active:scale-[0.98] hover:border-primary/40 group relative overflow-hidden{span_class}{selected_cls}"
                data-host="{host_name}" data-plan-id="{plan['plan_id']}" data-price="{final_price}" data-plan-name="{plan.get('plan_name', '')}"
                data-featured="{'1' if is_featured else '0'}"
                data-months="{months}" data-month-factor="{month_factor}"
                data-old-price="{old_price or ''}" data-base-old-price="{old_price or ''}" data-month-price="{month_price}" data-base-month-price="{month_price}"
                onclick="selectPlan(this)">
                <div class="absolute inset-0 plan-card-glow"></div>
                <div class="relative z-10 flex items-start justify-between gap-1.5 w-full min-h-[22px]">
                    <div class="plan-label text-[13px] font-extrabold text-white leading-tight min-w-0 flex-1 pr-1">{plan_title}</div>
                    <div class="plan-card-badges flex items-center gap-1 shrink-0">{discount_badge}{featured_badge}</div>
                </div>
                <div class="relative z-10 mt-2 flex items-end gap-0.5">
                    <span class="plan-price text-[22px] font-black text-white leading-none">{final_price}</span>
                    <span class="text-[14px] font-semibold text-gray-200 leading-none">₽</span>
                </div>
                <div class="plan-monthly-price relative z-10 mt-1 text-[11px] font-medium text-purple-200/70">{month_price} ₽ {monthly_label}</div>
            </button>
            """
    html += '</div>'

    return desc, html


def _get_servers_and_plans_html(user_id: int | None = None, webapp_config: dict | None = None):
    try:
        hosts = get_all_hosts(visible_only=True)
    except:
        hosts = []
        
    if not hosts:
        return "", '<div class="col-span-2 text-center text-xs text-gray-500 py-4 glass-card border border-white/5 rounded-xl">Нет доступных серверов</div>'
        
    server_options_html = '<div class="p-1 flex flex-col gap-0.5">'
    plans_html = ""
    
    for index, host in enumerate(hosts):
        host_name = host['host_name']
        
        is_selected = (index == 0)
            
        check_class = "text-primary" if is_selected else "text-transparent"
        text_color = "text-white" if is_selected else "text-gray-300"
        icon_color = "text-primary" if is_selected else "text-gray-500"
        
        server_options_html += f"""
        <button
            class="server-option w-full p-2.5 flex items-center justify-between rounded-lg hover:bg-white/5 transition-colors"
            data-server="{host_name}" data-index="{index}" onclick="selectServer(this)">
            <div class="flex items-center gap-2.5">
                <span class="material-icons-round {icon_color} text-sm">public</span>
                <div class="text-left">
                    <div class="text-xs font-bold {text_color}">{host_name}</div>
                </div>
            </div>
            <span class="material-icons-round {check_class} text-xs server-selected-icon">check</span>
        </button>
        """
        
        display_style = "grid" if is_selected else "none"
        desc, grid_html = _build_plans_grid_html(host_name, user_id, f"plans-{index}", display_style, webapp_config)
        
        plans_html += f'<div id="desc-content-{index}" style="display: none;">{desc}</div>'
        plans_html += grid_html

    server_options_html += '</div>'
    
    return server_options_html, plans_html


def _render_banned_page(webapp_settings: dict):
    title = webapp_settings.get("webapp_title") or get_setting("panel_brand_title") or "VPN"
    logo = webapp_settings.get("webapp_logo") or ""
    icon = webapp_settings.get("webapp_icon") or ""
    
    html = f"""<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>{title}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/icon?family=Material+Icons+Round" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
            theme: {{
                extend: {{
                    colors: {{
                        primary: '#a855f7',
                        surface: {{
                            dark: '#121212',
                            card: '#1e1e1e',
                            highlight: '#2a2a2a'
                        }}
                    }}
                }}
            }}
        }}
    </script>
    <style>
        body {{ font-family: 'Inter', sans-serif; -webkit-tap-highlight-color: transparent; }}
        .glass {{ background: rgba(30, 30, 30, 0.7); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.05); }}
    </style>
</head>
<body class="bg-surface-dark text-white h-screen flex flex-col items-center justify-center p-6 select-none overflow-hidden">
    <div class="fixed inset-0 pointer-events-none">
        <div class="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-primary/10 rounded-full blur-[120px]"></div>
        <div class="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-primary/5 rounded-full blur-[120px]"></div>
    </div>

    <div class="relative z-10 flex flex-col items-center text-center max-w-sm w-full">
        {f'<img src="{logo}" class="h-20 mb-8 drop-shadow-[0_0_20px_rgba(168,85,247,0.3)]">' if logo else f'<div class="w-20 h-20 bg-primary/20 rounded-3xl flex items-center justify-center mb-8 border border-primary/30 shadow-[0_0_30px_rgba(168,85,247,0.2)]"><span class="material-icons-round text-primary text-4xl">block</span></div>'}
        
        <h1 class="text-3xl font-black mb-3 tracking-tight">Доступ ограничен</h1>
        <p class="text-gray-400 font-medium leading-relaxed mb-8">
            Ваш аккаунт был заблокирован за нарушение правил сервиса. Использование функций WebApp временно недоступно.
        </p>

        <div class="glass rounded-[2rem] p-6 w-full border border-red-500/20 shadow-2xl">
            <div class="flex items-center gap-4 text-left">
                <div class="w-12 h-12 bg-red-500/10 rounded-2xl flex items-center justify-center shrink-0 border border-red-500/20">
                    <span class="material-icons-round text-red-500">lock_person</span>
                </div>
                <div>
                    <div class="text-[10px] text-gray-500 uppercase font-black tracking-widest mb-1">Статус аккаунта</div>
                    <div class="text-lg font-black text-red-500 leading-none">ЗАБЛОКИРОВАН</div>
                </div>
            </div>
            
            <div class="mt-6 pt-6 border-t border-white/5">
                <p class="text-[11px] text-gray-500 font-semibold mb-4 text-center">Если вы считаете, что это ошибка, обратитесь в нашу поддержку</p>
                <a href="https://t.me/{get_setting('support_bot_username')}" target="_blank"
                   class="flex items-center justify-center gap-2 w-full bg-white text-black py-4 rounded-2xl font-black text-sm uppercase tracking-wider hover:opacity-90 active:scale-[0.98] transition-all shadow-xl">
                    <span class="material-icons-round text-lg">headset_mic</span>
                    <span>Написать в поддержку</span>
                </a>
            </div>
        </div>

        <div class="mt-8 opacity-40 text-[10px] font-black uppercase tracking-widest flex items-center gap-2">
            <span>{title}</span>
            <span class="w-1 h-1 bg-gray-600 rounded-full"></span>
            <span>Security Module</span>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=403)


async def _render_main_page(user_id: int):
    webapp_settings = get_webapp_settings()
    webapp_config = webapp_settings.get("webapp_config") if isinstance(webapp_settings, dict) else {}
    if not isinstance(webapp_config, dict):
        webapp_config = {}
    
    # 1. Check if Webapp is enabled
    if not webapp_settings.get("webapp_enable"):
         return HTMLResponse(content="<h1>Webapp is disabled</h1>", status_code=403)
         
    # 2. Check if user is banned
    user = get_user(user_id)
    if user and user.get('is_banned'):
         return _render_banned_page(webapp_settings)
         
    # Можно использовать webapp_domen для проверок или редиректов если нужно
    # current_domain = webapp_settings.get("webapp_domen")

    key_section = _get_no_key_html(webapp_config)
    profile_card = ""
    profile_referral_block = ""
    home_renew_visible_class = "hidden"
    profile_keys_hidden_class = "hidden-empty"
    profile_install_section = _get_profile_install_section_html("", webapp_config)
    profile_devices_section = ""
    home_trial_cta = ""
    profile_keys_list = _get_simple_empty_html(
        _cfg_text(webapp_config, "profile.empty_keys_title", "Нет ключей"),
        _cfg_text(webapp_config, "profile.empty_keys_subtitle", "Купите ключ, чтобы начать пользоваться VPN"),
    )
    setup_keys_list = _get_setup_keys_html([], webapp_config)
    setup_wizard_config_json = _build_setup_wizard_config_json(webapp_config, "")
    renew_keys_options = ""
    renew_selected_key = "Нет активных ключей"
    renew_plans_html_data = _get_no_key_html(webapp_config)
    keys = []
    
    if user_id:
        keys = get_user_keys(user_id)
        # Скрытые истёкшие триал/тест-ключи убираем из ВСЕГО рендера (счётчик ключей, продление,
        # профиль, устройства, главная) — чтобы было консистентно. В БД они остаются (для рассылок).
        keys = [k for k in (keys or []) if not _should_hide_key(k)]
        # Sort all keys by expiry, soonest first
        keys.sort(
            key=lambda k: _parse_key_datetime(k.get('expiry_date') or k.get('expire_at')) or datetime.max
        )
        # Короткий понятный номер «Ключ #1/#2/…» (по порядку списка) — если у ключа нет своего названия
        for _i, _k in enumerate(keys):
            try:
                _k['display_number'] = _i + 1
            except Exception:
                pass
            
        now = get_msk_time().replace(tzinfo=None)
        
        # --- FETCH LIVE DATA ONLY FOR ACTIVE KEYS ---
        active_keys = []
        for k in keys:
            exp = _parse_key_datetime(k.get('expiry_date') or k.get('expire_at'))
            if exp and exp > now:
                active_keys.append(k)

        if active_keys:
            try:
                # --- 1. Fetch Key Details (User info from Host) ---
                details_tasks = []
                for k in active_keys:
                    details_tasks.append(remnawave_api.get_key_details_from_host(k))
                
                details_results = await asyncio.gather(*details_tasks, return_exceptions=True)
                
                # --- 2. Fetch Subscription Info (Traffic Stats) using UUID from Details ---
                sub_tasks = []
                # Map results to keys to keep order
                key_details_map = {}
                
                for k, res in zip(active_keys, details_results):
                    if isinstance(res, Exception) or not res or not res.get('user'):
                        sub_tasks.append(asyncio.sleep(0, None)) # Skip
                        continue
                        
                    u = res['user']
                    key_details_map[k['key_id']] = u
                    
                    # Update limits from user object immediately
                    if u.get('trafficLimitBytes') is not None:
                        k['limit_bytes'] = u.get('trafficLimitBytes')
                    if u.get('hwidDeviceLimit') is not None:
                        k['limit_ips'] = u.get('hwidDeviceLimit')

                    if not k.get('email') and not k.get('key_email'):
                        api_email = u.get('username') or u.get('email') or ''
                        if api_email:
                            k['email'] = api_email
                            k['key_email'] = api_email
                        
                    # Determine UUID for subscription check
                    # BOT PRIORITY: Use DB UUID first, then API response
                    target_uuid = k.get('remnawave_user_uuid') or u.get('uuid')
                    host = k.get('host_name')
                    
                    if target_uuid:
                        sub_tasks.append(remnawave_api.get_subscription_info(str(target_uuid), host_name=host))
                    else:
                        sub_tasks.append(asyncio.sleep(0, None))

                sub_results = await asyncio.gather(*sub_tasks, return_exceptions=True)
                
                # --- 3. Process Subscription Results ---
                for k, sub_res in zip(active_keys, sub_results):
                    # Try to find traffic in subscription response
                    found_traffic = None
                    if not isinstance(sub_res, Exception) and sub_res and isinstance(sub_res, dict):
                        # check common keys
                        for key_name in ['trafficUsed', 'traffic', 'used_traffic']:
                            val = sub_res.get(key_name)
                            if val is not None:
                                found_traffic = val
                                break
                    
                    if found_traffic is not None:
                        k['used_bytes'] = found_traffic
                    
                    # Fallback: check User Details (u)
                    if 'used_bytes' not in k:
                        u = key_details_map.get(k['key_id'])
                        if u:
                             # Check keys in user object
                             for key_name in ['traffic', 'trafficUsed', 'used_traffic']:
                                 if u.get(key_name) is not None:
                                     try: k['used_bytes'] = int(u.get(key_name)); break
                                     except: pass
                             
                             # Final fallback: sum upload + download
                             if 'used_bytes' not in k:
                                 uploaded = int(u.get('upload') or 0)
                                 downloaded = int(u.get('download') or 0)
                                 k['used_bytes'] = uploaded + downloaded

                # --- 4. Connected devices — PARALLEL (was a sequential await-loop = main page slowness) ---
                dev_tasks = []
                dev_keys = []
                for k in active_keys:
                    u = key_details_map.get(k['key_id'])
                    target_uuid = (u.get('uuid') if u else None) or k.get('remnawave_user_uuid')
                    host = k.get('host_name')
                    if target_uuid and host:
                        dev_tasks.append(remnawave_api.get_connected_devices_count(target_uuid, host_name=host))
                        dev_keys.append(k)
                if dev_tasks:
                    dev_results = await asyncio.gather(*dev_tasks, return_exceptions=True)
                    for k, devs in zip(dev_keys, dev_results):
                        if not isinstance(devs, Exception) and devs and isinstance(devs, dict) and 'total' in devs:
                            try:
                                k['used_ips'] = int(devs['total'])
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"[WEBAPP] - Ошибка получения живой статистики для {user_id}: {e}")

        # --- CALCULATE MIN PRICE ---
        min_price_val = 0.0
        try:
            all_hosts = get_all_hosts(visible_only=True)
            prices = []
            for h in all_hosts:
                plans = get_plans_for_host(h['host_name'])
                for p in plans:
                    if p.get('is_active'):
                        try:
                            raw_p = float(p.get('price', 0))
                            final_p = calculate_webapp_price(raw_p, user_id)
                            prices.append(final_p)
                        except: continue
            if prices:
                min_price_val = min(prices)
        except Exception as e:
            logger.error(f"[WEBAPP] - Ошибка расчета мин. цены для {user_id}: {e}")

        # --- GENERATE SECTIONS ---
        if keys:
            home_renew_visible_class = ""
            # For the main monitoring section, show only the soonest active key
            if active_keys:
                key_section = _get_key_html(active_keys[0], webapp_config)
            
            # Renew, Profile and Setup sections get the full list of keys
            # (Setup will filter internally, Profile shows all, Renew now shows all)
            renew_keys_options, renew_selected_key, renew_plans_html_data = _get_renew_keys_html(keys, user_id, webapp_config)
            renew_selected_display = renew_selected_key
            
            visible_keys = [k for k in keys if not _should_hide_key(k)]
            profile_keys_list = _get_profile_keys_html(visible_keys, webapp_config)
            if (profile_keys_list or "").strip():
                profile_keys_hidden_class = ""
            # Устройства — из видимых ключей (без скрытых истёкших триалов); нет видимых → блок не показываем
            profile_devices_section = _get_profile_devices_section_html(active_keys or visible_keys, webapp_config)
            setup_keys_list = _get_setup_keys_html(keys, webapp_config)

            sub_url = ""
            if active_keys:
                sub_url = active_keys[0].get("subscription_url") or active_keys[0].get("key") or ""
            elif keys:
                sub_url = keys[0].get("subscription_url") or keys[0].get("key") or ""
            profile_install_section = _get_profile_install_section_html(sub_url, webapp_config)
            setup_wizard_config_json = _build_setup_wizard_config_json(webapp_config, sub_url)
            
        # Profile Stats
        user = get_user(user_id)
        ref_count = get_referral_count(user_id)
        ref_earned = user.get("referral_balance_all") or 0.0
        tg_profile = {}
        if user_id and not str(user_id).startswith("999"):
            try:
                tg_profile = await _fetch_telegram_profile_for_user(int(user_id))
            except Exception as e:
                logger.debug(f"[WEBAPP] - SSR telegram profile for {user_id}: {e}")
        profile_card = _get_profile_card_html(
            user, ref_count, len(keys), ref_earned, webapp_config, tg_profile=tg_profile
        )
        profile_referral_block = _get_profile_referral_block_html(user, webapp_config)

        # Trial CTA on home: enabled and not used yet (как в боте)
        try:
            if _webapp_trial_available(user_id):
                home_trial_cta = _get_home_trial_cta_html(webapp_config)
        except Exception:
            pass

    p = os.path.join(os.path.dirname(__file__), "app.html")
    with open(p, "r", encoding="utf-8") as f:
        content = f.read()
    
    context = {
        "home_renew_visible_class": home_renew_visible_class,
        "profile_keys_hidden_class": profile_keys_hidden_class,
        "profile_card": profile_card,
        "profile_referral_block": profile_referral_block,
        "profile_install_section": profile_install_section if 'profile_install_section' in locals() else _get_profile_install_section_html("", webapp_config),
        "profile_devices_section": profile_devices_section if 'profile_devices_section' in locals() else "",
        "home_trial_cta": home_trial_cta if 'home_trial_cta' in locals() else "",
        "key_section": key_section,
        "profile_keys_list": profile_keys_list,
        "setup_keys_list": setup_keys_list,
        "setup_wizard_config_json": setup_wizard_config_json if 'setup_wizard_config_json' in locals() else _build_setup_wizard_config_json(webapp_config, ""),
        "renew_keys_options": renew_keys_options,
        "renew_plans_html_data": renew_plans_html_data,
        "renew_selected_display": renew_selected_display if 'renew_selected_display' in locals() else renew_selected_key,
        "min_price": f"{int(min_price_val)} ₽" if min_price_val > 0 else "0 ₽",
        "webapp_logo": webapp_settings.get("webapp_logo") or "",
        "webapp_icon": webapp_settings.get("webapp_icon") or "",
        "purchase_discount_badges_json": json.dumps(
            (webapp_config.get("purchase", {}) or {}).get("device_discount_badges", {}),
            ensure_ascii=False
        ),
        "device_tiers_bootstrap_json": _build_device_tiers_bootstrap_json(),
        "purchase_info_card_hidden_class": "hidden" if str((webapp_config.get("purchase", {}) or {}).get("show_info_card", "0")).strip().lower() not in ("1", "true", "yes", "on") else "",
    }
    
    content = _process_template_placeholders(content, user_id, webapp_settings, context)
    # no-cache: Telegram WebView иначе показывает старую версию Mini App после правок
    return HTMLResponse(content=content, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user_id: int | None = None, token: str | None = None):
    try:
        # 1. Авторизация ТОЛЬКО по токену (query ?token= / cookie / заголовок). Раньше сервер
        #    доверял «голому» ?user_id= и открывал кабинет любого юзера (IDOR). Теперь user_id
        #    из URL без валидного токена игнорируется → отдаём login.html (там подписанный вход).
        from shop_bot.data_manager import database
        authed_id = None
        _tok = token or request.cookies.get("auth_token") or request.headers.get("x-auth-token")
        if _tok:
            _u = database.get_user_by_auth_token(_tok)
            if _u:
                authed_id = _u['telegram_id']
        if authed_id is not None:
            user_id = authed_id
        else:
            user_id = None

        # 2. If no user_id (and no valid token), serve login.html
        if user_id is None:
            p = os.path.join(os.path.dirname(__file__), "login.html")
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # Process placeholders for login page too
                webapp_settings = get_webapp_settings()
                context = {
                    "webapp_logo": webapp_settings.get("webapp_logo") or "",
                    "webapp_icon": webapp_settings.get("webapp_icon") or ""
                }
                content = _process_template_placeholders(content, 0, webapp_settings, context)
                return HTMLResponse(content=content)
            else:
                return HTMLResponse(content="<h1>Login page not found</h1>", status_code=404)

        webapp_settings = get_webapp_settings()
        user = get_user(user_id)
        if user and user.get('is_banned'):
            return _render_banned_page(webapp_settings)

        return await _render_main_page(user_id)

    except Exception as e:
        error_details = traceback.format_exc()
        return HTMLResponse(content=f"<h1>500 Internal Server Error</h1><pre>{error_details}</pre>", status_code=500)

# ===== PWA (установка кабинета как отдельного приложения) =====
# Роуты ОБЯЗАНЫ быть выше catch-all "/{path_param}", иначе он их перехватит.

@app.get("/manifest.webmanifest")
async def pwa_manifest():
    """PWA-манифест: делает vpn-vless.ru устанавливаемым как standalone-приложение."""
    p = os.path.join(os.path.dirname(__file__), "manifest.webmanifest")
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return Response(status_code=404)
    return Response(
        content=content,
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )

@app.get("/sw.js")
async def pwa_service_worker():
    """Service worker (passthrough). Нужен для установки PWA на Android; scope = корень."""
    p = os.path.join(os.path.dirname(__file__), "sw.js")
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return Response(status_code=404)
    return Response(
        content=content,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )

# ===== API Models =====

class SupportStatusRequest(BaseModel):
    user_id: int

class SupportTicketCreateRequest(BaseModel):
    user_id: int
    subject: str

class SupportMessageSendRequest(BaseModel):
    user_id: int
    ticket_id: int
    message: str

class SupportCloseRequest(BaseModel):
    user_id: int
    ticket_id: int

class PaymentMethodsRequest(BaseModel):
    user_id: int

class TokenRequest(BaseModel):
    init_data: str

class TelegramDirectAuthRequest(BaseModel):
    user_id: int
    init_data: str | None = None

class EmailAuthRequest(BaseModel):
    email: str
    password: str

class PasswordResetRequest(BaseModel):
    email: str

class PasswordResetCheckRequest(BaseModel):
    email: str
    code: str

class PasswordResetVerifyRequest(BaseModel):
    email: str
    code: str
    new_password: str

class RecoveryResetRequest(BaseModel):
    email: str
    recovery_code: str
    new_password: str

# Stores dict: { "email@bot.local": {"code": "123456", "expires": float_timestamp} }
PASSWORD_RESET_TOKENS = {}

class SyncTgRequest(BaseModel):
    token: str
    init_data: str


class DeviceTiersRequest(BaseModel):
    host_name: str


class TelegramProfileRequest(BaseModel):
    init_data: str


class CreatePaymentRequest(BaseModel):
    user_id: int
    payment_method: str
    plan_id: int
    host_name: str | None = None
    action: str
    key_id: int | None = None
    promo_code: str | None = None
    tier_device_count: int | None = None
    tier_price: float = 0

class ApplyPromoRequest(BaseModel):
    user_id: int
    promo_code: str
    plan_id: int | None = None
    price: float | None = None

# ===== API Endpoints =====


def _telegram_display_name(user_data: dict) -> str:
    return " ".join(
        part for part in (user_data.get("first_name"), user_data.get("last_name")) if part
    ).strip()


async def _fetch_telegram_profile_for_user(user_id: int) -> dict:
    if not user_id or int(user_id) <= 0:
        return {}

    token = get_setting("telegram_bot_token")
    if not token:
        return {}

    bot = Bot(token=token)
    profile: dict[str, str] = {}
    try:
        chat = await bot.get_chat(int(user_id))
        username = (getattr(chat, "username", None) or "").strip()
        display_name = " ".join(
            part for part in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if part
        ).strip()
        if display_name:
            profile["display_name"] = display_name
        if username:
            profile["username"] = username
            from shop_bot.data_manager import database

            db_user = get_user(int(user_id))
            database.register_user_if_not_exists(
                int(user_id),
                username,
                db_user.get("referred_by") if db_user else None,
            )
        photo_url = await _fetch_telegram_photo_url(int(user_id), bot=bot)
        if photo_url:
            profile["photo_url"] = photo_url
        return profile
    except Exception as e:
        logger.debug(f"[WEBAPP] - telegram chat profile for {user_id}: {e}")
        return profile
    finally:
        await bot.session.close()


async def _fetch_telegram_photo_url(user_id: int, bot: Bot | None = None) -> str | None:
    token = get_setting("telegram_bot_token")
    if not token or not user_id:
        return None
    owns_bot = bot is None
    if owns_bot:
        bot = Bot(token=token)
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos.total_count or not photos.photos:
            return None
        file_id = photos.photos[0][-1].file_id
        file = await bot.get_file(file_id)
        if not file or not file.file_path:
            return None
        return f"https://api.telegram.org/file/bot{token}/{file.file_path}"
    except Exception as e:
        logger.debug(f"[WEBAPP] - profile photo for {user_id}: {e}")
        return None
    finally:
        if owns_bot and bot is not None:
            await bot.session.close()


def validate_telegram_data(init_data: str, bot_token: str) -> dict | None:
    from urllib.parse import parse_qsl, unquote
    import hmac
    import hashlib
    import json

    try:
        if not init_data or len(init_data) < 10:
            logger.warning("Telegram auth: init_data is empty or too short")
            return None

        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed_data:
            logger.warning("Telegram auth: hash not found in init_data")
            return None
        
        received_hash = parsed_data.pop("hash")
        
        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed_data.items())
        )
        
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash == received_hash:
            user_json = parsed_data.get("user")
            if user_json:
                return json.loads(user_json)
            logger.warning("Telegram auth: hash valid but no user field")
        else:
            logger.warning(f"Telegram auth: hash mismatch. Expected={calculated_hash[:16]}... Got={received_hash[:16]}...")
        return None
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка валидации данных Telegram: {e}")
        return None


def _resolve_authed_user_id(request: Request) -> int | None:
    """telegram_id владельца сессии из auth_token (заголовок X-Auth-Token / cookie / ?token=).
    Токен — непредсказуемый uuid4, выдаётся только после подписанного initData или логина.
    Возвращает None, если валидного токена нет."""
    try:
        from shop_bot.data_manager import database as _db
        tok = (request.headers.get("x-auth-token")
               or request.cookies.get("auth_token")
               or request.query_params.get("token"))
        if not tok:
            return None
        u = _db.get_user_by_auth_token(tok)
        if not u:
            return None
        return int(u.get("telegram_id") or 0) or None
    except Exception:
        return None


def _owns(request: Request, claimed_user_id) -> bool:
    """True, если запрос авторизован токеном ИМЕННО этого user_id.
    Закрывает IDOR: user_id из тела/query больше не принимается на веру."""
    try:
        aid = _resolve_authed_user_id(request)
        return aid is not None and int(aid) == int(claimed_user_id)
    except Exception:
        return False


@app.get("/api/auth/request-token")
async def api_request_auth_token():
    token = str(uuid.uuid4())[:36]
    TEMP_AUTH_TOKENS[token] = None
    bot_username = get_setting("telegram_bot_username")
    auth_url = f"tg://resolve?domain={bot_username}&start=auth_{token}"
    return {"ok": True, "token": token, "auth_url": auth_url}

@app.get("/api/auth/check-token/{token}")
async def api_check_auth_token(token: str):
    from shop_bot.data_manager import database
    # 1. Check in memory (waiting for bot confirmation)
    if token in TEMP_AUTH_TOKENS and TEMP_AUTH_TOKENS[token] is not None:
        user_id = TEMP_AUTH_TOKENS.pop(token)
        
        # Check existing token first
        existing_token = database.get_auth_token_by_user_id(user_id)
        if existing_token:
            return {"ok": True, "authorized": True, "user_id": user_id, "token": existing_token}
            
        # Generate persistent token
        persistent_token = str(uuid.uuid4())
        database.update_user_auth_token(user_id, persistent_token)
        return {"ok": True, "authorized": True, "user_id": user_id, "token": persistent_token}
    
    # 2. Check in DB (already authorized)
    user = database.get_user_by_auth_token(token)
    if user:
        if user.get('is_banned'):
            return {"ok": True, "authorized": False, "error": "Banned"}
        return {"ok": True, "authorized": True, "user_id": user['telegram_id'], "token": token}
    
    # 2.1 Check if user has persistent token (deep link flow edge case)
    # If the token passed is not found, it might be expired or invalid, return False
        
    return {"ok": True, "authorized": False}

@app.post("/api/auth/token")
async def api_create_token(req: TokenRequest):
    """Generate or retrieve a persistent login token using verified Telegram data."""
    token_str = get_setting("telegram_bot_token")
    if not token_str:
        return {"ok": False, "error": "Server configuration error"}

    user_data = validate_telegram_data(req.init_data, token_str)
    
    if not user_data:
        return {"ok": False, "error": "Invalid auth data"}

    user_id = user_data.get("id")
    from shop_bot.data_manager import database
    
    # Check ban status
    user = get_user(user_id)
    if user and user.get('is_banned'):
        return {"ok": False, "error": "Access denied"}

    tg_username = (user_data.get("username") or "").strip()
    if user_id and tg_username:
        database.register_user_if_not_exists(
            int(user_id),
            tg_username,
            user.get("referred_by") if user else None,
        )
    
    # Check if user already has a persistent token
    existing_token = database.get_auth_token_by_user_id(user_id)
    if existing_token:
         return {"ok": True, "token": existing_token}
    
    # Generate new persistent token
    token = str(uuid.uuid4())
    # Ensure it's unique (highly likely with UUID4)
    database.update_user_auth_token(user_id, token)

    return {"ok": True, "token": token}


@app.post("/api/auth/telegram-direct")
async def api_telegram_direct_auth(req: TelegramDirectAuthRequest):
    from shop_bot.data_manager import database
    try:
        # SECURITY: раньше эндпоинт выдавал постоянный токен по одному user_id без проверки
        # (мастер-ключ к любому аккаунту). Теперь требуем подписанный initData и сверяем,
        # что его user.id совпадает с запрошенным user_id. Без валидной подписи — отказ.
        token_str = get_setting("telegram_bot_token") or ""
        tg_data = validate_telegram_data(req.init_data or "", token_str) if token_str else None
        tg_uid = 0
        try:
            tg_uid = int((tg_data or {}).get("id") or 0)
        except Exception:
            tg_uid = 0
        if not tg_data or not tg_uid or tg_uid != int(req.user_id):
            logger.warning(f"[WEBAPP] - telegram-direct отклонён: невалидный initData для {req.user_id}")
            return {"ok": False, "error": "Access denied"}

        user = get_user(req.user_id)
        if not user:
            return {"ok": False, "error": "User not registered"}

        if user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        existing_token = database.get_auth_token_by_user_id(req.user_id)
        if existing_token:
            return {"ok": True, "token": existing_token}

        token = str(uuid.uuid4())
        database.update_user_auth_token(req.user_id, token)
        return {"ok": True, "token": token}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка прямой авторизации Telegram для {req.user_id}: {e}")
        return {"ok": False, "error": "Auth error"}

def _validate_password(password: str) -> str | None:
    if len(password) < 5:
        return "Пароль должен содержать минимум 5 символов"
    if password.isdigit():
        return "Пароль не должен состоять только из цифр"
    if len(set(password)) < 2:
        return "Пароль слишком простой — используйте разные символы"
    return None

# Код восстановления для сброса пароля без Telegram. Без неоднозначных символов (0/O/1/I/l).
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def _gen_recovery_code() -> str:
    import secrets
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
    return f"SPECTRA-{raw[:5]}-{raw[5:]}"

def _normalize_recovery_code(code: str) -> str:
    """Убирает дефисы/пробелы и приводит к верхнему регистру для устойчивого сравнения."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())

@app.post("/api/auth/email/register")
async def api_email_register(req: EmailAuthRequest):
    from shop_bot.data_manager import database
    existing = database.get_user_by_email(req.email)
    if existing:
        return {"ok": False, "error": "Email уже зарегистрирован"}
        
    pw_err = _validate_password(req.password)
    if pw_err:
        return {"ok": False, "error": pw_err}
    user = database.create_user_by_email(req.email, database.hash_password(req.password))
    if not user:
        return {"ok": False, "error": "Ошибка при регистрации"}

    token = str(uuid.uuid4())
    database.update_user_auth_token(user['telegram_id'], token)
    # Код восстановления (показывается один раз) — для сброса пароля без Telegram
    recovery = _gen_recovery_code()
    try:
        database.update_user_recovery_code(user['telegram_id'], database.hash_password(_normalize_recovery_code(recovery)))
    except Exception:
        recovery = None
    return {"ok": True, "token": token, "recovery_code": recovery}

@app.post("/api/auth/email/login")
async def api_email_login(req: EmailAuthRequest):
    from shop_bot.data_manager import database
    user = database.get_user_by_email(req.email)
    if not user or not database.verify_password(req.password, user.get('auth_pass')):
        return {"ok": False, "error": "Неверный email или пароль"}

    if user.get('is_banned'):
        return {"ok": False, "error": "Аккаунт заблокирован"}

    # Миграция: старый открытый пароль → pbkdf2-хеш при первом успешном входе
    if database.is_legacy_password(user.get('auth_pass')):
        try:
            database.update_user_password(user.get('auth_email') or req.email, database.hash_password(req.password))
        except Exception:
            pass

    token = str(uuid.uuid4())
    database.update_user_auth_token(user['telegram_id'], token)
    resp = {"ok": True, "token": token}
    # У аккаунтов без кода восстановления (старые регистрации) — выдаём его при входе
    if not user.get('recovery_code'):
        try:
            new_code = _gen_recovery_code()
            if database.update_user_recovery_code(user['telegram_id'], database.hash_password(_normalize_recovery_code(new_code))):
                resp["recovery_code"] = new_code
        except Exception:
            pass
    return resp

@app.post("/api/auth/email/reset/request")
async def api_email_reset_request(req: PasswordResetRequest):
    from shop_bot.data_manager import database
    user = database.get_user_by_email(req.email)
    if not user:
        return {"ok": False, "error": "Email не найден"}
        
    if str(user['telegram_id']).startswith("999"):
        return {"ok": False, "error": "Аккаунт не синхронизирован с Telegram.\nОтправить сообщение невозможно!"}

    import secrets
    import time
    code = str(secrets.randbelow(900000) + 100000)  # криптостойкий 6-значный код (не random)
    PASSWORD_RESET_TOKENS[req.email.lower().strip()] = {
        "code": code,
        "expires": time.time() + 600,
        "attempts": 0,
    }
    
    try:
        success = await _send_telegram_message(
            user['telegram_id'], 
            f"🔐 <b>Восстановление пароля</b>\n\nВаш код для сброса безопасности:\n<code>{code}</code>\n\n<i>Код действителен 10 минут. Если вы не запрашивали сброс пароля, проигнорируйте это сообщение.</i>"
        )
        if not success:
            return {"ok": False, "error": "Ошибка при отправке в Telegram. Возможно, вы заблокировали бота."}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка вызова _send_telegram_message для {req.email}: {e}")
        return {"ok": False, "error": "Ошибка при отправке в Telegram. Возможно, вы заблокировали бота."}

    return {"ok": True}

@app.post("/api/auth/email/reset/check")
async def api_email_reset_check(req: PasswordResetCheckRequest):
    import time
    import hmac
    email_lower = req.email.lower().strip()
    if email_lower not in PASSWORD_RESET_TOKENS:
        return {"ok": False, "error": "Код не запрашивался или истёк"}
        
    token_data = PASSWORD_RESET_TOKENS[email_lower]
    if time.time() > token_data["expires"]:
        return {"ok": False, "error": "Код устарел"}

    # анти-брутфорс: не более 5 попыток на код, иначе сжигаем
    token_data["attempts"] = token_data.get("attempts", 0) + 1
    if token_data["attempts"] > 5:
        del PASSWORD_RESET_TOKENS[email_lower]
        return {"ok": False, "error": "Слишком много попыток. Запросите код заново."}
    if not hmac.compare_digest(str(token_data["code"]), str(req.code or "")):
        return {"ok": False, "error": "Неверный код"}

    return {"ok": True}

@app.post("/api/auth/email/reset/verify")
async def api_email_reset_verify(req: PasswordResetVerifyRequest):
    import time
    import hmac
    email_lower = req.email.lower().strip()
    if email_lower not in PASSWORD_RESET_TOKENS:
        return {"ok": False, "error": "Код не запрашивался или истёк"}
        
    token_data = PASSWORD_RESET_TOKENS[email_lower]
    if time.time() > token_data["expires"]:
        del PASSWORD_RESET_TOKENS[email_lower]
        return {"ok": False, "error": "Код устарел"}

    # анти-брутфорс: не более 5 попыток на код, иначе сжигаем
    token_data["attempts"] = token_data.get("attempts", 0) + 1
    if token_data["attempts"] > 5:
        del PASSWORD_RESET_TOKENS[email_lower]
        return {"ok": False, "error": "Слишком много попыток. Запросите код заново."}
    if not hmac.compare_digest(str(token_data["code"]), str(req.code or "")):
        return {"ok": False, "error": "Неверный код"}

    from shop_bot.data_manager import database
    pw_err = _validate_password(req.new_password)
    if pw_err:
        return {"ok": False, "error": pw_err}
    if not database.update_user_password(req.email, database.hash_password(req.new_password)):
        return {"ok": False, "error": "Ошибка базы данных"}
        
    del PASSWORD_RESET_TOKENS[email_lower]
    return {"ok": True}

@app.post("/api/auth/email/reset/recovery")
async def api_email_reset_recovery(req: RecoveryResetRequest):
    """Сброс пароля по коду восстановления (без Telegram). Возвращает НОВЫЙ код."""
    from shop_bot.data_manager import database
    user = database.get_user_by_email(req.email)
    if not user:
        return {"ok": False, "error": "Email не найден"}
    stored = user.get('recovery_code')
    if not stored or not database.verify_password(_normalize_recovery_code(req.recovery_code), stored):
        return {"ok": False, "error": "Неверный код восстановления"}
    pw_err = _validate_password(req.new_password)
    if pw_err:
        return {"ok": False, "error": pw_err}
    if not database.update_user_password(req.email, database.hash_password(req.new_password)):
        return {"ok": False, "error": "Ошибка базы данных"}
    # Ротация: использованный код заменяем новым
    new_code = _gen_recovery_code()
    try:
        database.update_user_recovery_code(user['telegram_id'], database.hash_password(_normalize_recovery_code(new_code)))
    except Exception:
        new_code = None
    return {"ok": True, "recovery_code": new_code}

@app.post("/api/auth/sync-tg")
async def api_sync_tg(req: SyncTgRequest):
    from shop_bot.data_manager import database
    user = database.get_user_by_auth_token(req.token)
    if not user:
        return {"ok": False, "error": "Не авторизован"}
        
    token_str = get_setting("telegram_bot_token")
    if not token_str:
         return {"ok": False, "error": "Server configuration error"}
         
    tg_data = validate_telegram_data(req.init_data, token_str)
    if not tg_data or not tg_data.get('id'):
         return {"ok": False, "error": "Invalid Telegram data"}
         
    tg_id = tg_data.get('id')
    tg_username = tg_data.get('username') or ''
    
    if user['telegram_id'] > 0:
         return {"ok": False, "error": "Telegram уже привязан"}
         
    res = database.link_telegram_to_email_user(user['telegram_id'], tg_id, tg_username)
    if res is True:
         return {"ok": True}
    else:
         return {"ok": False, "error": str(res)}


@app.get("/api/profile-avatar")
async def api_profile_avatar(request: Request, user_id: int):
    try:
        from shop_bot.data_manager import database

        token = request.headers.get("x-auth-token") or request.cookies.get("auth_token") or request.query_params.get("token")
        if not token:
            return Response(status_code=401)

        user = database.get_user_by_auth_token(token)
        if not user or int(user.get("telegram_id") or 0) != int(user_id):
            return Response(status_code=403)

        photo_url = await _fetch_telegram_photo_url(int(user_id))
        if not photo_url:
            return Response(status_code=404)

        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as resp:
                if resp.status != 200:
                    return Response(status_code=404)
                body = await resp.read()
                content_type = resp.headers.get("Content-Type") or "image/jpeg"
                return Response(content=body, media_type=content_type)
    except Exception as e:
        logger.debug(f"[WEBAPP] - profile avatar proxy for {user_id}: {e}")
        return Response(status_code=500)


@app.post("/api/telegram-profile")
async def api_telegram_profile(req: TelegramProfileRequest):
    try:
        token_str = get_setting("telegram_bot_token")
        if not token_str:
            return {"ok": False, "error": "Server configuration error"}

        user_data = validate_telegram_data(req.init_data, token_str)
        if not user_data:
            return {"ok": False, "error": "Invalid auth data"}

        user_id = int(user_data.get("id") or 0)
        if not user_id:
            return {"ok": False, "error": "User not found"}

        from shop_bot.data_manager import database

        username = (user_data.get("username") or "").strip()
        user = get_user(user_id)
        if username:
            database.register_user_if_not_exists(
                user_id,
                username,
                user.get("referred_by") if user else None,
            )

        photo_url = (user_data.get("photo_url") or "").strip()
        if not photo_url:
            photo_url = (await _fetch_telegram_photo_url(user_id)) or ""

        display_name = _telegram_display_name(user_data) or username or f"#{user_id}"
        avatar_path = f"/api/profile-avatar?user_id={user_id}" if photo_url else ""
        return {
            "ok": True,
            "profile": {
                "id": user_id,
                "display_name": display_name,
                "username": username,
                "photo_url": avatar_path,
            },
        }
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка API telegram-profile: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/api/device-tiers")
async def api_device_tiers(req: DeviceTiersRequest):
    try:
        host_data = get_host(req.host_name)
        if not host_data:
            return {"ok": True, "device_mode": "plan", "tiers": [], "tier_lock_extend": 0}
        mode = host_data.get('device_mode', 'plan')
        lock = int(host_data.get('tier_lock_extend', 0) or 0)
        from shop_bot.data_manager import database
        base_devices = int(database.get_setting(f"base_device_{req.host_name}", "1"))
        tiers = []
        if mode == 'tiers':
            raw = get_device_tiers(req.host_name)
            tiers = [{"tier_id": t["tier_id"], "device_count": t["device_count"], "price": float(t["price"])} for t in raw]
        return {"ok": True, "device_mode": mode, "tiers": tiers, "tier_lock_extend": lock, "base_device_count": base_devices}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка API device-tiers для {req.host_name}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/payment-methods")
async def api_get_payment_methods(req: PaymentMethodsRequest, request: Request):
    if not _owns(request, req.user_id):
        return {"ok": False, "error": "Access denied"}
    user_id = req.user_id
    user = get_user(user_id)
    
    methods = []
    
    # 1. YooKassa
    if (get_setting("yookassa_shop_id") or "") and (get_setting("yookassa_secret_key") or ""):
        label = "Банковская карта"
        if (get_setting("sbp_enabled") or "false").strip().lower() == "true":
            label = "СБП / Банковская карта"
        methods.append({"id": "pay_yookassa", "name": label, "icon": "credit_card"})

    # 2. Platega
    if (get_setting("platega_payform_enabled") or "false").strip().lower() == "true":
        methods.append({"id": "pay_platega_payform", "name": "Platega", "icon": "credit_card"})
    if (get_setting("platega_enabled") or "false").strip().lower() == "true":
        methods.append({"id": "pay_platega", "name": "СБП / Platega", "icon": "payments"})
    if (get_setting("platega_crypto_enabled") or "false").strip().lower() == "true":
        methods.append({"id": "pay_platega_crypto", "name": "Крипта / Platega", "icon": "payments"})

    # 3. CryptoBot
    if get_setting("cryptobot_token"):
        methods.append({"id": "pay_cryptobot", "name": "Криптовалюта", "icon": "currency_bitcoin"})
    # 3.1 Heleket (alternative crypto)
    elif (get_setting("heleket_merchant_id") or "") and (get_setting("heleket_api_key") or ""):
        methods.append({"id": "pay_heleket", "name": "Криптовалюта", "icon": "currency_bitcoin"})

    # 4. TON Connect
    if (get_setting("ton_wallet_address") or "") and (get_setting("tonapi_key") or ""):
        methods.append({"id": "pay_tonconnect", "name": "TON Connect", "icon": "wallet"})

    # 5. Telegram Stars
    if (get_setting("stars_enabled") or "false").strip().lower() == "true":
        methods.append({"id": "pay_stars", "name": "Telegram Stars", "icon": "star"})

    # 6. YooMoney
    if (get_setting("yoomoney_enabled") or "false").strip().lower() == "true":
        methods.append({"id": "pay_yoomoney", "name": "ЮMoney (кошелёк)", "icon": "account_balance_wallet"})

    # 7. Balance
    balance = float(user.get('balance', 0)) if user else 0
    methods.append({"id": "pay_balance", "name": f"Баланс ({balance:.0f} RUB)", "icon": "account_balance", "balance": balance})

    return {"ok": True, "methods": methods, "balance": balance}


@app.post("/api/create-payment")
async def api_create_payment(req: CreatePaymentRequest, request: Request):
    try:
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        user_id = req.user_id
        plan_id = req.plan_id
        method_id = req.payment_method
        
        plan = get_plan_by_id(plan_id)
        if not plan:
            logger.warning(f"[WEBAPP] - Тариф {plan_id} не найден для пользователя {user_id}")
            return {"ok": False, "error": "Тариф не найден"}

        logger.info(f"[WEBAPP] - Начало создания платежа: User={user_id}, Plan={plan_id}, Method={method_id}")

        user = get_user(user_id)
        if not user:
            logger.warning(f"[WEBAPP] - Пользователь {user_id} не найден при создании платежа")
            return {"ok": False, "error": "Пользователь не найден (ID: " + str(user_id) + ")"}
        
        final_price = calculate_webapp_price(float(plan['price']), user_id) 
        
        months = int(plan.get('months') or 1)
        duration_days = int(plan.get('duration_days') or 0) or (months * 30)
        month_factor = duration_days / 30.0

        tier_device_count = req.tier_device_count
        tier_price_per_month = float(req.tier_price or 0)
        host_data = get_host(req.host_name) if req.host_name else None
        host_device_mode = (host_data or {}).get('device_mode', 'plan')
        host_tier_lock_extend = int((host_data or {}).get('tier_lock_extend', 0) or 0)
        base_devices = 1
        allowed_tier_prices: dict[int, float] = {}

        if req.host_name and host_device_mode == 'tiers':
            from shop_bot.data_manager import database
            try:
                base_devices = max(1, int(database.get_setting(f"base_device_{req.host_name}", "1")))
            except Exception:
                base_devices = 1

            allowed_tier_prices[base_devices] = 0.0
            for tier in get_device_tiers(req.host_name):
                try:
                    device_count = int(tier.get('device_count') or 0)
                    monthly_tier_surcharge = float(tier.get('price') or 0)
                except (TypeError, ValueError):
                    continue
                if device_count < base_devices:
                    continue
                allowed_tier_prices[device_count] = max(0.0, monthly_tier_surcharge)

            try:
                requested_device_count = int(tier_device_count) if tier_device_count is not None else base_devices
            except (TypeError, ValueError):
                requested_device_count = base_devices

            if requested_device_count not in allowed_tier_prices:
                requested_device_count = base_devices

            tier_device_count = requested_device_count
            tier_price_per_month = allowed_tier_prices.get(requested_device_count, 0.0)

            # Точная матрица цен: если для (host, выбранные устройства, месяцы) есть отдельный тариф —
            # берём его цену напрямую (она авторитетна на сервере), надбавку обнуляем.
            try:
                _matrix_plan = next(
                    (mp for mp in get_plans_for_host(req.host_name)
                     if mp.get('is_active')
                     and int(mp.get('hwid_limit') or 0) == int(requested_device_count)
                     and int(mp.get('months') or 1) == months),
                    None,
                )
            except Exception:
                _matrix_plan = None
            if _matrix_plan:
                final_price = calculate_webapp_price(float(_matrix_plan.get('price') or 0), user_id)
                tier_price_per_month = 0.0
        else:
            try:
                tier_device_count = int(tier_device_count) if tier_device_count is not None else None
            except (TypeError, ValueError):
                tier_device_count = None
            tier_price_per_month = max(0.0, tier_price_per_month)
        
        if req.action == 'extend' and req.key_id:
            if host_data and host_device_mode == 'tiers':
                key = get_key_by_id(req.key_id)
                if key and key.get('remnawave_user_uuid'):
                    try:
                        user_info = await remnawave_api.get_user_by_uuid(key['remnawave_user_uuid'], host_name=req.host_name)
                        if user_info:
                            old_hwid = int(user_info.get('hwidDeviceLimit') or 1)
                            if old_hwid < 1:
                                old_hwid = base_devices
                            if host_tier_lock_extend:
                                tier_device_count = old_hwid
                                tier_price_per_month = allowed_tier_prices.get(old_hwid, 0.0)
                            elif tier_device_count and int(tier_device_count) > old_hwid:
                                old_total_tier_price = allowed_tier_prices.get(old_hwid, 0.0)
                                new_total_tier_price = allowed_tier_prices.get(int(tier_device_count), old_total_tier_price)
                                monthly_diff_price = max(0.0, new_total_tier_price - old_total_tier_price)
                                if key.get('expiry_date') and monthly_diff_price > 0:
                                    expire_dt = datetime.strptime(key['expiry_date'], "%Y-%m-%d %H:%M:%S")
                                    now = get_msk_time().replace(tzinfo=None)
                                    days_left = (expire_dt - now).days
                                    if days_left > 0:
                                        remaining_months = float(days_left) / 30.0
                                        device_surcharge = monthly_diff_price * remaining_months
                                        final_price += device_surcharge
                    except Exception as e:
                        logger.error(f"[WEBAPP] - Ошибка HWID: {e}")
        
        if tier_price_per_month > 0:
            final_price += tier_price_per_month * month_factor
            
        # --- APPLY PROMO DISCOUNT ---
        if req.promo_code:
            promo, error = rw_repo.check_promo_code_available(req.promo_code, user_id)
            if promo and promo.get('promo_type') == 'discount':
                if promo.get('discount_percent'):
                    final_price -= final_price * (float(promo['discount_percent']) / 100)
                elif promo.get('discount_amount'):
                    final_price -= float(promo['discount_amount'])
                final_price = max(0, round(final_price, 2))
        
        action_name = req.action
        
        # --- YooKassa ---
        if method_id == "pay_yookassa":
            shop_id, secret = get_setting("yookassa_shop_id"), get_setting("yookassa_secret_key")
            if not shop_id or not secret: return {"ok": False, "error": "YooKassa не настроена"}
            YookassaConfiguration.account_id = shop_id
            YookassaConfiguration.secret_key = secret
            pid = str(uuid.uuid4())
            meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "YooKassa", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
            create_payload_pending(pid, user_id, float(final_price), meta)
            comment = get_transaction_comment({"id": user_id, "username": user.get("username")}, action_name, months, req.host_name)
            payload = {
                "amount": {"value": f"{final_price:.2f}", "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": f"https://t.me/{get_setting('telegram_bot_username')}"},
                "capture": True, "description": comment, "metadata": meta
            }
            # Чек для фискализации (54-ФЗ): без него ЮKassa отклоняет платёж при включённой фискализации.
            # Раньше Mini App не слал receipt -> оплата картой падала. Структура как в бот-пути.
            _r_email = (get_setting("receipt_email") or "").strip() or "example@example.com"
            payload["receipt"] = {
                "customer": {"email": _r_email},
                "items": [{
                    "description": (comment or "VPN")[:128],
                    "quantity": "1.00",
                    "amount": {"value": f"{final_price:.2f}", "currency": "RUB"},
                    "vat_code": "1",
                    "payment_subject": "service",
                    "payment_mode": "full_payment",
                }],
            }
            try:
                pay_obj = YookassaPayment.create(payload, pid)
                pay_url = pay_obj.confirmation.confirmation_url

                # Сохраняем id платежа YooKassa, чтобы можно было активно проверить статус (manual recheck)
                try:
                    yk_id = getattr(pay_obj, "id", None)
                    if yk_id:
                        meta["yk_payment_id"] = str(yk_id)
                        create_payload_pending(pid, user_id, float(final_price), meta)
                except Exception:
                    pass

                kb = create_payment_keyboard(pay_url)
                await _send_telegram_message(user_id, f"<b>Оплата через ЮKassa</b>\n\nСумма: <b>{final_price:.2f} RUB</b>\n\n<i>Вы можете оплатить счет здесь или в WebApp.</i>", kb)
                
                logger.info(f"[WEBAPP] - Успешно создан счет YooKassa для {user_id}: {pid}")
                return {"ok": True, "payment_url": pay_url, "payment_id": pid, "message": "Счёт создан"}
            except Exception as e:
                logger.error(f"[WEBAPP] - Ошибка YooKassa для {user_id}: {e}")
                return {"ok": False, "error": f"Ошибка YooKassa: {e}"}

        # --- Platega Payform ---
        elif method_id == "pay_platega_payform":
            mid, key = get_setting("platega_merchant_id"), get_setting("platega_api_key")
            if not mid or not key: return {"ok": False, "error": "Platega не настроена"}
            pid = str(uuid.uuid4())
            meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "Platega Payform", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
            create_payload_pending(pid, user_id, float(final_price), meta)
            desc = f"Order {pid}"
            try:
                platega = PlategaAPI(mid, key)
                _, url = await platega.create_payment_payform(float(final_price), desc, pid, f"https://t.me/{get_setting('telegram_bot_username')}", f"https://t.me/{get_setting('telegram_bot_username')}")
                if url:
                    kb = create_payment_keyboard(url)
                    await _send_telegram_message(user_id, f"<b>Оплата через Platega</b>\n\nСумма: <b>{final_price:.2f} RUB</b>\n\n<i>Счет также доступен в WebApp.</i>", kb)
                    return {"ok": True, "payment_url": url, "payment_id": pid, "message": "Счёт создан"}
                return {"ok": False, "error": "Ошибка получения ссылки Platega"}
            except Exception as e:
                return {"ok": False, "error": f"Ошибка Platega: {e}"}

        # --- Platega ---
        elif method_id == "pay_platega":
            mid, key = get_setting("platega_merchant_id"), get_setting("platega_api_key")
            if not mid or not key: return {"ok": False, "error": "Platega не настроена"}
            pid = str(uuid.uuid4())
            meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "Platega", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
            create_payload_pending(pid, user_id, float(final_price), meta)
            desc = f"Order {pid}"
            try:
                platega = PlategaAPI(mid, key)
                _, url = await platega.create_payment(float(final_price), desc, pid, f"https://t.me/{get_setting('telegram_bot_username')}", f"https://t.me/{get_setting('telegram_bot_username')}", 2)
                if url:
                    kb = create_payment_keyboard(url)
                    await _send_telegram_message(user_id, f"<b>Оплата через Platega</b>\n\nСумма: <b>{final_price:.2f} RUB</b>\n\n<i>Счет также доступен в WebApp.</i>", kb)
                    return {"ok": True, "payment_url": url, "payment_id": pid, "message": "Счёт создан"}
                return {"ok": False, "error": "Ошибка получения ссылки Platega"}
            except Exception as e:
                return {"ok": False, "error": f"Ошибка Platega: {e}"}

        # --- Platega Crypto ---
        elif method_id == "pay_platega_crypto":
            mid, key = get_setting("platega_merchant_id"), get_setting("platega_api_key")
            if not mid or not key: return {"ok": False, "error": "Platega не настроена"}
            pid = str(uuid.uuid4())
            meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "Platega Crypto", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
            create_payload_pending(pid, user_id, float(final_price), meta)
            desc = f"Order {pid}"
            try:
                platega = PlategaAPI(mid, key)
                _, url = await platega.create_payment(float(final_price), desc, pid, f"https://t.me/{get_setting('telegram_bot_username')}", f"https://t.me/{get_setting('telegram_bot_username')}", 13)
                if url:
                    kb = create_payment_keyboard(url)
                    await _send_telegram_message(user_id, f"<b>Оплата через Platega (Crypto)</b>\n\nСумма: <b>{final_price:.2f} RUB</b>\n\n<i>Счет также доступен в WebApp.</i>", kb)
                    return {"ok": True, "payment_url": url, "payment_id": pid, "message": "Счёт создан"}
                return {"ok": False, "error": "Ошибка получения ссылки Platega Crypto"}
            except Exception as e:
                 return {"ok": False, "error": f"Ошибка Platega Crypto: {e}"}

         # --- CryptoBot ---
        elif method_id == "pay_cryptobot":
             pid = str(uuid.uuid4())
             meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "CryptoBot", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
             create_payload_pending(pid, user_id, float(final_price), meta)
             # payload_str format MUST match what bot expects. Using a generic format for now or just ID
             # safe encoded payload
             payload_str = f"{pid}" 
             
             try:
                 # Note: create_cryptobot_api_invoice IS imported now
                 res = await create_cryptobot_api_invoice(amount=float(final_price), payload_str=payload_str)
                 if res:
                     # res[0] is url, res[1] is invoice_id
                     kb = create_cryptobot_payment_keyboard(res[0], res[1])
                     await _send_telegram_message(user_id, f"<b>Оплата через CryptoBot</b>\n\nСумма: <b>{final_price:.2f} RUB</b>\n\n<i>Счет также доступен в WebApp.</i>", kb)
                     logger.info(f"[WEBAPP] - Успешно создан счет CryptoBot для {user_id}: {pid}")
                     return {"ok": True, "payment_url": res[0], "payment_id": pid, "message": "Счёт создан"}
                 logger.error(f"[WEBAPP] - Ошибка API CryptoBot для {user_id}")
                 return {"ok": False, "error": "Ошибка API CryptoBot"}
             except Exception as e:
                 return {"ok": False, "error": f"Ошибка CryptoBot: {e}"}
             
        # --- Heleket ---
        elif method_id == "pay_heleket":
            pid = str(uuid.uuid4())
            meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "Heleket", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
            create_payload_pending(pid, user_id, float(final_price), meta)
            
            try:
                result = await create_heleket_payment_request(
                    amount=float(final_price), 
                    currency="RUB", 
                    description=f"Payment for {req.host_name}",
                    return_url=f"https://t.me/{get_setting('telegram_bot_username')}",
                    user_id=user_id,
                    email=user.get('email', 'no-email')
                )
                
                if result and result.get('payment_url'):
                    pay_url = result['payment_url']
                    kb = create_payment_keyboard(pay_url)
                    await _send_telegram_message(user_id, f"<b>Оплата через Crypto (Heleket)</b>\n\nСумма: <b>{final_price:.2f} RUB</b>", kb)
                    return {"ok": True, "payment_url": pay_url, "payment_id": pid}
                else:
                     return {"ok": False, "error": "Ошибка создания платежа Heleket"}

            except Exception as e:
                logger.error(f"[WEBAPP] - Ошибка Heleket для {user_id}: {e}")
                return {"ok": False, "error": f"Ошибка Heleket: {e}"}
                
        # --- YooMoney ---
        elif method_id == "pay_yoomoney":
             receiver = get_setting("yoomoney_receiver")
             if not receiver: return {"ok": False, "error": "YooMoney не настроен"}
             pid = str(uuid.uuid4())
             meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "YooMoney", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
             create_payload_pending(pid, user_id, float(final_price), meta)
             label = pid
             desc = get_transaction_comment({"id": user_id, "username": user.get("username")}, action_name, months, req.host_name)
             link = _build_yoomoney_link(receiver, Decimal(str(final_price)), label, desc)
             
             kb = create_yoomoney_payment_keyboard(link, pid)
             await _send_telegram_message(user_id, f"<b>Оплата через ЮMoney (кошелёк)</b>\n\nСумма: <b>{final_price:.2f} RUB</b>\n\n<i>Счет также доступен в WebApp.</i>", kb)
             
             return {"ok": True, "payment_url": link, "payment_id": pid, "message": "Счёт создан"}

        # --- TON Connect ---
        elif method_id == "pay_tonconnect":
             return {"ok": False, "error": "TON Connect пока недоступен через WebApp"}

        # --- Stars ---
        elif method_id == "pay_stars":
             try:
                stars_ratio = float(get_setting("stars_per_rub") or 0)
             except: stars_ratio = 0
             if stars_ratio <= 0: return {"ok": False, "error": "Stars отключены"}
             stars_amount = max(1, int((final_price * stars_ratio)))
             pid = str(uuid.uuid4())
             meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "Telegram Stars", "payment_id": pid,
                "tier_device_count": tier_device_count
            }
             create_payload_pending(pid, user_id, float(final_price), meta)
             title = f"{'Подписка' if action_name == 'new' else 'Продление'} на {months} мес."
             desc = get_transaction_comment({"id": user_id, "username": user.get("username")}, action_name, months, req.host_name)
             await _send_invoice_stars(user_id, title, desc, pid, stars_amount)
             bot_username = get_setting('telegram_bot_username')
             logger.info(f"[WEBAPP] - Успешно отправлен счет Stars для {user_id} на {stars_amount} звезд")
             return {"ok": True, "message": "Счёт Stars отправлен в бот", "payment_url": f"tg://resolve?domain={bot_username}"}

        # --- Balance ---
        elif method_id == "pay_balance":
            if not deduct_from_balance(user_id, float(final_price)):
                return {"ok": False, "error": "Недостаточно средств"}
                
            p_log_id = str(uuid.uuid4())
            meta = {
                "user_id": user_id, "months": months, "price": float(final_price),
                "action": action_name, "key_id": req.key_id, "host_name": req.host_name,
                "plan_id": plan_id, "payment_method": "Balance", "promo_code": "", "promo_discount": 0,
                "tier_device_count": tier_device_count,
                "payment_id": p_log_id
            }
            token = get_setting("telegram_bot_token")
            bot = Bot(token=token) if token else None
            
            success = False
            if bot:
                try:
                    res = await asyncio.wait_for(process_successful_payment(bot, meta), timeout=15.0)
                    if res is True or check_transaction_exists(p_log_id):
                        success = True
                except asyncio.TimeoutError:
                    logger.warning("Способ 1: Таймаут бота")
                    if check_transaction_exists(p_log_id):
                        success = True
                except Exception as e:
                    logger.error(f"Способ 1 ошибка: {e}")
                    if check_transaction_exists(p_log_id):
                        success = True
            
            if not success and not check_transaction_exists(p_log_id):
                logger.info("Способ 2: Создаем ключ независимо от бота")
                try:
                    res = await process_successful_payment(None, meta)
                    if res is True or check_transaction_exists(p_log_id):
                        success = True
                except Exception as e:
                    logger.error(f"Способ 2 ошибка: {e}")
                    
            if bot:
                await bot.session.close()
                
            if not success and not check_transaction_exists(p_log_id):
                logger.error(f"[WEBAPP] - Критическая ошибка списания с баланса для {user_id}")
                return {"ok": False, "error": "Ошибка обработки платежа"}
                
            logger.info(f"[WEBAPP] - Успешная оплата с баланса: User={user_id}, Sum={final_price}")
            return {"ok": True, "message": "Оплачено с баланса!", "paid": True}

        return {"ok": False, "error": "Метод не поддерживается"}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка API создания платежа: {e}")
        return {"ok": False, "error": str(e), "details": traceback.format_exc()}

@app.post("/api/apply-promo")
async def api_apply_promo(req: ApplyPromoRequest, request: Request):
    try:
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}
        user_id = req.user_id
        code = req.promo_code.strip().upper()
        
        promo, error = rw_repo.check_promo_code_available(code, user_id)
        if not promo:
            errors = {
                "not_found": "Промокод не найден",
                "inactive": "Промокод не активен",
                "not_started": "Акция еще не началась",
                "expired": "Срок действия промокода истек",
                "total_limit_reached": "Промокод закончился",
                "user_limit_reached": "Вы уже использовали этот промокод",
                "empty_code": "Введите промокод"
            }
            return {"ok": False, "error": errors.get(error, "Ошибка проверки промокода")}

        promo_type = promo.get('promo_type')

        # 1 активация ЛЮБОГО промокода на аккаунт. Проверяем только при АКТИВАЦИИ (без цены);
        # путь покупки (с ценой) применяет уже активированную скидку и не блокируется.
        if req.price is None and rw_repo.has_activated_promo(user_id, code):
            return {"ok": False, "error": "Вы уже использовали этот промокод"}

        # 1. DISCOUNT (For Payment Modal)
        if promo_type == 'discount':
            if req.price is None:
                # Активация из профиля (без цены): запоминаем скидку на аккаунт —
                # применится к ПЕРВОЙ покупке, потом сгорает (одноразово).
                rw_repo.set_active_promo_code(user_id, code)
                rw_repo.add_activated_promo(user_id, code)
                dp = float(promo.get('discount_percent') or 0)
                da = float(promo.get('discount_amount') or 0)
                _msg = (f"🎟 Скидка {int(dp)}% активирована!" if dp else f"🎟 Скидка {int(da)} ₽ активирована!") + " Применится при покупке."
                return {
                    "ok": True, "promo_type": "discount",
                    "discount_percent": dp or None, "discount_amount": da or None,
                    "code": code, "message": _msg,
                }

            new_price = float(req.price)
            if promo.get('discount_percent'):
                new_price -= new_price * (float(promo['discount_percent']) / 100)
            elif promo.get('discount_amount'):
                new_price -= float(promo['discount_amount'])
            
            return {
                "ok": True, 
                "promo_type": "discount", 
                "new_price": max(0, round(new_price, 2))
            }

        # 2. BALANCE or UNIVERSAL (For Profile)
        elif promo_type == 'balance':
            reward = float(promo.get('reward_value', 0))
            if rw_repo.adjust_user_balance(user_id, reward):
                rw_repo.redeem_universal_promo(code, user_id)
                rw_repo.add_activated_promo(user_id, code)
                return {"ok": True, "promo_type": "balance", "message": f"Зачислено {reward} ₽"}
            return {"ok": False, "error": "Ошибка начисления баланса"}

        elif promo_type == 'universal':
            days_to_add = int(promo.get('reward_value') or 0)
            keys = rw_repo.get_user_keys(user_id)
            if not keys:
                 return {"ok": False, "error": "У вас нет активных подписок для продления"}
             
            keys.sort(key=lambda x: x.get('expiry_date', ''))
            key = keys[0]
            key_id = key['key_id']
            
            host = key.get('host_name')
            c_email = key.get('key_email')
             
            res = await remnawave_api.create_or_update_key_on_host(
                host_name=host,
                email=c_email,
                days_to_add=days_to_add,
                telegram_id=user_id
            )
            if res:
                rw_repo.update_key(key_id, remnawave_user_uuid=res['client_uuid'], expire_at_ms=res['expiry_timestamp_ms'])
                rw_repo.redeem_universal_promo(code, user_id)
                rw_repo.add_activated_promo(user_id, code)
                return {"ok": True, "promo_type": "universal", "message": f"Добавлено {days_to_add} дн."}
            else:
                return {"ok": False, "error": "Ошибка активации на стороне сервера"}

    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка API apply-promo для {user_id}: {e}")
        return {"ok": False, "error": str(e)}


class ActivePromoRequest(BaseModel):
    user_id: int


@app.post("/api/active-promo")
async def api_active_promo(req: ActivePromoRequest, request: Request):
    """Активная (ожидающая покупки) разовая скидка аккаунта — для баннера и авто-применения на фронте."""
    try:
        if not _owns(request, req.user_id):
            return {"ok": True, "active": None}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": True, "active": None}
        code = rw_repo.get_active_promo_code(req.user_id)
        if not code:
            return {"ok": True, "active": None}
        promo, error = rw_repo.check_promo_code_available(code, req.user_id)
        if not promo or promo.get('promo_type') != 'discount':
            rw_repo.clear_active_promo_code(req.user_id)  # уже использован/недоступен → снять
            return {"ok": True, "active": None}
        return {"ok": True, "active": {
            "code": code,
            "discount_percent": float(promo.get('discount_percent') or 0) or None,
            "discount_amount": float(promo.get('discount_amount') or 0) or None,
        }}
    except Exception as e:
        logger.error(f"[WEBAPP] - active-promo: {e}")
        return {"ok": False, "active": None}


def _compute_delete_refund(user_id: int, key: dict) -> float:
    """Возврат при удалении ключа: пропорц. остатку дней, от РЕАЛЬНО уплаченного последнего
    платежа юзера. Триал / мигрированные / без нашего платежа / истёкший → 0. Без абьюза
    (от уплаченного, с потолком = сама сумма)."""
    try:
        if (key.get('tag') or '') == 'TRIAL':
            return 0.0
        data = _process_key_data(key)
        days_left = int(data.get('days_left') or 0)
        if days_left <= 0:
            return 0.0
        key_id = int(key.get('key_id') or 0)
        from shop_bot.data_manager.database import get_transactions_for_user
        txs = get_transactions_for_user(user_id, limit=100) or []
        # Берём платёж ИМЕННО ЗА ЭТОТ ключ (metadata.key_id == key_id) — последний по дате
        # (последняя покупка/продление этого ключа). Так возврат не превышает уплаченного
        # за конкретный ключ и не путается у мульти-ключевых юзеров (раньше брался просто
        # последний платёж юзера → переплата/абуз при нескольких ключах).
        last_amount = None
        last_months = 1
        for t in txs:  # get_transactions_for_user отдаёт сортировку по дате убыванием
            st = (t.get('status') or '').lower()
            amt = float(t.get('amount_rub') or 0)
            pm = (t.get('payment_method') or '').lower()
            if st not in ('success', 'paid') or amt <= 0 or pm == 'admin':
                continue
            try:
                meta = t.get('metadata')
                meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
            except Exception:
                meta = {}
            try:
                t_key_id = int(meta.get('key_id') or 0)
            except Exception:
                t_key_id = 0
            if t_key_id != key_id:
                continue
            last_amount = amt
            try:
                last_months = int(meta.get('months') or 1) or 1
            except Exception:
                last_months = 1
            break
        if last_amount is None:
            return 0.0
        amount = float(last_amount)
        months = last_months
        period_days = max(1, months * 30)
        refund = round(min(amount, amount * min(1.0, days_left / period_days)), 2)
        return refund if refund > 0 else 0.0
    except Exception as e:
        logger.error(f"_compute_delete_refund({user_id}): {e}")
        return 0.0


class DeleteKeyRequest(BaseModel):
    user_id: int
    key_id: int


@app.post("/api/delete-key")
async def api_delete_key(req: DeleteKeyRequest, request: Request):
    """Удаление ключа юзером + пропорциональный возврат на баланс (от реально уплаченного)."""
    try:
        from shop_bot.data_manager import database as _db
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}
        # SECURITY: проверка владельца теперь ОБЯЗАТЕЛЬНА (раньше пропускалась, если токен
        # просто не прислать → удаление чужого ключа по одному user_id).
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}

        key = rw_repo.get_key_by_id(req.key_id)
        if not key or int(key.get('user_id') or 0) != int(req.user_id):
            return {"ok": False, "error": "Ключ не найден"}

        refund = _compute_delete_refund(req.user_id, key)

        uuid = key.get('remnawave_user_uuid')
        host = key.get('host_name') or 'SpectraSokol'
        if uuid:
            try:
                await remnawave_api.delete_user_on_host(host, uuid)
            except Exception as e:
                logger.error(f"[WEBAPP] - delete-key panel {uuid}: {e}")
        try:
            rw_repo.delete_key_by_email(key.get('key_email'))
        except Exception as e:
            logger.warning(f"[WEBAPP] - delete-key local: {e}")

        refunded = 0.0
        if refund and refund > 0:
            try:
                if rw_repo.adjust_user_balance(req.user_id, refund):
                    refunded = refund
            except Exception as e:
                logger.error(f"[WEBAPP] - delete-key refund: {e}")

        msg = "Ключ удалён."
        if refunded > 0:
            msg = f"Ключ удалён. Возвращено {refunded:.0f} ₽ на баланс."
        return {"ok": True, "refund": refunded, "message": msg}
    except Exception as e:
        logger.error(f"[WEBAPP] - delete-key {req.user_id}: {e}")
        return {"ok": False, "error": str(e)}


class CheckPaymentRequest(BaseModel):
    payment_id: str

@app.post("/api/check-payment")
async def api_check_payment(req: CheckPaymentRequest):
    try:
        if not req.payment_id or req.payment_id == "undefined" or req.payment_id == "null":
            return {"ok": False, "error": "Invalid payment_id"}
            
        exists = check_transaction_exists(req.payment_id)
        if exists:
            return {"ok": True, "paid": True, "message": "Оплата успешно подтверждена"}

        # Транзакция ещё не завершена вебхуком — активно спрашиваем статус у PSP (fail-closed).
        paid_now = await _verify_and_fulfill_pending(req.payment_id)
        if paid_now:
            return {"ok": True, "paid": True, "message": "Оплата успешно подтверждена"}
        return {"ok": True, "paid": False}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка проверки платежа {req.payment_id}: {e}")
        return {"ok": False, "error": str(e)}


async def _verify_and_fulfill_pending(payment_id: str) -> bool:
    """Активно проверяет статус платежа у PSP и при подтверждении атомарно завершает выдачу.
    Безопасно (fail-closed): выдача только при явном подтверждении PSP; повторная выдача исключена
    идемпотентностью process_successful_payment и атомарным claim find_and_complete_pending_transaction."""
    from shop_bot.data_manager.database import get_pending_metadata, find_and_complete_pending_transaction

    meta = get_pending_metadata(payment_id)
    if not meta:
        return False

    method = str(meta.get("payment_method") or "").strip().lower()
    psp_paid = False
    try:
        if method == "yookassa":
            shop_id, secret = get_setting("yookassa_shop_id"), get_setting("yookassa_secret_key")
            yk_id = meta.get("yk_payment_id")
            if shop_id and secret and yk_id:
                YookassaConfiguration.account_id = shop_id
                YookassaConfiguration.secret_key = secret
                yk_obj = YookassaPayment.find_one(str(yk_id))
                psp_paid = bool(yk_obj and getattr(yk_obj, "status", "") == "succeeded")
        elif method.startswith("platega"):
            mid, key = get_setting("platega_merchant_id"), get_setting("platega_api_key")
            if mid and key:
                platega = PlategaAPI(mid, key)
                psp_paid = bool(await platega.check_payment(payment_id))
        # Heleket и прочие методы — полагаемся на вебхук (статус-API не реализован)
    except Exception as e:
        logger.warning(f"[WEBAPP] - Не удалось проверить статус у PSP для {payment_id} ({method}): {e}")
        return False

    if not psp_paid:
        return False

    claimed = find_and_complete_pending_transaction(payment_id)
    if not claimed:
        # Уже завершено параллельно (вебхук/другой опрос) — считаем оплаченным
        return True

    token = get_setting("telegram_bot_token")
    if token:
        bot = Bot(token=token)
        try:
            await process_successful_payment(bot, claimed)
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass
    logger.info(f"[WEBAPP] - Платёж {payment_id} подтверждён через ручную проверку PSP ({method}) и выдан.")
    return True

class KeyActionRequest(BaseModel):
    user_id: int
    key_id: int
    host_name: str | None = None

class DeleteDeviceRequest(BaseModel):
    user_id: int
    key_id: int
    device_id: str
    host_name: str | None = None

class CommentRequest(BaseModel):
    user_id: int
    key_id: int
    comment: str

class RenameDeviceRequest(BaseModel):
    user_id: int
    key_id: int
    device_id: str
    label: str = ""
    host_name: str | None = None

class TrialClaimRequest(BaseModel):
    user_id: int

@app.post("/api/key/devices")
async def api_key_devices(req: KeyActionRequest, request: Request):
    try:
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        from shop_bot.data_manager.remnawave_repository import get_key_by_id
        from shop_bot.data_manager import database
        from shop_bot.modules import remnawave_api
        key = get_key_by_id(req.key_id)
        if not key or key.get("user_id") != req.user_id:
            return {"ok": False, "error": "Ключ не найден"}
            
        uuid_val = key.get("remnawave_user_uuid")
        if not uuid_val:
            return {"ok": False, "error": "Ключ не имеет привязки к серверу"}
            
        host = req.host_name or key.get("host_name")
        devices_data = await remnawave_api.get_connected_devices_count(uuid_val, host_name=host)
        devices = []
        if devices_data and "devices" in devices_data:
            devices = devices_data["devices"] or []

        try:
            labels = database.get_device_labels(uuid_val)
        except Exception:
            labels = {}
        for d in devices:
            if isinstance(d, dict):
                hwid = d.get("hwid")
                if hwid and labels.get(hwid):
                    d["label"] = labels[hwid]

        return {"ok": True, "devices": devices}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка получения устройств для ключа {req.key_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/key/device/delete")
async def api_key_device_delete(req: DeleteDeviceRequest, request: Request):
    try:
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        from shop_bot.data_manager.remnawave_repository import get_key_by_id
        from shop_bot.data_manager import database
        from shop_bot.modules import remnawave_api
        key = get_key_by_id(req.key_id)
        if not key or key.get("user_id") != req.user_id:
            return {"ok": False, "error": "Ключ не найден"}
            
        uuid_val = key.get("remnawave_user_uuid")
        if not uuid_val:
            return {"ok": False, "error": "Ключ не имеет привязки"}
            
        host = req.host_name or key.get("host_name")
        success = await remnawave_api.delete_user_device(uuid_val, req.device_id, host_name=host)
        if success:
            try:
                database.delete_device_label(uuid_val, req.device_id)
            except Exception:
                pass
            return {"ok": True}
        return {"ok": False, "error": "Не удалось удалить устройство"}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка удаления устройства для ключа {req.key_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/key/device/rename")
async def api_key_device_rename(req: RenameDeviceRequest, request: Request):
    try:
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        from shop_bot.data_manager.remnawave_repository import get_key_by_id
        from shop_bot.data_manager import database
        key = get_key_by_id(req.key_id)
        if not key or key.get("user_id") != req.user_id:
            return {"ok": False, "error": "Ключ не найден"}

        uuid_val = key.get("remnawave_user_uuid")
        if not uuid_val:
            return {"ok": False, "error": "Ключ не имеет привязки"}

        label = (req.label or "").strip()[:64]
        ok = database.set_device_label(uuid_val, req.device_id, label)
        if ok:
            return {"ok": True, "label": label}
        return {"ok": False, "error": "Не удалось сохранить название"}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка переименования устройства для ключа {req.key_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/trial/claim")
async def api_trial_claim(req: TrialClaimRequest, request: Request):
    try:
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        from shop_bot.data_manager import database
        from shop_bot.modules import remnawave_api

        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        if not _is_trial_enabled():
            return {"ok": False, "error": "Пробный период недоступен"}

        if bool(user.get('trial_used')):
            return {"ok": False, "error": "Пробный период уже использован"}

        hosts = get_all_hosts(visible_only=True) or []
        if not hosts:
            return {"ok": False, "error": "Нет доступных серверов"}

        forced = get_setting("trial_host_id")
        host_name = forced if (forced and any(h['host_name'] == forced for h in hosts)) else hosts[0]['host_name']

        raw_user = (user.get('username') or f'user{req.user_id}').lower()
        clean = re.sub(r"[^a-z0-9_-]", "", raw_user.replace(".", "_").replace(" ", ""))
        slug = clean.lstrip("_-")[:16] or f"user{req.user_id}"
        attempt = 1
        while True:
            candidate_email = f"trial_{slug}{f'-{attempt}' if attempt > 1 else ''}@bot.local"
            if not rw_repo.get_key_by_email(candidate_email) or attempt > 100:
                break
            attempt += 1

        trial_days = int(get_setting("trial_duration_days") or 0) or 1
        trial_traffic = int(get_setting("trial_traffic_limit_gb") or 0)
        trial_hwid = int(get_setting("trial_hwid_limit") or 0) or 2
        result = await remnawave_api.create_or_update_key_on_host(
            host_name=host_name,
            email=candidate_email,
            days_to_add=trial_days,
            telegram_id=req.user_id,
            traffic_limit_gb=trial_traffic if trial_traffic > 0 else None,
            hwid_limit=trial_hwid if trial_hwid > 0 else None,
        )
        if not result:
            return {"ok": False, "error": "Не удалось создать пробный ключ"}

        database.set_trial_used(req.user_id)
        new_key_id = record_key_from_payload(user_id=req.user_id, payload=result, host_name=host_name, hwid_limit=trial_hwid)

        # Пост-активация: шлём юзеру в чат ключ + ТЕ ЖЕ действия, что после оплаты:
        # «📲 Подключиться» (web-app Remna) + «📱 QR-код» + инструкция + мои ключи.
        try:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            from aiogram.types import WebAppInfo
            from datetime import datetime
            key = rw_repo.get_key_by_email(candidate_email) or {}
            sub_url = (key.get("subscription_url") or (result or {}).get("subscriptionUrl") or "").strip()
            exp_raw = str(key.get("expire_at") or "")
            try:
                exp_disp = datetime.fromisoformat(exp_raw.replace("Z", "").split("+")[0].split(".")[0]).strftime("%d.%m.%Y %H:%M")
            except Exception:
                exp_disp = exp_raw or f"{trial_days} дн."
            text = (
                "🎉 <b>Пробный период активирован!</b>\n"
                f"📅 Действует до: <b>{exp_disp}</b>\n\n"
            )
            if sub_url:
                text += ("🔗 <b>Твоя ссылка-подписка</b> (нажми, чтобы скопировать):\n"
                         f"<code>{sub_url}</code>\n\n")
            text += ("<b>Как подключиться:</b>\n"
                     "1️⃣ Нажми «📲 Подключиться» ниже — подписка добавится в приложение\n"
                     "2️⃣ Нет приложения? Жми «📖 Инструкция» — поможем установить\n"
                     "3️⃣ Готово, можно пользоваться 🚀")
            kb = InlineKeyboardBuilder()
            if sub_url.startswith("https://"):
                kb.button(text="📲 Подключиться", web_app=WebAppInfo(url=sub_url))
            if new_key_id:
                kb.button(text="📱 QR-код", callback_data=f"show_qr_{new_key_id}")
            kb.button(text="📖 Инструкция по подключению", callback_data="howto_vless")
            kb.button(text="🔑 Мои ключи", callback_data="manage_keys")
            kb.adjust(1)
            await _send_telegram_message(req.user_id, text, reply_markup=kb.as_markup())
        except Exception as e:
            logger.warning(f"[WEBAPP] - пост-активация trial для {req.user_id} не отправлена: {e}")

        return {"ok": True}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка выдачи trial для {req.user_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/key/comment")
async def api_key_comment(req: CommentRequest, request: Request):
    try:
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        from shop_bot.data_manager.remnawave_repository import get_key_by_id, update_key
        key = get_key_by_id(req.key_id)
        if not key or key.get("user_id") != req.user_id:
            return {"ok": False, "error": "Ключ не найден"}
            
        update_key(req.key_id, comment_key=req.comment)
        return {"ok": True}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка обновления комментария для ключа {req.key_id}: {e}")
        return {"ok": False, "error": str(e)}


@app.get("/api/key/connection-status")
async def api_key_connection_status(user_id: int, request: Request):
    """Мастер подключения: «подключился ли VPN?». По ключам юзера спрашиваем панель про
    устройства (HWID регистрируется при подключении) и трафик. Вердикт:
    connected (есть устройства/трафик) / pending (ключ есть, ещё не подключался) / no_key."""
    try:
        if not _owns(request, user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}
        keys = [k for k in (get_user_keys(user_id) or []) if k.get("remnawave_user_uuid")]
        if not keys:
            return {"ok": True, "status": "no_key"}
        import asyncio as _asyncio
        acc = {"devices": 0, "traffic": 0}

        async def _check(k):
            uuid = str(k.get("remnawave_user_uuid"))
            host = k.get("host_name") or "SpectraSokol"
            try:
                devs = await remnawave_api.get_user_devices(uuid, host_name=host)
                acc["devices"] += len(devs or [])
            except Exception:
                pass
            try:
                u = await remnawave_api.get_user_by_uuid(uuid, host_name=host)
                if u:
                    t = 0
                    for kn in ("trafficUsed", "traffic", "used_traffic"):
                        if u.get(kn):
                            try:
                                t = int(u.get(kn))
                            except Exception:
                                t = 0
                            break
                    if not t:
                        try:
                            t = int(u.get("upload") or 0) + int(u.get("download") or 0)
                        except Exception:
                            t = 0
                    acc["traffic"] += t
            except Exception:
                pass

        try:
            await _asyncio.wait_for(_asyncio.gather(*[_check(k) for k in keys[:3]]), timeout=12)
        except Exception:
            pass  # панель недоступна/медленна — не роняем проверку
        connected = acc["devices"] > 0 or acc["traffic"] > 0
        return {"ok": True, "status": "connected" if connected else "pending", "devices": acc["devices"]}
    except Exception as e:
        logger.error(f"[WEBAPP] - connection-status {user_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/support/status")
async def api_support_status(req: SupportStatusRequest, request: Request):
    try:
        from shop_bot.data_manager import database as _db
        _tok = request.headers.get("x-auth-token") or request.cookies.get("auth_token") or request.query_params.get("token")
        _auth = _db.get_user_by_auth_token(_tok) if _tok else None
        if not _auth or int(_auth.get('telegram_id', 0)) != int(req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}
            
        from shop_bot.data_manager.remnawave_repository import get_user_tickets, get_ticket_messages
        tickets = get_user_tickets(req.user_id) or []
        open_tickets = [t for t in tickets if t.get('status') == 'open']
        if not open_tickets:
            return {"ok": True, "has_ticket": False}
        
        ticket = max(open_tickets, key=lambda t: int(t['ticket_id']))
        messages = get_ticket_messages(ticket['ticket_id']) or []
        
        formatted_messages = []
        for m in messages:
            if m.get('sender') == 'note':
                continue
            formatted_messages.append({
                "sender": m.get("sender"),
                "content": m.get("content"),
                "media": m.get("media"),
                "created_at": m.get("created_at")
            })
            
        return {
            "ok": True, 
            "has_ticket": True, 
            "ticket_id": ticket['ticket_id'],
            "subject": ticket.get('subject', 'Обращение без темы'),
            "status": ticket.get('status'),
            "messages": formatted_messages
        }
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка статуса поддержки для {req.user_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/support/create")
async def api_support_create(req: SupportTicketCreateRequest, request: Request):
    try:
        from shop_bot.data_manager import database as _db
        _tok = request.headers.get("x-auth-token") or request.cookies.get("auth_token") or request.query_params.get("token")
        _auth = _db.get_user_by_auth_token(_tok) if _tok else None
        if not _auth or int(_auth.get('telegram_id', 0)) != int(req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}
            
        from shop_bot.data_manager.remnawave_repository import get_or_create_open_ticket, add_support_message, get_setting
        
        full_text = req.subject.strip()
        subject_text = full_text[:64]
        if not full_text:
            return {"ok": False, "error": "Тема обращения не может быть пустой"}
            
        ticket_id, created_new = get_or_create_open_ticket(req.user_id, subject_text)
        
        if not ticket_id:
            return {"ok": False, "error": "Не удалось создать тикет"}
            
        if not created_new:
            return {"ok": False, "error": "У вас уже есть открытый тикет"}

        # Store the text the user typed as the first message so the chat is not empty.
        try:
            add_support_message(ticket_id, sender="user", content=full_text)
        except Exception as e:
            logger.warning(f"[WEBAPP] - Не удалось добавить первое сообщение тикета {ticket_id}: {e}")
            
        from aiogram import Bot
        token = get_setting("support_bot_token") or get_setting("telegram_bot_token")
        if token:
            bot = Bot(token=token)
            try:
                tg_chat = None
                try:
                    tg_chat = await bot.get_chat(req.user_id)
                    username_display = f"@{tg_chat.username}" if getattr(tg_chat, 'username', None) else f"ID {req.user_id}"
                except Exception:
                    username_display = f"ID {req.user_id}"

                import html
                from shop_bot.data_manager.database import get_admin_ids
                from shop_bot.support_user_card import send_support_user_card
                from shop_bot.support_inline_kb import build_admin_actions_kb, build_admin_dm_reply_kb

                short_notice = (
                    f"🆕 <b>Обращение #{ticket_id}</b> (WebApp)\n"
                    f"👤 <code>{req.user_id}</code> · {html.escape(username_display)}\n"
                    f"💬 <i>{html.escape(subject_text)}</i>\n\n"
                    f"<blockquote>{html.escape(full_text)}</blockquote>"
                )
                reply_kb = build_admin_dm_reply_kb(ticket_id)

                try:
                    from shop_bot.data_manager.remnawave_repository import update_ticket_thread_info
                    support_forum_chat_id = get_setting("support_forum_chat_id")
                    using_support_bot = bool(get_setting("support_bot_token"))
                    if support_forum_chat_id and using_support_bot:
                        forum_cid = int(support_forum_chat_id)
                        topic_name = f"#{ticket_id} • {username_display} • {subject_text}"[:120]
                        forum_topic = await bot.create_forum_topic(chat_id=forum_cid, name=topic_name)
                        t_thread_id = int(forum_topic.message_thread_id)
                        update_ticket_thread_info(ticket_id, str(forum_cid), t_thread_id)
                        await send_support_user_card(
                            bot,
                            forum_cid,
                            req.user_id,
                            message_thread_id=t_thread_id,
                            ticket_id=ticket_id,
                            tg_user=tg_chat,
                            pin=True,
                        )
                        await bot.send_message(
                            chat_id=forum_cid,
                            message_thread_id=t_thread_id,
                            text=short_notice,
                            parse_mode="HTML",
                        )
                        await bot.send_message(
                            chat_id=forum_cid,
                            message_thread_id=t_thread_id,
                            text="Панель управления тикетом:",
                            reply_markup=build_admin_actions_kb(ticket_id),
                        )
                except Exception as e:
                    logger.warning(f"[WEBAPP] - Не удалось создать тему форума для тикета {ticket_id}: {e}")

                for aid in get_admin_ids():
                    try:
                        await bot.send_message(
                            chat_id=int(aid),
                            text=short_notice,
                            parse_mode="HTML",
                            reply_markup=reply_kb,
                        )
                        await send_support_user_card(
                            bot,
                            int(aid),
                            req.user_id,
                            ticket_id=ticket_id,
                            tg_user=tg_chat,
                        )
                    except Exception:
                        pass
            finally:
                await bot.session.close()
                    
        return {"ok": True, "ticket_id": ticket_id}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка создания тикета поддержки для {req.user_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/support/close")
async def api_support_close(req: SupportCloseRequest, request: Request):
    try:
        from shop_bot.data_manager import database as _db
        _tok = request.headers.get("x-auth-token") or request.cookies.get("auth_token") or request.query_params.get("token")
        _auth = _db.get_user_by_auth_token(_tok) if _tok else None
        if not _auth or int(_auth.get('telegram_id', 0)) != int(req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get("is_banned"):
            return {"ok": False, "error": "Access denied"}

        from shop_bot.data_manager.remnawave_repository import get_ticket, set_ticket_status

        ticket = get_ticket(req.ticket_id)
        if not ticket or int(ticket.get("user_id") or 0) != int(req.user_id):
            return {"ok": False, "error": "Тикет не найден"}
        if (ticket.get("status") or "").strip().lower() != "open":
            return {"ok": False, "error": "Обращение уже закрыто"}

        if not set_ticket_status(req.ticket_id, "closed"):
            return {"ok": False, "error": "Не удалось закрыть обращение"}

        from aiogram import Bot
        token = get_setting("support_bot_token") or get_setting("telegram_bot_token")
        if token:
            bot = Bot(token=token)
            try:
                forum_chat_id = ticket.get("forum_chat_id")
                thread_id = ticket.get("message_thread_id")
                if forum_chat_id and thread_id:
                    try:
                        await bot.close_forum_topic(
                            chat_id=int(forum_chat_id),
                            message_thread_id=int(thread_id),
                        )
                    except Exception as e:
                        logger.warning(f"[WEBAPP] - close forum topic #{req.ticket_id}: {e}")
                    try:
                        username_display = f"ID {req.user_id}"
                        try:
                            tg_user = await bot.get_chat(req.user_id)
                            if getattr(tg_user, "username", None):
                                username_display = f"@{tg_user.username}"
                        except Exception:
                            pass
                        await bot.send_message(
                            chat_id=int(forum_chat_id),
                            message_thread_id=int(thread_id),
                            text=f"✅ Пользователь {username_display} закрыл тикет #{req.ticket_id}.",
                        )
                    except Exception as e:
                        logger.warning(f"[WEBAPP] - notify forum close #{req.ticket_id}: {e}")
            finally:
                await bot.session.close()

        return {"ok": True, "ticket_id": req.ticket_id}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка закрытия тикета {req.ticket_id} для {req.user_id}: {e}")
        return {"ok": False, "error": str(e)}


async def _mirror_support_user_message_to_telegram(user_id: int, ticket_id: int, message: str, ticket: dict):
    """Зеркалит сообщение веб-юзера в форум-топик группы + алерты админам. Выполняется В ФОНЕ (после ответа браузеру):
    медленные round-trip'ы к api.telegram.org из РФ-сервера больше не держат HTTP-ответ веб-чата (было «долго уходит»)."""
    try:
        from shop_bot.data_manager.remnawave_repository import get_setting
        from aiogram import Bot
        token = get_setting("support_bot_token") or get_setting("telegram_bot_token")
        if not token:
            return
        bot = Bot(token=token)
        try:
            # get_chat имеет смысл только для реальных Telegram-юзеров; у веб-юзеров (999…) чата нет → падал по таймауту
            if not str(user_id).startswith("999"):
                try:
                    u = await bot.get_chat(user_id)
                    username_display = f"@{u.username}" if getattr(u, 'username', None) else f"ID {user_id}"
                except Exception:
                    username_display = f"ID {user_id}"
            else:
                username_display = f"ID {user_id}"

            import html
            from shop_bot.data_manager.database import get_admin_ids
            from shop_bot.support_inline_kb import build_admin_dm_reply_kb
            notification_text = (
                f"✅ <b>Сообщение добавлено в тикет</b>\n\n"
                f"👤 <b>USER:</b> (<code>{user_id}</code> - {html.escape(username_display)})\n"
                f"📝 <b>ID тикета:</b> <code>#{ticket_id}</code>\n"
                f"💬 <b>Тема:</b> <i>{html.escape(ticket.get('subject', 'Без темы'))}</i>\n\n"
                f"💌 Сообщения:\n"
                f"<blockquote>{html.escape(message)}</blockquote>"
            )
            reply_kb = build_admin_dm_reply_kb(ticket_id)
            forum_chat_id = ticket.get('forum_chat_id')
            thread_id = ticket.get('message_thread_id')
            if forum_chat_id and thread_id:
                try:
                    await bot.send_message(
                        chat_id=int(forum_chat_id),
                        message_thread_id=int(thread_id),
                        text=notification_text,
                        parse_mode="HTML",
                        reply_markup=reply_kb,
                    )
                except Exception as e:
                    logger.warning(f"Error mirroring to forum: {e}")
            for aid in get_admin_ids():
                try:
                    await bot.send_message(
                        chat_id=int(aid),
                        text=notification_text,
                        parse_mode="HTML",
                        reply_markup=reply_kb
                    )
                except Exception:
                    pass
        finally:
            await bot.session.close()
    except Exception as e:
        logger.error(f"[WEBAPP] - Фоновое зеркалирование тикета #{ticket_id} упало: {e}")


@app.post("/api/support/send")
async def api_support_send(req: SupportMessageSendRequest, request: Request, background: BackgroundTasks):
    try:
        from shop_bot.data_manager import database as _db
        _tok = request.headers.get("x-auth-token") or request.cookies.get("auth_token") or request.query_params.get("token")
        _auth = _db.get_user_by_auth_token(_tok) if _tok else None
        if not _auth or int(_auth.get('telegram_id', 0)) != int(req.user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(req.user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        from shop_bot.data_manager.remnawave_repository import get_ticket, add_support_message
        ticket = get_ticket(req.ticket_id)
        if not ticket or ticket.get('user_id') != req.user_id or ticket.get('status') != 'open':
            return {"ok": False, "error": "Тикет не найден или закрыт"}

        add_support_message(req.ticket_id, sender="user", content=req.message)
        # Зеркало в Telegram (форум-топик + алерты админам) — В ФОНЕ, чтобы не держать ответ браузеру (фикс «долго уходит»)
        background.add_task(_mirror_support_user_message_to_telegram, req.user_id, req.ticket_id, req.message, ticket)
        return {"ok": True}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка отправки сообщения в поддержку для {req.user_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.post("/api/support/upload")
async def api_support_upload(request: Request, file: UploadFile = File(...), user_id: int = Form(...), ticket_id: int = Form(...)):
    """Загрузка вложения (фото/видео) в веб-чат поддержки: сохраняем у себя, пишем в тикет, зеркалим в форум-топик группы."""
    try:
        from shop_bot.data_manager import database as _db
        _tok = request.headers.get("x-auth-token") or request.cookies.get("auth_token") or request.query_params.get("token")
        _auth = _db.get_user_by_auth_token(_tok) if _tok else None
        if not _auth or int(_auth.get('telegram_id', 0)) != int(user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}

        from shop_bot.data_manager.remnawave_repository import get_ticket, add_support_message, get_setting
        ticket = get_ticket(ticket_id)
        if not ticket or ticket.get('user_id') != user_id or ticket.get('status') != 'open':
            return {"ok": False, "error": "Тикет не найден или закрыт"}

        ctype = (file.content_type or "").lower()
        is_image = ctype.startswith("image/")
        is_video = ctype.startswith("video/")
        if not (is_image or is_video):
            return {"ok": False, "error": "Можно прикрепить только фото или видео"}

        data = await file.read()
        if not data:
            return {"ok": False, "error": "Пустой файл"}
        max_bytes = 20 * 1024 * 1024 if is_video else 10 * 1024 * 1024
        if len(data) > max_bytes:
            return {"ok": False, "error": "Файл слишком большой (фото до 10 МБ, видео до 20 МБ)"}

        import uuid as _uuid, json as _json
        ext = os.path.splitext(file.filename or "")[1].lower()
        if not ext or len(ext) > 6 or "/" in ext or "\\" in ext:
            ext = ".jpg" if is_image else ".mp4"
        fname = f"{_uuid.uuid4().hex}{ext}"
        fpath = os.path.join(support_media_dir, fname)
        with open(fpath, "wb") as _f:
            _f.write(data)
        media_type = "video" if is_video else "image"
        media_url = f"/module/support-media/{fname}"
        media_json = _json.dumps({"type": media_type, "url": media_url})

        add_support_message(ticket_id, sender="user", content="", media=media_json)

        token = get_setting("support_bot_token") or get_setting("telegram_bot_token")
        if token:
            bot = Bot(token=token)
            try:
                cap = f"📎 Вложение от пользователя · тикет #{ticket_id}"
                forum_chat_id = ticket.get('forum_chat_id')
                thread_id = ticket.get('message_thread_id')
                if forum_chat_id and thread_id:
                    try:
                        if is_video:
                            await bot.send_video(chat_id=int(forum_chat_id), message_thread_id=int(thread_id), video=FSInputFile(fpath), caption=cap)
                        else:
                            await bot.send_photo(chat_id=int(forum_chat_id), message_thread_id=int(thread_id), photo=FSInputFile(fpath), caption=cap)
                    except Exception as e:
                        logger.warning(f"[WEBAPP] - Не удалось зеркалить вложение в форум: {e}")
                from shop_bot.data_manager.database import get_admin_ids
                for aid in (get_admin_ids() or []):
                    try:
                        await bot.send_message(chat_id=int(aid), text=f"📎 Новое вложение в тикет #{ticket_id} — смотрите тему в группе поддержки.")
                    except Exception:
                        pass
            finally:
                await bot.session.close()

        return {"ok": True, "media": {"type": media_type, "url": media_url}}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка загрузки вложения поддержки для {user_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/api/user-status")
async def api_user_status(user_id: int, request: Request):
    try:
        if not _owns(request, user_id):
            return {"ok": False, "error": "Access denied"}
        user = get_user(user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}
            
        keys = get_user_keys(user_id)
        # Sort keys by key_id descending to get the latest one first
        formatted_keys = []
        if keys:
            keys.sort(key=lambda k: k.get('key_id', 0), reverse=True)
            formatted_keys = [_process_key_data(k) for k in keys]
        
        return {"ok": True, "keys": formatted_keys}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка статуса пользователя {user_id}: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/api/contest")
async def api_contest(request: Request, user_id: int | None = None):
    """Активный конкурс + число билетов у пользователя (для баннера и экрана в аппе)."""
    try:
        from shop_bot.data_manager import database
        # чужое число билетов не раскрываем: без валидного токена показываем конкурс без «моих» билетов
        if user_id and not _owns(request, user_id):
            user_id = None
        contest = database.get_active_contest()
        if not contest:
            return {"ok": True, "active": False}
        tickets = 0
        if user_id:
            try:
                tickets = database.get_contest_tickets(int(user_id), contest)
            except Exception:
                tickets = 0
        return {
            "ok": True,
            "active": True,
            "tickets": tickets,
            "title": contest.get("title") or "",
            "description": contest.get("description") or "",
            "prizes": contest.get("prizes") or "",
            "start_date": contest.get("start_date") or "",
            "end_date": contest.get("end_date") or "",
        }
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка конкурса: {e}")
        return {"ok": False, "error": str(e)}

# ===== КОЛЕСО ФОРТУНЫ =====
async def _award_wheel_vpn(user_id: int, days: int) -> bool:
    """Продлевает последний ОПЛАЧЕННЫЙ ключ юзера на N дней (выдача VPN-приза колеса).

    Тестовые ключи (key_email trial_* или тег TRIAL) призов не получают:
    если у юзера только тестовые — приз не начисляется вовсе.
    Целевой ключ = key_id из последней оплаченной транзакции, фолбэк —
    самый свежий (по created_at) оплаченный ключ."""
    try:
        keys = rw_repo.get_user_keys(user_id)  # уже отсортированы по created_at DESC
        if not keys:
            return False

        def _is_trial_key(k: dict) -> bool:
            email = (k.get('key_email') or k.get('email') or '')
            return email.startswith('trial_') or (k.get('tag') or '').upper() == 'TRIAL'

        paid_keys = [k for k in keys if not _is_trial_key(k)]
        if not paid_keys:
            logger.info(f"[WEBAPP] - Колесо: у юзера {user_id} только тестовые ключи — приз не начисляем")
            return False
        key = None
        try:
            last_paid_id = rw_repo.get_last_paid_key_id(user_id)
            if last_paid_id:
                key = next((k for k in paid_keys if int(k.get('key_id') or 0) == int(last_paid_id)), None)
        except Exception:
            key = None
        if key is None:
            key = paid_keys[0]
        res = await remnawave_api.create_or_update_key_on_host(
            host_name=key.get('host_name'),
            email=key.get('key_email'),
            days_to_add=int(days),
            telegram_id=user_id,
        )
        if res:
            rw_repo.update_key(key['key_id'], remnawave_user_uuid=res['client_uuid'], expire_at_ms=res['expiry_timestamp_ms'])
            return True
        return False
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка выдачи VPN-приза колеса юзеру {user_id}: {e}")
        return False


@app.get("/api/wheel/state")
async def api_wheel_state(request: Request, user_id: int | None = None):
    """Состояние колеса: баланс спинов + все призы (для отрисовки, включая display-only)."""
    try:
        if (get_setting('wheel_enabled') or 'true') == 'false':
            return {"ok": False, "error": "disabled"}
        # чужой баланс спинов не раскрываем: без валидного токена показываем витрину с 0
        if user_id and not _owns(request, user_id):
            user_id = None
        prizes = rw_repo.get_wheel_prizes()
        spins = rw_repo.get_wheel_spins(int(user_id)) if user_id else 0
        out = [{"id": p.get("id"), "label": p.get("label"), "emoji": p.get("emoji") or "",
                "kind": p.get("kind"), "amount": int(p.get("amount") or 0)} for p in prizes]
        return {"ok": True, "spins": spins, "prizes": out}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка состояния колеса: {e}")
        return {"ok": False, "error": str(e)}


class WheelSpinRequest(BaseModel):
    user_id: int

@app.post("/api/wheel/spin")
async def api_wheel_spin(req: WheelSpinRequest, request: Request):
    """Спин: СЕРВЕР списывает спин, выбирает приз (только VPN-время, weight>0), выдаёт VPN. Клиент лишь анимирует."""
    try:
        if (get_setting('wheel_enabled') or 'true') == 'false':
            return {"ok": False, "error": "disabled"}
        if not _owns(request, req.user_id):
            return {"ok": False, "error": "Access denied"}
        user_id = int(req.user_id)
        user = get_user(user_id)
        if not user or user.get('is_banned'):
            return {"ok": False, "error": "Access denied"}
        prize = rw_repo.spin_wheel(user_id)
        if not prize:
            return {"ok": False, "error": "no_spins", "spins": rw_repo.get_wheel_spins(user_id)}
        awarded = False
        kind = prize.get('kind')
        amount = int(prize.get('amount') or 0)
        if kind in ('vpn_days', 'vpn_months') and amount > 0:
            days = amount if kind == 'vpn_days' else amount * 30
            awarded = await _award_wheel_vpn(user_id, days)
        return {"ok": True, "prize_id": prize.get('id'), "label": prize.get('label') or "",
                "emoji": prize.get('emoji') or "", "kind": kind, "amount": amount,
                "awarded": awarded, "spins": rw_repo.get_wheel_spins(user_id)}
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка спина колеса: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/{path_param}")
async def dynamic_route(request: Request, path_param: str):
    try:
        if path_param.startswith("token="):
            token = path_param.split("=")[1]
            from shop_bot.data_manager import database
            user = database.get_user_by_auth_token(token)
            if user:
                webapp_settings = get_webapp_settings()
                if user.get('is_banned'):
                    return _render_banned_page(webapp_settings)
                return await _render_main_page(user['telegram_id'])
            else:
                 # Token not valid or expired -> Render Login Page
                 p = os.path.join(os.path.dirname(__file__), "login.html")
                 if os.path.exists(p):
                     with open(p, "r", encoding="utf-8") as f:
                         content = f.read()
                     
                     webapp_settings = get_webapp_settings()
                     context = {
                        "webapp_logo": webapp_settings.get("webapp_logo") or "",
                        "webapp_icon": webapp_settings.get("webapp_icon") or ""
                     }
                     content = _process_template_placeholders(content, 0, webapp_settings, context)
                     return HTMLResponse(content=content)
                 else:
                     return HTMLResponse(content="<h1>Login page not found</h1>", status_code=404)
        
        # Pass through to 404 naturally or handle other dynamic routes
        return HTMLResponse(content="<h1>404 Not Found</h1>", status_code=404)
    except Exception as e:
        logger.error(f"[WEBAPP] - Ошибка динамического маршрута: {e}")
        return HTMLResponse(content="<h1>Error</h1>", status_code=500)
