import sqlite3
from datetime import datetime, timezone, timedelta
import logging
from pathlib import Path
import json
import re
from typing import Any

logger = logging.getLogger(__name__)


import os
if os.path.exists("/app/project/users.db"):

    DB_FILE = Path("/app/project/users.db")
elif os.path.exists("users-20251005-173430.db"):

    DB_FILE = Path("users-20251005-173430.db")
elif os.path.exists("users.db"):

    DB_FILE = Path("users.db")
else:

    DB_FILE = Path("users.db")


# ===== GET_MSK_TIME =====
def get_msk_time() -> datetime:
    return datetime.now(timezone(timedelta(hours=3)))
# ========================


# ===== _NOW_STR =====
def _now_str() -> str:
    return get_msk_time().strftime("%Y-%m-%d %H:%M:%S")
# ======================


# ===== _TO_DATETIME_STR =====
def _to_datetime_str(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(get_msk_time().tzinfo)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
# ============================


# ===== _NORMALIZE_EMAIL =====
def _normalize_email(value: str | None) -> str | None:
    if value is None: return None
    cleaned = value.strip().lower(); return cleaned or None
# ============================


# ===== _NORMALIZE_KEY_ROW =====
def _normalize_key_row(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None: return None
    data = dict(row)
    email = _normalize_email(data.get("email") or data.get("key_email"))
    if email: data["email"] = email; data["key_email"] = email
    rem_uuid = data.get("remnawave_user_uuid") or data.get("xui_client_uuid")
    if rem_uuid: data["remnawave_user_uuid"] = rem_uuid; data["xui_client_uuid"] = rem_uuid
    expire_value = data.get("expire_at") or data.get("expiry_date")
    if expire_value:
        expire_str = expire_value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(expire_value, datetime) else str(expire_value)
        data["expire_at"] = expire_str
        data["expiry_date"] = expire_str
    created_value = data.get("created_at") or data.get("created_date")
    if created_value:
        created_str = created_value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(created_value, datetime) else str(created_value)
        data["created_at"] = created_str
        data["created_date"] = created_str
    subscription_url = data.get("subscription_url") or data.get("connection_string")
    if subscription_url: data["subscription_url"] = subscription_url; data.setdefault("connection_string", subscription_url)
    return data
# ==============================


# ===== _GET_TABLE_COLUMNS =====
def _get_table_columns(cursor: sqlite3.Cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})"); return {row[1] for row in cursor.fetchall()}
# ==============================


# ===== _ENSURE_TABLE_COLUMN =====
def _ensure_table_column(cursor: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    columns = _get_table_columns(cursor, table)
    if column not in columns: cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
# ================================


# ===== _ENSURE_UNIQUE_INDEX =====
def _ensure_unique_index(cursor: sqlite3.Cursor, name: str, table: str, column: str) -> None:
    cursor.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table}({column})")
# ================================


# ===== _ENSURE_INDEX =====
def _ensure_index(cursor: sqlite3.Cursor, name: str, table: str, column: str) -> None:
    cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")
# ===========================


# ===== NORMALIZE_HOST_NAME =====
def normalize_host_name(name: str | None) -> str:
    s = (name or "").strip()
    for ch in ("\u00A0", "\u200B", "\u200C", "\u200D", "\uFEFF"): s = s.replace(ch, "")
    return s
# ===============================


# ===== GET_DB_CONNECTION =====
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    wal_enabled = False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_settings WHERE key='enable_wal_mode'")
        row = cursor.fetchone()
        if row and row[0] == '1':
            wal_enabled = True
    except Exception:
        pass
    if wal_enabled:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    else:
        conn.execute("PRAGMA journal_mode=DELETE")
    return conn
# ==============================





class DbExecResult:
    def __init__(self, cursor: sqlite3.Cursor):
        self.lastrowid = cursor.lastrowid; self.rowcount = cursor.rowcount

# ===== _EXEC =====
def _exec(sql: str, params: tuple | list = (), error_msg: str = "", commit: bool = True) -> DbExecResult | None:
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            if commit: conn.commit()
            return DbExecResult(cursor)
    except sqlite3.Error as e:
        if error_msg: logging.error(f"{error_msg}: {e}")
        return None
# =================


# ===== _FETCH_ROW =====
def _fetch_row(sql: str, params: tuple | list = (), error_msg: str = "") -> dict | None:
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        if error_msg: logging.error(f"{error_msg}: {e}")
        return None
# ======================


# ===== _FETCH_LIST =====
def _fetch_list(sql: str, params: tuple | list = (), error_msg: str = "") -> list[dict]:
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        if error_msg: logging.error(f"{error_msg}: {e}")
        return []
# =======================


# ===== _FETCH_VAL =====
def _fetch_val(sql: str, params: tuple | list = (), default: Any = None, error_msg: str = "") -> Any:
    row = _fetch_row(sql, params, error_msg)
    return list(row.values())[0] if row else default
# ======================

# ===== УНИВЕРСАЛЬНЫЕ ХЕЛПЕРЫ DRY =====

def _check_rowcount(cursor, entity_name: str, context: str = "") -> bool:
    if cursor and cursor.rowcount == 0:
        msg = f"{context}: {entity_name} не найден" if context else f"{entity_name} не найден"
        logging.warning(msg)
        return False
    return cursor is not None

def _exec_with_check(sql: str, params: tuple | list, entity_name: str, error_msg: str = "", context: str = "") -> bool:
    row = _fetch_row(f"SELECT 1 FROM {entity_name.split()[0] if ' ' in entity_name else entity_name}", params[:1] if params else (), "")
    if not row:
        if context: logging.warning(f"{context}: объект не найден")
        return False
    cursor = _exec(sql, params, error_msg)
    return cursor is not None

def _get_count_stat(query: str, default=0) -> int:
    r = _fetch_row(query, (), "")
    return int(r["c"]) if r and "c" in r else (int(r["s"]) if r and "s" in r else default)

# ========================


# ===== INITIALIZE_DB =====
def initialize_db():
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    total_spent REAL DEFAULT 0,
                    total_months INTEGER DEFAULT 0,
                    trial_used BOOLEAN DEFAULT 0,
                    agreed_to_terms BOOLEAN DEFAULT 0,
                    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_banned BOOLEAN DEFAULT 0,
                    balance REAL DEFAULT 0,
                    referred_by INTEGER,
                    referral_balance REAL DEFAULT 0,
                    referral_balance_all REAL DEFAULT 0,
                    referral_start_bonus_received BOOLEAN DEFAULT 0,
                    is_pinned BOOLEAN DEFAULT 0,
                    seller_active INTEGER DEFAULT 0,
                    auth_token TEXT,
                    auth_email TEXT,
                    auth_pass TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_transactions (
                    payment_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    amount_rub REAL,
                    metadata TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vpn_keys (
                    key_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    host_name TEXT,
                    squad_uuid TEXT,
                    remnawave_user_uuid TEXT,
                    short_uuid TEXT,
                    email TEXT UNIQUE,
                    key_email TEXT UNIQUE,
                    subscription_url TEXT,
                    expire_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    traffic_limit_bytes INTEGER,
                    traffic_limit_strategy TEXT DEFAULT 'NO_RESET',
                    tag TEXT,
                    description TEXT,
                    comment_key TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    username TEXT,
                    transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    amount_rub REAL NOT NULL,
                    amount_currency REAL,
                    currency_name TEXT,
                    payment_method TEXT,
                    metadata TEXT,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Инициализация дефолтных настроек
            cursor.execute('''
                INSERT OR IGNORE INTO bot_settings (key, value) 
                VALUES (?, ?)
            ''', ('pay_info_comment', json.dumps({"id": 1, "username": 1, "first_name": 1, "host_name": 1})))
            
            cursor.execute('''
                INSERT OR IGNORE INTO bot_settings (key, value) 
                VALUES (?, ?)
            ''', ('skip_email', '0'))
            
            cursor.execute('''
                INSERT OR IGNORE INTO bot_settings (key, value) 
                VALUES (?, ?)
            ''', ('enable_wal_mode', '0'))

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS other (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Инициализация записи для рассылки
            _ensure_default_values(cursor, "other", {
                "newsletter": json.dumps({}),
                "sg_promt": "",
                "theme_newsletter": json.dumps({}),
                "auto_start_bot": "0"
            })

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS button_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    menu_type TEXT NOT NULL,
                    button_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    callback_data TEXT,
                    url TEXT,
                    row_position INTEGER DEFAULT 0,
                    column_position INTEGER DEFAULT 0,
                    button_width INTEGER DEFAULT 1,
                    is_active INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(menu_type, button_id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS xui_hosts(
                    host_name TEXT PRIMARY KEY,
                    squad_uuid TEXT UNIQUE,
                    description TEXT,
                    default_traffic_limit_bytes INTEGER,
                    default_traffic_strategy TEXT DEFAULT 'NO_RESET',
                    host_url TEXT,
                    host_username TEXT,
                    host_pass TEXT,
                    host_inbound_id INTEGER,
                    subscription_url TEXT,
                    ssh_host TEXT,
                    ssh_port INTEGER,
                    ssh_user TEXT,
                    ssh_password TEXT,
                    ssh_key_path TEXT,
                    is_active INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    metadata TEXT,
                    see INTEGER DEFAULT 1
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_name TEXT,
                    squad_uuid TEXT,
                    plan_name TEXT NOT NULL,
                    months INTEGER,
                    duration_days INTEGER,
                    price REAL NOT NULL,
                    traffic_limit_bytes INTEGER,
                    traffic_limit_strategy TEXT DEFAULT 'NO_RESET',
                    is_active INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    metadata TEXT,
                    hwid_limit INTEGER DEFAULT 0,
                    traffic_limit_gb INTEGER DEFAULT 0,
                    FOREIGN KEY (host_name) REFERENCES xui_hosts (host_name)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS support_tickets (
                    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT "open",
                    subject TEXT,
                    forum_chat_id TEXT,
                    message_thread_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS support_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    sender TEXT NOT NULL,
                    content TEXT NOT NULL,
                    media TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES support_tickets (ticket_id)
                )
            ''')

            _ensure_index(cursor, "idx_support_tickets_status", "support_tickets", "status")
            _ensure_index(cursor, "idx_support_tickets_thread", "support_tickets", "forum_chat_id, message_thread_id")
            _ensure_index(cursor, "idx_support_messages_ticket_id", "support_messages", "ticket_id")
            _ensure_table_column(cursor, "support_messages", "media", "TEXT")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS seller_users (
                    id_seller INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_sale REAL DEFAULT 0,
                    seller_ref REAL DEFAULT 0,
                    seller_uuid TEXT DEFAULT '0',
                    user_id INTEGER UNIQUE
                )
            ''')
            _ensure_unique_index(cursor, "idx_seller_users_user_id", "seller_users", "user_id")

            try:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_thread ON support_tickets(forum_chat_id, message_thread_id)")
            except Exception:
                pass
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS host_speedtests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_name TEXT NOT NULL,
                    method TEXT NOT NULL,
                    ping_ms REAL,
                    jitter_ms REAL,
                    download_mbps REAL,
                    upload_mbps REAL,
                    server_name TEXT,
                    server_id TEXT,
                    ok INTEGER NOT NULL DEFAULT 1,
                    error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_host_speedtests_host_time ON host_speedtests(host_name, created_at DESC)")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS resource_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,                -- 'local' | 'host' | 'target'
                    object_name TEXT NOT NULL,          -- 'panel' | host_name | target_name
                    cpu_percent REAL,
                    mem_percent REAL,
                    disk_percent REAL,
                    load1 REAL,
                    net_bytes_sent INTEGER,
                    net_bytes_recv INTEGER,
                    raw_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_resource_metrics_scope_time ON resource_metrics(scope, object_name, created_at DESC)")

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS speedtest_ssh_targets (
                    target_name TEXT PRIMARY KEY,
                    ssh_host TEXT NOT NULL,
                    ssh_port INTEGER DEFAULT 22,
                    ssh_user TEXT,
                    ssh_password TEXT, 
                    ssh_key_path TEXT,
                    description TEXT,
                    is_active INTEGER DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    metadata TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_labels (
                    user_uuid TEXT NOT NULL,
                    hwid TEXT NOT NULL,
                    label TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_uuid, hwid)
                )
            ''')

            default_settings = {
                "panel_login": "admin",
                "panel_password": "admin",
                "about_text": None,
                "terms_url": None,
                "privacy_url": None,
                "support_user": None,
                "support_text": None,
                "channel_url": None,
                "force_subscription": "true",
                "receipt_email": "example@example.com",
                "telegram_bot_token": None,
                "telegram_bot_username": None,
                "trial_enabled": "true",
                "trial_duration_days": "1",
                # Ре-энгейджмент (воронка): МАСТЕР по умолчанию ВЫКЛЮЧЕН — ни одно
                # напоминание не уйдёт, пока владелец не включит в админке.
                "reengage_enabled": "false",
                "reengage_quiet_start": "10",   # тихие часы: не слать раньше 10:00 МСК
                "reengage_quiet_end": "21",     # и позже 21:00 МСК
                "reengage_daily_cap": "1",      # не больше N авто-оповещений в сутки на юзера
                "reengage_weekly_cap": "3",     # и не больше N в неделю
                "reengage_run_cap": "25",       # не больше N отправок за один прогон (плавный слив бэклога)
                "reengage_test_only_uid": "",   # тест-режим: если задан — движок шлёт ТОЛЬКО этому user_id
                "enable_referrals": "true",
                "referral_percentage": "10",
                "referral_discount": "5",
                "minimum_withdrawal": "100",
                "admin_telegram_id": None,
                "admin_telegram_ids": None,
                "yookassa_shop_id": None,
                "yookassa_secret_key": None,
                "sbp_enabled": "false",
                "cryptobot_token": None,
                "heleket_merchant_id": None,
                "heleket_api_key": None,
                "domain": None,
                "ton_wallet_address": None,
                "tonapi_key": None,
                "support_forum_chat_id": None,
                "enable_fixed_referral_bonus": "false",
                "fixed_referral_bonus_amount": "50",
                "referral_reward_type": "percent_purchase",
                "referral_on_start_referrer_amount": "20",
                "backup_interval_days": "1",
                "backup_interval_hours": "1",

                # FAQ поддержки (видео-инструкции). Редактируется в админке. Список [{title,url}].
                "support_faq_json": '[{"title":"Купить подписку","url":"https://t.me/Info_Alma/84"},{"title":"Продлить подписку","url":"https://t.me/Info_Alma/86"},{"title":"Пробный период","url":"https://t.me/Info_Alma/88"},{"title":"Тарифы","url":"https://t.me/Info_Alma/90"},{"title":"Найти ключи","url":"https://t.me/Info_Alma/92"},{"title":"Ключ на устройство","url":"https://t.me/Info_Alma/94"},{"title":"Промокод","url":"https://t.me/Info_Alma/96"},{"title":"Рефералка","url":"https://t.me/Info_Alma/98"},{"title":"Название ключа","url":"https://t.me/Info_Alma/100"},{"title":"Срок ключей","url":"https://t.me/Info_Alma/102"},{"title":"Рос. приложения","url":"https://t.me/Info_Alma/104"},{"title":"Сменить устройство","url":"https://t.me/Info_Alma/106"}]',
                "support_faq_intro": "❗️ <b>Актуальные вопросы</b> ❗️\\n\\nВозможно, ответ на ваш вопрос уже есть в коротком видео 👇\\nНе нашли? Нажмите «🆘 Позвать оператора».",

                "monitoring_enabled": "true",
                "monitoring_interval_sec": "300",
                "monitoring_cpu_threshold": "90",
                "monitoring_mem_threshold": "90",
                "monitoring_disk_threshold": "90",
                "monitoring_alert_cooldown_sec": "3600",
                "remnawave_base_url": None,
                "remnawave_api_token": None,
                "remnawave_cookies": "{}",
                "remnawave_is_local_network": "false",
                "default_extension_days": "30",

                "main_menu_text": None,
                "howto_intro_text": None,
                "howto_android_text": None,
                "howto_ios_text": None,
                "howto_windows_text": None,
                "howto_linux_text": None,

                "btn_trial_text": None,
                "btn_profile_text": None,
                "btn_my_keys_text": None,
                "btn_buy_key_text": None,
                "btn_topup_text": None,
                "btn_referral_text": None,
                "btn_support_text": None,
                "btn_about_text": None,
                "btn_speed_text": None,
                "btn_howto_text": None,
                "btn_admin_text": None,
                "btn_back_to_menu_text": None,
                "btn_trial_button_style": None,
                "btn_trial_icon_emoji_id": None,
                "btn_profile_button_style": None,
                "btn_profile_icon_emoji_id": None,
                "btn_my_keys_button_style": None,
                "btn_my_keys_icon_emoji_id": None,
                "btn_buy_key_button_style": None,
                "btn_buy_key_icon_emoji_id": None,
                "btn_topup_button_style": None,
                "btn_topup_icon_emoji_id": None,
                "btn_referral_button_style": None,
                "btn_referral_icon_emoji_id": None,
                "btn_support_button_style": None,
                "btn_support_icon_emoji_id": None,
                "btn_about_button_style": None,
                "btn_about_icon_emoji_id": None,
                "btn_howto_button_style": None,
                "btn_howto_icon_emoji_id": None,
                "btn_speed_button_style": None,
                "btn_speed_icon_emoji_id": None,
                "btn_admin_button_style": None,
                "btn_admin_icon_emoji_id": None,
                "btn_back_to_menu_button_style": None,
                "btn_back_to_menu_icon_emoji_id": None,

                "stars_enabled": "false",
                "yoomoney_enabled": "false",
                "yoomoney_wallet": None,
                "yoomoney_secret": None,

                "yoomoney_api_token": None,
                "yoomoney_client_id": None,
                "yoomoney_client_secret": None,
                "yoomoney_redirect_uri": None,
                "stars_per_rub": "1",
                
                "platega_enabled": "false",
                "platega_crypto_enabled": "false",
                "platega_merchant_id": None,
                "platega_api_key": None,

                "main_menu_image": None,
                "profile_image": None,  
                "topup_image": None, 
                "referral_image": None,
                "support_image": None,
                "about_image": None,
                "speedtest_image": None,
                "howto_image": None,
                "topup_amount_image": None,

                "payment_image": None,
                "buy_server_image": None,
                "buy_plan_image": None,
                "enter_email_image": None,
                "key_info_image": None,
                "extend_plan_image": None,
                "keys_list_image": None,
                "payment_method_image": None,
                "key_comments_image": None,
                "key_ready_image": None,
                "devices_list_image": None,
                "key_gemini": None,
                "stealth_login_enabled": "0",
                "stealth_login_hotkey": "ctrl+b",
                "dashboard_layout": "sidebar",
                "demo_mode_enabled": "0",
            }
            _ensure_default_values(cursor, "bot_settings", default_settings)
            conn.commit()
            

            



            try:
                cursor.execute("ALTER TABLE button_configs ADD COLUMN button_width INTEGER DEFAULT 1")
                logging.info("Добавлена колонка button_width в таблицу button_configs")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE button_configs ADD COLUMN button_color TEXT DEFAULT NULL")
                logging.info("Добавлена колонка button_color в таблицу button_configs")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE button_configs ADD COLUMN emoji_id TEXT DEFAULT NULL")
                logging.info("Добавлена колонка emoji_id в таблицу button_configs")
            except sqlite3.OperationalError:
                pass

            try:
                cursor.execute("ALTER TABLE support_tickets ADD COLUMN open_feed_msg_id INTEGER DEFAULT NULL")
                logging.info("Добавлена колонка open_feed_msg_id в таблицу support_tickets")
            except sqlite3.OperationalError:
                pass

            logging.info("База данных инициализирована")
        
        run_migration()
        
    except sqlite3.Error as e:
        logging.error("Не удалось инициализировать базу данных: %s", e)
# =========================


# ===== _ENSURE_DEFAULT_VALUES =====
def _ensure_default_values(cursor: sqlite3.Cursor, table: str, defaults: dict) -> None:
    for key, value in defaults.items():
        try:
            cursor.execute(
                f"INSERT OR IGNORE INTO {table} (key, value) VALUES (?, ?)",
                (key, value)
            )
        except Exception: pass
# ==================================


# ===== _ENSURE_USERS_COLUMNS =====
def _ensure_users_columns(cursor: sqlite3.Cursor) -> None:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cursor.fetchone(): return
    mapping = {
        "referred_by": "INTEGER",
        "balance": "REAL DEFAULT 0",
        "referral_balance": "REAL DEFAULT 0",
        "referral_balance_all": "REAL DEFAULT 0",
        "referral_start_bonus_received": "BOOLEAN DEFAULT 0",
        "is_pinned": "BOOLEAN DEFAULT 0",
        "seller_active": "INTEGER DEFAULT 0",
        "auth_token": "TEXT",
        "auth_email": "TEXT",
        "auth_pass": "TEXT",
        "recovery_code": "TEXT",
        "active_promo_code": "TEXT",
        "activated_promo_codes": "TEXT",
    }
    for column, definition in mapping.items():
        _ensure_table_column(cursor, "users", column, definition)


# =================================

# ===== DELETE_USER =====
def delete_user(telegram_id: int) -> bool:
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"Ошибка удаления пользователя {telegram_id}: {e}")
        return False
# =======================


# ===== _ENSURE_HOSTS_COLUMNS =====
def _ensure_hosts_columns(cursor: sqlite3.Cursor) -> None:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='xui_hosts'")
    if not cursor.fetchone(): return
    extras = {
        "squad_uuid": "TEXT",
        "description": "TEXT",
        "default_traffic_limit_bytes": "INTEGER",
        "default_traffic_strategy": "TEXT DEFAULT 'NO_RESET'",
        "default_traffic_reset_at": "TEXT",
        "is_active": "INTEGER DEFAULT 1",
        "sort_order": "INTEGER DEFAULT 0",
        "metadata": "TEXT",
        "subscription_url": "TEXT",
        "ssh_host": "TEXT",
        "ssh_port": "INTEGER",
        "ssh_user": "TEXT",
        "ssh_password": "TEXT",
        "ssh_key_path": "TEXT",

        "remnawave_base_url": "TEXT",
        "remnawave_api_token": "TEXT",
        "see": "INTEGER DEFAULT 1",
        "traffic_limit_strategy": "TEXT DEFAULT 'NO_RESET'",
        "device_mode": "TEXT DEFAULT 'plan'",
        "tier_lock_extend": "INTEGER DEFAULT 0",
        "button_style": "TEXT DEFAULT NULL",
        "icon_emoji_id": "TEXT DEFAULT NULL",
    }
    for column, definition in extras.items():
        _ensure_table_column(cursor, "xui_hosts", column, definition)


# =================================


def _ensure_device_tiers_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS device_tiers (
            tier_id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT NOT NULL,
            device_count INTEGER NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            UNIQUE(host_name, device_count)
        )
    ''')


# ===== _ENSURE_PLANS_COLUMNS =====
def _ensure_plans_columns(cursor: sqlite3.Cursor) -> None:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plans'")
    if not cursor.fetchone(): return
    extras = {
        "squad_uuid": "TEXT",
        "duration_days": "INTEGER",
        "traffic_limit_bytes": "INTEGER",
        "traffic_limit_strategy": "TEXT DEFAULT 'NO_RESET'",
        "is_active": "INTEGER DEFAULT 1",
        "sort_order": "INTEGER DEFAULT 0",
        "metadata": "TEXT",
        "hwid_limit": "INTEGER DEFAULT 0",
        "traffic_limit_gb": "INTEGER DEFAULT 0",
        "button_style": "TEXT DEFAULT NULL",
        "icon_emoji_id": "TEXT DEFAULT NULL",
    }
    for column, definition in extras.items():
        _ensure_table_column(cursor, "plans", column, definition)


# =================================


# ===== _ENSURE_SUPPORT_TICKETS_COLUMNS =====
def _ensure_support_tickets_columns(cursor: sqlite3.Cursor) -> None:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='support_tickets'")
    if not cursor.fetchone(): return
    extras = {
        "forum_chat_id": "TEXT",
        "message_thread_id": "INTEGER",
    }
    for column, definition in extras.items():
        _ensure_table_column(cursor, "support_tickets", column, definition)


# ===========================================


# ===== _FINALIZE_VPN_KEY_INDEXES =====
def _finalize_vpn_key_indexes(cursor: sqlite3.Cursor) -> None:
    _ensure_unique_index(cursor, "uq_vpn_keys_email", "vpn_keys", "email")
    _ensure_unique_index(cursor, "uq_vpn_keys_key_email", "vpn_keys", "key_email")
    _ensure_index(cursor, "idx_vpn_keys_user_id", "vpn_keys", "user_id")
    _ensure_index(cursor, "idx_vpn_keys_rem_uuid", "vpn_keys", "remnawave_user_uuid")
    _ensure_index(cursor, "idx_vpn_keys_expire_at", "vpn_keys", "expire_at")
    # transactions.user_id КРИТИЧЕН: без него dashboard-запрос get_dashboard_user_groups
    # делал full-scan transactions на каждого из ~9000 юзеров = 76с/запрос → CPU 100% при
    # автообновлении админки (сессия 9, 2026-06-26). С индексом: 76.62s → 0.34s.
    _ensure_index(cursor, "idx_transactions_user_id", "transactions", "user_id")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_status ON transactions(user_id, status)")


# =====================================


# ===== _REBUILD_VPN_KEYS_TABLE =====
def _rebuild_vpn_keys_table(cursor: sqlite3.Cursor) -> None:
    columns = _get_table_columns(cursor, "vpn_keys")
    legacy_markers = {"xui_client_uuid", "expiry_date", "created_date", "connection_string"}
    required = {"remnawave_user_uuid", "email", "expire_at", "created_at", "updated_at"}
    if required.issubset(columns) and not (columns & legacy_markers): _finalize_vpn_key_indexes(cursor); return

    cursor.execute("ALTER TABLE vpn_keys RENAME TO vpn_keys_legacy")
    cursor.execute('''
        CREATE TABLE vpn_keys (
            key_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            host_name TEXT,
            squad_uuid TEXT,
            remnawave_user_uuid TEXT,
            short_uuid TEXT,
            email TEXT UNIQUE,
            key_email TEXT UNIQUE,
            subscription_url TEXT,
            expire_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            traffic_limit_bytes INTEGER,
            traffic_limit_strategy TEXT DEFAULT 'NO_RESET',
            tag TEXT,
            description TEXT,
            comment_key TEXT
        )
    ''')
    old_columns = _get_table_columns(cursor, "vpn_keys_legacy")

    def has(column: str) -> bool: return column in old_columns

    def col(column: str, default: str = "NULL") -> str: return column if has(column) else default

    rem_uuid_expr = "remnawave_user_uuid" if has("remnawave_user_uuid") else ("xui_client_uuid" if has("xui_client_uuid") else "NULL")
    email_expr = "LOWER(email)" if has("email") else ("LOWER(key_email)" if has("key_email") else "NULL")
    key_email_expr = "LOWER(key_email)" if has("key_email") else ("LOWER(email)" if has("email") else "NULL")
    subscription_expr = col("subscription_url", "connection_string" if has("connection_string") else "NULL")
    expire_expr = col("expire_at", "expiry_date" if has("expiry_date") else "NULL")
    created_expr = col("created_at", "created_date" if has("created_date") else "CURRENT_TIMESTAMP")
    updated_expr = col("updated_at", created_expr)
    traffic_strategy_expr = col("traffic_limit_strategy", "'NO_RESET'")

    select_clause = ",\n            ".join([
        f"{col('key_id')} AS key_id",
        f"{col('user_id')} AS user_id",
        f"{col('host_name')} AS host_name",
        f"{col('squad_uuid')} AS squad_uuid",
        f"{rem_uuid_expr} AS remnawave_user_uuid",
        f"{col('short_uuid')} AS short_uuid",
        f"{email_expr} AS email",
        f"{key_email_expr} AS key_email",
        f"{subscription_expr} AS subscription_url",
        f"{expire_expr} AS expire_at",
        f"{created_expr} AS created_at",
        f"{updated_expr} AS updated_at",
        f"{col('traffic_limit_bytes')} AS traffic_limit_bytes",
        f"{traffic_strategy_expr} AS traffic_limit_strategy",
        f"{col('tag')} AS tag",
        f"{col('description')} AS description",
        f"{col('comment_key')} AS comment_key",
    ])

    cursor.execute(
        f"""
        INSERT INTO vpn_keys (
            key_id,
            user_id,
            host_name,
            squad_uuid,
            remnawave_user_uuid,
            short_uuid,
            email,
            key_email,
            subscription_url,
            expire_at,
            created_at,
            updated_at,
            traffic_limit_bytes,
            traffic_limit_strategy,
            tag,
            description,
            comment_key
        )
        SELECT
            {select_clause}
        FROM vpn_keys_legacy
        """
    )
    cursor.execute("DROP TABLE vpn_keys_legacy")
    cursor.execute("SELECT MAX(key_id) FROM vpn_keys")
    max_id = cursor.fetchone()[0]
    if max_id is not None:
        cursor.execute("INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES('vpn_keys', ?)", (max_id,))
    _finalize_vpn_key_indexes(cursor)


# ===================================


# ===== _ENSURE_VPN_KEYS_SCHEMA =====
def _ensure_vpn_keys_schema(cursor: sqlite3.Cursor) -> None:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vpn_keys'")
    if cursor.fetchone() is None:
        cursor.execute('''
            CREATE TABLE vpn_keys (
                key_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                host_name TEXT,
                squad_uuid TEXT,
                remnawave_user_uuid TEXT,
                short_uuid TEXT,
                email TEXT UNIQUE,
                key_email TEXT UNIQUE,
                subscription_url TEXT,
                expire_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                traffic_limit_bytes INTEGER,
                traffic_limit_strategy TEXT DEFAULT 'NO_RESET',
                tag TEXT,
                description TEXT,
                comment_key TEXT
            )
        ''')
        _finalize_vpn_key_indexes(cursor)
        return
    _rebuild_vpn_keys_table(cursor)


# ===================================


# ===== RUN_MIGRATION =====
# ===========================================
# ===== _ENSURE_WEBAPP_SETTINGS_TABLE =====
def _ensure_webapp_settings_table(cursor: sqlite3.Cursor):
    try:
        default_config_json = json.dumps(_default_webapp_config(), ensure_ascii=False)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS webapp_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                webapp_title TEXT DEFAULT 'VPN',
                webapp_domen TEXT DEFAULT '',
                webapp_enable INTEGER DEFAULT 0,
                webapp_logo TEXT DEFAULT '',
                webapp_icon TEXT DEFAULT '',
                tg_fullscreen INTEGER DEFAULT 0,
                webapp_config_json TEXT DEFAULT '{}'
            )
        ''')
        
        cursor.execute("PRAGMA table_info(webapp_settings)")
        columns = {row[1] for row in cursor.fetchall()}
        
        if "webapp_title" not in columns:
            cursor.execute("ALTER TABLE webapp_settings ADD COLUMN webapp_title TEXT DEFAULT 'VPN'")
        if "webapp_domen" not in columns:
            cursor.execute("ALTER TABLE webapp_settings ADD COLUMN webapp_domen TEXT DEFAULT ''")
        if "webapp_enable" not in columns:
            cursor.execute("ALTER TABLE webapp_settings ADD COLUMN webapp_enable INTEGER DEFAULT 0")
        if "webapp_logo" not in columns:
            cursor.execute("ALTER TABLE webapp_settings ADD COLUMN webapp_logo TEXT DEFAULT ''")
        if "webapp_icon" not in columns:
            cursor.execute("ALTER TABLE webapp_settings ADD COLUMN webapp_icon TEXT DEFAULT ''")
        if "tg_fullscreen" not in columns:
            cursor.execute("ALTER TABLE webapp_settings ADD COLUMN tg_fullscreen INTEGER DEFAULT 0")
        if "webapp_config_json" not in columns:
            cursor.execute("ALTER TABLE webapp_settings ADD COLUMN webapp_config_json TEXT DEFAULT '{}'")

        cursor.execute(
            "INSERT OR IGNORE INTO webapp_settings (id, webapp_title, webapp_domen, webapp_enable, webapp_logo, webapp_icon, webapp_config_json) VALUES (1, 'VPN', '', 0, '', '', ?)",
            (default_config_json,)
        )
        cursor.execute(
            "UPDATE webapp_settings SET webapp_config_json = ? WHERE id = 1 AND (webapp_config_json IS NULL OR TRIM(webapp_config_json) = '')",
            (default_config_json,)
        )
            
    except Exception as e:
        logging.error(f"Ошибка миграции webapp_settings: {e}")

# ===========================================


# ===== RUN_MIGRATION =====
def run_migration():
    if not DB_FILE.exists(): logging.error("Файл базы данных отсутствует, миграция пропущена."); return

    logging.info("Запуск миграций базы данных: %s", DB_FILE)

    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys = OFF")
            _ensure_users_columns(cursor)
            _ensure_hosts_columns(cursor)
            _ensure_device_tiers_table(cursor)
            _ensure_plans_columns(cursor)
            _ensure_support_tickets_columns(cursor)
            _ensure_vpn_keys_schema(cursor)
            _ensure_table_column(cursor, "vpn_keys", "comment_key", "TEXT")
            _ensure_table_column(cursor, "vpn_keys", "hwid_limit", "INTEGER DEFAULT 0")
            _ensure_ssh_targets_table(cursor)
            _ensure_host_speedtests_table(cursor)
            _ensure_resource_metrics_table(cursor)
            _ensure_gift_tokens_table(cursor)
            _ensure_promo_tables(cursor)
            _ensure_webapp_settings_table(cursor)
            try:
                cursor.execute("ALTER TABLE seller_users RENAME COLUMN sellr_ref TO seller_ref")
                logging.info("Переименована колонка sellr_ref в seller_ref в таблице seller_users")
            except Exception:
                pass

            _ensure_seller_users_table(cursor)
            _ensure_contests_table(cursor)
            _ensure_table_column(cursor, "users", "wheel_spins", "INTEGER DEFAULT 0")
            _ensure_wheel_tables(cursor)
            _ensure_table_column(cursor, "users", "reengage_optout", "INTEGER DEFAULT 0")
            _ensure_notification_log_table(cursor)

            try:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_tickets_thread ON support_tickets(forum_chat_id, message_thread_id)")
            except Exception:
                pass

            try:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS pending_transactions (
                        payment_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        amount_rub REAL,
                        metadata TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            except Exception:
                pass
            
            _ensure_default_values(cursor, "bot_settings", {
                "skip_email": "0",
                "enable_wal_mode": "0",
                "dashboard_layout": "sidebar",
                "demo_mode_enabled": "0"
            })
            
            _ensure_default_values(cursor, "other", {
                "theme_newsletter": json.dumps({}),
                "auto_start_bot": "0"
            })
            
            _ensure_pending_transactions_table(cursor)
            _ensure_default_button_configs(cursor)
            

            try:
                wide_buttons = [("trial", 2), ("referral", 2), ("admin", 2)]
                for button_id, width in wide_buttons:
                    cursor.execute("""
                        UPDATE button_configs 
                        SET button_width = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE menu_type = 'main_menu' AND button_id = ?
                    """, (width, button_id))
            except Exception:
                pass


            cursor.execute("PRAGMA foreign_keys = ON")
            conn.commit()
    except sqlite3.Error as e:
        logging.error("Сбой миграции базы данных: %s", e)

# =========================


# ===== _ENSURE_PENDING_TRANSACTIONS_TABLE =====
def _ensure_pending_transactions_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_transactions (
            payment_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            amount_rub REAL,
            metadata TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

# ==============================================


# ===== _ENSURE_DEFAULT_BUTTON_CONFIGS =====
def _ensure_default_button_configs(cursor: sqlite3.Cursor) -> None:
    def menu_has_buttons(m_type):
        cursor.execute("SELECT 1 FROM button_configs WHERE menu_type = ? LIMIT 1", (m_type,))
        return cursor.fetchone() is not None

    if not menu_has_buttons("main_menu"):
        main_menu_buttons = [
            ("trial", "🎁 Попробовать бесплатно", "get_trial", 0, 0, 0, 2),
            ("profile", "👤 Мой профиль", "show_profile", 1, 0, 1, 1),
            ("my_keys", "🔑 Мои ключи ({len(user_keys)})", "manage_keys", 1, 1, 2, 1),
            ("buy_key", "🛒 Купить ключ", "buy_new_key", 2, 0, 3, 1),
            ("topup", "💳 Пополнить баланс", "top_up_start", 2, 1, 4, 1),
            ("referral", "🤝 Реферальная программа", "show_referral_program", 3, 0, 5, 2),
            ("support", "🆘 Поддержка", "show_help", 4, 0, 6, 1),
            ("about", "ℹ️ О проекте", "show_about", 4, 1, 7, 1),
            ("speed", "⚡ Скорость", "user_speedtest_last", 5, 0, 8, 1),
            ("howto", "❓ Как использовать", "howto_vless", 5, 1, 9, 1),
            ("admin", "⚙️ Админка", "admin_menu", 6, 0, 10, 2),
        ]
        
        for button_id, text, callback_data, row_pos, col_pos, sort_order, button_width in main_menu_buttons:
            cursor.execute("""
                INSERT INTO button_configs 
                (menu_type, button_id, text, callback_data, row_position, column_position, sort_order, button_width, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, ("main_menu", button_id, text, callback_data, row_pos, col_pos, sort_order, button_width))
    

    if not menu_has_buttons("admin_menu"):
        admin_menu_buttons = [
            ("users", "👥 Пользователи", "admin_users", 0, 0, 0, 1),
            ("host_keys", "🌍 Ключи на хосте", "admin_host_keys", 0, 1, 1, 1),
            ("gift_key", "🎁 Выдать ключ", "admin_gift_key", 1, 0, 2, 1),
            ("promo", "🎟 Промокоды", "admin_promo_menu", 1, 1, 3, 1),
            ("speedtest", "⚡ Тест скорости", "admin_speedtest", 2, 0, 4, 1),
            ("monitor", "📊 Мониторинг", "admin_monitor", 2, 1, 5, 1),
            ("backup", "🗄 Бэкап БД", "admin_backup_db", 3, 0, 6, 1),
            ("restore", "♻️ Восстановить БД", "admin_restore_db", 3, 1, 7, 1),
            ("admins", "👮 Администраторы", "admin_admins_menu", 4, 0, 8, 1),
            ("broadcast", "📢 Рассылка", "start_broadcast", 4, 1, 9, 1),
            ("back_to_menu", "⬅️ Назад в меню", "back_to_main_menu", 5, 0, 10, 3),
        ]
        
        for button_id, text, callback_data, row_pos, col_pos, sort_order, button_width in admin_menu_buttons:
            cursor.execute("""
                INSERT INTO button_configs 
                (menu_type, button_id, text, callback_data, row_position, column_position, sort_order, button_width, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, ("admin_menu", button_id, text, callback_data, row_pos, col_pos, sort_order, button_width))
    

    if not menu_has_buttons("profile_menu"):
        profile_menu_buttons = [
            ("topup", "💳 Пополнить баланс", "top_up_start", 0, 0, 0, 2),
            ("referral", "🤝 Реферальная программа", "show_referral_program", 1, 0, 1, 2),
            ("howto", "🛠 Подключиться", "howto_vless", 2, 0, 2, 1),
            ("promo_uni", "🎁 Ввести промокод", "promo_uni", 2, 1, 3, 1),
            ("back_to_menu", "⬅️ Назад в меню", "back_to_main_menu", 3, 0, 4, 3),
        ]
        
        for button_id, text, callback_data, row_pos, col_pos, sort_order, button_width in profile_menu_buttons:
            cursor.execute("""
                INSERT INTO button_configs 
                (menu_type, button_id, text, callback_data, row_position, column_position, sort_order, button_width, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, ("profile_menu", button_id, text, callback_data, row_pos, col_pos, sort_order, button_width))
    

    if not menu_has_buttons("support_menu"):
        support_menu_buttons = [
            ("new_ticket", "✍️ Новое обращение", "support_new_ticket", 0, 0, 0, 1),
            ("my_tickets", "📨 Мои обращения", "support_my_tickets", 0, 1, 1, 1),
            ("external", "🆘 Внешняя поддержка", "support_external", 1, 0, 2, 2),
            ("back_to_menu", "⬅️ Назад в меню", "back_to_main_menu", 2, 0, 3, 2),
        ]
        
        for button_id, text, callback_data, row_pos, col_pos, sort_order, button_width in support_menu_buttons:
            cursor.execute("""
                INSERT INTO button_configs 
                (menu_type, button_id, text, callback_data, row_position, column_position, sort_order, button_width, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, ("support_menu", button_id, text, callback_data, row_pos, col_pos, sort_order, button_width))

    if not menu_has_buttons("key_info_menu"):
        key_info_menu_buttons = [
            ("connect", "📲 Подключиться", None, "{connection_string}", 0, 0, 0, 2),
            ("extend", "➕ Продлить ключ", "extend_key_{key_id}", None, 1, 0, 1, 2),
            ("key_devices", "📱 Устройства", "key_devices_{key_id}", None, 2, 0, 2, 1),
            ("qr", "📱 QR-код", "show_qr_{key_id}", None, 2, 1, 3, 1),
            ("howto", "📖 Инструкция", "howto_vless_{key_id}", None, 3, 0, 4, 1),
            ("comment_key", "📝 Комментарий", "key_comments_{key_id}", None, 3, 1, 5, 1),
            ("back", "⬅️ Назад к списку ключей", "manage_keys", None, 4, 0, 6, 2),
        ]

        for button_id, text, callback_data, url, row_pos, col_pos, sort_order, width in key_info_menu_buttons:
            cursor.execute("""
                INSERT INTO button_configs 
                (menu_type, button_id, text, callback_data, url, row_position, column_position, sort_order, button_width, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, ("key_info_menu", button_id, text, callback_data, url, row_pos, col_pos, sort_order, width))


# ==========================================


# ===== _ENSURE_SSH_TARGETS_TABLE =====
def _ensure_ssh_targets_table(cursor: sqlite3.Cursor) -> None:
    """Миграция: создать таблицу speedtest_ssh_targets при необходимости и добавить недостающие столбцы."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS speedtest_ssh_targets (
            target_name TEXT PRIMARY KEY,
            ssh_host TEXT NOT NULL,
            ssh_port INTEGER DEFAULT 22,
            ssh_user TEXT,
            ssh_password TEXT,
            ssh_key_path TEXT,
            description TEXT,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            metadata TEXT,
            time_auto TEXT DEFAULT '{}'
        )
    """)

    extras = {
        "ssh_host": "TEXT",
        "ssh_port": "INTEGER",
        "ssh_user": "TEXT",
        "ssh_password": "TEXT",
        "ssh_key_path": "TEXT",
        "description": "TEXT",
        "is_active": "INTEGER DEFAULT 1",
        "sort_order": "INTEGER DEFAULT 0",
        "metadata": "TEXT",
        "time_auto": "TEXT DEFAULT '{}'",
    }
    for column, definition in extras.items():
        _ensure_table_column(cursor, "speedtest_ssh_targets", column, definition)


# =====================================


# ===== _ENSURE_GIFT_TOKENS_TABLE =====
def _ensure_gift_tokens_table(cursor: sqlite3.Cursor) -> None:
    """Миграция для таблиц подарочных токенов."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_tokens (
            token TEXT PRIMARY KEY,
            host_name TEXT NOT NULL,
            days INTEGER NOT NULL,
            activation_limit INTEGER DEFAULT 1,
            activations_used INTEGER DEFAULT 0,
            expires_at TIMESTAMP,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_claimed_at TIMESTAMP,
            comment TEXT
        )
        """
    )
    _ensure_index(cursor, "idx_gift_tokens_host", "gift_tokens", "host_name")
    _ensure_index(cursor, "idx_gift_tokens_expires", "gift_tokens", "expires_at")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_token_claims (
            claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            key_id INTEGER,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(token) REFERENCES gift_tokens(token) ON DELETE CASCADE
        )
        """
    )
    _ensure_index(cursor, "idx_gift_token_claims_token", "gift_token_claims", "token")
    _ensure_index(cursor, "idx_gift_token_claims_user", "gift_token_claims", "user_id")


# =====================================


# ===== GET_USER_ID_BY_GIFT_TOKEN =====
def get_user_id_by_gift_token(token: str) -> int | None:
    row = _fetch_row("SELECT user_id FROM gift_token_claims WHERE token = ? ORDER BY claimed_at DESC LIMIT 1", (token,), f"Ошибка поиска user_id по токену {token}")
    return row["user_id"] if row else None
# =====================================


# ===== _ENSURE_SELLER_USERS_TABLE =====
def _ensure_seller_users_table(cursor: sqlite3.Cursor) -> None:
    """Миграция для таблицы seller_users."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seller_users (
            id_seller INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_sale REAL DEFAULT 0,
            seller_ref REAL DEFAULT 0,
            seller_uuid TEXT DEFAULT '0',
            user_id INTEGER UNIQUE
        )
    ''')
    
    mapping = {
        "seller_sale": "REAL DEFAULT 0",
        "seller_ref": "REAL DEFAULT 0",
        "seller_uuid": "TEXT DEFAULT '0'",
        "user_id": "INTEGER UNIQUE"
    }
    for column, definition in mapping.items():
        _ensure_table_column(cursor, "seller_users", column, definition)

    _ensure_unique_index(cursor, "idx_seller_users_user_id", "seller_users", "user_id")
# ====================================


# ===== _ENSURE_PROMO_TABLES =====
def _ensure_promo_tables(cursor: sqlite3.Cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            discount_percent REAL,
            discount_amount REAL,
            promo_type TEXT DEFAULT 'discount',
            reward_value INTEGER DEFAULT 0,
            usage_limit_total INTEGER,
            usage_limit_per_user INTEGER,
            used_total INTEGER DEFAULT 0,
            valid_from TIMESTAMP,
            valid_until TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        )
        """
    )
    
    mapping = {
        "promo_type": "TEXT DEFAULT 'discount'",
        "reward_value": "INTEGER DEFAULT 0"
    }
    for column, definition in mapping.items():
        _ensure_table_column(cursor, "promo_codes", column, definition)

    _ensure_index(cursor, "idx_promo_codes_valid", "promo_codes", "valid_until")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_code_usages (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            applied_amount REAL,
            order_id TEXT,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(code) REFERENCES promo_codes(code) ON DELETE CASCADE
        )
        """
    )
    _ensure_index(cursor, "idx_promo_code_usages_code", "promo_code_usages", "code")
    _ensure_index(cursor, "idx_promo_code_usages_user", "promo_code_usages", "user_id")


# =================================


# ===== _ENSURE_HOST_SPEEDTESTS_TABLE =====
def _ensure_host_speedtests_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS host_speedtests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT NOT NULL,
            method TEXT NOT NULL,
            ping_ms REAL,
            jitter_ms REAL,
            download_mbps REAL,
            upload_mbps REAL,
            server_name TEXT,
            server_id TEXT,
            ok INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_host_speedtests_host_time ON host_speedtests(host_name, created_at DESC)")


# =========================================


# ===== _ENSURE_RESOURCE_METRICS_TABLE =====
def _ensure_resource_metrics_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS resource_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,                -- 'local' | 'host' | 'target'
            object_name TEXT NOT NULL,          -- 'panel' | host_name | target_name
            cpu_percent REAL,
            mem_percent REAL,
            disk_percent REAL,
            load1 REAL,
            net_bytes_sent INTEGER,
            net_bytes_recv INTEGER,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_resource_metrics_scope_time ON resource_metrics(scope, object_name, created_at DESC)")




# ==========================================


# ===== INSERT_RESOURCE_METRIC =====
def insert_resource_metric(
    scope: str,
    object_name: str,
    cpu_percent: float | None = None,
    mem_percent: float | None = None,
    disk_percent: float | None = None,
    load1: float | None = None,
    net_bytes_sent: int | None = None,
    net_bytes_recv: int | None = None,
    raw_json: str | None = None
) -> int | None:
    cursor = _exec(
        """
        INSERT INTO resource_metrics (
            scope, object_name, cpu_percent, mem_percent, disk_percent, load1, 
            net_bytes_sent, net_bytes_recv, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (scope or '').strip(), (object_name or '').strip(),
            cpu_percent, mem_percent, disk_percent, load1, 
            net_bytes_sent, net_bytes_recv, raw_json
        ),
        f"Не удалось сохранить метрики ресурсов scope={scope} object={object_name}"
    )
    return cursor.lastrowid if cursor else None


# ==================================


# ===== GET_LATEST_RESOURCE_METRIC =====
def get_latest_resource_metric(scope: str, object_name: str) -> dict | None:
    return _fetch_row(
        """
        SELECT * FROM resource_metrics
        WHERE scope = ? AND object_name = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        ((scope or '').strip(), (object_name or '').strip()),
        f"Не удалось получить последние метрики ресурсов scope={scope} object={object_name}"
    )


# ======================================


# ===== GET_RESOURCE_METRICS =====
def get_resource_metrics(
    scope: str,
    object_name: str,
    limit: int = 20
) -> list[dict]:
    return _fetch_list(
        """
        SELECT *
        FROM resource_metrics
        WHERE scope = ? AND object_name = ?
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        ((scope or '').strip(), (object_name or '').strip(), limit),
        f"Не удалось получить метрики ресурсов scope={scope} object={object_name}"
    )


# ==============================


# ===== GET_METRICS_SERIES =====
def get_metrics_series(scope: str, object_name: str, *, since_hours: int = 24, limit: int = 500) -> list[dict]:
    if since_hours == 1:
        hours_filter = 2
    else:
        hours_filter = max(1, int(since_hours))
    
    rows = _fetch_list(
        f'''
        SELECT created_at, cpu_percent, mem_percent, disk_percent, load1
        FROM resource_metrics
        WHERE scope = ? AND object_name = ?
            AND created_at >= datetime('now', '+3 hours', ?)
        ORDER BY created_at ASC
        LIMIT ?
        ''',
        (
            (scope or '').strip(),
            (object_name or '').strip(),
            f'-{hours_filter} hours',
            max(10, int(limit)),
        ),
        f"Не удалось получить серию метрик для {scope}/{object_name}"
    )
    logging.debug(f"get_metrics_series: {scope}/{object_name}, since_hours={since_hours}, found {len(rows)} records")
    return rows




# ==============================


# ===== CREATE_HOST =====
def create_host(name: str, url: str, user: str, passwd: str, inbound: int, subscription_url: str | None = None):
    name = normalize_host_name(name)
    url = (url or "").strip()
    user = (user or "").strip()
    passwd = passwd or ""
    try:
        inbound = int(inbound)
    except Exception:
        pass
    subscription_url = (subscription_url or None)

    cursor = _exec(
         "INSERT INTO xui_hosts (host_name, host_url, host_username, host_pass, host_inbound_id, subscription_url) VALUES (?, ?, ?, ?, ?, ?)",
         (name, url, user, passwd, inbound, subscription_url),
         ""
    )
    if cursor:
        logging.info(f"Успешно создан новый хост: {name}")
        return

    cursor = _exec(
         "INSERT INTO xui_hosts (host_name, host_url, host_username, host_pass, host_inbound_id) VALUES (?, ?, ?, ?, ?)",
         (name, url, user, passwd, inbound),
         f"Ошибка при создании хоста '{name}'"
    )
    if cursor:
         logging.info(f"Успешно создан новый хост (fallback): {name}")

# =======================


# ===== UPDATE_HOST_SUBSCRIPTION_URL =====
def update_host_subscription_url(host_name: str, subscription_url: str | None) -> bool:
    host_name = normalize_host_name(host_name)
    cursor = _exec(
        "UPDATE xui_hosts SET subscription_url = ? WHERE TRIM(host_name) = TRIM(?)",
        (subscription_url, host_name),
        f"Не удалось обновить subscription_url для хоста '{host_name}'"
    )
    return _check_rowcount(cursor, f"хост '{host_name}'", "update_host_subscription_url")
# ========================================

# ===== UPDATE_HOST_DESCRIPTION =====

# ===== UPDATE_HOST_DESCRIPTION =====
# Обновление описания хоста
def update_host_description(host_name: str, description: str | None) -> bool:
    host_name = normalize_host_name(host_name)
    cursor = _exec(
        "UPDATE xui_hosts SET description = ? WHERE TRIM(host_name) = TRIM(?)",
        (description, host_name),
        f"Не удалось обновить описание для хоста '{host_name}'"
    )
    return _check_rowcount(cursor, f"хост '{host_name}'", "update_host_description")
# ===================================

# ===== UPDATE_HOST_TRAFFIC_SETTINGS =====

# ===== UPDATE_HOST_TRAFFIC_SETTINGS =====
# Обновление стратегии лимита трафика для хоста
# Default: 'NO_RESET'
def update_host_traffic_settings(host_name: str, traffic_strategy: str | None = 'NO_RESET') -> bool:
    host_name = normalize_host_name(host_name)
    cursor = _exec(
        "UPDATE xui_hosts SET default_traffic_strategy = ? WHERE TRIM(host_name) = TRIM(?)",
        (traffic_strategy or 'NO_RESET', host_name),
        f"Не удалось обновить настройки трафика для хоста '{host_name}'"
    )
    return _check_rowcount(cursor, f"хост '{host_name}'", "update_host_traffic_settings")
# ========================================


# ===== SET_REFERRAL_START_BONUS_RECEIVED =====
def set_referral_start_bonus_received(user_id: int) -> bool:
    return _check_rowcount(_exec(
        "UPDATE users SET referral_start_bonus_received = 1 WHERE telegram_id = ?",
        (user_id,),
        f"Не удалось установить бонус реферала для пользователя {user_id}"
    ), f"пользователь {user_id}", "")
# =============================================


# ===== UPDATE_HOST_URL =====
# Обновление URL хоста
def update_host_url(host_name: str, new_url: str) -> bool:
    host_name = normalize_host_name(host_name)
    cursor = _exec(
        "UPDATE xui_hosts SET host_url = ? WHERE TRIM(host_name) = TRIM(?)",
        (new_url, host_name),
        f"Не удалось обновить URL для хоста '{host_name}'"
    )
    return _check_rowcount(cursor, f"хост '{host_name}'", "update_host_url")
# ===========================


# ===== ADD_SELLER_USER =====
def add_seller_user(user_id: int, seller_sale: float = 0, seller_ref: float = 0, seller_uuid: str = "0") -> int | None:
    cursor = _exec(
        """
        INSERT OR REPLACE INTO seller_users (user_id, seller_sale, seller_ref, seller_uuid) 
        VALUES (?, ?, ?, ?)
        """,
        (user_id, seller_sale, seller_ref, str(seller_uuid)),
        "Не удалось добавить продавца"
    )
    return cursor.lastrowid if cursor else None

# =======================


# ===== GET_SELLER_USER =====
def get_seller_user(user_id: int) -> dict | None:
    row = _fetch_row("SELECT * FROM seller_users WHERE user_id = ?", (user_id,), f"Не удалось получить продавца {user_id}")
    if not row:
        return {
            "user_id": user_id,
            "seller_sale": 0.0,
            "seller_ref": 0.0,
            "seller_uuid": "0",
        }
    return row

# =======================


# ===== DELETE_SELLER_USER =====
def delete_seller_user(user_id: int) -> bool:
    cursor = _exec("DELETE FROM seller_users WHERE user_id = ?", (user_id,), f"Не удалось удалить продавца {user_id}")
    return cursor is not None

# ==========================


# ===== UPDATE_HOST_REMNAWAVE_SETTINGS =====
def update_host_remnawave_settings(
    host_name: str,
    *,
    remnawave_base_url: str | None = None,
    remnawave_api_token: str | None = None,
    squad_uuid: str | None = None,
) -> bool:
    host_name_n = normalize_host_name(host_name)
    row = _fetch_row("SELECT 1 FROM xui_hosts WHERE TRIM(host_name) = TRIM(?)", (host_name_n,), "")
    if not row:
        logging.warning(f"update_host_remnawave_settings: хост не найден '{host_name_n}'")
        return False

    sets: list[str] = []
    params: list[Any] = []
    if remnawave_base_url is not None:
        value = (remnawave_base_url or '').strip() or None
        sets.append("remnawave_base_url = ?")
        params.append(value)
    if remnawave_api_token is not None:
        value = (remnawave_api_token or '').strip() or None
        sets.append("remnawave_api_token = ?")
        params.append(value)
    if squad_uuid is not None:
        value = (squad_uuid or '').strip() or None
        sets.append("squad_uuid = ?")
        params.append(value)
    
    if not sets:
        return True
    
    params.append(host_name_n)
    sql = f"UPDATE xui_hosts SET {', '.join(sets)} WHERE TRIM(host_name) = TRIM(?)"
    cursor = _exec(sql, params, f"Не удалось обновить Remnawave-настройки для хоста '{host_name}'")
    return cursor is not None

# ========================================


# ===== UPDATE_HOST_SSH_SETTINGS =====
def update_host_ssh_settings(
    host_name: str,
    ssh_host: str | None = None,
    ssh_port: int | None = None,
    ssh_user: str | None = None,
    ssh_password: str | None = None,
    ssh_key_path: str | None = None,
) -> bool:
    host_name_n = normalize_host_name(host_name)
    row = _fetch_row("SELECT 1 FROM xui_hosts WHERE TRIM(host_name) = TRIM(?)", (host_name_n,), "")
    if not row:
        logging.warning(f"update_host_ssh_settings: хост не найден '{host_name_n}'")
        return False

    cursor = _exec(
        """
        UPDATE xui_hosts
        SET ssh_host = ?, ssh_port = ?, ssh_user = ?, ssh_password = ?, ssh_key_path = ?
        WHERE TRIM(host_name) = TRIM(?)
        """,
        (
            (ssh_host or None),
            (int(ssh_port) if ssh_port is not None else None),
            (ssh_user or None),
            (ssh_password if ssh_password is not None else None),
            (ssh_key_path or None),
            host_name_n,
        ),
        f"Не удалось обновить SSH-параметры для хоста '{host_name}'"
    )
    return cursor is not None
# ====================================


# ===== UPDATE_HOST_NAME =====
def update_host_name(old_name: str, new_name: str) -> bool:
    old_n = normalize_host_name(old_name)
    new_n = normalize_host_name(new_name)
    if not old_n or not new_n:
        return False
    if old_n == new_n:
        return True

    row = _fetch_row("SELECT 1 FROM xui_hosts WHERE TRIM(host_name) = TRIM(?)", (old_n,), "")
    if not row:
        logging.warning(f"update_host_name: исходный хост не найден '{old_n}'")
        return False

    row_new = _fetch_row("SELECT 1 FROM xui_hosts WHERE TRIM(host_name) = TRIM(?)", (new_n,), "")
    if row_new:
        logging.warning(f"update_host_name: новое имя занято '{new_n}'")
        return False

    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute(
                "UPDATE xui_hosts SET host_name = ? WHERE TRIM(host_name) = TRIM(?)",
                (new_n, old_n)
            )
            cursor.execute(
                "UPDATE plans SET host_name = ? WHERE TRIM(host_name) = TRIM(?)",
                (new_n, old_n)
            )
            cursor.execute(
                "UPDATE vpn_keys SET host_name = ? WHERE TRIM(host_name) = TRIM(?)",
                (new_n, old_n)
            )
            cursor.execute(
                "UPDATE host_speedtests SET host_name = ? WHERE TRIM(host_name) = TRIM(?)",
                (new_n, old_n)
            )
            conn.commit()
            cursor.execute("PRAGMA foreign_keys=ON")
            return True
    except sqlite3.Error as e: logging.error(f"Не удалось переименовать хост '{old_name}' -> '{new_name}': {e}"); return False

# ===== DELETE_HOST =====
# Удаление хоста и всех связанных тарифов
def delete_host(host_name: str):
    try:
        host_name = normalize_host_name(host_name)
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM plans WHERE TRIM(host_name) = TRIM(?)", (host_name,))
            cursor.execute("DELETE FROM xui_hosts WHERE TRIM(host_name) = TRIM(?)", (host_name,))
            conn.commit()
            logging.info(f"Хост '{host_name}' и его тарифы успешно удалены.")
    except sqlite3.Error as e: logging.error(f"Ошибка удаления хоста '{host_name}': {e}")
# =========================


# ===== GET_HOST =====
# Получение информации о хосте по имени
# Fallback: None если хост не найден
def get_host(host_name: str) -> dict | None:
    try:
        host_name = normalize_host_name(host_name)
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM xui_hosts WHERE TRIM(host_name) = TRIM(?)", (host_name,))
            result = cursor.fetchone(); return dict(result) if result else None
    except sqlite3.Error as e: logging.error(f"Ошибка получения хоста '{host_name}': {e}"); return None
# ==================


# ===== TOGGLE_HOST_VISIBILITY =====
# Переключение видимости хоста (поле see)
def toggle_host_visibility(host_name: str, visible: int) -> bool:
    host_name_n = normalize_host_name(host_name)
    visible_int = 1 if visible else 0
    row = _fetch_row("SELECT 1 FROM xui_hosts WHERE TRIM(host_name) = TRIM(?)", (host_name_n,), "")
    if not row: logging.warning(f"toggle_host_visibility: хост не найден '{host_name_n}'"); return False
    cursor = _exec(
        "UPDATE xui_hosts SET see = ? WHERE TRIM(host_name) = TRIM(?)",
        (visible_int, host_name_n),
        f"Не удалось обновить видимость для хоста '{host_name}'"
    )
    if cursor and cursor.rowcount > 0: logging.info(f"Видимость хоста '{host_name_n}' обновлена: see={visible_int}")
    return _check_rowcount(cursor, f"хост '{host_name_n}'", "")
# ==================================


def get_device_tiers(host_name: str) -> list[dict]:
    return _fetch_list("SELECT * FROM device_tiers WHERE TRIM(host_name)=TRIM(?) ORDER BY sort_order, device_count", (host_name,))

def add_device_tier(host_name: str, device_count: int, price: float) -> int | None:
    r = _exec("INSERT OR REPLACE INTO device_tiers (host_name, device_count, price) VALUES (?,?,?)", (host_name, device_count, price))
    return r.lastrowid if r else None

def update_device_tier(tier_id: int, device_count: int, price: float) -> bool:
    r = _exec("UPDATE device_tiers SET device_count=?, price=? WHERE tier_id=?", (device_count, price, tier_id))
    return r is not None and r.rowcount > 0

def delete_device_tier(tier_id: int) -> bool:
    r = _exec("DELETE FROM device_tiers WHERE tier_id=?", (tier_id,))
    return r is not None and r.rowcount > 0

def get_device_tier_by_id(tier_id: int) -> dict | None:
    return _fetch_row("SELECT * FROM device_tiers WHERE tier_id=?", (tier_id,))

def update_host_device_mode(host_name: str, mode: str) -> bool:
    r = _exec("UPDATE xui_hosts SET device_mode=? WHERE TRIM(host_name)=TRIM(?)", (mode, host_name))
    return r is not None and r.rowcount > 0


# ===== DELETE_KEY_BY_ID =====
# Удаление ключа по key_id
def delete_key_by_id(key_id: int) -> bool:
    return _check_rowcount(_exec("DELETE FROM vpn_keys WHERE key_id = ?", (key_id,), f"Не удалось удалить ключ по id {key_id}"), f"ключ {key_id}", "")
# ============================


# ===== UPDATE_KEY_COMMENT =====
# Обновление комментария (description) для ключа
def update_key_comment(key_id: int, comment: str) -> bool:
    return _check_rowcount(_exec("UPDATE vpn_keys SET description = ? WHERE key_id = ?", (comment, key_id), f"Не удалось обновить комментарий ключа для {key_id}"), f"ключ {key_id}", "")
# ==============================


# ===== GET_ALL_HOSTS =====
def get_all_hosts(visible_only: bool = False) -> list[dict]:
    # Сначала пытаемся выполнить запрос
    sql = "SELECT * FROM xui_hosts ORDER BY sort_order ASC, host_name ASC"
    if visible_only: sql = "SELECT * FROM xui_hosts WHERE see = 1 ORDER BY sort_order ASC, host_name ASC"
    
    rows = _fetch_list(sql, (), "")
    if not rows:
        # Если пусто или ошибка, возможно нет колонки see (хотя миграция должна была сработать)
        # Пробуем через старый механизм fallback только если реально была ошибка
        # Но у нас _fetch_list возвращает [], так что сложно отличить "пусто" от "ошибка".
        # Однако, раз мы строго следим за миграциями, колонка see должна быть.
        # Если ошибка была, она залогировалась в _fetch_list.
        pass

    result = []
    for row in rows:
        d = dict(row)
        d['host_name'] = normalize_host_name(d.get('host_name'))
        result.append(d)
    return result

# =========================


# ===== GET_SPEEDTESTS =====
def get_speedtests(host_name: str, limit: int = 20) -> list[dict]:
    host_name_n = normalize_host_name(host_name)
    try:
        limit_int = int(limit)
    except Exception: limit_int = 20
        
    return _fetch_list(
        """
        SELECT id, host_name, method, ping_ms, jitter_ms, download_mbps, upload_mbps,
               server_name, server_id, ok, error, created_at
        FROM host_speedtests
        WHERE TRIM(host_name) = TRIM(?)
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (host_name_n, limit_int),
        f"Не удалось получить speedtest-данные для хоста '{host_name}'"
    )

# ========================


# ===== GET_LATEST_SPEEDTEST =====
def get_latest_speedtest(host_name: str) -> dict | None:
    host_name_n = normalize_host_name(host_name)
    return _fetch_row(
        """
        SELECT id, host_name, method, ping_ms, jitter_ms, download_mbps, upload_mbps,
               server_name, server_id, ok, error, created_at
        FROM host_speedtests
        WHERE TRIM(host_name) = TRIM(?)
        ORDER BY datetime(created_at) DESC
        LIMIT 1
        """,
        (host_name_n,),
        f"Не удалось получить последний speedtest для хоста '{host_name}'"
    )

# ===== INSERT_HOST_SPEEDTEST =====
def insert_host_speedtest(
    host_name: str,
    method: str,
    ping_ms: float | None = None,
    jitter_ms: float | None = None,
    download_mbps: float | None = None,
    upload_mbps: float | None = None,
    server_name: str | None = None,
    server_id: str | None = None,
    ok: bool = True,
    error: str | None = None
) -> int | None:
    host_name_n = normalize_host_name(host_name)
    cursor = _exec(
        """
        INSERT INTO host_speedtests (host_name, method, ping_ms, jitter_ms, download_mbps, upload_mbps, server_name, server_id, ok, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (host_name_n, method, ping_ms, jitter_ms, download_mbps, upload_mbps, server_name, server_id, 1 if ok else 0, error),
        f"Не удалось сохранить запись speedtest для '{host_name}'"
    )
    return cursor.lastrowid if cursor else None






# ===== GET_ALL_SSH_TARGETS =====
def get_all_ssh_targets() -> list[dict]:
    return _fetch_list("SELECT * FROM speedtest_ssh_targets ORDER BY sort_order ASC, target_name ASC", (), "Не удалось получить список SSH-целей")


# ===========================


# ===== GET_SSH_TARGET =====
def get_ssh_target(target_name: str) -> dict | None:
    name = normalize_host_name(target_name)
    return _fetch_row("SELECT * FROM speedtest_ssh_targets WHERE TRIM(target_name) = TRIM(?)", (name,), f"Не удалось получить SSH-цель '{target_name}'")


# ========================


# ===== CREATE_SSH_TARGET =====
# Создание новой SSH-цели для speedtest
def create_ssh_target(
    target_name: str,
    ssh_host: str,
    ssh_port: int | None = 22,
    ssh_user: str | None = None,
    ssh_password: str | None = None,
    ssh_key_path: str | None = None,
    description: str | None = None,
    *,
    sort_order: int | None = 0,
    is_active: int | None = 1,
) -> bool:
    name = normalize_host_name(target_name)
    cursor = _exec(
        """
        INSERT INTO speedtest_ssh_targets
            (target_name, ssh_host, ssh_port, ssh_user, ssh_password, ssh_key_path, description, is_active, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            (ssh_host or '').strip(),
            int(ssh_port) if ssh_port is not None else None,
            (ssh_user or None),
            (ssh_password if ssh_password is not None else None),
            (ssh_key_path or None),
            (description or None),
            1 if (is_active is None or int(is_active) != 0) else 0,
            int(sort_order or 0),
        ),
        f"Не удалось создать SSH-цель '{target_name}'"
    )
    return cursor is not None
# ===========================


# ===== UPDATE_SSH_TARGET_FIELDS =====
# Обновление полей SSH-цели (выборочное обновление)
# Параметры с None не обновляются
def update_ssh_target_fields(
    target_name: str,
    *,
    ssh_host: str | None = None,
    ssh_port: int | None = None,
    ssh_user: str | None = None,
    ssh_password: str | None = None,
    ssh_key_path: str | None = None,
    description: str | None = None,
    sort_order: int | None = None,
    is_active: int | None = None,
) -> bool:
    name = normalize_host_name(target_name)
    row = _fetch_row("SELECT 1 FROM speedtest_ssh_targets WHERE TRIM(target_name) = TRIM(?)", (name,), "")
    if not row: logging.warning(f"update_ssh_target_fields: цель не найдена '{name}'"); return False
        
    sets: list[str] = []
    params: list[Any] = []
    if ssh_host is not None:
        sets.append("ssh_host = ?")
        params.append((ssh_host or '').strip())
    if ssh_port is not None:
        try:
            val = int(ssh_port)
        except Exception:
            val = None
        sets.append("ssh_port = ?")
        params.append(val)
    if ssh_user is not None:
        sets.append("ssh_user = ?")
        params.append(ssh_user or None)
    if ssh_password is not None:
        sets.append("ssh_password = ?")
        params.append(ssh_password)
    if ssh_key_path is not None:
        sets.append("ssh_key_path = ?")
        params.append(ssh_key_path or None)
    if description is not None:
        sets.append("description = ?")
        params.append(description or None)
    if sort_order is not None:
        try:
            so = int(sort_order)
        except Exception:
            so = 0
        sets.append("sort_order = ?")
        params.append(so)
    if is_active is not None:
        sets.append("is_active = ?")
        params.append(1 if int(is_active) != 0 else 0)
    
    if not sets: return True
    
    params.append(name)
    sql = f"UPDATE speedtest_ssh_targets SET {', '.join(sets)} WHERE TRIM(target_name) = TRIM(?)"
    cursor = _exec(sql, params, f"Не удалось обновить SSH-цель '{target_name}'")
    return cursor is not None








# ===== DELETE_SSH_TARGET =====
# Удаление SSH-цели по имени
def delete_ssh_target(target_name: str) -> bool:
    return _check_rowcount(_exec(
        "DELETE FROM speedtest_ssh_targets WHERE TRIM(target_name) = TRIM(?)",
        (normalize_host_name(target_name),),
        f"Не удалось удалить SSH-цель '{target_name}'"
    ), f"SSH-цель '{target_name}'", "")
# =============================


# ===== RENAME_SSH_TARGET =====
# Переименование SSH-цели с обновлением связанных speedtest-записей
def rename_ssh_target(old_target_name: str, new_target_name: str) -> bool:
    old_name = normalize_host_name(old_target_name)
    new_name = normalize_host_name(new_target_name)
    
    if old_name == new_name: return True
    
    row = _fetch_row("SELECT 1 FROM speedtest_ssh_targets WHERE TRIM(target_name) = TRIM(?)", (old_name,), "")
    if not row: logging.warning(f"rename_ssh_target: старая цель не найдена '{old_name}'"); return False
    
    row_new = _fetch_row("SELECT 1 FROM speedtest_ssh_targets WHERE TRIM(target_name) = TRIM(?)", (new_name,), "")
    if row_new: logging.warning(f"rename_ssh_target: новое имя уже занято '{new_name}'"); return False
    
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE speedtest_ssh_targets SET target_name = ? WHERE TRIM(target_name) = TRIM(?)",
                (new_name, old_name)
            )
            cursor.execute(
                "UPDATE host_speedtests SET host_name = ? WHERE TRIM(host_name) = TRIM(?)",
                (new_name, old_name)
            )
            conn.commit()
            logging.info(f"SSH-цель переименована: '{old_name}' → '{new_name}'")
            return True
    except sqlite3.Error as e: logging.error(f"Не удалось переименовать SSH-цель '{old_target_name}' → '{new_target_name}': {e}"); return False



