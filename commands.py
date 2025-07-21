import logging
import asyncio
import os
import pytz
import aiosqlite
from aiogram import Bot
from datetime import datetime
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from config import ADMIN_IDS, REPLICATE_USERNAME_OR_ORG_NAME
from database import (
    add_user_without_subscription,
    check_user_resources,
    update_user_balance,
    is_old_user,
    get_user_actions_stats,
    is_user_blocked,
    get_user_trainedmodels,
    get_active_trainedmodel,
    check_database_user,
    get_user_payments,
    update_user_credits
)
from keyboards import create_main_menu_keyboard, create_subscription_keyboard, create_user_profile_keyboard, create_payment_only_keyboard
from generation import reset_generation_context, check_training_status
from handlers.utils import safe_escape_markdown as escape_md, get_tariff_text, send_message_with_fallback
from handlers.onboarding import send_onboarding_message, schedule_tariff_messages, schedule_onboarding_reminders
from bot_counter import bot_counter

logger = logging.getLogger(__name__)

async def debug_avatars(message: Message, state: FSMContext) -> None:
    """Отладочная команда для проверки аватаров (только для админов)."""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer(
            escape_md("❌ У вас нет прав для этой команды.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if await is_user_blocked(user_id):
        await message.answer(
            escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался выполнить действие")
        return

    bot = message.bot
    
    try:
        models = await get_user_trainedmodels(user_id)
        
        if not models:
            await bot.send_message(
                chat_id=user_id,
                text=escape_md("У вас нет аватаров", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        response_text = escape_md("🔍 Детальная информация об аватарах:\n\n", version=2)
        
        for i, model_tuple in enumerate(models):
            response_text += escape_md(f"Аватар #{i+1}:\n", version=2)
            response_text += escape_md(f"  - avatar_id: `{model_tuple[0] if len(model_tuple) > 0 else 'N/A'}`\n", version=2)
            response_text += escape_md(f"  - model_id: `{model_tuple[1] if len(model_tuple) > 1 else 'N/A'}`\n", version=2)
            response_text += escape_md(f"  - model_version: `{model_tuple[2] if len(model_tuple) > 2 else 'N/A'}`\n", version=2)
            response_text += escape_md(f"  - status: `{model_tuple[3] if len(model_tuple) > 3 else 'N/A'}`\n", version=2)
            response_text += escape_md(f"  - prediction_id: `{model_tuple[4] if len(model_tuple) > 4 else 'N/A'}`\n", version=2)
            response_text += escape_md(f"  - trigger_word: `{model_tuple[5] if len(model_tuple) > 5 else 'N/A'}`\n", version=2)
            response_text += escape_md(f"  - training_step: `{model_tuple[7] if len(model_tuple) > 7 else 'N/A'}`\n", version=2)
            response_text += escape_md(f"  - avatar_name: `{model_tuple[8] if len(model_tuple) > 8 else 'N/A'}`\n", version=2)
            
            if len(model_tuple) > 2 and model_tuple[2]:
                version = model_tuple[2]
                response_text += escape_md(f"\n  Возможные LoRA URLs:\n", version=2)
                response_text += escape_md(f"  - yhqm: `https://replicate.delivery/yhqm/{version}/trained_model.tar`\n", version=2)
                response_text += escape_md(f"  - pbxt: `https://replicate.delivery/pbxt/{version}/trained_model.tar`\n", version=2)
                response_text += escape_md(f"  - czjl: `https://replicate.delivery/czjl/{version}/trained_model.tar`\n", version=2)
            
            response_text += escape_md("\n", version=2)
        
        if len(response_text) > 4000:
            parts = [response_text[i:i+4000] for i in range(0, len(response_text), 4000)]
            for part in parts:
                await bot.send_message(
                    chat_id=user_id,
                    text=part,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=response_text,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
    except Exception as e:
        logger.error(f"Ошибка в debug_avatars: {e}", exc_info=True)
        await bot.send_message(
            chat_id=user_id,
            text=escape_md(f"Ошибка: {str(e)}", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def check_user_blocked(message: Message) -> bool:
    """Проверяет, заблокирован ли пользователь, и отправляет сообщение о блоке."""
    user_id = message.from_user.id
    if await is_user_blocked(user_id):
        await message.answer(
            escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался выполнить действие")
        return True
    return False

async def start(message: Message, state: FSMContext) -> None:
    """Обрабатывает команду /start, отправляет приветственное сообщение."""
    user_id = message.from_user.id
    if await check_user_blocked(message):
        return

    username = message.from_user.username or "Без имени"
    first_name = message.from_user.first_name or "N/A"
    bot = message.bot
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    logger.info(f"Пользователь {user_id} ({username}) запустил бота. Args: {args}")
    await reset_generation_context(state, "start_command")
    
    referrer_id = None
    if args and args[0].startswith("ref_"):
        try:
            referrer_id_str = args[0].split("_")[1]
            if referrer_id_str.isdigit():
                referrer_id = int(referrer_id_str)
                if referrer_id == user_id:
                    logger.info(f"User {user_id} tried to use their own referral link.")
                    referrer_id = None
                else:
                    ref_data = await check_database_user(referrer_id)
                    if not ref_data or ref_data[3] is None:
                        logger.warning(f"Referrer ID {referrer_id} not found.")
                        referrer_id = None
                    elif await is_user_blocked(referrer_id):
                        logger.warning(f"Referrer ID {referrer_id} is blocked.")
                        referrer_id = None
                    else:
                        referral_actions = await get_user_actions_stats(action='use_referral')
                        referrer_referrals = [action for action in referral_actions if action['details'].get('referrer_id') == referrer_id]
                        if len(referrer_referrals) >= 100:
                            logger.warning(f"Referrer ID {referrer_id} has reached maximum referrals (100).")
                            referrer_id = None
                        else:
                            logger.info(f"User {user_id} came from referral link of {referrer_id}")
        except (IndexError, ValueError):
            logger.warning(f"Invalid referral link format: {args[0]}")
            referrer_id = None

    await add_user_without_subscription(user_id, username, first_name, referrer_id=referrer_id)

    try:
        subscription_data = await check_database_user(user_id)
        if not subscription_data or len(subscription_data) < 11:
            logger.error(f"Неполные данные подписки для user_id={user_id}: {subscription_data}")
            await message.answer(
                escape_md("❌ Ошибка сервера! Попробуйте /start позже.", version= 2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        is_notified = subscription_data[4]
        first_purchase = bool(subscription_data[5])
        last_reminder_type = subscription_data[9]
        created_at = subscription_data[10]
    except Exception as e:
        logger.error(f"Ошибка проверки подписки для user_id={user_id}: {e}", exc_info=True)
        await message.answer(
            escape_md("❌ Ошибка сервера! Попробуйте /start позже.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if not is_notified and user_id not in ADMIN_IDS:
        for admin_id_notify in ADMIN_IDS:
            try:
                display_name = f"@{username}" if username != "Без имени" else f"{first_name} (ID {user_id})"
                admin_text = (
                    escape_md(f"✨ Новая Печенька🍪: {display_name}", version=2) +
                    (escape_md(f" (приглашен ID {referrer_id})", version=2) if referrer_id else "")
                )
                await bot.send_message(
                    chat_id=admin_id_notify,
                    text=admin_text,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e_admin:
                logger.error(f"Не удалось уведомить админа {admin_id_notify}: {e_admin}")
        await update_user_credits(user_id, "set_notified", amount=1)

    # Проверяем, является ли пользователь старым
    is_old_user_flag = await is_old_user(user_id, cutoff_date="2025-07-11")
    logger.debug(f"Пользователь user_id={user_id} is_old_user={is_old_user_flag}")

    # Рассчитываем время и дни с момента регистрации
    moscow_tz = pytz.timezone('Europe/Moscow')
    registration_date = datetime.now(moscow_tz)
    time_since_registration = float('inf')
    days_since_registration = 0
    if created_at:
        try:
            registration_date = moscow_tz.localize(datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S'))
            time_since_registration = (datetime.now(moscow_tz) - registration_date).total_seconds()
            days_since_registration = (datetime.now(moscow_tz).date() - registration_date.date()).days
            logger.debug(f"Calculated time_since_registration={time_since_registration} for user_id={user_id}")
        except ValueError as e:
            logger.warning(f"Невалидная дата регистрации для user_id={user_id}: {created_at}. Ошибка: {e}")

    # Формируем приветственное сообщение
    welcome_text = (
        escape_md("Добро пожаловать в PixelPie! 🍪", version=2) + "\n\n" +
        escape_md("Привет, дружок 🍪 я PixelPie - твой Пиксельный пирожок.", version=2) + "\n\n" +
        escape_md("Сначала я обучаюсь на твоих фото, а потом создаю шедевры! Для старта выбери подходящий для тебя пакет печенек, затем создай свой аватар. Как? Очень просто!", version=2) + "\n\n" +
        escape_md("Первое — отправь мне свои фотки. Да-да, не стесняйся, я все равно их не сохраню, в отличие от твоих бывших 😃 Далее просто начни обучение, дай своему аватару имя (пожалуйста, не зови его \"Пирожок\", это моё!) и вуаля — твой аватар готов генерировать фото!", version=2) + "\n\n" +
        escape_md("Фотки могут быть обычными, но если у тебя есть студийные, то я обещаю, результат будет огненным! 🔥", version=2) + "\n\n" +
        escape_md("Так что, мой друг, самое время перейти к созданию шедевров! 🔥", version=2)
    )
    if referrer_id:
        welcome_text += (
            "\n\n" +
            escape_md(f"🎉 Поздравляю с регистрацией по приглашению от пользователя ID {referrer_id}!", version=2) + "\n" +
            escape_md("🎁 После твоей первой покупки ты получишь +1 печеньку в подарок, а твой друг — 10% от количества печенек в твоём тарифе!", version=2)
        )

    # Проверяем статус оплаты
    payments = await get_user_payments(user_id)
    is_paying_user = bool(payments) or not first_purchase
    logger.debug(f"start: user_id={user_id}, payments={payments}, payment_count={len(payments) if payments else 0}, first_purchase={first_purchase}, is_paying_user={is_paying_user}")

        # Логика для старых неоплативших пользователей
    if is_old_user_flag and not is_paying_user:
        # Отправляем одно общее сообщение для старых пользователей
        welcome_text += (
            "\n\n" +
            escape_md("🍪 Кажется, ты давно с нами, но ещё не выбрал тариф! 🚀", version=2) + "\n" +
            escape_md("Попробуй PixelPie и создавай крутые фото:\n", version=2) +
            escape_md("✔️ Тариф 'Мини' за 399₽ — 10 печенек\n", version=2) +
            escape_md("✔️ Тариф 'Лайт' за 599₽ — 30 печенек\n", version=2) +
            escape_md("✔️ Тариф 'Комфорт' за 1199₽ — 70 печенек\n", version=2) +
            escape_md("✔️ Или выбери только аватар за 590₽\n", version=2) +
            escape_md("📸 Получи доступ к созданию аватаров и генерации фото в любом стиле!", version=2)
        )
        reply_markup = await create_payment_only_keyboard(user_id, time_since_registration, days_since_registration, last_reminder_type, is_old_user=True)
    else:
        # Для новых пользователей или оплативших сохраняем стандартную клавиатуру
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Вперёд 🚀", callback_data="proceed_to_payment")]
            ]
        )

    # Отправляем приветственное сообщение
    video_path = "images/welcome.mp4"
    try:
        if os.path.exists(video_path):
            video_file = FSInputFile(path=video_path)
            video_message = await bot.send_video(
                chat_id=user_id,
                video=video_file,
                caption=welcome_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"Видео приветствия отправлено для user_id={user_id}, message_id={video_message.message_id}")
        else:
            logger.warning(f"Видео не найдено по пути: {video_path} для user_id={user_id}")
            await message.answer(
                welcome_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Не удалось отправить видео для user_id={user_id}: {e}", exc_info=True)
        await message.answer(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )

    # Планируем онбординговые напоминания только для новых неоплативших пользователей
    if not is_paying_user and not is_old_user_flag:
        await schedule_tariff_messages(bot, user_id)
        await schedule_onboarding_reminders(bot, user_id)
        logger.info(f"Запланированы онбординговые сообщения для нового пользователя user_id={user_id}")
    else:
        logger.info(f"Онбординговые сообщения НЕ запланированы для user_id={user_id} (is_paying_user={is_paying_user}, is_old_user={is_old_user_flag})")
         
async def menu(message: Message, state: FSMContext) -> None:
    """Показывает главное меню с учетом статуса оплаты пользователя."""
    user_id = message.from_user.id
    if await check_user_blocked(message):
        return

    bot = message.bot
    logger.info(f"Пользователь {user_id} вызвал /menu.")
    
    # Очистка предыдущих видео
    user_data = await state.get_data()
    if 'menu_video_message_id' in user_data:
        try:
            await bot.delete_message(chat_id=user_id, message_id=user_data['menu_video_message_id'])
            await state.update_data(menu_video_message_id=None)
        except Exception as e:
            logger.debug(f"Не удалось удалить предыдущее видео меню: {e}")
    
    if 'generation_video_message_id' in user_data:
        try:
            await bot.delete_message(chat_id=user_id, message_id=user_data['generation_video_message_id'])
            await state.update_data(generation_video_message_id=None)
        except Exception as e:
            logger.debug(f"Не удалось удалить видео генерации: {e}")
    
    await reset_generation_context(state, "menu_command")
    
    # Проверка данных подписки
    try:
        subscription_data = await check_database_user(user_id)
        if not subscription_data or len(subscription_data) < 11:
            logger.error(f"Неполные данные подписки для user_id={user_id}: {subscription_data}")
            error_text = escape_md("❌ Ошибка сервера! Попробуйте /menu позже.", version=2)
            main_menu_kb = await create_main_menu_keyboard(user_id)
            await message.answer(
                error_text,
                reply_markup=main_menu_kb,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        generations_left, avatar_left, has_trained_model, username_db, is_notified, first_purchase_db_val, email, active_avatar_id, first_name_db, last_reminder_type, created_at = subscription_data
        first_purchase = bool(first_purchase_db_val)
    except Exception as e:
        logger.error(f"Ошибка проверки подписки для user_id={user_id}: {e}", exc_info=True)
        error_text = escape_md("❌ Ошибка сервера! Попробуйте /menu позже.", version=2)
        main_menu_kb = await create_main_menu_keyboard(user_id)
        await message.answer(
            error_text,
            reply_markup=main_menu_kb,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Проверка статуса оплаты
    payments = await get_user_payments(user_id)
    is_paying_user = bool(payments) or not first_purchase
    logger.debug(f"menu: user_id={user_id}, payments={payments}, payment_count={len(payments) if payments else 0}, first_purchase={first_purchase}, is_paying_user={is_paying_user}")

    # Рассчитываем время и дни с момента регистрации
    moscow_tz = pytz.timezone('Europe/Moscow')
    registration_date = datetime.now(moscow_tz)
    time_since_registration = float('inf')
    days_since_registration = 0
    if created_at:
        try:
            registration_date = moscow_tz.localize(datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S'))
            time_since_registration = (datetime.now(moscow_tz) - registration_date).total_seconds()
            days_since_registration = (datetime.now(moscow_tz).date() - registration_date.date()).days
            logger.debug(f"Calculated time_since_registration={time_since_registration} for user_id={user_id}")
        except ValueError as e:
            logger.warning(f"Невалидная дата регистрации для user_id={user_id}: {created_at}. Ошибка: {e}")

    # Формируем текст меню с счётчиком
    try:
        total = await bot_counter.get_total_count()
        formatted = bot_counter.format_number(total)
        menu_text = (
            f"🎨 PixelPie | 👥 {formatted} Пользователей\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌈 Добро пожаловать в главное меню!\n"
            f"Что вы хотите создать сегодня? 😊\n"
            f"📸 Сгенерировать — Создайте Ваши фото или Видео\n"
            f"🎭 Мои аватары — создайте новый аватар или выберите активный\n"
            f"💎 Купить пакет — пополните баланс для новых генераций\n"
            f"👤 Личный кабинет — Ваш баланс, Статистика и История\n"
            f"💬 Поддержка — мы всегда готовы помочь! 24/7\n"
            f"Выберите нужный пункт с помощью кнопок ниже 👇"
        )
        menu_text = escape_md(menu_text, version=2)
    except Exception as e:
        logger.error(f"Ошибка получения текста меню для user_id={user_id}: {e}", exc_info=True)
        menu_text = escape_md("❌ Ошибка загрузки меню. Попробуйте позже.", version=2)
        main_menu_kb = await create_main_menu_keyboard(user_id)
        await message.answer(
            menu_text,
            reply_markup=main_menu_kb,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Формируем текст тарифов в зависимости от дня
    tariff_message_text = ""
    if not is_paying_user:
        if days_since_registration == 0:
            logger.debug(f"Day 0 tariff check: time_since_registration={time_since_registration}")
            if time_since_registration <= 1800:  # До 30 минут
                tariff_message_text = escape_md(
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
                )
            elif time_since_registration <= 5400:  # 30–90 минут
                tariff_message_text = escape_md(
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
                )
            else:  # После 90 минут
                tariff_message_text = escape_md(
                    "🧪 Тариф 'Мини' за 399₽\n"
                    "🍪 Тестовый пакет — без обязательств и больших вложений:\n\n"
                    "✅ 10 фото\n"
                    "✅ 1 аватар в подарок при первой покупке\n"
                    "✅ Генерация по твоему описанию\n"
                    "✅ Доступ к идеям из @pixelpie_idea\n"
                    "💳 Всего 399₽ — чтобы понять, насколько тебе заходит PixelPie!\n"
                    "😱 Такое предложение больше не появится!",
                    version=2
                )
        elif days_since_registration == 1:
            tariff_message_text = escape_md(
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
            )
        elif 2 <= days_since_registration <= 4:
            tariff_message_text = escape_md(
                "🧪 Тариф 'Мини' за 399₽\n"
                "🍪 Тестовый пакет — без обязательств и больших вложений:\n\n"
                "✅ 10 фото\n"
                "✅ 1 аватар в подарок при первой покупке\n"
                "✅ Генерация по твоему описанию\n"
                "✅ Доступ к идеям из @pixelpie_idea\n"
                "💳 Всего 399₽ — чтобы понять, насколько тебе заходит PixelPie!\n"
                "😱 Такое предложение больше не появится!",
                version=2
            )
        elif days_since_registration >= 5 and last_reminder_type == "reminder_day5":
            tariff_message_text = escape_md(
                "🍪 Последняя печенька, мой друг! 🍪\n"
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
            )

    # Логика отображения меню
    if generations_left > 0 or avatar_left > 0 or user_id in ADMIN_IDS:
        # Для оплативших пользователей или админов: полное меню с видео
        menu_video_path = "images/welcome1.mp4"
        main_menu_keyboard = await create_main_menu_keyboard(user_id)
        try:
            if os.path.exists(menu_video_path):
                video_file = FSInputFile(path=menu_video_path)
                video_message = await bot.send_video(
                    chat_id=user_id,
                    video=video_file,
                    caption=menu_text,
                    reply_markup=main_menu_keyboard,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.update_data(menu_video_message_id=video_message.message_id)
                logger.debug(f"Видео меню отправлено для user_id={user_id}, message_id={video_message.message_id}")
            else:
                logger.warning(f"Видео меню не найдено по пути: {menu_video_path}")
                await message.answer(
                    menu_text,
                    reply_markup=main_menu_keyboard,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        except Exception as e:
            logger.error(f"Не удалось отправить видео меню для user_id={user_id}: {e}", exc_info=True)
            await message.answer(
                menu_text,
                reply_markup=main_menu_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
    else:
        # Для неоплативших: текст меню + тарифы + кнопки оплаты
        payment_only_kb = await create_payment_only_keyboard(user_id, time_since_registration, days_since_registration, last_reminder_type)
        full_message = f"{menu_text}\n\n{tariff_message_text}"
        try:
            await message.answer(
                full_message,
                reply_markup=payment_only_kb,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"Меню с кнопками оплаты отправлено для user_id={user_id}: {full_message[:200]}...")
        except Exception as e:
            logger.error(f"Ошибка отправки меню для неоплатившего user_id={user_id}: {e}", exc_info=True)
            await message.answer(
                escape_md("❌ Ошибка сервера! Попробуйте /menu позже.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )

async def help_command(message: Message, state: FSMContext) -> None:
    """Показывает справку."""
    user_id = message.from_user.id
    if await check_user_blocked(message):
        return

    bot = message.bot
    logger.info(f"Пользователь {user_id} вызвал /help.")
    await reset_generation_context(state, "help_command")
    
    help_text = (
        escape_md("ℹ️ PixelPie — твой помощник для создания фото и видео! 😊", version=2) + "\n\n" +
        escape_md("1️⃣ Используй /menu для доступа к Главному меню бота.", version=2) + "\n\n" +
        escape_md("2️⃣ В разделе Сгенерировать ты можешь:", version=2) + "\n" +
        escape_md("   - ✨ Фотосессия (с аватаром): Создавай фото со своим личным аватаром в различных стилях. Сначала создай или выбери активный аватар!", version=2) + "\n" +
        escape_md("   - 👥 Фото по референсу: Загрузи свое фото и маску (по желанию), чтобы изменить его часть по текстовому описанию (промту). Требуется активный аватар.", version=2) + "\n" +
        escape_md("   - 🎥 AI-видео (Kling 2.1): Преврати статичное фото в короткое динамичное видео. Требуется от 20-30 печенек с баланса.", version=2) + "\n\n" +
        escape_md("3️⃣ Мои аватары:", version=2) + "\n" +
        escape_md("   - Просмотр статуса обучения твоих аватаров.", version=2) + "\n" +
        escape_md("   - Активация готового аватара для генерации.", version=2) + "\n" +
        escape_md("   - Создание нового аватара (требуется пакет с аватарами или покупка отдельно).", version=2) + "\n\n" +
        escape_md("4️⃣ Купить пакет:", version=2) + "\n" +
        escape_md("   - Здесь ты можешь пополнить свой баланс печеньками и доступных аватаров.", version=2) + "\n\n" +
        escape_md("5️⃣ Личный кабинет:", version=2) + "\n" +
        escape_md("   - Проверка текущего баланса печенек и аватаров.", version=2) + "\n" +
        escape_md("   - Просмотр статистики использования.", version=2) + "\n" +
        escape_md("   - Доступ к созданию нового аватара и просмотру существующих.", version=2) + "\n\n" +
        escape_md("6️⃣ Поддержка:", version=2) + "\n" +
        escape_md("   - Если возникли вопросы, проблемы или есть предложения, смело пиши нам: @AXIDI_Help", version=2) + "\n\n" +
        escape_md("✨ Твори и создавай шедевры вместе с PixelPie! ✨", version=2)
    )
    
    main_menu_kb = await create_main_menu_keyboard(user_id)
    await message.answer(
        help_text,
        reply_markup=main_menu_kb,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Справка отправлена для user_id={user_id}: {help_text}")

async def check_training(message: Message, state: FSMContext, user_id: int) -> None:
    """
    Проверяет статус обучения аватаров пользователя.
    Отображает текущие модели и их статус, используя данные из базы данных.
    Учитывает админский контекст, если пользователь действует от имени другого пользователя.
    """
    logger.info(f"Проверка статуса обучения для user_id={user_id} (входящий параметр)")
    
    # Проверяем админский контекст
    user_data = await state.get_data()
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    effective_user_id = target_user_id if is_admin_generation else user_id
    logger.debug(f"check_training: user_id={user_id}, is_admin_generation={is_admin_generation}, target_user_id={target_user_id}, effective_user_id={effective_user_id}")

    if await is_user_blocked(effective_user_id):
        logger.info(f"Заблокированный пользователь user_id={effective_user_id} пытался проверить статус обучения")
        text = escape_md(
            f"🚫 Аккаунт пользователя ID {effective_user_id} заблокирован. Обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        await send_message_with_fallback(
            message.bot, user_id, text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    bot = message.bot
    await reset_generation_context(state, "check_training_logic", user_id=user_id)

    try:
        # Проверяем данные пользователя
        subscription_data = await check_database_user(effective_user_id)
        if not subscription_data or len(subscription_data) < 11:
            logger.error(f"Неполные данные подписки для user_id={effective_user_id}: {subscription_data}")
            text = escape_md(
                f"❌ Ошибка получения данных пользователя ID {effective_user_id}. Попробуйте позже или обратитесь в поддержку: @AXIDI_Help",
                version=2
            )
            await send_message_with_fallback(
                message.bot, user_id, text,
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.update_data(user_id=user_id)
            return

        # Распаковываем данные подписки
        generations_left, avatar_left, has_trained_model, username, is_notified, first_purchase, email, active_avatar_id, first_name, is_blocked, created_at = subscription_data
        logger.debug(f"Данные подписки для user_id={effective_user_id}: generations_left={generations_left}, avatar_left={avatar_left}, has_trained_model={has_trained_model}")

        trained_models = await get_user_trainedmodels(effective_user_id)
        if not trained_models:
            text = (
                escape_md(f"🚫 У пользователя ID {effective_user_id} пока нет аватаров в обучении или готовых. Хочешь создать новый? 🛠", version=2) + "\n" +
                escape_md("Нажми /menu и выбери 'Личный кабинет' - 'Создать аватар'!", version=2)
            )
            reply_markup = await create_user_profile_keyboard(user_id, bot)
            await send_message_with_fallback(
                message.bot, user_id, text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"Сообщение об отсутствии аватаров отправлено для user_id={effective_user_id}")
            await state.update_data(user_id=user_id)
            return

        response_text = escape_md(f"📋 Статус аватаров пользователя ID {effective_user_id}:", version=2) + "\n\n"
        active_model_data = await get_active_trainedmodel(effective_user_id)
        active_avatar_id_current = active_model_data[0] if active_model_data else None
        needs_manual_check_info = False

        for model_tuple in trained_models:
            if len(model_tuple) >= 9:
                avatar_id, model_id_db_check, _, status, prediction_id, trigger_word_db_check, _, _, avatar_name = model_tuple[:9]
                name_display = avatar_name if avatar_name else f"Аватар {avatar_id}"
                status_icon = "⏳"

                if status in ['pending', 'starting', 'processing']:
                    model_name_for_check = model_id_db_check
                    if not model_name_for_check and trigger_word_db_check:
                        logger.warning(f"model_id не найден для avatar_id={avatar_id}. Попытка восстановить.")
                        if REPLICATE_USERNAME_OR_ORG_NAME:
                            model_slug_reconstructed = f"flux-{effective_user_id}-{trigger_word_db_check.lower().replace('_', '-')}"
                            model_name_for_check = f"{REPLICATE_USERNAME_OR_ORG_NAME}/{model_slug_reconstructed}"
                        else:
                            logger.error("REPLICATE_USERNAME_OR_ORG_NAME не установлен.")
                    
                    if prediction_id and model_name_for_check:
                        asyncio.create_task(check_training_status({
                            'bot': bot,
                            'user_id': effective_user_id,
                            'prediction_id': prediction_id,
                            'avatar_id': avatar_id,
                            'model_name': model_name_for_check
                        }))
                        status_display = f"_{status}, обновляю статус..._"
                        needs_manual_check_info = True
                    else:
                        status_display = f"_{status} (ошибка данных для авто-проверки)_"
                elif status == 'success':
                    status_icon = "✅"
                    status_display = "Готов"
                elif status == 'failed_no_version':
                    status_icon = "⚠️"
                    status_display = "Ошибка (версия не найдена)"
                else:
                    status_icon = "❌"
                    status_display = f"Ошибка ({status})"

                response_text += escape_md(
                    f"👤 {name_display}"
                    f"{ ' (Активный ✨)' if avatar_id == active_avatar_id_current and status == 'success' else ''}"
                    f": {status_icon} {status_display}",
                    version=2
                ) + "\n"

        if needs_manual_check_info:
            response_text += escape_md(
                "\n_Статус моделей в процессе обучения обновляется автоматически. Пожалуйста, подожди немного и проверь снова._",
                version=2
            ) + "\n"
        
        response_text += escape_md("\nВернуться в меню можно через /menu или кнопки ниже.", version=2)

        reply_markup_check = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад в Личный Кабинет", callback_data="user_profile")]
        ])
        await send_message_with_fallback(
            message.bot, user_id, response_text,
            reply_markup=reply_markup_check,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Статус обучения отправлен для user_id={effective_user_id}: {response_text[:200]}...")
        
    except Exception as e:
        logger.error(f"Ошибка в check_training для user_id={effective_user_id}: {e}", exc_info=True)
        error_text = escape_md(
            f"❌ Ошибка при проверке статуса обучения для пользователя ID {effective_user_id}. Попробуйте позже или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        main_menu_kb = await create_main_menu_keyboard(user_id)
        await send_message_with_fallback(
            message.bot, user_id, error_text,
            reply_markup=main_menu_kb,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)