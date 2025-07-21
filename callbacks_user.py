import re
import asyncio
import aiosqlite
import logging
import os
import time
import pytz
from aiogram.utils.markdown import text, bold
from typing import Optional
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.filters import Command
from datetime import datetime
from states import BotStates, VideoStates
from config import ADMIN_IDS, DATABASE_PATH, TARIFFS
from generation_config import IMAGE_GENERATION_MODELS, ASPECT_RATIOS, GENERATION_STYLES, NEW_MALE_AVATAR_STYLES, NEW_FEMALE_AVATAR_STYLES, style_prompts, new_male_avatar_prompts, new_female_avatar_prompts
from database import (
    check_database_user, update_user_balance, add_rating, get_user_trainedmodels,
    get_active_trainedmodel, delete_trained_model, get_user_video_tasks,
    get_user_rating_and_registration, get_user_generation_stats, get_user_payments,
    is_user_blocked, user_cache, update_user_credits, check_user_resources, is_old_user
)
from keyboards import (
    create_main_menu_keyboard, create_photo_generate_menu_keyboard,
    create_video_generate_menu_keyboard, create_video_styles_keyboard,
    create_aspect_ratio_keyboard, create_back_keyboard, create_avatar_style_choice_keyboard,
    create_subscription_keyboard, create_user_profile_keyboard, create_prompt_selection_keyboard, create_referral_keyboard,
    create_rating_keyboard, create_new_male_avatar_styles_keyboard, create_new_female_avatar_styles_keyboard, create_avatar_selection_keyboard, create_payment_only_keyboard
)
from generation.training import TrainingStates
from generation.videos import handle_generate_video_callback
from generation import reset_generation_context, generate_image, start_training, check_training_status
from handlers.utils import (
    safe_escape_markdown as escape_md, safe_answer_callback,
    check_resources, check_active_avatar, check_style_config, create_payment_link,
    get_tariff_text, send_typing_action, clean_admin_context, escape_message_parts, safe_escape_markdown
)
from handlers.onboarding import send_onboarding_message

logger = logging.getLogger(__name__)

# Создание роутера для пользовательских callback'ов
user_callbacks_router = Router()

