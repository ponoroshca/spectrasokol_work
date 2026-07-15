import asyncio
import logging
import json

from datetime import datetime, timedelta, timezone

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Bot
from aiogram.types import WebAppInfo

from shop_bot.bot_controller import BotController
from shop_bot.data_manager import remnawave_repository as rw_repo
from shop_bot.data_manager import resource_monitor
from shop_bot.data_manager import speedtest_runner
from shop_bot.data_manager import backup_manager

from shop_bot.modules import remnawave_api
from shop_bot.bot import keyboards
from shop_bot.data_manager.notifications import get_notif_text, notif_hours, notif_enabled, notif_buttons
from shop_bot.data_manager.reengagement import run_reengagement

CHECK_INTERVAL_SECONDS = 300
NOTIFY_BEFORE_HOURS = {72, 48, 24, 1}
notified_users = {}

logger = logging.getLogger(__name__)


def get_msk_time() -> datetime:
    return datetime.now(timezone(timedelta(hours=3)))



SPEEDTEST_INTERVAL_SECONDS = 8 * 3600
_scheduler_start_time = get_msk_time()
_last_speedtests_run_at: datetime | None = None
_last_backup_run_at: datetime | None = None
_last_resource_collect_at: datetime | None = None
_last_resource_alert_at: dict[tuple[str, str, str], datetime] = {}

def format_time_left(hours: int) -> str:
    if hours >= 24:
        days = hours // 24
        if days % 10 == 1 and days % 100 != 11:
            return f"{days} день"
        elif 2 <= days % 10 <= 4 and (days % 100 < 10 or days % 100 >= 20):
            return f"{days} дня"
        else:
            return f"{days} дней"
    else:
        if hours % 10 == 1 and hours % 100 != 11:
            return f"{hours} час"
        elif 2 <= hours % 10 <= 4 and (hours % 100 < 10 or hours % 100 >= 20):
            return f"{hours} часа"
        else:
            return f"{hours} часов"

async def send_subscription_notification(bot: Bot, user_id: int, key_id: int, time_left_hours: int, expiry_date: datetime, is_trial: bool = False):
    try:
        time_text = format_time_left(time_left_hours)
        expiry_str = expiry_date.strftime('%d.%m.%Y в %H:%M')

        nkey = "trial_expiry" if is_trial else "sub_expiry"
        message = get_notif_text(nkey, time_text=time_text, expiry=expiry_str)
        try:
            from shop_bot.bot.handlers import _get_telegram_webapp_url
            _webapp_url = _get_telegram_webapp_url()
        except Exception:
            _webapp_url = None
        builder = InlineKeyboardBuilder()
        for _b in notif_buttons(nkey, webapp_url=_webapp_url, key_id=key_id):
            if _b.get("web_app_url"):
                builder.button(text=_b["label"], web_app=WebAppInfo(url=_b["web_app_url"]))
            elif _b.get("url"):
                builder.button(text=_b["label"], url=_b["url"])
            elif _b.get("cb"):
                builder.button(text=_b["label"], callback_data=_b["cb"])
        builder.adjust(2)

        await bot.send_message(chat_id=user_id, text=message, reply_markup=builder.as_markup(), parse_mode='Markdown')
        logger.debug(f"Scheduler: Отправлено уведомление пользователю {user_id} по ключу {key_id} (осталось {time_left_hours} ч).")
        
    except Exception as e:
        logger.error(f"Scheduler: Ошибка отправки уведомления пользователю {user_id}: {e}")

def _cleanup_notified_users(all_db_keys: list[dict]):
    if not notified_users:
        return

    logger.debug("Scheduler: Очищаю кэш уведомлений...")
    
    active_key_ids = {key['key_id'] for key in all_db_keys}
    
    users_to_check = list(notified_users.keys())
    
    cleaned_users = 0
    cleaned_keys = 0

    for user_id in users_to_check:
        keys_to_check = list(notified_users[user_id].keys())
        for key_id in keys_to_check:
            if key_id not in active_key_ids:
                del notified_users[user_id][key_id]
                cleaned_keys += 1
        
        if not notified_users[user_id]:
            del notified_users[user_id]
            cleaned_users += 1
    
    if cleaned_users > 0 or cleaned_keys > 0:
        logger.debug(f"Scheduler: Очистка завершена. Удалено записей пользователей: {cleaned_users}, ключей: {cleaned_keys}.")

