"""Реестр уведомлений бота + хелперы.

Цель: дать админке управлять каждым оповещением (текст / вкл-выкл / тайминг /
тест-отправка) без правки кода. Код бота читает через get_notif_text /
notif_enabled / notif_hours; если настройка в БД пустая — берётся дефолт ниже,
поэтому поведение не меняется, пока не отредактируешь во вкладке «Уведомления».

Чтобы добавить новое управляемое уведомление: одна запись в NOTIFICATIONS +
заменить хардкод-текст в коде на get_notif_text("<key>", ...). Вкладка, сохранение
и тест-отправка строятся из реестра автоматически.
"""
from __future__ import annotations

import string

from shop_bot.data_manager.database import get_setting

# Каждая запись:
#   key          — уникальный ключ (настройки: notif_<key>_text/_enabled/_hours)
#   cat          — категория (группировка во вкладке)
#   name         — название для админки
#   desc         — пояснение (когда шлётся, кому)
#   target       — user | admin | group
#   parse_mode   — Markdown | HTML (как отправляется ботом)
#   timing       — None, либо строка дефолтных часов "72,48,24,1" (тайминг редактируемый)
#   placeholders — [(имя, описание)] — доступные подстановки в тексте
#   default      — дефолтный шаблон (с {placeholders})
NOTIFICATIONS: list[dict] = [
    {
        "key": "trial_expiry",
        "cat": "Напоминания (планировщик)",
        "name": "🎁 Конец пробного периода",
        "desc": "Пользователю с триалом — за N часов до окончания (планировщик, каждые 5 мин).",
        "target": "user",
        "parse_mode": "Markdown",
        "timing": "72,48,24,1",
        "placeholders": [("time_text", "сколько осталось, напр. «2 дня»"), ("expiry", "дата и время окончания")],
        "default": (
            "🎁 **Ваш пробный период заканчивается!**\n\n"
            "Бесплатный доступ истекает через **{time_text}** ({expiry}).\n\n"
            "Понравился сервис? Оформите подписку, чтобы не потерять доступ к VPN."
        ),
        "buttons": [
            {"id": "buy", "label": "💳 Купить подписку", "screen": "buy"},
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
        ],
    },
    {
        "key": "sub_expiry",
        "cat": "Напоминания (планировщик)",
        "name": "⚠️ Конец подписки",
        "desc": "Пользователю с платной подпиской — за N часов до окончания (планировщик).",
        "target": "user",
        "parse_mode": "Markdown",
        "timing": "72,48,24,1",
        "placeholders": [("time_text", "сколько осталось"), ("expiry", "дата окончания")],
        "default": (
            "⚠️ **Внимание!** ⚠️\n\n"
            "Срок действия вашей подписки истекает через **{time_text}**.\n"
            "Дата окончания: **{expiry}**\n\n"
            "Продлите подписку, чтобы не остаться без доступа к VPN!"
        ),
        "buttons": [
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
            {"id": "extend", "label": "➕ Продлить ключ", "screen": "renew"},
        ],
    },
    {
        "key": "purchase_success",
        "cat": "Покупка и баланс",
        "name": "🎉 Ключ готов / продлён",
        "desc": "Пользователю после успешной покупки, продления или активации триала — выдача ключа и ссылки. Кнопки под сообщением (Подключиться/QR/Продлить) остаются в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("key_number", "номер ключа у пользователя"),
            ("action_text", "«готов» (новый) или «продлен»"),
            ("expiry", "действует до — дата и время"),
            ("email", "ID ключа (email/логин)"),
            ("connection", "строка подключения (vless://…)"),
        ],
        "default": (
            "🎉 <b>Ваш ключ #{key_number} {action_text}!</b>\n\n"
            "📅 <b>Сроки действия:</b>\n"
            "⏳ <b>Действует до: {expiry}</b>\n"
            "💌 <b>ID ключа:</b> <code>{email}</code>\n\n"
            "🗽 <b>Ваш ключ:</b>\n"
            "<code>{connection}</code>"
        ),
        "buttons": [
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
            {"id": "setup", "label": "📖 Инструкция", "screen": "setup"},
        ],
    },
    {
        "key": "balance_topup",
        "cat": "Покупка и баланс",
        "name": "✅ Баланс пополнен",
        "desc": "Пользователю после успешного пополнения баланса. Кнопка «Профиль» остаётся в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("amount", "сумма пополнения (RUB)"),
            ("balance", "текущий баланс (RUB)"),
        ],
        "default": (
            "✅ <b>Баланс пополнен!</b>\n"
            "Сумма: <code>{amount} RUB</code>\n"
            "Текущий баланс: <code>{balance} RUB</code>"
        ),
        "buttons": [
            {"id": "buy", "label": "💳 Купить подписку", "screen": "buy"},
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
        ],
    },
    {
        "key": "admin_topup_alert",
        "cat": "Алерты администратору",
        "name": "📥 Админу: пополнение баланса",
        "desc": "Администратору при пополнении баланса пользователем.",
        "target": "admin",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("user_id", "Telegram ID пользователя"),
            ("username", "username (@… или N/A)"),
            ("method", "способ оплаты"),
            ("amount", "сумма (RUB)"),
        ],
        "default": (
            "📥 <b>Пополнение баланса</b>\n"
            "👤 Пользователь: <code>{user_id}</code>\n"
            "💌 Username: {username}\n"
            "💳 Метод: {method}\n"
            "💰 Сумма: {amount} RUB\n"
            "⚙️ Тип: ➕ Баланс ‼️"
        ),
        "buttons": [],
    },
    {
        "key": "admin_sale_alert",
        "cat": "Алерты администратору",
        "name": "📥 Админу: новая продажа",
        "desc": "Администратору при новой покупке или продлении ключа. Блок промокода (если применён) и итоги кассы добавляются в коде.",
        "target": "admin",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("user_id", "Telegram ID пользователя"),
            ("username", "username (@… или N/A)"),
            ("host", "локация/хост"),
            ("plan", "название тарифа"),
            ("months", "срок, мес."),
            ("method", "способ оплаты"),
            ("amount", "сумма (RUB)"),
            ("key_action", "«Новый ключ ➕» или «Продление ♻️»"),
            ("rub_today", "касса за сегодня в ₽"),
            ("crypto_today", "касса за сегодня в $ (в RUB)"),
        ],
        "default": (
            "📥 <b>Новая оплата</b>\n"
            "👤 Пользователь: <code>{user_id}</code>\n"
            "💌 Username: {username}\n"
            "🌍 Локация: <b>{host}</b>\n"
            "📦 Тариф: {plan} ({months} мес.)\n"
            "💳 Метод: {method}\n"
            "💰 Сумма: {amount} RUB\n"
            "⚙️ Тип: {key_action}\n\n"
            "<blockquote>💵 Касса за сегодня ₽: {rub_today} RUB\n"
            "💎 Касса за сегодня $: {crypto_today} RUB</blockquote>"
        ),
        "buttons": [],
    },
    {
        "key": "promo_balance",
        "cat": "Промокоды",
        "name": "🎟 Промокод: начислен баланс",
        "desc": "Клиенту при активации промокода на баланс (deep-link и в профиле — единый текст на все точки). Кнопка остаётся в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("amount", "начислено, ₽"),
        ],
        "default": (
            "✅ <b>Промокод активирован!</b>\n"
            "Вам начислено {amount} ₽"
        ),
        "buttons": [
            {"id": "buy", "label": "💳 Купить подписку", "screen": "buy"},
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
        ],
    },
    {
        "key": "promo_days",
        "cat": "Промокоды",
        "name": "🎟 Промокод: добавлены дни",
        "desc": "Клиенту при активации промокода на дни (продление подписки). Кнопка остаётся в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("days", "добавлено дней"),
        ],
        "default": (
            "✅ <b>Промокод успешно активирован!</b>\n"
            "🎉 Добавлено дней: {days}\n"
            "Ваша подписка продлена."
        ),
        "buttons": [
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
        ],
    },
    {
        "key": "admin_promo_alert",
        "cat": "Алерты администратору",
        "name": "🎟 Админу: применён промокод",
        "desc": "Администратору при активации бонусного промокода (баланс или дни). Промокод-скидка при покупке сюда не входит — она в «Новая продажа».",
        "target": "admin",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("user_id", "Telegram ID пользователя"),
            ("username", "username (@… или N/A)"),
            ("code", "промокод"),
            ("reward_text", "награда: «100 ₽» или «30 дн.»"),
        ],
        "default": (
            "🎟 <b>Применён промокод</b>\n"
            "👤 Пользователь: <code>{user_id}</code>\n"
            "💌 Username: {username}\n"
            "🔑 Код: <code>{code}</code>\n"
            "🎁 Награда: {reward_text}"
        ),
        "buttons": [],
    },
    {
        "key": "referral_signup",
        "cat": "Рефералка",
        "name": "🎉 Новый реферал",
        "desc": "Рефереру, когда по его ссылке зарегистрировался новый пользователь.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("new_user", "имя/username нового реферала"),
            ("user_id", "Telegram ID нового реферала"),
        ],
        "default": (
            "🎉 <b>У вас новый реферал!</b>\n"
            "📃 user: {new_user} / id: <code>{user_id}</code>\n\n"
        ),
        "buttons": [
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
        ],
    },
    {
        "key": "referral_bonus_purchase",
        "cat": "Рефералка",
        "name": "💰 Реф-бонус: покупка реферала",
        "desc": "Рефереру при начислении бонуса за покупку его реферала.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("amount", "начисленный бонус (RUB)"),
        ],
        "default": (
            "💰 <b>Реферальный бонус!</b>\n"
            "Ваш реферал совершил покупку.\n"
            "Вам начислено: <code>{amount} RUB</code>"
        ),
        "buttons": [
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
        ],
    },
    {
        "key": "referral_bonus_topup",
        "cat": "Рефералка",
        "name": "💰 Реф-бонус: пополнение реферала",
        "desc": "Рефереру при начислении бонуса за пополнение баланса его рефералом.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("username", "username реферала, который пополнил"),
            ("amount", "начисленный бонус (RUB)"),
        ],
        "default": (
            "💰 <b>Реферальный бонус!</b>\n"
            "Пользователь {username} пополнил баланс.\n"
            "Вам начислено: <code>{amount} RUB</code>"
        ),
        "buttons": [
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
        ],
    },
    {
        "key": "user_banned",
        "cat": "Действия из админки",
        "name": "🚫 Бан пользователя",
        "desc": "Клиенту при блокировке аккаунта администратором. Кнопка поддержки остаётся в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [],
        "default": "🚫 Ваш аккаунт заблокирован администратором. Если это ошибка — напишите в поддержку.",
        "buttons": [
            {"id": "support", "label": "🆘 Написать в поддержку", "url": "https://t.me/SpectraSokol_Support_bot"},
        ],
    },
    {
        "key": "user_unbanned",
        "cat": "Действия из админки",
        "name": "✅ Разбан пользователя",
        "desc": "Клиенту при разблокировке аккаунта администратором. Кнопка меню остаётся в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [],
        "default": "✅ Доступ к аккаунту восстановлен администратором.",
        "buttons": [
            {"id": "open", "label": "📲 Открыть приложение", "screen": "home"},
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
        ],
    },
    {
        "key": "balance_added_admin",
        "cat": "Действия из админки",
        "name": "💰 Начисление баланса (админ)",
        "desc": "Клиенту при ручном начислении баланса администратором из админки.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [("amount", "сумма начисления (RUB)")],
        "default": "💰 Вам начислено {amount} RUB на баланс администратором.",
        "buttons": [
            {"id": "buy", "label": "💳 Купить подписку", "screen": "buy"},
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
        ],
    },
    {
        "key": "balance_deducted_admin",
        "cat": "Действия из админки",
        "name": "➖ Списание баланса (админ)",
        "desc": "Клиенту при ручном списании баланса администратором. Кнопка поддержки остаётся в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [("amount", "сумма списания (RUB)")],
        "default": "➖ С вашего баланса списано {amount} RUB администратором.\nЕсли это ошибка — напишите в поддержку.",
        "buttons": [
            {"id": "profile", "label": "👤 Профиль", "screen": "profile"},
            {"id": "support", "label": "🆘 Поддержка", "url": "https://t.me/SpectraSokol_Support_bot"},
        ],
    },
    {
        "key": "admin_extend_key_user",
        "cat": "Действия из админки",
        "name": "ℹ️ Продление ключа (админ)",
        "desc": "Клиенту, когда администратор вручную продлил его ключ из админки.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [("key_id", "номер ключа"), ("days", "на сколько дней продлён")],
        "default": "ℹ️ Администратор продлил ваш ключ #{key_id} на {days} дн.",
        "buttons": [
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
            {"id": "setup", "label": "📖 Инструкция", "screen": "setup"},
        ],
    },
    {
        "key": "resource_alert",
        "cat": "Мониторинг",
        "name": "🚨 Мониторинг ресурсов",
        "desc": "Администратору при превышении порогов CPU/RAM/диска (планировщик). Список «Проблемы» ({issues}) собирается в коде; редактируется шапка, время и рекомендации.",
        "target": "admin",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("header_emoji", "🚨 (critical) или ⚠️ (warning)"),
            ("header_text", "КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ / ПРЕДУПРЕЖДЕНИЕ"),
            ("obj_name", "объект: панель / хост / SSH-цель"),
            ("time", "дата и время"),
            ("issues", "список проблем (формируется в коде)"),
        ],
        "default": (
            "{header_emoji} <b>{header_text}</b>\n"
            "\n"
            "🎯 <b>Объект:</b> {obj_name}\n"
            "⏰ <b>Время:</b> <code>{time}</code>\n"
            "\n"
            "📊 <b>Проблемы:</b>\n"
            "{issues}\n"
            "\n"
            "💡 <b>Рекомендации:</b>\n"
            "• Проверьте нагрузку на систему\n"
            "• Освободите место на диске\n"
            "• Перезапустите сервисы при необходимости"
        ),
        "buttons": [],
    },
    {
        "key": "ticket_autoclose_user",
        "cat": "Поддержка",
        "name": "🔒 Авто-закрытие тикета",
        "desc": "Клиенту, когда тикет авто-закрыт планировщиком (саппорт ответил, клиент молчит N часов).",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("ticket_id", "номер тикета"),
            ("hours", "часов молчания до авто-закрытия"),
        ],
        "default": (
            "🔒 <b>Тикет #{ticket_id} закрыт автоматически</b>\n\n"
            "Вы не ответили в течение {hours} ч. Если вопрос ещё актуален — "
            "просто напишите нам новым сообщением, и мы откроем обращение заново."
        ),
        "buttons": [
            {"id": "support", "label": "🆘 Написать в поддержку", "screen": "support"},
        ],
    },
    {
        "key": "backup_caption",
        "cat": "Система",
        "name": "🗄 Бэкап БД (подпись)",
        "desc": "Подпись к файлу ежедневного бэкапа БД для администраторов. Выключение останавливает ОТПРАВКУ файла в Telegram (на сервере бэкап всё равно создаётся).",
        "target": "admin",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("filename", "имя файла архива"),
        ],
        "default": "🗄 Бэкап БД: {filename}",
        "buttons": [],
    },
    {
        "key": "support_ban",
        "cat": "Поддержка",
        "name": "🚫 Бан из поддержки",
        "desc": "Клиенту при бане из support-бота (карточка тикета). Кнопка поддержки остаётся в коде.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [],
        "default": "🚫 Ваш аккаунт был заблокирован администратором. Если это ошибка — свяжитесь с поддержкой.",
        "buttons": [
            {"id": "support", "label": "🆘 Написать в поддержку", "url": "https://t.me/SpectraSokol_Support_bot"},
        ],
    },
    {
        "key": "support_unban",
        "cat": "Поддержка",
        "name": "✅ Разбан из поддержки",
        "desc": "Клиенту при разбане из support-бота (карточка тикета).",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [],
        "default": "✅ Ваш аккаунт был разблокирован. Вы снова можете пользоваться ботом.",
        "buttons": [
            {"id": "open", "label": "📲 Открыть приложение", "screen": "home"},
            {"id": "keys", "label": "🔑 Мои ключи", "screen": "keys"},
        ],
    },
    {
        "key": "device_limit_notice",
        "cat": "Покупка и баланс",
        "name": "📱 Лимит устройств (после продления)",
        "desc": "Клиенту после продления, когда у ключа ВПЕРВЫЕ появляется лимит устройств (мигрированные: был ∞ → тариф N). Строка-предупреждение о превышении и кнопки добавляются в коде только если устройств больше тарифа.",
        "target": "user",
        "parse_mode": "HTML",
        "timing": None,
        "placeholders": [
            ("limit", "лимит устройств по тарифу"),
            ("connected", "сколько устройств подключено сейчас"),
            ("traffic", "лимит трафика всего, ГБ/мес"),
            ("per_device", "ГБ трафика на одно устройство"),
        ],
        "default": (
            "📱 Ваш тариф: <b>{limit} устройств</b> (подключено {connected}).\n"
            "📊 Трафик: <b>{traffic} ГБ в месяц</b> (по {per_device} ГБ на устройство, обновляется каждый месяц).\n"
            "Мы обновили тарифы: подписка теперь на выбранное число устройств и трафик (раньше — без ограничений), минимум 2 устройства. Платите за то, что используете."
        ),
        "buttons": [
            {"id": "more", "label": "💳 Больше устройств", "screen": "buy"},
            {"id": "devices", "label": "📱 Мои устройства", "screen": "keys"},
        ],
    },

    # ===== РЕ-ЭНГЕЙДЖМЕНТ (ВОРОНКА) — цепочки-напоминания =====
    # Тайминг касаний задаётся в движке reengagement.py; здесь — только тексты/кнопки.
    # Работают ТОЛЬКО при мастер-тумблере reengage_enabled=true (по умолчанию выкл).
    {
        "key": "re_abandoned_pay_1",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "🛒 Брошенная оплата · 1 (через 30 мин)",
        "desc": "Создал счёт, но не оплатил ~30 мин. Мягкое напоминание завершить.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [("amount", "сумма счёта, ₽")],
        "default": (
            "🛒 **Вы почти оформили подписку!**\n\n"
            "Счёт на **{amount} ₽** ждёт оплаты. Остались вопросы или что-то не получилось?\n"
            "Завершите оплату — доступ откроется сразу."
        ),
        "buttons": [
            {"id": "pay", "label": "💳 Завершить оплату", "screen": "buy"},
            {"id": "help", "label": "🆘 Поддержка", "screen": "support"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_abandoned_pay_2",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "🛒 Брошенная оплата · 2 (в тот же день)",
        "desc": "Через несколько часов после незавершённой оплаты — помощь с оплатой.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [("amount", "сумма счёта, ₽")],
        "default": (
            "💳 **Не получилось оплатить?**\n\n"
            "Если возникли сложности с оплатой — напишите нам, поможем подобрать удобный способ "
            "и оформить подписку за минуту."
        ),
        "buttons": [
            {"id": "pay", "label": "💳 Оплатить", "screen": "buy"},
            {"id": "help", "label": "🆘 Поддержка", "screen": "support"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_abandoned_pay_3",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "🛒 Брошенная оплата · 3 (следующий день)",
        "desc": "На следующий день — финальное касание по брошенной оплате.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [("amount", "сумма счёта, ₽")],
        "default": (
            "⏳ **Ваш счёт всё ещё активен**\n\n"
            "Подписка на **{amount} ₽** ждёт вас. Оформите сегодня — и пользуйтесь VPN без ограничений."
        ),
        "buttons": [
            {"id": "pay", "label": "💳 Оформить", "screen": "buy"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_no_trial_1",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "🎁 Зашёл, не взял тест · 1 (через 1 ч)",
        "desc": "Новый юзер зашёл ~час назад, не активировал пробный, ключей нет.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "👋 **Вы заходили к нам — всё получилось?**\n\n"
            "Похоже, вы ещё не активировали **бесплатный пробный период**. Если что-то не вышло "
            "или остались вопросы — напишите в поддержку, поможем настроить за пару минут."
        ),
        "buttons": [
            {"id": "open", "label": "🎁 Активировать пробный", "screen": "home"},
            {"id": "help", "label": "🆘 Поддержка", "screen": "support"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_no_trial_2",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "🎁 Зашёл, не взял тест · 2 (следующий день)",
        "desc": "На следующий день — напоминание про бесплатный тест + польза.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "🎁 **Пробный период — бесплатно**\n\n"
            "Быстрый доступ, стабильная скорость, работает там, где другие не работают. "
            "Попробуйте — активация в один тап, карта не нужна."
        ),
        "buttons": [
            {"id": "open", "label": "🚀 Попробовать бесплатно", "screen": "home"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_no_trial_3",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "🎁 Зашёл, не взял тест · 3 (через день)",
        "desc": "Финальное мягкое касание для не активировавших тест.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "⌛ **Ваш бесплатный доступ всё ещё ждёт**\n\n"
            "Активируйте пробный период сегодня и оцените сервис без вложений. "
            "Не получается — мы на связи в поддержке."
        ),
        "buttons": [
            {"id": "open", "label": "🎁 Активировать", "screen": "home"},
            {"id": "help", "label": "🆘 Поддержка", "screen": "support"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_winback_1",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "💔 Подписка кончилась · 1 (следующий день)",
        "desc": "Платная подписка истекла ~1 день назад, не продлил.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "🔔 **Ваша подписка закончилась**\n\n"
            "Доступ к VPN приостановлен. Продлите — и всё снова заработает мгновенно, "
            "ваши настройки и ключ на месте."
        ),
        "buttons": [
            {"id": "buy", "label": "💳 Продлить подписку", "screen": "buy"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_winback_2",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "💔 Подписка кончилась · 2 (через 3 дня)",
        "desc": "Через ~3 дня — возврат со скидкой (владелец может вставить промокод в текст).",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "💙 **Скучаем!**\n\n"
            "Возвращайтесь — оформите подписку снова и пользуйтесь VPN без ограничений. "
            "Если что-то не устроило — расскажите нам, мы поможем."
        ),
        "buttons": [
            {"id": "buy", "label": "💳 Вернуться", "screen": "buy"},
            {"id": "help", "label": "🆘 Поддержка", "screen": "support"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_winback_3",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "💔 Подписка кончилась · 3 (через 7 дней)",
        "desc": "Через ~неделю — «сохраните условия / последний повод».",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "⭐ **Возвращайтесь на выгодных условиях**\n\n"
            "Оформите подписку сейчас и снова получите быстрый и стабильный доступ. "
            "Мы улучшили серверы — будет ещё лучше, чем раньше."
        ),
        "buttons": [
            {"id": "buy", "label": "💳 Оформить подписку", "screen": "buy"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },
    {
        "key": "re_winback_4",
        "cat": "Ре-энгейджмент (воронка)",
        "name": "💔 Подписка кончилась · 4 (через 14 дней)",
        "desc": "Финальное касание win-back; дальше юзера не трогаем автоматически.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "🙌 **Будем рады видеть вас снова**\n\n"
            "Если решите вернуться — доступ откроется сразу после оформления. "
            "Спасибо, что были с нами!"
        ),
        "buttons": [
            {"id": "buy", "label": "💳 Вернуться", "screen": "buy"},
            {"id": "mute", "label": "🔕 Не напоминать", "cb": "re_optout"},
        ],
    },

    # ===== ПРОМО / АНОНСЫ (для рассылки владельцем) =====
    {
        "key": "referral_promo",
        "cat": "Промо / анонсы",
        "name": "🤝 Анонс: партнёрская программа",
        "desc": "Анонс реф-программы для рассылки существующим юзерам. Разослать: админка → Рассылка (скопировать текст) или тест-кнопкой себе.",
        "target": "user", "parse_mode": "Markdown", "timing": None,
        "placeholders": [],
        "default": (
            "🤝 **Приглашай друзей — выгодно обоим!**\n\n"
            "🎁 **Ты получаешь** бонусы на баланс с каждой покупки друга.\n"
            "👋 **Друг получает** скидку на первую подписку.\n\n"
            "Твоя персональная ссылка — в профиле. Делись и зарабатывай! 🚀"
        ),
        "buttons": [
            {"id": "ref", "label": "🔗 Моя ссылка", "screen": "profile"},
            {"id": "buy", "label": "💳 Купить VPN", "screen": "buy"},
        ],
    },
]

NOTIF_BY_KEY: dict[str, dict] = {n["key"]: n for n in NOTIFICATIONS}

# Пример-данные для рендера в тест-отправке
SAMPLE_CONTEXT: dict[str, str] = {
    "time_text": "2 дня",
    "expiry": "20.06.2026 в 17:18",
    "key_number": "1",
    "action_text": "готов",
    "email": "user12345@bot",
    "connection": "vless://example-connection-string",
    "amount": "150.00",
    "balance": "150.00",
    "username": "@user",
    "user_id": "123456789",
    "plan": "1 мес · 2 устройства",
    "method": "ЮKassa",
    "host": "SpectraSokol",
    "ticket_id": "42",
    "hours": "24",
    "months": "1",
    "key_action": "Новый ключ ➕",
    "rub_today": "12 500.00",
    "crypto_today": "0.00",
    "days": "30",
    "code": "SUMMER",
    "reward_text": "100 ₽",
    "new_user": "@newbie",
    "key_id": "1",
    "header_emoji": "🚨",
    "header_text": "КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ",
    "obj_name": "🖥️ Панель (panel)",
    "time": "18.06.2026 21:00:00",
    "issues": "  🔴 <b>Процессор:</b> 95.0% (порог: 90%)\n  🔴 <b>Память:</b> 92.0% (порог: 85%)",
    "filename": "users.db.backup-20260618.zip",
    "limit": "2",
    "connected": "3",
    "traffic": "500",
    "per_device": "250",
}

# Экраны Mini App для выбора цели кнопки в админке (value = screen-токен, см. app.html initApp)
SCREEN_OPTIONS: list[tuple[str, str]] = [
    ("home", "🏠 Главная"),
    ("keys", "🔑 Мои ключи"),
    ("buy", "💳 Тарифы / Купить"),
    ("renew", "➕ Продление"),
    ("profile", "👤 Профиль"),
    ("support", "🆘 Поддержка"),
    ("setup", "📖 Инструкция"),
]


class _SafeDict(dict):
    def __missing__(self, key):  # неизвестный плейсхолдер оставляем как есть
        return "{" + key + "}"


def _render(template: str, variables: dict) -> str:
    try:
        return string.Formatter().vformat(template, (), _SafeDict(**(variables or {})))
    except Exception:
        return template


def notif_enabled(key: str) -> bool:
    val = get_setting(f"notif_{key}_enabled")
    if val is None or str(val).strip() == "":
        return True  # по умолчанию включено
    return str(val).strip().lower() not in ("false", "0", "no", "off")


def get_notif_text(key: str, **variables) -> str:
    """Текст уведомления: из настройки notif_<key>_text, иначе дефолт из реестра."""
    spec = NOTIF_BY_KEY.get(key, {})
    custom = get_setting(f"notif_{key}_text")
    template = custom if (custom and str(custom).strip()) else spec.get("default", "")
    return _render(template, variables)


def notif_hours(key: str) -> set[int]:
    """Часы-до-окончания для напоминания: из notif_<key>_hours, иначе дефолт реестра."""
    spec = NOTIF_BY_KEY.get(key, {})
    raw = get_setting(f"notif_{key}_hours")
    if not raw or not str(raw).strip():
        raw = spec.get("timing") or ""
    hours: set[int] = set()
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            hours.add(int(part))
    return hours or {72, 48, 24, 1}


def notif_buttons(key: str, webapp_url: str | None = None, **variables) -> list[dict]:
    """Кнопки уведомления в нейтральном виде: [{label, web_app_url|url|cb}].
    Подпись — из notif_<key>_btn_<id>_label (иначе дефолт). Тип кнопки по реестру:
      screen → web_app в Mini App (webapp_url + &screen=<screen>);
      url    → обычная ссылка (напр. «Написать в поддержку»);
      cb     → callback_data (легаси).
    Если у кнопки задан screen, но webapp_url пуст (приложение выключено) — кнопка пропускается."""
    spec = NOTIF_BY_KEY.get(key, {})
    out = []
    for b in spec.get("buttons", []):
        label = get_setting(f"notif_{key}_btn_{b['id']}_label")
        if not (label and str(label).strip()):
            label = b["label"]
        screen = b.get("screen")
        if screen is not None:
            _ov = (get_setting(f"notif_{key}_btn_{b['id']}_screen") or "").strip()
            if _ov:
                screen = _ov  # экран переопределён в админке
            if not webapp_url:
                continue
            scr = _render(str(screen), variables) if "{" in str(screen) else str(screen)
            sep = "&" if "?" in webapp_url else "?"
            out.append({"label": label, "web_app_url": f"{webapp_url}{sep}screen={scr}"})
        elif b.get("url"):
            url = str(b["url"])
            out.append({"label": label, "url": _render(url, variables) if "{" in url else url})
        else:
            cb = b.get("cb", "")
            out.append({"label": label, "cb": _render(cb, variables) if "{" in cb else cb})
    return out


def inline_keyboard_rows(buttons: list[dict], per_row: int = 2) -> list[list[dict]]:
    """Раскладка кнопок в строки для сырого Telegram API (inline_keyboard).
    Поддерживает web_app (Mini App), url и callback_data."""
    rows, row = [], []
    for b in buttons:
        if b.get("web_app_url"):
            tg = {"text": b["label"], "web_app": {"url": b["web_app_url"]}}
        elif b.get("url"):
            tg = {"text": b["label"], "url": b["url"]}
        else:
            tg = {"text": b["label"], "callback_data": b.get("cb") or "noop"}
        row.append(tg)
        if len(row) >= per_row:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows


def render_sample(key: str) -> str:
    """Текст с примерными данными — для предпросмотра/тест-отправки."""
    return get_notif_text(key, **SAMPLE_CONTEXT)


def notifications_for_admin() -> list[dict]:
    """Данные для вкладки админки: реестр + текущие значения настроек."""
    out = []
    for n in NOTIFICATIONS:
        k = n["key"]
        btns = []
        for b in n.get("buttons", []):
            btns.append({
                "id": b["id"],
                "default": b["label"],
                "cur": (get_setting(f"notif_{k}_btn_{b['id']}_label") or "").strip(),
                "is_screen": b.get("screen") is not None,
                "screen_default": b.get("screen") or "",
                "screen_cur": (get_setting(f"notif_{k}_btn_{b['id']}_screen") or "").strip(),
                "url": b.get("url") or "",
            })
        out.append({
            **n,
            "cur_text": (get_setting(f"notif_{k}_text") or "").strip(),
            "cur_enabled": notif_enabled(k),
            "cur_hours": (get_setting(f"notif_{k}_hours") or "").strip(),
            "cur_buttons": btns,
        })
    return out