# ===== GET_ADMIN_STATS =====
# Получение статистики для админ-панели
# Возвращает: total_users, total_keys, active_keys, total_income, today_new_users, today_income, today_issued_keys
def get_admin_stats() -> dict:
    stats = {}
    stats["total_users"] = _get_count_stat("SELECT COUNT(*) as c FROM users")
    stats["total_keys"] = _get_count_stat("SELECT COUNT(*) as c FROM vpn_keys")
    stats["active_keys"] = _get_count_stat("SELECT COUNT(*) as c FROM vpn_keys WHERE expire_at IS NOT NULL AND datetime(expire_at) > CURRENT_TIMESTAMP")
    stats["total_income"] = float(_get_count_stat("""
        SELECT COALESCE(SUM(amount_rub), 0) as s FROM transactions
        WHERE status IN ('paid','success','succeeded') AND LOWER(COALESCE(payment_method, '')) <> 'balance'
    """))
    stats["today_new_users"] = _get_count_stat("SELECT COUNT(*) as c FROM users WHERE date(registration_date) = date('now', '+3 hours')")
    stats["today_income"] = float(_get_count_stat("""
        SELECT COALESCE(SUM(amount_rub), 0) as s FROM transactions
        WHERE status IN ('paid','success','succeeded') AND date(created_date) = date('now', '+3 hours') 
          AND LOWER(COALESCE(payment_method, '')) <> 'balance'
    """))
    stats["today_issued_keys"] = _get_count_stat("SELECT COUNT(*) as c FROM vpn_keys WHERE date(COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)) = date('now', '+3 hours')")
    return stats
