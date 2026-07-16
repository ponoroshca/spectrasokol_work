"""Карточка клиента для операторов поддержки (форум / ЛС админа)."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import datetime
from typing import Any

from aiogram import Bot, types

from shop_bot.data_manager.database import (
    get_device_labels,
    get_msk_time,
    get_promo_usages_for_user,
    get_purchased_hwid_limit_hint,
    get_referral_count,
    get_referrals_for_user,
    get_setting,
    get_transactions_for_user,
    get_user,
    get_user_keys,
)
from shop_bot.modules import remnawave_api

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LEN = 3900
_CAPTION_LEN = 900


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else "—"


def _fmt_dt(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, (int, float)):
        try:
            if value > 1e12:
                value = value / 1000.0
            dt = datetime.fromtimestamp(float(value))
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return str(value)
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26], fmt).strftime("%d.%m.%Y %H:%M")
        except Exception:
            continue
    return s[:16] if len(s) > 16 else s


def _parse_expiry(key: dict) -> datetime | None:
    raw = key.get("expiry_date") or key.get("expire_at")
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts)
        except Exception:
            return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:26], fmt)
        except Exception:
            continue
    return None


def _tg_profile_lines(user_id: int, tg_user: types.User | types.Chat | None) -> list[str]:
    lines = [f"🆔 <code>{user_id}</code>"]
    if not tg_user:
        return lines
    first = getattr(tg_user, "first_name", None) or ""
    last = getattr(tg_user, "last_name", None) or ""
    full = f"{first} {last}".strip()
    uname = getattr(tg_user, "username", None)
    if full:
        lines.append(f"👤 {_esc(full)}")
    if uname:
        uname = uname.lstrip("@")
        lines.append(f"🔗 <a href='https://t.me/{_esc(uname)}'>@{_esc(uname)}</a>")
    elif full:
        lines.append(f"🔗 <a href='tg://user?id={user_id}'>открыть профиль</a>")
    return lines


def _account_lines(user: dict, user_id: int) -> list[str]:
    banned = bool(user.get("is_banned"))
    trial = bool(user.get("trial_used"))
    agreed = bool(user.get("agreed_to_terms"))
    ref_by = user.get("referred_by")
    lines = [
        f"📅 Регистрация: {_fmt_dt(user.get('registration_date'))}",
        f"{'🚫 Забанен' if banned else '✅ Активен'}",
        f"💰 Баланс: {float(user.get('balance') or 0):.2f} ₽",
        f"💳 Потрачено: {float(user.get('total_spent') or 0):.2f} ₽ · месяцев: {int(user.get('total_months') or 0)}",
        f"🎁 Trial: {'использован' if trial else 'не использован'}",
        f"📜 Оферта: {'да' if agreed else 'нет'}",
    ]
    if user.get("auth_email"):
        lines.append(f"✉️ Email (кабинет): {_esc(user.get('auth_email'))}")
    lines.append(f"🤝 Приглашён: <code>{ref_by}</code>" if ref_by else "🤝 Приглашён: —")
    ref_cnt = get_referral_count(user_id)
    ref_bal = float(user.get("referral_balance") or 0)
    ref_all = float(user.get("referral_balance_all") or 0)
    lines.append(f"👥 Рефералов: {ref_cnt} · баланс {ref_bal:.2f} ₽ (всего {ref_all:.2f} ₽)")
    if ref_cnt:
        refs = get_referrals_for_user(user_id)[:5]
        for r in refs:
            rid = r.get("telegram_id")
            run = (r.get("username") or "").lstrip("@")
            tag = f"@{run}" if run else f"id{rid}"
            lines.append(f"   └ {tag} · {_fmt_dt(r.get('registration_date'))} · {float(r.get('total_spent') or 0):.0f} ₽")
        if ref_cnt > 5:
            lines.append(f"   └ … ещё {ref_cnt - 5}")
    return lines


def _promo_lines(user_id: int) -> list[str]:
    usages = get_promo_usages_for_user(user_id, limit=15)
    if not usages:
        return ["— не использовал"]
    lines = []
    for u in usages:
        code = _esc(u.get("code") or "?")
        amt = float(u.get("applied_amount") or 0)
        when = _fmt_dt(u.get("used_at"))
        lines.append(f"• <code>{code}</code> · {amt:.0f} ₽ · {when}")
    return lines


def _payment_lines(user_id: int) -> list[str]:
    txs = get_transactions_for_user(user_id, limit=8)
    if not txs:
        return ["— нет оплат"]
    lines = []
    for t in txs:
        status = (t.get("status") or "").lower()
        if status not in ("paid", "completed", "success", "succeeded"):
            continue
        amt = float(t.get("amount_rub") or 0)
        method = _esc(t.get("payment_method") or "—")
        when = _fmt_dt(t.get("created_date"))
        meta_hint = ""
        raw = t.get("metadata")
        if raw:
            try:
                meta = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(meta, dict):
                    host = meta.get("host_name") or meta.get("host")
                    months = meta.get("months") or meta.get("duration_months")
                    if host or months:
                        meta_hint = f" · {_esc(host or '')}"
                        if months:
                            meta_hint += f" {months}мес"
            except Exception:
                pass
        lines.append(f"• {when} · {amt:.0f} ₽ · {method}{meta_hint}")
    return lines or ["— нет успешных оплат"]


def _format_bytes(num: Any) -> str:
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        n = 0
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    u = 0
    while n >= 1024 and u < len(units) - 1:
        n /= 1024
        u += 1
    if u == 0:
        return f"{int(n)} {units[u]}"
    return f"{n:.2f} {units[u]}"


def _remna_service_id(remna_user: dict | None, key: dict | None = None) -> str | None:
    """Числовой ID услуги в Remnawave (как 11806 в SpectraSokol_11806)."""
    if remna_user:
        for field in ("id", "userId", "userNumber"):
            val = remna_user.get(field)
            if val is None:
                continue
            s = str(val).strip()
            if s and s != "0":
                return s
        username = str(remna_user.get("username") or "")
        if "_" in username:
            tail = username.rsplit("_", 1)[-1]
            if tail.isdigit():
                return tail
    if key:
        short = key.get("short_uuid")
        if short:
            return str(short).strip()
    return None


def _brand_vpn_slug() -> str:
    brand = (get_setting("panel_brand_title") or "SpectraSokol").strip()
    slug = re.sub(r"[^a-zA-Z0-9]", "", brand)
    return slug or "VPN"


def _format_remna_service_label(remna_user: dict | None, service_id: str | None) -> str | None:
    """Имя услуги как в WebApp (SpectraSokol_11806), не Telegram-ник."""
    u = remna_user or {}
    rw = str(u.get("username") or "").strip()
    sid = service_id or _remna_service_id(remna_user, None)
    if rw and (re.search(r"vpn", rw, re.I) or re.search(r"_\d+$", rw)):
        return rw
    if sid:
        return f"{_brand_vpn_slug()}VPN_{sid}"
    if rw and re.search(r"\d", rw):
        return rw
    return None


def _hwid_limit_display(remna: dict | None, key: dict, user_id: int) -> str:
    remna = remna or {}
    for src in (
        remna.get("hwid_limit"),
        key.get("hwid_limit"),
        get_purchased_hwid_limit_hint(user_id, key.get("host_name")),
    ):
        if src is None:
            continue
        try:
            val = int(src)
        except (TypeError, ValueError):
            continue
        if val <= 0 or val >= 99:
            return "  📱 Устройств в тарифе: ∞"
        return f"  📱 Устройств в тарифе: <b>{val}</b>"
    return ""


def _remna_key_meta_lines(k: dict, remna: dict | None, user_id: int) -> list[str]:
    """Строки Remna: имя услуги, ID, UUID."""
    remna = remna or {}
    remna_user = remna.get("user") if isinstance(remna.get("user"), dict) else {}
    service_id = remna.get("service_id") or _remna_service_id(remna_user, k)
    service_label = _format_remna_service_label(remna_user, service_id)
    uuid_val = remna.get("uuid") or k.get("remnawave_user_uuid") or remna_user.get("uuid")
    short_uuid = remna.get("short_uuid") or k.get("short_uuid") or remna_user.get("shortUuid")

    meta: list[str] = []
    if service_label:
        meta.append(f"  🏷 Услуга: <code>{_esc(service_label)}</code>")
    if service_id:
        meta.append(f"  🔢 ID Remna: <code>{_esc(service_id)}</code>")
    meta.append(_hwid_limit_display(remna, k, user_id) or "  📱 Устройств в тарифе: —")
    if short_uuid and str(short_uuid) != str(service_id):
        meta.append(f"  shortUuid: <code>{_esc(short_uuid)}</code>")
    if uuid_val:
        u = str(uuid_val)
        shown = u if len(u) <= 36 else f"{u[:8]}…{u[-4:]}"
        meta.append(f"  UUID: <code>{_esc(shown)}</code>")
    if not meta:
        meta.append("  Remna: — (нет привязки / API)")
    return meta


async def _load_remna_context(keys: list[dict], limit: int = 8) -> dict[int, dict]:
    """Данные Remnawave по ключам: user, ID услуги, устройства."""
    ctx: dict[int, dict] = {}
    batch = [k for k in keys[:limit] if k.get("key_id") is not None]
    if not batch:
        return ctx

    detail_results = await asyncio.gather(
        *[remnawave_api.get_key_details_from_host(k) for k in batch],
        return_exceptions=True,
    )

    dev_jobs: list[tuple[int, dict, str]] = []
    for k, res in zip(batch, detail_results):
        kid = int(k["key_id"])
        entry: dict[str, Any] = {}
        remna_user: dict = {}
        if isinstance(res, dict) and res.get("user"):
            remna_user = res["user"]
            entry["user"] = remna_user
            entry["username"] = remna_user.get("username") or ""
            entry["uuid"] = remna_user.get("uuid") or k.get("remnawave_user_uuid")
            entry["short_uuid"] = (
                k.get("short_uuid") or remna_user.get("shortUuid") or remna_user.get("short_uuid")
            )
            entry["service_id"] = _remna_service_id(remna_user, k)
            if remna_user.get("hwidDeviceLimit") is not None:
                entry["hwid_limit"] = remna_user.get("hwidDeviceLimit")
            for field in ("trafficUsed", "traffic", "used_traffic"):
                if remna_user.get(field) is not None:
                    entry["traffic_used"] = remna_user.get(field)
                    break
            if remna_user.get("trafficLimitBytes") is not None:
                entry["traffic_limit"] = remna_user.get("trafficLimitBytes")
        else:
            entry["uuid"] = k.get("remnawave_user_uuid")
            entry["short_uuid"] = k.get("short_uuid")
            entry["service_id"] = _remna_service_id(None, k)

        ctx[kid] = entry
        uuid_val = entry.get("uuid")
        if uuid_val:
            dev_jobs.append((kid, k, str(uuid_val)))

    if dev_jobs:
        dev_results = await asyncio.gather(
            *[
                remnawave_api.get_connected_devices_count(uuid_val, host_name=k.get("host_name"))
                for _, k, uuid_val in dev_jobs
            ],
            return_exceptions=True,
        )
        for (kid, _k, _uuid), dr in zip(dev_jobs, dev_results):
            if isinstance(dr, dict):
                ctx[kid]["devices"] = dr

    return ctx


def _keys_lines(
    keys: list[dict],
    now: datetime,
    user_id: int,
    remna_ctx: dict[int, dict] | None = None,
) -> list[str]:
    if not keys:
        return ["— нет ключей"]
    remna_ctx = remna_ctx or {}
    lines = []
    for k in keys[:8]:
        kid = k.get("key_id")
        host = _esc(k.get("host_name") or "—")
        exp = _parse_expiry(k)
        if exp and exp > now:
            st = "🟢 активен"
        elif exp:
            st = "🔴 истёк"
        else:
            st = "⚪ без срока"
        exp_s = exp.strftime("%d.%m.%Y") if exp else "—"
        email = k.get("key_email") or k.get("email") or ""
        trial = " · trial" if str(email).startswith("trial_") else ""
        created = _fmt_dt(k.get("created_at"))
        remna = remna_ctx.get(int(kid)) if kid is not None else None
        limit_ips = (remna or {}).get("hwid_limit") or k.get("hwid_limit") or k.get("limit_ips")
        lim_s = ""
        if limit_ips is not None:
            try:
                lv = int(limit_ips)
                if 0 < lv < 99:
                    lim_s = f" · {lv} устр."
            except (TypeError, ValueError):
                pass
        nm = (k.get("comment_key") or "").strip()
        nm_s = f" «{_esc(nm)}»" if nm else ""
        lines.append(f"• <b>#{kid}</b>{nm_s} {host}{trial} · до {exp_s} · {st}{lim_s}")
        lines.append(f"  куплен/создан: {created}")
        lines.extend(_remna_key_meta_lines(k, remna, user_id))
        if remna and remna.get("traffic_limit") is not None:
            used = _format_bytes(remna.get("traffic_used") or 0)
            limit = _format_bytes(remna.get("traffic_limit"))
            lines.append(f"  📊 Трафик: {used} / {limit}")
    if len(keys) > 8:
        lines.append(f"… ещё {len(keys) - 8} ключ(ей)")
    return lines


def _devices_lines(keys: list[dict], remna_ctx: dict[int, dict] | None = None) -> list[str]:
    remna_ctx = remna_ctx or {}
    lines: list[str] = []
    total_connected = 0
    checked = 0
    for k in keys:
        if checked >= 6:
            break
        kid = k.get("key_id")
        remna = remna_ctx.get(int(kid)) if kid is not None else None
        uuid_val = (remna or {}).get("uuid") or k.get("remnawave_user_uuid")
        if not uuid_val:
            continue
        checked += 1
        host = k.get("host_name")
        data = (remna or {}).get("devices")
        if not isinstance(data, dict):
            continue
        devices = data.get("devices") or []
        total = int(data.get("total") or len(devices))
        total_connected += total
        limit = data.get("limit") or (remna or {}).get("hwid_limit") or k.get("hwid_limit") or k.get("limit_ips")
        lim_txt = f"{total}/{limit}" if limit else str(total)
        sid = (remna or {}).get("service_id")
        sid_s = f" · Remna <code>{_esc(sid)}</code>" if sid else ""
        lines.append(f"🔑 #{kid} ({_esc(host or '—')}){sid_s}: <b>{lim_txt}</b> подключено")
        labels = get_device_labels(str(uuid_val))
        for d in devices[:6]:
            if not isinstance(d, dict):
                continue
            hwid = (d.get("hwid") or d.get("id") or "")[:12]
            platform = d.get("platform") or d.get("deviceModel") or d.get("model") or ""
            label = labels.get(d.get("hwid") or "") or d.get("label") or ""
            name = label or platform or hwid or "устройство"
            lines.append(f"   · {_esc(name)}")
        if len(devices) > 6:
            lines.append(f"   · … +{len(devices) - 6}")
    if not lines:
        return ["— нет данных с сервера (нет UUID / API недоступен)"]
    lines.insert(0, f"📱 Всего подключений (по ключам): <b>{total_connected}</b>")
    return lines


async def build_support_user_card_html(
    user_id: int,
    *,
    tg_user: types.User | types.Chat | None = None,
    ticket_id: int | None = None,
) -> str:
    user = get_user(user_id) or {}
    now = get_msk_time().replace(tzinfo=None)
    keys = get_user_keys(user_id) or []
    keys.sort(
        key=lambda k: _parse_expiry(k) or datetime.max,
    )
    remna_ctx = await _load_remna_context(keys)

    header = "👤 <b>Карточка клиента</b>"
    if ticket_id:
        header += f" · тикет #{ticket_id}"

    sections = [
        header,
        "",
        "<b>Telegram</b>",
        *_tg_profile_lines(user_id, tg_user),
        "",
        "<b>Аккаунт</b>",
        *_account_lines(user, user_id),
        "",
        "<b>Промокоды</b>",
        *_promo_lines(user_id),
        "",
        "<b>Последние оплаты</b>",
        *_payment_lines(user_id),
        "",
        "<b>Ключи / подписки (Remnawave)</b>",
        *_keys_lines(keys, now, user_id, remna_ctx),
        "",
        "<b>Устройства</b>",
    ]
    sections.extend(_devices_lines(keys, remna_ctx))

    text = "\n".join(sections)
    if len(text) > _MAX_MESSAGE_LEN:
        text = text[: _MAX_MESSAGE_LEN - 20] + "\n\n… (обрезано)"
    return text


def build_support_user_card_caption(user_id: int, tg_user: types.User | types.Chat | None = None) -> str:
    """Короткая подпись к фото профиля."""
    user = get_user(user_id) or {}
    name = ""
    if tg_user:
        first = getattr(tg_user, "first_name", None) or ""
        last = getattr(tg_user, "last_name", None) or ""
        name = f"{first} {last}".strip()
    uname = ""
    if tg_user and getattr(tg_user, "username", None):
        uname = f"@{tg_user.username.lstrip('@')}"
    elif user.get("username"):
        uname = f"@{str(user.get('username')).lstrip('@')}"
    label = name or uname or f"ID {user_id}"
    spent = float(user.get("total_spent") or 0)
    keys_list = get_user_keys(user_id) or []
    keys_n = len(keys_list)
    cap = f"👤 <b>{_esc(label)}</b> · <code>{user_id}</code>\n💳 {spent:.0f} ₽ · 🔑 {keys_n} ключ."
    if uname and name:
        cap += f"\n{uname}"
    # ID активной подписки Remna в подписи к фото (если есть)
    now = get_msk_time().replace(tzinfo=None)
    for k in sorted(keys_list, key=lambda x: _parse_expiry(x) or datetime.max):
        exp = _parse_expiry(k)
        if exp and exp <= now:
            continue
        email = k.get("key_email") or k.get("email") or ""
        un = str(email).split("@")[0] if email else ""
        if "_" in un and un.rsplit("_", 1)[-1].isdigit():
            cap += f"\n🏷 Remna ID: <code>{_esc(un.rsplit('_', 1)[-1])}</code>"
            break
        if k.get("short_uuid"):
            cap += f"\n🏷 Remna ID: <code>{_esc(k.get('short_uuid'))}</code>"
            break
    return cap[:_CAPTION_LEN]


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    chunk = ""
    for line in text.split("\n"):
        candidate = chunk + ("\n" if chunk else "") + line
        if len(candidate) > limit and chunk:
            parts.append(chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        parts.append(chunk)
    return parts or [text[:limit]]


async def send_support_user_card(
    bot: Bot,
    chat_id: int,
    user_id: int,
    *,
    message_thread_id: int | None = None,
    ticket_id: int | None = None,
    tg_user: types.User | types.Chat | None = None,
    pin: bool = False,
    reply_markup: types.InlineKeyboardMarkup | None = None,
) -> types.Message | None:
    """Отправляет карточку (фото профиля + текст). Возвращает первое сообщение."""
    card_html = await build_support_user_card_html(user_id, tg_user=tg_user, ticket_id=ticket_id)
    caption = build_support_user_card_caption(user_id, tg_user=tg_user)
    kwargs: dict[str, Any] = {}
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id

    first_msg: types.Message | None = None
    photo_sent = False
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count and photos.photos:
            file_id = photos.photos[0][-1].file_id
            first_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup if len(card_html) <= _CAPTION_LEN + 200 else None,
                **kwargs,
            )
            photo_sent = True
    except Exception as e:
        logger.debug("profile photo for %s: %s", user_id, e)

    parts = _split_text(card_html, _MAX_MESSAGE_LEN)
    for i, part in enumerate(parts):
        rm = reply_markup if (i == len(parts) - 1 and not photo_sent) else None
        msg = await bot.send_message(
            chat_id=chat_id,
            text=part,
            parse_mode="HTML",
            reply_markup=rm,
            **kwargs,
        )
        if first_msg is None:
            first_msg = msg

    if pin and first_msg:
        try:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=first_msg.message_id,
                message_thread_id=message_thread_id,
                disable_notification=True,
            )
        except Exception as e:
            logger.debug("pin user card: %s", e)

    return first_msg
