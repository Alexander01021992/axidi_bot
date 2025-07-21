# handlers/broadcast.py

import asyncio
import json
import logging
import pytz
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from database import get_all_users_stats, get_broadcasts_with_buttons, get_broadcast_buttons, get_paid_users, get_non_paid_users, save_broadcast_button
from config import ADMIN_IDS, DATABASE_PATH, ALLOWED_BROADCAST_CALLBACKS, BROADCAST_CALLBACK_ALIASES
from keyboards import create_admin_keyboard, create_dynamic_broadcast_keyboard, create_admin_user_actions_keyboard, create_broadcast_with_payment_audience_keyboard
from handlers.utils import escape_message_parts, send_message_with_fallback, unescape_markdown
import aiosqlite
from states import BotStates

logger = logging.getLogger(__name__)

# Создание роутера для рассылок
broadcast_router = Router()

async def clear_user_data(state: FSMContext, user_id: int):
    """Очищает данные состояния FSM для пользователя, если не активно ожидание сообщения рассылки."""
    current_state = await state.get_state()
    if current_state == BotStates.AWAITING_BROADCAST_MESSAGE:
        logger.debug(f"Пропуск очистки состояния для user_id={user_id}, так как активно состояние {current_state}")
        return
    await state.clear()
    logger.info(f"Очистка данных для user_id={user_id} по таймеру")

async def initiate_broadcast(query: CallbackQuery, state: FSMContext) -> None:
    """Инициирует рассылку (общую, для оплативших, не оплативших или с кнопкой оплаты)."""
    user_id = query.from_user.id
    callback_data = query.data
    
    if user_id not in ADMIN_IDS:
        await query.message.answer(
            escape_message_parts("❌ У вас нет прав.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    current_state = await state.get_state()
    if current_state in [
        BotStates.AWAITING_BROADCAST_MESSAGE,
        BotStates.AWAITING_BROADCAST_MEDIA_CONFIRM,
        BotStates.AWAITING_BROADCAST_SCHEDULE,
        BotStates.AWAITING_BROADCAST_AUDIENCE,
        BotStates.AWAITING_BROADCAST_BUTTONS,
        BotStates.AWAITING_BROADCAST_BUTTON_INPUT
    ]:
        logger.debug(f"initiate_broadcast: состояние {current_state} уже активно, пропуск очистки для user_id={user_id}")
    else:
        await state.clear()

    if callback_data == "broadcast_with_payment":
        text = escape_message_parts(
            "📢 Выберите аудиторию для рассылки с кнопкой оплаты:",
            version=2
        )
        reply_markup = await create_broadcast_with_payment_audience_keyboard()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(awaiting_broadcast_audience=True, user_id=user_id)
        await state.set_state(BotStates.AWAITING_BROADCAST_AUDIENCE)
    else:
        broadcast_type = callback_data.replace("broadcast_", "") if callback_data.startswith("broadcast_") else callback_data
        await state.update_data(
            awaiting_broadcast_message=True,
            broadcast_type=broadcast_type,
            user_id=user_id,
            buttons=[]  # Инициализируем пустой список кнопок
        )

        # Запускаем таймер очистки состояния через 20 минут
        async def delayed_clear_user_data():
            await asyncio.sleep(1200)  # 20 минут
            current_state_after_delay = await state.get_state()
            if current_state_after_delay in [
                BotStates.AWAITING_BROADCAST_MESSAGE,
                BotStates.AWAITING_BROADCAST_AUDIENCE,
                BotStates.AWAITING_BROADCAST_BUTTONS,
                BotStates.AWAITING_BROADCAST_BUTTON_INPUT
            ]:
                await clear_user_data(state, user_id)

        asyncio.create_task(delayed_clear_user_data())

        text = escape_message_parts(
            "📢 Введите текст сообщения для рассылки.\n",
            "⚠️ Сначала отправьте текст, затем медиа (фото/видео).\n",
            "Для отмены используйте /cancel.",
            version=2
        )
        reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]
        ])

        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_MESSAGE)
    
    await query.answer()
    logger.info(f"initiate_broadcast: user_id={user_id}, callback_data={callback_data}")

