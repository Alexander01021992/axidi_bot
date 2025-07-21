# handlers/user_management.py

import asyncio
import logging
import uuid
import re
from states import BotStates
from datetime import datetime
from typing import List, Dict, Optional
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.filters import Command
from database import (
    check_database_user, get_user_generation_stats, get_user_payments, get_user_trainedmodels,
    get_user_rating_and_registration, get_user_logs, delete_user_activity, block_user_access, is_user_blocked,
    update_user_credits, get_active_trainedmodel, search_users_by_query
)
from config import ADMIN_IDS
from keyboards import create_admin_user_actions_keyboard, create_admin_keyboard
from handlers.utils import (
    escape_message_parts, send_message_with_fallback, truncate_text,
    create_isolated_context, clean_admin_context
)
import aiosqlite
from keyboards import create_main_menu_keyboard

logger = logging.getLogger(__name__)

# Создание роутера для управления пользователями
user_management_router = Router()

async def show_user_actions(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает действия, доступные админу для конкретного пользователя."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        await state.clear()
        text = escape_message_parts("❌ У вас нет прав.", version=2)
        await query.message.answer(
            text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    callback_data = query.data
    logger.debug(f"show_user_actions: user_id={user_id}, callback_data={callback_data}")

    try:
        parts = callback_data.split("_")
        if len(parts) < 3 or parts[0] != "user" or parts[1] != "actions":
            raise ValueError("Неверный формат callback_data")
        target_user_id = int(parts[2])
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка обработки callback_data: {callback_data}, error: {e}")
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка обработки команды.",
            " Попробуйте снова.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(0, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await state.clear()
        text = escape_message_parts(
            f"❌ Пользователь ID `{target_user_id}` не найден.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    g_left, a_left, _, u_name, _, f_purchase_val, email_val, act_avatar_id, f_name, _, _ = target_user_info
    display_name = f_name or u_name or f"ID {target_user_id}"
    username_display = f"(@{u_name})" if u_name and u_name != "Без имени" else ""
    email_display = email_val or "Не указан"

    text_parts = [
        "👤 Детальная информация о пользователе\n\n",
        f"Имя: {display_name} {username_display}\n",
        f"ID: `{target_user_id}`\n",
        f"Email: {email_display}\n",
        "\n💰 Баланс:\n",
        f"  • Печеньки: `{g_left}`\n",
        f"  • Аватары: `{a_left}`\n"
    ]

    gen_stats = await get_user_generation_stats(target_user_id)
    if gen_stats:
        text_parts.append("\n📊 Статистика генераций:\n")
        type_names = {
            'with_avatar': 'Фото с аватаром',
            'photo_to_photo': 'Фото по референсу',
            'ai_video_v2_1': 'AI-видео (Kling 2.1)',
            'train_flux': 'Обучение аватаров',
            'prompt_assist': 'Помощь с промптами'
        }
        for gen_type, count in gen_stats.items():
            text_parts.append(f"  • {type_names.get(gen_type, gen_type)}: `{count}`\n")

    avatars = await get_user_trainedmodels(target_user_id)
    if avatars:
        text_parts.append(f"\n🎭 Аватары ({len(avatars)}):\n")
        for avatar in avatars[:3]:
            if len(avatar) >= 9:
                avatar_id, _, _, status, _, _, _, _, avatar_name = avatar[:9]
                name = avatar_name or f"Аватар {avatar_id}"
                status_icon = "✅" if status == "success" else "⏳" if status in ["pending", "starting", "processing"] else "❌"
                text_parts.append(f"  • {name}: {status_icon} {status}\n")
        if len(avatars) > 3:
            text_parts.append(f"  ...и еще {len(avatars) - 3}\n")

    payments = await get_user_payments(target_user_id)
    if payments:
        total_spent = sum(p[2] for p in payments if p[2])
        text_parts.append(f"\n💳 История платежей ({len(payments)}):\n")
        text_parts.append(f"  • Всего потрачено: `{total_spent:.2f}` RUB\n")
        for _, plan, amount, p_date in payments[:3]:
            date_str = datetime.strptime(str(p_date).split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y') if p_date else "N/A"
            text_parts.append(f"  • `{date_str}`: {plan.capitalize() or 'Неизвестный план'} - `{amount:.2f}` RUB\n")

    text_parts.append("\nВыберите действие:")
    text = escape_message_parts(*text_parts, version=2)
    logger.debug(f"show_user_actions: сформирован текст: {text[:200]}...")

    is_blocked = await is_user_blocked(target_user_id)
    keyboard_buttons = await create_admin_user_actions_keyboard(target_user_id, is_blocked)

    user_data = await state.get_data()
    admin_view_source = user_data.get('admin_view_source', 'admin_stats')
    back_button_text = "🔙 В админ-панель"
    back_button_callback = "admin_panel"
    if admin_view_source == 'admin_stats':
        back_button_text = "🔙 К статистике"
        back_button_callback = "admin_stats"
    elif admin_view_source == 'admin_search_user':
        back_button_text = "🔙 К поиску"
        back_button_callback = "admin_search_user"

    buttons_list = keyboard_buttons.inline_keyboard
    buttons_list.append([InlineKeyboardButton(text=back_button_text, callback_data=back_button_callback)])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons_list)

    await send_message_with_fallback(
        query.bot, user_id, text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id, target_user_id=target_user_id)
    await query.answer()
    logger.debug(f"show_user_actions: завершение для user_id={user_id}, target_user_id={target_user_id}")

async def show_user_profile_admin(query: CallbackQuery, state: FSMContext, target_user_id: int) -> None:
    """Показывает профиль пользователя для админа."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        text = escape_message_parts("❌ У вас нет прав.", version=2)
        await query.message.answer(
            text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await state.clear()
        text = escape_message_parts(
            f"❌ Пользователь ID `{target_user_id}` не найден.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    g_left, a_left, _, u_name, _, f_purchase_val, email_val, act_avatar_id, f_name, _, _ = target_user_info
    display_name = f_name or u_name or f"ID {target_user_id}"
    username_display = f"(@{u_name})" if u_name and u_name != "Без имени" else ""
    email_display = email_val or "Не указан"

    active_avatar_name = "Не выбран"
    if act_avatar_id:
        active_model_data = await get_active_trainedmodel(target_user_id)
        if active_model_data and active_model_data[3] == 'success':
            active_avatar_name = active_model_data[8] or f"Аватар {act_avatar_id}"

    avg_rating, rating_count, registration_date = await get_user_rating_and_registration(target_user_id)
    rating_text = f"⭐ Средний рейтинг: {avg_rating:.2f} ({rating_count} оценок)" if avg_rating else "⭐ Нет оценок"
    registration_text = f"📅 Дата регистрации: {registration_date}" if registration_date else "📅 Не указана"

    payments = await get_user_payments(target_user_id)
    payments_history = "\n_Нет истории покупок._"
    if payments:
        payments_history = "\nПоследние покупки:\n"
        for _, plan, amount, p_date in payments[:3]:
            p_date_formatted = datetime.strptime(str(p_date).split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M') if p_date else "N/A"
            payments_history += f"  • {plan.capitalize() or 'Неизвестный план'} ({amount:.2f} RUB) - {p_date_formatted}\n"

    text = escape_message_parts(
        f"👤 Профиль пользователя: {display_name} {username_display} (ID: `{target_user_id}`)\n\n",
        f"💰 Баланс:\n  📸 Печеньки: `{g_left}`\n  👤 Аватары: `{a_left}`\n\n",
        f"🌟 Активный аватар: {active_avatar_name}\n",
        f"📧 Email: {email_display}\n",
        f"{rating_text}\n",
        f"{registration_text}\n",
        f"🛒 Первая покупка: {'Да' if f_purchase_val else 'Нет'}\n",
        f"{payments_history}",
        version=2
    )
    logger.debug(f"show_user_profile_admin: сформирован текст: {text[:200]}...")

    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id, target_user_id=target_user_id)
    await query.answer()

async def show_user_avatars_admin(query: CallbackQuery, state: FSMContext, target_user_id: int) -> None:
    """Показывает все аватары пользователя для админа."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        text = escape_message_parts("❌ У вас нет прав.", version=2)
        await query.message.answer(
            text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await state.clear()
        text = escape_message_parts(
            f"❌ Пользователь ID `{target_user_id}` не найден.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    display_name = target_user_info[8] or target_user_info[3] or f"ID {target_user_id}"
    text_parts = [f"🖼️ Аватары пользователя {display_name} (ID: `{target_user_id}`)\n\n"]

    avatars = await get_user_trainedmodels(target_user_id)
    if not avatars:
        text_parts.append("_У пользователя нет аватаров._")
    else:
        for avatar in avatars:
            if len(avatar) < 9:
                logger.warning(f"Неполные данные аватара для user_id={target_user_id}: {avatar}")
                continue
            avatar_id, model_id, model_version, status, prediction_id, trigger_word, _, _, avatar_name = avatar[:9]
            name = avatar_name or f"Аватар {avatar_id}"
            status_icon = "✅" if status == "success" else "⏳" if status in ["pending", "starting", "processing"] else "❌"
            text_parts.extend([
                f"{name} (ID: {avatar_id})\n",
                f"  • Статус: {status_icon} {status or 'N/A'}\n",
                f"  • Триггер: `{trigger_word}`\n"
            ])
            if model_id:
                text_parts.append(f"  • Модель: `{model_id}`\n")
            if model_version:
                text_parts.append(f"  • Версия: `{model_version}`\n")
            if prediction_id:
                text_parts.append(f"  • Training ID: `{prediction_id}`\n")
            text_parts.append("\n")

    text = escape_message_parts(*text_parts, version=2)
    logger.debug(f"show_user_avatars_admin: сформирован текст: {text[:200]}...")

    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id, target_user_id=target_user_id)
    await query.answer()

async def show_user_logs(query: CallbackQuery, state: FSMContext, target_user_id: int) -> None:
    """Показывает логи действий пользователя."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        text = escape_message_parts("❌ У вас нет прав.", version=2)
        await query.message.answer(
            text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        logs = await get_user_logs(target_user_id, limit=50)
        if not logs:
            text = escape_message_parts(
                f"📜 Логи для ID `{target_user_id}` не найдены.",
                version=2
            )
            await state.clear()
            await send_message_with_fallback(
                query.bot, user_id, text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        text_parts = [f"📜 Логи пользователя ID `{target_user_id}` (последние 50):\n\n"]
        for log in logs:
            timestamp, action_type, details = log
            timestamp_str = datetime.strptime(str(timestamp).split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
            text_parts.append(f"• `{timestamp_str}`: {action_type} - {truncate_text(str(details), 50)}\n")

        # Разбиваем текст на части, если он слишком длинный
        MAX_MESSAGE_LENGTH = 4000  # Немного меньше лимита Telegram (4096)
        messages = []
        current_message = ""
        for part in text_parts:
            if len(current_message) + len(part) < MAX_MESSAGE_LENGTH:
                current_message += part
            else:
                messages.append(current_message)
                current_message = part
        if current_message:
            messages.append(current_message)

        logger.debug(f"show_user_logs: сформировано {len(messages)} сообщений для user_id={target_user_id}")

        # Отправляем каждую часть сообщения
        for i, message_text in enumerate(messages):
            text = escape_message_parts(message_text, version=2)
            logger.debug(f"show_user_logs: отправка части {i+1}/{len(messages)}, длина={len(text)}")
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ]) if i == len(messages) - 1 else None
            await send_message_with_fallback(
                query.bot, user_id, text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )

    except Exception as e:
        logger.error(f"Ошибка при получении логов для ID {target_user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            "❌ Ошибка получения логов.",
            " Проверьте логи.",
            version=2
        )
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id, target_user_id=target_user_id)
    await query.answer()

async def change_balance_admin(query: CallbackQuery, state: FSMContext) -> None:
    """Инициирует процесс изменения баланса пользователя."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    callback_data = query.data
    logger.debug(f"change_balance_admin: user_id={user_id}, callback_data={callback_data}")

    try:
        parts = callback_data.split("_")
        if len(parts) < 3 or parts[0] != "change" or parts[1] != "balance":
            raise ValueError("Неверный формат callback_data")
        target_user_id = int(parts[2])
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка обработки callback_data: {callback_data}, error: {e}")
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка обработки команды.",
            " Попробуйте снова.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(0, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await state.clear()
        text = escape_message_parts(
            f"❌ Пользователь ID `{target_user_id}` не найден.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    username = target_user_info[3] or "Нет"
    first_name = target_user_info[8] or "Не указано"
    await state.clear()
    await state.update_data(awaiting_balance_change=True, target_user_id=target_user_id, user_id=user_id)
    text = escape_message_parts(
        f"💰 Изменение баланса для пользователя @{username} ({first_name}, ID `{target_user_id}`)\n\n",
        "Введите количество фото или аватаров для добавления/удаления в формате:\n",
        "`+10 фото` или `-1 аватар`\n",
        "Для отмены используйте /cancel.",
        version=2
    )
    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(BotStates.AWAITING_BALANCE_CHANGE)
    await query.answer()

async def handle_balance_change_input(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод изменения баланса."""
    user_id = message.from_user.id
    user_data = await state.get_data()
    if not user_data.get('awaiting_balance_change'):
        logger.warning(f"handle_balance_change_input вызвана без awaiting_balance_change для user_id={user_id}")
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка: действие не ожидается.",
            version=2
        )
        await message.answer(
            text,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    target_user_id = user_data.get('target_user_id')
    await state.update_data(awaiting_balance_change=None, target_user_id=None, user_id=user_id)

    if not target_user_id:
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка: пользователь не указан.",
            version=2
        )
        await message.answer(
            text,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    input_text = message.text.strip()
    try:
        operation = 'increment' if input_text.startswith('+') else 'decrement'
        amount = int(input_text[1:].split()[0])
        resource = input_text.split()[1].lower()
        if resource not in ['фото', 'аватара', 'аватар']:
            raise ValueError("Неверный тип ресурса")

        action = f"{operation}_{'photo' if resource == 'фото' else 'avatar'}"
        success = await update_user_credits(target_user_id, action, amount)
        user_info = await check_database_user(target_user_id)
        if success and user_info:
            text = escape_message_parts(
                f"✅ Баланс ID `{target_user_id}` изменен: {input_text}\n",
                f"Текущий баланс: `{user_info[0]}` печенек, `{user_info[1]}` аватаров",
                version=2
            )
        else:
            text = escape_message_parts(
                f"❌ Не удалось изменить баланс ID `{target_user_id}`.",
                version=2
            )
    except Exception as e:
        logger.error(f"Ошибка обработки ввода баланса для user_id={target_user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            f"❌ Ошибка: {str(e)}.",
            " Пример: `+10 фото`",
            version=2
        )

    await state.clear()
    reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
    ])
    await message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )

async def delete_user_admin(query: CallbackQuery, state: FSMContext, target_user_id: int) -> None:
    """Запрашивает подтверждение удаления пользователя."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    text = escape_message_parts(
        f"⚠️ Подтверждение удаления\n\n",
        f"Вы уверены, что хотите удалить пользователя ID `{target_user_id}`?\n",
        f"Это действие необратимо и удалит все данные пользователя.",
        version=2
    )
    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"confirm_delete_user_{target_user_id}")],
                [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
            ]
        ),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)
    await query.answer()

async def confirm_delete_user(query: CallbackQuery, state: FSMContext, target_user_id: int) -> None:
    """Выполняет удаление пользователя с уведомлением."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    try:
        target_user_info = await check_database_user(target_user_id)
        if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
            text = escape_message_parts(
                f"❌ Пользователь ID `{target_user_id}` не найден.",
                version=2
            )
            await state.clear()
            await send_message_with_fallback(
                query.bot, user_id, text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        try:
            await query.bot.send_message(
                chat_id=target_user_id,
                text=escape_message_parts(
                    "⚠️ Ваш аккаунт был удален администратором.",
                    " Обратитесь в поддержку: @AXIDI_Help",
                    version=2
                ),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Уведомление об удалении отправлено пользователю user_id={target_user_id}")
        except Exception as e_notify:
            logger.warning(f"Не удалось уведомить пользователя {target_user_id} об удалении: {e_notify}")

        success = await delete_user_activity(target_user_id)
        if success:
            text = escape_message_parts(
                f"✅ Пользователь ID `{target_user_id}` успешно удален.",
                version=2
            )
            logger.info(f"Пользователь user_id={target_user_id} удален администратором user_id={user_id}")
        else:
            text = escape_message_parts(
                f"❌ Не удалось удалить пользователя ID `{target_user_id}`.",
                version=2
            )
            logger.error(f"Не удалось удалить пользователя user_id={target_user_id}")

        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Критическая ошибка при удалении пользователя user_id={target_user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            f"❌ Ошибка при удалении пользователя ID `{target_user_id}`: {str(e)}.",
            version=2
        )
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)
    await query.answer()

async def block_user_admin(query: CallbackQuery, state: FSMContext) -> None:
    """Запрашивает подтверждение блокировки/разблокировки пользователя."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    callback_data = query.data
    logger.debug(f"block_user_admin: user_id={user_id}, callback_data={callback_data}")

    try:
        parts = callback_data.split("_")
        if len(parts) < 4 or parts[0] != "block" or parts[1] != "user":
            raise ValueError("Неверный формат callback_data")
        target_user_id = int(parts[2])
        action = parts[3]
        block = (action == "block")
    except (ValueError, IndexError) as e:
        logger.error(f"Ошибка обработки callback_data: {callback_data}, error: {e}")
        text = escape_message_parts(
            "❌ Ошибка обработки команды.",
            " Попробуйте снова.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(0, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await state.clear()
        text = escape_message_parts(
            f"❌ Пользователь ID `{target_user_id}` не найден.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    is_already_blocked = await is_user_blocked(target_user_id)
    if block and is_already_blocked:
        await state.clear()
        text = escape_message_parts(
            f"⚠️ Пользователь ID `{target_user_id}` уже заблокирован.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return
    elif not block and not is_already_blocked:
        await state.clear()
        text = escape_message_parts(
            f"⚠️ Пользователь ID `{target_user_id}` не заблокирован.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    action_text = "заблокировать" if block else "разблокировать"
    action_emoji = "🔒" if block else "🔓"

    await state.clear()
    await state.update_data(block_action={'target_user_id': target_user_id, 'block': block}, user_id=user_id)

    if block:
        await state.update_data(awaiting_block_reason={'target_user_id': target_user_id})
        text = escape_message_parts(
            f"⚠️ Введите текстовую причину блокировки пользователя ID `{target_user_id}`.\n",
            "Введите текст или выберите 'Без причины'.\n",
            "Для отмены используйте /cancel.",
            version=2
        )
        keyboard = [
            [InlineKeyboardButton(text="Без причины", callback_data=f"confirm_block_user_{target_user_id}_block_no_reason")],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BLOCK_REASON)
    else:
        text = escape_message_parts(
            f"⚠️ Подтверждение действия\n\n",
            f"Вы уверены, что хотите {action_text} пользователя ID `{target_user_id}`?",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"{action_emoji} Да, {action_text}", callback_data=f"confirm_block_user_{target_user_id}_unblock")],
                    [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
                ]
            ),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await query.answer()

async def confirm_block_user(query: CallbackQuery, state: FSMContext, bot: Bot, is_fake_query: bool = False) -> None:
    """Выполняет блокировку/разблокировку пользователя."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        if not is_fake_query:
            await query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    user_data = await state.get_data()
    block_action = user_data.get('block_action', {})
    target_user_id = block_action.get('target_user_id')
    block = block_action.get('block', True)
    block_reason = block_action.get('block_reason', "Без причины")

    if query.data:
        callback_data = query.data
        logger.debug(f"confirm_block_user: user_id={user_id}, callback_data={callback_data}")
        try:
            parts = callback_data.split("_")
            if len(parts) < 4 or parts[0] != "confirm" or parts[1] != "block" or parts[2] != "user":
                raise ValueError("Неверный формат callback_data")
            target_user_id = int(parts[3])
            action = parts[4]
            if action == "block" and len(parts) > 5 and parts[5] == "no":
                block_reason = "Без причины"
            elif action == "unblock":
                block = False
        except (ValueError, IndexError) as e:
            logger.error(f"Ошибка обработки callback_data: {callback_data}, error: {e}")
            text = escape_message_parts(
                "❌ Ошибка обработки команды.",
                " Попробуйте снова.",
                version=2
            )
            await send_message_with_fallback(
                bot, user_id, text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            if not is_fake_query:
                await query.answer()
            return

    if not target_user_id:
        logger.error(f"confirm_block_user: target_user_id не указан, block_action={block_action}")
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка: пользователь не указан.",
            version=2
        )
        await send_message_with_fallback(
            bot, user_id, text,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        if not is_fake_query:
            await query.answer()
        return

    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        logger.error(f"confirm_block_user: пользователь ID {target_user_id} не найден")
        await state.clear()
        text = escape_message_parts(
            f"❌ Пользователь ID `{target_user_id}` не найден.",
            version=2
        )
        await send_message_with_fallback(
            bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        if not is_fake_query:
            await query.answer()
        return

    try:
        logger.info(f"Выполняется {'блокировка' if block else 'разблокировка'} для user_id={target_user_id}, причина={block_reason if block else 'N/A'}")
        success = await block_user_access(target_user_id, block, block_reason if block else None)
        if success:
            action_text = "заблокирован" if block else "разблокирован"
            action_emoji = "🔒" if block else "🔓"
            text_parts = [
                f"✅ Пользователь ID `{target_user_id}` {action_text}."
            ]
            if block:
                text_parts.append(f"Причина: {block_reason}")
            text = escape_message_parts(*text_parts, version=2)
            logger.info(f"Пользователь user_id={target_user_id} {action_text} администратором user_id={user_id}")
            try:
                await bot.send_message(
                    chat_id=target_user_id,
                    text=escape_message_parts(
                        f"{action_emoji} Ваш аккаунт был {action_text} администратором.\n",
                        f"{'Причина: ' + block_reason if block else ''}",
                        "Обратитесь в поддержку: @AXIDI_Help",
                        version=2
                    ),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e_notify:
                logger.warning(f"Не удалось уведомить пользователя {target_user_id} о блокировке: {e_notify}")

        else:
            text = escape_message_parts(
                f"❌ Не удалось {'заблокировать' if block else 'разблокировать'} пользователя ID `{target_user_id}`.",
                version=2
            )
            logger.error(f"Не удалось {'заблокировать' if block else 'разблокировать'} пользователя user_id={target_user_id}")

        await state.clear()
        await send_message_with_fallback(
            bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при {'блокировке' if block else 'разблокировке'} пользователя user_id={target_user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            f"❌ Ошибка при {'блокировке' if block else 'разблокировке'} пользователя ID `{target_user_id}`: {str(e)}.",
            version=2
        )
        await state.clear()
        await send_message_with_fallback(
            bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    
    if not is_fake_query:
        await query.answer()
    await state.update_data(user_id=user_id)

async def search_users_admin(query: CallbackQuery, state: FSMContext) -> None:
    """Инициирует поиск пользователей по ID, имени или email."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        text = escape_message_parts("❌ У вас нет прав.", version=2)
        await query.message.answer(
            text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    await state.clear()
    await state.update_data(awaiting_user_search=True, admin_view_source='admin_search_user', user_id=user_id)
    text = escape_message_parts(
        "🔍 Поиск пользователей\n\n",
        "Введите ID, имя, username (с @) или email для поиска.\n",
        "Для отмены используйте /cancel.",
        version=2
    )
    reply_markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
    ])
    await send_message_with_fallback(
        query.bot, user_id, text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Инициирован поиск пользователей для user_id={user_id}")
    await state.set_state(BotStates.AWAITING_USER_SEARCH)

async def handle_user_search_input(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод для поиска пользователей."""
    user_id = message.from_user.id
    logger.debug(f"handle_user_search_input вызвана для user_id={user_id}, текст='{message.text}'")
    
    user_data = await state.get_data()
    if not user_data.get('awaiting_user_search'):
        logger.warning(f"handle_user_search_input вызвана без awaiting_user_search для user_id={user_id}")
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка: действие не ожидается.",
            version=2
        )
        await message.answer(
            text,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    await state.update_data(awaiting_user_search=None, user_id=user_id)
    search_query = message.text.strip()
    logger.info(f"Поиск пользователей для user_id={user_id}, запрос='{search_query}'")

    try:
        users: List[Tuple] = await search_users_by_query(search_query)
        logger.debug(f"Результат поиска для запроса '{search_query}': найдено {len(users)} пользователей")
        logger.debug(f"Данные пользователей: {[f'ID={u[0]}, username={u[1]}, first_name={u[2]}' for u in users]}")
        
        if not users:
            text = escape_message_parts(
                f"❌ Пользователи по запросу '{search_query}' не найдены.",
                version=2
            )
            await state.update_data(admin_view_source='admin_search_user', user_id=user_id)
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="admin_search_user")],
                    [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
                ]
            )
            await message.answer(
                text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"Поиск завершен без результатов для user_id={user_id}")
            return

        text_parts = [f"🔍 Результаты поиска по '{search_query}' (найдено: {len(users)}):\n\n"]
        keyboard_buttons = []
        for user in users[:10]:
            if not isinstance(user, tuple) or len(user) < 5:
                logger.error(f"Неверный формат данных пользователя: ожидается кортеж с 5 элементами, получен {type(user)}, данные={user}")
                continue
            
            u_id, u_name, f_name, generations_left, avatar_left = user
            u_name = u_name or "Без имени"
            f_name = f_name or "Не указано"
            name_display = f_name if f_name != "Не указано" else u_name
            text_parts.append(
                f"👤 {name_display} (@{u_name}) (ID: `{u_id}`)\n"
                f"📸 Генераций: {generations_left} | 🖼 Аватаров: {avatar_left}\n"
            )
            # Экранирование callback_data не требуется, так как это статический текст
            button_text = f"👤 {name_display} (ID: {u_id})"
            if len(button_text) > 64:  # Ограничение Telegram на длину текста кнопки
                button_text = f"👤 {name_display[:20]}... (ID: {u_id})"
            keyboard_buttons.append([
                InlineKeyboardButton(text=button_text, callback_data=f"user_actions_{u_id}")
            ])

        if len(users) > 10:
            text_parts.append(f"\n...и еще {len(users) - 10} пользователей.")

        text = escape_message_parts(*text_parts, version=2)
        logger.debug(f"handle_user_search_input: сформирован текст: {text[:200]}...")

        keyboard_buttons.append([InlineKeyboardButton(text="🔍 Новый поиск", callback_data="admin_search_user")])
        keyboard_buttons.append([InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await state.update_data(admin_view_source='admin_search_user', user_id=user_id)
        await message.answer(
            text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Поиск завершен успешно для user_id={user_id}, найдено {len(users)} пользователей")

    except Exception as e:
        logger.error(f"Ошибка при поиске пользователей для user_id={user_id}, запрос='{search_query}': {e}", exc_info=True)
        text = escape_message_parts(
            f"❌ Ошибка при поиске: {str(e)}.",
            " Попробуйте снова.",
            version=2
        )
        await state.update_data(admin_view_source='admin_search_user', user_id=user_id)
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="admin_search_user")],
                [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
            ]
        )
        await message.answer(
            text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Поиск завершился с ошибкой для user_id={user_id}")

async def confirm_reset_avatar(query: CallbackQuery, state: FSMContext, target_user_id: int) -> None:
    """Подтверждает и выполняет сброс активного аватара пользователя."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await state.clear()
        text = escape_message_parts(
            f"❌ Пользователь ID `{target_user_id}` не найден.",
            version=2
        )
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        success = await update_user_credits(target_user_id, action="set_active_avatar", amount=0)
        if success:
            text = escape_message_parts(
                f"✅ Активный аватар пользователя ID `{target_user_id}` сброшен.",
                version=2
            )
            logger.info(f"Активный аватар пользователя user_id={target_user_id} сброшен администратором user_id={user_id}")
            try:
                await query.bot.send_message(
                    chat_id=target_user_id,
                    text=escape_message_parts(
                        "⚠️ Ваш активный аватар был сброшен администратором.",
                        " Выберите новый аватар в профиле.",
                        version=2
                    ),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e_notify:
                logger.warning(f"Не удалось уведомить пользователя {target_user_id} о сбросе аватара: {e_notify}")
        else:
            text = escape_message_parts(
                f"❌ Не удалось сбросить аватар пользователя ID `{target_user_id}`.",
                version=2
            )
            logger.error(f"Не удалось сбросить аватар пользователя user_id={target_user_id}")

        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при сбросе аватара пользователя user_id={target_user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            f"❌ Ошибка при сбросе аватара ID `{target_user_id}`: {str(e)}.",
            version=2
        )
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await state.update_data(user_id=user_id)
    await query.answer()

async def cancel(message: Message, state: FSMContext) -> None:
    """Отменяет все активные действия и сбрасывает контекст."""
    user_id = message.from_user.id
    await state.clear()
    text = escape_message_parts("✅ Все действия отменены.", version=2)
    reply_markup = await create_admin_keyboard() if user_id in ADMIN_IDS else await create_main_menu_keyboard(user_id)
    await message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.update_data(user_id=user_id)

async def handle_block_reason_input(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод причины блокировки."""
    user_id = message.from_user.id
    bot = message.bot
    logger.debug(f"handle_block_reason_input: user_id={user_id}, message_text='{message.text}'")
    
    if user_id not in ADMIN_IDS:
        logger.warning(f"handle_block_reason_input: user_id={user_id} не является админом")
        text = escape_message_parts("❌ У вас нет прав.", version=2)
        await message.answer(
            text,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    user_data = await state.get_data()
    if not user_data.get('awaiting_block_reason'):
        logger.warning(f"handle_block_reason_input вызвана без awaiting_block_reason для user_id={user_id}")
        await state.clear()
        text = escape_message_parts(
            "❌ Ошибка: действие не ожидается.",
            version=2
        )
        await message.answer(
            text,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    target_user_id = user_data['awaiting_block_reason']['target_user_id']
    await state.update_data(awaiting_block_reason=None, user_id=user_id)

    if not message.text:
        logger.warning(f"handle_block_reason_input: нет текста сообщения для user_id={user_id}")
        text = escape_message_parts(
            f"⚠️ Введите текстовую причину блокировки или выберите 'Без причины'.\n",
            "Для отмены используйте /cancel.",
            version=2
        )
        keyboard = [
            [InlineKeyboardButton(text="Без причины", callback_data=f"confirm_block_user_{target_user_id}_block_no_reason")],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]
        await state.update_data(awaiting_block_reason={'target_user_id': target_user_id}, user_id=user_id)
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BLOCK_REASON)
        return

    reason = message.text.strip()
    if len(reason) > 255:
        logger.warning(f"handle_block_reason_input: причина блокировки слишком длинная ({len(reason)} символов) для user_id={user_id}")
        text = escape_message_parts(
            f"⚠️ Причина должна быть до 255 символов.\n",
            "Попробуйте снова или выберите 'Без причины'.\n",
            "Для отмены используйте /cancel.",
            version=2
        )
        keyboard = [
            [InlineKeyboardButton(text="Без причины", callback_data=f"confirm_block_user_{target_user_id}_block_no_reason")],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]
        await state.update_data(awaiting_block_reason={'target_user_id': target_user_id}, user_id=user_id)
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BLOCK_REASON)
        return

    await state.update_data(block_action={
        'target_user_id': target_user_id,
        'block': True,
        'block_reason': reason
    }, user_id=user_id)
    logger.info(f"Причина блокировки для user_id={target_user_id}: {reason}")

    try:
        fake_query = CallbackQuery(
            id=str(uuid.uuid4()),
            from_user=message.from_user,
            chat_instance=str(uuid.uuid4()),
            message=message,
            data=f"confirm_block_user_{target_user_id}_block"
        )
        fake_query = fake_query.as_(bot)
        await confirm_block_user(fake_query, state, bot, is_fake_query=True)
        logger.debug(f"confirm_block_user вызвана успешно для user_id={target_user_id}")
    except Exception as e:
        logger.error(f"Ошибка при вызове confirm_block_user для user_id={target_user_id}: {e}", exc_info=True)
        text = escape_message_parts(
            f"❌ Ошибка при обработке причины блокировки: {str(e)}.",
            version=2
        )
        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()

# Регистрация обработчиков
@user_management_router.callback_query(
    lambda c: c.data and c.data.startswith((
        'user_actions_', 'view_user_profile_', 'user_avatars_', 'user_logs_', 'change_balance_',
        'delete_user_', 'confirm_delete_user_', 'block_user_', 'confirm_block_user_', 'payments_',
        'visualize_', 'reset_avatar_', 'add_photos_to_user_', 'add_avatar_to_user_', 'chat_with_user_',
        'give_subscription_', 'activity_'
    ))
)
async def user_management_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    callback_data = query.data
    logger.debug(f"user_management_callback_handler: user_id={query.from_user.id}, callback_data={callback_data}")
    try:
        if callback_data.startswith("user_actions_"):
            logger.info(f"Обрабатывается user_actions для user_id={query.from_user.id}, callback_data={callback_data}")
            await show_user_actions(query, state)
        elif callback_data.startswith("view_user_profile_"):
            logger.info(f"Обрабатывается view_user_profile для user_id={query.from_user.id}, callback_data={callback_data}")
            target_user_id = int(callback_data.split("_")[-1])
            await show_user_profile_admin(query, state, target_user_id)
        elif callback_data.startswith("user_avatars_"):
            logger.info(f"Обрабатывается user_avatars для user_id={query.from_user.id}, callback_data={callback_data}")
            target_user_id = int(callback_data.split("_")[-1])
            await show_user_avatars_admin(query, state, target_user_id)
        elif callback_data.startswith("change_balance_"):
            logger.info(f"Обрабатывается change_balance для user_id={query.from_user.id}, callback_data={callback_data}")
            await change_balance_admin(query, state)
        elif callback_data.startswith("user_logs_"):
            logger.info(f"Обрабатывается user_logs для user_id={query.from_user.id}, callback_data={callback_data}")
            target_user_id = int(callback_data.split("_")[-1])
            await show_user_logs(query, state, target_user_id)
        elif callback_data.startswith("delete_user_"):
            logger.info(f"Обрабатывается delete_user для user_id={query.from_user.id}, callback_data={callback_data}")
            target_user_id = int(callback_data.split("_")[-1])
            await delete_user_admin(query, state, target_user_id)
        elif callback_data.startswith("confirm_delete_user_"):
            logger.info(f"Обрабатывается confirm_delete_user для user_id={query.from_user.id}, callback_data={callback_data}")
            target_user_id = int(callback_data.split("_")[-1])
            await confirm_delete_user(query, state, target_user_id)
        elif callback_data.startswith("block_user_"):
            logger.info(f"Обрабатывается block_user для user_id={query.from_user.id}, callback_data={callback_data}")
            await block_user_admin(query, state)
        elif callback_data.startswith("confirm_block_user_"):
            logger.info(f"Обрабатывается confirm_block_user для user_id={query.from_user.id}, callback_data={callback_data}")
            await confirm_block_user(query, state, query.bot, is_fake_query=False)
        elif callback_data.startswith("reset_avatar_"):
            logger.info(f"Обрабатывается reset_avatar для user_id={query.from_user.id}, callback_data={callback_data}")
            target_user_id = int(callback_data.split("_")[-1])
            await confirm_reset_avatar(query, state, target_user_id)
        else:
            logger.warning(f"Неизвестный callback_data: {callback_data} для user_id={query.from_user.id}")
            text = escape_message_parts(
                "❌ Неизвестное действие.",
                " Попробуйте снова.",
                version=2
            )
            await query.message.answer(
                text,
                reply_markup=await create_admin_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Ошибка в user_management_callback_handler: {e}", exc_info=True)
        text = escape_message_parts(
            "❌ Произошла ошибка.",
            " Попробуйте снова или обратитесь в поддержку.",
            version=2
        )
        await query.message.answer(
            text,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )