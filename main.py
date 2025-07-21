# main.py
from aiogram.fsm.context import FSMContext
import asyncio
import logging
import json
import os
from threading import Thread
from datetime import datetime, timezone
from typing import Dict, Optional, Any
import pytz
import aiosqlite
from generation_config import GENERATION_TYPE_TO_MODEL_KEY
from handlers.onboarding import setup_onboarding_handlers, onboarding_router, schedule_tariff_messages, schedule_onboarding_reminders
from aiogram import Bot, Dispatcher
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton, Message, ContentType
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from flask import Flask, request, jsonify
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from bot_counter import bot_counter, cmd_bot_name
from config import TELEGRAM_BOT_TOKEN as TOKEN, ADMIN_IDS, TARIFFS, DATABASE_PATH, METRICS_CONFIG
from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback, escape_message_parts, unescape_markdown
from database import (
    init_db, add_resources_on_payment, check_database_user, get_user_payments,
    user_cache, is_user_blocked, get_user_actions_stats, check_referral_integrity,
    update_user_balance, get_scheduled_broadcasts, get_users_for_welcome_message,
    mark_welcome_message_sent, block_user_access, update_user_credits, retry_on_locked, get_broadcast_buttons
)
from handlers.commands import start, menu, help_command, check_training, debug_avatars
from handlers.messages import (
    handle_photo, handle_admin_text, handle_video,
    award_referral_bonuses, handle_text
)
from handlers.errors import error_handler
from handlers.admin_panel import send_daily_payments_report
from handlers.visualization import handle_activity_dates_input
from handlers.user_management import (
    handle_balance_change_input, handle_block_reason_input, user_management_callback_handler, handle_user_search_input
)
from handlers.broadcast import (
    handle_broadcast_message, handle_broadcast_schedule_input, list_scheduled_broadcasts,
    broadcast_message_admin, broadcast_to_paid_users, broadcast_to_non_paid_users,
    broadcast_with_payment, handle_broadcast_schedule_time, handle_broadcast_button_input
)
from handlers.payments import handle_payments_date_input
from handlers.callbacks_admin import (
    admin_callback_handler, handle_admin_style_selection,
    handle_admin_custom_prompt, handle_admin_send_generation, handle_admin_regenerate, admin_callbacks_router
)
from handlers.callbacks_user import handle_user_callback, user_callbacks_router
from handlers.callbacks_utils import utils_callback_handler, utils_callbacks_router
from handlers.callbacks_referrals import referrals_callback_handler, referrals_callbacks_router
from generation import check_pending_trainings, check_pending_video_tasks
from keyboards import create_main_menu_keyboard
from fsm_handlers import setup_conversation_handler, fsm_router, BotStates
from handlers.user_management import user_management_router, cancel
from handlers.payments import payments_router
from handlers.visualization import visualization_router
from handlers.broadcast import broadcast_router
from bot_counter import bot_counter_router
from generation.videos import video_router
from generation.training import training_router
from handlers.photo_transform import photo_transform_router, init_photo_generator
# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Заполняем METRICS_CONFIG['generation_types'] после импорта
METRICS_CONFIG['generation_types'] = list(GENERATION_TYPE_TO_MODEL_KEY.keys())
# Инициализация Flask
app = Flask(__name__)

# Глобальные переменные
bot_instance = None
dp = None
bot_event_loop = None
YOOKASSA_WEBHOOK_SECRET = os.getenv('YOOKASSA_SECRET', '')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://axidiphoto.ru/webhook')

async def is_payment_processed_webhook(payment_id: str) -> bool:
    """Проверяет, был ли платёж уже обработан."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            await c.execute("SELECT COUNT(*) FROM payments WHERE payment_id = ?", (payment_id,))
            count = (await c.fetchone())[0]
        logger.debug(f"Проверка payment_id={payment_id}: обработан = {count > 0}")
        return count > 0
    except Exception as e:
        logger.error(f"Ошибка проверки платежа {payment_id}: {e}", exc_info=True)
        return True

def verify_yookassa_signature(webhook_data: Dict, signature: str) -> bool:
    """Проверяет подпись вебхука YooKassa."""
    try:
        if not YOOKASSA_WEBHOOK_SECRET:
            logger.warning("YOOKASSA_WEBHOOK_SECRET не настроен")
            return True
        signature_parts = signature.split(' ')
        if len(signature_parts) < 4:
            logger.error(f"Неверный формат подписи: {signature}")
            return False
        logger.info(f"Получена подпись YooKassa: {signature}")
        logger.info(f"Используется секрет: {YOOKASSA_WEBHOOK_SECRET[:10]}...")
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки подписи: {e}")
        return False

async def add_payment_log(user_id: int, payment_id: str, amount: float, payment_info: Dict[str, Any]) -> bool:
    """Добавляет запись о платеже в лог."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
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
        logger.error(f"Ошибка записи платежа в логи: {e}")
        return False