async def check_expiring_subscriptions(bot: Bot):
    logger.debug("Scheduler: Проверяю истекающие подписки...")
    current_time = get_msk_time().replace(tzinfo=None)
    all_keys = rw_repo.get_all_keys()
    
    _cleanup_notified_users(all_keys)
    
    for key in all_keys:
        try:
            expiry_date = datetime.fromisoformat(key['expiry_date'])
            time_left = expiry_date - current_time

            if time_left.total_seconds() < 0:
                continue

            total_hours_left = int(time_left.total_seconds() / 3600)
            user_id = key['user_id']
            key_id = key['key_id']
            is_trial = str(key.get('key_email') or key.get('email') or '').startswith('trial_')

            nkey = "trial_expiry" if is_trial else "sub_expiry"
            if not notif_enabled(nkey):
                continue
            for hours_mark in notif_hours(nkey):
                if hours_mark - 1 < total_hours_left <= hours_mark:
                    notified_users.setdefault(user_id, {}).setdefault(key_id, set())
                    
                    if hours_mark not in notified_users[user_id][key_id]:
                        await send_subscription_notification(bot, user_id, key_id, hours_mark, expiry_date, is_trial=is_trial)
                        notified_users[user_id][key_id].add(hours_mark)
                    break 
                    
        except Exception as e:
            logger.error(f"Scheduler: Ошибка обработки истечения для ключа {key.get('key_id')}: {e}")

