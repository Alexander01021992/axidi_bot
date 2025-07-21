import aiosqlite
import uuid
import json
import logging
import time
import os
import pytz
import shutil
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Tuple, Optional, Dict, Any
from functools import wraps
import asyncio
from config import ADMIN_IDS, TARIFFS, CACHE_TTL_SECONDS, DATABASE_PATH, BACKUP_ENABLED, BACKUP_INTERVAL_HOURS
from generation_config import REPLICATE_COSTS
from handlers.utils import safe_escape_markdown, send_message_with_fallback

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class UserCache:
    """Кэш для данных пользователей с TTL"""
    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self.cache: Dict[int, Tuple[Any, float]] = {}
        self.ttl = ttl
        self._lock = asyncio.Lock()
    
    async def get(self, user_id: int) -> Optional[Tuple]:
        async with self._lock:
            if user_id in self.cache:
                data, timestamp = self.cache[user_id]
                if time.time() - timestamp < self.ttl:
                    logger.debug(f"Cache hit for user_id={user_id}")
                    return data
                else:
                    del self.cache[user_id]
                    logger.debug(f"Cache expired for user_id={user_id}")
            return None
    
    async def set(self, user_id: int, data: Tuple):
        async with self._lock:
            self.cache[user_id] = (data, time.time())
            logger.debug(f"Cache set for user_id={user_id}")
    
    async def invalidate(self, user_id: int):
        async with self._lock:
            if user_id in self.cache:
                del self.cache[user_id]
                logger.debug(f"Cache invalidated for user_id={user_id}")
    
    async def clear(self):
        async with self._lock:
            self.cache.clear()
            logger.info("Cache cleared")

user_cache = UserCache()

def invalidate_cache(user_id_param: str = 'user_id'):
    """Декоратор для автоматической инвалидации кэша после изменения данных"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            user_id = kwargs.get(user_id_param)
            if not user_id:
                func_params = func.__code__.co_varnames[:func.__code__.co_argcount]
                if user_id_param in func_params:
                    idx = func_params.index(user_id_param)
                    if idx < len(args):
                        user_id = args[idx]
            
            if user_id:
                await user_cache.invalidate(user_id)
            
            return result
        return wrapper
    return decorator

def retry_on_locked(max_attempts: int = 10, initial_delay: float = 0.5):
    """Декоратор для повторных попыток при ошибке блокировки базы данных"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except aiosqlite.OperationalError as e:
                    if "database is locked" in str(e) and attempt < max_attempts - 1:
                        delay = initial_delay * (2 ** attempt)
                        logger.warning(
                            f"База данных заблокирована в {func.__name__}, попытка {attempt + 1}/{max_attempts}. "
                            f"Повтор через {delay:.2f}с... ⏳"
                        )
                        try:
                            async with aiosqlite.connect(DATABASE_PATH, timeout=5) as conn:
                                c = await conn.cursor()
                                await c.execute("PRAGMA busy_timeout = 30000")  # Увеличен таймаут
                                await c.execute("SELECT COUNT(*) FROM sqlite_master")
                                logger.debug(f"Диагностика: база доступна, попытка {attempt + 1}")
                        except Exception as diag_e:
                            logger.error(f"Диагностика не удалась: {diag_e}")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"Исчерпаны попытки в {func.__name__}: {e} 🚫")
                        raise
            raise aiosqlite.OperationalError("Достигнуто максимальное количество попыток при ошибке блокировки 🚫")
        return wrapper
    return decorator