async def handle_user_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обработчик пользовательских callback-запросов."""
    user_id = query.from_user.id
    callback_data = query.data
    logger.info(f"handle_user_callback: user_id={user_id}, callback_data={callback_data}")

    # Проверка блокировки
    if await is_user_blocked(user_id):
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался выполнить callback: {callback_data}")
        await query.answer("🚫 Ваш аккаунт заблокирован.", show_alert=True)
        await query.message.answer(
            escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Проверка и сброс состояния FSM
    user_data = await state.get_data()
    if any(key in user_data for key in [
        'awaiting_broadcast_message', 'awaiting_broadcast_schedule',
        'awaiting_balance_change', 'awaiting_block_reason', 'awaiting_user_search'
    ]):
        logger.warning(f"User {user_id} in FSM state, clearing FSM data: {user_data}")
        await state.clear()

    # Проверка админского контекста
    if user_data.get('admin_generation_for_user') or user_data.get('admin_target_user_id'):
        admin_callbacks = [
            'select_new_male_avatar_styles', 'select_new_female_avatar_styles',
            'confirm_generation', 'back_to_style_selection', 'back_to_aspect_selection',
            'enter_custom_prompt_manual', 'enter_custom_prompt_llama',
            'confirm_assisted_prompt', 'edit_assisted_prompt', 'video_style_'
        ]
        if (callback_data in admin_callbacks or
            callback_data.startswith(('style_', 'male_styles_page_', 'female_styles_page_', 'aspect_', 'video_style_'))):
            logger.info(f"User {user_id} in admin generation state, preserving context for callback: {callback_data}")
        else:
            logger.warning(f"User {user_id} in admin generation state, clearing admin context")
            await clean_admin_context(state)

    try:
        if callback_data == "proceed_to_payment":
            await handle_proceed_to_payment_callback(query, state, user_id)
        elif callback_data == "photo_generate_menu":
            await handle_photo_generate_menu_callback(query, state, user_id)
        elif callback_data == "photo_transform":
            from handlers.photo_transform import start_photo_transform
            await start_photo_transform(query, state)
        elif callback_data == "video_generate_menu":
            await handle_video_generate_menu_callback(query, state, user_id)
        elif callback_data == "generate_menu":  # Добавляем обработку
            await handle_photo_generate_menu_callback(query, state, user_id)    
        elif callback_data == "photo_to_photo":
            await handle_photo_to_photo_callback(query, state, user_id)
        elif callback_data == "ai_video_v2_1":
            await handle_ai_video_callback(query, state, user_id)
        elif callback_data == "repeat_last_generation":
            await handle_repeat_last_generation_callback(query, state, user_id)
        elif callback_data == "generate_with_avatar":
            await handle_style_selection_callback(query, state)
        elif callback_data == "select_new_male_avatar_styles":
            await handle_style_selection_callback(query, state)
        elif callback_data == "select_new_female_avatar_styles":
            await handle_style_selection_callback(query, state)
        elif callback_data.startswith("style_"):
            await handle_style_choice_callback(query, state)
        elif callback_data.startswith("video_style_"):
            await handle_video_style_choice_callback(query, state)
        elif callback_data.startswith("male_styles_page_"):
            await handle_male_styles_page_callback(query, state)
        elif callback_data.startswith("female_styles_page_"):
            await handle_female_styles_page_callback(query, state)
        elif callback_data == "page_info":
            await query.answer("ℹ️ Это текущая страница стилей.", show_alert=True)
        elif callback_data == "enter_custom_prompt_manual":
            await handle_custom_prompt_manual_callback(query, state)
        elif callback_data == "confirm_video_generation":
            await handle_confirm_video_generation_callback(query, state, user_id)    
        elif callback_data == "enter_custom_prompt_llama":
            await handle_custom_prompt_llama_callback(query, state,)
        elif callback_data == "confirm_assisted_prompt":
            await handle_confirm_assisted_prompt_callback(query, state,)
        elif callback_data == "edit_assisted_prompt":
            await handle_edit_assisted_prompt_callback(query, state, user_id)
        elif callback_data == "skip_prompt":
            await handle_skip_prompt_callback(query, state)
        elif callback_data.startswith("aspect_"):
            await handle_aspect_ratio_callback(query, state)
        elif callback_data == "aspect_ratio_info":
            from handlers.callbacks_utils import handle_aspect_ratio_info_callback
            await handle_aspect_ratio_info_callback(query, state, user_id)
        elif callback_data == "back_to_aspect_selection":
            await handle_back_to_aspect_selection_callback(query, state)
        elif callback_data == "back_to_style_selection":
            await handle_back_to_style_selection_callback(query, state, user_id)
        elif callback_data == "confirm_generation":
            await handle_confirm_generation_callback(query, state, user_id)
        elif callback_data == "confirm_photo_quality":
            await handle_confirm_photo_quality_callback(query, state, user_id)
        elif callback_data == "skip_mask":
            await handle_skip_mask_callback(query, state, user_id)
        elif callback_data.startswith("rate_"):
            await handle_rating_callback(query, state)
        elif callback_data == "user_profile":
            await handle_user_profile_callback(query, state, user_id)
        elif callback_data == "check_subscription":
            await handle_check_subscription_callback(query, state, user_id)
        elif callback_data == "user_stats":
            await handle_user_stats_callback(query, state, user_id)
        elif callback_data == "subscribe":
            await handle_subscribe_callback(query, state, user_id)
        elif callback_data.startswith("pay_"):
            await handle_payment_callback(query, state, user_id, callback_data)
        elif callback_data == "change_email":
            await handle_change_email_callback(query, state, user_id)
        elif callback_data == "confirm_change_email":
            await handle_confirm_change_email_callback(query, state, user_id)
        elif callback_data == "my_avatars":
            await handle_my_avatars_callback(query, state, user_id)
        elif callback_data.startswith("select_avatar_"):
            await handle_select_avatar_callback(query, state, user_id, callback_data)
        elif callback_data == "train_flux":
            await handle_train_flux_callback(query, state, user_id)
        elif callback_data == "continue_upload":
            await handle_continue_upload_callback(query, state, user_id)
        elif callback_data == "start_training":
            await handle_start_training_callback(query, state, user_id)
        elif callback_data == "confirm_start_training":
            await handle_confirm_start_training_callback(query, state, user_id)
        elif callback_data == "back_to_avatar_name_input":
            await handle_back_to_avatar_name_input_callback(query, state, user_id)
        elif callback_data.startswith("use_suggested_trigger_"):
            await handle_use_suggested_trigger_callback(query, state, user_id, callback_data)
        elif callback_data == "check_training":
            user_data = await state.get_data()
            target_user_id = user_data.get('admin_generation_for_user', user_id)
            from handlers.commands import check_training
            await check_training(query.message, state, target_user_id)
        elif callback_data == "tariff_info":
            await handle_tariff_info_callback(query, state, user_id)
        elif callback_data == "back_to_menu":
            await handle_back_to_menu_callback(query, state, user_id)
        else:
            logger.warning(f"Неизвестный callback_data: {callback_data} для user_id={user_id}")
            await query.answer("⚠️ Неизвестное действие", show_alert=True)
            await query.message.answer(
                escape_md("⚠️ Это действие не поддерживается. Используй /menu.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Ошибка в обработчике callback для user_id={user_id}, data={callback_data}: {e}", exc_info=True)
        await state.clear()
        await safe_answer_callback(query, "❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_tariff_info_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработка нажатия на кнопку 'Информация о тарифах'."""
    logger.debug(f"handle_tariff_info_callback вызван для user_id={user_id}")
    subscription_data = await check_database_user(user_id)
    first_purchase = bool(subscription_data[5]) if subscription_data and len(subscription_data) > 5 else True
    payments = await get_user_payments(user_id)
    is_paying_user = bool(payments) or not first_purchase

    # Проверка времени регистрации для неоплативших пользователей
    moscow_tz = pytz.timezone('Europe/Moscow')
    registration_date = datetime.now(moscow_tz)
    time_since_registration = float('inf')
    days_since_registration = 0
    last_reminder_type = subscription_data[9] if subscription_data and len(subscription_data) > 9 else None
    if subscription_data and len(subscription_data) > 10 and subscription_data[10]:
        try:
            registration_date = moscow_tz.localize(datetime.strptime(subscription_data[10], '%Y-%m-%d %H:%M:%S'))
            time_since_registration = (datetime.now(moscow_tz) - registration_date).total_seconds()
            days_since_registration = (datetime.now(moscow_tz).date() - registration_date.date()).days
            logger.debug(f"Calculated time_since_registration={time_since_registration}, days_since_registration={days_since_registration} for user_id={user_id}")
        except ValueError as e:
            logger.warning(f"Невалидная дата регистрации для user_id={user_id}: {subscription_data[10]}. Используется текущая дата. Ошибка: {e}")

    # Проверяем, является ли пользователь старым
    is_old_user_flag = await is_old_user(user_id, cutoff_date="2025-07-11")
    logger.debug(f"Пользователь user_id={user_id} is_old_user={is_old_user_flag}")

    try:
        text_parts = [
            "🔥 Горячий выбор для идеальных фото!\n\n",
            "Хочешь крутые кадры без лишних хлопот? Выбери свой пакет и начинай творить! 🚀\n\n",
        ]
        keyboard = []
        available_tariffs = {k: v for k, v in TARIFFS.items() if k != "admin_premium"}

        if is_paying_user or is_old_user_flag or (days_since_registration >= 5 and last_reminder_type == "reminder_day5"):
            # Для оплативших пользователей или после 5 дней показываем полную информацию о тарифах
            tariff_text = get_tariff_text(first_purchase=first_purchase, is_paying_user=True, time_since_registration=time_since_registration)
            for tariff_key, tariff in available_tariffs.items():
                keyboard.append([InlineKeyboardButton(text=tariff["display"], callback_data=tariff["callback"])])
            logger.debug(f"Показаны все тарифы для user_id={user_id} (is_paying_user={is_paying_user}, is_old_user={is_old_user_flag}, days_since_registration={days_since_registration}, last_reminder_type={last_reminder_type})")
            await query.message.edit_text(
                text=tariff_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Полная информация о тарифах отправлена для user_id={user_id} (is_paying_user={is_paying_user})")
        else:
            # Для неоплативших пользователей перенаправляем к текущему тарифу
            if days_since_registration == 0:
                if time_since_registration <= 1800:  # До 30 минут
                    tariff_key = "комфорт"
                    text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                    text_parts.append("Комфорт: 70 печенек + 1 аватар за 1199₽\n")
                elif time_since_registration <= 5400:  # 30–90 минут
                    tariff_key = "лайт"
                    text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                    text_parts.append("Лайт: 30 печенек за 599₽\n")
                else:  # После 90 минут
                    tariff_key = "мини"
                    text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                    text_parts.append("Мини: 10 печенек за 399₽\n")
            elif days_since_registration == 1:
                tariff_key = "лайт"
                text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                text_parts.append("Лайт: 30 печенек за 599₽\n")
            elif 2 <= days_since_registration <= 4:
                tariff_key = "мини"
                text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                text_parts.append("Мини: 10 печенек за 399₽\n")

            if first_purchase:
                text_parts.append("\n")
                text_parts.append("🎁 При первой покупке к любому купленному тарифу впервые 1 Аватар в подарок!\n")

            text_parts.append("\n")
            text_parts.append("Выбери свой пакет и начинай творить 🚀")

            # Формируем клавиатуру
            keyboard.append([InlineKeyboardButton(text=available_tariffs[tariff_key]["display"], callback_data=available_tariffs[tariff_key]["callback"])])
            keyboard.append([InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")])

            # Разбиваем текст на части
            MAX_MESSAGE_LENGTH = 4000
            messages = []
            current_message = []
            current_length = 0

            for part in text_parts:
                part_length = len(part) + 1
                if current_length + part_length < MAX_MESSAGE_LENGTH:
                    current_message.append(part)
                    current_length += part_length
                else:
                    messages.append(''.join(current_message))
                    current_message = [part]
                    current_length = part_length
            if current_message:
                messages.append(''.join(current_message))

            # Отправляем сообщения
            for i, message_text in enumerate(messages):
                text = escape_message_parts(message_text, version=2)
                reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if i == len(messages) - 1 else None
                await query.message.answer(
                    text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                logger.debug(f"handle_tariff_info_callback: отправка части {i+1}/{len(messages)}, длина={len(text)}")
            logger.info(f"Текущий тариф отправлен для неоплатившего user_id={user_id}: {tariff_key}")

    except Exception as e:
        logger.error(f"Ошибка отправки информации о тарифах для user_id={user_id}: {e}", exc_info=True)
        await query.message.answer(
            escape_md("❌ Ошибка при загрузке информации о тарифах. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help"),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

async def handle_back_to_menu_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработка нажатия на кнопку 'Назад в главное меню'."""
    logger.debug(f"handle_back_to_menu_callback вызван для user_id={user_id}")
    await state.clear()
    subscription_data = await check_database_user(user_id)
    if not subscription_data or len(subscription_data) < 11:
        logger.error(f"Неполные данные подписки для user_id={user_id}: {subscription_data}")
        text = escape_message_parts(
            "❌ Ошибка получения данных.",
            " Попробуйте позже.",
            version=2
        )
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    generations_left = subscription_data[0] if subscription_data and len(subscription_data) > 0 else 0
    avatar_left = subscription_data[1] if subscription_data and len(subscription_data) > 1 else 0
    first_purchase = bool(subscription_data[5]) if len(subscription_data) > 5 else True
    created_at = subscription_data[10] if subscription_data and len(subscription_data) > 10 else None
    last_reminder_type = subscription_data[9] if subscription_data and len(subscription_data) > 9 else None

    # Проверяем статус оплаты
    payments = await get_user_payments(user_id)
    is_paying_user = bool(payments) or not first_purchase
    logger.debug(f"handle_back_to_menu_callback: user_id={user_id}, is_paying_user={is_paying_user}, first_purchase={first_purchase}")

    # Вычисляем время и дни с момента регистрации
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

    if generations_left > 0 or avatar_left > 0 or user_id in ADMIN_IDS:
        await delete_all_videos(state, user_id, query.bot)
        menu_text = (
            "🌈 Главное меню! Что хочешь сделать? 😊\n\n"
            "📸 Сгенерировать фото или видео\n"
            "👤 Мои аватары для создания или выбора активного\n"
            "💳 Купить пакет для пополнения баланса\n"
            "👤 Личный кабинет для проверки баланса и статистики\n"
            "ℹ️ Поддержка если нужна помощь"
        )
        try:
            await query.message.answer(
                escape_md(menu_text),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Главное меню отправлено для user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки главного меню для user_id={user_id}: {e}", exc_info=True)
            await query.message.answer(
                escape_md("❌ Ошибка при возврате в главное меню. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help"),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    else:
        # Для неоплативших пользователей без ресурсов
        if not is_paying_user:
            text = escape_md(
                "🔐 У вас нет ресурсов для доступа к меню! 😔\n"
                "Пожалуйста, купите пакет, чтобы продолжить творить с PixelPie. 🚀",
                version=2
            )
            await query.message.answer(
                text,
                reply_markup=await create_payment_only_keyboard(user_id, time_since_registration, days_since_registration, last_reminder_type),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Доступ к меню запрещён для user_id={user_id} из-за отсутствия ресурсов")
        else:
            # Для оплативших пользователей показываем все тарифы
            tariff_text = get_tariff_text(first_purchase=first_purchase, is_paying_user=True)
            try:
                await query.message.answer(
                    tariff_text,
                    reply_markup=await create_subscription_keyboard(hide_mini_tariff=False),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                logger.info(f"Все тарифы отправлены для оплатившего пользователя user_id={user_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки тарифов для user_id={user_id}: {e}", exc_info=True)
                await query.message.answer(
                    escape_md("❌ Ошибка при загрузке тарифов. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help"),
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )

    await state.update_data(user_id=user_id)

async def delete_all_videos(state: FSMContext, user_id: int, bot: Bot) -> None:
    """Удаляет все видео (меню и генерации), если они есть."""
    user_data = await state.get_data()
    for key in ['menu_video_message_id', 'generation_video_message_id']:
        if key in user_data:
            try:
                await bot.delete_message(chat_id=user_id, message_id=user_data[key])
                await state.update_data({key: None})
            except Exception as e:
                logger.debug(f"Не удалось удалить видео {key} для user_id={user_id}: {e}")

async def handle_photo_generate_menu_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработка меню фотогенерации."""
    logger.info(f"Открытие меню фотогенерации для user_id={user_id}")
    await delete_all_videos(state, user_id, query.bot)
    await state.clear()
    text = (
        escape_md("✨ Выбери, что хочешь создать:", version=2) + "\n\n" +
        escape_md("📸 Фотосессия с аватаром", version=2) + "\n" +
        escape_md("Создай уникальные фото с твоим личным AI-аватаром. ", version=2) +
        escape_md("Выбери стиль и получи профессиональные снимки за секунды!", version=2) + "\n\n" +
        escape_md("🖼 Фото по референсу", version=2) + "\n" +
        escape_md("Загрузи любое фото и преврати его в шедевр с твоим аватаром. ", version=2) +
        escape_md("Идеально для воссоздания понравившихся образов!", version=2)
    )
    try:
        await query.message.answer(
            text,
            reply_markup=await create_photo_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Меню фотогенерации отправлено для user_id={user_id}: {text}")
    except Exception as e:
        logger.error(f"Ошибка отправки меню фотогенерации для user_id={user_id}: {e}", exc_info=True)
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_video_generate_menu_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработка меню видеогенерации."""
    logger.info(f"Открытие меню видеогенерации для user_id={user_id}")
    await delete_all_videos(state, user_id, query.bot)
    await state.clear()
    await state.update_data(acting_as_user=True, user_id=user_id)  # Добавляем флаг
    text = escape_message_parts(
        "🎬 Выбери опцию видеогенерации:\n\n",
        "🎬 AI-видео (Kling 2.1)\n",
        "Оживи статичное изображение! Превращаем фото в короткое ",
        "динамичное видео с реалистичными движениями.",
        version=2
    )
    try:
        await query.message.answer(
            text,
            reply_markup=await create_video_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Меню видеогенерации отправлено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки меню видеогенерации для user_id={user_id}: {e}", exc_info=True)
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

async def handle_generate_with_avatar_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обработка генерации с аватаром."""
    user_id = query.from_user.id
    logger.debug(f"handle_generate_with_avatar_callback вызван: user_id={user_id}")

    try:
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)

        # Сбрасываем состояние только если это не админская генерация
        if not is_admin_generation:
            await state.clear()
            await state.update_data(generation_type='with_avatar', model_key='flux-trained')
        else:
            await state.update_data(
                generation_type='with_avatar',
                model_key='flux-trained',
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )
        logger.info(f"Инициализация 'Фотосессия с аватаром' для user_id={user_id}, target_user_id={target_user_id}")

        # Инвалидируем кэш подписки
        try:
            if hasattr(user_cache, 'invalidate'):
                await user_cache.invalidate(target_user_id)
                logger.debug(f"Кэш подписки инвалидирован для user_id={target_user_id}")
        except Exception as e:
            logger.warning(f"Ошибка при инвалидации user_cache для user_id={target_user_id}: {e}")

        # Проверяем подписку
        subscription_data = await check_resources(query.bot, target_user_id)
        if not subscription_data:
            logger.error(f"Ошибка проверки подписки для user_id={target_user_id}: subscription_data={subscription_data}")
            await state.clear()
            await query.answer("❌ Ошибка проверки подписки", show_alert=True)
            await query.message.answer(
                escape_md("❌ Ошибка проверки подписки. Попробуйте позже.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        # Проверяем, что subscription_data — это кортеж
        if not isinstance(subscription_data, tuple) or len(subscription_data) < 10:
            logger.error(f"Некорректные данные подписки для user_id={target_user_id}: {subscription_data}")
            await state.clear()
            await query.answer("❌ Ошибка данных подписки", show_alert=True)
            await query.message.answer(
                escape_md("❌ Некорректные данные подписки. Обратитесь в поддержку: @AXIDI_Help", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        generations_left, avatar_left, has_trained_model, _, _, _, _, active_avatar_id, _, is_blocked, _ = subscription_data
        logger.info(f"Баланс user_id={target_user_id}: generations_left={generations_left}, avatar_left={avatar_left}, has_trained_model={has_trained_model}, active_avatar_id={active_avatar_id}, is_blocked={is_blocked}")

        if is_blocked:
            logger.info(f"Заблокированный пользователь user_id={target_user_id} пытался открыть 'Фотосессия с аватаром'")
            await state.clear()
            await query.answer("🚫 Ваш аккаунт заблокирован", show_alert=True)
            await query.message.answer(
                escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        if not has_trained_model or not active_avatar_id:
            logger.info(f"Нет обученного аватара для user_id={target_user_id}")
            await state.clear()
            text = escape_md("❌ У вас нет обученного аватара. Создайте аватар через /menu → Мои аватары.", version=2)
            await query.answer("❌ Нет аватара", show_alert=True)
            await query.message.answer(
                text, reply_markup=await create_user_profile_keyboard(user_id, query.bot),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        if not is_admin_generation and generations_left < 1:
            logger.info(f"Недостаточно генераций для user_id={target_user_id}: generations_left={generations_left}")
            await state.clear()
            text = escape_md("❌ У вас закончились генерации. Пополните баланс через /menu → Тарифы.", version=2)
            await query.answer("❌ Недостаточно генераций", show_alert=True)
            await query.message.answer(
                text, reply_markup=await create_subscription_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        text = escape_md(f"🎨 Выбери категорию стилей для генерации{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:", version=2)
        keyboard = await create_avatar_style_choice_keyboard()
        await query.answer("Выбор стилей")
        await query.message.answer(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Клавиатура выбора стилей отправлена для user_id={user_id}, target_user_id={target_user_id}")
        await state.set_state(BotStates.AWAITING_STYLE_SELECTION)
    except Exception as e:
        logger.error(f"Ошибка в handle_generate_with_avatar_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_style_selection_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обработка выбора категории стилей."""
    user_id = query.from_user.id
    callback_data = query.data
    logger.debug(f"handle_style_selection_callback вызван: user_id={user_id}, callback_data={callback_data}")

    try:
        # Сохраняем важные данные
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        generation_type = user_data.get('generation_type', 'with_avatar')
        model_key = user_data.get('model_key', 'flux-trained')
        current_style_set = user_data.get('current_style_set', 'generic_avatar')

        # Сохраняем админский контекст
        if is_admin_generation:
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )
        else:
            await state.clear()

        await state.update_data(generation_type=generation_type, model_key=model_key)
        logger.info(f"Выбор категории стилей для user_id={user_id}, target_user_id={target_user_id}: callback_data={callback_data}")

        if callback_data == "select_new_male_avatar_styles" or (callback_data == "back_to_style_selection" and current_style_set == 'new_male_avatar'):
            await state.update_data(current_style_set='new_male_avatar', selected_gender='man')
            if not check_style_config('new_male_avatar'):
                logger.error(f"Ошибка конфигурации мужских стилей для user_id={user_id}")
                await state.clear()
                await query.answer("❌ Ошибка конфигурации стилей", show_alert=True)
                await query.message.answer(
                    escape_md("❌ Ошибка конфигурации мужских стилей. Обратитесь в поддержку: @AXIDI_Help", version=2),
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return
            keyboard = await create_new_male_avatar_styles_keyboard(page=1)
            text = escape_md(f"👨 Выбери мужской стиль или введи свой промпт{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:", version=2)
        elif callback_data == "select_new_female_avatar_styles" or (callback_data == "back_to_style_selection" and current_style_set == 'new_female_avatar'):
            await state.update_data(current_style_set='new_female_avatar', selected_gender='woman')
            if not check_style_config('new_female_avatar'):
                logger.error(f"Ошибка конфигурации женских стилей для user_id={user_id}")
                await state.clear()
                await query.answer("❌ Ошибка конфигурации стилей", show_alert=True)
                await query.message.answer(
                    escape_md("❌ Ошибка конфигурации женских стилей. Обратитесь в поддержку: @AXIDI_Help", version=2),
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return
            keyboard = await create_new_female_avatar_styles_keyboard(page=1)
            text = escape_md(f"👩 Выбери женский стиль или введи свой промпт{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:", version=2)
        else:
            await state.update_data(current_style_set='generic_avatar')
            if not check_style_config('generic_avatar'):
                logger.error(f"Ошибка конфигурации общих стилей для user_id={user_id}")
                await state.clear()
                await query.answer("❌ Ошибка конфигурации стилей", show_alert=True)
                await query.message.answer(
                    escape_md("❌ Ошибка конфигурации общих стилей. Обратитесь в поддержку: @AXIDI_Help", version=2),
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return
            keyboard = await create_prompt_selection_keyboard(back_callback_data="generate_with_avatar", style_source_dict=GENERATION_STYLES)
            text = escape_md(f"🎨 Выбери стиль или введи свой промпт{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:", version=2)

        await query.answer("Выбор стилей")
        await query.message.answer(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Клавиатура стилей отправлена для user_id={user_id}, target_user_id={target_user_id}: callback_data={callback_data}")
        await state.set_state(BotStates.AWAITING_STYLE_SELECTION)
    except Exception as e:
        logger.error(f"Ошибка в handle_style_selection_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_style_choice_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обработка выбора конкретного стиля."""
    user_id = query.from_user.id
    callback_data = query.data
    logger.info(f"handle_style_choice_callback: user_id={user_id}, callback_data={callback_data}")

    try:
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        generation_type = user_data.get('generation_type', 'with_avatar')

        # Определяем стиль и промпт в зависимости от callback_data
        if callback_data.startswith("style_generic_"):
            style_key = callback_data.replace("style_generic_", "")
            prompt = style_prompts.get(style_key)
            style_name = GENERATION_STYLES.get(style_key, style_key)
        elif callback_data.startswith("style_new_male_"):
            style_key = callback_data.replace("style_new_male_", "")
            prompt = new_male_avatar_prompts.get(style_key)
            style_name = NEW_MALE_AVATAR_STYLES.get(style_key, style_key)
            logger.debug(f"Мужской стиль: style_key={style_key}, prompt={prompt}, style_name={style_name}")
        elif callback_data.startswith("style_new_female_"):
            style_key = callback_data.replace("style_new_female_", "")
            prompt = new_female_avatar_prompts.get(style_key)
            style_name = NEW_FEMALE_AVATAR_STYLES.get(style_key, style_key)
            logger.debug(f"Женский стиль: style_key={style_key}, prompt={prompt}, style_name={style_name}")
        else:
            logger.error(f"Неизвестный формат callback_data для стиля: {callback_data}")
            await state.clear()
            await query.answer("❌ Ошибка выбора стиля", show_alert=True)
            await query.message.answer(
                escape_md("❌ Ошибка выбора стиля. Попробуйте еще раз.", version=2),
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        if not prompt:
            logger.error(f"Промпт не найден для стиля '{style_key}'")
            await state.clear()
            await query.answer("❌ Промпт не найден", show_alert=True)
            await query.message.answer(
                escape_md(f"❌ Промпт для стиля '{style_name}' не найден.", version=2),
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        # Сохраняем админский контекст
        if is_admin_generation:
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )

        # Сохраняем параметры стиля
        await state.update_data(
            prompt=prompt,
            style_name=style_name,
            generation_type=generation_type,
            model_key=user_data.get('model_key', 'flux-trained')
        )

        # Обрабатываем в зависимости от типа генерации
        if generation_type == 'ai_video_v2_1':
            await state.update_data(
                video_prompt=prompt,
                video_cost=30,
                awaiting_video_photo=True
            )
            await query.message.answer(
                escape_md(
                    f"✅ Выбран стиль: {style_name}\n\n"
                    f"📸 Загрузи фото для генерации видео или пропусти (/skip){' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:",
                    version=2
                ),
                reply_markup=await create_back_keyboard("ai_video_v2_1"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(VideoStates.AWAITING_VIDEO_PHOTO)
            logger.info(f"Стиль '{style_name}' выбран для видеогенерации, user_id={user_id}, target_user_id={target_user_id}, prompt={prompt[:50]}...")
        else:
            await query.answer("Стиль выбран")
            await query.message.answer(
                escape_md(f"✅ Выбран стиль: {style_name}", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await ask_for_aspect_ratio_callback(query, state)
            logger.info(f"Стиль '{style_name}' выбран для фотогенерации, user_id={user_id}, target_user_id={target_user_id}, prompt={prompt[:50]}...")

    except Exception as e:
        logger.error(f"Ошибка в handle_style_choice_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_male_styles_page_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Переход по страницам мужских стилей."""
    user_id = query.from_user.id
    callback_data = query.data
    try:
        page = int(callback_data.replace("male_styles_page_", ""))
    except ValueError:
        logger.error(f"Некорректный формат callback_data для male_styles_page: {callback_data}")
        await query.answer("❌ Ошибка: неверный номер страницы", show_alert=True)
        return

    logger.info(f"Переход на страницу мужских стилей page={page} для user_id={user_id}")
    user_data = await state.get_data()
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    generation_type = user_data.get('generation_type', 'with_avatar')
    model_key = user_data.get('model_key', 'flux-trained')

    # Сохраняем админский контекст
    if is_admin_generation:
        await state.update_data(
            is_admin_generation=True,
            admin_generation_for_user=target_user_id,
            message_recipient=user_id,
            generation_target_user=target_user_id,
            original_admin_user=user_id
        )
    else:
        await state.clear()

    await state.update_data(
        generation_type=generation_type,
        model_key=model_key,
        current_style_set='new_male_avatar',
        selected_gender='man'
    )
    if not check_style_config('new_male_avatar'):
        logger.error(f"Ошибка конфигурации мужских стилей для user_id={user_id}")
        await state.clear()
        await query.message.answer(
            escape_md("❌ Ошибка конфигурации мужских стилей. Обратитесь к администратору.", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    keyboard = await create_new_male_avatar_styles_keyboard(page)
    text = escape_md(f"👨 Выбери мужской стиль или введи свой промпт{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:", version=2)
    try:
        await query.answer()
        await query.message.edit_text(
            text=text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Клавиатура мужских стилей page={page} обновлена для user_id={user_id}, target_user_id={target_user_id}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении клавиатуры мужских стилей для user_id={user_id}: {e}", exc_info=True)
        await query.message.answer(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.set_state(BotStates.AWAITING_STYLE_SELECTION)

async def handle_female_styles_page_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Переход по страницам женских стилей."""
    user_id = query.from_user.id
    callback_data = query.data
    try:
        page = int(callback_data.replace("female_styles_page_", ""))
    except ValueError:
        logger.error(f"Некорректный формат callback_data для female_styles_page: {callback_data}")
        await query.answer("❌ Ошибка: неверный номер страницы", show_alert=True)
        return

    logger.info(f"Переход на страницу женских стилей page={page} для user_id={user_id}")
    user_data = await state.get_data()
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    generation_type = user_data.get('generation_type', 'with_avatar')
    model_key = user_data.get('model_key', 'flux-trained')

    # Сохраняем админский контекст
    if is_admin_generation:
        await state.update_data(
            is_admin_generation=True,
            admin_generation_for_user=target_user_id,
            message_recipient=user_id,
            generation_target_user=target_user_id,
            original_admin_user=user_id
        )
    else:
        await state.clear()

    await state.update_data(
        generation_type=generation_type,
        model_key=model_key,
        current_style_set='new_female_avatar',
        selected_gender='woman'
    )
    if not check_style_config('new_female_avatar'):
        logger.error(f"Ошибка конфигурации женских стилей для user_id={user_id}")
        await state.clear()
        await query.message.answer(
            escape_md("❌ Ошибка конфигурации женских стилей. Обратитесь к администратору.", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    keyboard = await create_new_female_avatar_styles_keyboard(page)
    text = escape_md(f"👩 Выбери женский стиль или введи свой промпт{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:", version=2)
    try:
        await query.answer()
        await query.message.edit_text(
            text=text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Клавиатура женских стилей page={page} обновлена для user_id={user_id}, target_user_id={target_user_id}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении клавиатуры женских стилей для user_id={user_id}: {e}", exc_info=True)
        await query.message.answer(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.set_state(BotStates.AWAITING_STYLE_SELECTION)

async def handle_photo_to_photo_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработка photo-to-photo генерации."""
    await delete_all_videos(state, user_id, query.bot)
    if not await check_active_avatar(query.bot, user_id):
        return
    if not await check_resources(query.bot, user_id, required_photos=2):
        return
    await state.clear()
    await reset_generation_context(state, "photo_to_photo")
    await state.update_data(generation_type='photo_to_photo', model_key="flux-trained", waiting_for_photo=True)
    text = (
        escape_md("🖼 Фото по референсу", version=2) + "\n\n" +
        escape_md("Загрузи фото-референс, которое хочешь воспроизвести с твоим аватаром. ", version=2) +
        escape_md("📝 PixelPie AI создаст твое фото сам!", version=2)
    )
    await query.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="generate_menu")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Сообщение о фото по референсу отправлено для user_id={user_id}: {text}")

async def handle_ai_video_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработка генерации AI-видео (Kling 2.1)."""
    logger.debug(f"handle_ai_video_callback: user_id={user_id}")
    try:
        model_key = "kwaivgi/kling-v2.1"
        required_photos = 30
        generation_type = "ai_video_v2_1"

        # Проверка ресурсов пользователя
        if not await check_resources(query.bot, user_id, required_photos=required_photos):
            logger.info(f"Недостаточно ресурсов для генерации видео user_id={user_id}, required_photos={required_photos}")
            return

        # Сбрасываем состояние и устанавливаем данные для генерации
        await state.clear()
        await reset_generation_context(state, generation_type, user_id=user_id)
        await state.update_data(
            generation_type=generation_type,
            model_key=model_key,
            video_cost=required_photos,
            acting_as_user=True,  # Добавляем флаг
            user_id=user_id
        )

        # Формируем текст сообщения
        model_name = IMAGE_GENERATION_MODELS.get(model_key, {}).get('name', 'AI-Видео (Kling 2.1)')
        text = escape_message_parts(
            f"🎬 {model_name}\n\n",
            f"Для создания видео потребуется *{required_photos} печенек* с твоего баланса.\n\n",
            "Выбери стиль видео или введи свой промпт:",
            version=2
        )
        logger.debug(f"handle_ai_video_callback: сформирован текст: {text[:200]}...")

        # Формируем клавиатуру
        reply_markup = await create_video_styles_keyboard()

        # Отправляем сообщение
        await query.message.answer(
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Меню выбора стилей видео отправлено для user_id={user_id}, model_name={model_name}")

        # Уведомляем пользователя
        await query.answer("Выбери стиль для видео")
        await state.set_state(BotStates.AWAITING_VIDEO_STYLE)
    except Exception as e:
        logger.error(f"Ошибка в handle_ai_video_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.message.answer(
            text=safe_escape_markdown("❌ Ошибка при запуске генерации видео. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer("❌ Ошибка при запуске генерации видео", show_alert=True)
        await state.update_data(user_id=user_id)

async def handle_video_style_choice_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обработка выбора стиля видео."""
    user_id = query.from_user.id
    style_key = query.data.replace("video_style_", "")
    logger.info(f"handle_video_style_choice_callback: user_id={user_id}, style_key={style_key}")

    try:
        from generation_config import VIDEO_GENERATION_STYLES, VIDEO_STYLE_PROMPTS
        prompt = VIDEO_STYLE_PROMPTS.get(style_key)
        style_name = VIDEO_GENERATION_STYLES.get(style_key, style_key)
        if not prompt:
            logger.error(f"Промпт не найден для стиля '{style_name}' (style_key={style_key})")
            await query.message.answer(
                escape_md(f"❌ Промпт для стиля '{style_name}' не найден.", version=2),
                reply_markup=await create_video_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        await state.update_data(
            generation_type="ai_video_v2_1",
            model_key="kwaivgi/kling-v2.1",
            video_prompt=prompt,
            style_name=style_name,
            video_cost=30,
            awaiting_video_photo=True
        )

        # Запрашиваем загрузку фото
        await query.message.answer(
            escape_md(f"✅ Выбран стиль: {style_name}\n\n📸 Загрузи фото для генерации видео или пропусти (/skip).", version=2),
            reply_markup=await create_back_keyboard("ai_video_v2_1"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(VideoStates.AWAITING_VIDEO_PHOTO)
        logger.info(f"Стиль '{style_name}' выбран для user_id={user_id}, prompt={prompt[:50]}...")
    except Exception as e:
        logger.error(f"Ошибка в handle_video_style_choice_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка при выборе стиля. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_video_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_custom_prompt_manual_callback(query: CallbackQuery, state: FSMContext) -> None:
    user_id = query.from_user.id
    logger.debug(f"handle_custom_prompt_manual_callback: user_id={user_id}, data={await state.get_data()}")

    try:
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        generation_type = user_data.get('generation_type', 'with_avatar')

        # Проверка корректности generation_type
        if generation_type not in ['with_avatar', 'photo_to_photo', 'ai_video_v2_1']:
            logger.error(f"Некорректный generation_type для user_id={user_id}: {generation_type}")
            await query.message.answer(
                escape_md("❌ Ошибка: неверный тип генерации. Начни заново через /menu.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.clear()
            await state.update_data(user_id=user_id)
            return

        logger.info(f"Параметры ручного промпта: user_id={user_id}, target_user_id={target_user_id}, generation_type={generation_type}")

        # Сбрасываем состояние, сохраняя админский контекст
        await state.clear()
        preserved_data = {
            'generation_type': generation_type,
            'model_key': "kwaivgi/kling-v2.1" if generation_type == 'ai_video_v2_1' else 'flux-trained',
            'video_cost': 30 if generation_type == 'ai_video_v2_1' else None,
            'came_from_custom_prompt': True,
            'awaiting_llama_after_photo': False,  # Сбрасываем флаг для избежания конфликтов
            'user_id': user_id,
            'acting_as_user': True
        }

        if is_admin_generation:
            preserved_data.update({
                'is_admin_generation': True,
                'admin_generation_for_user': target_user_id,
                'message_recipient': user_id,
                'generation_target_user': target_user_id,
                'original_admin_user': user_id
            })

        await state.update_data(**preserved_data)

        # Проверка активного аватара для фото-генерации
        if generation_type in ['with_avatar', 'photo_to_photo']:
            if not await check_active_avatar(query.bot, target_user_id):
                logger.warning(f"Активный аватар отсутствует для target_user_id={target_user_id}")
                await query.message.answer(
                    escape_md("❌ У тебя нет активного аватара. Создай его в Личном кабинете.", version=2),
                    reply_markup=await create_user_profile_keyboard(user_id, query.bot),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.clear()
                await state.update_data(user_id=user_id)
                return

        # Запрашиваем ввод в зависимости от типа генерации
        if generation_type == 'ai_video_v2_1':
            await state.update_data(awaiting_video_photo=True)
            text = escape_md(
                f"📸 Загрузи фото для генерации видео или пропусти (/skip){' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:",
                version=2
            )
            back_callback = "ai_video_v2_1"
            await state.set_state(VideoStates.AWAITING_VIDEO_PHOTO)
        else:
            await state.update_data(waiting_for_custom_prompt_manual=True)
            text = escape_md(
                f"📝 Введи описание (промпт) для генерации изображения{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:\n"
                f"Пример: \"Девушка на закате у моря\" или \"Бизнесмен в офисе\"",
                version=2
            )
            back_callback = "back_to_style_selection"
            await state.set_state(BotStates.AWAITING_STYLE_SELECTION)

        await query.message.answer(
            text,
            reply_markup=await create_back_keyboard(back_callback),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Запрошен {'ввод фото для видео' if generation_type == 'ai_video_v2_1' else 'ввод промпта для фото'}, user_id={user_id}, target_user_id={target_user_id}")

    except Exception as e:
        logger.error(f"Ошибка в handle_custom_prompt_manual_callback для user_id={user_id}: {e}", exc_info=True)
        await query.answer("❌ Ошибка при запросе промпта", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуй снова или обратись в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()
        await state.update_data(user_id=user_id)

async def handle_custom_prompt_llama_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Ввод идеи для AI-помощника."""
    user_id = query.from_user.id
    logger.info(f"handle_custom_prompt_llama_callback: user_id={user_id}, data={await state.get_data()}")
    try:
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        generation_type = user_data.get('generation_type', 'with_avatar')

        if generation_type not in ['with_avatar', 'photo_to_photo', 'ai_video_v2_1']:
            logger.error(f"Некорректный generation_type для user_id={user_id}: {generation_type}")
            await query.message.answer(
                escape_md("❌ Ошибка: неверный тип генерации. Начните заново.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.clear()
            await state.update_data(user_id=user_id)
            return

        if generation_type in ['with_avatar', 'photo_to_photo'] and not await check_active_avatar(query.bot, target_user_id):
            logger.warning(f"Активный аватар отсутствует для target_user_id={target_user_id}")
            return

        preserved_data = {
            'selected_gender': user_data.get('selected_gender'),
            'generation_type': generation_type,
            'model_key': "kwaivgi/kling-v2.1" if generation_type == 'ai_video_v2_1' else 'flux-trained',
            'current_style_set': user_data.get('current_style_set'),
            'is_admin_generation': is_admin_generation,
            'admin_generation_for_user': target_user_id,
            'message_recipient': user_id,
            'generation_target_user': target_user_id,
            'original_admin_user': user_id if is_admin_generation else None,
            'video_cost': 30 if generation_type == 'ai_video_v2_1' else None,
            'use_llama_prompt': True,
            'user_id': user_id,
            'acting_as_user': True,
            'awaiting_llama_after_photo': True if generation_type == 'ai_video_v2_1' else False
        }

        await state.clear()
        await state.update_data(**preserved_data)

        if generation_type == 'ai_video_v2_1':
            await state.update_data(awaiting_video_photo=True)
            text = escape_md(
                f"📸 Загрузи фото для генерации видео или пропусти (/skip){' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:",
                version=2
            )
            back_callback = "ai_video_v2_1"
            await state.set_state(VideoStates.AWAITING_VIDEO_PHOTO)
        else:
            await state.update_data(waiting_for_custom_prompt_llama=True)
            text = escape_md(
                f"🤖 AI-помощник поможет создать детальный промпт для генерации{' с твоим аватаром' if generation_type == 'with_avatar' else ''}{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}!\n\n"
                f"Опиши свою идею простыми словами, например: _\"деловой человек в офисе\"_ или _\"девушка на пляже на закате\"_",
                version=2
            )
            back_callback = "back_to_style_selection"
            await state.set_state(BotStates.AWAITING_STYLE_SELECTION)

        await query.message.answer(
            text,
            reply_markup=await create_back_keyboard(back_callback),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Запрошен {'ввод текста для AI-промпта' if generation_type != 'ai_video_v2_1' else 'загрузка фото для AI-промпта'}, user_id={user_id}, target_user_id={target_user_id}")
    except Exception as e:
        logger.error(f"Ошибка в handle_custom_prompt_llama_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

async def handle_confirm_video_generation_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Подтверждение параметров генерации видео."""
    logger.debug(f"handle_confirm_video_generation_callback: user_id={user_id}, data={await state.get_data()}")
    try:
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        generation_type = user_data.get('generation_type', 'ai_video_v2_1')
        model_key = user_data.get('model_key', 'kwaivgi/kling-v2.1')
        style_name = user_data.get('style_name', 'custom')
        prompt = user_data.get('video_prompt')
        start_image_path = user_data.get('start_image')

        if not prompt:
            logger.error(f"Отсутствует video_prompt для user_id={user_id}, target_user_id={target_user_id}")
            await query.message.answer(
                escape_md("❌ Ошибка: отсутствует описание видео. Начните заново.", version=2),
                reply_markup=await create_video_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.clear()
            await state.update_data(user_id=user_id)
            return

        # Проверка ресурсов
        cost = user_data.get('video_cost', 30)
        if not await check_user_resources(query.bot, target_user_id, required_photos=cost):
            logger.info(f"Недостаточно ресурсов для user_id={target_user_id}, required_photos={cost}")
            await state.clear()
            await state.update_data(user_id=user_id)
            return

        # Сохраняем админский контекст
        if is_admin_generation:
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )

        await state.update_data(
            generation_type=generation_type,
            model_key=model_key,
            video_cost=cost,
            style_name=style_name,
            user_id=user_id
        )

        logger.info(f"Запуск генерации видео для user_id={user_id}, target_user_id={target_user_id}, style={style_name}, prompt={prompt[:50]}...")
        await query.message.delete()  # Удаляем сообщение с подтверждением
        from generation.videos import generate_video
        await generate_video(query.message, state)
        await query.answer("✅ Генерация видео запущена")
    except Exception as e:
        logger.error(f"Ошибка в handle_confirm_video_generation_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Ошибка при запуске генерации", show_alert=True)
        await query.message.answer(
            escape_md("❌ Ошибка при запуске генерации видео. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_video_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

async def handle_confirm_assisted_prompt_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Подтверждение AI-промпта."""
    user_id = query.from_user.id
    logger.info(f"handle_confirm_assisted_prompt_callback: user_id={user_id}, data={await state.get_data()}")
    try:
        user_data = await state.get_data()
        prompt = user_data.get('prompt')
        generation_type = user_data.get('generation_type', 'with_avatar')
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        model_key = user_data.get('model_key', 'flux-trained')
        style_name = user_data.get('style_name', 'custom')

        if not prompt:
            logger.error(f"Отсутствует assisted_prompt для user_id={user_id}")
            await query.message.answer(
                escape_md("❌ Ошибка: промпт не найден. Начните заново.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.clear()
            await state.update_data(user_id=user_id)
            return

        if is_admin_generation:
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )

        if generation_type == 'ai_video_v2_1':
            await state.update_data(
                video_prompt=prompt,
                style_name=style_name,
                video_cost=30,
                awaiting_video_photo=user_data.get('awaiting_video_photo', False),
                start_image=user_data.get('start_image'),
                user_id=user_id
            )
            text = escape_md(
                f"✅ Подтвержден промпт для видео: _{prompt[:50]}{'...' if len(prompt) > 50 else ''}_\n\n"
                f"📸 Фото: {'Загружено' if user_data.get('start_image') else 'Отсутствует'}\n\n"
                f"Все верно?",
                version=2
            )
            await query.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Да, генерировать!", callback_data="confirm_video_generation")],
                    [InlineKeyboardButton(text="✏️ Изменить промпт", callback_data="edit_assisted_prompt")],
                    [InlineKeyboardButton(text="📸 Изменить фото", callback_data="edit_video_photo")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="ai_video_v2_1")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(VideoStates.AWAITING_VIDEO_CONFIRMATION)
            logger.info(f"Подтверждение AI-промпта для видеогенерации, user_id={user_id}, target_user_id={target_user_id}, prompt={prompt[:50]}...")
        else:
            await state.update_data(
                prompt=prompt,
                style_name=style_name,
                user_id=user_id
            )
            await query.message.answer(
                escape_md(f"✅ Подтвержден промпт: _{prompt[:50]}{'...' if len(prompt) > 50 else ''}_", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await ask_for_aspect_ratio_callback(query, state)
            logger.info(f"Подтверждение AI-промпта для фотогенерации, user_id={user_id}, target_user_id={target_user_id}, prompt={prompt[:50]}...")
    except Exception as e:
        logger.error(f"Ошибка в handle_confirm_assisted_prompt_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

async def handle_edit_assisted_prompt_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Редактирование промпта от AI помощника."""
    user_data = await state.get_data()
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    await state.clear()
    # Сохраняем админский контекст
    if is_admin_generation:
        await state.update_data(
            is_admin_generation=True,
            admin_generation_for_user=target_user_id,
            message_recipient=user_id,
            generation_target_user=target_user_id,
            original_admin_user=user_id
        )
    await state.update_data(waiting_for_custom_prompt_manual=True, came_from_custom_prompt=True)
    current_prompt = user_data.get('prompt', '')
    text = (
        f"✏️ Отредактируй промпт или введи свой:\n\n"
        f"Текущий промпт:\n`{escape_md(current_prompt, version=2)}`"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="confirm_assisted_prompt")]])
    await query.message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Сообщение для редактирования промпта отправлено для user_id={user_id}, target_user_id={target_user_id}")

async def handle_skip_prompt_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Пропуск ввода промпта."""
    user_id = query.from_user.id
    logger.info(f"skip_prompt: Установлен стандартный промпт для user_id={user_id}")
    user_data = await state.get_data()
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    generation_type = user_data.get('generation_type', 'photo_to_photo')
    model_key = user_data.get('model_key', 'flux-trained')
    reference_image_url = user_data.get('reference_image_url')
    photo_path = user_data.get('photo_path')
    await state.clear()
    # Сохраняем админский контекст
    if is_admin_generation:
        await state.update_data(
            is_admin_generation=True,
            admin_generation_for_user=target_user_id,
            message_recipient=user_id,
            generation_target_user=target_user_id,
            original_admin_user=user_id
        )
    await state.update_data(
        prompt="copy reference style",
        generation_type=generation_type,
        model_key=model_key
    )
    if reference_image_url:
        await state.update_data(reference_image_url=reference_image_url)
    if photo_path:
        await state.update_data(photo_path=photo_path)
    logger.info(f"Сохранены данные: generation_type={generation_type}, model_key={model_key}, target_user_id={target_user_id}")
    await query.answer()
    await query.message.answer(
        escape_md("✅ Использую стандартный промпт. Выбери соотношение сторон:", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await ask_for_aspect_ratio_callback(query, state)
    await state.set_state(BotStates.AWAITING_STYLE_SELECTION)

async def handle_aspect_ratio_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обработка выбора соотношения сторон."""
    user_id = query.from_user.id
    callback_data = query.data
    logger.debug(f"handle_aspect_ratio_callback вызван: user_id={user_id}, callback_data={callback_data}")
    
    try:
        aspect_ratio = callback_data.replace("aspect_", "")
        if not aspect_ratio or aspect_ratio not in ASPECT_RATIOS:
            logger.error(f"Некорректное соотношение сторон: {aspect_ratio}")
            await query.answer(f"❌ Неверный формат соотношения сторон: {aspect_ratio}", show_alert=True)
            await query.message.answer(
                escape_md(f"❌ Неверное соотношение сторон: {aspect_ratio}. Выберите заново.", version=2),
                reply_markup=await create_aspect_ratio_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)

        # Сохраняем админский контекст
        if is_admin_generation:
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )

        await state.update_data(aspect_ratio=aspect_ratio)
        
        if not user_data.get('generation_type'):
            logger.error(f"Отсутствует generation_type для user_id={user_id}")
            await state.clear()
            await query.answer("❌ Ошибка: тип генерации не задан", show_alert=True)
            await query.message.answer(
                escape_md("❌ Тип генерации не задан. Начните заново через меню.", version=2),
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        if not user_data.get('prompt'):
            logger.error(f"Отсутствует prompt для user_id={user_id}")
            await state.clear()
            await query.answer("❌ Ошибка: промпт не задан", show_alert=True)
            await query.message.answer(
                escape_md("❌ Промпт не задан. Начните заново через меню.", version=2),
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        generation_type = user_data.get('generation_type', 'unknown')
        prompt = user_data.get('prompt', 'Не указан')
        generation_type_display = {
            'with_avatar': 'Фотосессия с аватаром',
            'photo_to_photo': 'Фото по референсу',
            'ai_video_v2_1': 'AI-видео (Kling 2.1)',
            'prompt_assist': 'С помощником AI'
        }.get(generation_type, generation_type)
        prompt_source = ""
        selected_gender = user_data.get('selected_gender')
        current_style_set = user_data.get('current_style_set')
        if current_style_set == 'new_male_avatar':
            prompt_source = "👨 Мужской стиль"
            for style_key, style_name in NEW_MALE_AVATAR_STYLES.items():
                if new_male_avatar_prompts.get(style_key) == prompt:
                    prompt_source += f": {style_name}"
                    break
        elif current_style_set == 'new_female_avatar':
            prompt_source = "👩 Женский стиль"
            for style_key, style_name in NEW_FEMALE_AVATAR_STYLES.items():
                if new_female_avatar_prompts.get(style_key) == prompt:
                    prompt_source += f": {style_name}"
                    break
        elif current_style_set == 'generic_avatar':
            prompt_source = "🎨 Общий стиль"
            for style_key, style_name in GENERATION_STYLES.items():
                if style_prompts.get(style_key) == prompt:
                    prompt_source += f": {style_name}"
                    break
        if user_data.get('came_from_custom_prompt'):
            if user_data.get('user_input_for_llama'):
                prompt_source = "🤖 Промпт от AI-помощника"
            else:
                prompt_source = "✍️ Свой промпт"
        prompt_preview = prompt[:150] + '...' if len(prompt) > 150 else prompt
        confirm_text_parts = [
            f"📋 Проверь параметры генерации:\n\n",
            f"🎨 Тип: {escape_md(generation_type_display, version=2)}\n"
        ]
        if prompt_source:
            confirm_text_parts.append(f"📝 Выбор: {escape_md(prompt_source, version=2)}\n")
        confirm_text_parts.extend([
            f"📐 Формат: {escape_md(aspect_ratio, version=2)}\n",
            f"\n💭 Промпт: _{escape_md(prompt_preview, version=2)}_\n\n",
            f"Всё верно?"
        ])
        confirm_text = "".join(confirm_text_parts)
        await query.answer()
        await query.message.answer(
            confirm_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, генерировать!", callback_data="confirm_generation")],
                [InlineKeyboardButton(text="🔙 Изменить", callback_data="back_to_style_selection")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Соотношение сторон '{aspect_ratio}' выбрано для user_id={user_id}, target_user_id={target_user_id}")
        await state.set_state(BotStates.AWAITING_STYLE_SELECTION)
    except Exception as e:
        logger.error(f"Ошибка в handle_aspect_ratio_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка при выборе соотношения сторон", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_back_to_aspect_selection_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Возврат к выбору соотношения сторон."""
    user_id = query.from_user.id
    logger.debug(f"handle_back_to_aspect_selection_callback вызван для user_id={user_id}")
    user_data = await state.get_data()
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    # Сохраняем админский контекст
    if is_admin_generation:
        await state.update_data(
            is_admin_generation=True,
            admin_generation_for_user=target_user_id,
            message_recipient=user_id,
            generation_target_user=target_user_id,
            original_admin_user=user_id
        )
    await query.answer()
    await ask_for_aspect_ratio_callback(query, state)
    await state.set_state(BotStates.AWAITING_STYLE_SELECTION)

async def handle_back_to_style_selection_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Возврат к выбору стилей."""
    logger.debug(f"handle_back_to_style_selection_callback вызван: user_id={user_id}")
    
    try:
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        current_style_set = user_data.get('current_style_set', 'generic_avatar')
        callback_data_map = {
            'new_male_avatar': 'select_new_male_avatar_styles',
            'new_female_avatar': 'select_new_female_avatar_styles',
            'generic_avatar': 'select_generic_avatar_styles'
        }
        callback_data = callback_data_map.get(current_style_set, 'select_generic_avatar_styles')
        logger.info(f"Возврат к выбору стилей для user_id={user_id}, target_user_id={target_user_id}: current_style_set={current_style_set}, callback_data={callback_data}")
        # Сохраняем админский контекст
        if is_admin_generation:
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )
        await handle_style_selection_callback(query, state)
    except Exception as e:
        logger.error(f"Ошибка в handle_back_to_style_selection_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_confirm_generation_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработка подтверждения генерации."""
    user_data = await state.get_data()
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    generation_type = user_data.get('generation_type')
    if generation_type == 'admin_with_user_avatar':
        await state.update_data(generation_type='with_avatar')
        generation_type = 'with_avatar'
        logger.info(f"Изменен тип генерации с 'admin_with_user_avatar' на 'with_avatar' для админской генерации")

    # Сохраняем админский контекст
    if is_admin_generation:
        await state.update_data(
            is_admin_generation=True,
            admin_generation_for_user=target_user_id,
            message_recipient=user_id,
            generation_target_user=target_user_id,
            original_admin_user=user_id
        )
    
    if not user_data.get('model_key'):
        if generation_type in ['with_avatar', 'photo_to_photo']:
            await state.update_data(model_key='flux-trained')
        elif generation_type == 'ai_video_v2_1':
            model_key = 'kwaivgi/kling-v2.1'
            await state.update_data(model_key=model_key)
        else:
            await state.update_data(model_key='flux-trained')
        logger.info(f"Установлен model_key='{user_data.get('model_key')}' для generation_type='{generation_type}'")
    
    if generation_type == 'photo_to_photo':
        required_fields = ['reference_image_url', 'prompt', 'aspect_ratio']
        missing_fields = [f for f in required_fields if not user_data.get(f)]
        if missing_fields:
            logger.error(f"Отсутствуют поля для photo_to_photo: {missing_fields}")
            await state.clear()
            await query.message.answer(
                escape_md("❌ Ошибка: отсутствуют данные. Начните заново через меню.", version=2),
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        reference_url = user_data.get('reference_image_url')
        if not reference_url or not reference_url.startswith('http'):
            logger.error(f"Некорректный reference_image_url: {reference_url}")
            await state.clear()
            await query.message.answer(
                escape_md("❌ Ошибка: референсное изображение не загружено. Попробуйте снова.", version=2),
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
    logger.info(f"Запуск генерации для user_id={user_id}, target_user_id={target_user_id}, generation_type={generation_type}, "
                f"model_key={user_data.get('model_key')}")
    try:
        if generation_type in ['with_avatar', 'photo_to_photo']:
            await generate_image(query.message, state, num_outputs=2, user_id=user_id)
        elif generation_type == 'ai_video_v2_1':
            await handle_generate_video_callback(query, state)
        else:
            logger.error(f"Неизвестный тип генерации: {generation_type}")
            await state.clear()
            await query.message.answer(
                escape_md("❌ Неизвестный тип генерации. Попробуйте снова.", version=2),
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Ошибка в confirm_generation: {e}", exc_info=True)
        await state.clear()
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте еще раз.", version=2),
            reply_markup=await create_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_rating_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обработка оценки генерации."""
    user_id = query.from_user.id
    callback_data = query.data
    logger.debug(f"handle_rating_callback вызван: user_id={user_id}, callback_data={callback_data}")

    try:
        rating = int(callback_data.split('_')[1])
        if not 1 <= rating <= 5:
            logger.error(f"Некорректная оценка для user_id={user_id}: {rating}")
            await safe_answer_callback(query, "❌ Неверная оценка. Выберите от 1 до 5 звезд.", show_alert=True)
            return

        user_data = await state.get_data()
        generation_type = user_data.get('generation_type', 'unknown')
        model_key = user_data.get('model_key', 'unknown')

        try:
            await add_rating(user_id, generation_type, model_key, rating)
            logger.info(f"Оценка {rating} сохранена для user_id={user_id}, generation_type={generation_type}, model_key={model_key}")
        except Exception as e:
            logger.error(f"Ошибка в add_rating для user_id={user_id}: {e}", exc_info=True)
            await safe_answer_callback(query, "❌ Не удалось сохранить оценку. Попробуйте позже.", show_alert=True)
            return

        await state.clear()
        await safe_answer_callback(query, f"Спасибо за оценку {rating} ⭐!", show_alert=True)
        await query.message.answer(
            escape_message_parts(f"Спасибо за оценку {rating} ⭐! Твой отзыв поможет нам стать лучше."),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info(f"Сообщение об успешной оценке отправлено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при обработке рейтинга для user_id={user_id}: {e}", exc_info=True)
        await safe_answer_callback(query, "❌ Ошибка при сохранении оценки. Попробуйте позже.", show_alert=True)
        await state.clear()
        await query.message.answer(
            escape_md("❌ Произошла ошибка при сохранении оценки. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help"),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_user_profile_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показ личного кабинета."""
    logger.debug(f"handle_user_profile_callback вызван для user_id={user_id}")
    await delete_all_videos(state, user_id, query.bot)
    await state.clear()
    await reset_generation_context(state, "user_profile", user_id=user_id)
    subscription_data = await check_database_user(user_id)
    if not subscription_data or len(subscription_data) < 9:
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка получения данных профиля.",
            " Попробуйте позже.",
            version=2
        )
        logger.debug(f"handle_user_profile_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return
    generations_left, avatar_left = subscription_data[0], subscription_data[1]
    text_parts = [
        "👤 Личный кабинет\n\n",
        f"💰 Баланс: {generations_left} печенек, {avatar_left} аватар{'ов' if avatar_left != 1 else ''}"
    ]
    text = escape_message_parts(*text_parts, version=2)
    logger.debug(f"handle_user_profile_callback: сформирован текст: {text[:200]}...")
    await query.message.answer(
        text,
        reply_markup=await create_user_profile_keyboard(user_id, query.bot),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

async def handle_check_subscription_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Проверка подписки."""
    logger.debug(f"handle_check_subscription_callback вызван для user_id={user_id}")
    subscription_data = await check_database_user(user_id)
    if not subscription_data or len(subscription_data) < 9:
        await state.clear()
        await safe_answer_callback(query, "❌ Ошибка получения данных", show_alert=True)
        text = escape_message_parts(
            "❌ Ошибка получения данных профиля.",
            " Попробуйте позже.",
            version=2
        )
        logger.debug(f"handle_check_subscription_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return
    generations_left, avatar_left, _, username, _, _, email, _, _, _, _ = subscription_data
    text_parts = [
        "💳 Твоя подписка:\n\n",
        f"📸 Печенек на балансе: {generations_left}\n",
        f"👤 Аватары на балансе: {avatar_left}"
    ]
    if email:
        text_parts.append(f"\n📧 Email: {email}")
    text_parts.extend([
        "\n\n",
        "_Печеньки тратятся на генерацию изображений и видео._\n",
        "_Аватары нужны для создания персональных моделей._"
    ])
    text = escape_message_parts(*text_parts, version=2)
    logger.debug(f"handle_check_subscription_callback: сформирован текст: {text[:200]}...")
    await query.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="subscribe")],
            [InlineKeyboardButton(text="🔙 В личный кабинет", callback_data="user_profile")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Клавиатура подписки создана для user_id={user_id}")
    await state.update_data(user_id=user_id)

async def handle_user_stats_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показ статистики пользователя, разбивая длинные сообщения на части, если они превышают лимит Telegram."""
    logger.debug(f"handle_user_stats_callback вызван для user_id={user_id}")
    await state.clear()
    try:
        gen_stats = await get_user_generation_stats(user_id)
        payments = await get_user_payments(user_id)
        total_spent = sum(p[2] for p in payments if p[2] is not None)
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("SELECT referred_id, status, completed_at FROM referrals WHERE referrer_id = ?", (user_id,))
            my_referrals = await c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения статистики для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка получения статистики.",
            " Попробуйте позже.",
            version=2
        )
        logger.debug(f"handle_user_stats_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    active_referrals = 0
    total_bonuses = 0
    for ref in my_referrals:
        ref_user_id = ref['referred_id']
        ref_status = ref['status']
        ref_data = await check_database_user(ref_user_id)
        has_purchased = ref_status == 'completed' or (ref_data and len(ref_data) > 5 and not bool(ref_data[5]))
        if has_purchased:
            active_referrals += 1
            total_bonuses += 5

    bot_username = (await query.bot.get_me()).username
    text_parts = [
        "📊 Твоя статистика:\n\n"
    ]
    if gen_stats:
        text_parts.append("Генерации:\n")
        type_names = {
            'with_avatar': 'Фото с аватаром',
            'photo_to_photo': 'Фото по референсу',
            'ai_video_v2_1': 'AI-видео (Kling 2.1)',
            'train_flux': 'Обучение аватаров',
            'prompt_assist': 'Помощь с промптами'
        }
        for gen_type, count in gen_stats.items():
            type_name = type_names.get(gen_type, gen_type)
            text_parts.append(f"  • {type_name}: {count}\n")
    else:
        text_parts.append("_Ты еще ничего не генерировал_\n")
    text_parts.extend([
        "\n",
        f"💵 Всего потрачено: {total_spent:.2f} RUB\n",
        f"💳 Всего покупок: {len(payments)}\n",
        f"👥 Рефералов (с покупкой): {active_referrals}\n",
        f"🎁 Бонусных печенек за рефералов: {total_bonuses}\n\n",
        f"🔗 Твоя реферальная ссылка:\n",
        f"`t.me/{bot_username.lstrip('@')}?start=ref_{user_id}`"
    ])

    # Разбиваем текст на части, если он слишком длинный
    MAX_MESSAGE_LENGTH = 4000
    messages = []
    current_message = []
    current_length = 0

    for part in text_parts:
        part_length = len(part) + 1
        if current_length + part_length < MAX_MESSAGE_LENGTH:
            current_message.append(part)
            current_length += part_length
        else:
            messages.append(''.join(current_message))
            current_message = [part]
            current_length = part_length
    if current_message:
        messages.append(''.join(current_message))

    logger.debug(f"handle_user_stats_callback: сформировано {len(messages)} сообщений для user_id={user_id}")

    try:
        for i, message_text in enumerate(messages):
            text = escape_message_parts(message_text, version=2)
            reply_markup = await create_referral_keyboard(user_id, bot_username) if i == len(messages) - 1 else None
            await query.message.answer(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"handle_user_stats_callback: отправка части {i+1}/{len(messages)}, длина={len(text)}")
        logger.info(f"Статистика отправлена для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки статистики для user_id={user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            "❌ Ошибка получения статистики.",
            " Попробуйте позже.",
            version=2
        )
        logger.debug(f"handle_user_stats_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_subscribe_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показ актуального тарифа или всех тарифов после 5 дней, разбивая длинные сообщения на части."""
    logger.debug(f"handle_subscribe_callback вызван для user_id={user_id}")
    await delete_all_videos(state, user_id, query.bot)
    await state.clear()
    subscription_data = await check_database_user(user_id)
    if not subscription_data or len(subscription_data) < 11:
        logger.error(f"Неполные данные подписки для user_id={user_id}: {subscription_data}")
        text = escape_message_parts(
            "❌ Ошибка получения данных.",
            " Попробуйте позже.",
            version=2
        )
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    first_purchase = bool(subscription_data[5])
    payments = await get_user_payments(user_id)
    is_paying_user = bool(payments)
    logger.info(f"handle_subscribe_callback: user_id={user_id}, payment_count={len(payments) if payments else 0}, first_purchase={first_purchase}, is_paying_user={is_paying_user}")

    # Проверка времени действия скидки и статуса напоминаний
    moscow_tz = pytz.timezone('Europe/Moscow')
    registration_date = datetime.now(moscow_tz)
    time_since_registration = float('inf')
    days_since_registration = 0
    last_reminder_type = subscription_data[9] if subscription_data and len(subscription_data) > 9 else None
    if subscription_data and len(subscription_data) > 10 and subscription_data[10]:
        try:
            registration_date = moscow_tz.localize(datetime.strptime(subscription_data[10], '%Y-%m-%d %H:%M:%S'))
            time_since_registration = (datetime.now(moscow_tz) - registration_date).total_seconds()
            days_since_registration = (datetime.now(moscow_tz).date() - registration_date.date()).days
            logger.debug(f"Calculated time_since_registration={time_since_registration}, days_since_registration={days_since_registration} for user_id={user_id}")
        except ValueError as e:
            logger.error(f"Невалидная дата регистрации для user_id={user_id}: {subscription_data[10]}. Ошибка: {e}")

    # Проверяем, является ли пользователь старым
    is_old_user_flag = await is_old_user(user_id, cutoff_date="2025-07-11")
    logger.debug(f"Пользователь user_id={user_id} is_old_user={is_old_user_flag}")

    # Формируем текст тарифов
    text_parts = [
        "🔥 Горячий выбор для идеальных фото!\n\n",
        "Хочешь крутые кадры без лишних хлопот? Выбери свой пакет и начинай творить! 🚀\n\n",
    ]
    keyboard = []
    available_tariffs = {k: v for k, v in TARIFFS.items() if k != "admin_premium"}

    if is_paying_user or is_old_user_flag or (days_since_registration >= 5 and last_reminder_type == "reminder_day5"):
        text_parts.append("💎 НАШИ ПАКЕТЫ:\n")
        for tariff_key, tariff in available_tariffs.items():
            text_parts.append(f"{tariff['display']}\n")
            keyboard.append([InlineKeyboardButton(text=tariff["display"], callback_data=tariff["callback"])])
        logger.debug(f"Показаны все тарифы для user_id={user_id} (is_paying_user={is_paying_user}, is_old_user={is_old_user_flag}, days_since_registration={days_since_registration}, last_reminder_type={last_reminder_type})")
    else:
        if days_since_registration == 0:
            if time_since_registration <= 1800:  # До 30 минут
                tariff_key = "комфорт"
                text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                text_parts.append("Комфорт: 70 печенек + 1 аватар за 1199₽\n")
            elif time_since_registration <= 5400:  # 30–90 минут
                tariff_key = "лайт"
                text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                text_parts.append("Лайт: 30 печенек за 599₽\n")
            else:  # После 90 минут
                tariff_key = "мини"
                text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
                text_parts.append("Мини: 10 печенек за 399₽\n")
        elif days_since_registration == 1:
            tariff_key = "лайт"
            text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
            text_parts.append("Лайт: 30 печенек за 599₽\n")
        elif 2 <= days_since_registration <= 4:
            tariff_key = "мини"
            text_parts.append("💎 ТВОЙ ПАКЕТ:\n")
            text_parts.append("Мини: 10 печенек за 399₽\n")
        keyboard.append([InlineKeyboardButton(text=available_tariffs[tariff_key]["display"], callback_data=available_tariffs[tariff_key]["callback"])])

    if first_purchase:
        text_parts.append("\n")
        text_parts.append("🎁 При первой покупке к любому купленному тарифу впервые 1 Аватар в подарок!\n")

    text_parts.append("\n")
    text_parts.append("Выбери свой пакет и начинай творить 🚀")

    # Разбиваем текст на части
    MAX_MESSAGE_LENGTH = 4000
    messages = []
    current_message = []
    current_length = 0

    for part in text_parts:
        part_length = len(part) + 1
        if current_length + part_length < MAX_MESSAGE_LENGTH:
            current_message.append(part)
            current_length += part_length
        else:
            messages.append(''.join(current_message))
            current_message = [part]
            current_length = part_length
    if current_message:
        messages.append(''.join(current_message))

    logger.debug(f"handle_subscribe_callback: сформировано {len(messages)} сообщений для user_id={user_id}")

    # Добавляем кнопки "В меню" и "Информация о тарифах"
    keyboard.append([InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")])
    keyboard.append([InlineKeyboardButton(text="ℹ️ Информация о тарифах", callback_data="tariff_info")])

    try:
        for i, message_text in enumerate(messages):
            text = escape_message_parts(message_text, version=2)
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if i == len(messages) - 1 else None
            await query.message.answer(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"handle_subscribe_callback: отправка части {i+1}/{len(messages)}, длина={len(text)}")
        logger.info(f"Меню тарифов успешно отправлено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки меню тарифов для user_id={user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            "❌ Ошибка при загрузке тарифов.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)
   

async def handle_payment_callback(query: CallbackQuery, state: FSMContext, user_id: int, callback_data: str) -> None:
    """Обработка выбора тарифа для оплаты."""
    logger.info(f"Начало handle_payment_callback для user_id={user_id}, callback_data={callback_data}")
    
    try:
        # Извлечение суммы из callback_data
        amount_str = callback_data.replace("pay_", "")
        try:
            amount = float(amount_str)
        except ValueError:
            logger.error(f"Некорректная сумма в callback_data: {amount_str}, user_id={user_id}")
            await safe_answer_callback(query, "❌ Неверный формат тарифа", show_alert=True)
            text = escape_message_parts(
                "❌ Неверный формат тарифа.",
                " Выберите тариф заново.",
                version=2
            )
            await query.message.answer(
                text,
                reply_markup=await create_subscription_keyboard(hide_mini_tariff=True),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.update_data(user_id=user_id)
            return
        
        # Поиск тарифа по сумме
        tariff_key = None
        for key, details in TARIFFS.items():
            if abs(float(details["amount"]) - amount) < 0.01:
                tariff_key = key
                break
        
        if not tariff_key:
            logger.error(f"Тариф с суммой {amount} не найден в TARIFFS для user_id={user_id}")
            await safe_answer_callback(query, "❌ Тариф не найден", show_alert=True)
            text = escape_message_parts(
                "❌ Выбранный тариф не найден.",
                " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
                version=2
            )
            await query.message.answer(
                text,
                reply_markup=await create_subscription_keyboard(hide_mini_tariff=True),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.update_data(user_id=user_id)
            return
        
        # Проверка актуальности тарифа
        moscow_tz = pytz.timezone('Europe/Moscow')
        subscription_data = await check_database_user(user_id)
        registration_date = datetime.now(moscow_tz)
        days_since_registration = 0
        time_since_registration = float('inf')
        if subscription_data and len(subscription_data) > 10 and subscription_data[10]:
            try:
                registration_date = moscow_tz.localize(datetime.strptime(subscription_data[10], '%Y-%m-%d %H:%M:%S'))
                days_since_registration = (datetime.now(moscow_tz).date() - registration_date.date()).days
                time_since_registration = (datetime.now(moscow_tz) - registration_date).total_seconds()
            except ValueError as e:
                logger.warning(f"Невалидный формат даты в subscription_data[10] для user_id={user_id}: {subscription_data[10]}. Ошибка: {e}")

        payments = await get_user_payments(user_id)
        is_paying_user = len(payments) > 0
        last_reminder_type = subscription_data[9] if subscription_data and len(subscription_data) > 9 else None

        # Проверка актуальности тарифа для неоплативших пользователей
        if not is_paying_user:
            five_days_seconds = 5 * 24 * 3600
            show_all_tariffs = last_reminder_type == "reminder_day5" and time_since_registration >= five_days_seconds
            if not show_all_tariffs:
                expected_tariff = None
                if days_since_registration == 0:
                    if time_since_registration <= 1800:  # До 30 минут
                        expected_tariff = "комфорт"
                    elif time_since_registration <= 5400:  # 30–90 минут
                        expected_tariff = "лайт"
                    else:  # После 90 минут
                        expected_tariff = "мини"
                elif days_since_registration == 1:
                    expected_tariff = "лайт"
                elif days_since_registration <= 4:
                    expected_tariff = "мини"
                
                if expected_tariff and tariff_key != expected_tariff:
                    logger.warning(f"Тариф {tariff_key} неактуален для user_id={user_id} на день {days_since_registration}, ожидается {expected_tariff}")
                    await safe_answer_callback(query, "❌ Этот тариф больше недоступен", show_alert=True)
                    new_message_type = f"tariff_{expected_tariff}" if days_since_registration <= 4 else "subscribe"
                    await send_onboarding_message(
                        bot=query.bot,
                        user_id=user_id,
                        message_type=new_message_type,
                        subscription_data=subscription_data,
                        first_purchase=bool(subscription_data[5]) if subscription_data else True
                    )
                    await state.update_data(user_id=user_id)
                    return

        tariff = TARIFFS[tariff_key]
        amount = tariff["amount"]
        description = tariff["display"]
        logger.debug(f"Найден тариф: key={tariff_key}, amount={amount}, description={description}")
        
        # Сохранение данных платежа
        await state.clear()
        await state.update_data(
            payment_amount=amount,
            payment_description=description,
            payment_tariff_key=tariff_key,
            user_id=user_id
        )
        logger.debug(f"Сохранены данные платежа для user_id={user_id}: amount={amount}, description={description}")
        
        # Проверка email
        email = subscription_data[6] if subscription_data and len(subscription_data) > 6 and subscription_data[6] else None
        logger.debug(f"Email для user_id={user_id}: {email}")
        
        if email:
            await state.update_data(email=email)
            from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY
            if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
                logger.error(f"YooKassa не настроена для user_id={user_id}")
                text = escape_message_parts(
                    "❌ Платежная система временно недоступна.",
                    " Обратитесь в поддержку: @AXIDI_Help",
                    version=2
                )
                await query.message.answer(
                    text,
                    reply_markup=await create_subscription_keyboard(hide_mini_tariff=True),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.update_data(user_id=user_id)
                return
            
            try:
                bot_username = (await query.bot.get_me()).username
                payment_url = await create_payment_link(user_id, email, amount, description, bot_username)
                is_first_purchase = bool(subscription_data[5]) if len(subscription_data) > 5 else True
                bonus_text = " (+ 1 аватар в подарок!)" if is_first_purchase and tariff.get("photos", 0) > 0 else ""
                
                # Формирование текста с корректным экранированием
                text = escape_message_parts(
                    "💳 Оплата пакета\n",
                    f"✨ Вы выбрали: {description}{bonus_text}\n",
                    f"💰 Сумма: {amount:.2f} RUB\n\n",
                    f"🔗 [Нажмите здесь для безопасной оплаты через YooKassa]({payment_url})\n\n",
                    "_После успешной оплаты ресурсы будут начислены автоматически_ ",
                    version=2
                )
                
                logger.info(f"Платежная ссылка создана для user_id={user_id}: {payment_url}")
                await query.message.answer(
                    text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 Назад к пакетам", callback_data="subscribe")]
                    ]),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e:
                logger.error(f"Ошибка создания платежной ссылки для user_id={user_id}: {e}", exc_info=True)
                text = escape_message_parts(
                    "❌ Не удалось создать платежную ссылку.",
                    " Попробуйте позже или обратитесь в поддержку: @AXIDI_Help",
                    version=2
                )
                await query.message.answer(
                    text,
                    reply_markup=await create_subscription_keyboard(hide_mini_tariff=True),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        else:
            await state.update_data(awaiting_email=True)
            logger.info(f"Запрошен email для user_id={user_id}, tariff={description}")
            text = escape_message_parts(
                f"📧 Для оформления покупки \"{description}\" ({amount:.2f} RUB) введите ваш email:",
                version=2
            )
            await query.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад к пакетам", callback_data="subscribe")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Критическая ошибка в handle_payment_callback для user_id={user_id}, callback_data={callback_data}: {e}", exc_info=True)
        await safe_answer_callback(query, "❌ Ошибка обработки тарифа", show_alert=True)
        text = escape_message_parts(
            "❌ Произошла ошибка при выборе тарифа.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        await query.message.answer(
            text,
            reply_markup=await create_subscription_keyboard(hide_mini_tariff=True),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_my_avatars_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показ аватаров пользователя."""
    logger.debug(f"handle_my_avatars_callback вызван для user_id={user_id}")
    await delete_all_videos(state, user_id, query.bot)
    await state.clear()
    await reset_generation_context(state, "my_avatars", user_id=user_id)
    text = escape_message_parts(
        "👥 Мои аватары\n\n",
        "Здесь ты можешь выбрать активный аватар или создать новый.",
        version=2
    )
    logger.debug(f"handle_my_avatars_callback: сформирован текст: {text[:200]}...")
    await query.message.answer(
        text,
        reply_markup=await create_avatar_selection_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

async def handle_select_avatar_callback(query: CallbackQuery, state: FSMContext, user_id: int, callback_data: str) -> None:
    """Выбор активного аватара."""
    logger.debug(f"handle_select_avatar_callback вызван для user_id={user_id}, callback_data={callback_data}")
    try:
        avatar_id = int(callback_data.split('_')[2])
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            await c.execute("SELECT avatar_id FROM user_trainedmodels WHERE avatar_id = ? AND user_id = ?", (avatar_id, user_id))
            if not await c.fetchone():
                logger.error(f"Аватар avatar_id={avatar_id} не найден для user_id={user_id}")
                await safe_answer_callback(query, "❌ Аватар не найден", show_alert=True)
                await state.clear()
                text = escape_message_parts(
                    "❌ Аватар не найден.",
                    " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
                    version=2
                )
                logger.debug(f"handle_select_avatar_callback: сформирован текст: {text[:200]}...")
                await query.message.answer(
                    text,
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.update_data(user_id=user_id)
                return
        success = await update_user_credits(user_id, action="set_active_avatar", amount=avatar_id)
        if not success:
            logger.error(f"Не удалось установить активный аватар avatar_id={avatar_id} для user_id={user_id}")
            await safe_answer_callback(query, "❌ Не удалось выбрать аватар", show_alert=True)
            await state.clear()
            text = escape_message_parts(
                "❌ Не удалось выбрать аватар.",
                " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
                version=2
            )
            logger.debug(f"handle_select_avatar_callback: сформирован текст: {text[:200]}...")
            await query.message.answer(
                text,
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.update_data(user_id=user_id)
            return
        await state.clear()
        await safe_answer_callback(query, "✅ Аватар активирован!", show_alert=True)
        await handle_my_avatars_callback(query, state, user_id)
        logger.info(f"Аватар avatar_id={avatar_id} успешно активирован для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при выборе аватара для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await safe_answer_callback(query, "❌ Не удалось выбрать аватар", show_alert=True)
        text = escape_message_parts(
            "❌ Произошла ошибка при выборе аватара.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        logger.debug(f"handle_select_avatar_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_train_flux_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Начало обучения нового аватара, разбивая длинные сообщения на части, если они превышают лимит Telegram."""
    logger.debug(f"handle_train_flux_callback вызван для user_id={user_id}")
    if not await check_resources(query.bot, user_id, required_avatars=1):
        await state.update_data(user_id=user_id)
        return
    await state.clear()
    await reset_generation_context(state, "train_flux", user_id=user_id)
    await state.update_data(training_step='upload_photos', training_photos=[], user_id=user_id)
    text_parts = [
        "🎨 СОЗДАНИЕ ВАШЕГО АВАТАРА\n\n",
        "Для создания высококачественного аватара мне нужно минимум 10 твоих фотографий (оптимально 15-20) с АКЦЕНТОМ на лицо. ",
        "Каждая фотография должна быть четкой и профессиональной, чтобы PixelPie точно воспроизвел ваши черты.\n\n",
        "📸 РЕКОМЕНДАЦИИ ДЛЯ ИДЕАЛЬНОГО РЕЗУЛЬТАТА:\n",
        "- ФОТОГРАФИИ ДОЛЖНЫ БЫТЬ ПРЯМЫМИ, ЧЕТКИМИ, БЕЗ ИСКАЖЕНИЙ И РАЗМЫТИЯ. Используй камеру с высоким разрешением.\n",
        "- Снимай в правильных ракурсах: Лицо должно быть полностью видно, без обрезки.\n",
        "- Используй разнообразное освещение: дневной свет, золотой час, мягкий студийный свет. ИЗБЕГАЙ ТЕМНЫХ ТЕНЕЙ И ПЕРЕСВЕТОВ.\n",
        "- Фон должен быть czystым, без лишних объектов (мебель, растения, животные). НЕ ДОПУСКАЮТСЯ ЗЕРКАЛА И ОТРАЖЕНИЯ.\n",
        "- Снимай только себя. ГРУППОВЫЕ ФОТО ИЛИ ФОТО С ДРУГИМИ ЛЮДЬМИ НЕ ПОДХОДЯТ.\n",
        "- НЕ ИСПОЛЬЗУЙ ОЧКИ, ШЛЯПЫ, МАСКИ ИЛИ ДРУГИЕ АКСЕССУАРЫ, закрывающие лицо. Макияж должен быть минимальным.\n",
        "- Выражение лица: нейтральное или легкая улыбка. ИЗБЕГАЙ КРИВЛЯНИЙ ИЛИ ЭКСТРЕМАЛЬНЫХ ЭМОЦИЙ.\n",
        "- Чем больше разнообразных фотографий (ракурсы, освещение, фон), тем точнее будет аватар.\n\n",
        "⚠️ ВАЖНО: Каждая фотография должна быть хорошего качества, без фильтров, цифрового шума или артефактов. ",
        "Фотографии с низким разрешением, искажениями или посторонними объектами будут влиять на КАЧЕСТВО АВАТАРА.\n\n",
        "📤 Начинай загружать фотографии! Я проверю и сообщу, когда будет достаточно."
    ]

    MAX_MESSAGE_LENGTH = 4000
    messages = []
    current_message = []
    current_length = 0

    for part in text_parts:
        part_length = len(part) + 1
        if current_length + part_length < MAX_MESSAGE_LENGTH:
            current_message.append(part)
            current_length += part_length
        else:
            messages.append(''.join(current_message))
            current_message = [part]
            current_length = part_length
    if current_message:
        messages.append(''.join(current_message))

    logger.debug(f"handle_train_flux_callback: сформировано {len(messages)} сообщений для user_id={user_id}")

    try:
        for i, message_text in enumerate(messages):
            text = escape_message_parts(message_text, version=2)
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="user_profile")]
            ]) if i == len(messages) - 1 else None
            await query.message.answer(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"handle_train_flux_callback: отправка части {i+1}/{len(messages)}, длина={len(text)}")
        logger.info(f"Сообщение о создании аватара отправлено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения о создании аватара для user_id={user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            "❌ Произошла ошибка при начале обучения аватара.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        logger.debug(f"handle_train_flux_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_continue_upload_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Продолжение загрузки фото."""
    logger.debug(f"handle_continue_upload_callback вызван для user_id={user_id}")
    try:
        await state.clear()
        await state.update_data(training_step='upload_photos', user_id=user_id)
        user_data = await state.get_data()
        training_photos = user_data.get('training_photos', [])
        photo_count = len(training_photos)
        logger.debug(f"Количество загруженных фото для user_id={user_id}: {photo_count}, training_photos={training_photos}")
        text = escape_message_parts(
            f"📸 Загружено {photo_count} фото.",
            " Продолжай загружать или нажми \"Начать обучение\".",
            version=2
        )
        logger.debug(f"handle_continue_upload_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_training_keyboard(user_id, photo_count),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Сообщение о продолжении загрузки отправлено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка в handle_continue_upload_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.answer("❌ Произошла ошибка", show_alert=True)
        text = escape_message_parts(
            "❌ Произошла ошибка при загрузке фотографий.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        logger.debug(f"handle_continue_upload_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_confirm_start_training_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Запуск обучения аватара."""
    logger.debug(f"handle_confirm_start_training_callback вызван для user_id={user_id}, user_data={await state.get_data()}")
    try:
        user_data = await state.get_data()
        avatar_name = user_data.get('avatar_name')
        training_photos = user_data.get('training_photos', [])
        photo_count = len(training_photos)

        if not avatar_name:
            logger.warning(f"Отсутствует avatar_name для user_id={user_id} в handle_confirm_start_training_callback")
            await state.set_state(TrainingStates.AWAITING_AVATAR_NAME)
            await state.update_data(training_step='enter_avatar_name', training_photos=training_photos, user_id=user_id)
            text = escape_message_parts(
                f"🏷 Придумай имя для своего аватара (например: \"Мой стиль\", \"Бизнес-образ\").",
                f"📸 У тебя загружено {photo_count} фото.",
                version=2
            )
            logger.debug(f"handle_confirm_start_training_callback: сформирован текст: {text[:200]}...")
            await query.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 К загрузке фото", callback_data="continue_upload")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Пользователь user_id={user_id} перенаправлен на ввод имени аватара")
            await state.update_data(user_id=user_id)
            return

        if photo_count < 10:
            logger.warning(f"Недостаточно фото для user_id={user_id}: {photo_count}")
            text = escape_message_parts(
                f"❌ Недостаточно фото для обучения.",
                f" Загружено {photo_count}, требуется минимум 10.",
                version=2
            )
            logger.debug(f"handle_confirm_start_training_callback: сформирован текст: {text[:200]}...")
            await query.message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 К загрузке фото", callback_data="continue_upload")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(TrainingStates.AWAITING_PHOTOS)
            await state.update_data(user_id=user_id)
            return

        await state.update_data(user_id=user_id)
        await start_training(query.message, state)
        await state.clear()
        logger.info(f"Обучение успешно запущено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка запуска обучения для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка при запуске обучения.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        logger.debug(f"handle_confirm_start_training_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_back_to_avatar_name_input_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Возврат к вводу имени аватара."""
    logger.debug(f"handle_back_to_avatar_name_input_callback вызван для user_id={user_id}")
    await state.clear()
    await state.update_data(training_step='enter_avatar_name', user_id=user_id)
    user_data = await state.get_data()
    photo_count = len(user_data.get('training_photos', []))
    text = escape_message_parts(
        f"🏷 Придумай имя для своего аватара (например: \"Мой стиль\", \"Бизнес-образ\").",
        f"📸 У тебя загружено {photo_count} фото.",
        version=2
    )
    logger.debug(f"handle_back_to_avatar_name_input_callback: сформирован текст: {text[:200]}...")
    await query.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад к загрузке фото", callback_data="continue_upload")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

async def handle_use_suggested_trigger_callback(query: CallbackQuery, state: FSMContext, user_id: int, callback_data: str) -> None:
    """Использование предложенного триггер-слова."""
    logger.debug(f"handle_use_suggested_trigger_callback вызван для user_id={user_id}, callback_data={callback_data}")
    trigger_word = callback_data.replace("use_suggested_trigger_", "")
    await state.clear()
    await state.update_data(trigger_word=trigger_word, training_step='confirm_training', user_id=user_id)
    from handlers.messages import handle_trigger_word_input
    await handle_trigger_word_input(query.message, state, trigger_word)
    await state.update_data(user_id=user_id)

async def handle_confirm_photo_quality_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Подтверждение качества фото перед обучением."""
    logger.debug(f"handle_confirm_photo_quality_callback вызван для user_id={user_id}, user_data={await state.get_data()}")
    user_data = await state.get_data()
    avatar_name = user_data.get('avatar_name', 'Без имени')
    training_photos = user_data.get('training_photos', [])
    photo_count = len(training_photos)

    if photo_count < 10:
        logger.warning(f"Недостаточно фото для user_id={user_id}: {photo_count}")
        text = escape_message_parts(
            f"❌ Недостаточно фото для обучения.",
            f" Загружено {photo_count}, требуется минимум 10.",
            version=2
        )
        logger.debug(f"handle_confirm_photo_quality_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К загрузке фото", callback_data="continue_upload")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    if not avatar_name or avatar_name == 'Без имени':
        logger.warning(f"Отсутствует имя аватара для user_id={user_id}")
        await state.update_data(training_step='enter_avatar_name')
        await state.set_state(TrainingStates.AWAITING_AVATAR_NAME)
        text = escape_message_parts(
            "❌ Ошибка: имя аватара не задано.",
            " Введите имя для аватара.",
            version=2
        )
        logger.debug(f"handle_confirm_photo_quality_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К загрузке фото", callback_data="continue_upload")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    if not await check_user_resources(query.bot, user_id, required_avatars=1):
        await state.clear()
        await state.update_data(user_id=user_id)
        return

    text_parts = [
        "👍 Отлично! Давай проверим финальные данные:\n\n",
        f"👤 Имя аватара: {avatar_name}\n",
        f"📸 Загружено фото: {photo_count} шт.\n\n",
        "🚀 Все готово для запуска обучения!\n",
        "⏱ Это займет около 3-5 минут.\n",
        "💎 Будет списан 1 аватар с твоего баланса.\n\n",
        "Начинаем?"
    ]
    text = escape_message_parts(*text_parts, version=2)
    logger.debug(f"handle_confirm_photo_quality_callback: сформирован текст: {text[:200]}...")

    try:
        await asyncio.sleep(0.1)
        await query.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Начать обучение!", callback_data="confirm_start_training")],
                [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="train_flux")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Сообщение подтверждения успешно отправлено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения для user_id={user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            "❌ Ошибка при подтверждении данных.",
            " Попробуйте снова.",
            version=2
        )
        logger.debug(f"handle_confirm_photo_quality_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_repeat_last_generation_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Повтор последней генерации."""
    logger.info(f"handle_repeat_last_generation_callback вызван для user_id={user_id}")
    try:
        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        last_gen_data = user_data.get(f'last_admin_generation_{target_user_id}' if is_admin_generation else 'last_generation_params', {})

        if not last_gen_data:
            logger.error(f"Нет данных последней генерации для user_id={user_id}, target_user_id={target_user_id}")
            await safe_answer_callback(query, "❌ Нет данных о последней генерации", show_alert=True)
            text = escape_message_parts(
                "❌ Нет данных о последней генерации.",
                " Начните новую генерацию.",
                version=2
            )
            logger.debug(f"handle_repeat_last_generation_callback: сформирован текст: {text[:200]}...")
            await query.message.answer(
                text,
                reply_markup=await create_photo_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.update_data(user_id=user_id)
            return

        required_fields = ['prompt', 'aspect_ratio', 'generation_type', 'model_key']
        missing_fields = [f for f in required_fields if f not in last_gen_data or not last_gen_data[f]]
        if missing_fields:
            logger.error(f"Отсутствуют обязательные поля для повторной генерации: {missing_fields}, user_id={user_id}")
            await safe_answer_callback(query, "❌ Неполные данные последней генерации", show_alert=True)
            text = escape_message_parts(
                f"❌ Неполные данные для повторной генерации: {', '.join(missing_fields)}.",
                version=2
            )
            logger.debug(f"handle_repeat_last_generation_callback: сформирован текст: {text[:200]}...")
            await query.message.answer(
                text,
                reply_markup=await create_photo_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.update_data(user_id=user_id)
            return

        preserved_data = {
            'prompt': last_gen_data.get('prompt'),
            'aspect_ratio': last_gen_data.get('aspect_ratio'),
            'generation_type': last_gen_data.get('generation_type'),
            'model_key': last_gen_data.get('model_key'),
            'selected_gender': last_gen_data.get('selected_gender'),
            'user_input_for_llama': last_gen_data.get('user_input_for_llama'),
            'style_name': last_gen_data.get('style_name', 'custom'),
            'current_style_set': last_gen_data.get('current_style_set'),
            'came_from_custom_prompt': last_gen_data.get('came_from_custom_prompt', False),
            'use_llama_prompt': last_gen_data.get('use_llama_prompt', False),
            'last_generation_params': last_gen_data,
            'is_admin_generation': is_admin_generation,
            'admin_generation_for_user': target_user_id,
            'message_recipient': user_id,
            'generation_target_user': target_user_id,
            'original_admin_user': user_id if is_admin_generation else None,
            'user_id': user_id
        }
        if is_admin_generation:
            preserved_data[f'last_admin_generation_{target_user_id}'] = last_gen_data

        await state.clear()
        await state.update_data(**preserved_data)
        logger.debug(f"Восстановлены данные для повторной генерации: {preserved_data}")

        if not is_admin_generation:
            required_photos = 30 if last_gen_data['generation_type'] == 'ai_video_v2_1' else 2
            if not await check_resources(query.bot, user_id, required_photos=required_photos):
                logger.info(f"Недостаточно ресурсов для повторной генерации user_id={user_id}")
                await state.update_data(user_id=user_id)
                return

        if last_gen_data['generation_type'] in ['with_avatar', 'photo_to_photo']:
            active_model_data = await get_active_trainedmodel(target_user_id)
            if not active_model_data or active_model_data[3] != 'success':
                logger.error(f"Нет активного аватара для target_user_id={target_user_id}")
                await safe_answer_callback(query, "❌ Нет активного аватара", show_alert=True)
                text = escape_message_parts(
                    f"❌ У пользователя ID `{target_user_id}` нет активного аватара.",
                    version=2
                )
                logger.debug(f"handle_repeat_last_generation_callback: сформирован текст: {text[:200]}...")
                await query.message.answer(
                    text,
                    reply_markup=await create_user_profile_keyboard(user_id, query.bot),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.update_data(user_id=user_id)
                return

        logger.info(f"Повторная генерация для user_id={user_id}, target_user_id={target_user_id}, generation_type={last_gen_data['generation_type']}")
        text = escape_message_parts(
            "⏳ Повторяю последнюю генерацию...",
            version=2
        )
        logger.debug(f"handle_repeat_last_generation_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        if last_gen_data['generation_type'] in ['with_avatar', 'photo_to_photo']:
            await generate_image(query.message, state, num_outputs=2, user_id=user_id)
        elif last_gen_data['generation_type'] == 'ai_video_v2_1':
            await handle_generate_video_callback(query, state)
        else:
            logger.error(f"Неподдерживаемый тип генерации для повторения: {last_gen_data['generation_type']}")
            text = escape_message_parts(
                "❌ Неподдерживаемый тип генерации.",
                " Начните новую генерацию.",
                version=2
            )
            logger.debug(f"handle_repeat_last_generation_callback: сформирован текст: {text[:200]}...")
            await query.message.answer(
                text,
                reply_markup=await create_photo_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.update_data(user_id=user_id)
            return

        await query.answer("✅ Запущена повторная генерация")
        logger.info(f"Повторная генерация успешно запущена для user_id={user_id}, target_user_id={target_user_id}")
    except Exception as e:
        logger.error(f"Ошибка в handle_repeat_last_generation_callback для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await safe_answer_callback(query, "❌ Ошибка при повторной генерации", show_alert=True)
        text = escape_message_parts(
            "❌ Произошла ошибка при повторной генерации.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        logger.debug(f"handle_repeat_last_generation_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_photo_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def handle_change_email_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Инициирует изменение email."""
    logger.debug(f"handle_change_email_callback вызван для user_id={user_id}")
    await state.clear()
    await state.update_data(awaiting_email_change=True, user_id=user_id)
    text = escape_message_parts(
        "📧 Введите новый email для вашего профиля:",
        version=2
    )
    logger.debug(f"handle_change_email_callback: сформирован текст: {text[:200]}...")
    await query.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В личный кабинет", callback_data="user_profile")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

async def handle_confirm_change_email_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Подтверждает изменение email."""
    logger.warning(f"handle_confirm_change_email_callback вызван для user_id={user_id}, функция не реализована")
    await state.clear()
    await safe_answer_callback(query, "❌ Функция изменения email пока недоступна", show_alert=True)
    text = escape_message_parts(
        "❌ Функция изменения email пока недоступна.",
        " Обратитесь в поддержку: @AXIDI_Help",
        version=2
    )
    logger.debug(f"handle_confirm_change_email_callback: сформирован текст: {text[:200]}...")
    await query.message.answer(
        text,
        reply_markup=await create_main_menu_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

async def handle_skip_mask_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Пропуск маски для генерации."""
    logger.warning(f"handle_skip_mask_callback вызван для user_id={user_id}, функция не реализована")
    await state.clear()
    await safe_answer_callback(query, "❌ Функция пропуска маски пока недоступна", show_alert=True)
    text = escape_message_parts(
        "❌ Функция пропуска маски пока недоступна.",
        " Обратитесь в поддержку: @AXIDI_Help",
        version=2
    )
    logger.debug(f"handle_skip_mask_callback: сформирован текст: {text[:200]}...")
    await query.message.answer(
        text,
        reply_markup=await create_main_menu_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

async def ask_for_aspect_ratio_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Запрос соотношения сторон."""
    user_id = query.from_user.id
    logger.debug(f"ask_for_aspect_ratio_callback вызван для user_id={user_id}")
    try:
        user_data = await state.get_data()
        came_from_custom = user_data.get('came_from_custom_prompt', False)
        back_callback = "enter_custom_prompt_manual" if came_from_custom else "back_to_style_selection"
        text = escape_message_parts(
            "📐 Выбери соотношение сторон для изображения:",
            version=2
        )
        logger.debug(f"ask_for_aspect_ratio_callback: сформирован текст: {text[:200]}...")
        await query.answer()
        await query.message.answer(
            text,
            reply_markup=await create_aspect_ratio_keyboard(back_callback),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Клавиатура соотношений сторон отправлена для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка в ask_for_aspect_ratio_callback для user_id={user_id}: {e}", exc_info=True)
        await query.answer("❌ Ошибка при запросе соотношения сторон", show_alert=True)
        await state.clear()
        text = escape_message_parts(
            "❌ Произошла ошибка.",
            " Попробуйте снова или обратитесь в поддержку: @AXIDI_Help",
            version=2
        )
        logger.debug(f"ask_for_aspect_ratio_callback: сформирован текст: {text[:200]}...")
        await query.message.answer(
            text,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)

async def cancel(message: Message, state: FSMContext) -> None:
    """Отменяет все активные действия и сбрасывает контекст."""
    user_id = message.from_user.id
    logger.debug(f"cancel вызван для user_id={user_id}")
    await state.clear()
    text = escape_message_parts(
        "✅ Все действия отменены.",
        version=2
    )
    logger.debug(f"cancel: сформирован текст: {text[:200]}...")
    reply_markup = await create_main_menu_keyboard(user_id)
    await message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

# Регистрация обработчиков
@user_callbacks_router.callback_query(
    lambda c: c.data in [
        "photo_generate_menu", "video_generate_menu", "generate_menu", "photo_to_photo", "ai_video_v2_1",
        "repeat_last_generation", "select_generic_avatar_styles", "select_new_male_avatar_styles",
        "select_new_female_avatar_styles", "page_info", "enter_custom_prompt_manual",
        "enter_custom_prompt_llama", "confirm_assisted_prompt", "edit_assisted_prompt",
        "skip_prompt", "aspect_ratio_info", "back_to_aspect_selection", "back_to_style_selection",
        "confirm_generation", "confirm_photo_quality", "skip_mask", "user_profile",
        "check_subscription", "user_stats", "subscribe", "change_email", "confirm_change_email",
        "my_avatars", "train_flux", "continue_upload", "start_training", "confirm_start_training",
        "back_to_avatar_name_input", "check_training", "tariff_info", "back_to_menu"
    ] or c.data.startswith(("style_", "video_style_", "male_styles_page_", "female_styles_page_", "aspect_", "confirm_video_generation", "rate_", "select_avatar_", "use_suggested_trigger_", "pay_"))
)
async def user_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    logger.debug(f"Callback_query получен: id={query.id}, data={query.data}")
    await handle_user_callback(query, state)