# =======================

# ===== GET_ALL_KEYS =====
# Получение всех ключей из БД с нормализацией
def get_all_keys() -> list[dict]:
    rows = _fetch_list("SELECT * FROM vpn_keys ORDER BY key_id DESC", (), "Не удалось получить все ключи")
    return [_normalize_key_row(row) for row in rows]
# =========================


# ===== GET_KEYS_FOR_USER =====
def get_keys_for_user(user_id: int) -> list[dict]:
    return get_user_keys(user_id)

# =============================


# ===== UPDATE_KEY_EMAIL =====
def update_key_email(key_id: int, new_email: str) -> bool:
    normalized = _normalize_email(new_email) or new_email.strip()
    return update_key_fields(key_id, email=normalized)

# ============================


# ===== UPDATE_KEY_HOST =====
def update_key_host(key_id: int, new_host_name: str) -> bool:
    return update_key_fields(key_id, host_name=new_host_name)

# ===========================


# ===== CREATE_GIFT_KEY =====
def create_gift_key(user_id: int, host_name: str, key_email: str, months: int, remnawave_user_uuid: str | None = None) -> int | None:
    try:
        from datetime import timedelta

        months_value = max(1, int(months or 1))
        expiry_dt = get_msk_time() + timedelta(days=30 * months_value)
        expiry_ms = int(expiry_dt.timestamp() * 1000)
        uuid_value = remnawave_user_uuid or f"GIFT-{user_id}-{int(get_msk_time().timestamp())}"
        return add_new_key(
            user_id=user_id,
            host_name=host_name,
            remnawave_user_uuid=uuid_value,
            key_email=key_email,
            expiry_timestamp_ms=expiry_ms,
        )
    except Exception as e:
        logging.error(f"Не удалось создать подарочный ключ для пользователя {user_id}: {e}")
        return None
# ===========================


# ===== GET_SETTING =====
def get_setting(key: str, default: str | None = None) -> str | None:
    row = _fetch_row("SELECT value FROM bot_settings WHERE key = ?", (key,), f"Не удалось получить настройку '{key}'")
    return row["value"] if row else default

# =======================


# ===== DEVICE LABELS (локальные подписи устройств) =====
def get_device_labels(user_uuid: str) -> dict[str, str]:
    """Возвращает словарь {hwid: label} с пользовательскими подписями устройств."""
    if not user_uuid:
        return {}
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT hwid, label FROM device_labels WHERE user_uuid = ?", (user_uuid,))
            return {row["hwid"]: (row["label"] or "") for row in cursor.fetchall()}
    except Exception as e:
        logging.error(f"Не удалось получить подписи устройств для {user_uuid}: {e}")
        return {}


def set_device_label(user_uuid: str, hwid: str, label: str | None) -> bool:
    """Создаёт/обновляет подпись устройства. Пустая подпись удаляет запись."""
    if not user_uuid or not hwid:
        return False
    label = (label or "").strip()
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            cursor = conn.cursor()
            if not label:
                cursor.execute("DELETE FROM device_labels WHERE user_uuid = ? AND hwid = ?", (user_uuid, hwid))
            else:
                cursor.execute(
                    """
                    INSERT INTO device_labels (user_uuid, hwid, label, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_uuid, hwid) DO UPDATE SET label = excluded.label, updated_at = CURRENT_TIMESTAMP
                    """,
                    (user_uuid, hwid, label),
                )
            conn.commit()
        return True
    except Exception as e:
        logging.error(f"Не удалось сохранить подпись устройства {hwid} для {user_uuid}: {e}")
        return False