async def migrate_referral_stats_table(bot: Bot = None):
    """Миграция таблицы referral_stats для добавления столбца total_reward_photos."""
    try:
        async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
            await conn.execute("PRAGMA busy_timeout = 30000")
            c = await conn.cursor()

            # Проверяем текущую схему таблицы
            await c.execute("PRAGMA table_info(referral_stats)")
            columns = {col[1]: {'notnull': col[3]} for col in await c.fetchall()}
            logger.debug(f"Текущая схема referral_stats: {columns}")

            if 'total_reward_photos' not in columns:
                logger.info("Столбец total_reward_photos отсутствует, добавляем его")
                try:
                    await c.execute("ALTER TABLE referral_stats ADD COLUMN total_reward_photos INTEGER DEFAULT 0")
                    await conn.commit()
                    logger.info("Столбец total_reward_photos добавлен в таблицу referral_stats")
                except Exception as e:
                    logger.error(f"Ошибка при добавлении total_reward_photos: {e}", exc_info=True)
                    await conn.rollback()
                    raise
            else:
                logger.info("Столбец total_reward_photos уже существует, миграция не требуется")

            # Проверяем наличие индексов
            await c.execute("CREATE INDEX IF NOT EXISTS idx_referral_stats_user ON referral_stats(user_id)")
            await conn.commit()
            logger.info("Индексы для referral_stats созданы или подтверждены")

    except Exception as e:
        logger.error(f"Критическая ошибка миграции таблицы referral_stats: {e}", exc_info=True)
        if bot:
            for admin_id in ADMIN_IDS:
                try:
                    await send_message_with_fallback(
                        bot, admin_id,
                        safe_escape_markdown(f"🚨 Ошибка миграции таблицы referral_stats: {str(e)}", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e_notify:
                    logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")
        raise

async def init_db(bot: Bot = None) -> None:
    """Инициализирует базу данных, создавая все необходимые таблицы с индексами и выполняя миграции."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            await conn.execute('PRAGMA foreign_keys = ON')
            c = await conn.cursor()

            # Выполняем миграции таблиц
            await migrate_referral_stats_table(bot)

            # Таблица пользователей
            await c.execute('''CREATE TABLE IF NOT EXISTS users (
                                user_id INTEGER PRIMARY KEY,
                                username TEXT,
                                first_name TEXT,
                                generations_left INTEGER DEFAULT 0,
                                avatar_left INTEGER DEFAULT 0,
                                has_trained_model INTEGER DEFAULT 0,
                                is_notified INTEGER DEFAULT 0,
                                first_purchase INTEGER DEFAULT 1,
                                email TEXT,
                                active_avatar_id INTEGER DEFAULT NULL,
                                referrer_id INTEGER DEFAULT NULL,
                                is_blocked INTEGER DEFAULT 0,
                                block_reason TEXT DEFAULT NULL,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                welcome_message_sent INTEGER DEFAULT 0,
                                last_reminder_type TEXT DEFAULT NULL,
                                last_reminder_sent TEXT DEFAULT NULL,
                                FOREIGN KEY (active_avatar_id) REFERENCES user_trainedmodels(avatar_id) ON DELETE SET NULL,
                                FOREIGN KEY (referrer_id) REFERENCES users(user_id) ON DELETE SET NULL
                             )''')

            # Проверка наличия столбцов в users
            await c.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in await c.fetchall()]
            if 'welcome_message_sent' not in columns:
                await c.execute("ALTER TABLE users ADD COLUMN welcome_message_sent INTEGER DEFAULT 0")
                logger.info("Добавлен столбец welcome_message_sent в таблицу users")
            if 'block_reason' not in columns:
                await c.execute("ALTER TABLE users ADD COLUMN block_reason TEXT DEFAULT NULL")
                logger.info("Добавлен столбец block_reason в таблицу users")
            if 'last_reminder_type' not in columns:
                await c.execute("ALTER TABLE users ADD COLUMN last_reminder_type TEXT DEFAULT NULL")
                logger.info("Добавлен столбец last_reminder_type в таблицу users")
            if 'last_reminder_sent' not in columns:
                await c.execute("ALTER TABLE users ADD COLUMN last_reminder_sent TEXT DEFAULT NULL")
                logger.info("Добавлен столбец last_reminder_sent в таблицу users")

            # Таблица referrals
            await c.execute('''CREATE TABLE IF NOT EXISTS referrals (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                referrer_id INTEGER NOT NULL,
                                referred_id INTEGER NOT NULL UNIQUE,
                                status TEXT DEFAULT 'pending',
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                completed_at TIMESTAMP DEFAULT NULL,
                                FOREIGN KEY (referrer_id) REFERENCES users(user_id) ON DELETE CASCADE,
                                FOREIGN KEY (referred_id) REFERENCES users(user_id) ON DELETE CASCADE
                             )''')

            # Миграция для добавления столбца completed_at, если он отсутствует
            await c.execute("PRAGMA table_info(referrals)")
            referral_columns = {col[1]: {'notnull': col[3]} for col in await c.fetchall()}
            logger.debug(f"Текущая схема таблицы referrals: {referral_columns}")
            if 'completed_at' not in referral_columns:
                logger.info("Столбец completed_at отсутствует в таблице referrals, добавляем его")
                try:
                    await c.execute("ALTER TABLE referrals ADD COLUMN completed_at TIMESTAMP DEFAULT NULL")
                    await conn.commit()
                    logger.info("Столбец completed_at успешно добавлен в таблицу referrals")
                except Exception as e:
                    logger.error(f"Ошибка при добавлении столбца completed_at в таблицу referrals: {e}", exc_info=True)
                    await conn.rollback()
                    raise

            # Остальные таблицы (без изменений)
            await c.execute('''CREATE TABLE IF NOT EXISTS referral_rewards (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                referrer_id INTEGER NOT NULL,
                                referred_user_id INTEGER NOT NULL,
                                reward_photos INTEGER NOT NULL,
                                created_at TEXT NOT NULL,
                                FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                                FOREIGN KEY (referred_user_id) REFERENCES users (user_id)
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS referral_stats (
                                user_id INTEGER PRIMARY KEY,
                                total_referrals INTEGER DEFAULT 0,
                                total_reward_photos INTEGER DEFAULT 0,
                                updated_at TEXT,
                                FOREIGN KEY (user_id) REFERENCES users (user_id)
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS user_trainedmodels (
                                avatar_id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER,
                                model_id TEXT,
                                model_version TEXT,
                                status TEXT,
                                prediction_id TEXT UNIQUE,
                                trigger_word TEXT,
                                photo_paths TEXT,
                                training_step TEXT,
                                avatar_name TEXT,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS user_ratings (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER,
                                generation_type TEXT,
                                model_key TEXT,
                                rating INTEGER,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS video_tasks (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER,
                                video_path TEXT,
                                status TEXT DEFAULT 'pending',
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                prediction_id TEXT UNIQUE,
                                model_key TEXT,
                                style_name TEXT,
                                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                             )''')

            # Проверка наличия столбца style_name
            await c.execute("PRAGMA table_info(video_tasks)")
            columns = [col[1] for col in await c.fetchall()]
            if 'style_name' not in columns:
                await c.execute("ALTER TABLE video_tasks ADD COLUMN style_name TEXT")
                logger.info("Добавлен столбец style_name в таблицу video_tasks")

            await c.execute('''CREATE TABLE IF NOT EXISTS payments (
                                payment_id TEXT PRIMARY KEY,
                                user_id INTEGER,
                                plan TEXT,
                                amount REAL,
                                status TEXT DEFAULT 'pending',
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS generation_log (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER,
                                generation_type TEXT,
                                replicate_model_id TEXT,
                                units_generated INTEGER,
                                cost_per_unit REAL,
                                total_cost REAL,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS user_actions (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER,
                                action TEXT,
                                details TEXT,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                scheduled_time TEXT NOT NULL,
                                broadcast_data TEXT NOT NULL,
                                status TEXT DEFAULT 'pending',
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS broadcast_buttons (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                broadcast_id INTEGER NOT NULL,
                                button_text TEXT NOT NULL,
                                callback_data TEXT NOT NULL,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (broadcast_id) REFERENCES scheduled_broadcasts(id) ON DELETE CASCADE
                             )''')

            await c.execute('''CREATE TABLE IF NOT EXISTS fixes (
                                fix_name TEXT PRIMARY KEY,
                                applied INTEGER DEFAULT 0,
                                applied_at TIMESTAMP
                             )''')

            # Индексы
            indices = [
                ('idx_users_active_avatar', 'users(active_avatar_id)'),
                ('idx_users_referrer', 'users(referrer_id)'),
                ('idx_users_blocked', 'users(is_blocked)'),
                ('idx_trainedmodels_user', 'user_trainedmodels(user_id)'),
                ('idx_trainedmodels_status', 'user_trainedmodels(status)'),
                ('idx_trainedmodels_prediction', 'user_trainedmodels(prediction_id)'),
                ('idx_payments_user', 'payments(user_id)'),
                ('idx_payments_created', 'payments(created_at)'),
                ('idx_generation_log_user', 'generation_log(user_id)'),
                ('idx_generation_log_created', 'generation_log(created_at)'),
                ('idx_generation_log_type', 'generation_log(generation_type)'),
                ('idx_video_tasks_user', 'video_tasks(user_id)'),
                ('idx_video_tasks_status', 'video_tasks(status)'),
                ('idx_referrals_referrer', 'referrals(referrer_id)'),
                ('idx_referrals_referred', 'referrals(referred_id)'),
                ('idx_referrals_status', 'referrals(status)'),
                ('idx_user_actions_user', 'user_actions(user_id)'),
                ('idx_user_actions_action', 'user_actions(action)'),
                ('idx_user_actions_created', 'user_actions(created_at)'),
                ('idx_scheduled_broadcasts_schedule', 'scheduled_broadcasts(scheduled_time)'),
                ('idx_referral_rewards_referrer', 'referral_rewards(referrer_id)'),
                ('idx_referral_rewards_referred', 'referral_rewards(referred_user_id)'),
                ('idx_referral_stats_user', 'referral_stats(user_id)'),
                ('idx_broadcast_buttons_broadcast', 'broadcast_buttons(broadcast_id)')
            ]

            for index_name, index_def in indices:
                await c.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON {index_def}')

            # Триггеры
            await c.execute('''CREATE TRIGGER IF NOT EXISTS update_users_updated_at
                              AFTER UPDATE ON users
                              FOR EACH ROW
                              BEGIN
                                  UPDATE users 
                                  SET updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = NEW.user_id;
                              END;''')

            await c.execute('''CREATE TRIGGER IF NOT EXISTS update_trainedmodels_updated_at
                              AFTER UPDATE ON user_trainedmodels
                              FOR EACH ROW
                              BEGIN
                                  UPDATE user_trainedmodels 
                                  SET updated_at = CURRENT_TIMESTAMP 
                                  WHERE avatar_id = NEW.avatar_id;
                              END;''')

            await c.execute('''CREATE TABLE IF NOT EXISTS bot_config (
                                key TEXT PRIMARY KEY,
                                value TEXT,
                                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                             )''')
            await c.execute('''CREATE INDEX IF NOT EXISTS idx_bot_config_key ON bot_config(key)''')

            await conn.commit()
            logger.info("База данных успешно инициализирована с индексами, триггерами и миграцией referrals")
            await backup_database()
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}", exc_info=True)
        if bot:
            for admin_id in ADMIN_IDS:
                try:
                    await send_message_with_fallback(
                        bot, admin_id,
                        safe_escape_markdown(f"🚨 Критическая ошибка инициализации базы данных: {str(e)}", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e_notify:
                    logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")
        raise

@retry_on_locked(max_attempts=15, initial_delay=0.5)
async def save_broadcast_button(broadcast_id: int, button_text: str, callback_data: str, conn=None) -> bool:
    """Сохраняет кнопку рассылки в базу данных."""
    try:
        if conn is None:
            async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
                await conn.execute("PRAGMA busy_timeout = 30000")  # Увеличен таймаут
                c = await conn.cursor()
                # Проверяем существование таблицы
                await c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='broadcast_buttons'")
                if not await c.fetchone():
                    logger.error(f"Таблица broadcast_buttons не существует для broadcast_id={broadcast_id}")
                    return False
                await c.execute(
                    "INSERT INTO broadcast_buttons (broadcast_id, button_text, callback_data, created_at) "
                    "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (broadcast_id, button_text, callback_data)
                )
                await conn.commit()
                logger.info(f"Кнопка сохранена для broadcast_id={broadcast_id}: text={button_text}, callback_data={callback_data}")
                return True
        else:
            c = await conn.cursor()
            # Проверяем существование таблицы
            await c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='broadcast_buttons'")
            if not await c.fetchone():
                logger.error(f"Таблица broadcast_buttons не существует для broadcast_id={broadcast_id}")
                return False
            await c.execute(
                "INSERT INTO broadcast_buttons (broadcast_id, button_text, callback_data, created_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (broadcast_id, button_text, callback_data)
            )
            logger.info(f"Кнопка сохранена для broadcast_id={broadcast_id}: text={button_text}, callback_data={callback_data}")
            return True
    except aiosqlite.OperationalError as e:
        logger.error(f"Ошибка сохранения кнопки для broadcast_id={broadcast_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Неизвестная ошибка сохранения кнопки для broadcast_id={broadcast_id}: {e}", exc_info=True)
        return False

@retry_on_locked(max_attempts=15, initial_delay=0.5)
async def get_broadcast_buttons(broadcast_id: int) -> List[Dict[str, str]]:
    """Получает список кнопок для указанной рассылки."""
    try:
        async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
            await conn.execute("PRAGMA busy_timeout = 30000")  # Увеличен таймаут
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            # Проверяем существование таблицы
            await c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='broadcast_buttons'")
            if not await c.fetchone():
                logger.warning(f"Таблица broadcast_buttons не существует для broadcast_id={broadcast_id}, возвращается пустой список")
                return []
            await c.execute(
                "SELECT button_text, callback_data FROM broadcast_buttons WHERE broadcast_id = ? ORDER BY id",
                (broadcast_id,)
            )
            buttons = await c.fetchall()
            return [{"text": button["button_text"], "callback_data": button["callback_data"]} for button in buttons]
    except aiosqlite.OperationalError as e:
        logger.error(f"Ошибка получения кнопок для broadcast_id={broadcast_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Неизвестная ошибка получения кнопок для broadcast_id={broadcast_id}: {e}", exc_info=True)
        return []

async def backup_database() -> None:
    """Создание резервной копии базы данных"""
    if not BACKUP_ENABLED:
        return
    
    try:
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"users_backup_{timestamp}.db")
        
        shutil.copy2(DATABASE_PATH, backup_path)
        logger.info(f"Database backup created: {backup_path}")
        
        backups = sorted([f for f in os.listdir(backup_dir) if f.endswith('.db')])
        if len(backups) > 7:
            for old_backup in backups[:-7]:
                os.remove(os.path.join(backup_dir, old_backup))
                logger.info(f"Old backup removed: {old_backup}")
                
    except Exception as e:
        logger.error(f"Error creating database backup: {e}", exc_info=True)

async def periodic_backup():
    """Периодическое создание резервных копий БД"""
    while True:
        try:
            await asyncio.sleep(BACKUP_INTERVAL_HOURS * 3600)
            await backup_database()
        except Exception as e:
            logger.error(f"Ошибка в periodic_backup: {e}", exc_info=True)

def start_periodic_tasks():
    """Запускает периодические задачи в фоне"""
    if BACKUP_ENABLED:
        asyncio.create_task(periodic_backup())
        logger.info("Периодическое резервное копирование запущено")

async def add_user(user_id: int, first_name: str, username: str, email: str = "", referrer_id: Optional[int] = None) -> bool:
    """Добавляет нового пользователя (алиас для add_user_without_subscription)"""
    try:
        await add_user_without_subscription(user_id, username, first_name, referrer_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя {user_id}: {e}", exc_info=True)
        return False

@invalidate_cache()
async def add_user_without_subscription(user_id: int, username: str, first_name: str, referrer_id: Optional[int] = None) -> None:
    """Добавляет нового пользователя или обновляет существующего."""
    try:
        async with aiosqlite.connect(DATABASE_PATH, timeout=10) as conn:
            await conn.execute("PRAGMA busy_timeout = 5000")
            c = await conn.cursor()
            
            # Проверка существующего пользователя
            await c.execute("SELECT referrer_id, created_at FROM users WHERE user_id = ?", (user_id,))
            existing_user_data = await c.fetchone()
            
            moscow_tz = pytz.timezone('Europe/Moscow')
            current_timestamp = datetime.now(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')
            
            if existing_user_data:
                current_referrer_id = existing_user_data[0]
                if current_referrer_id is None and referrer_id is not None:
                    await c.execute(
                        """UPDATE users 
                           SET username = ?, first_name = ?, referrer_id = ?, updated_at = ?
                           WHERE user_id = ?""",
                        (username, first_name, referrer_id, current_timestamp, user_id)
                    )
                    logger.info(f"Данные пользователя user_id={user_id} обновлены. Новый referrer_id={referrer_id}.")
                else:
                    await c.execute(
                        """UPDATE users 
                           SET username = ?, first_name = ?, updated_at = ? 
                           WHERE user_id = ?""",
                        (username, first_name, current_timestamp, user_id)
                    )
                    logger.info(f"Данные пользователя user_id={user_id} обновлены (referrer_id не изменен: {current_referrer_id}).")
            else:
                await c.execute(
                    '''INSERT INTO users (
                        user_id, username, first_name, generations_left, avatar_left,
                        is_notified, first_purchase, referrer_id, created_at, updated_at,
                        welcome_message_sent
                    ) VALUES (?, ?, ?, 0, 0, 0, 1, ?, ?, ?, 0)''',
                    (user_id, username, first_name, referrer_id, current_timestamp, current_timestamp)
                )
                logger.info(f"Пользователь user_id={user_id} добавлен с referrer_id={referrer_id}.")
            
            # Реферальная логика
            if referrer_id and referrer_id != user_id:
                await c.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
                referrer_exists = await c.fetchone()
                
                if referrer_exists:
                    try:
                        await c.execute(
                            '''INSERT INTO referrals (
                                referrer_id, referred_id, status, created_at
                            ) VALUES (?, ?, 'pending', ?)
                            ON CONFLICT(referred_id) DO NOTHING''',
                            (referrer_id, user_id, current_timestamp)
                        )
                        if c.rowcount > 0:
                            logger.info(f"Реферальная связь добавлена: referrer_id={referrer_id} -> referred_id={user_id}")
                    except aiosqlite.IntegrityError as e:
                        logger.warning(f"Ошибка добавления реферальной связи для {referrer_id} -> {user_id}: {e}")
                else:
                    logger.warning(f"Реферер ID {referrer_id} не найден в таблице users.")
            
            await conn.commit()
        
        # Логирование действия после завершения транзакции
        if not existing_user_data:
            await log_user_action(user_id, 'start_bot', {'referrer_id': referrer_id})
            if referrer_id and referrer_id != user_id and referrer_exists:
                await log_user_action(user_id, 'use_referral', {'referrer_id': referrer_id})
            
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя {user_id}: {e}", exc_info=True)
        raise

async def get_users_for_welcome_message() -> List[Dict[str, Any]]:
    """Получает пользователей, зарегистрированных более часа назад, без платежей и без отправленного приветственного сообщения."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("""
                SELECT user_id, first_name, username, created_at
                FROM users
                WHERE welcome_message_sent = 0
                AND first_purchase = 1
                AND created_at <= datetime('now', '-5 minutes')
                AND is_blocked = 0
                AND user_id NOT IN (
                    SELECT user_id FROM payments WHERE status = 'succeeded'
                )
            """)
            
            users = await c.fetchall()
            return [
                {
                    'user_id': row['user_id'],
                    'first_name': row['first_name'],
                    'username': row['username'],
                    'created_at': row['created_at']
                }
                for row in users
            ]
            
    except Exception as e:
        logger.error(f"Ошибка получения пользователей для приветственного сообщения: {e}", exc_info=True)
        return []

@invalidate_cache()
async def mark_welcome_message_sent(user_id: int) -> bool:
    """Отмечает, что приветственное сообщение было отправлено пользователю."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("""
                UPDATE users
                SET welcome_message_sent = 1, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (user_id,))
            
            await conn.commit()
            
            if c.rowcount > 0:
                logger.info(f"Приветственное сообщение отмечено как отправленное для user_id={user_id}")
                return True
            else:
                logger.warning(f"Не удалось отметить отправку сообщения для user_id={user_id}")
                return False
                
    except Exception as e:
        logger.error(f"Ошибка отметки отправки приветственного сообщения для user_id={user_id}: {e}", exc_info=True)
        return False

async def add_user_resources(user_id: int, photos: int, avatars: int) -> bool:
    """Добавляет ресурсы пользователю (фото и аватары)"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if not await c.fetchone():
                logger.warning(f"Попытка добавить ресурсы несуществующему пользователю {user_id}")
                return False
            
            await c.execute("""
                UPDATE users 
                SET generations_left = generations_left + ?, 
                    avatar_left = avatar_left + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (photos, avatars, user_id))
            
            await conn.commit()
            
            if c.rowcount > 0:
                logger.info(f"Ресурсы добавлены пользователю {user_id}: +{photos} фото, +{avatars} аватаров")
                await user_cache.invalidate(user_id)
                return True
            
            return False
            
    except Exception as e:
        logger.error(f"Ошибка добавления ресурсов пользователю {user_id}: {e}", exc_info=True)
        return False