async def sync_keys_with_panels():
    logger.debug("Scheduler: Запускаю синхронизацию с Remnawave API...")
    total_affected_records = 0

    squads = rw_repo.list_squads()
    if not squads:
        logger.debug("Scheduler: Сквады Remnawave не настроены. Синхронизация пропущена.")
        return

    for squad in squads:
        host_name = (squad.get('host_name') or squad.get('name') or '').strip() or 'unknown'
        squad_uuid = (squad.get('squad_uuid') or squad.get('squadUuid') or '').strip()
        if not squad_uuid:
            logger.warning("Scheduler: Сквад '%s' не имеет squad_uuid — пропускаю синхронизацию.", host_name)
            continue

        try:
            remote_users = await remnawave_api.list_users(host_name=host_name, squad_uuid=squad_uuid)
        except Exception as exc:
            logger.error("Scheduler: Не удалось получить пользователей Remnawave для '%s': %s", host_name, exc)
            continue

        remote_by_email: dict[str, tuple[str, dict]] = {}
        remote_by_uuid: dict[str, dict] = {}
        for remote_user in remote_users or []:
            remote_uuid = (remote_user.get('uuid') or remote_user.get('id') or remote_user.get('client_uuid') or '').strip()
            if remote_uuid:
                remote_by_uuid[remote_uuid] = remote_user
            raw_email = (remote_user.get('email') or remote_user.get('accountEmail') or '').strip()
            if not raw_email:
                continue
            remote_by_email[raw_email.lower()] = (raw_email, remote_user)

        keys_in_db = rw_repo.get_keys_for_host(host_name) or []
        now = get_msk_time().replace(tzinfo=None)

        for db_key in keys_in_db:
            raw_email = (db_key.get('key_email') or db_key.get('email') or '').strip()
            normalized_email = raw_email.lower()
            if not raw_email:
                continue

            local_uuid = (db_key.get('remnawave_user_uuid') or '').strip()
            remote_entry = None
            remote_email = None
            remote_user = None
            matched_by = None

            # 1) Основной и самый надежный матч — по UUID (работает даже когда email=NULL в Remnawave)
            if local_uuid:
                remote_user = remote_by_uuid.pop(local_uuid, None)
                if remote_user:
                    matched_by = "uuid"
                    remote_email = (remote_user.get('email') or remote_user.get('accountEmail') or '').strip() or None
                    if remote_email:
                        remote_by_email.pop(remote_email.lower(), None)

            # 2) Фолбэк — по email
            if not remote_user:
                remote_entry = remote_by_email.pop(normalized_email, None)
                if remote_entry:
                    remote_email, remote_user = remote_entry
                    matched_by = "email"
                    matched_uuid = (remote_user.get('uuid') or remote_user.get('id') or remote_user.get('client_uuid') or '').strip()
                    if matched_uuid:
                        remote_by_uuid.pop(matched_uuid, None)

            # 3) Последний фолбэк: если есть UUID, но его не оказалось в list_users (пагинация/фильтр/гонка), проверяем точечно
            if not remote_user and local_uuid:
                try:
                    remote_user = await remnawave_api.get_user_by_uuid(local_uuid, host_name=host_name)
                except Exception as exc:
                    logger.debug(
                        "Scheduler: UUID lookup failed for '%s' on host '%s': %s",
                        local_uuid,
                        host_name,
                        exc,
                    )
                if remote_user:
                    matched_by = "uuid-direct"
                    remote_email = (remote_user.get('email') or remote_user.get('accountEmail') or '').strip() or None
                    if remote_email:
                        remote_by_email.pop(remote_email.lower(), None)

            expiry_raw = db_key.get('expiry_date') or db_key.get('expire_at')
            try:
                expiry_date = datetime.fromisoformat(str(expiry_raw)) if expiry_raw else None
            except Exception:
                try:
                    expiry_date = datetime.fromisoformat(str(expiry_raw).replace('Z', '+00:00'))
                except Exception:
                    expiry_date = None

            if expiry_date and expiry_date < now - timedelta(days=5):
                logger.info(
                    "Scheduler: Ключ '%s' (host '%s') просрочен >5 дней, но в safe mode удаление отключено.",
                    raw_email,
                    host_name,
                )
                continue

            if remote_user:
                remote_uuid = (remote_user.get('uuid') or remote_user.get('id') or remote_user.get('client_uuid') or '').strip()
                expire_value = remote_user.get('expireAt') or remote_user.get('expiryDate')
                remote_dt = None
                if expire_value:
                    try:
                        remote_dt = datetime.fromisoformat(str(expire_value).replace('Z', '+00:00'))
                    except Exception:
                        remote_dt = None
                local_ms = int(expiry_date.timestamp() * 1000) if expiry_date else None
                remote_ms = int(remote_dt.timestamp() * 1000) if remote_dt else None
                subscription_url = remnawave_api.extract_subscription_url(remote_user)
                local_subscription = db_key.get('subscription_url') or db_key.get('connection_string')

                needs_update = False
                if remote_ms is not None and local_ms is not None and abs(remote_ms - local_ms) > 1000:
                    needs_update = True
                if subscription_url and subscription_url != local_subscription:
                    needs_update = True
                if remote_uuid and remote_uuid != local_uuid:
                    needs_update = True
                if remote_email and remote_email.lower() != normalized_email:
                    needs_update = True

                if needs_update:
                    if rw_repo.update_key(
                        db_key.get('key_id'),
                        remnawave_user_uuid=remote_uuid or local_uuid,
                        email=remote_email if remote_email else None,
                        expire_at_ms=remote_ms,
                        subscription_url=subscription_url if subscription_url else None,
                        short_uuid=remote_user.get('shortUuid') or remote_user.get('short_uuid'),
                        traffic_limit_bytes=remote_user.get('trafficLimitBytes') or remote_user.get('traffic_limit_bytes'),
                        traffic_limit_strategy=remote_user.get('trafficLimitStrategy') or remote_user.get('traffic_limit_strategy'),
                    ):
                        total_affected_records += 1
                        logger.debug(
                            "Scheduler: Обновлён ключ '%s' на основе данных Remnawave (host '%s', match=%s).",
                            raw_email,
                            host_name,
                            matched_by or "unknown",
                        )
            else:
                logger.warning(
                    "Scheduler: Ключ '%s' (host '%s') не найден в текущем цикле sync — оставляю без удаления (safe mode).",
                    raw_email,
                    host_name,
                )
                # Safe mode: не удаляем ключ сразу, чтобы избежать потерь при рассинхроне email/UUID.
                continue

        if remote_by_email:
            for normalized_email, (remote_email, remote_user) in remote_by_email.items():
                import re

                # Пытаемся найти user_id разными способами
                user_id = remote_user.get('telegramId')
                
                if not user_id:
                    # Пробуем найти в note (часто там хранят)
                    note = str(remote_user.get('note') or "")
                    if note.isdigit():
                        user_id = int(note)
                
                if not user_id:
                    match = re.search(r"user(\d+)", remote_email)
                    user_id = int(match.group(1)) if match else None
                
                # Если все равно нет ID, можно попробовать найти пользователя по username из email (до @)
                if not user_id:
                     username_part = remote_email.split('@')[0]
                     # Здесь нужен метод репозитория для поиска по username, но его может не быть.
                     # Пока оставим как есть, но без user_id мы не можем привязать.
                     pass
                remote_uuid = (remote_user.get('uuid') or remote_user.get('id') or remote_user.get('client_uuid') or '').strip()
                
                # Ищем существующий ключ в базе по Email или UUID
                existing_key = None
                existing_by_email = rw_repo.get_key_by_email(remote_email)
                if existing_by_email:
                    existing_key = existing_by_email
                elif remote_uuid:
                    existing_by_uuid = rw_repo.get_key_by_remnawave_uuid(remote_uuid)
                    if existing_by_uuid:
                        existing_key = existing_by_uuid

                # Если user_id не пришел с панели, пробуем взять его из существующего ключа
                if user_id is None and existing_key:
                    user_id = existing_key.get('user_id')
                    logger.info(
                        "Scheduler: ID пользователя восстановлен из локальной БД (user_id=%s) для '%s'.",
                        user_id,
                        remote_email,
                    )

                # Ищем пользователя по username из email (до @)
                if user_id is None:
                    username_part = remote_email.split('@')[0]
                    
                    # 1. Сначала пробуем "как есть"  
                    candidates = [username_part]
                    
                    # 2. Если есть суффиксы -2, -3 и т.д., пробуем отрезать их
                     
                    import re
                    # Ищем паттерн: любое_имя-цифра(ы)
                    # Используем цикл, чтобы отрезать несколько раз, если вдруг что-то типа name-2-3 (редко, но бывает)
                    temp = username_part
                    while True:
                        match_suffix = re.search(r"^(.*?)(?:-\d+)$", temp)
                        if match_suffix:
                             base = match_suffix.group(1)
                             if base and base not in candidates:
                                 candidates.append(base)
                             temp = base
                        else:
                            break
                    
                    for candidate in candidates:
                        # Попытка найти пользователя по username в базе данных
                        user_by_username = rw_repo.get_user_by_username(candidate)
                        if user_by_username:
                            user_id = user_by_username.get('telegram_id')
                            logger.info(
                                "Scheduler: ID пользователя найден по username '%s' (из '%s') -> user_id=%s.",
                                candidate,
                                remote_email,
                                user_id,
                            )
                            break

                if user_id is None:
                    # Если это подарочный ключ (gift-uuid@bot.local)
                    if remote_email.startswith('gift-'):
                        token_prefix = remote_email.split('@')[0]
                        # Сначала ищем по полному префиксу (напр. gift-xxxx)
                        user_id = rw_repo.get_user_id_by_gift_token(token_prefix)
                        if user_id is None and '-' in token_prefix:
                            # Пробуем без префикса "gift-"
                            user_id = rw_repo.get_user_id_by_gift_token(token_prefix.split('-', 1)[1])
                
                # Если user_id всё ещё нет, но ключ есть в БД — это странно, но мы можем обновить его
                # Если ключа нет и user_id нет — это реально новый "осиротевший" пользователь
                if user_id is None:
                    if existing_key:
                         # Теоретически не должно сюда попасть, если мы взяли user_id из existing_key
                         # Но если в existing_key user_id=None (что невозможно по схеме), то ок.
                         pass
                    else:
                        if remote_email.startswith('gift-'):
                            logger.info(
                                "Scheduler: Подарочный ключ '%s' в Remnawave ожидает активации — оставляем в БД и панели без изменений.",
                                remote_email,
                            )
                        else:
                            logger.warning(
                                "Scheduler: Осиротевший пользователь '%s' в Remnawave не содержит user_id — пропускаю (ключ останется в панели, но не привяжется).",
                                remote_email,
                            )
                        continue

                # Автоматическая регистрация пользователя, если его нет в БД (и это не подарок без владельца)
                if user_id and user_id != 0 and not rw_repo.get_user(user_id):
                    logger.info(
                        "Scheduler: Автоматически регистрирую недостающего пользователя user_id=%s для '%s'.",
                        user_id,
                        remote_email,
                    )
                    rw_repo.register_user_if_not_exists(user_id, f"User_{user_id}", None)

                # Если ключ уже есть в базе (по Email или UUID) — ОБНОВЛЯЕМ его
                if existing_key:
                    old_email = existing_key.get('email') or existing_key.get('key_email')
                    key_id = existing_key.get('key_id')
                    old_user_id = existing_key.get('user_id')
                    
                    # Если user_id изменился (например, перепривязка), логгируем
                    if old_user_id != user_id:
                        logger.info(
                             "Scheduler: Обновление владельца ключа key_id=%s: %s -> %s",
                             key_id, old_user_id, user_id
                        )

                    expire_value = remote_user.get('expireAt') or remote_user.get('expiryDate')
                    expire_ms = None
                    if expire_value:
                        try:
                            expire_ms = int(datetime.fromisoformat(str(expire_value).replace('Z', '+00:00')).timestamp() * 1000)
                        except Exception:
                            pass
                    subscription_url = remnawave_api.extract_subscription_url(remote_user)
                    
                    rw_repo.update_key(
                        key_id,
                        user_id=user_id,
                        email=remote_email,
                        remnawave_user_uuid=remote_uuid,
                        expire_at_ms=expire_ms,
                        subscription_url=subscription_url,
                        short_uuid=remote_user.get('shortUuid') or remote_user.get('short_uuid'),
                        traffic_limit_bytes=remote_user.get('trafficLimitBytes') or remote_user.get('traffic_limit_bytes'),
                        traffic_limit_strategy=remote_user.get('trafficLimitStrategy') or remote_user.get('traffic_limit_strategy'),
                        host_name=host_name, # Принудительно обновляем хост, если ключ "переехал"
                        squad_uuid=squad_uuid,
                    )
                    total_affected_records += 1
                    continue

                # Если ключа нет — СОЗДАЕМ новый
                payload = dict(remote_user)
                payload.setdefault('host_name', host_name)
                payload.setdefault('squad_uuid', squad_uuid)
                payload.setdefault('squadUuid', squad_uuid)

                new_id = rw_repo.record_key_from_payload(
                    user_id=user_id,
                    payload=payload,
                    host_name=host_name,
                    description=payload.get('description'),
                    tag=payload.get('tag'),
                )
                if new_id:
                    total_affected_records += 1
                    logger.info(
                        "Scheduler: Привязал нового пользователя '%s' (host '%s') к user_id=%s как key_id=%s.",
                        remote_email,
                        host_name,
                        user_id,
                        new_id,
                    )
                else:
                    logger.warning(
                        "Scheduler: Не удалось привязать нового пользователя '%s' (host '%s').",
                        remote_email,
                        host_name,
                    )

    logger.debug(
        "Scheduler: Синхронизация с Remnawave API завершена. Затронуто записей: %s.",
        total_affected_records,
    )