def delete_device_label(user_uuid: str, hwid: str) -> bool:
    """Удаляет подпись устройства (например, при удалении устройства)."""
    if not user_uuid or not hwid:
        return False
    try:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM device_labels WHERE user_uuid = ? AND hwid = ?", (user_uuid, hwid))
            conn.commit()
        return True
    except Exception as e:
        logging.error(f"Не удалось удалить подпись устройства {hwid} для {user_uuid}: {e}")
        return False

# =======================


# ===== GET_ADMIN_IDS =====
def get_admin_ids() -> set[int]:
    ids: set[int] = set()
    try:
        single = get_setting("admin_telegram_id")
        if single:
            try:
                ids.add(int(single))
            except Exception:
                pass
        multi_raw = get_setting("admin_telegram_ids")
        if multi_raw:
            s = (multi_raw or "").strip()

            try:
                arr = json.loads(s)
                if isinstance(arr, list):
                    for v in arr:
                        try:
                            ids.add(int(v))
                        except Exception:
                            pass
                    return ids
            except Exception:
                pass

            parts = [p for p in re.split(r"[\s,]+", s) if p]
            for p in parts:
                try:
                    ids.add(int(p))
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"Ошибка get_admin_ids: {e}")
    return ids
# =========================


# ===== IS_ADMIN =====
def is_admin(user_id: int) -> bool:
    try:
        return int(user_id) in get_admin_ids()
    except Exception: return False


# ====================


# ===== CREATE_PAYLOAD_PENDING =====
def create_payload_pending(payment_id: str, user_id: int, amount_rub: float | None, metadata: dict | None) -> bool:
    print(f"[DEBUG] create_payload_pending called: payment_id={payment_id}, user_id={user_id}, amount_rub={amount_rub}, metadata={metadata}")
    cursor = _exec(
        """
        INSERT OR REPLACE INTO pending_transactions (payment_id, user_id, amount_rub, metadata, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, COALESCE((SELECT status FROM pending_transactions WHERE payment_id = ?), 'pending'),
                COALESCE((SELECT created_at FROM pending_transactions WHERE payment_id = ?), CURRENT_TIMESTAMP), CURRENT_TIMESTAMP)
        """,
        (payment_id, int(user_id), float(amount_rub) if amount_rub is not None else None, json.dumps(metadata or {}), payment_id, payment_id),
        f"Не удалось создать ожидающую транзакцию {payment_id}"
    )
    return cursor is not None
# ==============================


# ===== _GET_PENDING_METADATA =====
def _get_pending_metadata(payment_id: str) -> dict | None:
    row = _fetch_row("SELECT * FROM pending_transactions WHERE payment_id = ?", (payment_id,), f"Не удалось прочитать ожидающую транзакцию {payment_id}")
    if not row: return None
    try:
        meta = json.loads(row["metadata"] or "{}")
    except Exception: meta = {}

    meta.setdefault('payment_id', payment_id)
    return meta
# =================================


# ===== GET_PENDING_METADATA =====
def get_pending_metadata(payment_id: str) -> dict | None:
    return _get_pending_metadata(payment_id)


# ================================


# ===== GET_PENDING_STATUS =====
def get_pending_status(payment_id: str) -> str | None:
    row = _fetch_row("SELECT status FROM pending_transactions WHERE payment_id = ?", (payment_id,), f"Не удалось получить статус для ожидающей {payment_id}")
    return (row["status"] or '').strip() or None if row else None

# ==============================


# ===== _COMPLETE_PENDING =====
def _complete_pending(payment_id: str) -> bool:
    cursor = _exec(
        "UPDATE pending_transactions SET status = 'paid', updated_at = CURRENT_TIMESTAMP WHERE payment_id = ? AND status != 'paid'",
        (payment_id,),
        f"Не удалось завершить ожидающую транзакцию {payment_id}"
    )
    return cursor is not None and cursor.rowcount > 0
# ===========================


# ===== FIND_AND_COMPLETE_PENDING_TRANSACTION =====
def find_and_complete_pending_transaction(payment_id: str) -> dict | None:
    logging.info(f"🔍 Ищем ожидающую транзакцию: {payment_id}")
    meta = _get_pending_metadata(payment_id)
    if not meta: logging.warning(f"❌ Ожидающая транзакция не найдена: {payment_id}"); return None
    
    user_id = meta.get('user_id', 'неизвестно')
    amount = meta.get('price', 0)
    logging.info(f"✅ Найдена ожидающая транзакция: пользователь {user_id}, сумма {amount:.2f} RUB")
    
    success = _complete_pending(payment_id)
    if success:
        logging.info(f"✅ Транзакция отмечена как оплаченная: {payment_id}")
        return meta
    else:
        logging.warning(f"⚠️ Транзакция {payment_id} уже была оплачена или заблокирована (дубликат вебхука)")
        return None
# =================================================


# ===== GET_LATEST_PENDING_FOR_USER =====
def get_latest_pending_for_user(user_id: int) -> dict | None:
    row = _fetch_row(
        """
        SELECT payment_id, metadata FROM pending_transactions
        WHERE user_id = ? AND status = 'pending'
        ORDER BY datetime(created_at) DESC, datetime(updated_at) DESC
        LIMIT 1
        """,
        (int(user_id),),
        f"Не удалось получить последнюю ожидающую для пользователя {user_id}"
    )
    if not row:
        return None
    try:
        meta = json.loads(row["metadata"] or "{}")
    except Exception:
        meta = {}
    meta.setdefault('payment_id', row["payment_id"])
    return meta
# =======================================


# ===== РЕ-ЭНГЕЙДЖМЕНТ (воронка): лог, дедуп, лимиты, optout, кандидаты =====

def log_notification(user_id: int, campaign: str, touch: int = 0) -> None:
    """Отметить, что касание campaign/touch отправлено юзеру (для дедупа и лимитов)."""
    _exec(
        "INSERT INTO notification_log (user_id, campaign, touch) VALUES (?, ?, ?)",
        (int(user_id), str(campaign), int(touch)),
        f"notification_log insert для {user_id}/{campaign}/{touch}",
    )


def was_notification_sent(user_id: int, campaign: str, touch: int = 0) -> bool:
    row = _fetch_row(
        "SELECT 1 FROM notification_log WHERE user_id = ? AND campaign = ? AND touch = ? LIMIT 1",
        (int(user_id), str(campaign), int(touch)),
        "notification_log check",
    )
    return bool(row)


def count_notifications_since(user_id: int, hours: int) -> int:
    """Сколько ре-энгейджмент-оповещений ушло юзеру за последние N часов (для лимита)."""
    row = _fetch_row(
        f"SELECT COUNT(*) AS c FROM notification_log WHERE user_id = ? "
        f"AND datetime(sent_at) > datetime('now', '-{int(hours)} hours')",
        (int(user_id),),
        "notification_log count",
    )
    return int(row["c"]) if row else 0


def set_reengage_optout(user_id: int, value: int = 1) -> None:
    _exec("UPDATE users SET reengage_optout = ? WHERE telegram_id = ?",
          (1 if value else 0, int(user_id)), f"reengage_optout для {user_id}")


def _paid_tx_cond(alias: str = "t") -> str:
    return f"LOWER(COALESCE({alias}.status, '')) IN ('paid', 'completed', 'success', 'succeeded')"


def get_reengage_abandoned_pending(min_minutes: int, max_minutes: int) -> list[dict]:
    """Кандидаты «брошенная оплата»: счёт создан min..max минут назад, всё ещё pending,
    юзер не отписан/не забанен и НЕ докупил (нет свежей оплаченной транзакции после счёта)."""
    rows = _fetch_list(
        f"""
        SELECT p.payment_id, p.user_id, p.metadata, p.created_at
        FROM pending_transactions p
        JOIN users u ON u.telegram_id = p.user_id
        WHERE p.status = 'pending'
          AND COALESCE(u.reengage_optout, 0) = 0
          AND COALESCE(u.is_banned, 0) = 0
          AND datetime(p.created_at) <= datetime('now', '-{int(min_minutes)} minutes')
          AND datetime(p.created_at) >  datetime('now', '-{int(max_minutes)} minutes')
          AND NOT EXISTS (
              SELECT 1 FROM transactions t
              WHERE t.user_id = p.user_id AND {_paid_tx_cond('t')}
                AND datetime(t.created_date) >= datetime(p.created_at)
          )
        """,
        (), "reengage: abandoned pending",
    )
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        out.append(d)
    return out


def get_reengage_entered_no_trial(min_hours: int, max_hours: int) -> list[dict]:
    """Кандидаты «зашёл, не взял тест»: зарегистрирован min..max часов назад, триал не брал,
    ничего не купил, ключей нет, не отписан/не забанен."""
    return _fetch_list(
        f"""
        SELECT u.telegram_id AS user_id, u.username
        FROM users u
        WHERE COALESCE(u.trial_used, 0) = 0
          AND COALESCE(u.reengage_optout, 0) = 0
          AND COALESCE(u.is_banned, 0) = 0
          AND datetime(u.registration_date) <= datetime('now', '-{int(min_hours)} hours')
          AND datetime(u.registration_date) >  datetime('now', '-{int(max_hours)} hours')
          AND NOT EXISTS (SELECT 1 FROM vpn_keys k WHERE k.user_id = u.telegram_id)
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.user_id = u.telegram_id AND {_paid_tx_cond('t')})
        """,
        (), "reengage: entered no trial",
    )


def get_reengage_winback(min_days: int, max_days: int) -> list[dict]:
    """Кандидаты win-back: покупали (есть оплаченная транзакция), сейчас нет активного
    НЕ-триального ключа, и последний платный ключ истёк min..max дней назад."""
    return _fetch_list(
        f"""
        SELECT u.telegram_id AS user_id, u.username,
               (SELECT MAX(k2.expire_at) FROM vpn_keys k2
                 WHERE k2.user_id = u.telegram_id AND NOT ({_dashboard_trial_key_predicate('k2')})) AS last_expire
        FROM users u
        WHERE COALESCE(u.reengage_optout, 0) = 0
          AND COALESCE(u.is_banned, 0) = 0
          AND EXISTS (SELECT 1 FROM transactions t WHERE t.user_id = u.telegram_id AND {_paid_tx_cond('t')})
          AND NOT EXISTS (
              SELECT 1 FROM vpn_keys k WHERE k.user_id = u.telegram_id
                AND NOT ({_dashboard_trial_key_predicate('k')})
                AND (k.expire_at IS NULL OR k.expire_at > datetime('now', '+3 hours'))
          )
          AND (SELECT MAX(k2.expire_at) FROM vpn_keys k2
                WHERE k2.user_id = u.telegram_id AND NOT ({_dashboard_trial_key_predicate('k2')}))
              BETWEEN datetime('now', '-{int(max_days)} days') AND datetime('now', '-{int(min_days)} days')
        """,
        (), "reengage: winback",
    )
# =======================================================================


# ===== GET_PROMO_USAGES_FOR_USER =====
def get_promo_usages_for_user(user_id: int, limit: int = 20) -> list[dict]:
    rows = _fetch_list(
        """
        SELECT code, applied_amount, order_id, used_at
        FROM promo_code_usages
        WHERE user_id = ?
        ORDER BY used_at DESC
        LIMIT ?
        """,
        (user_id, max(1, int(limit))),
        f"Не удалось получить промокоды пользователя {user_id}",
    )
    return [dict(r) for r in rows]
# =====================================


# ===== GET_TRANSACTIONS_FOR_USER =====
def get_purchased_hwid_limit_hint(user_id: int, host_name: str | None = None) -> int | None:
    """Лимит устройств из metadata последней успешной оплаты (tier_device_count)."""
    host_norm = (host_name or "").strip()
    txs = get_transactions_for_user(user_id, limit=25)
    for t in txs:
        status = (t.get("status") or "").lower()
        if status not in ("paid", "completed", "success", "succeeded"):
            continue
        raw = t.get("metadata")
        if not raw:
            continue
        try:
            meta = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        th = (meta.get("host_name") or meta.get("host") or "").strip()
        if host_norm and th and th != host_norm:
            continue
        td = meta.get("tier_device_count") or meta.get("device_count") or meta.get("hwid_limit")
        if td is not None:
            try:
                val = int(td)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                pass
    return None


# ===== GET_TRANSACTIONS_FOR_USER =====
def get_transactions_for_user(user_id: int, limit: int = 15) -> list[dict]:
    rows = _fetch_list(
        """
        SELECT payment_id, status, amount_rub, payment_method, metadata, created_date
        FROM transactions
        WHERE user_id = ?
        ORDER BY created_date DESC
        LIMIT ?
        """,
        (user_id, max(1, int(limit))),
        f"Не удалось получить транзакции пользователя {user_id}",
    )
    return [dict(r) for r in rows]
# =====================================


# ===== GET_REFERRALS_FOR_USER =====
def get_referrals_for_user(user_id: int) -> list[dict]:
    rows = _fetch_list(
        """
        SELECT telegram_id, username, registration_date, total_spent
        FROM users
        WHERE referred_by = ?
        ORDER BY registration_date DESC
        """,
        (user_id,),
        f"Не удалось получить рефералов для пользователя {user_id}"
    )
    return [dict(r) for r in rows]
# ====================================


# ===== GET_ALL_SETTINGS =====
def get_all_settings() -> dict:
    rows = _fetch_list("SELECT key, value FROM bot_settings", (), "Не удалось получить все настройки")
    return {row['key']: row['value'] for row in rows}

# ============================


# ===== UPDATE_SETTING =====
def update_setting(key: str, value: str):
    cursor = _exec(
        "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
        (key, value),
        f"Не удалось обновить настройку '{key}'"
    )
    if cursor: logging.info(f"Настройка '{key}' обновлена.")
# ==========================


# ===== GET_BUTTON_CONFIGS =====
def get_button_configs(menu_type: str, include_inactive: bool = False) -> list[dict]:
    query = """
        SELECT * FROM button_configs 
        WHERE menu_type = ? 
        ORDER BY sort_order, row_position, column_position
    """
    if not include_inactive:
        query = """
            SELECT * FROM button_configs 
            WHERE menu_type = ? AND is_active = 1 
            ORDER BY sort_order, row_position, column_position
        """
        
    rows = _fetch_list(query, (menu_type,), f"Не удалось получить конфиг кнопок для {menu_type}")
    
    if not rows and menu_type in ("main_menu", "admin_menu", "profile_menu", "support_menu", "key_info_menu"):
        try:
            count = _get_count_stat("SELECT COUNT(*) as c FROM button_configs")
            if count == 0:
                with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
                    cursor = conn.cursor()
                    _ensure_default_button_configs(cursor)
                    conn.commit()
                rows = _fetch_list(query, (menu_type,), f"Не удалось получить конфиг кнопок для {menu_type}")
        except Exception as e:
            logging.error(f"Не удалось инициализировать дефолтные кнопки: {e}")
            
    return [dict(r) for r in rows]
# ============================


# ===== GET_BUTTON_CONFIG =====
def get_button_config(menu_type: str, button_id: str) -> dict | None:
    row = _fetch_row(
        """
        SELECT * FROM button_configs 
        WHERE menu_type = ? AND button_id = ?
        """,
        (menu_type, button_id),
        f"Не удалось получить конфиг кнопки {menu_type}/{button_id}"
    )
    return dict(row) if row else None

# =============================