async def get_user_info(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает полную информацию о пользователе"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT user_id, username, first_name, email, generations_left, avatar_left,
                              has_trained_model, is_notified, first_purchase, active_avatar_id,
                              referrer_id, is_blocked, block_reason, created_at, updated_at
                              FROM users 
                              WHERE user_id = ?''',
                           (user_id,))
            
            result = await c.fetchone()
            if result:
                return dict(result)
            
            return None
            
    except Exception as e:
        logger.error(f"Ошибка получения информации о пользователе {user_id}: {e}", exc_info=True)
        return None

async def add_payment_log(user_id: int, payment_id: str, amount: float, payment_info: Dict[str, Any]) -> bool:
    """Добавляет запись о платеже в логи"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("""
                CREATE TABLE IF NOT EXISTS payment_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    payment_id TEXT NOT NULL UNIQUE,
                    amount REAL NOT NULL,
                    payment_info TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            
            current_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            payment_info_json = json.dumps(payment_info, ensure_ascii=False)
            
            await c.execute("""
                INSERT OR IGNORE INTO payment_logs (user_id, payment_id, amount, payment_info, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, payment_id, amount, payment_info_json, current_timestamp))
            
            await conn.commit()
            
            if c.rowcount > 0:
                logger.info(f"Платеж записан в логи: user_id={user_id}, payment_id={payment_id}, amount={amount}")
                return True
            else:
                logger.warning(f"Платеж уже существует в логах: payment_id={payment_id}")
                return True
                
    except Exception as e:
        logger.error(f"Ошибка записи платежа в логи: {e}", exc_info=True)
        return False

async def update_user_payment_stats(user_id: int, payment_amount: float) -> bool:
    """Обновляет статистику платежей пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("""
                CREATE TABLE IF NOT EXISTS user_payment_stats (
                    user_id INTEGER PRIMARY KEY,
                    total_payments INTEGER DEFAULT 0,
                    total_amount REAL DEFAULT 0.0,
                    first_payment_date TEXT,
                    last_payment_date TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            
            current_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            
            await c.execute("""
                SELECT total_payments, total_amount, first_payment_date 
                FROM user_payment_stats 
                WHERE user_id = ?
            """, (user_id,))
            
            existing_stats = await c.fetchone()
            
            if existing_stats:
                new_total_payments = existing_stats[0] + 1
                new_total_amount = existing_stats[1] + payment_amount
                first_payment_date = existing_stats[2]
                
                await c.execute("""
                    UPDATE user_payment_stats 
                    SET total_payments = ?, total_amount = ?, last_payment_date = ?
                    WHERE user_id = ?
                """, (new_total_payments, new_total_amount, current_timestamp, user_id))
            else:
                await c.execute("""
                    INSERT INTO user_payment_stats 
                    (user_id, total_payments, total_amount, first_payment_date, last_payment_date)
                    VALUES (?, 1, ?, ?, ?)
                """, (user_id, payment_amount, current_timestamp, current_timestamp))
            
            await conn.commit()
            logger.info(f"Статистика платежей обновлена для user_id={user_id}")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка обновления статистики платежей для user_id={user_id}: {e}", exc_info=True)
        return False

async def get_user_payment_count(user_id: int) -> int:
    """Получает количество платежей пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("""
                SELECT total_payments 
                FROM user_payment_stats 
                WHERE user_id = ?
            """, (user_id,))
            
            result = await c.fetchone()
            if result:
                return result[0]
            
            await c.execute("""
                SELECT COUNT(*) 
                FROM payments 
                WHERE user_id = ? AND status = 'succeeded'
            """, (user_id,))
            
            result = await c.fetchone()
            return result[0] if result else 0
            
    except Exception as e:
        logger.error(f"Ошибка получения количества платежей для user_id={user_id}: {e}", exc_info=True)
        return 0

async def get_referrer_info(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает информацию о реферере пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("""
                SELECT u.referrer_id, ref.username as referrer_username, ref.first_name as referrer_name
                FROM users u
                LEFT JOIN users ref ON u.referrer_id = ref.user_id
                WHERE u.user_id = ?
            """, (user_id,))
            
            result = await c.fetchone()
            if result and result['referrer_id']:
                return {
                    'referrer_id': result['referrer_id'],
                    'referrer_username': result['referrer_username'],
                    'referrer_name': result['referrer_name']
                }
            
            return None
            
    except Exception as e:
        logger.error(f"Ошибка получения информации о реферере для user_id={user_id}: {e}", exc_info=True)
        return None

@retry_on_locked(max_attempts=10, initial_delay=0.5)
@invalidate_cache()
async def add_referral_reward(referrer_id: int, referred_user_id: int, reward_amount: float) -> bool:
    """Добавляет реферальное вознаграждение в виде фото."""
    try:
        async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
            await conn.execute("PRAGMA busy_timeout = 30000")
            c = await conn.cursor()
            
            await c.execute('''CREATE TABLE IF NOT EXISTS referral_rewards (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                referrer_id INTEGER NOT NULL,
                                referred_user_id INTEGER NOT NULL,
                                reward_photos INTEGER NOT NULL,
                                created_at TEXT NOT NULL,
                                FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                                FOREIGN KEY (referred_user_id) REFERENCES users (user_id)
                             )''')
            
            await c.execute("PRAGMA table_info(referral_rewards)")
            columns = [col[1] for col in await c.fetchall()]
            if 'reward_photos' not in columns:
                await c.execute("ALTER TABLE referral_rewards ADD COLUMN reward_photos INTEGER NOT NULL DEFAULT 0")
                logger.info("Добавлен столбец reward_photos в таблицу referral_rewards")
            
            current_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            
            await c.execute("SELECT id FROM referrals WHERE referrer_id = ? AND referred_id = ?", (referrer_id, referred_user_id))
            if not await c.fetchone():
                logger.warning(f"Реферальная связь для referrer_id={referrer_id}, referred_id={referred_user_id} не найдена, создается новая")
                await c.execute(
                    '''INSERT INTO referrals (referrer_id, referred_id, status, created_at)
                       VALUES (?, ?, 'pending', ?)''',
                    (referrer_id, referred_user_id, current_timestamp)
                )
            
            await c.execute('''INSERT INTO referral_rewards (referrer_id, referred_user_id, reward_photos, created_at)
                              VALUES (?, ?, ?, ?)''',
                           (referrer_id, referred_user_id, int(reward_amount), current_timestamp))
            
            await c.execute('''CREATE TABLE IF NOT EXISTS referral_stats (
                                user_id INTEGER PRIMARY KEY,
                                total_referrals INTEGER DEFAULT 0,
                                total_reward_photos INTEGER DEFAULT 0,
                                updated_at TEXT,
                                FOREIGN KEY (user_id) REFERENCES users (user_id)
                             )''')
            
            await c.execute('''INSERT OR REPLACE INTO referral_stats (user_id, total_referrals, total_reward_photos, updated_at)
                              VALUES (
                                  ?, 
                                  COALESCE((SELECT total_referrals FROM referral_stats WHERE user_id = ?), 0) + 1,
                                  COALESCE((SELECT total_reward_photos FROM referral_stats WHERE user_id = ?), 0) + ?,
                                  ?)''',
                           (referrer_id, referrer_id, referrer_id, int(reward_amount), current_timestamp))
            
            await conn.commit()
            
            logger.info(f"Реферальное вознаграждение добавлено: referrer_id={referrer_id}, "
                       f"referred_user_id={referred_user_id}, photos={reward_amount}")
            
            await user_cache.invalidate(referrer_id)
            return True
            
    except Exception as e:
        logger.error(f"Ошибка добавления реферального вознаграждения: {e}", exc_info=True)
        return False

async def get_user_detailed_stats(user_id: int) -> Dict[str, Any]:
    """Получает детальную статистику пользователя для админки"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("""
                SELECT u.*, 
                       (SELECT COUNT(*) FROM referrals WHERE referrer_id = u.user_id AND status = 'completed') as referrals_count,
                       (SELECT COUNT(*) FROM payments WHERE user_id = u.user_id AND status = 'succeeded') as payments_count,
                       (SELECT SUM(amount) FROM payments WHERE user_id = u.user_id AND status = 'succeeded') as total_spent,
                       (SELECT COUNT(*) FROM user_trainedmodels WHERE user_id = u.user_id) as total_avatars,
                       (SELECT COUNT(*) FROM user_trainedmodels WHERE user_id = u.user_id AND status = 'success') as successful_avatars
                FROM users u
                WHERE u.user_id = ?
            """, (user_id,))
            user_info = await c.fetchone()
            
            if not user_info:
                return None
            
            await c.execute("""
                SELECT generation_type, SUM(units_generated) as total_units, COUNT(*) as count
                FROM generation_log 
                WHERE user_id = ? 
                GROUP BY generation_type
            """, (user_id,))
            generation_stats = await c.fetchall()
            
            await c.execute("""
                SELECT payment_id, plan, amount, created_at 
                FROM payments 
                WHERE user_id = ? AND status = 'succeeded'
                ORDER BY created_at DESC 
                LIMIT 10
            """, (user_id,))
            recent_payments = await c.fetchall()
            
            await c.execute("""
                SELECT avatar_id, avatar_name, status, trigger_word, created_at, updated_at
                FROM user_trainedmodels 
                WHERE user_id = ? 
                ORDER BY avatar_id DESC
            """, (user_id,))
            avatars = await c.fetchall()
            
            await c.execute("""
                SELECT referred_id, created_at, completed_at 
                FROM referrals 
                WHERE referrer_id = ? 
                ORDER BY created_at DESC
            """, (user_id,))
            referrals = await c.fetchall()
            
            return {
                'user_info': dict(user_info),
                'generation_stats': [dict(row) for row in generation_stats],
                'recent_payments': [dict(row) for row in recent_payments],
                'avatars': [dict(row) for row in avatars],
                'referrals': [dict(row) for row in referrals]
            }
            
    except Exception as e:
        logger.error(f"Ошибка получения детальной статистики для user_id={user_id}: {e}", exc_info=True)
        return None

async def get_paid_users() -> List[int]:
    """Возвращает список ID пользователей, совершивших хотя бы один платёж."""
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        c = await conn.cursor()
        await c.execute("""
            SELECT DISTINCT user_id 
            FROM payments 
            WHERE status = 'succeeded'
        """)
        return [row[0] for row in await c.fetchall()]

async def get_non_paid_users() -> List[int]:
    """Возвращает список ID пользователей, не совершивших платежей."""
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        c = await conn.cursor()
        await c.execute("""
            SELECT user_id 
            FROM users 
            WHERE user_id NOT IN (
                SELECT DISTINCT user_id 
                FROM payments 
                WHERE status = 'succeeded'
            )
        """)
        return [row[0] for row in await c.fetchall()]

async def debug_user_payment_state(user_id: int) -> Dict[str, Any]:
    """Отладочная функция для проверки состояния платежей пользователя."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("""
                SELECT user_id, username, first_name, generations_left, avatar_left, 
                       first_purchase, referrer_id, created_at as registration_date
                FROM users WHERE user_id = ?
            """, (user_id,))
            user_data = await c.fetchone()
            
            if not user_data:
                return {"error": f"User {user_id} not found"}
            
            await c.execute("""
                SELECT payment_id, plan, amount, status, created_at
                FROM payments 
                WHERE user_id = ? 
                ORDER BY created_at DESC
            """, (user_id,))
            payments = await c.fetchall()
            
            await c.execute("""
                SELECT referrer_id, referred_id, status, created_at, completed_at
                FROM referrals
                WHERE referrer_id = ? OR referred_id = ?
            """, (user_id, user_id))
            referrals = await c.fetchall()
            
            return {
                "user": dict(user_data),
                "payments": [dict(p) for p in payments],
                "referrals": [dict(r) for r in referrals],
                "payment_count": len(payments),
                "is_first_purchase": bool(user_data['first_purchase'])
            }
            
    except Exception as e:
        logger.error(f"Error in debug_user_payment_state: {e}", exc_info=True)
        return {"error": str(e)}

async def get_referrer(referred_id: int) -> Optional[int]:
    """Получает ID реферера для пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT r.referrer_id
                              FROM referrals r
                              JOIN users u ON r.referred_id = u.user_id
                              WHERE r.referred_id = ? AND r.status = 'pending' AND u.first_purchase = 1''',
                            (referred_id,))
            result = await c.fetchone()
            
            return result['referrer_id'] if result else None
            
    except Exception as e:
        logger.error(f"Ошибка получения реферера для referred_id={referred_id}: {e}", exc_info=True)
        return None

@retry_on_locked(max_attempts=10, initial_delay=0.5)
@invalidate_cache('referrer_id')
@invalidate_cache('referred_id')
async def update_referral_status(referrer_id: int, referred_id: int, status: str) -> bool:
    """Обновляет статус реферальной связи."""
    try:
        async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
            await conn.execute("PRAGMA busy_timeout = 30000")  # Увеличен таймаут
            c = await conn.cursor()
            
            completed_at = 'CURRENT_TIMESTAMP' if status == 'completed' else 'NULL'
            await c.execute(f'''UPDATE referrals 
                               SET status = ?, completed_at = {completed_at}
                               WHERE referrer_id = ? AND referred_id = ?''',
                           (status, referrer_id, referred_id))
            
            if c.rowcount == 0:
                logger.warning(f"Реферальная связь для referrer_id={referrer_id}, referred_id={referred_id} не найдена")
                return False
            
            await conn.commit()
            logger.info(f"Статус реферала обновлён: referrer_id={referrer_id}, referred_id={referred_id}, status={status}")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка обновления статуса реферала: {e}", exc_info=True)
        return False

