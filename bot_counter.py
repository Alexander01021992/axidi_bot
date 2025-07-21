import logging
import aiosqlite
from aiogram import Bot
from aiogram.types import Message
from config import ADMIN_IDS, DATABASE_PATH
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

logger = logging.getLogger(__name__)

class BotCounter:
    async def start(self, bot: Bot):
        logger.info("BotCounter started")
    async def stop(self):
        logger.info("BotCounter stopped")
    
    def __init__(self):
        self.additional_users = 0
        self.db_path = DATABASE_PATH  # Используем DATABASE_PATH из config.py
        
    async def load_settings(self):
        """Загружает настройки из БД."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS bot_counter_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                await db.commit()
                
                cursor = await db.execute(
                    "SELECT value FROM bot_counter_settings WHERE key = 'additional_users'"
                )
                result = await cursor.fetchone()
                if result:
                    self.additional_users = int(result[0])
                    
        except Exception as e:
            logger.error(f"Ошибка загрузки настроек: {e}", exc_info=True)
    
    async def save_settings(self):
        """Сохраняет настройки в БД."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS bot_counter_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                
                await db.execute("""
                    INSERT OR REPLACE INTO bot_counter_settings (key, value)
                    VALUES ('additional_users', ?)
                """, (str(self.additional_users),))
                
                await db.commit()
                
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}", exc_info=True)
        
    async def get_real_users_count(self) -> int:
        """Получает реальное количество пользователей."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM users WHERE user_id IS NOT NULL"
                )
                result = await cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"Ошибка подсчета: {e}", exc_info=True)
            return 0
    
    async def get_total_count(self) -> int:
        """Получает общее количество."""
        await self.load_settings()
        real_count = await self.get_real_users_count()
        return real_count + self.additional_users
    
    def format_number(self, num: int) -> str:
        """Форматирует число с пробелами."""
        return f"{num:,}".replace(",", " ")

# Глобальный экземпляр
bot_counter = BotCounter()

async def get_beautiful_main_menu_text() -> str:
    """Возвращает красиво оформленный текст главного меню."""
    total = await bot_counter.get_total_count()
    formatted = bot_counter.format_number(total)
    
    return (
        f"<b>🏠 ГЛАВНОЕ МЕНЮ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>PixelPie AI | 👥 {formatted}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Добро пожаловать в мир AI-творчества!</b> 🎨\n\n"
        f"<b>Что вы хотите создать сегодня?</b>\n\n"
        f"🖼 <b>Сгенерировать</b> — создайте уникальные фото или видео с помощью AI\n\n"
        f"🎭 <b>Мои аватары</b> — управляйте своими AI-аватарами для персонализированных генераций\n\n"
        f"💎 <b>Купить пакет</b> — пополните баланс и получите больше возможностей\n\n"
        f"👤 <b>Личный кабинет</b> — проверьте баланс, статистику и историю\n\n"
        f"💬 <b>Поддержка</b> — мы всегда готовы помочь!\n\n"
        f"<i>Выберите нужный раздел с помощью кнопок ниже 👇</i>"
    )

async def get_main_menu_variant2() -> str:
    """Вариант 2: Более компактный."""
    total = await bot_counter.get_total_count()
    formatted = bot_counter.format_number(total)
    
    return (
        f"<b>🎨 PixelPie AI</b>\n"
        f"<i>Нас уже {formatted} пользователей!</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🏠 Главное меню</b>\n\n"
        f"Выберите, что хотите сделать:\n\n"
        f"🖼 <b>Генерация</b> – фото и видео\n"
        f"🎭 <b>Аватары</b> – ваши AI-модели\n"
        f"💎 <b>Пакеты</b> – пополнение баланса\n"
        f"👤 <b>Профиль</b> – баланс и статистика\n"
        f"💬 <b>Поддержка</b> – помощь 24/7"
    )

async def get_main_menu_variant3() -> str:
    """Вариант 3: Минималистичный."""
    total = await bot_counter.get_total_count()
    formatted = bot_counter.format_number(total)
    
    return (
        f"<b>PixelPie AI | {formatted} users</b>\n\n"
        f"🏠 <b>Главное меню</b>\n\n"
        f"🖼 Создать изображение или видео\n"
        f"🎭 Управление аватарами\n"
        f"💎 Купить генерации\n"
        f"👤 Мой профиль\n"
        f"💬 Связаться с поддержкой"
    )

async def cmd_bot_name(message: Message, state: FSMContext):
    """Команда для управления счетчиком."""
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    
    await bot_counter.load_settings()
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    
    if not args:
        real = await bot_counter.get_real_users_count()
        total = await bot_counter.get_total_count()
        
        text = (
            f"📊 <b>Управление счетчиком</b>\n\n"
            f"<b>Реальных:</b> {bot_counter.format_number(real)}\n"
            f"<b>Добавлено:</b> +{bot_counter.format_number(bot_counter.additional_users)}\n"
            f"<b>Всего показывается:</b> {bot_counter.format_number(total)}\n\n"
            f"<b>Команды:</b>\n"
            f"/botname set 88027 - установить количество\n"
            f"/botname add 1000 - добавить\n"
            f"/botname show - показать варианты меню\n"
            f"/botname reset - сбросить на реальное"
        )
        
        await message.answer(text, parse_mode='HTML')
        return
    
    cmd = args[0].lower()
    
    if cmd == "show":
        menu1 = await get_beautiful_main_menu_text()
        await message.answer(
            f"<b>Вариант 1 (основной):</b>\n\n{menu1}",
            parse_mode='HTML'
        )
        
        menu2 = await get_main_menu_variant2()
        await message.answer(
            f"<b>Вариант 2 (компактный):</b>\n\n{menu2}",
            parse_mode='HTML'
        )
        
        menu3 = await get_main_menu_variant3()
        await message.answer(
            f"<b>Вариант 3 (минималистичный):</b>\n\n{menu3}",
            parse_mode='HTML'
        )
        
    elif cmd == "set" and len(args) > 1:
        try:
            target = int(args[1])
            real = await bot_counter.get_real_users_count()
            bot_counter.additional_users = max(0, target - real)
            await bot_counter.save_settings()
            
            await message.answer(
                f"✅ Установлено: {bot_counter.format_number(target)}",
                parse_mode='HTML'
            )
        except ValueError:
            await message.answer("❌ Укажите число")
            
    elif cmd == "add" and len(args) > 1:
        try:
            amount = int(args[1])
            bot_counter.additional_users += amount
            await bot_counter.save_settings()
            total = await bot_counter.get_total_count()
            
            await message.answer(
                f"✅ Добавлено +{amount}\n"
                f"Всего: {bot_counter.format_number(total)}",
                parse_mode='HTML'
            )
        except ValueError:
            await message.answer("❌ Укажите число")
            
    elif cmd == "reset":
        bot_counter.additional_users = 0
        await bot_counter.save_settings()
        real = await bot_counter.get_real_users_count()
        
        await message.answer(
            f"✅ Сброшено на реальное: {bot_counter.format_number(real)}",
            parse_mode='HTML'
        )

# Регистрация обработчика
from aiogram import Router
bot_counter_router = Router()

@bot_counter_router.message(Command("botname"))
async def cmd_bot_name_handler(message: Message, state: FSMContext):
    await cmd_bot_name(message, state)