# ===== CREATE_BUTTON_CONFIG =====
def create_button_config(menu_type: str, button_id: str, text: str, callback_data: str = None, 
                        url: str = None, row_position: int = 0, column_position: int = 0, 
                        button_width: int = 1, metadata: str = None, 
                        button_color: str = None, emoji_id: str = None) -> bool:
    cursor = _exec(
        """
        INSERT OR REPLACE INTO button_configs 
        (menu_type, button_id, text, callback_data, url, row_position, column_position, button_width, metadata, button_color, emoji_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (menu_type, button_id, text, callback_data, url, row_position, column_position, button_width, metadata, button_color, emoji_id),
        "Не удалось создать конфиг кнопки"
    )
    if cursor: logging.info(f"Конфиг кнопки создан: {menu_type}/{button_id}"); return True
    return False
# ================================


# ===== UPDATE_BUTTON_CONFIG =====
def update_button_config(button_id: int, text: str = None, callback_data: str = None, 
                        url: str = None, row_position: int = None, column_position: int = None, 
                        button_width: int = None, is_active: bool = None, sort_order: int = None, 
                        metadata: str = None, button_color: str = None, emoji_id: str = None) -> bool:
    logging.info(f"update_button_config called for {button_id}: text={text}, callback_data={callback_data}, url={url}, row={row_position}, col={column_position}, active={is_active}, sort={sort_order}")
    
    updates = []
    params = []
    
    if text is not None:
        updates.append("text = ?")
        params.append(text)
    if callback_data is not None:
        updates.append("callback_data = ?")
        params.append(callback_data)
    if url is not None:
        updates.append("url = ?")
        params.append(url)
    if row_position is not None:
        updates.append("row_position = ?")
        params.append(row_position)
    if column_position is not None:
        updates.append("column_position = ?")
        params.append(column_position)
    if button_width is not None:
        updates.append("button_width = ?")
        params.append(button_width)
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if is_active else 0)
    if sort_order is not None:
        updates.append("sort_order = ?")
        params.append(sort_order)
    if metadata is not None:
        updates.append("metadata = ?")
        params.append(metadata)
    if button_color is not None:
        updates.append("button_color = ?")
        params.append(button_color if button_color else None)
    if emoji_id is not None:
        updates.append("emoji_id = ?")
        params.append(emoji_id if emoji_id else None)
    
    if not updates: return True
        
    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(button_id)
    
    query = f"UPDATE button_configs SET {', '.join(updates)} WHERE id = ?"
    cursor = _exec(query, params, f"Не удалось обновить конфиг кнопки {button_id}")
    
    if cursor and cursor.rowcount > 0: logging.info(f"Конфиг кнопки {button_id} успешно обновлён"); return True
    if cursor and cursor.rowcount == 0: logging.warning(f"Кнопка с id {button_id} не найдена")
    return False
# ================================


# ===== REORDER_BUTTON_CONFIGS =====
def reorder_button_configs(menu_type: str, button_orders: list[dict]) -> bool:
    try:
        logging.info(f"Reordering {len(button_orders)} buttons for {menu_type}")
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            for order_data in button_orders:
                button_id = order_data.get('button_id')
                sort_order = order_data.get('sort_order', 0)
                row_position = order_data.get('row_position', 0)
                column_position = order_data.get('column_position', 0)
                button_width = order_data.get('button_width', None)
                is_active = order_data.get('is_active')
                
                set_clauses = [
                    "sort_order = ?",
                    "row_position = ?",
                    "column_position = ?",
                    "updated_at = CURRENT_TIMESTAMP"
                ]
                query_params = [sort_order, row_position, column_position]

                if button_width is not None:
                    set_clauses.insert(3, "button_width = ?")
                    query_params.insert(3, int(button_width))
                
                if is_active is not None:
                    set_clauses.insert(len(set_clauses)-1, "is_active = ?")
                    query_params.insert(len(query_params), 1 if is_active else 0)

                query_params.append(menu_type)
                query_params.append(button_id)

                cursor.execute(
                    f"""
                    UPDATE button_configs 
                    SET {', '.join(set_clauses)}
                    WHERE menu_type = ? AND button_id = ?
                    """,
                    query_params,
                )
            conn.commit()
            return True
    except sqlite3.Error as e:
        logging.error(f"Failed to reorder button configs for {menu_type}: {e}")
        return False
# ==================================


# ===== UPDATE_EXISTING_MY_KEYS_BUTTON =====
def update_existing_my_keys_button():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE button_configs SET button_id = 'my_keys' WHERE button_id = 'keys'")
            conn.commit()
            return True
    except sqlite3.Error:
        return False
# ==========================================


# ===== DELETE_BUTTON_CONFIG =====
def delete_button_config(button_id: int) -> bool:
    cursor = _exec("DELETE FROM button_configs WHERE id = ?", (button_id,), f"Не удалось удалить конфиг кнопки {button_id}")
    if cursor: logging.info(f"Конфиг кнопки {button_id} удалён"); return True
    return False
# ================================



# ===== CREATE_PLAN =====
def create_plan(host_name: str, plan_name: str, months: int, price: float, hwid_limit: int = 0, traffic_limit_gb: int = 0, button_style: str = None, icon_emoji_id: str = None):
    host_name = normalize_host_name(host_name)
    cursor = _exec(
        "INSERT INTO plans (host_name, plan_name, months, price, hwid_limit, traffic_limit_gb, button_style, icon_emoji_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (host_name, plan_name, months, price, hwid_limit, traffic_limit_gb, button_style or None, icon_emoji_id or None),
        f"Не удалось создать тариф для хоста '{host_name}'"
    )
    if cursor: new_id = cursor.lastrowid; logging.info(f"Created new plan '{plan_name}' for host '{host_name}' with HWID={hwid_limit}, Traffic={traffic_limit_gb}GB."); return new_id
    return None
# =======================


# ===== GET_PLANS_FOR_HOST =====
def get_plans_for_host(host_name: str) -> list[dict]:
    host_name = normalize_host_name(host_name)
    rows = _fetch_list("SELECT * FROM plans WHERE TRIM(host_name) = TRIM(?) ORDER BY months", (host_name,), f"Не удалось получить тарифы для хоста '{host_name}'")
    return [dict(plan) for plan in rows]

# ==============================


# ===== GET_PLAN_BY_ID =====
def get_plan_by_id(plan_id: int) -> dict | None:
    row = _fetch_row("SELECT * FROM plans WHERE plan_id = ?", (plan_id,), f"Не удалось получить тариф по id '{plan_id}'")
    return dict(row) if row else None

# ==========================


# ===== DELETE_PLAN =====
def delete_plan(plan_id: int):
    cursor = _exec("DELETE FROM plans WHERE plan_id = ?", (plan_id,), f"Не удалось удалить тариф с id {plan_id}")
    if cursor: logging.info(f"Удалён тариф с id {plan_id}.")
# =======================


# ===== UPDATE_PLAN =====
def update_plan(plan_id: int, plan_name: str, months: int, price: float, hwid_limit: int = 0, traffic_limit_gb: int = 0, button_style: str = None, icon_emoji_id: str = None) -> bool:
    cursor = _exec(
        "UPDATE plans SET plan_name = ?, months = ?, price = ?, hwid_limit = ?, traffic_limit_gb = ?, button_style = ?, icon_emoji_id = ? WHERE plan_id = ?",
        (plan_name, months, price, hwid_limit, traffic_limit_gb, button_style or None, icon_emoji_id or None, plan_id),
        f"Не удалось обновить тариф {plan_id}"
    )
    if cursor and cursor.rowcount > 0: logging.info(f"Updated plan {plan_id}: name='{plan_name}', months={months}, price={price}, hwid={hwid_limit}, traffic={traffic_limit_gb}."); return True
    if cursor and cursor.rowcount == 0: logging.warning(f"No plan updated for id {plan_id} (not found).")
    return False


def update_host_button_style(host_name: str, button_style: str = None, icon_emoji_id: str = None) -> bool:
    host_name = normalize_host_name(host_name)
    cursor = _exec(
        "UPDATE xui_hosts SET button_style = ?, icon_emoji_id = ? WHERE TRIM(host_name) = TRIM(?)",
        (button_style or None, icon_emoji_id or None, host_name),
        f"Не удалось обновить стиль кнопки для хоста '{host_name}'"
    )
    if cursor and cursor.rowcount > 0: logging.info(f"Updated button style for host '{host_name}': style={button_style}, emoji={icon_emoji_id}"); return True
    return False
# =======================


def register_user_if_not_exists(telegram_id: int, username: str, referrer_id):
    row = _fetch_row("SELECT referred_by FROM users WHERE telegram_id = ?", (telegram_id,), "")
    
    if not row:
        _exec(
            "INSERT INTO users (telegram_id, username, registration_date, referred_by) VALUES (?, ?, ?, ?)",
            (telegram_id, username, get_msk_time().replace(tzinfo=None).replace(microsecond=0), referrer_id),
            f"Не удалось зарегистрировать пользователя {telegram_id}"
        )
    else:
        _exec("UPDATE users SET username = ? WHERE telegram_id = ?", (username, telegram_id), "")
        
        current_ref = row['referred_by']
        if referrer_id and (current_ref is None or str(current_ref).strip() == "") and int(referrer_id) != int(telegram_id):
            _exec("UPDATE users SET referred_by = ? WHERE telegram_id = ?", (int(referrer_id), telegram_id), "")

def add_to_referral_balance(user_id: int, amount: float):
    _exec("UPDATE users SET referral_balance = referral_balance + ? WHERE telegram_id = ?", (amount, user_id), f"Не удалось добавить реферальный баланс для пользователя {user_id}")

def set_referral_balance(user_id: int, value: float):
    _exec("UPDATE users SET referral_balance = ? WHERE telegram_id = ?", (value, user_id), f"Не удалось установить реферальный баланс для пользователя {user_id}")

def set_referral_balance_all(user_id: int, value: float):
    _exec("UPDATE users SET referral_balance_all = ? WHERE telegram_id = ?", (value, user_id), f"Не удалось установить общий реф-баланс для пользователя {user_id}")

def add_to_referral_balance_all(user_id: int, amount: float):
    _exec(
        "UPDATE users SET referral_balance_all = referral_balance_all + ? WHERE telegram_id = ?",
        (amount, user_id),
        f"Не удалось добавить к общему реф-балансу для пользователя {user_id}"
    )

def get_referral_balance_all(user_id: int) -> float:
    row = _fetch_row("SELECT referral_balance_all FROM users WHERE telegram_id = ?", (user_id,), f"Не удалось получить общий реф-баланс для пользователя {user_id}")
    return row["referral_balance_all"] if row else 0.0

def get_referral_balance(user_id: int) -> float:
    row = _fetch_row("SELECT referral_balance FROM users WHERE telegram_id = ?", (user_id,), f"Не удалось получить реф-баланс для пользователя {user_id}")
    return row["referral_balance"] if row else 0.0

def get_balance(user_id: int) -> float:
    row = _fetch_row("SELECT balance FROM users WHERE telegram_id = ?", (user_id,), f"Не удалось получить баланс для пользователя {user_id}")
    return row["balance"] if row else 0.0

def adjust_user_balance(user_id: int, delta: float) -> bool:
    cursor = _exec(
        "UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE telegram_id = ?",
        (float(delta), user_id),
        f"Не удалось изменить баланс для пользователя {user_id}"
    )
    return cursor is not None and cursor.rowcount > 0

def set_balance(user_id: int, value: float) -> bool:
    cursor = _exec(
        "UPDATE users SET balance = ? WHERE telegram_id = ?",
        (value, user_id),
        f"Не удалось установить баланс для пользователя {user_id}"
    )
    return cursor is not None and cursor.rowcount > 0

def add_to_balance(user_id: int, amount: float) -> bool:
    logging.info(f"💳 Добавляем {amount:.2f} RUB к балансу пользователя {user_id}")
    
    # Check if user exists first to match original logic logging
    row = _fetch_row("SELECT telegram_id, balance FROM users WHERE telegram_id = ?", (int(user_id),), "")
    if not row: logging.error(f"❌ Пользователь {user_id} не найден в базе данных"); return False

    old_balance = row["balance"] or 0.0
    
    cursor = _exec(
        "UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE telegram_id = ?",
        (float(amount), int(user_id)),
        f"Ошибка базы данных при пополнении баланса для пользователя {user_id}"
    )
    
    if cursor and cursor.rowcount > 0:
        new_balance = old_balance + float(amount)
        logging.info(f"✅ Баланс обновлен: пользователь {user_id} | {old_balance:.2f} → {new_balance:.2f} RUB (+{amount:.2f})")
        return True
    
    logging.error(f"❌ Не удалось обновить баланс для пользователя {user_id}: строки не затронуты")
    return False

def deduct_from_balance(user_id: int, amount: float) -> bool:
    if amount <= 0: return True
        
    cursor = _exec(
        "UPDATE users SET balance = balance - ? WHERE telegram_id = ? AND balance >= ?",
        (amount, user_id, amount),
        f"Не удалось списать с баланса для пользователя {user_id}"
    )
    if cursor and cursor.rowcount > 0: return True
        
    return False
# ============================


# ===== DEDUCT_FROM_REFERRAL_BALANCE =====
def deduct_from_referral_balance(user_id: int, amount: float) -> bool:
    if amount <= 0: return True
    cursor = _exec(
        "UPDATE users SET referral_balance = referral_balance - ? WHERE telegram_id = ? AND referral_balance >= ?",
        (amount, user_id, amount),
        f"Не удалось списать с реферального баланса для пользователя {user_id}"
    )
    if cursor and cursor.rowcount > 0: return True
    return False
# ======================================


# ===== GET_REFERRAL_COUNT =====
def get_referral_count(user_id: int) -> int:
    row = _fetch_row("SELECT COUNT(*) as c FROM users WHERE referred_by = ?", (user_id,), f"Не удалось получить кол-во рефералов для пользователя {user_id}")
    return row["c"] if row else 0
# ==============================


# ===== REFERRAL_TERMS — единые условия двусторонней рефералки =====
def referral_terms() -> dict:
    """Человеко-читаемые условия рефералки для показа юзеру ОДИНАКОВО в боте, мини-аппе и анонсе.
    Считаются из настроек (тип награды рефереру + скидка приглашённому)."""
    def _num(key, default):
        try:
            return float(str(get_setting(key) or default).replace(",", "."))
        except Exception:
            return float(default)
    enabled = str(get_setting("enable_referrals") or "true").strip().lower() not in ("false", "0", "no", "off")
    rtype = (get_setting("referral_reward_type") or "percent_purchase").strip()
    percent = _num("referral_percentage", 10)
    fixed = _num("fixed_referral_bonus_amount", 50)
    start_amt = _num("referral_on_start_referrer_amount", 20)
    discount = _num("referral_discount", 0)
    if rtype == "fixed_purchase":
        you = f"{fixed:.0f} ₽ на баланс за каждую покупку друга"
    elif rtype == "fixed_start_referrer":
        you = f"{start_amt:.0f} ₽ на баланс, когда друг запустит бота"
    else:
        you = f"{percent:.0f}% на баланс с каждой покупки друга"
    friend = f"скидку {discount:.0f}% на первую подписку" if discount > 0 else "быстрый доступ к VPN"
    return {"enabled": enabled, "you": you, "friend": friend,
            "percent": percent, "discount": discount, "reward_type": rtype}
# ==============================


# ===== GET_USER =====
def get_user(telegram_id: int):
    row = _fetch_row("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,), f"Не удалось получить пользователя {telegram_id}")
    return dict(row) if row else None
# ==================

# ===== GET_USER_BY_EMAIL =====
def get_user_by_email(email: str):
    row = _fetch_row("SELECT * FROM users WHERE LOWER(auth_email) = ?", (email.lower().strip(),), f"Не удалось получить пользователя {email}")
    return dict(row) if row else None
# ==================

# ===== PASSWORD HASHING (pbkdf2_sha256, stdlib — без внешних зависимостей) =====
_PW_ALGO = "pbkdf2_sha256"
_PW_ITERS = 200_000


def hash_password(password: str) -> str:
    """Self-describing хеш: pbkdf2_sha256$iters$salt_hex$hash_hex."""
    import hashlib, os
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, _PW_ITERS)
    return f"{_PW_ALGO}${_PW_ITERS}${salt.hex()}${dk.hex()}"


def is_legacy_password(stored: str) -> bool:
    """True, если пароль хранится в старом открытом виде (не хеширован)."""
    return bool(stored) and not str(stored).startswith(_PW_ALGO + "$")


def verify_password(password: str, stored: str) -> bool:
    """Проверяет пароль против хеша ИЛИ против легаси-открытого значения (для миграции)."""
    import hashlib, hmac
    if not stored:
        return False
    stored = str(stored)
    if stored.startswith(_PW_ALGO + "$"):
        try:
            _algo, iters, salt_hex, hash_hex = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(iters))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False
    # Легаси: пароль лежит открытым текстом (до внедрения хеширования) — разрешаем вход один раз,
    # вызывающий код перехеширует при успешной проверке.
    return hmac.compare_digest(stored, password or "")
# =================================


# ===== CREATE_USER_BY_EMAIL =====
def create_user_by_email(email: str, password_hash: str) -> dict | None:
    import random
    while True:
        telegram_id = int(f"999{random.randint(1000000, 9999999)}")
        if not get_user(telegram_id):
            break
            
    cursor = _exec(
        "INSERT INTO users (telegram_id, username, registration_date, auth_email, auth_pass) VALUES (?, ?, ?, ?, ?)",
        (telegram_id, "", get_msk_time().replace(tzinfo=None).replace(microsecond=0), email.strip(), password_hash),
        f"Не удалось зарегистрировать пользователя {email}"
    )
    if cursor:
        return get_user(telegram_id)
    return None
# =================================

# ===== UPDATE_USER_PASSWORD =====
def update_user_password(email: str, new_password_hash: str) -> bool:
    cursor = _exec("UPDATE users SET auth_pass = ? WHERE LOWER(auth_email) = ?", (new_password_hash, email.lower().strip()), f"Не удалось обновить пароль для {email}")
    return cursor is not None and cursor.rowcount > 0
# =================================

# ===== UPDATE_USER_RECOVERY_CODE =====
def update_user_recovery_code(telegram_id: int, code_hash: str) -> bool:
    cursor = _exec("UPDATE users SET recovery_code = ? WHERE telegram_id = ?", (code_hash, telegram_id), f"Не удалось сохранить код восстановления {telegram_id}")
    return cursor is not None and cursor.rowcount > 0
# =================================

# ===== UPDATE_USER_AUTH_TOKEN =====
def update_user_auth_token(telegram_id: int, token: str) -> bool:
    cursor = _exec("UPDATE users SET auth_token = ? WHERE telegram_id = ?", (token, telegram_id), f"Не удалось обновить токен {telegram_id}")
    return cursor is not None and cursor.rowcount > 0
# ==================================

# ===== LINK_TELEGRAM_TO_EMAIL_USER =====
def link_telegram_to_email_user(old_telegram_id: int, new_telegram_id: int, new_username: str):
    old_user = get_user(old_telegram_id)
    if not old_user:
        return "Ошибка: веб-аккаунт не найден."

    existing = get_user(new_telegram_id)
        
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            if existing:
                cursor.execute("UPDATE vpn_keys SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE transactions SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE pending_transactions SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE support_tickets SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE seller_users SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE users SET referred_by = ? WHERE referred_by = ?", (new_telegram_id, old_telegram_id))
                
                old_bal = old_user.get('balance', 0)
                old_ref_bal = old_user.get('referral_balance', 0)
                old_ref_all = old_user.get('referral_balance_all', 0)
                old_spent = old_user.get('total_spent', 0)
                old_months = old_user.get('total_months', 0)
                
                cursor.execute("""
                    UPDATE users 
                    SET balance = balance + ?, 
                        referral_balance = referral_balance + ?,
                        referral_balance_all = referral_balance_all + ?,
                        total_spent = total_spent + ?,
                        total_months = total_months + ?,
                        auth_email = ?,
                        auth_pass = ?,
                        auth_token = ?
                    WHERE telegram_id = ?
                """, (old_bal, old_ref_bal, old_ref_all, old_spent, old_months, 
                      old_user.get('auth_email'), old_user.get('auth_pass'), old_user.get('auth_token'),
                      new_telegram_id))
                
                cursor.execute("DELETE FROM users WHERE telegram_id = ?", (old_telegram_id,))
            else:
                cursor.execute("UPDATE users SET telegram_id = ?, username = ? WHERE telegram_id = ?", (new_telegram_id, new_username, old_telegram_id))
                cursor.execute("UPDATE vpn_keys SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE transactions SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE pending_transactions SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE support_tickets SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE seller_users SET user_id = ? WHERE user_id = ?", (new_telegram_id, old_telegram_id))
                cursor.execute("UPDATE users SET referred_by = ? WHERE referred_by = ?", (new_telegram_id, old_telegram_id))
            
            conn.commit()
            return True
    except sqlite3.Error as e:
        logging.error(f"Failed to link telegram {new_telegram_id} to {old_telegram_id}: {e}")
        return "Ошибка базы данных."
# =======================================


# ===== GET_TRANSACTION =====
def get_transaction(payment_id: str) -> dict | None:
    row = _fetch_row("SELECT * FROM transactions WHERE payment_id = ?", (payment_id,), f"Не удалось получить транзакцию {payment_id}")
    return dict(row) if row else None
# =========================


# ===== SET_TERMS_AGREED =====
def set_terms_agreed(telegram_id: int):
    cursor = _exec("UPDATE users SET agreed_to_terms = 1 WHERE telegram_id = ?", (telegram_id,), f"Не удалось установить согласие с условиями для пользователя {telegram_id}")
    if cursor: logging.info(f"Пользователь {telegram_id} согласился с условиями.")
# ==========================


# ===== UPDATE_USER_STATS =====
def update_user_stats(telegram_id: int, amount_spent: float, months_purchased: int):
    _exec("UPDATE users SET total_spent = total_spent + ?, total_months = total_months + ? WHERE telegram_id = ?", (amount_spent, months_purchased, telegram_id), f"Не удалось обновить статистику пользователя {telegram_id}")
# ===========================


# ===== DASHBOARD PERIODS =====
DASHBOARD_PERIOD_MONTHS_BACK: dict[str, int] = {
    "1m": 0,
    "2m": 1,
    "3m": 2,
    "6m": 5,
    "12m": 11,
}

DASHBOARD_PERIOD_LABELS: dict[str, str] = {
    "1m": "с 1-го числа месяца",
    "2m": "2 месяца (с 1-го)",
    "3m": "3 месяца (с 1-го)",
    "6m": "полгода (с 1-го)",
    "12m": "год (с 1-го)",
    "all": "Всё время",
    "7d": "7 дней",
}


def normalize_dashboard_period(period: str | None) -> str:
    key = (period or "1m").strip().lower()
    if key in DASHBOARD_PERIOD_LABELS or key == "all":
        return key
    return "1m"


def resolve_dashboard_period_days(period: str | None) -> int:
    """Legacy helper: 0 = all time, else approximate day window for charts."""
    key = normalize_dashboard_period(period)
    if key == "all":
        return 0
    if key == "7d":
        return 7
    months = DASHBOARD_PERIOD_MONTHS_BACK.get(key)
    if months is None:
        return 30
    return (months + 1) * 31


def get_dashboard_period_label(period: str | None) -> str:
    key = normalize_dashboard_period(period)
    return DASHBOARD_PERIOD_LABELS.get(key, DASHBOARD_PERIOD_LABELS["1m"])


def _dashboard_period_since_sql(column: str, period: str | None) -> tuple[str, tuple]:
    """Filter from 1st day of month (MSK +3h). 'all' = no filter."""
    key = normalize_dashboard_period(period)
    if key == "all":
        return "", ()
    if key == "7d":
        return f" AND {column} >= datetime('now', '+3 hours', '-7 days')", ()
    months_back = DASHBOARD_PERIOD_MONTHS_BACK.get(key, 0)
    if months_back <= 0:
        return f" AND date({column}) >= date('now', '+3 hours', 'start of month')", ()
    return (
        f" AND date({column}) >= date('now', '+3 hours', 'start of month', '-{months_back} months')",
        (),
    )


def _dashboard_trial_key_predicate(alias: str = "k") -> str:
    """Only SHM-imported trials (SERVICE_ID in description), not local trial_* keys."""
    return (
        f"(COALESCE({alias}.tag, '') = 'TRIAL' "
        f"AND COALESCE({alias}.description, '') LIKE '%SERVICE_ID:%')"
    )


def _dashboard_shm_key_predicate(alias: str = "k") -> str:
    p = f"{alias}." if alias else ""
    return (
        f"(COALESCE({p}description, '') LIKE '%[ACTIVE]%' "
        f"AND COALESCE({p}description, '') LIKE '%SERVICE_ID:%')"
    )


def _tx_paid_where(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return (
        f"LOWER(COALESCE({p}status, '')) IN ('paid', 'completed', 'success', 'succeeded')"
    )


def _tx_shm_auto_pay_where(alias: str = "") -> str:
    """Как выручка в SHM «Аналитика»: авто-шлюзы, без manual/admin."""
    p = f"{alias}." if alias else ""
    return (
        f"{_tx_paid_where(p)} AND LOWER(COALESCE({p}payment_method, '')) NOT IN "
        f"('balance', 'admin', 'referral', 'manual')"
    )


def _tx_manual_pay_where(alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return (
        f"{_tx_paid_where(p)} AND LOWER(COALESCE({p}payment_method, '')) IN ('manual', 'admin')"
    )


def get_users_total_count() -> int:
    row = _fetch_row("SELECT COUNT(*) as c FROM users", (), "users total")
    return int(row["c"]) if row else 0


def get_shm_revenue_sum(period: str | int = "1m") -> float:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    since, params = _dashboard_period_since_sql("created_date", str(period))
    row = _fetch_row(
        f"""
        SELECT COALESCE(SUM(amount_rub), 0.0) as s
        FROM transactions
        WHERE {_tx_shm_auto_pay_where()}
        {since}
        """,
        params,
        "shm revenue sum",
    )
    return float(row["s"]) if row else 0.0


def get_shm_payment_count(period: str | int = "1m") -> int:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    since, params = _dashboard_period_since_sql("created_date", str(period))
    row = _fetch_row(
        f"""
        SELECT COUNT(*) as c
        FROM transactions
        WHERE {_tx_shm_auto_pay_where()}
        {since}
        """,
        params,
        "shm payment count",
    )
    return int(row["c"]) if row else 0


def get_manual_revenue_sum(period: str | int = "1m") -> float:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    since, params = _dashboard_period_since_sql("created_date", str(period))
    row = _fetch_row(
        f"""
        SELECT COALESCE(SUM(amount_rub), 0.0) as s
        FROM transactions
        WHERE {_tx_manual_pay_where()}
        {since}
        """,
        params,
        "manual revenue",
    )
    return float(row["s"]) if row else 0.0


def get_manual_payment_count(period: str | int = "1m") -> int:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    since, params = _dashboard_period_since_sql("created_date", str(period))
    row = _fetch_row(
        f"""
        SELECT COUNT(*) as c FROM transactions
        WHERE {_tx_manual_pay_where()}
        {since}
        """,
        params,
        "manual payment count",
    )
    return int(row["c"]) if row else 0


def get_paying_clients_count(period: str | int = "1m") -> int:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    since, params = _dashboard_period_since_sql("created_date", str(period))
    row = _fetch_row(
        f"""
        SELECT COUNT(DISTINCT user_id) as c
        FROM transactions
        WHERE {_tx_shm_auto_pay_where()}
        {since}
        """,
        params,
        "paying clients",
    )
    return int(row["c"]) if row else 0


def get_active_services_now_count() -> int:
    row = _fetch_row(
        f"""
        SELECT COUNT(*) as c FROM vpn_keys
        WHERE {_dashboard_shm_key_predicate('')}
        """,
        (),
        "active services now",
    )
    return int(row["c"]) if row else 0


def get_profit_last_7_days() -> float:
    """Как SHM «Обзор»: авто-платежи за 7 суток (без manual)."""
    row = _fetch_row(
        f"""
        SELECT COALESCE(SUM(amount_rub), 0.0) as s
        FROM transactions
        WHERE {_tx_shm_auto_pay_where()}
          AND created_date >= datetime('now', '+3 hours', '-7 days', '+2 hours')
        """,
        (),
        "profit 7d shm",
    )
    return float(row["s"]) if row else 0.0


def get_profit_7d_payment_count() -> int:
    row = _fetch_row(
        f"""
        SELECT COUNT(*) as c FROM transactions
        WHERE {_tx_shm_auto_pay_where()}
          AND created_date >= datetime('now', '+3 hours', '-7 days', '+2 hours')
        """,
        (),
        "profit 7d count",
    )
    return int(row["c"]) if row else 0


def get_dashboard_metric_hints(period: str | None = "1m") -> dict[str, str]:
    period = normalize_dashboard_period(period)
    plabel = get_dashboard_period_label(period)
    return {
        "users_total": "Все зарегистрированные пользователи в базе бота.",
        "users_period": f"Новые регистрации за период ({plabel}).",
        "revenue_shm": f"Авто-платежи: YooKassa, CryptoPay и др., без ручных начислений ({plabel}).",
        "revenue_manual": f"Ручные и админ-начисления за период ({plabel}), в основную выручку не входят.",
        "services_active": "Активные подписки (статус ACTIVE) на текущий момент.",
        "services_new": f"Новые подписки, созданные за период ({plabel}).",
        "paying_clients": f"Уникальные клиенты с успешной авто-оплатой за период ({plabel}).",
        "profit_7d": "Сумма авто-платежей за последние 7 суток (не зависит от выбранного периода).",
    }


def get_dashboard_text_digest(period: str | None = "1m") -> dict:
    """Daily breakdown from 1st of month (readable text report)."""
    key = normalize_dashboard_period(period)
    since_users, _ = _dashboard_period_since_sql("registration_date", key)
    since_keys, _ = _dashboard_period_since_sql(
        "COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)", key
    )
    since_tx, tx_params = _dashboard_period_since_sql("created_date", key)

    user_rows = _fetch_list(
        f"""
        SELECT date(registration_date) as d, COUNT(*) as c
        FROM users WHERE 1=1{since_users}
        GROUP BY d ORDER BY d
        """,
        (),
        "digest users",
    )
    key_rows = _fetch_list(
        f"""
        SELECT date(COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)) as d, COUNT(*) as c
        FROM vpn_keys
        WHERE {_dashboard_shm_key_predicate('')}{since_keys}
        GROUP BY d ORDER BY d
        """,
        (),
        "digest keys",
    )
    income_rows = _fetch_list(
        f"""
        SELECT date(created_date) as d, COALESCE(SUM(amount_rub), 0.0) as s
        FROM transactions
        WHERE {_tx_shm_auto_pay_where()}
        {since_tx}
        GROUP BY d ORDER BY d
        """,
        tx_params,
        "digest income",
    )

    by_date: dict[str, dict] = {}
    for src, field in ((user_rows, "users"), (key_rows, "keys"), (income_rows, "income")):
        for row in src:
            d = row.get("d") or ""
            if not d:
                continue
            slot = by_date.setdefault(d, {"users": 0, "keys": 0, "income": 0.0})
            if field == "income":
                slot[field] = float(row.get("s") or 0.0)
            else:
                slot[field] = int(row.get("c") or 0)

    rows = []
    totals = {"users": 0, "keys": 0, "income": 0.0}
    for d in sorted(by_date.keys()):
        item = by_date[d]
        parts = d.split("-")
        label = f"{parts[2]}.{parts[1]}" if len(parts) == 3 else d
        rows.append({
            "date": label,
            "iso": d,
            "users": item["users"],
            "keys": item["keys"],
            "income": round(item["income"], 2),
        })
        totals["users"] += item["users"]
        totals["keys"] += item["keys"]
        totals["income"] += item["income"]
    totals["income"] = round(totals["income"], 2)

    period_start = rows[0]["iso"] if rows else None
    return {
        "period": key,
        "period_label": get_dashboard_period_label(key),
        "from": period_start,
        "rows": rows,
        "totals": totals,
    }


# ===== GET_USER_COUNT =====
def get_user_count(period: str | int = "all") -> int:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    since, params = _dashboard_period_since_sql("registration_date", str(period))
    row = _fetch_row(
        f"SELECT COUNT(*) as c FROM users WHERE 1=1{since}",
        params,
        "Не удалось получить кол-во пользователей",
    )
    return row["c"] if row else 0
# ========================


# ===== GET_TOTAL_KEYS_COUNT =====
def get_total_keys_count(period: str | int = "all") -> int:
    return get_shm_keys_count(period)


def get_shm_keys_count(period: str | int = "all") -> int:
    """ACTIVE SHM subscriptions (imported keys), optionally filtered by created date."""
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    since, params = _dashboard_period_since_sql(
        "COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)", str(period)
    )
    row = _fetch_row(
        f"""
        SELECT COUNT(*) as c FROM vpn_keys
        WHERE {_dashboard_shm_key_predicate('')}
        {since}
        """,
        params,
        "Не удалось получить кол-во SHM-ключей",
    )
    return row["c"] if row else 0
# ==============================


# ===== GET_TOTAL_SPENT_SUM =====
def get_total_spent_sum(period: str | int = "all") -> float:
    """Основная выручка на дашборде = как SHM (без manual)."""
    return get_shm_revenue_sum(period)
# =============================


# ===== GET_TOTAL_SPENT_BY_METHOD =====
def get_total_spent_by_method(payment_method: str, period: str | int = "all") -> float:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    method_norm = (payment_method or '').strip().lower()
    method_aliases = {
        'platega': ('platega', 'platega payform', 'platega crypto'),
        'ton connect': ('ton connect', 'ton'),
    }
    methods = method_aliases.get(method_norm, (method_norm,))
    placeholders = ','.join('?' for _ in methods)
    since, since_params = _dashboard_period_since_sql("created_date", str(period))
    val = _fetch_val(
        f"""
        SELECT COALESCE(SUM(amount_rub), 0.0)
        FROM transactions
        WHERE LOWER(COALESCE(status, '')) IN ('paid', 'completed', 'success', 'succeeded')
          AND LOWER(COALESCE(payment_method, '')) IN ({placeholders})
          AND LOWER(COALESCE(payment_method, '')) NOT IN ('balance', 'admin', 'referral')
        {since}
        """,
        (*methods, *since_params),
        0.0,
        f"Не удалось получить доход по методу {payment_method}",
    )
    return float(val) if val is not None else 0.0
# ===================================


# ===== GET_TODAY_INCOME_BY_CURRENCY =====
def get_today_income_by_currency() -> dict:
    rub_methods = ('yookassa', 'platega', 'platega payform')
    crypto_methods = ('telegram stars', 'cryptobot', 'heleket', 'ton connect', 'platega crypto')
    rub = _fetch_val(
        f"""
        SELECT COALESCE(SUM(amount_rub), 0.0)
        FROM transactions
        WHERE LOWER(COALESCE(status, '')) IN ('paid', 'completed', 'success')
          AND date(created_date) = date('now', '+3 hours')
          AND LOWER(COALESCE(payment_method, '')) IN ({','.join('?' for _ in rub_methods)})
        """,
        rub_methods, 0.0, "Не удалось получить рублёвый доход за сегодня"
    )
    crypto = _fetch_val(
        f"""
        SELECT COALESCE(SUM(amount_rub), 0.0)
        FROM transactions
        WHERE LOWER(COALESCE(status, '')) IN ('paid', 'completed', 'success')
          AND date(created_date) = date('now', '+3 hours')
          AND LOWER(COALESCE(payment_method, '')) IN ({','.join('?' for _ in crypto_methods)})
        """,
        crypto_methods, 0.0, "Не удалось получить крипто доход за сегодня"
    )
    return {"rub": float(rub or 0), "crypto": float(crypto or 0)}
# ========================================


# ===== CREATE_PENDING_TRANSACTION =====
def create_pending_transaction(payment_id: str, user_id: int, amount_rub: float, metadata: dict) -> int:
    cursor = _exec(
        "INSERT INTO transactions (payment_id, user_id, status, amount_rub, metadata) VALUES (?, ?, ?, ?, ?)",
        (payment_id, user_id, 'pending', amount_rub, json.dumps(metadata)),
        f"Не удалось создать ожидающую транзакцию для пользователя {user_id}"
    )
    return cursor.lastrowid if cursor else 0
# ====================================


# ===== LOG_TRANSACTION_SIMPLE =====
def log_transaction_simple(user_id: int, amount: float, method: str, description: str) -> bool:
    logging.info(f"📝 Логирование транзакции: user={user_id}, amount={amount}, method={method}")
    cursor = _exec(
        """
        INSERT INTO transactions (user_id, amount_rub, payment_method, status, description, created_date)
        VALUES (?, ?, ?, 'paid', ?, ?)
        """,
        (user_id, amount, method, description, get_msk_time().replace(tzinfo=None).replace(microsecond=0)),
        f"Не удалось залогировать транзакцию для пользователя {user_id}"
    )
    if cursor: logging.info(f"✅ Транзакция успешно сохранена для пользователя {user_id}"); return True
    return False
# ==================================

# ===== FIND_AND_COMPLETE_TON_TRANSACTION =====
def find_and_complete_ton_transaction(payment_id: str, amount_ton: float) -> dict | None:
    row = _fetch_row("SELECT * FROM transactions WHERE payment_id = ? AND status = 'pending'", (payment_id,), "")
    if not row: logging.warning(f"TON Webhook: Получен платеж для неизвестного или уже обработанного payment_id: {payment_id}"); return None
        
    cursor = _exec(
        "UPDATE transactions SET status = 'paid', amount_currency = ?, currency_name = 'TON', payment_method = 'TON' WHERE payment_id = ?",
        (amount_ton, payment_id),
        f"Не удалось завершить TON-транзакцию {payment_id}"
    )
    
    if cursor and cursor.rowcount > 0:
        try: return json.loads(row['metadata'])
        except Exception: return {}

    return None
# ===============================================


# ===== LOG_TRANSACTION =====
def log_transaction(username: str, transaction_id: str | None, payment_id: str | None, user_id: int, status: str, amount_rub: float, amount_currency: float | None, currency_name: str | None, payment_method: str, metadata: str):
    _exec(
        """INSERT INTO transactions
           (username, transaction_id, payment_id, user_id, status, amount_rub, amount_currency, currency_name, payment_method, metadata, created_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, transaction_id, payment_id, user_id, status, amount_rub, amount_currency, currency_name, payment_method, metadata, get_msk_time().replace(tzinfo=None).replace(microsecond=0)),
        f"Не удалось залогировать транзакцию для пользователя {user_id}"
    )