async def add_rating(user_id: int, generation_type: str, model_key: str, rating: int) -> None:
    """Добавляет оценку от пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute('''INSERT INTO user_ratings (user_id, generation_type, model_key, rating) 
                              VALUES (?, ?, ?, ?)''',
                            (user_id, generation_type, model_key, rating))
            
            await conn.commit()
            logger.info(f"Оценка {rating} добавлена для user_id={user_id}, type={generation_type}")
            
            await log_user_action(user_id, 'rate_generation', {
                'generation_type': generation_type,
                'model_key': model_key,
                'rating': rating
            })
            
    except Exception as e:
        logger.error(f"Ошибка добавления оценки для user_id={user_id}: {e}", exc_info=True)
        raise

async def check_database_user(user_id: int) -> Tuple[int, int, int, Optional[str], int, int, Optional[str], Optional[int], Optional[str], int, Optional[str]]:
    """Проверяет подписку пользователя"""
    cached_data = await user_cache.get(user_id)
    if cached_data and len(cached_data) >= 11:  # Обновляем проверку на 11 элементов
        logger.debug(f"Кэш использован для check_database_user user_id={user_id}: {cached_data}")
        return cached_data
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute('''SELECT generations_left, avatar_left, has_trained_model, username, is_notified,
                              first_purchase, email, active_avatar_id, first_name, is_blocked, created_at
                              FROM users WHERE user_id = ?''',
                            (user_id,))
            result = await c.fetchone()
            if result:
                data = (
                    result['generations_left'] or 0,
                    result['avatar_left'] or 0,
                    int(result['has_trained_model'] or 0),
                    result['username'],
                    int(result['is_notified'] or 0),
                    int(result['first_purchase'] or 1),
                    result['email'],
                    result['active_avatar_id'],
                    result['first_name'],
                    int(result['is_blocked'] or 0),
                    result['created_at']
                )
                await user_cache.set(user_id, data)
                logger.debug(f"Данные подписки для user_id={user_id}: {data}")
                return data
            logger.warning(f"Пользователь user_id={user_id} не найден, возвращаются значения по умолчанию")
            data = (0, 0, 0, None, 0, 1, None, None, None, 0, None)
            await user_cache.set(user_id, data)
            return data
    except Exception as e:
        logger.error(f"Ошибка в check_database_user для user_id={user_id}: {str(e)}", exc_info=True)
        data = (0, 0, 0, None, 0, 1, None, None, None, 0, None)
        await user_cache.set(user_id, data)
        return data

@invalidate_cache()
async def update_user_credits(user_id: int, action: str, amount: int = 1, email: Optional[str] = None) -> bool:
    """Обновляет ресурсы пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("SELECT generations_left, avatar_left FROM users WHERE user_id = ?", (user_id,))
            current_resources = await c.fetchone()
            
            if not current_resources:
                logger.warning(f"Попытка обновить ресурсы для несуществующего user_id={user_id}")
                return False
            
            generations_left, avatar_left = current_resources
            
            if action == "decrement_photo":
                if generations_left >= amount:
                    await c.execute('''UPDATE users 
                                      SET generations_left = generations_left - ?, updated_at = CURRENT_TIMESTAMP 
                                      WHERE user_id = ?''',
                                    (amount, user_id))
                else:
                    logger.warning(f"Недостаточно фото для списания у user_id={user_id}: есть {generations_left}, нужно {amount}")
                    return False
                    
            elif action == "increment_photo":
                await c.execute('''UPDATE users 
                                  SET generations_left = generations_left + ?, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (amount, user_id))
                                
            elif action == "decrement_avatar":
                if avatar_left >= amount:
                    await c.execute('''UPDATE users 
                                      SET avatar_left = avatar_left - ?, updated_at = CURRENT_TIMESTAMP 
                                      WHERE user_id = ?''',
                                    (amount, user_id))
                else:
                    logger.warning(f"Недостаточно аватаров для списания у user_id={user_id}: есть {avatar_left}, нужно {amount}")
                    return False
                    
            elif action == "increment_avatar":
                await c.execute('''UPDATE users 
                                  SET avatar_left = avatar_left + ?, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (amount, user_id))
                                
            elif action == "set_trained_model":
                await c.execute('''UPDATE users 
                                  SET has_trained_model = ?, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (amount, user_id))
                                
            elif action == "reset_avatar":
                await c.execute('''UPDATE users 
                                  SET has_trained_model = 0, active_avatar_id = NULL, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (user_id,))
                await c.execute('''DELETE FROM user_trainedmodels 
                                  WHERE user_id = ?''',
                                (user_id,))
                logger.info(f"Все аватары сброшены для user_id={user_id}")
                
            elif action == "set_notified":
                await c.execute('''UPDATE users 
                                  SET is_notified = ?, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (amount, user_id))
                                
            elif action == "set_first_purchase_completed":
                await c.execute('''UPDATE users 
                                  SET first_purchase = 0, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (user_id,))
                                
            elif action == "set_active_avatar":
                await c.execute('''UPDATE users 
                                  SET active_avatar_id = ?, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (amount if amount else None, user_id))
                                
            elif action == "update_email" and email:
                await c.execute('''UPDATE users 
                                  SET email = ?, updated_at = CURRENT_TIMESTAMP 
                                  WHERE user_id = ?''',
                                (email, user_id))
            else:
                logger.warning(f"Неизвестное действие '{action}' или неверные параметры для user_id={user_id}")
                return False
                
            await conn.commit()
            
            logger.info(f"Ресурсы обновлены для user_id={user_id}, action={action}, amount/value={amount if action != 'update_email' else email}")
            return c.rowcount > 0
            
    except Exception as e:
        logger.error(f"Ошибка обновления ресурсов для user_id={user_id}: {e}", exc_info=True)
        return False

@invalidate_cache()
async def update_user_balance(user_id: int, photos: int, avatars: int, operation: str = 'add') -> bool:
    """Обновляет баланс пользователя (фото и аватары)"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("SELECT generations_left, avatar_left FROM users WHERE user_id = ?", (user_id,))
            current_resources = await c.fetchone()
            
            if not current_resources:
                logger.warning(f"Попытка обновить баланс для несуществующего user_id={user_id}")
                return False
            
            generations_left, avatar_left = current_resources
            
            if operation == 'add':
                new_photos = generations_left + photos
                new_avatars = avatar_left + avatars
            else:  # subtract
                new_photos = max(0, generations_left - photos)
                new_avatars = max(0, avatar_left - avatars)
                if photos > generations_left or avatars > avatar_left:
                    logger.warning(f"Недостаточно ресурсов для списания у user_id={user_id}: "
                                   f"фото={generations_left}/{photos}, аватары={avatar_left}/{avatars}")
                    return False
            
            await c.execute('''UPDATE users 
                              SET generations_left = ?, avatar_left = ?, updated_at = CURRENT_TIMESTAMP 
                              WHERE user_id = ?''',
                            (new_photos, new_avatars, user_id))
            
            await conn.commit()
            logger.info(f"Баланс обновлен для user_id={user_id}: "
                        f"фото={new_photos}, аватары={new_avatars}, операция={operation}")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка обновления баланса для user_id={user_id}: {e}", exc_info=True)
        return False

async def get_user_activity_metrics(start_date: str, end_date: str) -> List[Tuple[int, str, int, int, int, int]]:
    """Получает статистику активности пользователей за указанный период"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT 
                                 u.user_id, 
                                 u.username,
                                 (SELECT COUNT(*) FROM user_actions ua WHERE ua.user_id = u.user_id 
                                  AND ua.action = 'send_message' 
                                  AND ua.created_at BETWEEN ? AND ?) as messages_count,
                                 (SELECT SUM(units_generated) FROM generation_log gl 
                                  WHERE gl.user_id = u.user_id AND gl.generation_type = 'with_avatar'
                                  AND gl.created_at BETWEEN ? AND ?) as photo_generations,
                                 (SELECT SUM(units_generated) FROM generation_log gl 
                                  WHERE gl.user_id = u.user_id AND gl.generation_type = 'ai_video_v2_1'
                                  AND gl.created_at BETWEEN ? AND ?) as video_generations,
                                 (SELECT COUNT(*) FROM payments p WHERE p.user_id = u.user_id 
                                  AND p.status = 'succeeded' AND p.created_at BETWEEN ? AND ?) as purchases_count
                              FROM users u
                              WHERE EXISTS (
                                  SELECT 1 FROM user_actions ua WHERE ua.user_id = u.user_id 
                                  AND ua.created_at BETWEEN ? AND ?
                              )
                              ORDER BY messages_count DESC, photo_generations DESC
                              LIMIT 100''',
                           (start_date, end_date, start_date, end_date, start_date, end_date, 
                            start_date, end_date, start_date, end_date))
            
            results = await c.fetchall()
            return [
                (
                    row['user_id'], row['username'], row['messages_count'], 
                    row['photo_generations'] or 0, row['video_generations'] or 0, row['purchases_count']
                )
                for row in results
            ]
            
    except Exception as e:
        logger.error(f"Ошибка получения статистики активности за {start_date} - {end_date}: {e}", exc_info=True)
        return []