async def update_user_payment_stats(user_id: int, payment_amount: float) -> bool:
    """Обновляет статистику платежей пользователя."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
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
        logger.error(f"Ошибка обновления статистики платежей для user_id={user_id}: {e}")
        return False

async def get_referrer_info(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает информацию о реферере пользователя."""
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
        logger.error(f"Ошибка получения информации о реферере для user_id={user_id}: {e}")
        return None

async def handle_webhook_error(error_message: str, webhook_data: Dict = None):
    """Обрабатывает ошибки вебхука и уведомляет админов."""
    logger.error(f"Ошибка webhook: {error_message}")
    if not bot_instance or not ADMIN_IDS:
        return
    error_details = f"🚨 Ошибка обработки платежа\n\n"
    error_details += f"⚠️ Ошибка: {error_message}\n"
    error_details += f"🕒 Время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    if webhook_data:
        payment_id = webhook_data.get('object', {}).get('id', 'unknown')
        error_details += f"🆔 Payment ID: {payment_id}\n"
        metadata = webhook_data.get('object', {}).get('metadata', {})
        user_id = metadata.get('user_id', 'unknown')
        error_details += f"👤 User ID: {user_id}\n"
    try:
        for admin_id in ADMIN_IDS:
            await bot_instance.send_message(
                chat_id=admin_id,
                text=error_details,
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление об ошибке админу: {e}")

async def _send_message_async(
    bot: Bot, chat_id: int, text: str, reply_markup=None, parse_mode=ParseMode.MARKDOWN
) -> None:
    """Асинхронная отправка сообщения с fallback."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        logger.info(f"Сообщение отправлено пользователю {chat_id}.")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}", exc_info=True)

async def _handle_successful_payment_async(
    user_id: int, plan_key: str, payment_id: str, payment_amount: float, description: str, bot: Bot
) -> None:
    """Обрабатывает успешный платёж."""
    logger.info(f"Начало обработки платежа: user_id={user_id}, payment_id={payment_id}, plan_key={plan_key}")

    if await is_payment_processed_webhook(payment_id):
        logger.warning(f"Платеж {payment_id} для user_id={user_id} уже обработан.")
        return

    try:
        if not await check_referral_integrity(user_id):
            logger.error(f"Не удалось восстановить реферальную связь для user_id={user_id}")
            for admin_id in ADMIN_IDS:
                await _send_message_async(
                    bot, admin_id,
                    escape_md(f"⚠️ Не удалось восстановить реферальную связь для user_id={user_id}, payment_id={payment_id}", version=2),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        else:
            logger.debug(f"Реферальная связь проверена для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка проверки реферальной связи для user_id={user_id}: {e}", exc_info=True)

    initial_subscription = await check_database_user(user_id)
    if not initial_subscription or len(initial_subscription) < 9:
        logger.error(f"Неполные данные подписки для user_id={user_id}")
        await _send_message_async(
            bot, user_id,
            escape_md("❌ Ошибка обработки платежа. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    initial_avatars = initial_subscription[1]
    initial_first_purchase = bool(initial_subscription[5])
    
    # Определяем, является ли это первой покупкой
    previous_payments = await get_user_payments(user_id)
    payment_count = len([p for p in previous_payments if p[0] != payment_id])  # Исключаем текущий платеж
    is_first_purchase = (payment_count == 0)
    
    logger.info(f"Определение первой покупки: user_id={user_id}, previous_payments={payment_count}, "
                f"initial_first_purchase={initial_first_purchase}, is_first_purchase={is_first_purchase}")

    try:
        # Передаем правильный флаг is_first_purchase
        payment_processed_successfully = await add_resources_on_payment(
            user_id, plan_key, payment_amount, payment_id, bot, is_first_purchase=is_first_purchase
        )
        if not payment_processed_successfully:
            logger.error(f"Ошибка начисления ресурсов для user_id={user_id}, plan={plan_key}, payment_id={payment_id}")
            await _send_message_async(
                bot, user_id,
                escape_md("❌ Ошибка начисления ресурсов. Обратитесь в поддержку: @AXIDI_Help", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            for admin_id in ADMIN_IDS:
                await _send_message_async(
                    bot, admin_id,
                    escape_md(f"⚠️ Ошибка начисления ресурсов для user_id={user_id}, payment_id={payment_id}", version=2),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            return
        logger.info(f"Ресурсы начислены для user_id={user_id}, plan={plan_key}, payment_id={payment_id}")
    except Exception as e:
        logger.error(f"Исключение при начислении ресурсов для user_id={user_id}: {e}", exc_info=True)
        return

    logger.info(f"Сохранение платежа в логи...")
    tariff_info = TARIFFS.get(plan_key, {})
    photos_added = tariff_info.get('photos', 0)
    avatars_to_add = tariff_info.get('avatars', 0)

    bonus_avatar = False
    
    # Проверяем наличие реферера для начисления бонуса РЕФЕРЕРУ (не пользователю)
    referrer_info = await get_referrer_info(user_id)
    referrer_id = referrer_info['referrer_id'] if referrer_info else None
    
    # Бонусный аватар только при первой покупке
    if is_first_purchase and plan_key != 'аватар':
        avatars_to_add += 1
        bonus_avatar = True
        logger.info(f"Добавлен бонусный аватар для первой покупки user_id={user_id}")

    payment_info = {
        'tariff_key': plan_key,
        'photos_added': photos_added,
        'avatars_added': avatars_to_add,
        'is_first_purchase': is_first_purchase,
        'bonus_avatar': bonus_avatar
    }

    await add_payment_log(user_id, payment_id, payment_amount, payment_info)
    await update_user_payment_stats(user_id, payment_amount)

    # Гарантируем обновление first_purchase с повторными попытками
    if is_first_purchase:
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                logger.info(f"Попытка {attempt + 1}/{max_attempts}: Обновление статуса первой покупки для user_id={user_id}")
                await update_user_credits(user_id, action="set_first_purchase_completed")
                logger.info(f"first_purchase успешно обновлен для user_id={user_id}")
                await user_cache.invalidate(user_id)  # Инвалидируем кэш после обновления
                break
            except Exception as e:
                logger.error(f"Попытка {attempt + 1}/{max_attempts}: Ошибка обновления first_purchase для user_id={user_id}: {e}", exc_info=True)
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))  # Экспоненциальная задержка
                else:
                    logger.critical(f"Не удалось обновить first_purchase для user_id={user_id} после {max_attempts} попыток")
                    for admin_id in ADMIN_IDS:
                        await _send_message_async(
                            bot, admin_id,
                            escape_md(f"🚨 Критическая ошибка: Не удалось обновить first_purchase для user_id={user_id} после {max_attempts} попыток: {str(e)}", version=2),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )

    logger.info(f"Инвалидация кэша для user_id={user_id}")
    await user_cache.invalidate(user_id)
    logger.debug(f"Кэш инвалидирован для user_id={user_id}")

    logger.info(f"Получение актуальных данных после начисления...")
    try:
        subscription_data = await asyncio.wait_for(check_database_user(user_id), timeout=5.0)
        logger.info(f"Данные получены: subscription_data длина = {len(subscription_data) if subscription_data else 0}")

        if not subscription_data:
            logger.error(f"Нет данных подписки для user_id={user_id}")
            await _send_message_async(
                bot, user_id,
                escape_md("❌ Ошибка обновления баланса. Обратитесь в поддержку: @AXIDI_Help", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        if len(subscription_data) >= 9:
            generations_left = subscription_data[0]
            avatar_left = subscription_data[1]
            training_mode = subscription_data[2] if len(subscription_data) > 2 else None
            username = subscription_data[3] if len(subscription_data) > 3 else None
            is_notified = subscription_data[4] if len(subscription_data) > 4 else None
            first_purchase = subscription_data[5] if len(subscription_data) > 5 else None
            registration_date = subscription_data[6] if len(subscription_data) > 6 else None
            blocked = subscription_data[7] if len(subscription_data) > 7 else None
            first_name = subscription_data[8] if len(subscription_data) > 8 else None
            logger.info(f"Данные после начисления: user_id={user_id}, photos={generations_left}, avatars={avatar_left}")
        else:
            logger.error(f"Неполные данные подписки для user_id={user_id}: длина {len(subscription_data)}")
            return

    except asyncio.TimeoutError:
        logger.error(f"Таймаут при получении данных подписки для user_id={user_id}")
        return
    except Exception as e:
        logger.error(f"Ошибка при получении данных подписки: {e}", exc_info=True)
        return

    logger.info(f"Подготовка данных для уведомлений...")
    try:
        payments = await asyncio.wait_for(get_user_payments(user_id), timeout=3.0)
        payment_count = len([p for p in payments if p[0] != payment_id])  # Исключаем текущий платеж
    except:
        payment_count = 0
        logger.warning(f"Не удалось получить историю платежей для user_id={user_id}")

    try:
        referrer_text = f"ID {referrer_id}" if referrer_id else "Отсутствует"
    except:
        referrer_id = None
        referrer_text = "Отсутствует"
        logger.warning(f"Не удалось получить информацию о реферере для user_id={user_id}")

    photos = TARIFFS.get(plan_key, {}).get('photos', 0)
    avatars = TARIFFS.get(plan_key, {}).get('avatars', 0)

    avatars_added = avatar_left - initial_avatars
    bonus_avatars = 0
    if is_first_purchase and plan_key != 'аватар':
        expected_avatars = avatars + 1
        if avatars_added >= expected_avatars:
            bonus_avatars = 1

    added_text = f"{photos} печенек"
    if avatars_added > 0:
        added_text += f", {avatars_added} аватар{'ов' if avatars_added != 1 else ''}"
        if bonus_avatars:
            added_text += f" (включая бонусный)"

    logger.info(f"=== НАЧАЛО ОТПРАВКИ УВЕДОМЛЕНИЯ ПОЛЬЗОВАТЕЛЮ ===")
    try:
        bot_username = (await bot.get_me()).username.lstrip('@') or "Bot"
        description_safe = description or "Пакет"
        username_safe = username or "Пользователь"
        first_name_safe = first_name or "Пользователь"
        user_id_safe = str(user_id)

        user_message_parts = [
            "✅ Оплата прошла успешно!",
            f"📦 Пакет: {description_safe}",
        ]
        if bonus_avatars:
            user_message_parts.append("🎁 +1 аватар в подарок за первую покупку!")
        user_message_parts.extend([
            f"📸 Печенек на балансе: {generations_left}",
            f"👤 Аватары на балансе: {avatar_left}",
            "",  # Пустая строка для переноса
            "✨ Создай аватар или сгенерируй фото через /menu!",
            f"🔗 Приглашай друзей: t.me/{bot_username}?start=ref_{user_id_safe}",
        ])
        
        user_message = escape_md("\n".join(user_message_parts), version=2)

        logger.debug(f"Отправляемое сообщение пользователю user_id={user_id}:\n{user_message}")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать аватар", callback_data="train_flux")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_to_menu")]
        ])

        await bot.send_message(
            chat_id=user_id,
            text=user_message,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"✅ Уведомление успешно отправлено пользователю user_id={user_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления пользователю user_id={user_id}: {e}", exc_info=True)
        try:
            user_message_fallback_parts = [
                "✅ Оплата прошла успешно!",
                f"📦 Пакет: {description_safe}",
            ]
            if bonus_avatars:
                user_message_fallback_parts.append("🎁 +1 аватар в подарок за первую покупку!")
            user_message_fallback_parts.extend([
                f"📸 Печенек на балансе: {generations_left}",
                f"👤 Аватары на балансе: {avatar_left}",
                "",
                "✨ Создай аватар или сгенерируй фото через /menu!",
                f"🔗 Приглашай друзей: t.me/{bot_username}?start=ref_{user_id_safe}",
            ])
            
            user_message_fallback = "\n".join(user_message_fallback_parts)
            
            await bot.send_message(
                chat_id=user_id,
                text=user_message_fallback,
                reply_markup=keyboard,
                parse_mode=None
            )
            logger.info(f"✅ Fallback-уведомление без Markdown отправлено пользователю user_id={user_id}")
        except Exception as e_fallback:
            logger.error(f"❌ Ошибка отправки fallback-уведомления пользователю user_id={user_id}: {e_fallback}", exc_info=True)

    logger.info(f"=== НАЧАЛО ОТПРАВКИ УВЕДОМЛЕНИЙ АДМИНАМ ===")
    try:
        moscow_tz = pytz.timezone('Europe/Moscow')
        timestamp = datetime.now(moscow_tz).strftime('%Y-%m-%d %H:%M:%S MSK')
        payment_method_type = "YooKassa"
        plan_display = TARIFFS.get(plan_key, {}).get('display', 'Пакет')
        plan_name = plan_display.split('💎 ')[1] if '💎 ' in plan_display else plan_display

        admin_message_parts = [
            f"💸 Новый платеж: ID {payment_id[:8]}...",
            f"👤 Пользователь: {first_name_safe} (@{username_safe}, ID {user_id_safe})",
            f"📦 План: {plan_name}",
            f"💰 Сумма: {payment_amount:.2f}₽",
            f"💳 Метод оплаты: {payment_method_type}",
            "🛠 Статус: Успешно",
            f"📅 Время: {timestamp}",
            f"➕ Начислено: {added_text}",
            f"📋 Баланс: {generations_left} печенек, {avatar_left} аватар{'' if avatar_left == 1 else 'ов'}",
            f"📢 Реферал: {referrer_text}",
            f"🔢 Платежей ранее: {payment_count}",
            f"🎯 Первая покупка: {'Да' if is_first_purchase else 'Нет'}",
        ]
        
        admin_message = escape_md("\n".join(admin_message_parts), version=2)

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=admin_message,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                logger.info(f"✅ Уведомление о платеже отправлено админу {admin_id}")
            except Exception as e_admin:
                logger.error(f"❌ Ошибка отправки уведомления админу {admin_id}: {e_admin}", exc_info=True)
                try:
                    admin_message_fallback = "\n".join(admin_message_parts)
                    await bot.send_message(
                        chat_id=admin_id,
                        text=admin_message_fallback,
                        parse_mode=None
                    )
                    logger.info(f"✅ Fallback-уведомление без Markdown отправлено админу {admin_id}")
                except Exception as e_fallback:
                    logger.error(f"❌ Ошибка отправки fallback-уведомления админу {admin_id}: {e_fallback}", exc_info=True)

    except Exception as e:
        logger.error(f"❌ Общая ошибка формирования уведомления админов для user_id={user_id}: {e}", exc_info=True)

    logger.info(f"=== ЗАВЕРШЕНИЕ ОБРАБОТКИ ПЛАТЕЖА для user_id={user_id} ===")

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обрабатывает вебхуки YooKassa."""
    global dp, bot_instance, bot_event_loop
    logger.info(f"Вебхук: method={request.method}, headers={dict(request.headers)}")

    try:
        raw_body = request.get_data(as_text=True)
        logger.debug(f"Тело вебхука: {raw_body[:500]}...")

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка декодирования JSON: {e}. Тело: {raw_body[:200]}...")
            return jsonify({'status': 'error', 'message': 'Invalid JSON format'}), 400
        except Exception as e:
            logger.error(f"Ошибка получения JSON: {e}", exc_info=True)
            return jsonify({'status': 'error', 'message': 'Could not parse request body'}), 400

        signature = request.headers.get('Signature', '')
        if YOOKASSA_WEBHOOK_SECRET and signature and not verify_yookassa_signature(data, signature):
            logger.warning("Неверная подпись YooKassa webhook")
            if bot_event_loop:
                asyncio.run_coroutine_threadsafe(
                    handle_webhook_error("Invalid webhook signature", data),
                    bot_event_loop
                )
            return jsonify({'status': 'error', 'message': 'Invalid signature'}), 403

        event = data.get('event')
        if event != 'payment.succeeded':
            logger.info(f"Вебхук '{event}' не обрабатывается")
            return jsonify({'status': 'ok', 'message': 'Event not processed'}), 200

        payment_object = data.get('object')
        if not payment_object:
            logger.error("Отсутствует 'object' в вебхуке")
            if bot_event_loop:
                asyncio.run_coroutine_threadsafe(
                    handle_webhook_error("Missing 'object' in webhook", data),
                    bot_event_loop
                )
            return jsonify({'status': 'error', 'message': "Missing 'object'"}), 400

        payment_id = payment_object.get('id')
        metadata = payment_object.get('metadata', {})
        user_id_str = metadata.get('user_id')
        description = metadata.get('description_for_user', payment_object.get('description', 'Неизвестный пакет'))

        if not payment_id or not user_id_str:
            logger.error(f"Отсутствует payment_id или user_id: payment_id={payment_id}, user_id={user_id_str}")
            if bot_event_loop:
                asyncio.run_coroutine_threadsafe(
                    handle_webhook_error(f"Missing required data: payment_id={payment_id}, user_id={user_id_str}", data),
                    bot_event_loop
                )
            return jsonify({'status': 'error', 'message': 'Missing payment_id or user_id'}), 400

        try:
            user_id = int(user_id_str)
        except ValueError:
            logger.error(f"Некорректный user_id: {user_id_str}")
            if bot_event_loop:
                asyncio.run_coroutine_threadsafe(
                    handle_webhook_error(f"Invalid user_id format: {user_id_str}", data),
                    bot_event_loop
                )
            return jsonify({'status': 'error', 'message': 'Invalid user_id format'}), 400

        amount_val = payment_object.get('amount', {}).get('value', "0.0")
        try:
            payment_amount = float(amount_val)
        except ValueError:
            logger.error(f"Некорректная сумма платежа: {amount_val}")
            if bot_event_loop:
                asyncio.run_coroutine_threadsafe(
                    handle_webhook_error(f"Invalid payment amount: {amount_val}", data),
                    bot_event_loop
                )
            return jsonify({'status': 'error', 'message': 'Invalid payment amount'}), 400

        is_processed = asyncio.run(is_payment_processed_webhook(payment_id))
        if is_processed:
            logger.info(f"Платеж {payment_id} для user_id={user_id} уже обработан.")
            return jsonify({'status': 'ok', 'message': 'Payment already processed'}), 200

        plan_key = None
        for key, tariff_details in TARIFFS.items():
            if abs(tariff_details["amount"] - payment_amount) < 0.01:
                plan_key = key
                break

        if not plan_key:
            logger.error(f"Неизвестный тариф для amount={payment_amount}, user_id={user_id}")
            if bot_instance and bot_event_loop:
                for admin_id in ADMIN_IDS:
                    asyncio.run_coroutine_threadsafe(
                        _send_message_async(
                            bot_instance, admin_id,
                            escape_md(
                                f"⚠️ Неизвестный платеж!\n"
                                f"🔖 ID: `{payment_id}`\n"
                                f"👤 User ID: `{user_id}`\n"
                                f"💵 Сумма: {payment_amount:.2f} RUB\n"
                                f"📝 Описание: {escape_md(description)}",
                                version=2
                            ),
                            parse_mode=ParseMode.MARKDOWN_V2
                        ),
                        bot_event_loop
                    )
            return jsonify({'status': 'error', 'message': 'Unknown tariff plan'}), 400

        if bot_instance and bot_event_loop:
            asyncio.run_coroutine_threadsafe(
                _handle_successful_payment_async(user_id, plan_key, payment_id, payment_amount, description, bot_instance),
                bot_event_loop
            )
            logger.info(f"Платеж {payment_id} для user_id={user_id} запланирован.")
        else:
            logger.error("Экземпляр бота или event loop не инициализирован.")
            return jsonify({'status': 'error', 'message': 'Bot instance not initialized'}), 500

        return jsonify({'status': 'ok', 'message': 'Webhook received'}), 200

    except Exception as e:
        logger.error(f"Критическая ошибка в webhook: {e}", exc_info=True)
        if bot_event_loop:
            asyncio.run_coroutine_threadsafe(
                handle_webhook_error(f"Critical error: {str(e)}", data if 'data' in locals() else None),
                bot_event_loop
            )
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Проверяет состояние бота."""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'bot_ready': bot_instance is not None,
        'event_loop_ready': bot_event_loop is not None
    }), 200

async def process_scheduled_broadcasts(bot: Bot) -> None:
    """Обрабатывает запланированные рассылки."""
    try:
        broadcasts = await get_scheduled_broadcasts(bot=bot)
        if not broadcasts:
            logger.debug("Нет запланированных рассылок для обработки")
            return
        for broadcast in broadcasts:
            broadcast_id = broadcast['id']
            if not isinstance(broadcast_id, int) or broadcast_id <= 0:
                logger.error(f"Некорректный broadcast_id: {broadcast_id}")
                continue
            broadcast_data = broadcast.get('broadcast_data', {})
            if not isinstance(broadcast_data, dict):
                logger.error(f"Некорректные данные broadcast_data для broadcast_id={broadcast_id}")
                continue
            message_text = broadcast_data.get('message', '')
            media = broadcast_data.get('media', None)
            media_type = media.get('type') if media else None
            media_id = media.get('file_id') if media else None
            target_group = broadcast_data.get('broadcast_type', 'all')
            admin_user_id = broadcast_data.get('admin_user_id', ADMIN_IDS[0])
            # Извлекаем кнопки из таблицы broadcast_buttons
            buttons = await get_broadcast_buttons(broadcast_id)
            # Если кнопки не найдены в таблице, используем резерв из broadcast_data
            if not buttons and 'buttons' in broadcast_data:
                buttons = broadcast_data.get('buttons', [])
                logger.debug(f"Кнопки для broadcast_id={broadcast_id} взяты из broadcast_data: {buttons}")
            scheduled_time = broadcast.get('scheduled_time')
            if not scheduled_time:
                logger.warning(f"Рассылка ID {broadcast_id} пропущена: отсутствует scheduled_time")
                continue
            logger.info(f"Выполняется рассылка ID {broadcast_id} для группы {target_group} на {scheduled_time}")

            # Очищаем текст от возможного экранирования и экранируем заново
            raw_message = unescape_markdown(message_text)
            logger.debug(f"Очищенный текст сообщения для broadcast_id={broadcast_id}: {raw_message[:100]}...")
            signature = "🍪 PixelPie"
            caption = raw_message + ("\n\n" + signature if raw_message.strip() else "\n" + signature)
            escaped_caption = escape_message_parts(caption, version=2)
            logger.debug(f"Экранированный текст для отправки: {escaped_caption[:100]}...")

            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Да, хочу! 💳", callback_data="subscribe")]
            ]) if broadcast_data.get('with_payment_button', False) else None

            try:
                async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET status = 'completed' WHERE id = ?",
                        (broadcast_id,)
                    )
                    await conn.commit()
                logger.debug(f"Статус рассылки ID {broadcast_id} обновлен на completed")
                if target_group == 'all':
                    await broadcast_message_admin(bot, escaped_caption, admin_user_id, media_type, media_id, buttons)
                elif target_group == 'paid':
                    await broadcast_to_paid_users(bot, escaped_caption, admin_user_id, media_type, media_id, buttons)
                elif target_group == 'non_paid':
                    await broadcast_to_non_paid_users(bot, escaped_caption, admin_user_id, media_type, media_id, buttons)
                elif target_group.startswith('with_payment'):
                    await broadcast_with_payment(bot, escaped_caption, admin_user_id, media_type, media_id, buttons)
                else:
                    logger.warning(f"Неизвестная группа рассылки для ID {broadcast_id}: {target_group}")
                    continue
                logger.info(f"Рассылка ID {broadcast_id} завершена")
            except Exception as e:
                logger.error(f"Ошибка выполнения рассылки ID {broadcast_id}: {e}", exc_info=True)
                for admin_id in ADMIN_IDS:
                    try:
                        await send_message_with_fallback(
                            bot, admin_id,
                            escape_message_parts(
                                f"🚨 Ошибка выполнения рассылки ID {broadcast_id} для группы {target_group}: {str(e)}",
                                version=2
                            ),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    except Exception as e_notify:
                        logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")
    except Exception as e:
        logger.error(f"Ошибка в фоновой задаче рассылок: {e}", exc_info=True)
        for admin_id in ADMIN_IDS:
            try:
                await send_message_with_fallback(
                    bot, admin_id,
                    escape_message_parts(f"🚨 Ошибка в фоновой задаче рассылок: {str(e)}", version=2),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e_notify:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")

async def run_checks(bot: Bot) -> None:
    """Запускает проверки задач генерации."""
    try:
        if callable(check_pending_trainings):
            logger.info("Запуск проверки задач обучения...")
            await check_pending_trainings(bot)
            logger.info("Проверка задач обучения завершена.")
        if callable(check_pending_video_tasks):
            logger.info("Запуск проверки задач видео...")
            await check_pending_video_tasks(bot)
            logger.info("Проверка задач видео завершена.")
    except Exception as e:
        logger.error(f"Ошибка в run_checks: {e}", exc_info=True)

def run_flask():
    """Запускает Flask-сервер."""
    logger.info("Запуск Flask сервера на 0.0.0.0:8000")
    app.run(host='0.0.0.0', port=8000, debug=False)

async def notify_startup() -> None:
    """Уведомляет о запуске бота."""
    if not bot_instance:
        return
    try:
        await bot_instance.send_message(
            chat_id=706527960,
            text=escape_md("🚀 Бот успешно запущен! Все системы работают в штатном режиме.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info("Уведомление о запуске отправлено владельцу")
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление владельцу: {e}")
    try:
        await bot_instance.send_message(
            chat_id=1154672896,
            text=escape_md("🚀 Бот успешно запущен! Все системы работают в штатном режиме.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info("Уведомление о запуске отправлено Зойдбергу")
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление Зойдбергу: {e}")

async def init_payment_tables():
    """Инициализирует таблицы для платежей и добавляет столбец last_reminder_type."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            # Проверяем, существует ли столбец last_reminder_type
            await db.execute("PRAGMA table_info(users)")
            columns = await db.fetchall()
            column_names = [col[1] for col in columns]
            if 'last_reminder_type' not in column_names:
                await db.execute("ALTER TABLE users ADD COLUMN last_reminder_type TEXT")
                logger.info("Столбец last_reminder_type добавлен в таблицу users")
            await db.execute("""
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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_payment_stats (
                    user_id INTEGER PRIMARY KEY,
                    total_payments INTEGER DEFAULT 0,
                    total_amount REAL DEFAULT 0.0,
                    first_payment_date TEXT,
                    last_payment_date TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS referral_rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER NOT NULL,
                    referred_user_id INTEGER NOT NULL,
                    reward_amount REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                    FOREIGN KEY (referred_user_id) REFERENCES users (user_id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS referral_stats (
                    user_id INTEGER PRIMARY KEY,
                    total_referrals INTEGER DEFAULT 0,
                    total_rewards REAL DEFAULT 0.0,
                    updated_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_payment_logs_user_id ON payment_logs (user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_payment_logs_created_at ON payment_logs (created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_payment_logs_payment_id ON payment_logs (payment_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON referral_rewards (referrer_id)")
            await db.commit()
            logger.info("Дополнительные таблицы для платежей инициализированы")
    except Exception as e:
        logger.error(f"Ошибка инициализации таблиц платежей: {e}")

async def main():
    """Основная функция запуска бота."""
    global bot_instance, dp, bot_event_loop
    try:
        logger.info("=== ЗАПУСК TELEGRAM БОТА ===")
        logger.info("Инициализация дополнительных таблиц для платежей...")
        await init_payment_tables()
        logger.info("Инициализация базы данных...")
        await init_db()
        logger.info("База данных инициализирована")
        logger.info("Создание экземпляра бота...")
        bot_instance = Bot(token=TOKEN)
        dp = Dispatcher()
        bot_info = await bot_instance.get_me()
        logger.info(f"Экземпляр бота создан: @{bot_info.username}")
        # Инициализация модуля Фото Преображение
        REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
        if REPLICATE_API_KEY:
            init_photo_generator(REPLICATE_API_KEY)
            logger.info("✅ Модуль Фото Преображение инициализирован")
        else:
            logger.warning("❌ REPLICATE_API_KEY не найден! Функция Фото Преображение недоступна")
        from aiogram.filters import BaseFilter

        # Универсальный фильтр для администраторов с проверкой состояния FSM
        class AdminStateFilter(BaseFilter):
            def __init__(self, state_key: str):
                self.state_key = state_key

            async def __call__(self, message: Message, state: FSMContext):
                return (
                    message.from_user.id in ADMIN_IDS and
                    (await state.get_state()) == self.state_key
                )

        # Регистрация FSM-роутера
        setup_onboarding_handlers()
        setup_conversation_handler(dp)
        logger.info("Обработчики FSM зарегистрированы")
        dp.include_router(photo_transform_router)
        
        # Регистрация дополнительных роутеров
        dp.include_router(broadcast_router)
        dp.include_router(admin_callbacks_router)
        dp.include_router(onboarding_router)
        dp.include_router(user_callbacks_router)
        dp.include_router(referrals_callbacks_router)
        dp.include_router(utils_callbacks_router)
        dp.include_router(user_management_router)
        dp.include_router(payments_router)
        dp.include_router(visualization_router)
        dp.include_router(bot_counter_router)
        dp.include_router(video_router)
        dp.include_router(training_router)
        

        # Регистрация обработчиков команд
        dp.message.register(cancel, Command("cancel"))
        dp.message.register(start, Command("start"))
        dp.message.register(menu, Command("menu"))
        dp.message.register(help_command, Command("help"))
        dp.message.register(check_training, Command("check_training"))
        dp.message.register(debug_avatars, Command("debug_avatars"))
        dp.message.register(list_scheduled_broadcasts, Command("manage_broadcasts"))
        dp.message.register(cmd_bot_name, Command("botname"))

        # Специфичные обработчики для текстовых сообщений
        dp.message.register(
            handle_broadcast_message,
            AdminStateFilter(BotStates.AWAITING_BROADCAST_MESSAGE)
        )
        dp.message.register(
            handle_broadcast_schedule_time,
            AdminStateFilter(BotStates.AWAITING_BROADCAST_SCHEDULE)
        )
        dp.message.register(
            handle_broadcast_button_input,
            AdminStateFilter(BotStates.AWAITING_BROADCAST_BUTTON_INPUT)
        )
        dp.message.register(
            handle_payments_date_input,
            AdminStateFilter(BotStates.AWAITING_PAYMENT_DATES)
        )
        dp.message.register(
            handle_balance_change_input,
            AdminStateFilter(BotStates.AWAITING_BALANCE_CHANGE)
        )
        dp.message.register(
            handle_block_reason_input,
            AdminStateFilter(BotStates.AWAITING_BLOCK_REASON)
        )
        dp.message.register(
            handle_activity_dates_input,
            AdminStateFilter(BotStates.AWAITING_ACTIVITY_DATES)
        )
        dp.message.register(
            handle_user_search_input,
            AdminStateFilter(BotStates.AWAITING_USER_SEARCH)
        )
        dp.message.register(
            handle_admin_custom_prompt,
            AdminStateFilter(BotStates.AWAITING_ADMIN_PROMPT)
        )

        # Обработчики для фото и видео
        dp.message.register(handle_photo, lambda message: message.content_type == ContentType.PHOTO)
        dp.message.register(handle_video, lambda message: message.content_type == ContentType.VIDEO)

        # Общий обработчик текста (должен быть последним)
        dp.message.register(handle_text, lambda message: message.content_type == ContentType.TEXT)

        dp.error.register(error_handler)
        logger.info("Все обработчики зарегистрированы")

        # Настройка планировщика задач
        scheduler = AsyncIOScheduler(timezone=pytz.timezone('Europe/Moscow'))
        scheduler.add_job(
            send_daily_payments_report,
            trigger=CronTrigger(hour=10, minute=0, timezone=pytz.timezone('Europe/Moscow')),
            args=[bot_instance],
            misfire_grace_time=300,
            id='daily_report'
        )
        scheduler.add_job(
            process_scheduled_broadcasts,
            trigger=CronTrigger(minute='*', timezone=pytz.timezone('Europe/Moscow')),
            args=[bot_instance],
            misfire_grace_time=30,
            id='scheduled_broadcasts'
        )
        scheduler.add_job(
            check_pending_video_tasks,
            trigger=CronTrigger(minute='*/5', timezone=pytz.timezone('Europe/Moscow')),
            args=[bot_instance],
            misfire_grace_time=60,
            id='check_pending_videos'
        )
        scheduler.add_job(
            check_pending_trainings,
            trigger=CronTrigger(minute='*/5', timezone=pytz.timezone('Europe/Moscow')),
            args=[bot_instance],
            misfire_grace_time=60,
            id='check_pending_trainings'
        )
        scheduler.start()
        logger.info("Планировщик задач запущен")

        # Запуск проверки задач при старте
        logger.info("Запуск проверки задач при старте...")
        asyncio.create_task(run_checks(bot_instance))
        asyncio.create_task(check_pending_video_tasks(bot_instance))
        asyncio.create_task(check_pending_trainings(bot_instance))

        # Запуск Flask-сервера
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("Flask сервер запущен в потоке.")

        # Запуск бота в режиме polling
        logger.info("Запуск бота в режиме polling...")
        bot_event_loop = asyncio.get_running_loop()
        await bot_counter.start(bot_instance)
        logger.info("Счетчик пользователей в имени бота запущен")
        await notify_startup()
        await dp.start_polling(bot_instance, allowed_updates=["message", "callback_query"], drop_pending_updates=True)
        logger.info("✅ Бот успешно запущен и работает!")

    except (KeyboardInterrupt, SystemExit):
        logger.info("Получен сигнал остановки...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        logger.info("Остановка бота...")
        if 'scheduler' in locals():
            scheduler.shutdown(wait=True)
            logger.info("Планировщик остановлен")
        if bot_instance:
            logger.info("Счетчик пользователей не имеет метода stop, пропускаем")
            await bot_instance.session.close()
            logger.info("Сессия бота закрыта")
        logger.info("Бот полностью остановлен.")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Программа завершена пользователем.")
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {e}", exc_info=True)