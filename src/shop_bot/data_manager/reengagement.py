"""Движок ре-энгейджмента (воронка удержания).

Цепочки-напоминания на несколько дней, чтобы вернуть пользователя к подключению/покупке:
  • abandoned_pay — создал счёт, не оплатил (30мин / тот же день / следующий день)
  • no_trial      — зашёл, не активировал пробный (1ч / следующий день / через день)
  • winback       — платная подписка истекла, не продлил (1 / 3 / 7 / 14 дней)

ПРЕДОХРАНИТЕЛЬ (чтобы не надоедать):
  • мастер-тумблер reengage_enabled (по умолчанию OFF — без него ничего не шлётся);
  • тихие часы (reengage_quiet_start..end по МСК);
  • лимит на юзера: не больше N в сутки и M в неделю (reengage_daily_cap / _weekly_cap);
  • приоритет цепочек: не больше одного сообщения на юзера за один прогон;
  • персистентный дедуп через notification_log (не слать одно касание дважды);
  • стоп на конверсии/бане/optout — заложено в запросах кандидатов;
  • заблокировавших бота помечаем optout (перестаём слать).

Тексты/кнопки/вкл-выкл каждого касания — в реестре notifications.py и админке «Уведомления».
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import WebAppInfo

try:
    from aiogram.exceptions import TelegramForbiddenError
except Exception:  # на случай иной версии aiogram
    TelegramForbiddenError = Exception

from shop_bot.data_manager import database as db
from shop_bot.data_manager.database import get_setting
from shop_bot.data_manager.notifications import get_notif_text, notif_enabled, notif_buttons

logger = logging.getLogger(__name__)


def _msk_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=3)))


def _setting_int(key: str, default: int) -> int:
    try:
        return int(str(get_setting(key) or default).strip())
    except Exception:
        return default


# Цепочки. touch = (номер, ключ_реестра, min_offset, max_offset) — окно «созревания» касания
# в единицах кампании (minutes/hours/days). Кандидат-запрос возвращает dict с 'user_id'.
CAMPAIGNS = [
    {
        "id": "abandoned_pay", "priority": 1,
        "touches": [
            (1, "re_abandoned_pay_1", 30, 180),      # 30 мин .. 3 ч
            (2, "re_abandoned_pay_2", 240, 600),     # 4 .. 10 ч
            (3, "re_abandoned_pay_3", 1200, 2160),   # 20 .. 36 ч (следующий день)
        ],
        "candidates": lambda mn, mx: db.get_reengage_abandoned_pending(mn, mx),
    },
    {
        "id": "no_trial", "priority": 2,
        "touches": [
            (1, "re_no_trial_1", 1, 6),
            (2, "re_no_trial_2", 24, 34),
            (3, "re_no_trial_3", 48, 60),
        ],
        "candidates": lambda mn, mx: db.get_reengage_entered_no_trial(mn, mx),
    },
    {
        "id": "winback", "priority": 3,
        "touches": [
            (1, "re_winback_1", 1, 2),
            (2, "re_winback_2", 3, 4),
            (3, "re_winback_3", 7, 9),
            (4, "re_winback_4", 14, 16),
        ],
        "candidates": lambda mn, mx: db.get_reengage_winback(mn, mx),
    },
]


def _ctx_vars(cand: dict) -> dict:
    """Плейсхолдеры для текста/кнопок из данных кандидата (напр. сумма счёта)."""
    v = {}
    meta = cand.get("metadata") or {}
    amount = meta.get("price", meta.get("amount"))
    if amount is not None:
        try:
            v["amount"] = f"{float(amount):.0f}"
        except Exception:
            v["amount"] = str(amount)
    return v


async def _send(bot: Bot, user_id: int, nkey: str, cand: dict) -> bool:
    """Отправить одно касание. Возвращает True при успехе. Заблокировавших — в optout."""
    try:
        variables = _ctx_vars(cand)
        message = get_notif_text(nkey, **variables)
        try:
            from shop_bot.bot.handlers import _get_telegram_webapp_url
            webapp_url = _get_telegram_webapp_url()
        except Exception:
            webapp_url = None
        builder = InlineKeyboardBuilder()
        for b in notif_buttons(nkey, webapp_url=webapp_url, **variables):
            if b.get("web_app_url"):
                builder.button(text=b["label"], web_app=WebAppInfo(url=b["web_app_url"]))
            elif b.get("url"):
                builder.button(text=b["label"], url=b["url"])
            elif b.get("cb"):
                builder.button(text=b["label"], callback_data=b["cb"])
        builder.adjust(1)
        await bot.send_message(chat_id=int(user_id), text=message,
                               reply_markup=builder.as_markup(), parse_mode="Markdown")
        logger.info(f"reengage: отправлено {nkey} юзеру {user_id}")
        return True
    except TelegramForbiddenError:
        # бот заблокирован пользователем — перестаём его беспокоить (данные сохраняем)
        try:
            db.set_reengage_optout(int(user_id), 1)
        except Exception:
            pass
        logger.info(f"reengage: {user_id} заблокировал бота → optout")
        return False
    except Exception as e:
        logger.warning(f"reengage: не удалось отправить {nkey} юзеру {user_id}: {e}")
        return False


async def run_reengagement(bot: Bot) -> None:
    """Один прогон движка. Вызывается из планировщика каждый цикл."""
    if bot is None:
        return
    # Тест-режим: если задан reengage_test_only_uid — движок работает по обычной логике,
    # но РЕАЛЬНО шлёт только этому user_id (все остальные кандидаты пропускаются).
    # Позволяет протестировать конвейер на своём аккаунте, НЕ включая боевую рассылку.
    test_only = (get_setting("reengage_test_only_uid") or "").strip()
    try:
        test_only_uid = int(test_only) if test_only else 0
    except Exception:
        test_only_uid = 0
    master_on = (get_setting("reengage_enabled") or "false").strip().lower() == "true"
    if not master_on and not test_only_uid:
        return  # мастер выключен и тест-режим не задан — тишина

    now = _msk_now()
    q_start = _setting_int("reengage_quiet_start", 10)
    q_end = _setting_int("reengage_quiet_end", 21)
    if not (q_start <= now.hour < q_end):
        logger.debug("reengage: тихие часы, пропуск")
        return

    daily_cap = _setting_int("reengage_daily_cap", 1)
    weekly_cap = _setting_int("reengage_weekly_cap", 3)
    run_cap = _setting_int("reengage_run_cap", 25)   # не больше N отправок за один прогон
    sent_this_run: set[int] = set()
    total_sent = 0

    for camp in sorted(CAMPAIGNS, key=lambda c: c["priority"]):
        cid = camp["id"]
        for (touch_no, nkey, mn, mx) in camp["touches"]:
            if not notif_enabled(nkey):
                continue
            try:
                cands = camp["candidates"](mn, mx) or []
            except Exception as e:
                logger.error(f"reengage: запрос кандидатов {cid}/{touch_no} упал: {e}")
                continue
            for cand in cands:
                if run_cap > 0 and total_sent >= run_cap:
                    logger.info(f"reengage: достигнут лимит прогона ({run_cap}), остальное — в след. цикл")
                    return  # плавный слив бэклога пачками, чтобы не ловить флуд-лимит Telegram
                try:
                    uid = int(cand.get("user_id"))
                except Exception:
                    continue
                if test_only_uid and uid != test_only_uid:
                    continue  # тест-режим: шлём только указанному аккаунту
                if uid in sent_this_run:
                    continue  # приоритет: одно сообщение на юзера за прогон
                if db.was_notification_sent(uid, cid, touch_no):
                    continue  # это касание уже слали
                # лимиты частоты
                if daily_cap > 0 and db.count_notifications_since(uid, 24) >= daily_cap:
                    continue
                if weekly_cap > 0 and db.count_notifications_since(uid, 168) >= weekly_cap:
                    continue
                if await _send(bot, uid, nkey, cand):
                    db.log_notification(uid, cid, touch_no)
                    sent_this_run.add(uid)
                    total_sent += 1
                    await asyncio.sleep(0.15)  # мягко к лимитам Telegram (~30 msg/s)

    if total_sent:
        logger.info(f"reengage: прогон завершён, отправлено {total_sent} напоминаний")


async def send_test_reengagement(bot: Bot, user_id: int, nkey: str, amount: str = "199") -> bool:
    """Тест-отправка одного касания конкретному юзеру В ОБХОД предохранителя и дедупа.
    Для проверки владельцем перед боевым запуском. Ничего не логирует в notification_log."""
    return await _send(bot, int(user_id), nkey, {"metadata": {"price": amount}})