async def get_referral_stats() -> Dict[str, Any]:
    """Получает статистику реферальной программы"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("SELECT COUNT(*) as total FROM referrals")
            total_referrals = (await c.fetchone())['total']
            
            await c.execute("SELECT COUNT(*) as paid FROM referrals WHERE status = 'completed'")
            paid_referrals = (await c.fetchone())['paid']
            
            await c.execute('''SELECT r.referrer_id, u.username, COUNT(*) as referral_count
                              FROM referrals r
                              JOIN users u ON r.referrer_id = u.user_id
                              GROUP BY r.referrer_id
                              ORDER BY referral_count DESC
                              LIMIT 10''')
            top_referrers = [(row['referrer_id'], row['username'], row['referral_count']) for row in await c.fetchall()]
            
            return {
                'total_referrals': total_referrals,
                'paid_referrals': paid_referrals,
                'top_referrers': top_referrers
            }
            
    except Exception as e:
        logger.error(f"Ошибка получения реферальной статистики: {e}", exc_info=True)
        return {}

async def get_user_logs(user_id: int, limit: int = 50) -> List[Tuple[str, str, str]]:
    """Получает логи действий пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT created_at, action, details
                              FROM user_actions
                              WHERE user_id = ?
                              ORDER BY created_at DESC
                              LIMIT ?''',
                           (user_id, limit))
            logs = await c.fetchall()
            
            return [(row['created_at'], row['action'], row['details']) for row in logs]
            
    except Exception as e:
        logger.error(f"Ошибка получения логов для user_id={user_id}: {e}", exc_info=True)
        return []

async def get_scheduled_broadcasts(bot: Bot = None) -> List[Dict]:
    
    from handlers.utils import safe_escape_markdown, send_message_with_fallback
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            msk_tz = pytz.timezone('Europe/Moscow')
            current_time = (datetime.now(msk_tz) + timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')
            logger.debug(f"Fetching broadcasts with scheduled_time <= {current_time} (MSK)")
            
            await c.execute('SELECT id, scheduled_time, status, broadcast_data FROM scheduled_broadcasts')
            all_rows = await c.fetchall()
            logger.debug(f"Все записи в scheduled_broadcasts: {[dict(row) for row in all_rows]}")
            
            await c.execute('''
                SELECT id, scheduled_time, broadcast_data, status
                FROM scheduled_broadcasts
                WHERE status = 'pending' AND scheduled_time <= ?
                ORDER BY scheduled_time ASC
            ''', (current_time,))
            rows = await c.fetchall()
            
            broadcasts = []
            skipped_broadcasts = []
            for row in rows:
                try:
                    if not isinstance(row['id'], int) or row['id'] <= 0:
                        logger.error(f"Некорректный ID рассылки: {row['id']}")
                        continue
                    
                    try:
                        scheduled_dt = datetime.strptime(row['scheduled_time'], '%Y-%m-%d %H:%M:%S')
                        scheduled_dt = msk_tz.localize(scheduled_dt)
                        current_dt = datetime.now(msk_tz)
                        if not (current_dt.replace(second=0, microsecond=0) <= scheduled_dt < (current_dt + timedelta(minutes=1)).replace(second=0, microsecond=0)):
                            logger.debug(f"Рассылка ID {row['id']} пропущена: scheduled_time {row['scheduled_time']} вне текущей минуты")
                            skipped_broadcasts.append((row['id'], row['scheduled_time']))
                            continue
                    except ValueError as e:
                        logger.warning(f"Некорректный формат scheduled_time для ID {row['id']}: {row['scheduled_time']}")
                        if bot:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await send_message_with_fallback(
                                        bot, admin_id,
                                        safe_escape_markdown(f"🚨 Некорректный формат scheduled_time для рассылки ID {row['id']}: {row['scheduled_time']}", version=2),
                                        parse_mode=ParseMode.MARKDOWN_V2
                                    )
                                except Exception as e_notify:
                                    logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")
                        continue
                    
                    broadcast_data = json.loads(row['broadcast_data'])
                    message_text = broadcast_data.get('message', '')
                    media = broadcast_data.get('media', None)
                    media_type = media.get('type') if media else None
                    media_id = media.get('file_id') if media else None
                    target_group = broadcast_data.get('broadcast_type', 'all')
                    admin_user_id = broadcast_data.get('admin_user_id', ADMIN_IDS[0])
                    criteria = broadcast_data.get('criteria', None)
                    scheduled_time = row['scheduled_time']
                    
                    if not scheduled_time:
                        logger.warning(f"Рассылка ID {row['id']} пропущена: отсутствует scheduled_time")
                        continue
                    
                    broadcasts.append({
                        'id': row['id'],
                        'message_text': message_text,
                        'media_type': media_type,
                        'media_id': media_id,
                        'target_group': target_group,
                        'admin_user_id': admin_user_id,
                        'criteria': criteria,
                        'scheduled_time': scheduled_time,
                        'broadcast_data': broadcast_data
                    })
                except json.JSONDecodeError as je:
                    logger.error(f"Ошибка парсинга broadcast_data для ID {row['id']}: {je}", exc_info=True)
                    continue
                except Exception as e:
                    logger.error(f"Ошибка обработки рассылки ID {row['id']}: {e}", exc_info=True)
                    continue
            
            logger.info(f"Получено {len(broadcasts)} запланированных рассылок")
            if skipped_broadcasts:
                logger.info(f"Пропущено {len(skipped_broadcasts)} рассылок из-за времени: {skipped_broadcasts}")
            
            if not broadcasts and any(row['status'] == 'pending' for row in all_rows) and bot:
                await c.execute("SELECT value FROM bot_config WHERE key = 'last_broadcast_warning_time'")
                last_warning_row = await c.fetchone()
                last_warning = datetime.strptime(last_warning_row[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=msk_tz) if last_warning_row else None
                
                current_time_dt = datetime.now(msk_tz)
                if not last_warning or (current_time_dt - last_warning).total_seconds() >= 1200:
                    logger.warning(f"Запланированные рассылки есть, но не найдены из-за времени: {[(row['id'], row['scheduled_time']) for row in all_rows if row['status'] == 'pending']}")
                    await c.execute(
                        "INSERT OR REPLACE INTO bot_config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                        ('last_broadcast_warning_time', current_time_dt.strftime('%Y-%m-%d %H:%M:%S'))
                    )
                    await conn.commit()
                    
                    for admin_id in ADMIN_IDS:
                        try:
                            message_text = safe_escape_markdown(
                                f"⚠️ Запланированные рассылки есть, но не найдены (scheduled_time > {current_time}). "
                                f"Ожидают выполнения в будущем: {[(row['id'], row['scheduled_time']) for row in all_rows if row['status'] == 'pending']}",
                                version=2
                            )
                            await send_message_with_fallback(
                                bot, admin_id,
                                message_text,
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        except Exception as e_notify:
                            logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")
            
            return broadcasts
            
    except Exception as e:
        logger.error(f"Ошибка получения запланированных рассылок: {e}", exc_info=True)
        if bot:
            for admin_id in ADMIN_IDS:
                try:
                    await send_message_with_fallback(
                        bot, admin_id,
                        safe_escape_markdown(f"🚨 Ошибка получения запланированных рассылок: {str(e)}", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e_notify:
                    logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")
        return []

@retry_on_locked(max_attempts=15, initial_delay=0.5)
@invalidate_cache()
async def add_resources_on_payment(user_id: int, plan_key: str, payment_amount: float, payment_id_yookassa: str, bot: Bot = None, is_first_purchase: bool = None) -> bool:
    """Добавляет ресурсы пользователю после оплаты."""
    try:
        async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
            await conn.execute("PRAGMA busy_timeout = 30000")
            c = await conn.cursor()

            user_data = await check_database_user(user_id)
            if not user_data:
                logger.error(f"Пользователь user_id={user_id} не найден")
                return False

            generations_left = user_data[0]
            avatar_left = user_data[1]
            
            # Если is_first_purchase не передан явно, определяем его
            if is_first_purchase is None:
                # Проверяем реальное количество платежей
                await c.execute("""
                    SELECT COUNT(*) FROM payments 
                    WHERE user_id = ? AND status = 'succeeded' AND payment_id != ?
                """, (user_id, payment_id_yookassa))
                payment_count = (await c.fetchone())[0]
                is_first_purchase = (payment_count == 0)
                logger.info(f"Определено is_first_purchase={is_first_purchase} для user_id={user_id}, предыдущих платежей: {payment_count}")
            
            referrer_info = await get_referrer_info(user_id)
            referrer_id = referrer_info['referrer_id'] if referrer_info else None
            logger.debug(f"add_resources_on_payment: user_id={user_id}, is_first_purchase={is_first_purchase}, referrer_id={referrer_id}")

            tariff_info = TARIFFS.get(plan_key, {})
            photos_to_add = tariff_info.get('photos', 0)
            avatars_to_add = tariff_info.get('avatars', 0)

            bonus_avatar = False
            
            # Бонусный аватар только при первой покупке любого пакета (кроме пакета "аватар")
            if is_first_purchase and plan_key != 'аватар':
                avatars_to_add += 1
                bonus_avatar = True
                logger.info(f"Добавлен бонусный аватар для user_id={user_id} (первая покупка)")
            else:
                logger.info(f"Бонусный аватар НЕ добавлен для user_id={user_id}: is_first_purchase={is_first_purchase}, plan_key={plan_key}")

            new_generations = generations_left + photos_to_add
            new_avatars = avatar_left + avatars_to_add

            await c.execute('''UPDATE users
                              SET generations_left = ?, avatar_left = ?, first_purchase = 0, updated_at = CURRENT_TIMESTAMP
                              WHERE user_id = ?''',
                           (new_generations, new_avatars, user_id))

            await c.execute('''INSERT INTO payments (payment_id, user_id, plan, amount, status, created_at)
                              VALUES (?, ?, ?, ?, 'succeeded', CURRENT_TIMESTAMP)
                              ON CONFLICT(payment_id) DO NOTHING''',
                           (payment_id_yookassa, user_id, plan_key, payment_amount))

            # Реферальный бонус для реферера (не для самого пользователя)
            referral_photos = 0
            if is_first_purchase and referrer_id:
                await c.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
                if await c.fetchone():
                    referral_photos = await convert_amount_to_photos(payment_amount, plan_key)
                    if referral_photos > 0:
                        await c.execute('''UPDATE users
                                          SET generations_left = generations_left + ?, updated_at = CURRENT_TIMESTAMP
                                          WHERE user_id = ?''',
                                       (referral_photos, referrer_id))
                        current_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                        await c.execute('''INSERT INTO referral_rewards (referrer_id, referred_user_id, reward_photos, created_at)
                                          VALUES (?, ?, ?, ?)''',
                                       (referrer_id, user_id, referral_photos, current_timestamp))
                        await c.execute('''INSERT OR REPLACE INTO referral_stats (user_id, total_referrals, total_reward_photos, updated_at)
                                          VALUES (?, 
                                                  COALESCE((SELECT total_referrals FROM referral_stats WHERE user_id = ?), 0) + 1,
                                                  COALESCE((SELECT total_reward_photos FROM referral_stats WHERE user_id = ?), 0) + ?,
                                                  ?)''',
                                       (referrer_id, referrer_id, referrer_id, referral_photos, current_timestamp))
                        await c.execute('''UPDATE referrals
                                          SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                                          WHERE referrer_id = ? AND referred_id = ?''',
                                       (referrer_id, user_id))
                        logger.info(f"Реферальный бонус начислен: referrer_id={referrer_id}, referred_id={user_id}, {referral_photos} фото")
                    else:
                        logger.warning(f"Реферальный бонус не начислен для referrer_id={referrer_id}: нет фото для начисления (тариф '{plan_key}')")
                else:
                    logger.warning(f"Реферер user_id={referrer_id} не найден для user_id={user_id}")

            await log_user_action(user_id, 'payment_processed', {
                'payment_id': payment_id_yookassa,
                'plan': plan_key,
                'amount': payment_amount,
                'photos_added': photos_to_add,
                'avatars_added': avatars_to_add,
                'is_first_purchase': is_first_purchase,
                'bonus_avatar': bonus_avatar,
                'referral_photos': referral_photos
            }, conn=conn)

            await update_user_payment_stats(user_id, payment_amount)

            await conn.commit()

            logger.info(
                f"Ресурсы добавлены для user_id={user_id} по плану '{plan_key}'. "
                f"Баланс: {new_generations} фото (было {generations_left}, добавлено {photos_to_add}), "
                f"{new_avatars} аватар (было {avatar_left}, добавлено {avatars_to_add}). "
                f"Первая покупка: {is_first_purchase}. "
                f"Начислено аватаров: {avatars_to_add} (включая бонус: {bonus_avatar}). "
                f"Реферальный бонус для реферера: {referral_photos} фото."
            )

            if bot:
                try:
                    # Сообщение пользователю
                    tariff_display = TARIFFS.get(plan_key, {}).get('display', plan_key)
                    message_parts = [
                        "🎉 Оплата успешно обработана!",
                        f"📦 Тариф: {tariff_display}",
                        f"✅ Начислено: {photos_to_add} печенек {avatars_to_add - (1 if bonus_avatar else 0)} аватар(ов)"
                    ]
                    
                    if bonus_avatar:
                        message_parts.append("🎁 +1 аватар в подарок за первую покупку!")
                    
                    message_parts.extend([
                        f"💎 Текущий баланс: {new_generations} печенек, {new_avatars} аватар(ов)"
                    ])
                    
                    if referral_photos > 0:
                        message_parts.append("🎁 Реферальный бонус начислен вашему другу!")
                    
                    message_text = safe_escape_markdown("\n".join(message_parts), version=2)
                    
                    await send_message_with_fallback(
                        bot, user_id,
                        message_text,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

                # Уведомление рефереру
                if referral_photos > 0 and referrer_id:
                    try:
                        referrer_data = await get_user_info(referrer_id)
                        if referrer_data:
                            message_text = safe_escape_markdown(
                                f"🎁 Ваш друг оплатил подписку! Вам начислено {referral_photos} печенек за реферала!\n"
                                f"💎 Текущий баланс: {referrer_data['generations_left'] + referral_photos} печенек",
                                version=2
                            )
                            await send_message_with_fallback(
                                bot, referrer_id,
                                message_text,
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления рефереру {referrer_id}: {e}")

            return True

    except Exception as e:
        logger.error(f"Ошибка добавления ресурсов для user_id={user_id}: {e}", exc_info=True)
        if bot:
            for admin_id in ADMIN_IDS:
                try:
                    await send_message_with_fallback(
                        bot, admin_id,
                        safe_escape_markdown(f"🚨 Ошибка обработки платежа для user_id={user_id}, payment_id={payment_id_yookassa}: {str(e)}", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e_notify:
                    logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")
        return False

@invalidate_cache()
async def save_user_trainedmodel(user_id: int, prediction_id: str, trigger_word: str, 
                                photo_paths_list: List[str], avatar_name: Optional[str] = None, 
                                training_step: str = "initial_save", conn=None) -> int:
    """Сохраняет информацию об обученной модели или обновляет ее"""
    try:
        photo_paths_str = json.dumps(photo_paths_list) if photo_paths_list else None
        
        if conn is None:
            async with aiosqlite.connect(DATABASE_PATH, timeout=10) as conn:
                await conn.execute("PRAGMA busy_timeout = 10000")
                c = await conn.cursor()
                
                await c.execute("SELECT avatar_id FROM user_trainedmodels WHERE prediction_id = ?", (prediction_id,))
                existing_model = await c.fetchone()
                
                if existing_model:
                    await c.execute('''UPDATE user_trainedmodels 
                                     SET user_id = ?, status = ?, trigger_word = ?, photo_paths = ?, 
                                         avatar_name = ?, training_step = ?, updated_at = CURRENT_TIMESTAMP
                                     WHERE prediction_id = ?''',
                                   (user_id, 'pending', trigger_word, photo_paths_str, avatar_name, training_step, prediction_id))
                    avatar_id = existing_model[0]
                    logger.info(f"Обучаемая модель обновлена для user_id={user_id}, avatar_id={avatar_id}, prediction_id={prediction_id}")
                else:
                    await c.execute('''INSERT INTO user_trainedmodels 
                                     (user_id, prediction_id, status, trigger_word, photo_paths, avatar_name, training_step, created_at, updated_at)
                                     VALUES (?, ?, 'pending', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)''',
                                   (user_id, prediction_id, trigger_word, photo_paths_str, avatar_name, training_step))
                    avatar_id = c.lastrowid
                    logger.info(f"Обучаемая модель сохранена для user_id={user_id}, avatar_id={avatar_id}, prediction_id={prediction_id}")
                    
                    await log_user_action(user_id, 'train_avatar', {
                        'avatar_id': avatar_id,
                        'avatar_name': avatar_name,
                        'trigger_word': trigger_word,
                        'photo_count': len(photo_paths_list) if photo_paths_list else 0
                    }, conn=conn)
                
                if avatar_id:
                    await c.execute("UPDATE users SET has_trained_model = 1 WHERE user_id = ?", (user_id,))
                else:
                    logger.error(f"Не удалось получить/обновить avatar_id для user_id={user_id}, prediction_id={prediction_id}")
                
                await conn.commit()
        else:
            c = await conn.cursor()
            
            await c.execute("SELECT avatar_id FROM user_trainedmodels WHERE prediction_id = ?", (prediction_id,))
            existing_model = await c.fetchone()
            
            if existing_model:
                await c.execute('''UPDATE user_trainedmodels 
                                 SET user_id = ?, status = ?, trigger_word = ?, photo_paths = ?, 
                                     avatar_name = ?, training_step = ?, updated_at = CURRENT_TIMESTAMP
                                 WHERE prediction_id = ?''',
                               (user_id, 'pending', trigger_word, photo_paths_str, avatar_name, training_step, prediction_id))
                avatar_id = existing_model[0]
                logger.info(f"Обучаемая модель обновлена для user_id={user_id}, avatar_id={avatar_id}, prediction_id={prediction_id}")
            else:
                await c.execute('''INSERT INTO user_trainedmodels 
                                 (user_id, prediction_id, status, trigger_word, photo_paths, avatar_name, training_step, created_at, updated_at)
                                 VALUES (?, ?, 'pending', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)''',
                               (user_id, prediction_id, trigger_word, photo_paths_str, avatar_name, training_step))
                avatar_id = c.lastrowid
                logger.info(f"Обучаемая модель сохранена для user_id={user_id}, avatar_id={avatar_id}, prediction_id={prediction_id}")
                
                await log_user_action(user_id, 'train_avatar', {
                    'avatar_id': avatar_id,
                    'avatar_name': avatar_name,
                    'trigger_word': trigger_word,
                    'photo_count': len(photo_paths_list) if photo_paths_list else 0
                }, conn=conn)
            
            if avatar_id:
                await c.execute("UPDATE users SET has_trained_model = 1 WHERE user_id = ?", (user_id,))
            else:
                logger.error(f"Не удалось получить/обновить avatar_id для user_id={user_id}, prediction_id={prediction_id}")
        
        return avatar_id
        
    except Exception as e:
        logger.error(f"Ошибка сохранения модели для user_id={user_id}: {e}", exc_info=True)
        raise

@invalidate_cache()
async def update_trainedmodel_status(avatar_id: int, model_id: Optional[str] = None, 
                                   model_version: Optional[str] = None, 
                                   status: Optional[str] = None, 
                                   prediction_id: Optional[str] = None):
    """Обновляет статус и данные обученной модели"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            fields_to_update = []
            params = []
            
            if model_id is not None:
                fields_to_update.append("model_id = ?")
                params.append(model_id)
                
            if model_version is not None:
                if ':' in str(model_version) and model_version.count(':') > 0:
                    version_hash = model_version.split(':')[-1]
                    logger.warning(f"Исправление model_version: {model_version} -> {version_hash}")
                    model_version = version_hash
                
                fields_to_update.append("model_version = ?")
                params.append(model_version)
                
            if status is not None:
                fields_to_update.append("status = ?")
                params.append(status)
                
            if prediction_id is not None:
                fields_to_update.append("prediction_id = ?")
                params.append(prediction_id)
            
            if not fields_to_update:
                logger.warning(f"Нет полей для обновления для avatar_id={avatar_id}")
                return
            
            fields_to_update.append("updated_at = CURRENT_TIMESTAMP")
            params.append(avatar_id)
            
            query = f"UPDATE user_trainedmodels SET {', '.join(fields_to_update)} WHERE avatar_id = ?"
            await c.execute(query, tuple(params))
            
            if status == 'success':
                await c.execute("SELECT user_id FROM user_trainedmodels WHERE avatar_id = ?", (avatar_id,))
                user_id_row = await c.fetchone()
                
                if user_id_row:
                    user_id = user_id_row[0]
                    await c.execute("SELECT active_avatar_id FROM users WHERE user_id = ?", (user_id,))
                    active_avatar_row = await c.fetchone()
                    
                    if not active_avatar_row or active_avatar_row[0] is None:
                        await c.execute("UPDATE users SET active_avatar_id = ? WHERE user_id = ?", (avatar_id, user_id))
                        logger.info(f"Аватар avatar_id={avatar_id} сделан активным для user_id={user_id} (т.к. активного не было).")
                        
                        await user_cache.invalidate(user_id)
            
            await conn.commit()
            
        logger.info(f"Статус/данные модели обновлены для avatar_id={avatar_id}")
        
    except Exception as e:
        logger.error(f"Ошибка обновления статуса модели avatar_id={avatar_id}: {e}", exc_info=True)
        raise

async def get_user_trainedmodels(user_id: int) -> List[Tuple]:
    """Получает все обученные модели пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT avatar_id, model_id, model_version, status, prediction_id, 
                             trigger_word, photo_paths, training_step, avatar_name
                             FROM user_trainedmodels 
                             WHERE user_id = ? 
                             ORDER BY avatar_id DESC''',
                           (user_id,))
            results = await c.fetchall()
            
        models_list = []
        for row in results:
            try:
                photo_paths = json.loads(row['photo_paths']) if row['photo_paths'] else []
            except (json.JSONDecodeError, TypeError):
                photo_paths = str(row['photo_paths']).split(',') if row['photo_paths'] and isinstance(row['photo_paths'], str) else []
                logger.warning(f"Ошибка декодирования JSON photo_paths у avatar_id={row['avatar_id']}, user_id={user_id}. Fallback.")
            
            models_list.append((
                row['avatar_id'], row['model_id'], row['model_version'], row['status'],
                row['prediction_id'], row['trigger_word'], photo_paths,
                row['training_step'], row['avatar_name']
            ))
            
        return models_list
        
    except Exception as e:
        logger.error(f"Ошибка получения моделей для user_id={user_id}: {e}", exc_info=True)
        return []

