# handlers/onboarding.py

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import pytz
from aiogram import Bot, Router
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, Message, CallbackQuery, InputMediaPhoto
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiosqlite
from config import DATABASE_PATH, TARIFFS, ADMIN_IDS
from handlers.utils import safe_escape_markdown as escape_md, get_tariff_text
from database import check_database_user, get_user_payments, is_old_user
from keyboards import create_subscription_keyboard, create_main_menu_keyboard

logger = logging.getLogger(__name__)

onboarding_router = Router()

# Примеры изображений для приветственного сообщения
EXAMPLE_IMAGES = [
    "images/example1.jpg",
    "images/example2.jpg",
    "images/example3.jpg",
]

async def send_onboarding_message(bot: Bot, user_id: int, message_type: str, subscription_data: Optional[tuple] = None, first_purchase: bool = False) -> None:
    """Отправляет сообщения онбординга в зависимости от типа и уведомляет админов о напоминаниях."""
    logger.debug(f"Отправка сообщения типа {message_type} для user_id={user_id}")
    bot_username = (await bot.get_me()).username.lstrip('@') or "PixelPieBot"
    username = subscription_data[3] if subscription_data and len(subscription_data) > 3 else "Пользователь"
    first_name = subscription_data[8] if subscription_data and len(subscription_data) > 8 else "Пользователь"
    
    # Проверяем, является ли пользователь старым
    is_old_user_flag = await is_old_user(user_id, cutoff_date="2025-07-11")
    logger.debug(f"Пользователь user_id={user_id} is_old_user={is_old_user_flag}")
    
    # Если пользователь старый, не отправляем напоминания
    if is_old_user_flag and message_type.startswith("reminder_"):
        logger.info(f"Напоминание {message_type} НЕ отправлено для user_id={user_id}: пользователь старый")
        return

    moscow_tz = pytz.timezone('Europe/Moscow')
    
    # Проверяем валидность даты регистрации
    registration_date = datetime.now(moscow_tz)
    if subscription_data and len(subscription_data) > 10 and subscription_data[10]:
        try:
            registration_date = moscow_tz.localize(datetime.strptime(subscription_data[10], '%Y-%m-%d %H:%M:%S'))
        except ValueError as e:
            logger.warning(f"Невалидный формат даты в subscription_data[10] для user_id={user_id}: {subscription_data[10]}. Используется текущая дата. Ошибка: {e}")
            logger.debug(f"Содержимое subscription_data для user_id={user_id}: {subscription_data}")
    
    current_time = datetime.now(moscow_tz)
    days_since_registration = (current_time.date() - registration_date.date()).days
    time_since_registration = (current_time - registration_date).total_seconds()
    logger.debug(f"Для user_id={user_id}: days_since_registration={days_since_registration}, time_since_registration={time_since_registration} секунд")
    
    # Проверяем, является ли пользователь оплатившим
    payments = await get_user_payments(user_id)
    is_paying_user = len(payments) > 0
    logger.debug(f"Проверка оплаты для user_id={user_id}: is_paying_user={is_paying_user}, payments={payments}")
    if not first_purchase and subscription_data and len(subscription_data) > 5:
        first_purchase = bool(subscription_data[5])
    logger.debug(f"first_purchase для user_id={user_id}: {first_purchase}")

    # Определяем тариф для сообщения
    tariff_key = None
    if message_type == "proceed_to_tariff":
        if days_since_registration == 0:
            if time_since_registration <= 1800:  # До 30 минут
                tariff_key = "комфорт"
            elif time_since_registration <= 5400:  # 30–90 минут
                tariff_key = "лайт"
            else:  # После 90 минут
                tariff_key = "мини"
        elif days_since_registration == 1:
            tariff_key = "лайт"
        elif days_since_registration <= 3:
            tariff_key = "мини"
        else:
            tariff_key = None  # Все тарифы
    elif message_type == "tariff_комфорт":
        tariff_key = "комфорт"
    elif message_type == "tariff_лайт":
        tariff_key = "лайт"
    elif message_type == "tariff_мини":
        tariff_key = "мини"
    elif message_type in ("reminder_day2", "reminder_day3", "reminder_day4", "reminder_day5"):
        if message_type == "reminder_day2" and days_since_registration == 1:  # Второй день
            tariff_key = "лайт"
        elif message_type == "reminder_day3" and days_since_registration == 2:  # Третий день
            tariff_key = "лайт"
        elif message_type == "reminder_day4" and days_since_registration == 3:  # Четвёртый день
            tariff_key = "мини"
        elif message_type == "reminder_day5" and days_since_registration >= 4:  # Пятый день
            tariff_key = None  # Все тарифы
    logger.debug(f"Выбран tariff_key={tariff_key} для message_type={message_type}")

    messages = {
        "welcome": {
            "text": escape_md(
                f"Привет, {first_name}! Я — PixelPie 🍪\n"
                "Твоя нейросеть для создания стильных фото с твоим лицом. Никакого фотошопа — только магия пикселей!\n\n"
                "Здесь ты можешь:\n"
                "🍪 Создать аватар по своим фото\n"
                "🍪 Выбрать стиль или описать свой\n"
                "🍪 Генерировать фото, оживлять их и вдохновляться идеями\n"
                "🍪 Смотреть образы в канале: @pixelpie_idea\n\n"
                "Вот примеры того, что ты получишь:\n"
                "Готов? Жми ниже, начнём!",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🚀 Начать",
                    callback_data="proceed_to_tariff" if not is_paying_user else "subscribe"
                )]
            ]),
            "with_images": True
        },
        "proceed_to_tariff": {
            "text": escape_md(
                f"Привет, {first_name}! Давай выберем подходящий тариф для старта! 🚀\n\n" +
                ("💎 Тариф 'Комфорт' за 1199₽\n"
                 "🍪 Специальная цена только при первом запуске!\n\n"
                 "1199₽ вместо 2999₽ — скидка 60%\n"
                 "⏳ Только в первые 30 минут!\n\n"
                 "Ты получаешь:\n"
                 "✅ 70 фото высокого качества\n"
                 "✅ 1 аватар в подарок при первой покупке\n"
                 "✅ Генерация по описанию\n"
                 "✅ Оживление фото\n"
                 "✅ Идеи из канала: @pixelpie_idea\n\n"
                 "📥 Сделай аватар, как у топовых блогеров — без студии и фотошопа" if days_since_registration == 0 and time_since_registration <= 1800 else
                 "⏳ Тариф 'Лайт' за 599₽\n"
                 "🍪 Последний шанс взять пробный старт!\n\n"
                 "🔥 599₽ вместо 2999₽ — скидка 80%\n\n"
                 "Ты получаешь:\n"
                 "✅ 30 фото\n"
                 "✅ 1 аватар в подарок при первой покупке\n"
                 "✅ Генерация по описанию\n"
                 "✅ Оживление фото\n"
                 "✅ Идеи из канала @pixelpie_idea" if (days_since_registration == 0 and time_since_registration > 1800 and time_since_registration <= 5400) or days_since_registration == 1 else
                 "🧪 Тариф 'Мини' за 399₽\n"
                 "🍪 Тестовый пакет — без обязательств и больших вложений:\n\n"
                 "✅ 10 фото\n"
                 "✅ 1 аватар в подарок при первой покупке\n"
                 "✅ Генерация по твоему описанию\n"
                 "✅ Доступ к идеям из @pixelpie_idea\n"
                 "💳 Всего 399₽ — чтобы понять, насколько тебе заходит PixelPie!\n"
                 "😱 Такое предложение больше не появится!" if days_since_registration <= 3 else
                 "🍪 Последняя печенька, мой друг! 🍪\n"
                 "Твоя персональная скидка скоро исчезнет…\n"
                 "А ты так и не попробовал, на что способен PixelPie.\n\n"
                 "⏳ Выбери тариф и начни создавать крутые фото:\n\n"
                 "✔️ 1199₽ за полный пакет (вместо 2999₽)\n"
                 "✔️ Или 599₽ за пробный старт\n"
                 "✔️ Или 399₽ за тестовый пакет\n"
                 "✔️ Или 590₽ только за аватар\n\n"
                 "📸 Ты получишь доступ к созданию аватара и начнёшь генерировать фото с собой — в любом образе.\n\n"
                 "Хочешь успеть?"),
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="💳 Выбрать этот тариф",
                    callback_data="pay_1199" if days_since_registration == 0 and time_since_registration <= 1800 else
                                 "pay_599" if (days_since_registration == 0 and time_since_registration > 1800 and time_since_registration <= 5400) or days_since_registration == 1 else
                                 "pay_399"
                )] if days_since_registration <= 3 else [
                    InlineKeyboardButton(text="💎 1199₽ за 70 печенек", callback_data="pay_1199"),
                    InlineKeyboardButton(text="💎 599₽ за 30 печенек", callback_data="pay_599"),
                    InlineKeyboardButton(text="💎 399₽ за 10 печенек", callback_data="pay_399"),
                    InlineKeyboardButton(text="💎 Только аватар (590₽)", callback_data="pay_590")
                ],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
            ]) if days_since_registration <= 3 else await create_subscription_keyboard(hide_mini_tariff=False),
            "with_images": False
        },
        "tariff_комфорт": {
            "text": escape_md(
                f"Привет, {first_name}! Давай выберем подходящий тариф для старта! 🚀\n\n"
                "💎 Тариф 'Комфорт' за 1199₽\n"
                "🍪 Специальная цена только при первом запуске!\n\n"
                "1199₽ вместо 2999₽ — скидка 60%\n"
                "⏳ Только в первые 30 минут!\n\n"
                "Ты получаешь:\n"
                "✅ 70 фото высокого качества\n"
                "✅ 1 аватар в подарок при первой покупке\n"
                "✅ Генерация по описанию\n"
                "✅ Оживление фото\n"
                "✅ Идеи из канала: @pixelpie_idea\n\n"
                "📥 Сделай аватар, как у топовых блогеров — без студии и фотошопа",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Выбрать этот тариф", callback_data="pay_1199")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
            ]),
            "with_images": False
        },
        "tariff_лайт": {
            "text": escape_md(
                f"Привет, {first_name}! Не успел на первый тариф? 🚀\n\n"
                "⏳ Тариф 'Лайт' за 599₽\n"
                "🍪 Последний шанс взять пробный старт!\n\n"
                "🔥 599₽ вместо 2999₽ — скидка 80%\n\n"
                "Ты получаешь:\n"
                "✅ 30 фото\n"
                "✅ 1 аватар в подарок при первой покупке\n"
                "✅ Генерация по описанию\n"
                "✅ Оживление фото\n"
                "✅ Идеи из канала @pixelpie_idea",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Выбрать этот тариф", callback_data="pay_599")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
            ]),
            "with_images": False
        },
        "tariff_мини": {
            "text": escape_md(
                f"Привет, {first_name}! Хочешь просто попробовать? 🚀\n\n"
                "🧪 Тариф 'Мини' за 399₽\n"
                "🍪 Тестовый пакет — без обязательств и больших вложений:\n\n"
                "✅ 10 фото\n"
                "✅ 1 аватар в подарок при первой покупке\n"
                "✅ Генерация по твоему описанию\n"
                "✅ Доступ к идеям из @pixelpie_idea\n"
                "💳 Всего 399₽ — чтобы понять, насколько тебе заходит PixelPie!\n"
                "😱 Такое предложение больше не появится!",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Выбрать этот тариф", callback_data="pay_399")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
            ]),
            "with_images": False
        },
        "reminder_day2": {
            "text": escape_md(
                f"🍪 PixelPie снова на связи, {first_name}!\n\n"
                "Ты запустил меня… и всё. А я уже настроился создавать тебе крутые фото 😢\n\n"
                "Первые шаги простые:\n"
                "1. Выбираешь тариф\n"
                "2. Загружаешь свои фото\n"
                "3. Я создаю твой цифровой аватар\n"
                "4. И… магия! ✨\n\n"
                "🎁 Специальная цена: 30 печенек + 1 аватар в подарок за 599₽",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Начать и создать аватар", callback_data="pay_599")]
            ]),
            "with_images": False
        },
        "reminder_day3": {
            "text": escape_md(
                f"🍪 Эй, {first_name}! PixelPie зовёт тебя!\n\n"
                "Ты ещё не создал свой цифровой аватар 😱\n"
                "Значит, не попробовал генерацию — а это же самое вкусное!\n\n"
                "Вот что тебя ждёт:\n"
                "✅ 1 аватар в подарок при покупке\n"
                "✅ 30 фото в различных стилях\n"
                "✅ Оживление — фото с эмоциями и движением!\n\n"
                "🔥 Всё это за 599₽",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Хочу попробовать сейчас", callback_data="pay_599")]
            ]),
            "with_images": False
        },
        "reminder_day4": {
            "text": escape_md(
                f"🍪 Почти не считается, {first_name}!\n\n"
                "Ты запустил PixelPie, но так и не сделал первый шаг —\n"
                "а именно не выбрал пакет и не создал аватар.\n\n"
                "Исправим?\n\n"
                "🎁 Держи спецпредложение: 10 печенек + 1 аватар в подарок за 399₽\n\n"
                "Это меньше, чем один кофе на доставке ☕\n"
                "Но с кучей крутых образов.",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Забрать за 399₽ и начать", callback_data="pay_399")]
            ]),
            "with_images": False
        },
        "reminder_day5": {
            "text": escape_md(
                f"🍪 Последняя печенька, {first_name} 🍪\n\n"
                "Твоя персональная скидка скоро исчезнет…\n"
                "А ты так и не попробовал, на что способен PixelPie.\n\n"
                "⏳ Выбери тариф и начни создавать крутые фото:\n\n"
                "✔️ 1199₽ за полный пакет (вместо 2999₽)\n"
                "✔️ Или 599₽ за пробный старт\n"
                "✔️ Или 399₽ за тестовый пакет\n"
                "✔️ Или 590₽ только за аватар\n\n"
                "📸 Ты получишь доступ к созданию аватара и начнёшь генерировать фото с собой — в любом образе.\n\n"
                "Хочешь успеть?",
                version=2
            ),
            "keyboard": InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 1199₽ за 70 печенек", callback_data="pay_1199")],
                [InlineKeyboardButton(text="💎 599₽ за 30 печенек", callback_data="pay_599")],
                [InlineKeyboardButton(text="💎 399₽ за 10 печенек", callback_data="pay_399")],
                [InlineKeyboardButton(text="💎 Только аватар (590₽)", callback_data="pay_590")]
            ]),
            "with_images": False
        }
    }

    message_data = messages.get(message_type)
    if not message_data:
        logger.error(f"Неизвестный тип сообщения: {message_type} для user_id={user_id}")
        return

    # Проверка оплаты для всех сообщений, кроме welcome
    if message_type != "welcome" and is_paying_user:
        logger.debug(f"Пользователь {user_id} уже оплатил, показываем все тарифы")
        tariff_message_text = get_tariff_text(first_purchase=first_purchase, is_paying_user=True)
        subscription_kb = await create_subscription_keyboard(hide_mini_tariff=False)
        await bot.send_message(
            chat_id=user_id,
            text=tariff_message_text,
            reply_markup=subscription_kb,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Проверка актуальности тарифа для тарифных сообщений и напоминаний
    if message_type.startswith(("tariff_", "reminder_")) and tariff_key:
        expected_tariff = None
        if message_type.startswith("tariff_"):
            # Для тарифных сообщений в первый день
            if days_since_registration == 0:
                if time_since_registration <= 1800:  # До 30 минут
                    expected_tariff = "комфорт"
                elif time_since_registration <= 5400:  # 30–90 минут
                    expected_tariff = "лайт"
                else:  # После 90 минут
                    expected_tariff = "мини"
        else:
            # Для напоминаний по дням
            if message_type == "reminder_day2" and days_since_registration == 1:  # Второй день
                expected_tariff = "лайт"
            elif message_type == "reminder_day3" and days_since_registration == 2:  # Третий день
                expected_tariff = "лайт"
            elif message_type == "reminder_day4" and days_since_registration == 3:  # Четвёртый день
                expected_tariff = "мини"
            elif message_type == "reminder_day5" and days_since_registration >= 4:  # Пятый день
                expected_tariff = None  # Все тарифы
        
        if expected_tariff and tariff_key != expected_tariff:
            logger.warning(f"Тариф {tariff_key} неактуален для user_id={user_id} на день {days_since_registration}, ожидается {expected_tariff}")
            new_message_type = f"tariff_{expected_tariff}" if expected_tariff in ("комфорт", "лайт", "мини") else "subscribe"
            await send_onboarding_message(bot, user_id, new_message_type, subscription_data, first_purchase=first_purchase)
            return

    try:
        if message_data.get("with_images"):
            # Формируем медиагруппу для изображений
            media_group = []
            for img_path in EXAMPLE_IMAGES:
                if os.path.exists(img_path):
                    media_group.append(InputMediaPhoto(media=FSInputFile(path=img_path)))
                else:
                    logger.warning(f"Изображение не найдено: {img_path}")
            if media_group:
                await bot.send_media_group(
                    chat_id=user_id,
                    media=media_group
                )
                logger.info(f"Медиагруппа с {len(media_group)} изображениями отправлена пользователю {user_id}")
            else:
                logger.warning(f"Нет доступных изображений для медиагруппы для user_id={user_id}")
        
        await bot.send_message(
            chat_id=user_id,
            text=message_data["text"],
            reply_markup=message_data["keyboard"],
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Сообщение {message_type} отправлено пользователю {user_id}")
        
        # Обновление статуса отправки напоминания и уведомление админов
        if message_type.startswith("reminder_"):
            async with aiosqlite.connect(DATABASE_PATH) as conn:
                c = await conn.cursor()
                await c.execute(
                    "UPDATE users SET last_reminder_type = ?, last_reminder_sent = ? WHERE user_id = ?",
                    (message_type, datetime.now(moscow_tz).strftime('%Y-%m-%d %H:%M:%S'), user_id)
                )
                await conn.commit()
                logger.debug(f"Статус напоминания {message_type} обновлён для user_id={user_id}")
            
            # Уведомление админов об успешной отправке напоминания
            admin_message = escape_md(
                f"📬 Напоминание '{message_type}' успешно отправлено пользователю ID {user_id} (@{username})",
                version=2
            )
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=admin_message,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    logger.info(f"Уведомление о напоминании {message_type} отправлено админу {admin_id}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу {admin_id} для user_id={user_id}: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения {message_type} для user_id={user_id}: {e}", exc_info=True)
        await bot.send_message(
            chat_id=user_id,
            text=escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        # Уведомление админов об ошибке
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=escape_md(f"🚨 Ошибка отправки напоминания '{message_type}' для user_id={user_id}: {str(e)}", version=2),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                logger.info(f"Уведомление об ошибке отправки {message_type} отправлено админу {admin_id}")
            except Exception as e_admin:
                logger.error(f"Ошибка отправки уведомления об ошибке админу {admin_id}: {e_admin}")

async def schedule_tariff_messages(bot: Bot, user_id: int) -> None:
    """Планирует отправку тарифных сообщений: Лайт через 30 минут, Мини через 90 минут."""
    try:
        subscription_data = await check_database_user(user_id)
        if not subscription_data:
            logger.error(f"Неполные данные подписки для user_id={user_id}")
            return

        payments = await get_user_payments(user_id)
        if payments:
            logger.debug(f"Пользователь {user_id} уже имеет платежи, тарифные сообщения не планируются")
            return

        moscow_tz = pytz.timezone('Europe/Moscow')
        current_time = datetime.now(moscow_tz)
        
        # Проверяем валидность даты регистрации
        registration_date = current_time
        if subscription_data and len(subscription_data) > 10 and subscription_data[10]:
            try:
                registration_date = moscow_tz.localize(datetime.strptime(subscription_data[10], '%Y-%m-%d %H:%M:%S'))
            except ValueError as e:
                logger.warning(f"Невалидный формат даты в subscription_data[10] для user_id={user_id}: {subscription_data[10]}. Используется текущая дата. Ошибка: {e}")
                logger.debug(f"Содержимое subscription_data для user_id={user_id}: {subscription_data}")

        # Планируем тарифные сообщения
        tariff_messages = [
            ("tariff_лайт", registration_date + timedelta(minutes=30)),
            ("tariff_мини", registration_date + timedelta(minutes=90)),
        ]

        scheduler = AsyncIOScheduler(timezone=moscow_tz)
        for tariff_type, schedule_time in tariff_messages:
            job_id = f"tariff_{tariff_type}_{user_id}"
            if scheduler.get_job(job_id):
                logger.debug(f"Задача {job_id} уже запланирована для user_id={user_id}, пропускаем")
                continue
            if schedule_time <= current_time:
                logger.warning(f"Попытка запланировать задачу {job_id} в прошлом: {schedule_time}")
                await send_onboarding_message(bot, user_id, tariff_type, subscription_data)
                continue
            logger.info(f"Планируем задачу {job_id} для user_id={user_id} на {schedule_time}")
            scheduler.add_job(
                send_onboarding_message,
                trigger='date',
                run_date=schedule_time,
                args=[bot, user_id, tariff_type, subscription_data],
                id=job_id,
                misfire_grace_time=300
            )
        scheduler.start()
        logger.info(f"Тарифные сообщения запланированы для user_id={user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка планирования тарифных сообщений для user_id={user_id}: {e}", exc_info=True)

async def schedule_onboarding_reminders(bot: Bot, user_id: int) -> None:
    """Планирует напоминания для пользователей, не оплативших подписку, начиная со второго дня."""
    try:
        subscription_data = await check_database_user(user_id)
        if not subscription_data or len(subscription_data) < 11:
            logger.error(f"Неполные данные подписки для user_id={user_id}")
            return
        
        payments = await get_user_payments(user_id)
        if payments:
            logger.debug(f"Пользователь {user_id} уже имеет платежи, напоминания не планируются")
            return

        # Проверяем, является ли пользователь старым
        is_old_user_flag = await is_old_user(user_id, cutoff_date="2025-07-11")
        if is_old_user_flag:
            logger.info(f"Напоминания НЕ запланированы для user_id={user_id}: пользователь старый")
            return

        moscow_tz = pytz.timezone('Europe/Moscow')
        current_time = datetime.now(moscow_tz)
        
        # Проверяем валидность даты регистрации
        registration_date = current_time
        if subscription_data and len(subscription_data) > 10 and subscription_data[10]:
            try:
                registration_date = moscow_tz.localize(datetime.strptime(subscription_data[10], '%Y-%m-%d %H:%M:%S'))
            except ValueError as e:
                logger.warning(f"Невалидный формат даты в subscription_data[10] для user_id={user_id}: {subscription_data[10]}. Используется текущая дата. Ошибка: {e}")
                logger.debug(f"Содержимое subscription_data для user_id={user_id}: {subscription_data}")
        
        # Планируем напоминания, начиная со второго дня
        reminders = [
            ("reminder_day2", registration_date + timedelta(days=1)),
            ("reminder_day3", registration_date + timedelta(days=2)),
            ("reminder_day4", registration_date + timedelta(days=3)),
            ("reminder_day5", registration_date + timedelta(days=4)),
        ]

        scheduler = AsyncIOScheduler(timezone=moscow_tz)
        for reminder_type, schedule_time in reminders:
            job_id = f"reminder_{reminder_type}_{user_id}"
            if scheduler.get_job(job_id):
                logger.debug(f"Задача {job_id} уже запланирована для user_id={user_id}, пропускаем")
                continue
            if schedule_time <= current_time:
                logger.warning(f"Попытка запланировать задачу {job_id} в прошлом: {schedule_time}")
                await send_onboarding_message(bot, user_id, reminder_type, subscription_data)
                continue
            logger.info(f"Планируем задачу {job_id} для user_id={user_id} на {schedule_time}")
            scheduler.add_job(
                send_onboarding_message,
                trigger='date',
                run_date=schedule_time,
                args=[bot, user_id, reminder_type, subscription_data],
                id=job_id,
                misfire_grace_time=300
            )
        scheduler.start()
        logger.info(f"Напоминания запланированы для user_id={user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка планирования напоминаний для user_id={user_id}: {e}", exc_info=True)

async def proceed_to_payment_callback(callback_query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Обрабатывает нажатие кнопки 'Вперёд' для пользователей."""
    user_id = callback_query.from_user.id
    subscription_data = await check_database_user(user_id)
    if not subscription_data or len(subscription_data) < 11:
        await callback_query.message.answer(
            escape_md("❌ Ошибка сервера! Попробуйте позже.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await callback_query.answer()
        return

    payments = await get_user_payments(user_id)
    first_purchase = bool(subscription_data[5]) if len(subscription_data) > 5 else True
    is_paying_user = len(payments) > 0
    logger.debug(f"proceed_to_payment_callback: user_id={user_id}, payments={payments}, payment_count={len(payments) if payments else 0}, first_purchase={first_purchase}, is_paying_user={is_paying_user}")

    if is_paying_user:
        # Для оплативших пользователей показываем все тарифы
        tariff_message_text = get_tariff_text(first_purchase=first_purchase, is_paying_user=True)
        subscription_kb = await create_subscription_keyboard(hide_mini_tariff=False)
        await callback_query.message.answer(
            tariff_message_text,
            reply_markup=subscription_kb,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        # Для неоплативших пользователей отправляем сообщение welcome
        await send_onboarding_message(bot, user_id, "welcome", subscription_data, first_purchase=first_purchase)
    
    await callback_query.answer()

async def proceed_to_tariff_callback(callback_query: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Обрабатывает нажатие кнопки 'Начать' для перехода к тарифам."""
    user_id = callback_query.from_user.id
    subscription_data = await check_database_user(user_id)
    if not subscription_data or len(subscription_data) < 11:
        await callback_query.message.answer(
            escape_md("❌ Ошибка сервера! Попробуйте позже.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await callback_query.answer()
        return

    payments = await get_user_payments(user_id)
    first_purchase = bool(subscription_data[5]) if len(subscription_data) > 5 else True
    is_paying_user = len(payments) > 0
    logger.debug(f"proceed_to_tariff_callback: user_id={user_id}, payments={payments}, payment_count={len(payments) if payments else 0}, first_purchase={first_purchase}, is_paying_user={is_paying_user}")

    if is_paying_user:
        # Для оплативших пользователей показываем все тарифы
        tariff_message_text = get_tariff_text(first_purchase=first_purchase, is_paying_user=True)
        subscription_kb = await create_subscription_keyboard(hide_mini_tariff=False)
        await callback_query.message.answer(
            tariff_message_text,
            reply_markup=subscription_kb,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        # Для неоплативших пользователей вызываем send_onboarding_message с типом proceed_to_tariff
        await send_onboarding_message(bot, user_id, "proceed_to_tariff", subscription_data, first_purchase=first_purchase)
    
    await callback_query.answer()

def setup_onboarding_handlers():
    """Регистрирует обработчики для онбординга."""
    @onboarding_router.callback_query(lambda c: c.data == "proceed_to_payment")
    async def onboarding_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
        logger.debug(f"onboarding_callback_handler: Callback_query получен: id={query.id}, data={query.data}, user_id={query.from_user.id}")
        await proceed_to_payment_callback(query, state, query.bot)
    
    @onboarding_router.callback_query(lambda c: c.data == "proceed_to_tariff")
    async def tariff_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
        logger.debug(f"tariff_callback_handler: Callback_query получен: id={query.id}, data={query.data}, user_id={query.from_user.id}")
        await proceed_to_tariff_callback(query, state, query.bot)