# ===========================

# ===== CHECK_TRANSACTION_EXISTS =====
def check_transaction_exists(payment_id: str) -> bool:
    row = _fetch_row("SELECT 1 as ex FROM transactions WHERE payment_id = ? LIMIT 1", (payment_id,), f"Не удалось проверить транзакцию {payment_id}")
    return bool(row)

def get_paginated_transactions(page: int = 1, per_page: int = 15) -> tuple[list[dict], int]:
    offset = (page - 1) * per_page
    transactions = []
    total = 0

    r_count = _fetch_row("SELECT COUNT(*) as c FROM transactions", (), "Не удалось получить кол-во транзакций")
    total = r_count["c"] if r_count else 0

    query = "SELECT * FROM transactions ORDER BY created_date DESC LIMIT ? OFFSET ?"
    rows = _fetch_list(query, (per_page, offset), "Не удалось получить страницу транзакций")

    for row in rows:
        transaction_dict = dict(row)
        
        metadata_str = transaction_dict.get('metadata')
        metadata: dict = {}
        if metadata_str:
            try:
                metadata = json.loads(metadata_str)
                if not isinstance(metadata, dict):
                    metadata = {}
            except json.JSONDecodeError:
                metadata = {}
        transaction_dict['action'] = metadata.get('action')
        plan_name = metadata.get('plan_name') or metadata.get('service_name')
        host_name = metadata.get('host_name') or metadata.get('host')
        if not plan_name:
            src = str(metadata.get('source') or '').lower()
            method = str(transaction_dict.get('payment_method') or '').lower()
            if src == 'shm_import':
                plan_name = 'VPN подписка (SHM)'
            elif method in ('yookassa', 'yoomoney'):
                plan_name = 'Подписка VPN'
            elif method == 'manual':
                plan_name = 'Ручная оплата'
            elif method == 'admin':
                plan_name = 'Пополнение / админ'
            else:
                plan_name = 'Оплата VPN'
        if not host_name:
            host_name = 'Alma'
        transaction_dict['host_name'] = host_name
        transaction_dict['plan_name'] = plan_name
        
        transactions.append(transaction_dict)
    
    return transactions, total
# ==========================================


# ===== SET_TRIAL_USED =====
def set_trial_used(telegram_id: int):
    cursor = _exec("UPDATE users SET trial_used = 1 WHERE telegram_id = ?", (telegram_id,), f"Не удалось установить trial_used для пользователя {telegram_id}")
    if cursor: logging.info(f"Пробный период отмечен как использованный для пользователя {telegram_id}.")
# ========================