async def get_active_trainedmodel(user_id: int) -> Optional[Tuple]:
    """Получает активную обученную модель пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("SELECT active_avatar_id FROM users WHERE user_id = ?", (user_id,))
            active_avatar_id_row = await c.fetchone()
            active_avatar_id = active_avatar_id_row['active_avatar_id'] if active_avatar_id_row else None
            
            model_to_return_data = None
            
            if active_avatar_id is not None:
                await c.execute('''SELECT avatar_id, model_id, model_version, status, prediction_id, 
                                 trigger_word, photo_paths, training_step, avatar_name
                                 FROM user_trainedmodels 
                                 WHERE avatar_id = ? AND status = 'success' ''',
                               (active_avatar_id,))
                model_to_return_data = await c.fetchone()
            
            if not model_to_return_data:
                status_msg = "не найден или не 'success'" if active_avatar_id else "не установлен"
                logger.warning(f"Активный avatar_id={active_avatar_id} для user_id={user_id} {status_msg}. Ищем последнюю 'success' модель.")
                
                await c.execute('''SELECT avatar_id, model_id, model_version, status, prediction_id, 
                                 trigger_word, photo_paths, training_step, avatar_name
                                 FROM user_trainedmodels 
                                 WHERE user_id = ? AND status = 'success' 
                                 ORDER BY avatar_id DESC LIMIT 1''',
                               (user_id,))
                model_to_return_data = await c.fetchone()
                
                if model_to_return_data:
                    logger.info(f"Найдена последняя 'success' модель (ID: {model_to_return_data['avatar_id']}) для user_id={user_id} как fallback.")
            
            if model_to_return_data:
                try:
                    photo_paths_list = json.loads(model_to_return_data['photo_paths']) if model_to_return_data['photo_paths'] else []
                except (json.JSONDecodeError, TypeError):
                    photo_paths_list = str(model_to_return_data['photo_paths']).split(',') if model_to_return_data['photo_paths'] and isinstance(model_to_return_data['photo_paths'], str) else []
                    logger.warning(f"Ошибка декодирования JSON photo_paths у avatar_id={model_to_return_data['avatar_id']}, user_id={user_id}. Fallback.")
                
                return (
                    model_to_return_data['avatar_id'], model_to_return_data['model_id'], 
                    model_to_return_data['model_version'], model_to_return_data['status'],
                    model_to_return_data['prediction_id'], model_to_return_data['trigger_word'],
                    photo_paths_list, model_to_return_data['training_step'], model_to_return_data['avatar_name']
                )
                
        logger.warning(f"Ни одна активная 'success' модель не найдена для user_id={user_id}")
        return None
        
    except Exception as e:
        logger.error(f"Ошибка получения активной модели для user_id={user_id}: {e}", exc_info=True)
        return None

@invalidate_cache()
async def delete_trained_model(user_id: int, avatar_id: int) -> bool:
    """Удаляет обученную модель пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("SELECT active_avatar_id FROM users WHERE user_id = ?", (user_id,))
            active_avatar_id_row = await c.fetchone()
            
            if active_avatar_id_row and active_avatar_id_row[0] == avatar_id:
                await c.execute("UPDATE users SET active_avatar_id = NULL WHERE user_id = ?", (user_id,))
            
            await c.execute('''DELETE FROM user_trainedmodels 
                             WHERE avatar_id = ? AND user_id = ?''',
                           (avatar_id, user_id))
            deleted_rows = c.rowcount
            
            await c.execute("SELECT COUNT(*) FROM user_trainedmodels WHERE user_id = ?", (user_id,))
            remaining_models_count = (await c.fetchone())[0]
            
            if remaining_models_count == 0:
                await c.execute("UPDATE users SET has_trained_model = 0 WHERE user_id = ?", (user_id,))
            
            await conn.commit()
            
        if deleted_rows > 0:
            logger.info(f"Модель avatar_id={avatar_id} удалена для user_id={user_id}")
            return True
            
        logger.warning(f"Модель avatar_id={avatar_id} не найдена для удаления для user_id={user_id}")
        return False
        
    except Exception as e:
        logger.error(f"Ошибка удаления модели avatar_id={avatar_id} для user_id={user_id}: {e}", exc_info=True)
        raise

async def get_all_users_stats(page: int = 1, page_size: int = 10) -> Tuple[List[Tuple], int]:
    """Получает статистику всех пользователей с пагинацией"""
    try:
        offset = (page - 1) * page_size
        
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("SELECT COUNT(*) as total FROM users")
            total_users = (await c.fetchone())['total']
            
            await c.execute('''SELECT u.user_id, u.username, u.first_name, u.generations_left, u.avatar_left,
                             u.first_purchase, u.active_avatar_id, u.email, u.referrer_id,
                             (SELECT COUNT(*) FROM referrals WHERE referrer_id = u.user_id AND status = 'completed') as referrals_made_count,
                             (SELECT COUNT(*) FROM payments WHERE user_id = u.user_id) as payments_count,
                             (SELECT SUM(p.amount) FROM payments p WHERE p.user_id = u.user_id) as total_spent
                             FROM users u
                             ORDER BY u.created_at DESC 
                             LIMIT ? OFFSET ?''',
                           (page_size, offset))
            users_data_rows = await c.fetchall()
            
        users_data_tuples = [tuple(row) for row in users_data_rows]
        return users_data_tuples, total_users
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики пользователей: {e}", exc_info=True)
        return [], 0

async def search_users_by_query(query: str) -> List[Tuple]:
    """Поиск пользователей по запросу"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            search_query = query.strip().lower()
            
            if search_query.startswith('@'):
                search_query = search_query[1:]
            
            if search_query.isdigit():
                await c.execute('''SELECT user_id, username, first_name, generations_left, avatar_left
                                 FROM users 
                                 WHERE user_id = ?''',
                               (int(search_query),))
            else:
                await c.execute('''SELECT user_id, username, first_name, generations_left, avatar_left
                                 FROM users 
                                 WHERE LOWER(username) LIKE ? OR LOWER(first_name) LIKE ?
                                 LIMIT 50''',
                               (f'%{search_query}%', f'%{search_query}%'))
            
            users = await c.fetchall()
            
        return [(row['user_id'], row['username'], row['first_name'], 
               row['generations_left'], row['avatar_left']) for row in users]
        
    except Exception as e:
        logger.error(f"Ошибка поиска пользователей по запросу '{query}': {e}", exc_info=True)
        return []

async def save_video_task(user_id: int, prediction_id: str, model_key: str, video_path: str, status: str, style_name: str = 'custom') -> int:
    """Сохраняет задачу видеогенерации в базу данных."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            # Проверяем наличие столбца style_name
            await c.execute("PRAGMA table_info(video_tasks)")
            columns = [col[1] for col in await c.fetchall()]
            if 'style_name' in columns:
                await c.execute(
                    "INSERT INTO video_tasks (user_id, prediction_id, model_key, video_path, status, style_name) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, prediction_id, model_key, video_path, status, style_name)
                )
            else:
                # Если столбец style_name отсутствует, сохраняем без него
                await c.execute(
                    "INSERT INTO video_tasks (user_id, prediction_id, model_key, video_path, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, prediction_id, model_key, video_path, status)
                )
                logger.warning(f"Столбец style_name отсутствует в таблице video_tasks, сохранено без style_name для user_id={user_id}")
            await conn.commit()
            await c.execute("SELECT last_insert_rowid()")
            task_id = (await c.fetchone())[0]
            logger.info(f"Видео-задача сохранена: user_id={user_id}, task_id={task_id}, style_name={style_name}")
            return task_id
    except Exception as e:
        logger.error(f"Ошибка сохранения видео-задачи для user_id={user_id}: {e}", exc_info=True)
        raise