async def _maybe_manage_support_tickets():
    """Тикеты: авто-закрытие (саппорт ответил, клиент молчит N часов) + ленты
    «🟢 Открытые» / «🔒 Закрытые» (топики). Best-effort, через отдельный support-бот."""
    from shop_bot.data_manager import database as _db
    forum_chat = (rw_repo.get_setting("support_forum_chat_id") or "").strip()
    token = (rw_repo.get_setting("support_bot_token") or "").strip()
    if not (forum_chat and token):
        return
    open_topic = (rw_repo.get_setting("support_open_topic_id") or "").strip()
    closed_topic = (rw_repo.get_setting("support_closed_topic_id") or "").strip()
    try:
        hours = int(rw_repo.get_setting("support_autoclose_hours") or 24)
    except (TypeError, ValueError):
        hours = 24

    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    sbot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    def _topic_link(thread_id):
        try:
            internal = str(forum_chat).replace("-100", "", 1)
            # 3-сегментный формат (.../<topic_id>/<msg_id>) надёжно открывает ТЕМУ форума, а не главную группы.
            return f"https://t.me/c/{internal}/{int(thread_id)}/{int(thread_id)}"
        except Exception:
            return None

    def _card(emoji, t):
        subj = (t.get("subject") or "без темы")[:60]
        link = _topic_link(t.get("message_thread_id"))
        txt = f"{emoji} <b>Тикет #{t['ticket_id']}</b> — {subj}"
        if link:
            txt += f"\n<a href=\"{link}\">Открыть переписку →</a>"
        return txt

    try:
        # 1) АВТО-ЗАКРЫТИЕ: саппорт ответил, клиент молчит >= N часов
        for t in (_db.get_open_tickets_for_autoclose(hours) or []):
            tid = t["ticket_id"]
            if not _db.set_ticket_status(tid, "closed"):
                continue
            logger.info("Scheduler: тикет #%s авто-закрыт (клиент молчит %sч)", tid, hours)
            try:
                if t.get("forum_chat_id") and t.get("message_thread_id"):
                    await sbot.close_forum_topic(chat_id=int(t["forum_chat_id"]), message_thread_id=int(t["message_thread_id"]))
            except Exception:
                pass
            try:
                if notif_enabled("ticket_autoclose_user"):
                    from shop_bot.bot import keyboards as _kb
                    await sbot.send_message(int(t["user_id"]),
                        get_notif_text("ticket_autoclose_user", ticket_id=tid, hours=hours),
                        reply_markup=_kb.build_notif_keyboard("ticket_autoclose_user"))
            except Exception:
                pass

        # 2) ЛЕНТА «🔒 Закрытые»: закрытые тикеты с карточкой → убрать из «Открытые», добавить в «Закрытые»
        if closed_topic:
            for t in (_db.get_tickets_closed_with_feed_card() or []):
                tid = t["ticket_id"]
                if open_topic and t.get("open_feed_msg_id"):
                    try:
                        await sbot.delete_message(chat_id=int(forum_chat), message_id=int(t["open_feed_msg_id"]))
                    except Exception:
                        pass
                try:
                    await sbot.send_message(chat_id=int(forum_chat), message_thread_id=int(closed_topic),
                                            text=_card("🔒", t), disable_web_page_preview=True)
                except Exception:
                    pass
                _db.set_ticket_open_feed_msg_id(tid, None)

        # 3) ЛЕНТА «🟢 Открытые»: открытые тикеты без карточки → добавить в «Открытые»
        if open_topic:
            for t in (_db.get_tickets_open_without_feed_card() or []):
                tid = t["ticket_id"]
                try:
                    sent = await sbot.send_message(chat_id=int(forum_chat), message_thread_id=int(open_topic),
                                                   text=_card("🟢", t), disable_web_page_preview=True)
                    _db.set_ticket_open_feed_msg_id(tid, sent.message_id)
                except Exception as e:
                    logger.warning("Scheduler: карточка тикета #%s не добавлена: %s", tid, e)
    finally:
        try:
            await sbot.session.close()
        except Exception:
            pass