# Добавляем новый обработчик для выбора аудитории
@broadcast_router.callback_query(
    lambda c: c.data and c.data.startswith("broadcast_with_payment_")
)
async def handle_broadcast_audience_selection(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор аудитории для рассылки с кнопкой оплаты."""
    user_id = query.from_user.id
    callback_data = query.data

    if user_id not in ADMIN_IDS:
        await query.message.answer(
            escape_message_parts("❌ У вас нет прав.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    user_data = await state.get_data()
    current_state = await state.get_state()
    logger.debug(f"handle_broadcast_audience_selection: user_id={user_id}, callback_data={callback_data}, current_state={current_state}, user_data={user_data}")

    if not user_data.get('awaiting_broadcast_audience') or current_state != BotStates.AWAITING_BROADCAST_AUDIENCE:
        logger.warning(f"handle_broadcast_audience_selection invoked without awaiting_broadcast_audience or incorrect state for user_id={user_id}, state={current_state}")
        await state.clear()
        await query.message.answer(
            escape_message_parts("❌ Ошибка: выбор аудитории не ожидается. Начните заново.", version=2),
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    broadcast_type = callback_data.replace("broadcast_with_payment_", "")
    await state.update_data(
        awaiting_broadcast_audience=False,
        awaiting_broadcast_message=True,
        broadcast_type=f"with_payment_{broadcast_type}",
        user_id=user_id
    )

    # Запускаем таймер очистки состояния через 15 минут
    async def delayed_clear_user_data():
        await asyncio.sleep(900)  # 15 минут
        current_state_after_delay = await state.get_state()
        if current_state_after_delay in [BotStates.AWAITING_BROADCAST_MESSAGE, BotStates.AWAITING_BROADCAST_AUDIENCE]:
            await clear_user_data(state, user_id)
            logger.info(f"Состояние очищено для user_id={user_id} по таймеру")

    asyncio.create_task(delayed_clear_user_data())

    text = escape_message_parts(
        "📢 Введите текст сообщения для рассылки.\n",
        "⚠️ Сначала отправьте текст, затем медиа (фото/видео).\n",
        "Для отмены используйте /cancel.",
        version=2
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
    ])

    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(BotStates.AWAITING_BROADCAST_MESSAGE)
    logger.info(f"Состояние установлено в AWAITING_BROADCAST_MESSAGE для user_id={user_id}, broadcast_type={broadcast_type}")
    await query.answer()

@broadcast_router.message(
    StateFilter(BotStates.AWAITING_BROADCAST_MESSAGE),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)
async def handle_broadcast_message(message: Message, state: FSMContext) -> None:
    """Обрабатывает текст сообщения для рассылки."""
    user_id = message.from_user.id
    user_data = await state.get_data()
    current_state = await state.get_state()
    logger.debug(f"handle_broadcast_message: user_id={user_id}, current_state={current_state}, user_data={user_data}")

    if user_id not in ADMIN_IDS:
        await message.answer(
            escape_message_parts("❌ У вас нет прав.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if not user_data.get('awaiting_broadcast_message') or current_state != BotStates.AWAITING_BROADCAST_MESSAGE:
        logger.warning(f"handle_broadcast_message invoked without awaiting_broadcast_message or incorrect state for user_id={user_id}, state={current_state}")
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка: действие рассылки не ожидается.\n",
            "Попробуйте начать рассылку заново через админ-панель.",
            version=2
        )
        await message.answer(
            text, 
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    await state.update_data(awaiting_broadcast_message=False, user_id=user_id)
    message_text = message.text.strip() if message.text else ""

    if not message_text:
        logger.warning(f"Пустое сообщение рассылки от user_id={user_id}")
        text = escape_message_parts(
            "⚠️ Сообщение не может быть пустым. Введите текст сообщения.",
            version=2
        )
        keyboard = [[InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]]
        await state.update_data(awaiting_broadcast_message=True, user_id=user_id)
        await message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_MESSAGE)
        return

    await state.update_data(broadcast_message=message_text, awaiting_broadcast_media_confirm=True)

    text = escape_message_parts(
        "📸 Хотите прикрепить медиа к рассылке?\n",
        "Отправьте фото/видео или выберите 'Без медиа'.\n",
        "⚠️ Фото/видео можно отправить только сейчас.",
        version=2
    )
    keyboard = [
        [InlineKeyboardButton(text="Без медиа", callback_data="broadcast_no_media")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]
    ]
    await message.answer(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(BotStates.AWAITING_BROADCAST_MEDIA_CONFIRM)
    logger.info(f"Переход в состояние AWAITING_BROADCAST_MEDIA_CONFIRM для user_id={user_id}")

async def handle_broadcast_media(update: Message | CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает медиа для рассылки."""
    user_id = update.from_user.id if isinstance(update, Message) else update.from_user.id
    user_data = await state.get_data()
    if not user_data.get('awaiting_broadcast_media_confirm'):
        logger.warning(f"handle_broadcast_media invoked without awaiting_broadcast_media_confirm for user_id={user_id}")
        await state.clear()
        text = escape_message_parts("❌ Ошибка: действие рассылки не ожидается.", version=2)
        if isinstance(update, Message):
            await update.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        return

    await state.update_data(awaiting_broadcast_media_confirm=False, user_id=user_id)
    media = None
    media_type = None

    if isinstance(update, Message):
        if update.photo:
            media = update.photo[-1].file_id
            media_type = 'photo'
        elif update.video:
            media = update.video.file_id
            media_type = 'video'
    elif isinstance(update, CallbackQuery) and update.data == "broadcast_no_media":
        await update.answer()

    if media:
        await state.update_data(broadcast_media={'file_id': media, 'type': media_type})
    else:
        await state.update_data(broadcast_media=None)

    text = escape_message_parts(
        "🔘 Хотите добавить кнопки к рассылке?\n",
        "Введите количество кнопок (0-3) или выберите 'Без кнопок'.",
        version=2
    )
    keyboard = [
        [InlineKeyboardButton(text="0 (Без кнопок)", callback_data="broadcast_no_buttons")],
        [InlineKeyboardButton(text="1", callback_data="broadcast_buttons_1")],
        [InlineKeyboardButton(text="2", callback_data="broadcast_buttons_2")],
        [InlineKeyboardButton(text="3", callback_data="broadcast_buttons_3")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]
    ]
    if isinstance(update, Message):
        await update.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await update.message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.set_state(BotStates.AWAITING_BROADCAST_BUTTONS)
    logger.info(f"Переход в состояние AWAITING_BROADCAST_BUTTONS для user_id={user_id}")

async def handle_broadcast_buttons_count(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор количества кнопок для рассылки или копирование кнопок из другой рассылки."""
    user_id = query.from_user.id
    callback_data = query.data

    if user_id not in ADMIN_IDS:
        await query.message.answer(
            escape_message_parts("❌ У вас нет прав.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    user_data = await state.get_data()
    current_state = await state.get_state()
    logger.debug(f"handle_broadcast_buttons_count: user_id={user_id}, callback_data={callback_data}, current_state={current_state}, user_data={user_data}")

    if current_state != BotStates.AWAITING_BROADCAST_BUTTONS:
        logger.warning(f"handle_broadcast_buttons_count invoked without AWAITING_BROADCAST_BUTTONS for user_id={user_id}, state={current_state}")
        await state.clear()
        await query.message.answer(
            escape_message_parts("❌ Ошибка: выбор количества кнопок не ожидается. Начните заново.", version=2),
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if callback_data == "broadcast_no_buttons":
        await state.update_data(buttons=[], awaiting_broadcast_buttons=False)
        await proceed_to_broadcast_confirmation(query, state)
        return

    if callback_data.startswith("copy_buttons_"):
        try:
            source_broadcast_id = int(callback_data.replace("copy_buttons_", ""))
            buttons = await get_broadcast_buttons(source_broadcast_id)
            if not buttons:
                logger.warning(f"Кнопки для broadcast_id={source_broadcast_id} не найдены")
                text = escape_message_parts(
                    f"❌ Кнопки для рассылки ID {source_broadcast_id} не найдены. Выберите количество кнопок или пропустите.",
                    version=2
                )
                keyboard = [
                    [InlineKeyboardButton(text="0 (Без кнопок)", callback_data="broadcast_no_buttons")],
                    [InlineKeyboardButton(text="1", callback_data="broadcast_buttons_1")],
                    [InlineKeyboardButton(text="2", callback_data="broadcast_buttons_2")],
                    [InlineKeyboardButton(text="3", callback_data="broadcast_buttons_3")],
                    [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]
                ]
                await query.message.answer(
                    text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.set_state(BotStates.AWAITING_BROADCAST_BUTTONS)
                return
            await state.update_data(buttons=buttons, awaiting_broadcast_buttons=False)
            await proceed_to_broadcast_confirmation(query, state)
            logger.info(f"Кнопки скопированы из broadcast_id={source_broadcast_id} для user_id={user_id}")
        except ValueError as e:
            logger.error(f"Некорректный broadcast_id в copy_buttons: {callback_data}, error: {e}")
            text = escape_message_parts(
                f"❌ Ошибка: Некорректный ID рассылки. Выберите количество кнопок.",
                version=2
            )
            keyboard = [
                [InlineKeyboardButton(text="0 (Без кнопок)", callback_data="broadcast_no_buttons")],
                [InlineKeyboardButton(text="1", callback_data="broadcast_buttons_1")],
                [InlineKeyboardButton(text="2", callback_data="broadcast_buttons_2")],
                [InlineKeyboardButton(text="3", callback_data="broadcast_buttons_3")],
                [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]
            ]
            await query.message.answer(
                text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(BotStates.AWAITING_BROADCAST_BUTTONS)
        await query.answer()
        return

    try:
        button_count = int(callback_data.replace("broadcast_buttons_", ""))
        if button_count < 1 or button_count > 3:
            raise ValueError("Количество кнопок должно быть от 1 до 3")
        await state.update_data(
            button_count=button_count,
            current_button_index=1,
            buttons=[],
            awaiting_broadcast_button_input=True
        )
        text = escape_message_parts(
            f"🔘 Введите текст и алиас для кнопки 1 (формат: `Текст кнопки | алиас`):\n",
            "Пример: `В меню | menu`\n",
            f"Доступные алиасы: `{', '.join(sorted(BROADCAST_CALLBACK_ALIASES.keys()))}`",
            version=2
        )
        keyboard = [[InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]]
        await query.message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_BUTTON_INPUT)
        logger.info(f"Ожидание ввода кнопки 1 для user_id={user_id}, button_count={button_count}")
    except ValueError as e:
        logger.warning(f"Некорректное количество кнопок от user_id={user_id}: {callback_data}")
        text = escape_message_parts(
            f"❌ Ошибка: {str(e)}. Выберите количество кнопок (0-3).",
            version=2
        )
        keyboard = [
            [InlineKeyboardButton(text="0 (Без кнопок)", callback_data="broadcast_no_buttons")],
            [InlineKeyboardButton(text="1", callback_data="broadcast_buttons_1")],
            [InlineKeyboardButton(text="2", callback_data="broadcast_buttons_2")],
            [InlineKeyboardButton(text="3", callback_data="broadcast_buttons_3")],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]
        ]
        await query.message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_BUTTONS)
    await query.answer()

async def handle_broadcast_button_input(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод текста и алиаса для кнопки."""
    user_id = message.from_user.id
    user_data = await state.get_data()
    current_state = await state.get_state()
    logger.debug(f"handle_broadcast_button_input: user_id={user_id}, current_state={current_state}, user_data={user_data}")

    if user_id not in ADMIN_IDS:
        await message.answer(
            escape_message_parts("❌ У вас нет прав.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if not user_data.get('awaiting_broadcast_button_input') or current_state != BotStates.AWAITING_BROADCAST_BUTTON_INPUT:
        logger.warning(f"handle_broadcast_button_input invoked without awaiting_broadcast_button_input for user_id={user_id}, state={current_state}")
        await state.clear()
        await message.answer(
            escape_message_parts("❌ Ошибка: ввод кнопки не ожидается. Начните заново.", version=2),
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    button_input = message.text.strip()
    button_count = user_data.get('button_count', 0)
    current_button_index = user_data.get('current_button_index', 1)
    buttons = user_data.get('buttons', [])

    # Валидация ввода
    try:
        button_text, alias = button_input.split('|', 1)
        button_text = button_text.strip()[:64]  # Ограничиваем длину текста
        alias = alias.strip()[:64]  # Ограничиваем длину алиаса
        if not button_text or not alias:
            raise ValueError("Текст кнопки и алиас не могут быть пустыми")
        # Преобразуем алиас в callback_data
        callback_data = BROADCAST_CALLBACK_ALIASES.get(alias)
        if not callback_data or callback_data not in ALLOWED_BROADCAST_CALLBACKS:
            raise ValueError(f"Алиас должен быть одним из: {', '.join(sorted(BROADCAST_CALLBACK_ALIASES.keys()))}")
        buttons.append({"text": button_text, "callback_data": callback_data})
        await state.update_data(buttons=buttons)
        logger.debug(f"Кнопка добавлена для user_id={user_id}: text={button_text}, alias={alias}, callback_data={callback_data}")
    except ValueError as e:
        logger.warning(f"Некорректный ввод кнопки от user_id={user_id}: {button_input}")
        text = escape_message_parts(
            f"❌ Ошибка: {str(e)}.\n",
            f"Введите текст и алиас для кнопки {current_button_index} (формат: `Текст кнопки | алиас`).\n",
            "Пример: `В меню | menu`\n",
            f"Доступные алиасы: `{', '.join(sorted(BROADCAST_CALLBACK_ALIASES.keys()))}`",
            version=2
        )
        keyboard = [[InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]]
        await message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if current_button_index < button_count:
        next_index = current_button_index + 1
        await state.update_data(current_button_index=next_index)
        text = escape_message_parts(
            f"🔘 Введите текст и алиас для кнопки {next_index} (формат: `Текст кнопки | алиас`):\n",
            "Пример: `В меню | menu`\n",
            f"Доступные алиасы: `{', '.join(sorted(BROADCAST_CALLBACK_ALIASES.keys()))}`",
            version=2
        )
        keyboard = [[InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]]
        await message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_BUTTON_INPUT)
        logger.info(f"Ожидание ввода кнопки {next_index} для user_id={user_id}")
        return

    await state.update_data(awaiting_broadcast_button_input=False)
    await proceed_to_broadcast_confirmation(message, state)
    logger.info(f"Все кнопки введены для user_id={user_id}, переход к подтверждению рассылки")

async def proceed_to_broadcast_confirmation(update: Message | CallbackQuery, state: FSMContext) -> None:
    """Переходит к подтверждению рассылки."""
    user_id = update.from_user.id if isinstance(update, Message) else update.from_user.id
    user_data = await state.get_data()
    broadcast_type = user_data.get('broadcast_type', 'all')
    message_text = user_data.get('broadcast_message', '')
    media = user_data.get('broadcast_media', None)
    buttons = user_data.get('buttons', [])

    # Определяем целевую группу пользователей
    if broadcast_type == 'all':
        all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)
        target_users = [user[0] for user in all_users_data]
    elif broadcast_type == 'paid':
        target_users = await get_paid_users()
    elif broadcast_type == 'non_paid':
        target_users = await get_non_paid_users()
    elif broadcast_type.startswith('with_payment_'):
        audience_type = broadcast_type.replace('with_payment_', '')
        if audience_type == 'all':
            all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)
            target_users = [user[0] for user in all_users_data]
        elif audience_type == 'paid':
            target_users = await get_paid_users()
        elif audience_type == 'non_paid':
            target_users = await get_non_paid_users()
        else:
            text = escape_message_parts(
                f"❌ Неизвестный тип аудитории для рассылки: `{audience_type}`.",
                version=2
            )
            if isinstance(update, Message):
                await update.answer(
                    text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await update.message.answer(
                    text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
                )
            await state.clear()
            logger.error(f"Неизвестный тип аудитории: {audience_type} для user_id={user_id}")
            return
    else:
        text = escape_message_parts(
            f"❌ Неизвестный тип рассылки: `{broadcast_type}`.",
            version=2
        )
        if isinstance(update, Message):
            await update.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        await state.clear()
        logger.error(f"Неизвестный тип рассылки: {broadcast_type} для user_id={user_id}")
        return

    if not target_users:
        text = escape_message_parts(
            f"❌ Нет пользователей для рассылки (тип: `{broadcast_type}`).",
            version=2
        )
        if isinstance(update, Message):
            await update.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await update.message.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        await state.clear()
        logger.info(f"Нет пользователей для рассылки типа {broadcast_type} для user_id={user_id}")
        return

    # Формируем текст подтверждения
    buttons_text = "\n".join([f"• `{button['text']}` -> `{BROADCAST_CALLBACK_ALIASES.get(k, button['callback_data'])}`" for button in buttons for k, v in BROADCAST_CALLBACK_ALIASES.items() if v == button['callback_data']]) if buttons else "Нет кнопок"
    text = escape_message_parts(
        f"📢 Подтверждение рассылки\n\n",
        f"👥 Получатели: `{len(target_users)}` пользователей\n",
        f"📝 Сообщение:\n{message_text}\n\n",
        f"📸 Медиа: {'Есть' if media else 'Нет'}\n",
        f"🔘 Кнопки:\n{buttons_text}\n\n",
        f"⏰ Отправить сейчас или запланировать?",
        version=2
    )
    keyboard = [
        [InlineKeyboardButton(text="📤 Отправить сейчас", callback_data="broadcast_send_now")],
        [InlineKeyboardButton(text="⏰ Запланировать", callback_data="broadcast_schedule")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]
    ]
    if isinstance(update, Message):
        await update.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await update.message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.set_state(BotStates.AWAITING_BROADCAST_SCHEDULE)
    logger.info(f"Переход к подтверждению рассылки для user_id={user_id}")

async def handle_broadcast_schedule_time(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод времени для запланированной рассылки."""
    user_id = message.from_user.id
    user_data = await state.get_data()
    if not user_data.get('awaiting_broadcast_schedule'):
        logger.warning(f"handle_broadcast_schedule_time invoked without awaiting_broadcast_schedule for user_id={user_id}")
        await state.clear()
        await message.answer(
            escape_message_parts("❌ Ошибка: действие рассылки не ожидается.", version=2),
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    await state.update_data(awaiting_broadcast_schedule=None, user_id=user_id)
    text = message.text.strip()

    try:
        schedule_time = datetime.strptime(text, '%Y-%m-%d %H:%M')
        if schedule_time < datetime.now():
            raise ValueError("Время рассылки не может быть в прошлом.")

        broadcast_type = user_data.get('broadcast_type', 'all')
        message_text = user_data.get('broadcast_message', '')
        media = user_data.get('broadcast_media', None)
        buttons = user_data.get('buttons', [])  # Извлекаем кнопки, по умолчанию пустой список

        await schedule_broadcast(schedule_time, message_text, media, broadcast_type, user_id, buttons)

        text = escape_message_parts(
            f"✅ Рассылка запланирована на `{text}`!\n",
            f"👥 Получатели будут определены на момент отправки.",
            version=2
        )
        await state.clear()
        await message.answer(
            text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

    except ValueError as e:
        logger.warning(f"Неверный формат времени рассылки от user_id={user_id}: {text}, error: {e}")
        text = escape_message_parts(
            f"⚠️ Неверный формат времени: {str(e)}. ",
            f"Используйте `YYYY-MM-DD HH:MM` (например, `2025-06-15 14:30`).",
            version=2
        )
        keyboard = [[InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]]
        await state.update_data(awaiting_broadcast_schedule=True, user_id=user_id)
        await message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_SCHEDULE)
    except Exception as e:
        logger.error(f"Ошибка при планировании рассылки для user_id={user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            f"❌ Ошибка при планировании рассылки: {str(e)}. ",
            "Обратитесь в поддержку.",
            version=2
        )
        await state.clear()
        await message.answer(
            text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )
        for admin_id in ADMIN_IDS:
            try:
                await send_message_with_fallback(
                    message.bot, admin_id,
                    escape_message_parts(
                        f"🚨 Ошибка при планировании рассылки для user_id={user_id}: {str(e)}",
                        version=2
                    ),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e_notify:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e_notify}")

async def migrate_scheduled_broadcasts_table(conn) -> None:
    """Выполняет миграцию таблицы scheduled_broadcasts, добавляя столбец scheduled_time."""
    try:
        c = await conn.execute("PRAGMA table_info(scheduled_broadcasts)")
        columns = [row[1] for row in await c.fetchall()]
        if 'scheduled_time' not in columns:
            logger.info("Столбец scheduled_time отсутствует в таблице scheduled_broadcasts. Выполняется миграция.")
            await conn.execute("ALTER TABLE scheduled_broadcasts RENAME TO scheduled_broadcasts_old")
            await conn.execute("""
                CREATE TABLE scheduled_broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scheduled_time TEXT NOT NULL,
                    broadcast_data TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                INSERT INTO scheduled_broadcasts (id, broadcast_data, status, created_at)
                SELECT id, broadcast_data, status, created_at
                FROM scheduled_broadcasts_old
            """)
            await conn.execute("DROP TABLE scheduled_broadcasts_old")
            await conn.commit()
            logger.info("Миграция таблицы scheduled_broadcasts завершена успешно.")
        else:
            logger.debug("Столбец scheduled_time уже существует в таблице scheduled_broadcasts.")
    except Exception as e:
        logger.error(f"Ошибка при миграции таблицы scheduled_broadcasts: {e}", exc_info=True)
        raise

async def migrate_scheduled_time_format(conn):
    """Миграция формата scheduled_time в scheduled_broadcasts."""
    try:
        conn.row_factory = aiosqlite.Row
        c = await conn.cursor()
        await c.execute("SELECT id, scheduled_time FROM scheduled_broadcasts WHERE scheduled_time LIKE '%T%'")
        rows = await c.fetchall()
        logger.debug(f"Найдено записей для миграции: {len(rows)}")
        for row in rows:
            try:
                if not row['scheduled_time'] or not isinstance(row['scheduled_time'], str):
                    logger.warning(f"Пропущена запись ID {row['id']}: scheduled_time пустое или некорректное")
                    continue
                old_time = datetime.fromisoformat(row['scheduled_time'].replace('Z', '+00:00'))
                new_time = old_time.strftime('%Y-%m-%d %H:%M:%S')
                logger.debug(f"Миграция записи ID {row['id']}: {row['scheduled_time']} -> {new_time}")
                await c.execute(
                    "UPDATE scheduled_broadcasts SET scheduled_time = ? WHERE id = ?",
                    (new_time, row['id'])
                )
            except ValueError as ve:
                logger.warning(f"Некорректный формат scheduled_time для ID {row['id']}: {ve}")
                continue
        await conn.commit()
        logger.info("Миграция формата scheduled_time завершена")
    except Exception as e:
        logger.error(f"Ошибка миграции формата scheduled_time: {e}", exc_info=True)
        raise

async def migrate_broadcast_data(conn) -> None:
    """Миграция таблицы scheduled_broadcasts для исправления некорректных или пустых broadcast_data."""
    try:
        conn.row_factory = aiosqlite.Row
        c = await conn.cursor()
        await c.execute("SELECT id, broadcast_data FROM scheduled_broadcasts")
        rows = await c.fetchall()
        logger.debug(f"Найдено записей для миграции broadcast_data: {len(rows)}")
        updated_count = 0
        for row in rows:
            broadcast_id = row['id']
            try:
                # Проверяем на None, пустую строку или строку "null"
                if row['broadcast_data'] is None or row['broadcast_data'].strip() == '' or row['broadcast_data'].lower() == 'null':
                    logger.warning(f"Пустое, None или 'null' broadcast_data для broadcast_id={broadcast_id}")
                    new_broadcast_data = json.dumps({
                        'message': '',
                        'media': None,
                        'broadcast_type': 'all',
                        'admin_user_id': ADMIN_IDS[0],
                        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'with_payment_button': False,
                        'buttons': []
                    }, ensure_ascii=False)
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET broadcast_data = ? WHERE id = ?",
                        (new_broadcast_data, broadcast_id)
                    )
                    updated_count += 1
                    continue
                # Проверяем JSON
                broadcast_data = json.loads(row['broadcast_data'])
                if not isinstance(broadcast_data, dict):
                    logger.error(f"Некорректный формат broadcast_data (не словарь) для broadcast_id={broadcast_id}: {row['broadcast_data']}")
                    new_broadcast_data = json.dumps({
                        'message': '',
                        'media': None,
                        'broadcast_type': 'all',
                        'admin_user_id': ADMIN_IDS[0],
                        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'with_payment_button': False,
                        'buttons': []
                    }, ensure_ascii=False)
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET broadcast_data = ? WHERE id = ?",
                        (new_broadcast_data, broadcast_id)
                    )
                    updated_count += 1
                    continue
                # Проверяем наличие обязательных полей
                required_fields = ['message', 'media', 'broadcast_type', 'admin_user_id', 'with_payment_button', 'buttons']
                updated = False
                for field in required_fields:
                    if field not in broadcast_data:
                        logger.warning(f"Отсутствует поле {field} в broadcast_data для broadcast_id={broadcast_id}")
                        broadcast_data[field] = '' if field == 'message' else None if field == 'media' else 'all' if field == 'broadcast_type' else ADMIN_IDS[0] if field == 'admin_user_id' else False if field == 'with_payment_button' else []
                        updated = True
                if updated:
                    new_broadcast_data = json.dumps(broadcast_data, ensure_ascii=False)
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET broadcast_data = ? WHERE id = ?",
                        (new_broadcast_data, broadcast_id)
                    )
                    updated_count += 1
            except json.JSONDecodeError as je:
                logger.error(f"Ошибка парсинга broadcast_data для broadcast_id={broadcast_id}: {je}, данные: {row['broadcast_data']}")
                new_broadcast_data = json.dumps({
                    'message': '',
                    'media': None,
                    'broadcast_type': 'all',
                    'admin_user_id': ADMIN_IDS[0],
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'with_payment_button': False,
                    'buttons': []
                }, ensure_ascii=False)
                await c.execute(
                    "UPDATE scheduled_broadcasts SET broadcast_data = ? WHERE id = ?",
                    (new_broadcast_data, broadcast_id)
                )
                updated_count += 1
        await conn.commit()
        logger.info(f"Миграция broadcast_data завершена, обновлено записей: {updated_count}")
    except Exception as e:
        logger.error(f"Ошибка миграции broadcast_data: {e}", exc_info=True)
        raise

async def migrate_broadcast_message_escaping(conn) -> None:
    """Миграция broadcast_data для очистки экранированных символов в поле message."""
    try:
        conn.row_factory = aiosqlite.Row
        c = await conn.cursor()
        await c.execute("SELECT id, broadcast_data FROM scheduled_broadcasts WHERE status = 'pending'")
        rows = await c.fetchall()
        logger.debug(f"Найдено записей для миграции экранирования: {len(rows)}")
        for row in rows:
            try:
                broadcast_data = json.loads(row['broadcast_data'])
                if 'message' not in broadcast_data:
                    logger.debug(f"Пропущена запись ID {row['id']}: отсутствует поле message")
                    continue
                # Очищаем текст от экранирования
                raw_message = unescape_markdown(broadcast_data['message'])
                broadcast_data['message'] = raw_message
                new_broadcast_data = json.dumps(broadcast_data, ensure_ascii=False)
                await c.execute(
                    "UPDATE scheduled_broadcasts SET broadcast_data = ? WHERE id = ?",
                    (new_broadcast_data, row['id'])
                )
                logger.debug(f"Очищено экранирование для записи ID {row['id']}")
            except json.JSONDecodeError as je:
                logger.warning(f"Ошибка парсинга broadcast_data для ID {row['id']}: {je}")
                continue
        await conn.commit()
        logger.info("Миграция экранирования сообщений в broadcast_data завершена")
    except Exception as e:
        logger.error(f"Ошибка миграции экранирования broadcast_data: {e}", exc_info=True)
        raise

async def migrate_scheduled_timezone(conn):
    """Корректирует scheduled_time, если оно записано в UTC, добавляя 3 часа для MSK."""
    try:
        conn.row_factory = aiosqlite.Row
        c = await conn.cursor()
        await c.execute("SELECT id, scheduled_time FROM scheduled_broadcasts WHERE status = 'pending'")
        rows = await c.fetchall()
        logger.debug(f"Проверка часового пояса: найдено записей {len(rows)}")
        msk_tz = pytz.timezone('Europe/Moscow')
        utc_tz = pytz.timezone('UTC')
        for row in rows:
            try:
                if not row['scheduled_time'] or not isinstance(row['scheduled_time'], str):
                    logger.warning(f"Пропущена запись ID {row['id']}: scheduled_time пустое или некорректное")
                    continue
                utc_time = datetime.strptime(row['scheduled_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=utc_tz)
                msk_time = utc_time.astimezone(msk_tz)
                new_time = msk_time.strftime('%Y-%m-%d %H:%M:%S')
                if new_time != row['scheduled_time']:
                    logger.debug(f"Корректировка часового пояса для ID {row['id']}: {row['scheduled_time']} -> {new_time}")
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET scheduled_time = ? WHERE id = ?",
                        (new_time, row['id'])
                    )
            except ValueError as ve:
                logger.warning(f"Некорректный формат scheduled_time для ID {row['id']}: {ve}")
                continue
        await conn.commit()
        logger.info("Миграция часового пояса scheduled_time завершена")
    except Exception as e:
        logger.error(f"Ошибка миграции часового пояса scheduled_time: {e}", exc_info=True)
        raise

async def migrate_broadcast_admin_user_id(conn):
    """Добавляет admin_user_id в broadcast_data для существующих записей."""
    try:
        conn.row_factory = aiosqlite.Row
        c = await conn.cursor()
        await c.execute("SELECT id, broadcast_data FROM scheduled_broadcasts WHERE status = 'pending'")
        rows = await c.fetchall()
        logger.debug(f"Найдено записей для миграции admin_user_id: {len(rows)}")
        for row in rows:
            try:
                broadcast_data = json.loads(row['broadcast_data'])
                if 'admin_user_id' not in broadcast_data:
                    broadcast_data['admin_user_id'] = ADMIN_IDS[0]  # Fallback на первого админа
                    new_broadcast_data = json.dumps(broadcast_data, ensure_ascii=False)
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET broadcast_data = ? WHERE id = ?",
                        (new_broadcast_data, row['id'])
                    )
                    logger.debug(f"Добавлен admin_user_id для записи ID {row['id']}")
            except json.JSONDecodeError as je:
                logger.warning(f"Ошибка парсинга broadcast_data для ID {row['id']}: {je}")
                continue
        await conn.commit()
        logger.info("Миграция admin_user_id в broadcast_data завершена")
    except Exception as e:
        logger.error(f"Ошибка миграции admin_user_id: {e}", exc_info=True)
        raise

async def schedule_broadcast(schedule_time: datetime, message_text: str, media: Optional[Dict], broadcast_type: str, admin_user_id: int, buttons: List[Dict[str, str]]) -> None:
    """Сохраняет запланированную рассылку в базу данных."""
    try:
        # Сохраняем текст сообщения без предварительного экранирования
        broadcast_data = {
            'message': message_text,  # Храним чистый текст
            'media': media,
            'broadcast_type': broadcast_type,
            'admin_user_id': admin_user_id,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'with_payment_button': broadcast_type.startswith('with_payment_'),
            'buttons': buttons  # Сохраняем кнопки в broadcast_data как резерв
        }
        async with aiosqlite.connect(DATABASE_PATH, timeout=15) as conn:
            await conn.execute("PRAGMA busy_timeout = 30000")
            await migrate_scheduled_broadcasts_table(conn)
            await migrate_scheduled_time_format(conn)
            await migrate_scheduled_timezone(conn)
            await migrate_broadcast_admin_user_id(conn)
            await migrate_broadcast_message_escaping(conn)
            c = await conn.cursor()
            scheduled_time_str = schedule_time.strftime('%Y-%m-%d %H:%M:%S')
            await c.execute(
                "INSERT INTO scheduled_broadcasts (scheduled_time, broadcast_data, status) VALUES (?, ?, 'pending')",
                (scheduled_time_str, json.dumps(broadcast_data, ensure_ascii=False))
            )
            broadcast_id = c.lastrowid
            # Сохраняем кнопки в таблицу broadcast_buttons
            for button in buttons:
                success = await save_broadcast_button(broadcast_id, button['text'], button['callback_data'], conn=conn)
                if not success:
                    logger.error(f"Не удалось сохранить кнопку для broadcast_id={broadcast_id}: text={button['text']}. Откат транзакции.")
                    await conn.rollback()
                    raise aiosqlite.OperationalError(f"Не удалось сохранить кнопку для broadcast_id={broadcast_id}")
            await conn.commit()
        logger.info(f"Рассылка запланирована на {scheduled_time_str} для типа {broadcast_type} от admin_user_id={admin_user_id} с {len(buttons)} кнопками")
    except Exception as e:
        logger.error(f"Ошибка при сохранении запланированной рассылки: {e}", exc_info=True)
        raise

async def broadcast_message_admin(bot: Bot, message_text: str, admin_user_id: int, media_type: str = None, media_id: str = None, buttons: List[Dict[str, str]] = None) -> None:
    """Выполняет рассылку всем пользователям."""
    buttons = buttons or []
    all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)
    target_users = [user[0] for user in all_users_data]
    sent_count = 0
    failed_count = 0
    total_to_send = len(target_users)
    logger.info(f"Начало общей рассылки от админа {admin_user_id} для {total_to_send} пользователей.")
    await send_message_with_fallback(
        bot, admin_user_id,
        escape_message_parts(
            f"🚀 Начинаю рассылку для ~`{total_to_send}` пользователей...",
            version=2
        ),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    for target_user_id in target_users:
        try:
            reply_markup = await create_dynamic_broadcast_keyboard(buttons, target_user_id) if buttons else None
            try:
                # Пытаемся отправить с MarkdownV2
                if media_type == 'photo' and media_id:
                    await bot.send_photo(
                        chat_id=target_user_id, photo=media_id,
                        caption=message_text, parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=reply_markup
                    )
                elif media_type == 'video' and media_id:
                    await bot.send_video(
                        chat_id=target_user_id, video=media_id,
                        caption=message_text, parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=reply_markup
                    )
                else:
                    await bot.send_message(
                        chat_id=target_user_id, text=message_text, parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=reply_markup
                    )
            except TelegramBadRequest as e:
                # Fallback: отправка без Markdown
                logger.warning(f"Ошибка Markdown для user_id={target_user_id}: {e}. Пробуем без парсинга.")
                raw_text = unescape_markdown(message_text)
                if media_type == 'photo' and media_id:
                    await bot.send_photo(
                        chat_id=target_user_id, photo=media_id,
                        caption=raw_text, parse_mode=None,
                        reply_markup=reply_markup
                    )
                elif media_type == 'video' and media_id:
                    await bot.send_video(
                        chat_id=target_user_id, video=media_id,
                        caption=raw_text, parse_mode=None,
                        reply_markup=reply_markup
                    )
                else:
                    await bot.send_message(
                        chat_id=target_user_id, text=raw_text, parse_mode=None,
                        reply_markup=reply_markup
                    )
            sent_count += 1
            if sent_count % 20 == 0:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}", exc_info=True)
            failed_count += 1
    summary_text = escape_message_parts(
        f"🏁 Рассылка завершена!\n",
        f"✅ Отправлено: `{sent_count}`\n",
        f"❌ Не удалось отправить: `{failed_count}`",
        version=2
    )
    await send_message_with_fallback(
        bot, admin_user_id, summary_text, reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Общая рассылка завершена. Отправлено: {sent_count}, Ошибок: {failed_count}")

async def broadcast_to_paid_users(bot: Bot, message_text: str, admin_user_id: int, media_type: str = None, media_id: str = None, buttons: List[Dict[str, str]] = None) -> None:
    """Выполняет рассылку только оплатившим пользователям."""
    buttons = buttons or []
    target_users = await get_paid_users()
    sent_count = 0
    failed_count = 0
    total_to_send = len(target_users)
    logger.info(f"Начало рассылки для оплативших от админа {admin_user_id} для {total_to_send} пользователей.")
    await send_message_with_fallback(
        bot, admin_user_id,
        escape_message_parts(
            f"🚀 Начинаю рассылку для ~`{total_to_send}` оплативших пользователей...",
            version=2
        ),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    signature = "🍪 PixelPie"
    caption = message_text + ("\n\n" + signature if message_text.strip() else "\n" + signature)
    escaped_caption = escape_message_parts(caption, version=2)
    for target_user_id in target_users:
        try:
            reply_markup = await create_dynamic_broadcast_keyboard(buttons, target_user_id) if buttons else None
            if media_type == 'photo' and media_id:
                await bot.send_photo(
                    chat_id=target_user_id, photo=media_id,
                    caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            elif media_type == 'video' and media_id:
                await bot.send_video(
                    chat_id=target_user_id, video=media_id,
                    caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            else:
                await bot.send_message(
                    chat_id=target_user_id, text=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            sent_count += 1
            if sent_count % 20 == 0:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}", exc_info=True)
            failed_count += 1
    summary_text = escape_message_parts(
        f"🏁 Рассылка для оплативших завершена!\n",
        f"✅ Отправлено: `{sent_count}`\n",
        f"❌ Не удалось отправить: `{failed_count}`",
        version=2
    )
    await send_message_with_fallback(
        bot, admin_user_id, summary_text, reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Рассылка для оплативших завершена. Отправлено: {sent_count}, Ошибок: {failed_count}")

async def broadcast_to_non_paid_users(bot: Bot, message_text: str, admin_user_id: int, media_type: str = None, media_id: str = None, buttons: List[Dict[str, str]] = None) -> None:
    """Выполняет рассылку только не оплатившим пользователям."""
    buttons = buttons or []
    target_users = await get_non_paid_users()
    sent_count = 0
    failed_count = 0
    total_to_send = len(target_users)
    logger.info(f"Начало рассылки для не оплативших от админа {admin_user_id} для {total_to_send} пользователей.")
    await send_message_with_fallback(
        bot, admin_user_id,
        escape_message_parts(
            f"🚀 Начинаю рассылку для ~`{total_to_send}` не оплативших пользователей...",
            version=2
        ),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    signature = "🍪 PixelPie"
    caption = message_text + ("\n\n" + signature if message_text.strip() else "\n" + signature)
    escaped_caption = escape_message_parts(caption, version=2)
    for target_user_id in target_users:
        try:
            reply_markup = await create_dynamic_broadcast_keyboard(buttons, target_user_id) if buttons else None
            if media_type == 'photo' and media_id:
                await bot.send_photo(
                    chat_id=target_user_id, photo=media_id,
                    caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            elif media_type == 'video' and media_id:
                await bot.send_video(
                    chat_id=target_user_id, video=media_id,
                    caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            else:
                await bot.send_message(
                    chat_id=target_user_id, text=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            sent_count += 1
            if sent_count % 20 == 0:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}", exc_info=True)
            failed_count += 1
    summary_text = escape_message_parts(
        f"🏁 Рассылка для не оплативших завершена!\n",
        f"✅ Отправлено: `{sent_count}`\n",
        f"❌ Не удалось отправить: `{failed_count}`",
        version=2
    )
    await send_message_with_fallback(
        bot, admin_user_id, summary_text, reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Рассылка для не оплативших завершена. Отправлено: {sent_count}, Ошибок: {failed_count}")

async def broadcast_with_payment(
    bot: Bot, 
    message_text: str, 
    admin_user_id: int, 
    media_type: Optional[str] = None, 
    media_id: Optional[str] = None, 
    buttons: List[Dict[str, str]] = None
) -> None:
    """Выполняет рассылку всем пользователям с кнопкой для перехода к тарифам."""
    buttons = buttons or []
    all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)
    target_users = [user[0] for user in all_users_data]
    sent_count = 0
    failed_count = 0
    total_to_send = len(target_users)
    logger.info(f"Начало рассылки с оплатой от админа {admin_user_id} для {total_to_send} пользователей.")
    await send_message_with_fallback(
        bot, admin_user_id,
        escape_message_parts(
            f"🚀 Начинаю рассылку с оплатой для ~`{total_to_send}` пользователей...",
            version=2
        ),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    signature = "🍪 PixelPie"
    caption = message_text + ("\n\n" + signature if message_text.strip() else "\n" + signature)
    escaped_caption = escape_message_parts(caption, version=2)
    for target_user_id in target_users:
        try:
            reply_markup = await create_dynamic_broadcast_keyboard(buttons, target_user_id) if buttons else None
            if media_type == 'photo' and media_id:
                await bot.send_photo(
                    chat_id=target_user_id, photo=media_id,
                    caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            elif media_type == 'video' and media_id:
                await bot.send_video(
                    chat_id=target_user_id, video=media_id,
                    caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=reply_markup
                )
            else:
                await bot.send_message(
                    chat_id=target_user_id, text=escaped_caption,
                    parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
                )
            sent_count += 1
            if sent_count % 20 == 0:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}", exc_info=True)
            failed_count += 1
    summary_text = escape_message_parts(
        f"🏁 Рассылка с оплатой завершена!\n",
        f"✅ Отправлено: `{sent_count}`\n",
        f"❌ Не удалось отправить: `{failed_count}`",
        version=2
    )
    await send_message_with_fallback(
        bot, admin_user_id, summary_text,
        reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Рассылка с оплатой завершена. Отправлено: {sent_count}, Ошибок: {failed_count}")

async def handle_broadcast_schedule_input(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает выбор времени отправки или немедленную рассылку."""
    await query.answer()
    user_id = query.from_user.id
    logger.info(f"handle_broadcast_schedule_input: user_id={user_id}, callback_data={query.data}")

    if user_id not in ADMIN_IDS:
        await state.clear()
        await query.message.answer(
            escape_message_parts("❌ У вас нет прав для выполнения рассылки.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.warning(f"Пользователь user_id={user_id} попытался выполнить рассылку без прав администратора")
        return

    user_data = await state.get_data()
    try:
        if query.data == "broadcast_send_now":
            broadcast_type = user_data.get('broadcast_type', '')
            message_text = user_data.get('broadcast_message', '')
            media = user_data.get('broadcast_media', None)
            buttons = user_data.get('buttons', [])
            media_type = media.get('type') if media else None
            media_id = media.get('file_id') if media else None

            # Определяем целевую группу пользователей
            if broadcast_type == 'all':
                all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)
                target_users = [user[0] for user in all_users_data]
            elif broadcast_type == 'paid':
                target_users = await get_paid_users()
            elif broadcast_type == 'non_paid':
                target_users = await get_non_paid_users()
            elif broadcast_type.startswith('with_payment_'):
                audience_type = broadcast_type.replace('with_payment_', '')
                if audience_type == 'all':
                    all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)
                    target_users = [user[0] for user in all_users_data]
                elif audience_type == 'paid':
                    target_users = await get_paid_users()
                elif audience_type == 'non_paid':
                    target_users = await get_non_paid_users()
                else:
                    text = escape_message_parts(
                        f"❌ Неизвестный тип аудитории для рассылки: `{audience_type}`.",
                        version=2
                    )
                    await query.message.answer(
                        text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
                    )
                    await state.clear()
                    logger.error(f"Неизвестный тип аудитории: {audience_type} для user_id={user_id}")
                    return
            else:
                text = escape_message_parts(
                    f"❌ Неизвестный тип рассылки: `{broadcast_type}`.",
                    version=2
                )
                await query.message.answer(
                    text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.clear()
                logger.error(f"Неизвестный тип рассылки: {broadcast_type} для user_id={user_id}")
                return

            if not target_users:
                text = escape_message_parts(
                    f"❌ Нет пользователей для рассылки (тип: `{broadcast_type}`).",
                    version=2
                )
                await query.message.answer(
                    text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.clear()
                logger.info(f"Нет пользователей для рассылки типа {broadcast_type} для user_id={user_id}")
                return

            # Формируем подпись сообщения
            signature = "🍪 PixelPie"
            caption = message_text + ("\n\n" + signature if message_text.strip() else "\n" + signature)
            escaped_caption = escape_message_parts(caption, version=2)

            # Отправляем сообщение о начале рассылки
            await query.message.edit_text(
                escape_message_parts("⏳ Выполняется рассылка...", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )

            # Выполняем рассылку
            success_count = 0
            error_count = 0
            for target_user_id in target_users:
                try:
                    reply_markup = await create_dynamic_broadcast_keyboard(buttons, target_user_id) if buttons else None
                    if media_type == 'photo' and media_id:
                        await query.bot.send_photo(
                            chat_id=target_user_id, photo=media_id,
                            caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=reply_markup
                        )
                    elif media_type == 'video' and media_id:
                        await query.bot.send_video(
                            chat_id=target_user_id, video=media_id,
                            caption=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=reply_markup
                        )
                    else:
                        await query.bot.send_message(
                            chat_id=target_user_id, text=escaped_caption, parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=reply_markup
                        )
                    success_count += 1
                    if success_count % 20 == 0:
                        await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}")
                    error_count += 1

            # Формируем итоговое сообщение
            text = escape_message_parts(
                f"✅ Рассылка завершена!\n\n",
                f"📤 Успешно отправлено: `{success_count}`\n",
                f"❌ Ошибок: `{error_count}`\n",
                f"👥 Всего получателей: `{len(target_users)}`",
                version=2
            )
            await state.clear()
            await query.message.edit_text(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Немедленная рассылка завершена для user_id={user_id}, "
                        f"тип={broadcast_type}, отправлено={success_count}, ошибок={error_count}")

        elif query.data == "broadcast_schedule":
            await state.update_data(awaiting_broadcast_schedule=True, user_id=user_id)
            text = escape_message_parts(
                "⏰ Введите дату и время для запланированной рассылки\n\n",
                "📅 Формат: `YYYY-MM-DD HH:MM`\n",
                "Пример: `2025-06-15 14:30`\n\n",
                "Время в часовом поясе MSK.",
                version=2
            )
            keyboard = [[InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_broadcast")]]
            await query.message.edit_text(
                text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(BotStates.AWAITING_BROADCAST_SCHEDULE)
            logger.info(f"Запрошен ввод времени для запланированной рассылки для user_id={user_id}")

        elif query.data == "cancel_broadcast":
            await state.clear()
            text = escape_message_parts(
                "✅ Создание рассылки отменено.",
                version=2
            )
            await query.message.edit_text(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Создание рассылки отменено для user_id={user_id}")

        else:
            logger.error(f"Неизвестная callback_data в handle_broadcast_schedule: {query.data} для user_id={user_id}")
            await state.clear()
            await query.message.answer(
                escape_message_parts("❌ Неизвестная команда.", version=2),
                reply_markup=await create_admin_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Ошибка в handle_broadcast_schedule_input для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await query.message.answer(
            escape_message_parts(
                "❌ Произошла ошибка при обработке рассылки.",
                " Попробуйте снова или обратитесь в поддержку @AXIDI_Help",
                version=2
            ),
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def list_scheduled_broadcasts(message: Message | CallbackQuery, state: FSMContext) -> None:
    """Показывает список запланированных рассылок и позволяет управлять ими."""
    user_id = message.from_user.id if isinstance(message, Message) else message.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        if isinstance(message, Message):
            await message.answer(
                escape_message_parts("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await message.message.answer(
                escape_message_parts("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
            )
        return

    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute('''
                SELECT id, scheduled_time, status, broadcast_data
                FROM scheduled_broadcasts
                WHERE status = 'pending'
                ORDER BY scheduled_time ASC
            ''')
            broadcasts = await c.fetchall()

        if not broadcasts:
            text = escape_message_parts("📢 Нет запланированных рассылок.")
            reply_markup = await create_admin_keyboard()
            if isinstance(message, Message):
                await message.answer(
                    text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await message.message.answer(
                    text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
                )
            await state.clear()
            return

        await state.update_data(broadcasts=[
            {
                'id': row['id'],
                'scheduled_time': row['scheduled_time'],
                'status': row['status'],
                'broadcast_data': json.loads(row['broadcast_data'])
            } for row in broadcasts
        ])

        text = escape_message_parts("📢 Список запланированных рассылок:\n\n")
        keyboard = []
        for idx, broadcast in enumerate((await state.get_data()).get('broadcasts', []), 1):
            broadcast_data = broadcast['broadcast_data']
            message_preview = broadcast_data.get('message', '')[:50] + ('...' if len(broadcast_data.get('message', '')) > 50 else '')
            media_type = broadcast_data.get('media', {}).get('type', 'Нет')
            target_group = broadcast_data.get('broadcast_type', 'all')
            text += escape_md(
                f"`{idx}`. ID: `{broadcast['id']}`\n"
                f"⏰ Время: `{broadcast['scheduled_time']}` MSK\n"
                f"👥 Группа: `{target_group}`\n"
                f"📝 Сообщение: `{message_preview}`\n"
                f"📸 Медиа: `{media_type}`\n\n"
            )
            keyboard.append([
                InlineKeyboardButton(text=f"🗑 Удалить #{broadcast['id']}", callback_data=f"delete_broadcast_{broadcast['id']}")
            ])

        keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        await state.update_data(awaiting_broadcast_manage_action=True)
        if isinstance(message, Message):
            await message.answer(
                text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await message.message.answer(
                text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
        await state.set_state(BotStates.AWAITING_BROADCAST_MANAGE_ACTION)

    except Exception as e:
        logger.error(f"Ошибка получения списка рассылок для user_id={user_id}: {e}", exc_info=True)
        text = escape_message_parts(f"❌ Ошибка получения списка рассылок: {str(e)}.")
        if isinstance(message, Message):
            await message.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await message.message.answer(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
        await state.clear()

async def handle_broadcast_manage_action(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает действия с запланированными рассылками."""
    await query.answer()
    user_id = query.from_user.id

    user_data = await state.get_data()
    if not user_data.get('awaiting_broadcast_manage_action'):
        logger.warning(f"handle_broadcast_manage_action invoked without awaiting_broadcast_manage_action for user_id={user_id}, callback_data={query.data}")
        await state.clear()
        text = escape_message_parts("✅ Возврат в админ-панель.", version=2)
        await query.message.edit_text(
            text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if query.data.startswith("delete_broadcast_"):
        broadcast_id = int(query.data.replace("delete_broadcast_", ""))
        broadcasts = user_data.get('broadcasts', [])
        broadcast = next((b for b in broadcasts if b['id'] == broadcast_id), None)

        if not broadcast:
            text = escape_message_parts(
                f"❌ Рассылка ID `{broadcast_id}` не найдена.",
                version=2
            )
            await query.message.edit_text(
                text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.clear()
            return

        await state.update_data(delete_broadcast_id=broadcast_id, awaiting_broadcast_delete_confirm=True, user_id=user_id)

        # Очищаем message от возможных экранирований
        raw_message = unescape_markdown(broadcast['broadcast_data'].get('message', ''))
        message_preview = raw_message[:50] + ('...' if len(raw_message) > 50 else '')
        text = escape_message_parts(
            f"🗑 Подтвердите удаление рассылки ID `{broadcast_id}`\n",
            f"⏰ Время: `{broadcast['scheduled_time']}` MSK\n",
            f"👥 Группа: `{broadcast['broadcast_data'].get('broadcast_type', 'all')}`\n",
            f"📝 Сообщение: `{message_preview}`",
            version=2
        )
        keyboard = [
            [InlineKeyboardButton(text="✅ Удалить", callback_data=f"confirm_delete_{broadcast_id}")],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="list_broadcasts")]
        ]
        await query.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_DELETE_CONFIRM)

    elif query.data == "admin_panel":
        await state.clear()
        text = escape_message_parts("✅ Возврат в админ-панель.", version=2)
        await query.message.edit_text(
            text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_broadcast_delete_confirm(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает подтверждение удаления рассылки."""
    await query.answer()
    user_id = query.from_user.id

    user_data = await state.get_data()
    if not user_data.get('awaiting_broadcast_delete_confirm'):
        logger.warning(f"handle_broadcast_delete_confirm invoked without awaiting_broadcast_delete_confirm for user_id={user_id}")
        await state.clear()
        text = escape_message_parts("❌ Ошибка: действие не ожидается.", version=2)
        await query.message.edit_text(
            text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    broadcast_id = user_data.get('delete_broadcast_id')
    if not broadcast_id:
        logger.warning(f"handle_broadcast_delete_confirm: broadcast_id отсутствует для user_id={user_id}")
        await state.clear()
        text = escape_message_parts("❌ Ошибка: ID рассылки не указан.", version=2)
        await query.message.edit_text(
            text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if query.data == f"confirm_delete_{broadcast_id}":
        try:
            async with aiosqlite.connect(DATABASE_PATH) as conn:
                c = await conn.cursor()
                await c.execute("DELETE FROM scheduled_broadcasts WHERE id = ?", (broadcast_id,))
                await conn.commit()
                if c.rowcount > 0:
                    logger.info(f"Рассылка ID {broadcast_id} удалена пользователем {user_id}")
                    text = escape_message_parts(
                        f"✅ Рассылка ID `{broadcast_id}` успешно удалена.",
                        version=2
                    )
                else:
                    logger.warning(f"Рассылка ID {broadcast_id} не найдена для удаления пользователем {user_id}")
                    text = escape_message_parts(
                        f"❌ Рассылка ID `{broadcast_id}` не найдена.",
                        version=2
                    )
        except Exception as e:
            logger.error(f"Ошибка удаления рассылки ID {broadcast_id} пользователем {user_id}: {e}", exc_info=True)
            text = escape_message_parts(
                f"❌ Ошибка удаления рассылки ID `{broadcast_id}`: {str(e)}.",
                version=2
            )

        await state.clear()
        await query.message.edit_text(
            text, reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

    elif query.data == "list_broadcasts":
        await state.update_data(awaiting_broadcast_delete_confirm=None, delete_broadcast_id=None, user_id=user_id)
        await list_scheduled_broadcasts(query, state)

async def cancel_broadcast(message: Message, state: FSMContext) -> None:
    """Обрабатывает отмену рассылки."""
    user_id = message.from_user.id
    logger.debug(f"Отмена рассылки для user_id={user_id}")
    await state.clear()
    await message.answer(
        escape_message_parts("✅ Рассылка отменена.", version=2),
        reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Рассылка отменена для user_id={user_id}")

async def cancel(message: Message, state: FSMContext) -> None:
    """Отменяет все активные действия и сбрасывает контекст."""
    user_id = message.from_user.id
    await state.clear()
    text = escape_message_parts("✅ Все действия отменены.", version=2)
    reply_markup = await create_admin_keyboard() if user_id in ADMIN_IDS else await create_main_menu_keyboard(user_id)
    await message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )

# Регистрация обработчиков
@broadcast_router.callback_query(
    lambda c: c.data and (
        c.data.startswith(("broadcast_", "delete_broadcast_", "confirm_delete_", "broadcast_with_payment_", "copy_buttons_")) or
        c.data in ["broadcast_no_media", "broadcast_send_now", "broadcast_schedule", "list_broadcasts", "broadcast_no_buttons", "cancel_broadcast"] or
        c.data.startswith("broadcast_buttons_")
    ) and not c.data.startswith(("delete_user_", "confirm_delete_user_"))
)
async def broadcast_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает callback-запросы для рассылок."""
    callback_data = query.data
    logger.debug(f"broadcast_callback_handler: user_id={query.from_user.id}, callback_data={callback_data}")
    try:
        if callback_data.startswith("delete_broadcast_"):
            await handle_broadcast_manage_action(query, state)
        elif callback_data.startswith("confirm_delete_"):
            await handle_broadcast_delete_confirm(query, state)
        elif callback_data.startswith("copy_buttons_"):
            await handle_broadcast_buttons_count(query, state)
        elif callback_data == "list_broadcasts":
            await list_scheduled_broadcasts(query, state)
        elif callback_data == "broadcast_no_media":
            await handle_broadcast_media(query, state)
        elif callback_data in ["broadcast_send_now", "broadcast_schedule", "cancel_broadcast"]:
            await handle_broadcast_schedule_input(query, state)
        elif callback_data.startswith("broadcast_with_payment_"):
            await handle_broadcast_audience_selection(query, state)
        elif callback_data.startswith("broadcast_buttons_") or callback_data == "broadcast_no_buttons":
            await handle_broadcast_buttons_count(query, state)
        elif callback_data.startswith("broadcast_"):
            await initiate_broadcast(query, state)
        else:
            logger.error(f"Неизвестная callback_data в broadcast_callback_handler: {callback_data} для user_id={query.from_user.id}")
            await query.message.answer(
                escape_message_parts("❌ Неизвестная команда.", version=2),
                reply_markup=await create_admin_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Ошибка в broadcast_callback_handler: {e}", exc_info=True)
        await query.message.answer(
            escape_message_parts(
                "❌ Произошла ошибка.",
                " Попробуйте снова или обратитесь в поддержку @AXIDI_Help",
                version=2
            ),
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()