async def update_video_task_status(task_id: int, status: str, video_path: Optional[str] = None, 
                                 prediction_id: Optional[str] = None):
    """Обновляет статус задачи генерации видео"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            fields_to_update = ["status = ?"]
            params = [status]
            
            if video_path is not None:
                fields_to_update.append("video_path = ?")
                params.append(video_path)
                
            if prediction_id is not None:
                fields_to_update.append("prediction_id = ?")
                params.append(prediction_id)
            
            params.append(task_id)
            
            query = f"UPDATE video_tasks SET {', '.join(fields_to_update)} WHERE id = ?"
            await c.execute(query, tuple(params))
            
            await conn.commit()
            
        logger.info(f"Статус задачи видео обновлён: task_id={task_id}, status={status}")
        
    except Exception as e:
        logger.error(f"Ошибка обновления статуса видео-задачи task_id={task_id}: {e}", exc_info=True)
        raise

async def get_user_video_tasks(user_id: int) -> List[Tuple]:
    """Получает все видео-задачи пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT id, user_id, video_path, status, created_at, prediction_id, model_key 
                             FROM video_tasks 
                             WHERE user_id = ? 
                             ORDER BY created_at DESC''',
                           (user_id,))
            
            return [tuple(row) for row in await c.fetchall()]
            
    except Exception as e:
        logger.error(f"Ошибка получения видео-задач для user_id={user_id}: {e}", exc_info=True)
        return []

async def get_user_payments(user_id: int, limit: Optional[int] = None) -> List[Tuple]:
    """Получает историю успешных платежей пользователя с опциональным ограничением количества записей."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            query = '''SELECT payment_id, plan, amount, created_at 
                       FROM payments 
                       WHERE user_id = ? AND status = 'succeeded'
                       ORDER BY created_at DESC'''
            params = [user_id]
            
            if limit is not None:
                query += ' LIMIT ?'
                params.append(limit)
            
            await c.execute(query, params)
            payments = await c.fetchall()
            
            logger.debug(f"get_user_payments: user_id={user_id}, found {len(payments)} successful payments: {[dict(p) for p in payments]}")
            return [tuple(row) for row in payments]
            
    except Exception as e:
        logger.error(f"Ошибка получения платежей для user_id={user_id}: {e}", exc_info=True)
        return []

async def log_generation(user_id: int, generation_type: str, replicate_model_id: str, units_generated: int):
    """Логирует генерацию для подсчета расходов"""
    try:
        cost_per_unit = REPLICATE_COSTS.get(replicate_model_id, 0.0)
        
        if replicate_model_id == "meta/meta-llama-3-8b-instruct" and units_generated == 1:
            total_cost = Decimal(str(REPLICATE_COSTS.get("meta/meta-llama-3-8b-instruct", "0.0005")))
            cost_per_unit = float(total_cost)
        else:
            total_cost = Decimal(str(units_generated)) * Decimal(str(cost_per_unit))
        
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute('''INSERT INTO generation_log (
                               user_id, generation_type, replicate_model_id, units_generated, 
                               cost_per_unit, total_cost
                           ) VALUES (?, ?, ?, ?, ?, ?)''',
                           (user_id, generation_type, replicate_model_id, 
                            units_generated, float(cost_per_unit), float(total_cost)))
            
            await conn.commit()
            
        logger.info(f"Генерация записана: user_id={user_id}, type={generation_type}, model={replicate_model_id}, "
                  f"units={units_generated}, cost_pu={cost_per_unit:.6f}, total_cost={total_cost:.6f}")
        
        await log_user_action(user_id, 'generate_image', {
            'generation_type': generation_type,
            'model_id': replicate_model_id,
            'units': units_generated,
            'cost': float(total_cost)
        })
        
    except Exception as e:
        logger.error(f"Ошибка логирования генерации для user_id={user_id}: {e}", exc_info=True)
        raise

async def get_user_generation_stats(user_id: int) -> Dict[str, int]:
    """Получает статистику генераций пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT generation_type, SUM(units_generated) as total_units
                             FROM generation_log 
                             WHERE user_id = ? 
                             GROUP BY generation_type''',
                           (user_id,))
            stats = await c.fetchall()
            
        return {row['generation_type']: row['total_units'] for row in stats}
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики генераций для user_id={user_id}: {e}", exc_info=True)
        return {}

async def get_generation_cost_log(start_date_str: Optional[str] = None, 
                                    end_date_str: Optional[str] = None) -> List[Tuple]:
    """Получает лог генераций для подсчета расходов"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            query = "SELECT replicate_model_id, units_generated, total_cost, created_at FROM generation_log"
            params = []
            conditions = []
            
            if start_date_str:
                conditions.append("created_at >= ?")
                params.append(start_date_str)
                
            if end_date_str:
                if len(end_date_str) == 10:
                    end_date_str += " 23:59:59"
                conditions.append("created_at <= ?")
                params.append(end_date_str)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
                
            query += " ORDER BY created_at DESC"
            
            await c.execute(query, tuple(params))
            
            return [tuple(row) for row in await c.fetchall()]
            
    except Exception as e:
        logger.error(f"Ошибка получения лога генераций: {e}", exc_info=True)
        return []

async def get_total_remaining_photos() -> int:
    """Получает общий остаток фото у всех пользователей"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute("SELECT SUM(generations_left) FROM users")
            total_photos = await c.fetchone()
            
            return total_photos[0] if total_photos and total_photos[0] is not None else 0
            
    except Exception as e:
        logger.error(f"Ошибка подсчета общего остатка фото: {e}", exc_info=True)
        return 0

async def get_user_avatars(user_id: int) -> List[Tuple]:
    """Получает краткую информацию об аватарах пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute('''SELECT avatar_id, avatar_name, status 
                             FROM user_trainedmodels 
                             WHERE user_id = ? 
                             ORDER BY avatar_id DESC''',
                           (user_id,))
            
            return [(row['avatar_id'], row['avatar_name'], row['status']) for row in await c.fetchall()]
            
    except Exception as e:
        logger.error(f"Ошибка получения аватаров для user_id={user_id}: {e}", exc_info=True)
        return []

def retry_on_locked(max_attempts: int = 10, initial_delay: float = 0.5):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except aiosqlite.OperationalError as e:
                    if "database is locked" in str(e) and attempt < max_attempts - 1:
                        delay = initial_delay * (2 ** attempt)  # Экспоненциальная задержка
                        logger.warning(
                            f"База данных заблокирована в {func.__name__}, попытка {attempt + 1}/{max_attempts}. "
                            f"Повтор через {delay:.2f}с... ⏳"
                        )
                        try:
                            async with aiosqlite.connect(DATABASE_PATH, timeout=5) as conn:
                                c = await conn.cursor()
                                await c.execute("PRAGMA busy_timeout = 10000")
                                await c.execute("SELECT COUNT(*) FROM sqlite_master")
                                logger.debug(f"Диагностика: база доступна, попытка {attempt + 1}")
                        except Exception as diag_e:
                            logger.error(f"Диагностика не удалась: {diag_e}")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"Исчерпаны попытки в {func.__name__}: {e} 🚫")
                        raise
            raise aiosqlite.OperationalError("Достигнуто максимальное количество попыток при ошибке блокировки 🚫")
        return wrapper
    return decorator

@retry_on_locked(max_attempts=10, initial_delay=0.5)
async def log_user_action(user_id: int, action: str, details: Dict[str, Any] = None, conn=None):
    """Логирует действие пользователя в таблицу user_actions."""
    try:
        if conn is None:
            async with aiosqlite.connect(DATABASE_PATH, timeout=10) as conn:
                await conn.execute("PRAGMA busy_timeout = 10000")
                c = await conn.cursor()
                details_json = json.dumps(details or {}, ensure_ascii=False)
                await c.execute(
                    '''INSERT INTO user_actions 
                       (user_id, action, details, created_at) 
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP)''',
                    (user_id, action, details_json)
                )
                await conn.commit()
        else:
            c = await conn.cursor()
            details_json = json.dumps(details or {}, ensure_ascii=False)
            await c.execute(
                '''INSERT INTO user_actions 
                   (user_id, action, details, created_at) 
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)''',
                (user_id, action, details_json)
            )
        logger.debug(f"Действие пользователя записано: user_id={user_id}, action={action} ✅")
    except aiosqlite.OperationalError as e:
        logger.error(f"Ошибка логирования действия user_id={user_id}: {e} 🚫")
        raise
    except Exception as e:
        logger.error(f"Неизвестная ошибка логирования действия user_id={user_id}: {e} 🚫", exc_info=True)
        raise
       
async def get_user_actions_stats(user_id: Optional[int] = None, 
                               action: Optional[str] = None,
                               start_date: Optional[datetime] = None,
                               end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Получает статистику действий пользователей"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            query = "SELECT * FROM user_actions"
            conditions = []
            params = []
            
            if user_id is not None:
                conditions.append("user_id = ?")
                params.append(user_id)
                
            if action is not None:
                conditions.append("action = ?")
                params.append(action)
                
            if start_date is not None:
                conditions.append("created_at >= ?")
                params.append(start_date.strftime('%Y-%m-%d %H:%M:%S'))
                
            if end_date is not None:
                conditions.append("created_at <= ?")
                params.append(end_date.strftime('%Y-%m-%d %H:%M:%S'))
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
                
            query += " ORDER BY created_at DESC"
            
            await c.execute(query, tuple(params))
            rows = await c.fetchall()
            
            result = []
            for row in rows:
                try:
                    details = json.loads(row['details']) if row['details'] else {}
                except json.JSONDecodeError:
                    details = {}
                    
                result.append({
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'action': row['action'],
                    'details': details,
                    'created_at': row['created_at']
                })
                
            return result
            
    except Exception as e:
        logger.error(f"Ошибка получения статистики действий: {e}", exc_info=True)
        return []

async def get_user_rating_and_registration(user_id: int) -> Tuple[Optional[float], Optional[int], Optional[str]]:
    """Получает средний рейтинг, количество оценок и дату регистрации пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()

            await c.execute('''SELECT AVG(rating) as avg_rating, COUNT(rating) as rating_count
                              FROM user_ratings
                              WHERE user_id = ?''', (user_id,))
            rating_result = await c.fetchone()
            avg_rating = float(rating_result['avg_rating']) if rating_result['avg_rating'] is not None else None
            rating_count = int(rating_result['rating_count']) if rating_result['rating_count'] is not None else 0

            await c.execute('''SELECT created_at
                              FROM users
                              WHERE user_id = ?''', (user_id,))
            user_result = await c.fetchone()
            registration_date = None

            if user_result and user_result['created_at']:
                try:
                    registration_date_obj = datetime.strptime(
                        user_result['created_at'].split('.')[0], '%Y-%m-%d %H:%M:%S'
                    )
                    registration_date = registration_date_obj.strftime('%d.%m.%Y')
                    logger.debug(f"Дата регистрации для user_id={user_id} взята из users: {registration_date}")
                except ValueError as e:
                    logger.warning(f"Ошибка формата created_at в users для user_id={user_id}: {user_result['created_at']}, ошибка: {e}")

            if not registration_date:
                await c.execute('''SELECT created_at
                                  FROM user_actions
                                  WHERE user_id = ?
                                  ORDER BY created_at ASC
                                  LIMIT 1''', (user_id,))
                action_result = await c.fetchone()
                if action_result and action_result['created_at']:
                    try:
                        registration_date_obj = datetime.strptime(
                            action_result['created_at'].split('.')[0], '%Y-%m-%d %H:%M:%S'
                        )
                        registration_date = registration_date_obj.strftime('%d.%m.%Y')
                        logger.info(f"Дата регистрации для user_id={user_id} восстановлена из user_actions: {registration_date}")
                    except ValueError as e:
                        logger.warning(f"Ошибка формата created_at в user_actions для user_id={user_id}: {action_result['created_at']}, ошибка: {e}")
                else:
                    logger.info(f"Дата регистрации для user_id={user_id} не найдена ни в users, ни в user_actions")

            logger.debug(f"Рейтинг для user_id={user_id}: средний={avg_rating}, количество={rating_count}")
            return avg_rating, rating_count, registration_date

    except Exception as e:
        logger.error(f"Ошибка получения рейтинга, количества оценок и даты регистрации для user_id={user_id}: {e}", exc_info=True)
        return None, None, None    
        