# ===== ADD_NEW_KEY =====
def add_new_key(
    user_id: int,
    host_name: str | None,
    remnawave_user_uuid: str,
    key_email: str,
    expiry_timestamp_ms: int,
    *,
    squad_uuid: str | None = None,
    short_uuid: str | None = None,
    subscription_url: str | None = None,
    traffic_limit_bytes: int | None = None,
    traffic_limit_strategy: str | None = None,
    description: str | None = None,
    tag: str | None = None,
    comment_key: str | None = None,
    created_at_ms: int | None = None,
    hwid_limit: int | None = None,
) -> int | None:
    host_name_norm = normalize_host_name(host_name) if host_name else None
    email_normalized = _normalize_email(key_email) or key_email.strip()
    expire_str = _to_datetime_str(expiry_timestamp_ms) or _now_str()
    created_str = _to_datetime_str(created_at_ms) or _now_str() if created_at_ms is not None else _now_str()
    strategy_value = traffic_limit_strategy or "NO_RESET"
    hwid_val = int(hwid_limit) if hwid_limit is not None else 0
    
    cursor = _exec(
        """
        INSERT INTO vpn_keys (
            user_id, host_name, squad_uuid, remnawave_user_uuid, short_uuid, email, key_email,
            subscription_url, expire_at, created_at, updated_at, traffic_limit_bytes,
            traffic_limit_strategy, tag, description, comment_key, hwid_limit
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, host_name_norm, squad_uuid, remnawave_user_uuid, short_uuid, email_normalized,
            email_normalized, subscription_url, expire_str, created_str, created_str,
            traffic_limit_bytes, strategy_value, tag, description, comment_key, hwid_val
        ),
        f"Не удалось добавить новый ключ для пользователя {user_id}"
    )
    return cursor.lastrowid if cursor else None
# =======================


# ===== _APPLY_KEY_UPDATES =====
def _apply_key_updates(key_id: int, updates: dict[str, Any]) -> bool:
    if not updates: return False
    updates = dict(updates)
    updates["updated_at"] = _now_str()
    columns = ", ".join(f"{column} = ?" for column in updates)
    values = list(updates.values())
    values.append(key_id)
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE vpn_keys SET {columns} WHERE key_id = ?",
                tuple(values),
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e: logging.error("Не удалось обновить ключ %s: %s", key_id, e); return False
# ==============================


# ===== UPDATE_KEY_FIELDS =====
def update_key_fields(
    key_id: int,
    *,
    user_id: int | None = None,
    host_name: str | None = None,
    squad_uuid: str | None = None,
    remnawave_user_uuid: str | None = None,
    short_uuid: str | None = None,
    email: str | None = None,
    subscription_url: str | None = None,
    expire_at_ms: int | None = None,
    traffic_limit_bytes: int | None = None,
    traffic_limit_strategy: str | None = None,
    tag: str | None = None,
    description: str | None = None,
    comment_key: str | None = None,
    hwid_limit: int | None = None,
) -> bool:
    updates: dict[str, Any] = {}
    if user_id is not None:
        updates["user_id"] = user_id
    if host_name is not None:
        updates["host_name"] = normalize_host_name(host_name)
    if squad_uuid is not None:
        updates["squad_uuid"] = squad_uuid
    if remnawave_user_uuid is not None:
        updates["remnawave_user_uuid"] = remnawave_user_uuid
    if short_uuid is not None:
        updates["short_uuid"] = short_uuid
    if email is not None:
        normalized = _normalize_email(email) or email.strip()
        updates["email"] = normalized
        updates["key_email"] = normalized
    if subscription_url is not None:
        updates["subscription_url"] = subscription_url
    if expire_at_ms is not None:
        expire_str = _to_datetime_str(expire_at_ms) or _now_str()
        updates["expire_at"] = expire_str
    if traffic_limit_bytes is not None:
        updates["traffic_limit_bytes"] = traffic_limit_bytes
    if traffic_limit_strategy is not None:
        updates["traffic_limit_strategy"] = traffic_limit_strategy or "NO_RESET"
    if tag is not None:
        updates["tag"] = tag
    if description is not None:
        updates["description"] = description
    if comment_key is not None:
        updates["comment_key"] = comment_key
    if hwid_limit is not None:
        updates["hwid_limit"] = int(hwid_limit)
    return _apply_key_updates(key_id, updates)
# ===========================


# ===== DELETE_KEY_BY_EMAIL =====
def delete_key_by_email(email: str) -> bool:
    lookup = _normalize_email(email) or email.strip()
    cursor = _exec(
        "DELETE FROM vpn_keys WHERE email = ? OR key_email = ?",
        (lookup, lookup),
        "Не удалось удалить ключ"
    )
    if cursor: logger.debug("delete_key_by_email('%s') affected=%s", email, cursor.rowcount); return cursor.rowcount > 0
    return False
# ===========================


# ===== GET_USER_KEYS =====
def get_user_keys(user_id: int) -> list[dict]:
    rows = _fetch_list(
        "SELECT * FROM vpn_keys WHERE user_id = ? ORDER BY datetime(created_at) DESC, key_id DESC",
        (user_id,),
        f"Не удалось получить ключи для пользователя {user_id}"
    )
    return [_normalize_key_row(row) for row in rows]
# ===========================


# ===== GET_KEY_BY_ID =====
def get_key_by_id(key_id: int) -> dict | None:
    row = _fetch_row(
        "SELECT * FROM vpn_keys WHERE key_id = ?",
        (key_id,),
        f"Не удалось получить ключ по ID {key_id}"
    )
    return _normalize_key_row(row)
# =========================


# ===== GET_KEY_BY_EMAIL =====
def get_key_by_email(key_email: str) -> dict | None:
    lookup = _normalize_email(key_email) or key_email.strip()
    row = _fetch_row(
        "SELECT * FROM vpn_keys WHERE email = ? OR key_email = ?",
        (lookup, lookup),
        f"Не удалось получить ключ по email {key_email}"
    )
    return _normalize_key_row(row)
# =================================


# ===== GET_KEY_BY_REMNAWAVE_UUID =====
def get_key_by_remnawave_uuid(remnawave_uuid: str) -> dict | None:
    if not remnawave_uuid: return None
    normalized_uuid = remnawave_uuid.strip()
    row = _fetch_row(
        "SELECT * FROM vpn_keys WHERE remnawave_user_uuid = ? LIMIT 1",
        (normalized_uuid,),
        f"Не удалось получить ключ по remnawave uuid {remnawave_uuid}"
    )
    return _normalize_key_row(row)
# ===========================


# ===== UPDATE_KEY_INFO =====
def update_key_info(key_id: int, new_remnawave_uuid: str, new_expiry_ms: int, **kwargs) -> bool:
    return update_key_fields(
        key_id,
        remnawave_user_uuid=new_remnawave_uuid,
        expire_at_ms=new_expiry_ms,
        **kwargs,
    )


# ===== UPDATE_KEY_HOST_AND_INFO =====
def update_key_host_and_info(
    key_id: int,
    new_host_name: str,
    new_remnawave_uuid: str,
    new_expiry_ms: int,
    **kwargs,
) -> bool:
    return update_key_fields(
        key_id,
        host_name=new_host_name,
        remnawave_user_uuid=new_remnawave_uuid,
        expire_at_ms=new_expiry_ms,
        **kwargs,
    )


# ===== GET_NEXT_KEY_NUMBER =====
def get_next_key_number(user_id: int) -> int:
    count = _fetch_val("SELECT COUNT(*) FROM vpn_keys WHERE user_id = ?", (user_id,), 0)
    return int(count) + 1
# ===========================


# ===== GET_KEYS_FOR_HOST =====
def get_keys_for_host(host_name: str) -> list[dict]:
    host_name_normalized = normalize_host_name(host_name)
    rows = _fetch_list(
        "SELECT * FROM vpn_keys WHERE TRIM(host_name) = TRIM(?)",
        (host_name_normalized,),
        f"Не удалось получить ключи для хоста '{host_name}'"
    )
    return [_normalize_key_row(row) for row in rows]
# =============================


# ===== GET_ALL_VPN_USERS =====
def get_all_vpn_users() -> list[dict]:
    return _fetch_list("SELECT DISTINCT user_id FROM vpn_keys", (), "Не удалось получить всех VPN пользователей")
# ===========================


# ===== UPDATE_KEY_STATUS_FROM_SERVER =====
def update_key_status_from_server(key_email: str, client_data) -> bool:
    try:
        normalized_email = _normalize_email(key_email) or key_email.strip()
        existing = get_key_by_email(normalized_email)
        if client_data:
            if isinstance(client_data, dict):
                remote_uuid = client_data.get('uuid') or client_data.get('id')
                expire_value = client_data.get('expireAt') or client_data.get('expiryDate')
                subscription_url = client_data.get('subscriptionUrl') or client_data.get('subscription_url')
                expiry_ms = None
                if expire_value:
                    try:
                        remote_dt = datetime.fromisoformat(str(expire_value).replace('Z', '+00:00'))
                        expiry_ms = int(remote_dt.timestamp() * 1000)
                    except Exception: expiry_ms = None
            else:
                remote_uuid = getattr(client_data, 'id', None) or getattr(client_data, 'uuid', None)
                expiry_ms = getattr(client_data, 'expiry_time', None)
                subscription_url = getattr(client_data, 'subscription_url', None)
            if not existing: return False
            return update_key_fields(
                existing['key_id'],
                remnawave_user_uuid=remote_uuid,
                expire_at_ms=expiry_ms,
                subscription_url=subscription_url,
            )
        if existing: return delete_key_by_email(normalized_email)
        return True
    except sqlite3.Error as e: logging.error("Не удалось обновить статус ключа для %s: %s", key_email, e); return False
# ===========================


# ===== GET_DAILY_STATS_FOR_CHARTS =====
def get_daily_stats_for_charts(period: str | int = "1m") -> dict:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    chart_period = normalize_dashboard_period(str(period))
    stats = {'users': {}, 'keys': {}, 'income': {}, 'finance': {'topups': {'amount': 0.0, 'count': 0}, 'subscriptions': {'amount': 0.0, 'count': 0}, 'total': {'amount': 0.0, 'count': 0}}}
    group_fmt = "%Y-%m-%d"

    def period_filter_sql(date_col: str) -> tuple[str, tuple]:
        clause, params = _dashboard_period_since_sql(date_col, chart_period)
        if not clause:
            return "", ()
        return clause.replace(f" AND date({date_col})", f" AND date({date_col})").replace(
            f" AND {date_col}", f" AND date({date_col})"
        ) if "date(" not in clause else clause, params
    
    def get_data(table, date_col, is_count=True):
        nonlocal group_fmt
        pf, pparams = period_filter_sql(date_col)
        where_clause = f"WHERE 1=1{pf}" if pf else ""
        params = list(pparams)
        
        if is_count:
            query = f"SELECT STRFTIME('{group_fmt}', {date_col}) AS period, COUNT(*) as cnt FROM {table} {where_clause} GROUP BY period ORDER BY period"
        else:
            income_filter = _tx_shm_auto_pay_where()
            if where_clause:
                where_clause += f" AND {income_filter}"
            else:
                where_clause = f"WHERE {income_filter}"
            query = f"SELECT STRFTIME('{group_fmt}', {date_col}) AS period, payment_method, SUM(amount_rub) as total FROM {table} {where_clause} GROUP BY period, payment_method ORDER BY period"
        
        return _fetch_list(query, tuple(params), "Не удалось получить данные статистики по дням")

    for row in get_data("users", "registration_date"):
        stats['users'][row['period']] = row['cnt']

    for row in get_data("vpn_keys", "COALESCE(created_at, updated_at, CURRENT_TIMESTAMP)"):
        stats['keys'][row['period']] = row['cnt']

    for row in get_data("transactions", "created_date", is_count=False):
        day_key = row['period']
        method = row['payment_method']
        amount = row['total']
        if day_key not in stats['income']:
            stats['income'][day_key] = {}
        stats['income'][day_key][method or 'Other'] = float(amount) if amount else 0.0
    
    tx_where = "WHERE LOWER(COALESCE(status, '')) IN ('paid', 'completed', 'success')"
    tx_pf, tx_params = _dashboard_period_since_sql("created_date", chart_period)
    tx_where += tx_pf
    rows = _fetch_list(
        f"""
        SELECT amount_rub, payment_method, metadata
        FROM transactions
        {tx_where}
        """,
        tuple(tx_params),
        "Не удалось получить финансовую статистику"
    )
    for row in rows:
        amount = float(row['amount_rub'] or 0.0)
        payment_method = str(row['payment_method'] or '').strip().lower()
        try:
            metadata = json.loads(row['metadata'] or '{}')
            if not isinstance(metadata, dict):
                metadata = {}
        except Exception:
            metadata = {}
        action = str(metadata.get('action') or '').strip().lower()
        reason = str(metadata.get('reason') or '').strip().lower()
        is_income_method = payment_method not in ('balance', 'admin', 'referral')
        is_topup = action in ('topup', 'top_up') or reason == 'external_balance_top_up'
        is_subscription = action in ('new', 'extend') or reason == 'subscription_purchase_or_extend' or any(metadata.get(k) for k in ('plan_id', 'key_id', 'host_name', 'host', 'customer_email')) or is_income_method
        if is_topup and is_income_method:
            stats['finance']['topups']['amount'] += abs(amount)
            stats['finance']['topups']['count'] += 1
        elif is_subscription and is_income_method:
            stats['finance']['subscriptions']['amount'] += abs(amount)
            stats['finance']['subscriptions']['count'] += 1
    stats['finance']['total']['amount'] = stats['finance']['topups']['amount'] + stats['finance']['subscriptions']['amount']
    stats['finance']['total']['count'] = stats['finance']['topups']['count'] + stats['finance']['subscriptions']['count']
    return stats
# ==========================


# ===== GET_RECENT_TRANSACTIONS =====
def get_recent_transactions(limit: int = 15) -> list[dict]:
    query = """
        SELECT
            k.key_id,
            k.host_name,
            k.created_at,
            u.telegram_id,
            u.username
        FROM vpn_keys k
        JOIN users u ON k.user_id = u.telegram_id
        ORDER BY datetime(k.created_at) DESC, k.key_id DESC
        LIMIT ?
    """
    rows = _fetch_list(query, (limit,), "Не удалось получить последние транзакции")
    return rows


# ===== GET_ALL_USERS =====
# Получение всех пользователей с сортировкой по дате регистрации
def get_all_users() -> list[dict]:
    return _fetch_list("SELECT * FROM users ORDER BY registration_date DESC", (), "Не удалось получить всех пользователей")

    return rows
# ===================================


# ===== GET_USERS_PAGINATED =====
def get_users_paginated(page: int = 1, per_page: int = 30, q: str | None = None) -> tuple[list[dict], int]:
    """Вернуть пользователей постранично и общее количество (с учётом фильтра).

    Фильтр q ищет по username (LIKE) и по текстовому представлению telegram_id.
    """
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 30))
    offset = (page - 1) * per_page
    
    if q:
        q_like = f"%{q.strip()}%"
        
        count_query = """
            SELECT COUNT(*)
            FROM users
            WHERE (username LIKE ?)
               OR (CAST(telegram_id AS TEXT) LIKE ?)
        """
        total = _fetch_val(count_query, (q_like, q_like), 0, "Не удалось подсчитать пользователей с фильтром") or 0

        data_query = """
            SELECT *
            FROM users
            WHERE (username LIKE ?)
               OR (CAST(telegram_id AS TEXT) LIKE ?)
            ORDER BY is_pinned DESC, registration_date DESC
            LIMIT ? OFFSET ?
        """
        users = _fetch_list(data_query, (q_like, q_like, per_page, offset), "Не удалось получить страницу пользователей с фильтром")
    else:
        total = _fetch_val("SELECT COUNT(*) FROM users", (), 0, "Не удалось подсчитать пользователей") or 0
        
        data_query = "SELECT * FROM users ORDER BY is_pinned DESC, registration_date DESC LIMIT ? OFFSET ?"
        users = _fetch_list(data_query, (per_page, offset), "Не удалось получить страницу пользователей")

    return users, total


    return users, total
# ========================


# ===== TOGGLE_USER_PIN =====
def toggle_user_pin(user_id: int) -> bool:
    cursor = _exec(
        "UPDATE users SET is_pinned = NOT COALESCE(is_pinned, 0) WHERE telegram_id = ?",
        (user_id,),
        f"Не удалось переключить закреп для пользователя {user_id}"
    )
    return cursor is not None and cursor.rowcount > 0

    return cursor is not None and cursor.rowcount > 0
# ===========================


# ===== GET_KEYS_COUNTS_FOR_USERS =====
def get_keys_counts_for_users(user_ids: list[int]) -> dict[int, int]:
    result: dict[int, int] = {}
    if not user_ids: return result

    placeholders = ",".join(["?"] * len(user_ids))
    query = f"SELECT user_id, COUNT(*) AS cnt FROM vpn_keys WHERE user_id IN ({placeholders}) GROUP BY user_id"
    
    rows = _fetch_list(query, tuple(int(x) for x in user_ids), "Не удалось получить кол-во ключей для пользователей")
    
    for row in rows: result[int(row['user_id'])] = int(row['cnt'] or 0)
        
    return result

# ===== BAN_USER =====
# Установка флага is_banned=1 для пользователя
def ban_user(telegram_id: int):
    _exec("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (telegram_id,), f"Не удалось забанить пользователя {telegram_id}")

# ===== UNBAN_USER =====
# Снятие бана (is_banned=0) для пользователя
def unban_user(telegram_id: int):
    _exec("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (telegram_id,), f"Не удалось разбанить пользователя {telegram_id}")

# ===== DELETE_USER_KEYS =====
# Удаление всех ключей пользователя
def delete_user_keys(user_id: int):
    _exec("DELETE FROM vpn_keys WHERE user_id = ?", (user_id,), f"Не удалось удалить ключи пользователя {user_id}")

# ===== CREATE_SUPPORT_TICKET =====
def create_support_ticket(user_id: int, subject: str | None = None) -> int | None:
    row = _fetch_row(
        "SELECT ticket_id FROM support_tickets WHERE user_id = ? AND status = 'open' ORDER BY updated_at DESC LIMIT 1",
        (user_id,),
        ""
    )
    if row and row['ticket_id']: return int(row['ticket_id'])

    cursor = _exec(
        "INSERT INTO support_tickets (user_id, subject) VALUES (?, ?)",
        (user_id, subject),
        f"Не удалось создать тикет поддержки для пользователя {user_id}"
    )
    return cursor.lastrowid if cursor else None

    return cursor.lastrowid if cursor else None
# ===========================


# ===== GET_OR_CREATE_OPEN_TICKET =====
def get_or_create_open_ticket(user_id: int, subject: str | None = None) -> tuple[int | None, bool]:
    row = _fetch_row(
        "SELECT ticket_id FROM support_tickets WHERE user_id = ? AND status = 'open' ORDER BY updated_at DESC LIMIT 1",
        (user_id,),
        f"Не удалось получить тикет для пользователя {user_id}"
    )
    if row and row['ticket_id']: return int(row['ticket_id']), False
    cursor = _exec(
        "INSERT INTO support_tickets (user_id, subject) VALUES (?, ?)",
        (user_id, subject),
        f"Не удалось создать/получить тикет для пользователя {user_id}"
    )
    if cursor and cursor.lastrowid: return int(cursor.lastrowid), True
    return None, False

    return None, False
# ===================================


# ===== ADD_SUPPORT_MESSAGE =====
def add_support_message(ticket_id: int, sender: str, content: str, media: str | None = None) -> int | None:
    cursor = _exec(
        "INSERT INTO support_messages (ticket_id, sender, content, media) VALUES (?, ?, ?, ?)",
        (ticket_id, sender, content, media),
        f"Не удалось добавить сообщение в тикет {ticket_id}"
    )
    if cursor and cursor.lastrowid: mid = cursor.lastrowid; _exec("UPDATE support_tickets SET updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?", (ticket_id,), "Не удалось обновить время тикета"); return mid
    return None
# =============================


# ===== UPDATE_TICKET_THREAD_INFO =====
def update_ticket_thread_info(ticket_id: int, forum_chat_id: str | None, message_thread_id: int | None) -> bool:
    cursor = _exec(
        "UPDATE support_tickets SET forum_chat_id = ?, message_thread_id = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
        (forum_chat_id, message_thread_id, ticket_id),
        f"Не удалось обновить инфо о треде для тикета {ticket_id}"
    )
    return cursor is not None and cursor.rowcount > 0

    return cursor is not None and cursor.rowcount > 0
# =================================


# ===== GET_TICKET =====
def get_ticket(ticket_id: int) -> dict | None:
    return _fetch_row("SELECT * FROM support_tickets WHERE ticket_id = ?", (ticket_id,), f"Не удалось получить тикет {ticket_id}")

    return _fetch_row("SELECT * FROM support_tickets WHERE ticket_id = ?", (ticket_id,), f"Не удалось получить тикет {ticket_id}")
# ==================


# ===== GET_TICKET_BY_THREAD =====
def get_ticket_by_thread(forum_chat_id: str, message_thread_id: int) -> dict | None:
    return _fetch_row(
        "SELECT * FROM support_tickets WHERE forum_chat_id = ? AND message_thread_id = ?",
        (str(forum_chat_id), int(message_thread_id)),
        f"Не удалось получить тикет по треду {forum_chat_id}/{message_thread_id}"
    )

    return _fetch_row(
        "SELECT * FROM support_tickets WHERE forum_chat_id = ? AND message_thread_id = ?",
        (str(forum_chat_id), int(message_thread_id)),
        f"Не удалось получить тикет по треду {forum_chat_id}/{message_thread_id}"
    )
# ============================


# ===== GET_USER_TICKETS =====
def get_user_tickets(user_id: int, status: str | None = None) -> list[dict]:
    if status:
        return _fetch_list(
            "SELECT * FROM support_tickets WHERE user_id = ? AND status = ? ORDER BY updated_at DESC",
            (user_id, status),
            f"Не удалось получить тикеты для пользователя {user_id}"
        )
    return _fetch_list(
        "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
        f"Не удалось получить тикеты для пользователя {user_id}"
    )

    return _fetch_list(
        "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
        f"Не удалось получить тикеты для пользователя {user_id}"
    )
# ============================


# ===== GET_TICKET_MESSAGES =====
def get_ticket_messages(ticket_id: int) -> list[dict]:
    return _fetch_list(
        "SELECT * FROM support_messages WHERE ticket_id = ? ORDER BY created_at ASC",
        (ticket_id,),
        f"Не удалось получить сообщения для тикета {ticket_id}"
    )
# ===============================


# ===== SET_TICKET_STATUS =====
def set_ticket_status(ticket_id: int, status: str) -> bool:
    cursor = _exec(
        "UPDATE support_tickets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
        (status, ticket_id),
        f"Не удалось установить статус '{status}' для тикета {ticket_id}"
    )
    return cursor is not None and cursor.rowcount > 0

    return cursor is not None and cursor.rowcount > 0
# ===========================


# ===== SUPPORT FEED / AUTO-CLOSE HELPERS =====
def set_ticket_open_feed_msg_id(ticket_id: int, msg_id) -> bool:
    cursor = _exec(
        "UPDATE support_tickets SET open_feed_msg_id = ? WHERE ticket_id = ?",
        (msg_id, ticket_id),
        f"Не удалось сохранить open_feed_msg_id для тикета {ticket_id}"
    )
    return cursor is not None

def get_tickets_open_without_feed_card() -> list[dict]:
    return _fetch_list(
        "SELECT ticket_id, user_id, subject, forum_chat_id, message_thread_id FROM support_tickets "
        "WHERE status='open' AND open_feed_msg_id IS NULL",
        (), "Не удалось получить открытые тикеты без карточки"
    )

def get_tickets_closed_with_feed_card() -> list[dict]:
    return _fetch_list(
        "SELECT ticket_id, user_id, subject, forum_chat_id, message_thread_id, open_feed_msg_id FROM support_tickets "
        "WHERE status='closed' AND open_feed_msg_id IS NOT NULL",
        (), "Не удалось получить закрытые тикеты с карточкой"
    )

def get_open_tickets_for_autoclose(hours: int) -> list[dict]:
    """Открытые тикеты, где последнее не-note сообщение — от 'admin' и старше N часов
    (саппорт ответил, клиент молчит N часов)."""
    return _fetch_list(
        "SELECT t.ticket_id, t.user_id, t.subject, t.forum_chat_id, t.message_thread_id, t.open_feed_msg_id "
        "FROM support_tickets t WHERE t.status='open' "
        "AND (SELECT m.sender FROM support_messages m WHERE m.ticket_id=t.ticket_id AND m.sender!='note' "
        "     ORDER BY m.created_at DESC, m.message_id DESC LIMIT 1)='admin' "
        "AND (julianday('now') - julianday((SELECT m.created_at FROM support_messages m WHERE m.ticket_id=t.ticket_id "
        "     AND m.sender!='note' ORDER BY m.created_at DESC, m.message_id DESC LIMIT 1)))*24.0 >= ?",
        (hours,), "Не удалось получить тикеты для авто-закрытия"
    )
# ===========================


# ===== UPDATE_TICKET_SUBJECT =====
def update_ticket_subject(ticket_id: int, subject: str) -> bool:
    cursor = _exec(
        "UPDATE support_tickets SET subject = ?, updated_at = CURRENT_TIMESTAMP WHERE ticket_id = ?",
        (subject, ticket_id),
        f"Не удалось обновить тему для тикета {ticket_id}"
    )
    return cursor is not None and cursor.rowcount > 0

    return cursor is not None and cursor.rowcount > 0
# ===============================


# ===== DELETE_TICKET =====
def delete_ticket(ticket_id: int) -> bool:
    _exec("DELETE FROM support_messages WHERE ticket_id = ?", (ticket_id,), "Не удалось удалить сообщения тикета")
    cursor = _exec("DELETE FROM support_tickets WHERE ticket_id = ?", (ticket_id,), f"Не удалось удалить тикет {ticket_id}")
    return cursor is not None and cursor.rowcount > 0

    return cursor is not None and cursor.rowcount > 0
# ===========================


# ===== GET_TICKETS_PAGINATED =====
def get_tickets_paginated(page: int = 1, per_page: int = 20, status: str | None = None) -> tuple[list[dict], int]:
    offset = (page - 1) * per_page
    
    if status:
        total = _fetch_val("SELECT COUNT(*) FROM support_tickets WHERE status = ?", (status,), 0) or 0
        where_clause = " WHERE t.status = ?"
        params = [status]
    else:
        total = _fetch_val("SELECT COUNT(*) FROM support_tickets", (), 0) or 0
        where_clause = ""
        params = []
    
    base_query = """
        SELECT t.*, 
               u.username,
               (SELECT sender FROM support_messages 
                WHERE ticket_id = t.ticket_id 
                ORDER BY created_at DESC LIMIT 1) as last_sender
        FROM support_tickets t
        LEFT JOIN users u ON t.user_id = u.telegram_id
    """
    
    order_clause = """
        ORDER BY 
        CASE 
            WHEN t.status = 'open' AND (
                SELECT sender FROM support_messages 
                WHERE ticket_id = t.ticket_id 
                ORDER BY created_at DESC LIMIT 1
            ) != 'admin' THEN 1
            WHEN t.status = 'open' THEN 2
            ELSE 3
        END ASC,
        t.updated_at DESC
    """
    
    full_query = base_query + where_clause + order_clause + " LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
    
    rows = _fetch_list(full_query, tuple(params), "Не удалось получить страницу тикетов поддержки")
    return rows, total
# ===========================


# ===== GET_OPEN_TICKETS_COUNT =====
def get_open_tickets_count() -> int:
    return _fetch_val("SELECT COUNT(*) FROM support_tickets WHERE status = 'open'", (), 0) or 0
# ==============================


# ===== GET_WAITING_TICKETS_COUNT =====
def get_waiting_tickets_count() -> int:
    query = """
        SELECT COUNT(*) FROM support_tickets t
        WHERE t.status = 'open' AND (
            SELECT sender FROM support_messages 
            WHERE ticket_id = t.ticket_id 
            ORDER BY created_at DESC LIMIT 1
        ) != 'admin'
    """
    return _fetch_val(query, (), 0, "Не удалось получить кол-во ожидающих тикетов")
# ===================================


# ===== GET_SUPPORT_BADGE_COUNTS =====
def get_support_badge_counts() -> dict:
    """Универсальная функция для получения всех счетчиков бейджей в один запрос."""
    try:
        # Получаем общее количество открытых тикетов
        open_count = _fetch_val("SELECT COUNT(*) FROM support_tickets WHERE status = 'open'", (), 0) or 0
        
        # Получаем количество тикетов, ожидающих ответа админа (последнее сообщение не от админа)
        waiting_count = _fetch_val("""
            SELECT COUNT(*) FROM support_tickets t
            WHERE t.status = 'open' AND (
                SELECT sender FROM support_messages 
                WHERE ticket_id = t.ticket_id 
                ORDER BY created_at DESC LIMIT 1
            ) != 'admin'
        """, (), 0) or 0

        # ID последнего входящего (от пользователя) сообщения — для детекта новых сообщений на клиенте
        last_user_message_id = _fetch_val(
            "SELECT MAX(message_id) FROM support_messages WHERE sender = 'user'", (), 0
        ) or 0
        
        return {
            "ok": True,
            "open_count": open_count,
            "waiting_tickets_count": waiting_count,
            "last_user_message_id": last_user_message_id
        }
    except Exception as e:
        logger.error(f"Ошибка при получении счетчиков бейджей: {e}")
        return {"ok": False, "error": str(e), "open_count": 0, "waiting_tickets_count": 0, "last_user_message_id": 0}


# ===== GET_CLOSED_TICKETS_COUNT =====
def get_closed_tickets_count() -> int:
    return _fetch_val("SELECT COUNT(*) FROM support_tickets WHERE status = 'closed'", (), 0) or 0
# ==================================


# ===== GET_ALL_TICKETS_COUNT =====
def get_all_tickets_count() -> int:
    return _fetch_val("SELECT COUNT(*) FROM support_tickets", (), 0) or 0
# ===============================


# ===== GET_OTHER_VALUE =====
def get_other_value(key: str) -> str | None:
    return _fetch_val("SELECT value FROM other WHERE key = ?", (key,), None, f"Не удалось получить other-значение для {key}")


    return _fetch_val("SELECT value FROM other WHERE key = ?", (key,), None, f"Не удалось получить other-значение для {key}")
# =========================


# ===== SET_OTHER_VALUE =====
def set_other_value(key: str, value: str) -> bool:
    cursor = _exec(
        "INSERT OR REPLACE INTO other (key, value) VALUES (?, ?)",
        (key, value),
        f"Не удалось установить other-значение для {key}"
    )
    return cursor is not None




    return cursor is not None
# =======================


# ===== UPDATE_SSH_TARGET_SCHEDULER =====
def update_ssh_target_scheduler(target_name: str, time_auto: str) -> bool:
    name = normalize_host_name(target_name)
    cursor = _exec(
        "UPDATE speedtest_ssh_targets SET time_auto = ? WHERE TRIM(target_name) = TRIM(?)",
        (time_auto, name),
        f"Не удалось обновить планировщик для '{target_name}'"
    )
    return cursor is not None and cursor.rowcount > 0
# ===================================


# ===== UPDATE_HOST_SORT_ORDER =====
def update_host_sort_order(host_name: str, sort_order: int) -> bool:
    name = normalize_host_name(host_name)
    cursor = _exec(
        "UPDATE xui_hosts SET sort_order = ? WHERE TRIM(host_name) = TRIM(?)",
        (sort_order, name),
        "Не удалось обновить sort_order хоста"
    )
    if cursor and cursor.rowcount > 0: logging.info(f"Обновлён sort_order хоста '{name}': {sort_order}"); return True
    logging.warning(f"Хост '{name}' не найден для обновления sort_order"); return False
# ==============================


# ===== UPDATE_SSH_TARGET_SORT_ORDER =====
def update_ssh_target_sort_order(target_name: str, sort_order: int) -> bool:
    name = normalize_host_name(target_name)
    cursor = _exec(
        "UPDATE speedtest_ssh_targets SET sort_order = ? WHERE TRIM(target_name) = TRIM(?)",
        (sort_order, name),
        "Не удалось обновить sort_order SSH-цели"
    )
    if cursor and cursor.rowcount > 0: logging.info(f"Обновлён sort_order SSH-цели '{name}': {sort_order}"); return True
    logging.warning(f"SSH-цель '{name}' не найдена для обновления sort_order"); return False
# ====================================


# ===== GET_OTHER_SETTING =====
def get_other_setting(key: str, default: Any = None) -> Any:
    val = get_other_value(key)
    return val if val is not None else default
# =========================


# ===== UPDATE_OTHER_SETTING =====
def update_other_setting(key: str, value: Any) -> bool:
    return set_other_value(key, str(value))


def get_all_other_settings() -> dict:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM other")
        return {row['key']: row['value'] for row in cursor.fetchall()}

# ===========================================
# ===== WEBAPP SETTINGS =====
# Проверка и получение настроек веб-приложения
def _default_webapp_config() -> dict:
    return {
        "home": {
            "buy_button_prefix": "Купить подписку",
            "buy_button_price_prefix": "от",
            "renew_button_text": "Продлить ключ VPN",
            "setup_button_text": "Установка и настройка",
            "setup_tooltip_text": "Сначала нужно настроить VPN на вашем устройстве",
            "trial_button_text": "Попробовать бесплатно",
            "trial_button_hint": "Бесплатный доступ без оплаты",
            "profile_button_text": "Профиль",
            "support_button_text": "Поддержка",
            "status_badge_active": "подписка активна",
            "status_badge_expired": "подписка истекла",
            "status_no_subscription": "нет подписки",
            "status_online": "online",
            "status_offline": "offline",
            "date_prefix": "до",
        },
        "purchase": {
            "title": "Покупка подписки",
            "subtitle": "Подключайте больше устройств и пользуйтесь сервисом вместе с друзьями и близкими",
            "pay_button_text": "Оплатить подписку",
            "server_label_text": "Локация сервера",
            "server_placeholder_text": "Выберите сервер",
            "devices_label_text": "Устройств",
            "devices_hint_text": "Одновременно в подписке",
            "info_title": "Информация",
            "show_info_card": "0",
            "device_card_title": "Устройство",
            "device_card_title_one": "Устройство",
            "device_card_title_few": "Устройства",
            "device_card_title_many": "Устройств",
            "device_card_subtitle": "Одновременно в подписке",
            "recommended_months_csv": "6",
            "discount_badge_min_percent": "10",
            "device_discount_badges": {
                "1": "",
                "3": "-50%",
                "5": "-60%",
                "7": "-64%",
                "10": "-70%"
            },
            "month_label_one": "месяц",
            "month_label_few": "месяца",
            "month_label_many": "месяцев",
            "per_month_label": "в месяц",
            "year_label": "год",
            "trial_plan_title": "Тестовый тариф",
            "trial_plan_subtitle": "Бесплатный пробный период",
            "trial_plan_price_label": "0 ₽",
        },
        "renew": {
            "pay_button_text": "Продлить подписку",
            "info_title": "Информация",
            "selected_key_label_text": "Выбранный ключ",
            "devices_label_text": "Устройств",
            "devices_hint_text": "Одновременно в подписке",
        },
        "setup": {
            "title": "Настройка на iOS",
            "subtitle": "Настройка VPN происходит в 3 шага и занимает пару минут",
            "active_keys_title": "Ваши действующие ключи",
            "footer_text": "Нужна помощь? Обратитесь в поддержку",
            "instruction_title": "Инструкция",
            "instruction_body": "Нажмите «Открыть инструкцию» для получения персонального руководства по настройке на вашем устройстве.",
            "open_button_text": "Открыть инструкцию",
            "devices_button_text": "Устройства",
            "comments_button_text": "Название",
            "empty_title": "Подготовьте подключение",
            "empty_subtitle": "Откройте ключ, установите приложение и подключитесь за пару минут.",
            "wizard_primary_button": "Начать настройку на этом устройстве",
            "wizard_secondary_button": "Установить на другом устройстве",
            "empty_keys_title": "Нет активных ключей",
            "empty_keys_subtitle": "Купите подписку, чтобы начать настройку",
            "platform_title_ios": "Настройка на iOS",
            "platform_title_android": "Настройка на Android",
            "platform_title_macos": "Настройка на macOS",
            "platform_title_windows": "Настройка на Windows",
            "step_app_title": "Приложение",
            "step_app_subtitle": "Установите приложение Happ из магазина приложений",
            "step_app_button": "Скачать Happ",
            "step_app_store_url_ios": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973",
            "step_app_store_url_android": "https://play.google.com/store/apps/details?id=com.happproxy",
            "step_app_store_url_macos": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973",
            "step_app_store_url_windows": "https://github.com/Happ-proxy/happ-desktop/releases/latest",
            "step_sub_title": "Подписка",
            "step_sub_subtitle": "Добавьте подписку в приложение Happ одним нажатием",
            "step_sub_button": "Добавить подписку",
            "buy_subscription_button": "Купить подписку",
            "no_subscription_subtitle": "У вас пока нет активной подписки. Оформите её, чтобы продолжить настройку.",
            "no_subscription_notice": "Сначала оформите подписку",
            "step_done_title": "Готово!",
            "step_done_subtitle": "Подписка добавлена. Откройте Happ и подключитесь к VPN.",
            "step_done_button": "Открыть Happ",
            "step_done_home_button": "На главную",
            "app_name": "Happ",
            "instruction_ios": "Установите приложение Happ из App Store\nВернитесь сюда и нажмите «Добавить подписку»\nРазрешите добавление VPN-конфигурации\nОткройте Happ и нажмите «Подключиться»",
            "instruction_android": "Установите приложение Happ из Google Play\nВернитесь сюда и нажмите «Добавить подписку»\nРазрешите добавление VPN-конфигурации\nОткройте Happ и нажмите «Подключиться»",
            "instruction_macos": "Установите приложение Happ из App Store\nВернитесь сюда и нажмите «Добавить подписку»\nРазрешите добавление VPN-конфигурации\nОткройте Happ и нажмите «Подключиться»",
            "instruction_windows": "Скачайте Happ для Windows по кнопке ниже\nУстановите и запустите приложение\nСкопируйте ссылку на подписку и импортируйте её в Happ\nНажмите «Подключиться»",
            "instruction_store_button": "Скачать приложение",
            "instruction_add_sub_button": "Добавить подписку",
            "instruction_copy_link_button": "Скопировать ссылку",
            "instruction_no_sub_text": "Сначала оформите подписку, чтобы получить ссылку.",
        },
        "profile": {
            "keys_title": "Ваши ключи",
            "user_id_label": "ID пользователя",
            "balance_label": "Баланс",
            "referrals_label": "Рефералы",
            "income_label": "Доход",
            "keys_stat_label": "Ключи",
            "referrals_unit": "чел.",
            "keys_unit": "шт.",
            "promo_button_text": "Ввести промокод",
            "logout_button_text": "Выйти из аккаунта",
            "install_hero_title": "Установка на другом устройстве",
            "install_hero_subtitle": "Выберите платформу для подключения на другом устройстве",
            "subscription_link_label": "Ваша ссылка на подписку",
            "platform_ios_label": "Инструкция для iOS",
            "platform_android_label": "Инструкция для Android",
            "platform_macos_label": "Инструкция для macOS",
            "platform_windows_label": "Инструкция для Windows",
            "empty_keys_title": "Нет ключей",
            "empty_keys_subtitle": "Купите ключ, чтобы начать пользоваться VPN",
            "devices_section_title": "Мои устройства",
            "devices_section_subtitle": "Устройства, подключённые к вашей подписке",
            "devices_manage_button": "Управление",
            "devices_count_unit": "уст.",
            "devices_section_empty": "Нет активных подписок",
            "referral_share_text": "Подключайся к быстрому VPN — заходи по моей ссылке:",
        },
        "support": {
            "header_title": "Служба заботы",
            "create_title": "Нужна помощь?",
            "create_subtitle": "Опишите кратко вашу проблему в форме ниже, чтобы мы могли быстрее вам помочь.",
            "subject_placeholder": "Например: Не работает VPN",
            "message_placeholder": "Сообщение...",
            "start_chat_button_text": "Начать чат",
            "telegram_button_text": "Написать в Telegram",
            "response_time_text": "Среднее время ответа: 50 минут",
            "loading_text": "Загрузка информации...",
            "faq_title": "Часто задаваемые вопросы",
            "faq_subtitle": "Ответы на частые вопросы",
            "other_device_title": "Установка на другом устройстве",
            "other_device_subtitle": "Подробная инструкция для установки",
            "contact_title": "Поддержка",
            "contact_subtitle": "Связаться с поддержкой",
            "quick_section_title": "Связаться с поддержкой",
            "quick_section_subtitle": "Ответы на частые вопросы ниже. Переписка с поддержкой — в Telegram-боте.",
            "bot_cta_title": "Чат с поддержкой в Telegram",
            "bot_cta_subtitle": "Тикеты ведутся в боте поддержки — напишите туда, это удобнее.",
            "bot_open_button_text": "Открыть чат поддержки",
            "closed_ticket_text": "Это обращение закрыто.",
            "empty_thread_text": "Опишите проблему — оператор скоро ответит.",
            "open_ticket_title": "Открыть текущее обращение",
            "open_ticket_subtitle": "У вас есть открытый диалог с поддержкой",
            "new_ticket_button_text": "Создать новое обращение",
            "close_ticket_button_text": "Закрыть обращение",
            "close_ticket_confirm_text": "Закрыть это обращение? Вы сможете создать новое, если вопрос не решён.",
            "faq_raw": (
                "Как установить VPN?\n"
                "Откройте раздел «Установка», выберите вашу платформу и следуйте пошаговой инструкции — это займёт около минуты.\n"
                "---\n"
                "Как оплатить подписку?\n"
                "Нажмите «Купить», выберите тариф и удобный способ оплаты. После оплаты ключ активируется автоматически.\n"
                "---\n"
                "Не работает подключение, что делать?\n"
                "Проверьте, что подписка активна и приложение обновлено. Переподключитесь или смените сервер. Если не помогло — напишите в поддержку.\n"
                "---\n"
                "Сколько устройств можно подключить?\n"
                "Зависит от тарифа. Текущие устройства видны в профиле в разделе «Мои устройства», там же их можно удалить или переименовать."
            ),
        },
        "common": {
            "back_text": "Назад",
            "miniapp_subtitle": "мини-приложение",
            "or_separator": "Или",
            "expand_text": "Развернуть ▼",
            "collapse_text": "Свернуть ▲",
            "devices_modal_title": "Устройства",
            "comment_modal_title": "Название ключа",
            "comment_placeholder": "Напр.: Мама, Папа, Брат",
            "menu_refresh_text": "Обновить",
            "menu_logout_text": "Выйти",
            "devices_empty_text": "Нет активных устройств",
            "device_rename_title": "Название устройства",
            "device_rename_placeholder": "Напр: iPhone мамы",
            "device_rename_save_text": "Сохранить",
            "device_renamed_text": "Название сохранено",
            "trial_activated_text": "Пробный доступ активирован!",
            "payment_check_text": "Я оплатил — проверить",
            "payment_not_yet_text": "Оплата ещё не поступила. Если вы только что оплатили — подождите минуту и проверьте снова.",
            "network_error_text": "Ошибка сети",
            "comment_save_button_text": "Сохранить",
            "error_text": "Ошибка",
            "connection_error_text": "Ошибка связи",
            "promo_hint_text": "Введите бонусный промокод на баланс или дни",
            "promo_placeholder_text": "Напр: BONUS2024",
            "promo_apply_text": "Применить",
            "promo_activate_text": "Активировать",
            "promo_applied_text": "Промокод применен!",
            "promo_not_found_text": "Промокод не найден",
            "promo_activated_text": "Промокод активирован!",
            "payment_select_method_text": "Выберите способ оплаты",
            "payment_confirm_title": "Подтверждение оплаты",
            "payment_methods_title": "Изменить способ оплаты",
            "payment_new_card_text": "Оплата новой картой",
            "payment_devices_count_prefix": "Количество устройств:",
            "payment_subscription_prefix": "Подписка до",
            "payment_pay_prefix": "Оплатить",
            "payment_waiting_title": "Ожидаем оплату",
            "payment_waiting_desc": "Завершите оплату в открывшемся окне...",
            "payment_go_to_pay_text": "Перейти к оплате",
            "payment_cancel_text": "Отменить",
            "device_deleted_text": "Устройство удалено",
            "comment_saved_text": "Название сохранено!",
            "support_subject_required_text": "Укажите причину обращения",
            "payment_link_missing_text": "Ссылка на оплату не получена.",
        },
    }


def _merge_webapp_config(default_cfg: dict, custom_cfg: dict) -> dict:
    merged: dict = {}
    for key, value in default_cfg.items():
        if isinstance(value, dict):
            candidate = custom_cfg.get(key) if isinstance(custom_cfg, dict) else {}
            merged[key] = _merge_webapp_config(value, candidate if isinstance(candidate, dict) else {})
        else:
            if isinstance(custom_cfg, dict) and key in custom_cfg and custom_cfg[key] is not None:
                merged[key] = custom_cfg[key]
            else:
                merged[key] = value
    if isinstance(custom_cfg, dict):
        for key, value in custom_cfg.items():
            if key not in merged:
                merged[key] = value
    return merged


def get_webapp_settings() -> dict:
    row = _fetch_row("SELECT * FROM webapp_settings WHERE id = 1")
    if not row:
        with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
            _ensure_webapp_settings_table(conn.cursor())
            conn.commit()
        row = _fetch_row("SELECT * FROM webapp_settings WHERE id = 1")
    if not row:
        return {}
    result = dict(row)
    default_cfg = _default_webapp_config()
    cfg_raw = result.get("webapp_config_json")
    cfg_parsed = {}
    if isinstance(cfg_raw, str) and cfg_raw.strip():
        try:
            decoded = json.loads(cfg_raw)
            if isinstance(decoded, dict):
                cfg_parsed = decoded
        except Exception:
            cfg_parsed = {}
    result["webapp_config"] = _merge_webapp_config(default_cfg, cfg_parsed)
    return result

# Обновление настроек веб-приложения
def update_webapp_settings(
    webapp_title: str = None,
    webapp_domen: str = None,
    webapp_enable: int = None,
    webapp_logo: str = None,
    webapp_icon: str = None,
    tg_fullscreen: int = None,
    webapp_config_json: str = None
) -> bool:
    try:
        updates = []
        params = []
        if webapp_title is not None:
            updates.append("webapp_title = ?")
            params.append(webapp_title)
        if webapp_domen is not None:
            updates.append("webapp_domen = ?")
            params.append(webapp_domen)
        if webapp_enable is not None:
            updates.append("webapp_enable = ?")
            params.append(int(webapp_enable))
        if webapp_logo is not None:
            updates.append("webapp_logo = ?")
            params.append(webapp_logo)
        if webapp_icon is not None:
            updates.append("webapp_icon = ?")
            params.append(webapp_icon)
        if tg_fullscreen is not None:
            updates.append("tg_fullscreen = ?")
            params.append(int(tg_fullscreen))
        if webapp_config_json is not None:
            updates.append("webapp_config_json = ?")
            params.append(webapp_config_json)
        
        if not updates:
            return False
        
        # Строим SQL запрос
        sql = f"UPDATE webapp_settings SET {', '.join(updates)} WHERE id = 1"
        return _exec(sql, tuple(params))
    except Exception as e:
        logging.error(f"Ошибка при обновлении настроек webapp: {e}")
        return False
def update_user_auth_token(user_id: int, token: str | None) -> bool:
    return _exec("UPDATE users SET auth_token = ? WHERE telegram_id = ?", (token, user_id), "Failed to update auth_token") is not None

def get_user_by_auth_token(token: str) -> dict | None:
    if not token: return None
    return _fetch_row("SELECT * FROM users WHERE auth_token = ?", (token,), "Failed to get user by auth_token")

def get_auth_token_by_user_id(user_id: int) -> str | None:
    row = _fetch_row("SELECT auth_token FROM users WHERE telegram_id = ?", (user_id,), "Failed to get auth_token by user_id")
    return row["auth_token"] if row else None

# ===== ДАШБОРД: СТАТИСТИКА ГРУПП ПОЛЬЗОВАТЕЛЕЙ =====
def get_dashboard_user_groups(period: str | int = "all") -> dict:
    if isinstance(period, int):
        period = "all" if not period or period <= 0 else "1m"
    period = normalize_dashboard_period(str(period))
    groups = {
        "no_purchases": [],
        "inactive_buyers": [],
        "trials": [],
        "active_buyers": [],
        "active_keys": []
    }
    reg_since, reg_params = _dashboard_period_since_sql("u.registration_date", period)
    key_since, key_params = _dashboard_period_since_sql(
        "COALESCE(k.created_at, k.updated_at, CURRENT_TIMESTAMP)", period
    )
    period_active = period != "all"
    
    def purchase_condition(alias: str) -> str:
        meta_expr = f"CASE WHEN json_valid(COALESCE({alias}.metadata, '{{}}')) THEN COALESCE({alias}.metadata, '{{}}') ELSE '{{}}' END"
        period_filter = ""
        if period_active:
            tx_pf, _ = _dashboard_period_since_sql(f"{alias}.created_date", period)
            period_filter = tx_pf
        return f"""
        LOWER(COALESCE({alias}.status, '')) IN ('paid', 'completed', 'success', 'succeeded')
        AND LOWER(COALESCE({alias}.payment_method, '')) NOT IN ('admin', 'referral')
        AND (
            LOWER(COALESCE(json_extract({meta_expr}, '$.action'), '')) IN ('new', 'extend')
            OR LOWER(COALESCE(json_extract({meta_expr}, '$.reason'), '')) = 'subscription_purchase_or_extend'
            OR json_extract({meta_expr}, '$.plan_id') IS NOT NULL
            OR json_extract({meta_expr}, '$.key_id') IS NOT NULL
            OR json_extract({meta_expr}, '$.host_name') IS NOT NULL
            OR json_extract({meta_expr}, '$.host') IS NOT NULL
            OR json_extract({meta_expr}, '$.customer_email') IS NOT NULL
        ){period_filter}
        """
    
    # 1. Не купил ключ (за период: зарегистрировались, но без покупки в периоде)
    if period_active:
        q_no = f"""
        SELECT u.telegram_id, u.username, u.balance,
               (SELECT COALESCE(SUM(t2.amount_rub), 0) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as total_spent
        FROM users u
        WHERE 1=1{reg_since}
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.user_id = u.telegram_id AND {purchase_condition('t')})
        """
        groups["no_purchases"] = _fetch_list(q_no, reg_params, "Ошибка получения no_purchases")
    else:
        q_no = f"""
        SELECT u.telegram_id, u.username, u.balance,
               (SELECT COALESCE(SUM(t2.amount_rub), 0) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as total_spent
        FROM users u
        WHERE NOT EXISTS (SELECT 1 FROM vpn_keys k WHERE k.user_id = u.telegram_id AND NOT ({_dashboard_trial_key_predicate('k')}))
          AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.user_id = u.telegram_id AND {purchase_condition('t')})
        """
        groups["no_purchases"] = _fetch_list(q_no, (), "Ошибка получения no_purchases")
    
    # 2. Покупали, но сейчас нет активных (истекли или нет ключей, но есть транзакции)
    q_inactive = f"""
    SELECT u.telegram_id, u.username, u.balance,
           (SELECT SUM(COALESCE(
               CAST(json_extract(t2.metadata, '$.months') AS INTEGER),
               (SELECT p.months FROM plans p WHERE p.plan_id = CAST(json_extract(t2.metadata, '$.plan_id') AS INTEGER)),
               0
           )) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as months_bought,
           (SELECT COALESCE(SUM(t2.amount_rub), 0) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as total_spent
    FROM users u
    WHERE EXISTS (SELECT 1 FROM transactions t WHERE t.user_id = u.telegram_id AND {purchase_condition('t')})
      AND NOT EXISTS (
          SELECT 1 FROM vpn_keys k 
          WHERE k.user_id = u.telegram_id 
            AND NOT ({_dashboard_trial_key_predicate('k')})
            AND (k.expire_at IS NULL OR k.expire_at > datetime('now', '+3 hours'))
      )
    """
    groups["inactive_buyers"] = _fetch_list(q_inactive, (), "Ошибка получения inactive_buyers")
    
    # 3. Используют триал (активный триал; за период — выдан в периоде)
    q_trials = f"""
    SELECT u.telegram_id, u.username, u.balance, k.key_id, k.expire_at,
           (SELECT SUM(COALESCE(
               CAST(json_extract(t2.metadata, '$.months') AS INTEGER),
               (SELECT p.months FROM plans p WHERE p.plan_id = CAST(json_extract(t2.metadata, '$.plan_id') AS INTEGER)),
               0
           )) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as months_bought,
           (SELECT COALESCE(SUM(t2.amount_rub), 0) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as total_spent
    FROM users u
    JOIN vpn_keys k ON k.user_id = u.telegram_id
    WHERE {_dashboard_trial_key_predicate('k')}
      AND (k.expire_at IS NULL OR k.expire_at > datetime('now', '+3 hours'))
      {key_since}
    GROUP BY u.telegram_id
    """
    groups["trials"] = _fetch_list(q_trials, key_params, "Ошибка получения trials")
    
    # 4. Купили ключ (за период — по оплатам; за всё время — с активным ключом)
    if period_active:
        q_active_buyers = f"""
        SELECT u.telegram_id, u.username, u.balance, NULL as key_id, NULL as expire_at,
               (SELECT SUM(COALESCE(
                   CAST(json_extract(t2.metadata, '$.months') AS INTEGER),
                   (SELECT p.months FROM plans p WHERE p.plan_id = CAST(json_extract(t2.metadata, '$.plan_id') AS INTEGER)),
                   0
               )) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as months_bought,
               (SELECT COALESCE(SUM(t2.amount_rub), 0) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as total_spent
        FROM users u
        WHERE EXISTS (SELECT 1 FROM transactions t WHERE t.user_id = u.telegram_id AND {purchase_condition('t')})
        GROUP BY u.telegram_id
        """
    else:
        q_active_buyers = f"""
        SELECT u.telegram_id, u.username, u.balance, k.key_id, k.expire_at,
               (SELECT SUM(COALESCE(
                   CAST(json_extract(t2.metadata, '$.months') AS INTEGER),
                   (SELECT p.months FROM plans p WHERE p.plan_id = CAST(json_extract(t2.metadata, '$.plan_id') AS INTEGER)),
                   0
               )) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as months_bought,
               (SELECT COALESCE(SUM(t2.amount_rub), 0) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as total_spent
        FROM users u
        JOIN vpn_keys k ON k.user_id = u.telegram_id
        WHERE NOT ({_dashboard_trial_key_predicate('k')})
          AND (k.expire_at IS NULL OR k.expire_at > datetime('now', '+3 hours'))
        GROUP BY u.telegram_id
        """
    groups["active_buyers"] = _fetch_list(q_active_buyers, (), "Ошибка получения active_buyers")
    
    # 5. Активные ключи (за период — выданные в периоде и ещё действуют; иначе все активные)
    q_active_keys = f"""
    SELECT k.key_id, k.user_id as telegram_id, k.host_name, k.expire_at, u.username, u.balance,
           (SELECT SUM(COALESCE(
               CAST(json_extract(t2.metadata, '$.months') AS INTEGER),
               (SELECT p.months FROM plans p WHERE p.plan_id = CAST(json_extract(t2.metadata, '$.plan_id') AS INTEGER)),
               0
           )) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as months_bought,
           (SELECT COALESCE(SUM(t2.amount_rub), 0) FROM transactions t2 WHERE t2.user_id = u.telegram_id AND {purchase_condition('t2')}) as total_spent
    FROM vpn_keys k
    LEFT JOIN users u ON k.user_id = u.telegram_id
    WHERE (k.expire_at IS NULL OR k.expire_at > datetime('now', '+3 hours'))
      AND NOT ({_dashboard_trial_key_predicate('k')})
      {key_since}
    """
    groups["active_keys"] = _fetch_list(q_active_keys, key_params, "Ошибка получения active_keys")

    return groups
# ===================================================


# ==========================================================================
# ===== КОНКУРС ПАРТНЁРОВ (билеты = приведённые платящие клиенты) ===========
# ==========================================================================
# Билет = реферал партнёра, у которого ПЕРВАЯ квалифицирующая покупка подписки
# (не топ-ап, не admin-грант, сумма >= порога) попадает в период конкурса.
# Первая-в-периоде = «новый клиент», отсекает мигрированных и фейки-топапы.

def _ensure_contests_table(cursor: sqlite3.Cursor) -> None:
    """Таблица конкурсов + индекс на users.referred_by для быстрого подсчёта билетов."""
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS contests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                prizes TEXT DEFAULT '',
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                min_rub REAL DEFAULT 150,
                status TEXT DEFAULT 'active',
                winners TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)")
    except Exception as e:
        logging.error(f"Не удалось создать таблицу contests: {e}")


def _ensure_notification_log_table(cursor: sqlite3.Cursor) -> None:
    """Лог отправленных ре-энгейджмент-оповещений: персистентный дедуп (не слать
    одно касание дважды даже после рестарта) + подсчёт дневного/недельного лимита."""
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                campaign TEXT NOT NULL,
                touch INTEGER DEFAULT 0,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notiflog_user ON notification_log(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notiflog_user_campaign ON notification_log(user_id, campaign, touch)")
    except Exception as e:
        logging.error(f"Не удалось создать таблицу notification_log: {e}")


def _row_is_contest_purchase(amount_rub, payment_method, metadata_raw, min_rub) -> bool:
    """True, если транзакция — покупка подписки на сумму >= порога (не топ-ап, не admin-грант).
    Определение «покупки подписки» согласовано с get_finance_stats (metadata.action / ключи)."""
    try:
        amount = float(amount_rub or 0.0)
    except Exception:
        amount = 0.0
    if amount < float(min_rub or 0):
        return False
    method = str(payment_method or '').strip().lower()
    if method == 'admin':
        return False
    try:
        md = json.loads(metadata_raw or '{}')
        if not isinstance(md, dict):
            md = {}
    except Exception:
        md = {}
    action = str(md.get('action') or '').strip().lower()
    if action in ('topup', 'top_up'):
        return False
    is_sub = action in ('new', 'extend') or any(md.get(k) for k in ('plan_id', 'key_id', 'host_name', 'host', 'customer_email'))
    return bool(is_sub)


def _contest_first_purchase_by_user(user_ids, min_rub) -> dict:
    """{user_id: 'YYYY-MM-DD' первой квалифицирующей покупки} по списку рефералов."""
    result: dict = {}
    ids = list({int(u) for u in (user_ids or [])})
    if not ids:
        return result
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        ph = ",".join("?" for _ in chunk)
        rows = _fetch_list(
            f"SELECT user_id, amount_rub, payment_method, metadata, created_date "
            f"FROM transactions WHERE user_id IN ({ph}) "
            f"AND LOWER(COALESCE(status,'')) IN ('paid','completed','success','succeeded')",
            tuple(chunk), "contest: не удалось получить транзакции")
        for r in rows:
            if not _row_is_contest_purchase(r.get('amount_rub'), r.get('payment_method'), r.get('metadata'), min_rub):
                continue
            d = r.get('created_date')
            if not d:
                continue
            day = str(d)[:10]
            uid = r.get('user_id')
            if uid not in result or day < result[uid]:
                result[uid] = day
    return result


def get_active_contest() -> dict | None:
    return _fetch_row("SELECT * FROM contests WHERE status = 'active' ORDER BY id DESC LIMIT 1", (), "contest: активный")


def get_contest(contest_id: int) -> dict | None:
    return _fetch_row("SELECT * FROM contests WHERE id = ?", (contest_id,), "contest: по id")


def list_contests() -> list[dict]:
    return _fetch_list("SELECT * FROM contests ORDER BY id DESC", (), "contest: список")


def create_contest(title, description, prizes, start_date, end_date, min_rub=150) -> int | None:
    """Создаёт активный конкурс; прежние активные автоматически завершаются (активный один)."""
    _exec("UPDATE contests SET status='finished', finished_at=CURRENT_TIMESTAMP WHERE status='active'", (), "contest: закрыть прежние")
    res = _exec(
        "INSERT INTO contests (title, description, prizes, start_date, end_date, min_rub, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'active')",
        (title or '', description or '', prizes or '', start_date, end_date, float(min_rub or 150)),
        "contest: создать")
    return res.lastrowid if res else None


def update_contest(contest_id, **fields) -> bool:
    allowed = {'title', 'description', 'prizes', 'start_date', 'end_date', 'min_rub', 'status', 'winners'}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return False
    params.append(contest_id)
    res = _exec(f"UPDATE contests SET {', '.join(sets)} WHERE id = ?", tuple(params), "contest: обновить")
    return bool(res and res.rowcount)


def finish_contest(contest_id) -> bool:
    res = _exec("UPDATE contests SET status='finished', finished_at=CURRENT_TIMESTAMP WHERE id = ?", (contest_id,), "contest: завершить")
    return bool(res and res.rowcount)


def set_contest_winners(contest_id, winners) -> bool:
    """winners = list[{'place':int,'user_id':int}] или готовая JSON-строка."""
    payload = winners if isinstance(winners, str) else json.dumps(winners, ensure_ascii=False)
    res = _exec("UPDATE contests SET winners = ? WHERE id = ?", (payload, contest_id), "contest: победители")
    return bool(res and res.rowcount)


def get_contest_tickets(user_id: int, contest: dict | None = None) -> int:
    """Билеты партнёра: рефералы, которые ЗАРЕГИСТРИРОВАЛИСЬ по ссылке в период И купили подписку ≥ порога в период."""
    contest = contest or get_active_contest()
    if not contest:
        return 0
    start, end = str(contest.get('start_date')), str(contest.get('end_date'))
    refs = _fetch_list("SELECT telegram_id, registration_date FROM users WHERE referred_by = ?", (user_id,), "contest: рефералы")
    # только рефералы, ЗАРЕГИСТРИРОВАННЫЕ в период (новые приведённые)
    ref_ids = [r['telegram_id'] for r in refs if r.get('registration_date') and start <= str(r['registration_date'])[:10] <= end]
    if not ref_ids:
        return 0
    first = _contest_first_purchase_by_user(ref_ids, contest.get('min_rub'))
    return sum(1 for _uid, day in first.items() if start <= day <= end)


def get_contest_leaderboard(contest: dict | None = None, limit: int = 500) -> list[dict]:
    """Рейтинг партнёров: рефералы, ЗАРЕГИСТРИРОВАННЫЕ по ссылке в период И купившие подписку ≥ порога в период."""
    contest = contest or get_active_contest()
    if not contest:
        return []
    start, end = str(contest.get('start_date')), str(contest.get('end_date'))
    ref_rows = _fetch_list("SELECT telegram_id, referred_by, registration_date FROM users WHERE referred_by IS NOT NULL", (), "contest: рефсвязи")
    # только рефералы, ЗАРЕГИСТРИРОВАННЫЕ в период (новые приведённые)
    ref_to_partner: dict = {}
    for r in ref_rows:
        rd = r.get('registration_date')
        if rd and start <= str(rd)[:10] <= end:
            ref_to_partner[r['telegram_id']] = r['referred_by']
    if not ref_to_partner:
        return []
    first = _contest_first_purchase_by_user(list(ref_to_partner.keys()), contest.get('min_rub'))
    tally: dict = {}
    for uid, day in first.items():
        if start <= day <= end:
            p = ref_to_partner.get(uid)
            if p is not None:
                tally[p] = tally.get(p, 0) + 1
    if not tally:
        return []
    part_ids = list(tally.keys())
    info: dict = {}
    for i in range(0, len(part_ids), 900):
        chunk = part_ids[i:i + 900]
        ph = ",".join("?" for _ in chunk)
        rows = _fetch_list(
            f"SELECT telegram_id, username FROM users WHERE telegram_id IN ({ph})",
            tuple(chunk), "contest: партнёры")
        for r in rows:
            info[r['telegram_id']] = r
    board = [{
        'user_id': pid,
        'username': (info.get(pid, {}) or {}).get('username') or '',
        'tickets': tally[pid],
    } for pid in part_ids]
    board.sort(key=lambda x: (-x['tickets'], x['user_id']))
    return board[:limit]
# ===================================================


# ==========================================================================
# ===== КОЛЕСО ФОРТУНЫ ======================================================
# ==========================================================================
# Дорогие призы (техника/кэш) — display-only (weight=0, НЕ выигрываются, только
# для вида на колесе, чистый маркетинг). Выигрывается ТОЛЬКО VPN-время (weight>0).
# Спины начисляются за покупку/продление по месяцам (1→1, 3→2, 6→3, 12→6).
# Результат спина решает ТОЛЬКО сервер (взвешенный рандом), клиент лишь анимирует.

_WHEEL_DEFAULT_PRIZES = [
    # (label, emoji, kind, amount, weight, sort_order) — kind: vpn_days | vpn_months | display
    ("iPhone 16",     "📱", "display",     0,    0,  1),
    ("VPN 1 день",    "🎁", "vpn_days",    1, 3500,  2),
    ("2 500 ₽",       "💵", "display",     0,    0,  3),
    ("VPN 5 дней",    "🎁", "vpn_days",    5, 1500,  4),
    ("Алиса",         "🔊", "display",     0,    0,  5),
    ("VPN 3 дня",     "🎁", "vpn_days",    3, 2500,  6),
    ("5 000 ₽",       "💵", "display",     0,    0,  7),
    ("VPN 1 месяц",   "🏆", "vpn_months",  1,  200,  8),
    ("10 000 ₽",      "💰", "display",     0,    0,  9),
    ("VPN 7 дней",    "🎁", "vpn_days",    7,  700, 10),
    ("AirPods",       "🎧", "display",     0,    0, 11),
    ("VPN 3 месяца",  "🏆", "vpn_months",  3,   40, 12),
    ("PlayStation 5", "🎮", "display",     0,    0, 13),
    ("VPN 6 месяцев", "🏆", "vpn_months",  6,    8, 14),
    ("1 000 ₽",       "💵", "display",     0,    0, 15),
    ("VPN 1 год",     "👑", "vpn_months", 12,    2, 16),
    ("100 000 ₽",     "💎", "display",     0,    0, 17),
    ("Apple Watch",   "⌚", "display",     0,    0, 18),
    ("Шкатулка",      "🎁", "display",     0,    0, 19),
]


def _ensure_wheel_tables(cursor: sqlite3.Cursor) -> None:
    """Таблицы колеса фортуны + сид дефолтных призов (только если пусто)."""
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wheel_prizes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                emoji TEXT DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'display',
                amount INTEGER DEFAULT 0,
                weight INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wheel_spin_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                prize_id INTEGER,
                prize_label TEXT,
                kind TEXT,
                amount INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_wheel_spin_log_user ON wheel_spin_log(user_id, created_at DESC)")
        cnt = cursor.execute("SELECT COUNT(*) FROM wheel_prizes").fetchone()[0]
        if not cnt:
            cursor.executemany(
                "INSERT INTO wheel_prizes (label, emoji, kind, amount, weight, sort_order) VALUES (?,?,?,?,?,?)",
                _WHEEL_DEFAULT_PRIZES)
    except Exception as e:
        logging.error(f"Не удалось создать таблицы колеса: {e}")


def spins_for_months(months) -> int:
    """Сколько спинов даёт покупка на N месяцев: 12→6, 6→3, 3→2, 1→1."""
    try:
        m = int(months or 0)
    except Exception:
        m = 0
    if m >= 12:
        return 6
    if m >= 6:
        return 3
    if m >= 3:
        return 2
    if m >= 1:
        return 1
    return 0


def add_wheel_spins(user_id: int, n: int) -> bool:
    if not n or int(n) <= 0:
        return False
    res = _exec("UPDATE users SET wheel_spins = COALESCE(wheel_spins,0) + ? WHERE telegram_id = ?", (int(n), user_id), "wheel: начислить спины")
    return bool(res and res.rowcount)


def get_wheel_spins(user_id: int) -> int:
    return int(_fetch_val("SELECT COALESCE(wheel_spins,0) FROM users WHERE telegram_id = ?", (user_id,), 0, "wheel: баланс спинов") or 0)


def get_wheel_prizes() -> list[dict]:
    """Все призы для отрисовки колеса (включая display-only)."""
    return _fetch_list("SELECT * FROM wheel_prizes ORDER BY sort_order, id", (), "wheel: призы")


def spin_wheel(user_id: int) -> dict | None:
    """Списывает 1 спин, взвешенно выбирает выигрышный приз (только weight>0 = VPN), логирует.
    Возвращает приз (dict) или None если спинов нет / нет выигрышных призов. Результат решает сервер."""
    import random
    res = _exec("UPDATE users SET wheel_spins = wheel_spins - 1 WHERE telegram_id = ? AND COALESCE(wheel_spins,0) > 0", (user_id,), "wheel: списать спин")
    if not (res and res.rowcount):
        return None
    prizes = _fetch_list("SELECT * FROM wheel_prizes WHERE weight > 0", (), "wheel: выигрышные призы")
    total = sum(int(p.get('weight') or 0) for p in prizes) if prizes else 0
    if total <= 0:
        _exec("UPDATE users SET wheel_spins = COALESCE(wheel_spins,0) + 1 WHERE telegram_id = ?", (user_id,), "wheel: вернуть спин")
        return None
    r = random.uniform(0, total)
    acc = 0
    chosen = prizes[-1]
    for p in prizes:
        acc += int(p.get('weight') or 0)
        if r <= acc:
            chosen = p
            break
    _exec("INSERT INTO wheel_spin_log (user_id, prize_id, prize_label, kind, amount) VALUES (?,?,?,?,?)",
          (user_id, chosen.get('id'), chosen.get('label', ''), chosen.get('kind', ''), int(chosen.get('amount') or 0)),
          "wheel: лог спина")
    return dict(chosen)


# ---- админка колеса ----
def update_wheel_prize(prize_id, **fields) -> bool:
    allowed = {'label', 'emoji', 'kind', 'amount', 'weight', 'sort_order'}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return False
    params.append(prize_id)
    res = _exec(f"UPDATE wheel_prizes SET {', '.join(sets)} WHERE id = ?", tuple(params), "wheel: обновить приз")
    return bool(res and res.rowcount)


def add_wheel_prize(label, emoji, kind, amount, weight, sort_order=0) -> int | None:
    res = _exec("INSERT INTO wheel_prizes (label, emoji, kind, amount, weight, sort_order) VALUES (?,?,?,?,?,?)",
                (label or '', emoji or '', kind or 'display', int(amount or 0), int(weight or 0), int(sort_order or 0)),
                "wheel: добавить приз")
    return res.lastrowid if res else None


def delete_wheel_prize(prize_id) -> bool:
    res = _exec("DELETE FROM wheel_prizes WHERE id = ?", (prize_id,), "wheel: удалить приз")
    return bool(res and res.rowcount)


def get_wheel_spin_log(limit: int = 200) -> list[dict]:
    return _fetch_list(
        "SELECT l.*, u.username FROM wheel_spin_log l LEFT JOIN users u ON u.telegram_id = l.user_id "
        "ORDER BY l.id DESC LIMIT ?", (int(limit),), "wheel: лог спинов")


def get_last_paid_key_id(user_id: int) -> int | None:
    """key_id из последней оплаченной транзакции юзера (metadata.key_id).
    Нужен колесу фортуны: приз продлевает последний ОПЛАЧЕННЫЙ ключ."""
    rows = _fetch_list(
        "SELECT metadata FROM transactions WHERE user_id = ? AND status = 'paid' "
        "ORDER BY transaction_id DESC LIMIT 50",
        (user_id,),
        f"Не удалось получить оплаченные транзакции {user_id}",
    )
    for row in rows:
        try:
            meta = json.loads(dict(row).get("metadata") or "{}")
        except Exception:
            continue
        kid = meta.get("key_id")
        if kid:
            try:
                return int(kid)
            except Exception:
                continue
    return None
# ===================================================