async def periodic_subscription_check(bot_controller: BotController):
    logger.info("Scheduler: Планировщик фоновых задач запущен.")
    await asyncio.sleep(10)

    while True:
        try:
            await sync_keys_with_panels()


            await _maybe_run_periodic_speedtests()


            bot = bot_controller.get_bot_instance() if bot_controller.get_status().get("is_running") else None
            if bot:
                await _maybe_run_daily_backup(bot)


            bot = bot_controller.get_bot_instance() if bot_controller.get_status().get("is_running") else None
            await _maybe_collect_resource_metrics(bot)

            if bot_controller.get_status().get("is_running"):
                bot = bot_controller.get_bot_instance()
                if bot:
                    await check_expiring_subscriptions(bot)
                    try:
                        await run_reengagement(bot)
                    except Exception as _e_re:
                        logger.error(f"Scheduler: ошибка ре-энгейджмента: {_e_re}", exc_info=True)
                else:
                    logger.warning("Scheduler: Бот помечен как запущенный, но экземпляр недоступен.")
            else:
                logger.debug("Scheduler: Бот остановлен, уведомления пользователям пропущены.")

            try:
                await _maybe_manage_support_tickets()
            except Exception as _e_tickets:
                logger.error(f"Scheduler: ошибка управления тикетами: {_e_tickets}", exc_info=True)

        except Exception as e:
            logger.error(f"Scheduler: Необработанная ошибка в основном цикле: {e}", exc_info=True)
            
        logger.info(f"Scheduler: Цикл завершён. Следующая проверка через {CHECK_INTERVAL_SECONDS} сек.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def _maybe_run_periodic_speedtests():
    global _last_speedtests_run_at
    now = get_msk_time()
    
    if _last_speedtests_run_at is None:
        if (now - _scheduler_start_time).total_seconds() < 120:
            return
    elif (now - _last_speedtests_run_at).total_seconds() < SPEEDTEST_INTERVAL_SECONDS:
        return
    try:
        await _run_speedtests_for_all_ssh_targets()
        _last_speedtests_run_at = now
    except Exception as e:
        logger.error(f"Scheduler: Ошибка запуска speedtests: {e}", exc_info=True)

async def _run_speedtests_for_all_hosts():
    hosts = rw_repo.get_all_hosts()
    if not hosts:
        logger.debug("Scheduler: Нет хостов для измерений скорости.")
        return
    logger.info(f"Scheduler: Запускаю speedtest для {len(hosts)} хост(ов)...")
    for h in hosts:
        host_name = h.get('host_name')
        if not host_name:
            continue
        try:
            logger.info(f"Scheduler: Speedtest для '{host_name}' запущен...")

            try:
                async with asyncio.timeout(180):
                    res = await speedtest_runner.run_both_for_host(host_name)
            except AttributeError:

                res = await asyncio.wait_for(speedtest_runner.run_both_for_host(host_name), timeout=180)
            ok = res.get('ok')
            err = res.get('error')
            if ok:
                logger.info(f"Scheduler: Speedtest для '{host_name}' завершён успешно")
            else:
                logger.warning(f"Scheduler: Speedtest для '{host_name}' завершён с ошибками: {err}")
        except asyncio.TimeoutError:
            logger.warning(f"Scheduler: Таймаут speedtest для хоста '{host_name}'")
        except Exception as e:
            logger.error(f"Scheduler: Ошибка выполнения speedtest для '{host_name}': {e}", exc_info=True)

async def _run_speedtests_for_all_ssh_targets():
    targets = rw_repo.get_all_ssh_targets() or []
    if not targets:
        logger.debug("Scheduler: Нет SSH-целей для измерений скорости.")
        return
    logger.info(f"Scheduler: Запускаю SSH speedtest для {len(targets)} цел(ей)...")
    for t in targets:
        target_name = (t.get('target_name') or '').strip()
        if not target_name:
            continue
        try:
            logger.info(f"Scheduler: SSH speedtest для цели '{target_name}' запущен...")
            try:
                async with asyncio.timeout(180):
                    res = await speedtest_runner.run_and_store_ssh_speedtest_for_target(target_name)
            except AttributeError:
                res = await asyncio.wait_for(speedtest_runner.run_and_store_ssh_speedtest_for_target(target_name), timeout=180)
            ok = res.get('ok')
            err = res.get('error')
            if ok:
                logger.info(f"Scheduler: SSH speedtest для цели '{target_name}' завершён успешно")
            else:
                logger.warning(f"Scheduler: SSH speedtest для цели '{target_name}' завершён с ошибками: {err}")
        except asyncio.TimeoutError:
            logger.warning(f"Scheduler: Таймаут SSH speedtest для цели '{target_name}'")
        except Exception as e:
            logger.error(f"Scheduler: Ошибка выполнения SSH speedtest для цели '{target_name}': {e}", exc_info=True)



async def _maybe_collect_resource_metrics(bot: Bot | None):
    """Периодический сбор метрик (локально + SSH на хостах) и отправка алертов при превышении порогов.
    Читает настройки:
      - monitoring_enabled (true/false)
      - monitoring_interval_sec (по умолчанию 300)
      - monitoring_cpu_threshold, monitoring_mem_threshold, monitoring_disk_threshold (проценты)
      - monitoring_alert_cooldown_sec (по умолчанию 3600)
    """
    global _last_resource_collect_at, _last_resource_alert_at
    try:
        enabled = (rw_repo.get_setting("monitoring_enabled") or "true").strip().lower() == "true"
        if not enabled:
            return
        try:
            interval_sec = int((rw_repo.get_setting("monitoring_interval_sec") or "300").strip() or 300)
        except Exception:
            interval_sec = 300
        now = get_msk_time()
        if _last_resource_collect_at and (now - _last_resource_collect_at).total_seconds() < max(30, interval_sec):
            return


        def _to_int(s: str | None, default: int) -> int:
            try:
                return int((s or "").strip() or default)
            except Exception:
                return default
        cpu_thr = _to_int(rw_repo.get_setting("monitoring_cpu_threshold"), 90)
        mem_thr = _to_int(rw_repo.get_setting("monitoring_mem_threshold"), 90)
        disk_thr = _to_int(rw_repo.get_setting("monitoring_disk_threshold"), 90)
        cooldown = _to_int(rw_repo.get_setting("monitoring_alert_cooldown_sec"), 3600)


        try:
            local = await asyncio.to_thread(resource_monitor.get_local_metrics)
            cpu_p = (local.get('cpu') or {}).get('percent')
            mem_p = (local.get('memory') or {}).get('percent')
            disks = local.get('disks') or []
            disk_p = max((d.get('percent') or 0) for d in disks) if disks else None
            rw_repo.insert_resource_metric(
                'local', 'panel',
                cpu_percent=cpu_p, mem_percent=mem_p, disk_percent=disk_p,
                load1=(local.get('cpu') or {}).get('loadavg',[None])[0] if (local.get('cpu') or {}).get('loadavg') else None,
                net_bytes_sent=(local.get('net') or {}).get('bytes_sent'),
                net_bytes_recv=(local.get('net') or {}).get('bytes_recv'),
                raw_json=json.dumps(local, ensure_ascii=False)
            )
            await _maybe_alert(bot, scope='local', name='panel', cpu=cpu_p, mem=mem_p, disk=disk_p,
                               cpu_thr=cpu_thr, mem_thr=mem_thr, disk_thr=disk_thr, cooldown_sec=cooldown)
        except Exception:
            logger.debug("Scheduler: не удалось собрать локальные метрики", exc_info=True)


        hosts = rw_repo.get_all_hosts() or []
        for h in hosts:
            name = h.get('host_name') or ''
            if not name:
                continue

            if not (h.get('ssh_host') and h.get('ssh_user')):
                continue
            try:
                rm = await asyncio.to_thread(resource_monitor.get_remote_metrics_for_host, name)
                mem_p = (rm.get('memory') or {}).get('percent')
                disks = rm.get('disks') or []
                disk_p = max((d.get('percent') or 0) for d in disks) if disks else None
                rw_repo.insert_resource_metric(
                    'host', name,
                    mem_percent=mem_p,
                    disk_percent=disk_p,
                    load1=(rm.get('loadavg') or [None])[0],
                    raw_json=json.dumps(rm, ensure_ascii=False)
                )
                await _maybe_alert(bot, scope='host', name=name, cpu=None, mem=mem_p, disk=disk_p,
                                   cpu_thr=cpu_thr, mem_thr=mem_thr, disk_thr=disk_thr, cooldown_sec=cooldown)
            except Exception:
                logger.debug("Scheduler: не удалось собрать метрики хоста для %s", name, exc_info=True)

        _last_resource_collect_at = now
    except Exception:
        logger.error("Scheduler: Ошибка сбора метрик ресурсов", exc_info=True)


async def _maybe_run_daily_backup(bot: Bot):
    """Автобэкап базы и отправка админам.

    Интервал: backup_interval_hours (приоритет, в часах) либо backup_interval_days (в днях).
    Полностью выключить = backup_interval_hours=0 И backup_interval_days=0.
    """
    global _last_backup_run_at
    now = get_msk_time()
    interval_seconds = 0
    try:
        h = str(rw_repo.get_setting("backup_interval_hours") or "").strip()
        hours = int(h) if h else 0
    except Exception:
        hours = 0
    if hours > 0:
        interval_seconds = hours * 3600
    else:
        try:
            days = int(str(rw_repo.get_setting("backup_interval_days") or "1").strip() or "1")
        except Exception:
            days = 1
        if days <= 0:
            return
        interval_seconds = max(1, days) * 24 * 3600
    if interval_seconds <= 0:
        return
    if _last_backup_run_at and (now - _last_backup_run_at).total_seconds() < interval_seconds:
        return
    try:
        zip_path = backup_manager.create_backup_file()
        if zip_path and zip_path.exists():
            try:
                sent = await backup_manager.send_backup_to_admins(bot, zip_path)
                logger.info(f"Scheduler: Создан бэкап {zip_path.name}, отправлен {sent} адм.")
            except Exception as e:
                logger.error(f"Scheduler: Не удалось отправить бэкап: {e}")
            try:
                backup_manager.cleanup_old_backups(keep=48)
            except Exception:
                pass
        _last_backup_run_at = now
    except Exception as e:
        logger.error(f"Scheduler: Критическая ошибка при создании и отправке бэкапа: {e}", exc_info=True)


async def _maybe_alert(
    bot: Bot | None,
    *,
    scope: str,
    name: str,
    cpu: float | None,
    mem: float | None,
    disk: float | None,
    cpu_thr: int,
    mem_thr: int,
    disk_thr: int,
    cooldown_sec: int,
):
    if not bot:
        return
    

    cpu_warning = max(50, cpu_thr - 20)
    mem_warning = max(50, mem_thr - 20)
    disk_warning = max(50, disk_thr - 20)
    
    breaches: list[dict] = []
    alerts: list[dict] = []
    

    if cpu is not None:
        if cpu >= cpu_thr:
            breaches.append({
                'type': 'Процессор',
                'value': cpu,
                'threshold': cpu_thr,
                'level': 'critical',
                'emoji': '🔴'
            })
        elif cpu >= cpu_warning:
            alerts.append({
                'type': 'Процессор',
                'value': cpu,
                'threshold': cpu_warning,
                'level': 'warning',
                'emoji': '🟡'
            })
    

    if mem is not None:
        if mem >= mem_thr:
            breaches.append({
                'type': 'Память',
                'value': mem,
                'threshold': mem_thr,
                'level': 'critical',
                'emoji': '🔴'
            })
        elif mem >= mem_warning:
            alerts.append({
                'type': 'Память',
                'value': mem,
                'threshold': mem_warning,
                'level': 'warning',
                'emoji': '🟡'
            })
    

    if disk is not None:
        if disk >= disk_thr:
            breaches.append({
                'type': 'Диск',
                'value': disk,
                'threshold': disk_thr,
                'level': 'critical',
                'emoji': '🔴'
            })
        elif disk >= disk_warning:
            alerts.append({
                'type': 'Диск',
                'value': disk,
                'threshold': disk_warning,
                'level': 'warning',
                'emoji': '🟡'
            })
    

    if breaches:
        key = (scope, name, "critical", ",".join(sorted([b['type'] for b in breaches])))
        now = get_msk_time()
        last = _last_resource_alert_at.get(key)
        if not last or (now - last).total_seconds() >= max(60, cooldown_sec):
            _last_resource_alert_at[key] = now
            await _send_alert(bot, scope, name, breaches, 'critical')
    

    if alerts:
        key = (scope, name, "warning", ",".join(sorted([a['type'] for a in alerts])))
        now = get_msk_time()
        last = _last_resource_alert_at.get(key)
        if not last or (now - last).total_seconds() >= max(300, cooldown_sec * 2):
            _last_resource_alert_at[key] = now
            await _send_alert(bot, scope, name, alerts, 'warning')


async def _send_alert(bot: Bot, scope: str, name: str, issues: list[dict], level: str):
    """Отправка алерта админам"""
    try:
        admin_ids = rw_repo.get_admin_ids() or set()
    except Exception:
        admin_ids = set()
    if not admin_ids:
        return
    if not notif_enabled("resource_alert"):
        return
    

    if level == 'critical':
        header_emoji = "🚨"
        header_text = "КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ"
    else:
        header_emoji = "⚠️"
        header_text = "ПРЕДУПРЕЖДЕНИЕ"
    

    if scope == 'local':
        obj_name = f"🖥️ Панель ({name})"
    elif scope == 'host':
        obj_name = f"🖥️ Хост {name}"
    elif scope == 'target':
        obj_name = f"🔌 SSH-цель {name}"
    else:
        obj_name = f"❓ {scope}:{name}"
    

    issue_lines = []
    
    for issue in issues:
        issue_lines.append(f"  {issue['emoji']} <b>{issue['type']}:</b> {issue['value']:.1f}% (порог: {issue['threshold']}%)")
    

    # Шапка, время и рекомендации — в шаблоне реестра resource_alert (редактируется в админке)
    
    text = get_notif_text(
        "resource_alert",
        header_emoji=header_emoji,
        header_text=header_text,
        obj_name=obj_name,
        time=get_msk_time().strftime('%d.%m.%Y %H:%M:%S'),
        issues="\n".join(issue_lines),
    )
    

    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode='HTML')
        except Exception:
            continue