@invalidate_cache()
async def delete_user_activity(user_id: int) -> bool:
    """Удаляет пользователя и все связанные с ним данные из всех таблиц."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()

            await c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if not await c.fetchone():
                logger.warning(f"Попытка удалить несуществующего пользователя user_id={user_id}")
                return False

            table_columns = {
                "user_trainedmodels": "user_id",
                "user_ratings": "user_id",
                "video_tasks": "user_id",
                "payments": "user_id",
                "generation_log": "user_id",
                "user_actions": "user_id",
                "referrals": None
            }

            for table, column in table_columns.items():
                if table == "referrals":
                    await c.execute(
                        "DELETE FROM referrals WHERE referrer_id = ? OR referred_id = ?",
                        (user_id, user_id)
                    )
                    logger.debug(f"Удалено {c.rowcount} записей из таблицы referrals для user_id={user_id} (referrer_id или referred_id)")
                elif column:
                    await c.execute(f"DELETE FROM {table} WHERE {column} = ?", (user_id,))
                    logger.debug(f"Удалено {c.rowcount} записей из таблицы {table} для user_id={user_id}")
                else:
                    logger.warning(f"Пропущена таблица {table}: не указан столбец для удаления")

            await c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            deleted_rows = c.rowcount

            await conn.commit()

            if deleted_rows > 0:
                logger.info(f"Пользователь user_id={user_id} и все связанные данные успешно удалены")
                return True
            else:
                logger.error(f"Не удалось удалить пользователя user_id={user_id}: пользователь не найден")
                return False

    except Exception as e:
        logger.error(f"Ошибка удаления пользователя user_id={user_id}: {e}", exc_info=True)
        raise

@invalidate_cache()
async def block_user_access(user_id: int, block: bool = True, block_reason: Optional[str] = None) -> bool:
    """Блокирует или разблокирует пользователя с указанием причины."""
    action = "блокировки" if block else "разблокировки"
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()

            await c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if not await c.fetchone():
                logger.warning(f"Попытка {action} несуществующего пользователя user_id={user_id}")
                return False

            reason = block_reason if block else None
            await c.execute('''UPDATE users
                              SET is_blocked = ?, block_reason = ?, updated_at = CURRENT_TIMESTAMP
                              WHERE user_id = ?''',
                           (1 if block else 0, reason, user_id))

            await conn.commit()

            await log_user_action(user_id, f"{'block' if block else 'unblock'}_user", {
                'admin_action': action,
                'reason': block_reason,
                'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            })

            logger.info(f"Пользователь user_id={user_id} {action} с причиной: {block_reason}")
            return True

    except Exception as e:
        logger.error(f"Ошибка при {action} пользователя user_id={user_id}: {e}", exc_info=True)
        raise

async def is_user_blocked(user_id: int) -> bool:
    """Проверяет, заблокирован ли пользователь"""
    try:
        cached_data = await user_cache.get(user_id)
        if cached_data and len(cached_data) >= 10:
            return bool(cached_data[9])  # is_blocked

        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()

            await c.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
            result = await c.fetchone()
            is_blocked = bool(result['is_blocked']) if result else False

            if result:
                await check_database_user(user_id)  # Обновляет кэш

            return is_blocked

    except Exception as e:
        logger.error(f"Ошибка проверки статуса блокировки для user_id={user_id}: {e}", exc_info=True)
        return False

async def get_payments_by_date(start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Tuple]:
    """Получает платежи за указанный период, возвращая время в МСК."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()

            query = """
                SELECT p.user_id, p.plan, p.amount, p.payment_id, p.created_at, 
                       u.username, u.first_name
                FROM payments p
                JOIN users u ON p.user_id = u.user_id
                WHERE 1=1
            """
            params = []

            if start_date:
                query += " AND DATE(p.created_at) >= ?"
                params.append(start_date)
            if end_date:
                query += " AND DATE(p.created_at) <= ?"
                params.append(end_date)

            query += " ORDER BY p.created_at DESC"

            await c.execute(query, params)
            payments = await c.fetchall()

            # Преобразуем время в МСК
            moscow_tz = pytz.timezone('Europe/Moscow')
            result = []
            for p in payments:
                try:
                    # Предполагаем, что created_at в базе хранится в UTC
                    utc_dt = datetime.strptime(p['created_at'], '%Y-%m-%d %H:%M:%S') if p['created_at'] else None
                    if utc_dt:
                        utc_dt = pytz.utc.localize(utc_dt)
                        msk_dt = utc_dt.astimezone(moscow_tz)
                        created_at_msk = msk_dt  # Сохраняем объект datetime для передачи
                    else:
                        created_at_msk = None
                except ValueError as e:
                    logger.error(f"Ошибка формата времени для payment_id={p['payment_id']}: {e}")
                    created_at_msk = None

                result.append((
                    p['user_id'],
                    p['plan'],
                    p['amount'],
                    p['payment_id'],
                    created_at_msk,  # Передаём время в МСК
                    p['username'],
                    p['first_name']
                ))

            return result

    except Exception as e:
        logger.error(f"Ошибка получения платежей за период {start_date} - {end_date}: {e}", exc_info=True)
        return []

async def check_referral_integrity(user_id: int) -> bool:
    """Проверяет целостность реферальной связи для пользователя."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            await c.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
            referrer_id_row = await c.fetchone()
            referrer_id = referrer_id_row[0] if referrer_id_row else None

            logger.debug(f"check_referral_integrity: user_id={user_id}, referrer_id={referrer_id}")

            if not referrer_id:
                logger.info(f"Нет реферера для user_id={user_id}, целостность подтверждена")
                return True

            await c.execute("SELECT 1 FROM referrals WHERE referrer_id = ? AND referred_id = ?",
                            (referrer_id, user_id))
            referral_record = await c.fetchone()

            if referral_record:
                logger.debug(f"Реферальная связь найдена для user_id={user_id}, referrer_id={referrer_id}")
                return True
            else:
                current_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                await c.execute('''INSERT OR IGNORE INTO referrals (referrer_id, referred_id, status, created_at)
                                  VALUES (?, ?, 'pending', ?)''',
                                (referrer_id, user_id, current_timestamp))
                await conn.commit()
                logger.info(f"Восстановлена реферальная связь для user_id={user_id}, referrer_id={referrer_id}")
                return c.rowcount > 0

    except Exception as e:
        logger.error(f"Ошибка проверки реферальной целостности для user_id={user_id}: {e}", exc_info=True)
        return False
        
async def get_registrations_by_date(start_date: str, end_date: str = None) -> List[Tuple]:
    """Получает данные о пользователях, зарегистрированных в указанный день или период."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()

            query = """
                SELECT user_id, username, first_name, created_at, referrer_id
                FROM users
                WHERE 1=1
            """
            params = []

            if start_date:
                query += " AND DATE(created_at) >= ?"
                params.append(start_date)
            if end_date:
                query += " AND DATE(created_at) <= ?"
                params.append(end_date)
            else:
                query += " AND DATE(created_at) = ?"
                params.append(start_date)

            query += " ORDER BY created_at DESC"

            await c.execute(query, params)
            registrations = await c.fetchall()

            return [
                (
                    r['user_id'],
                    r['username'],
                    r['first_name'],
                    datetime.strptime(r['created_at'], '%Y-%m-%d %H:%M:%S') if r['created_at'] else None,
                    r['referrer_id']
                )
                for r in registrations
            ]

    except Exception as e:
        logger.error(f"Ошибка получения регистраций за {start_date} - {end_date or start_date}: {e}", exc_info=True)
        return []

async def check_user_resources(bot, user_id: int, required_photos: int = 0, required_avatars: int = 0) -> bool:
    from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback
    try:
        user_data = await check_database_user(user_id)
        if not user_data:
            logger.warning(f"Пользователь user_id={user_id} не найден при проверке ресурсов")
            await send_message_with_fallback(
                bot, user_id,
                escape_md("❌ Пользователь не найден. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return False

        available_photos = user_data[0]  # generations_left
        available_avatars = user_data[1]  # avatar_left
        is_blocked = user_data[9]  # is_blocked

        if is_blocked:
            logger.info(f"Пользователь user_id={user_id} заблокирован, доступ к ресурсам запрещен")
            await send_message_with_fallback(
                bot, user_id,
                escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return False

        if available_photos < required_photos:
            logger.info(f"Недостаточно фото для user_id={user_id}: доступно {available_photos}, требуется {required_photos}")
            await send_message_with_fallback(
                bot, user_id,
                escape_md(f"⚠️ Недостаточно печенек на балансе: доступно {available_photos}, требуется {required_photos}. Пополните баланс через /subscribe", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return False

        if available_avatars < required_avatars:
            logger.info(f"Недостаточно аватаров для user_id={user_id}: доступно {available_avatars}, требуется {required_avatars}")
            await send_message_with_fallback(
                bot, user_id,
                escape_md(f"⚠️ Недостаточно аватаров на балансе: доступно {available_avatars}, требуется {required_avatars}. Пополните баланс через /subscribe", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return False

        return True

    except Exception as e:
        logger.error(f"Ошибка проверки ресурсов для user_id={user_id}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("❌ Произошла ошибка при проверке ресурсов. Попробуйте позже или обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return False

async def convert_amount_to_photos(amount: float, tariff_key: Optional[str] = None) -> int:
    """Конвертирует сумму платежа или тариф в количество фото для реферального бонуса."""
    try:
        if not TARIFFS:
            logger.error("Конфигурация TARIFFS пуста, невозможно определить количество фото")
            return 0
        
        if tariff_key and tariff_key in TARIFFS:
            photos = TARIFFS[tariff_key].get('photos', 0)
            referral_photos = max(int(photos * 0.10), 0)  # 10% от количества фото в тарифе, округление вниз
            logger.debug(f"Конвертация по тарифу '{tariff_key}': {photos} фото -> {referral_photos} реферальных фото")
            return referral_photos
        
        logger.warning(f"Тариф '{tariff_key}' не найден, возвращается 0 реферальных фото")
        return 0
    except Exception as e:
        logger.error(f"Ошибка конвертации суммы в фото для тарифа '{tariff_key}': {e}", exc_info=True)
        return 0

async def reset_user_model(user_id: int) -> bool:
    """Сбрасывает все обученные модели пользователя"""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            
            await c.execute('''UPDATE users 
                              SET has_trained_model = 0, active_avatar_id = NULL, updated_at = CURRENT_TIMESTAMP 
                              WHERE user_id = ?''',
                           (user_id,))
            
            await c.execute('''DELETE FROM user_trainedmodels 
                              WHERE user_id = ?''',
                           (user_id,))
            
            await conn.commit()
            
            logger.info(f"Все модели сброшены для user_id={user_id}")
            await user_cache.invalidate(user_id)
            return True
            
    except Exception as e:
        logger.error(f"Ошибка сброса моделей для user_id={user_id}: {e}", exc_info=True)
        return False
        
async def get_broadcasts_with_buttons() -> List[Dict[str, Any]]:
    """Получает список рассылок, у которых есть кнопки в таблице broadcast_buttons."""
    try:
        async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute('''
                SELECT DISTINCT sb.id, sb.scheduled_time, sb.status, sb.broadcast_data
                FROM scheduled_broadcasts sb
                JOIN broadcast_buttons bb ON sb.id = bb.broadcast_id
                WHERE sb.status = 'pending'
                ORDER BY sb.scheduled_time ASC
            ''')
            broadcasts = await c.fetchall()
            result = []
            for row in broadcasts:
                try:
                    broadcast_data = json.loads(row['broadcast_data']) if row['broadcast_data'] else {}
                    if not isinstance(broadcast_data, dict):
                        logger.warning(f"Некорректные данные broadcast_data для broadcast_id={row['id']}: {row['broadcast_data']}")
                        broadcast_data = {}
                except json.JSONDecodeError as e:
                    logger.error(f"Ошибка декодирования JSON для broadcast_id={row['id']}: {e}, данные: {row['broadcast_data']}")
                    broadcast_data = {}
                result.append({
                    'id': row['id'],
                    'scheduled_time': row['scheduled_time'],
                    'status': row['status'],
                    'broadcast_data': broadcast_data
                })
            logger.debug(f"Найдено {len(result)} рассылок с кнопками")
            return result
    except Exception as e:
        logger.error(f"Ошибка получения рассылок с кнопками: {e}", exc_info=True)
        return []      
        
async def is_old_user(user_id: int, cutoff_date: str = "2025-07-11") -> bool:
    """Проверяет, является ли пользователь 'старым' (зарегистрирован до указанной даты)."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("SELECT created_at FROM users WHERE user_id = ?", (user_id,))
            result = await c.fetchone()
            if not result or not result['created_at']:
                logger.warning(f"Дата регистрации не найдена для user_id={user_id}")
                return False
            try:
                registration_date = datetime.strptime(result['created_at'], '%Y-%m-%d %H:%M:%S')
                cutoff = datetime.strptime(cutoff_date, '%Y-%m-%d')
                is_old = registration_date.date() < cutoff.date()
                logger.debug(f"Проверка is_old_user для user_id={user_id}: created_at={result['created_at']}, cutoff_date={cutoff_date}, is_old={is_old}")
                return is_old
            except ValueError as e:
                logger.error(f"Ошибка формата даты для user_id={user_id}: {e}")
                return False
    except Exception as e:
        logger.error(f"Ошибка проверки статуса старого пользователя для user_id={user_id}: {e}", exc_info=True)
        return False        