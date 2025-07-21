# fsm_handlers.py
import re
import logging
from typing import Optional
from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from handlers.commands import start, menu, check_training, debug_avatars, help_command
from handlers.messages import handle_photo, handle_text, handle_admin_text, handle_video
from handlers.broadcast import handle_broadcast_message, handle_broadcast_button_input, handle_broadcast_schedule_time, handle_broadcast_media
from handlers.payments import handle_payments_date_input
from handlers.user_management import handle_balance_change_input, handle_block_reason_input, handle_user_search_input, cancel
from handlers.visualization import handle_activity_dates_input
from handlers.callbacks_admin import handle_admin_custom_prompt
from handlers.callbacks_user import (
    handle_confirm_photo_quality_callback, handle_continue_upload_callback,
    handle_style_selection_callback, handle_style_choice_callback,
    handle_male_styles_page_callback, handle_female_styles_page_callback,
    handle_custom_prompt_manual_callback, handle_generate_with_avatar_callback,
    handle_aspect_ratio_callback, handle_back_to_aspect_selection_callback,
    handle_confirm_assisted_prompt_callback, handle_rating_callback, handle_confirm_video_generation_callback, handle_custom_prompt_llama_callback
)
from generation.videos import handle_video_prompt, handle_video_photo, handle_skip_photo, handle_confirm_video_prompt, handle_edit_video_prompt, handle_edit_video_photo
from config import ADMIN_IDS, DATABASE_PATH
from keyboards import create_main_menu_keyboard
from bot_counter import cmd_bot_name
from utils import clear_user_data
import aiosqlite
from states import BotStates, VideoStates
from generation.training import TrainingStates, handle_confirmation, handle_confirm_training_callback

logger = logging.getLogger(__name__)

# Создание роутера для FSM
fsm_router = Router()

async def debug_payment(message: Message, state: FSMContext) -> None:
    """Отладочная команда для проверки состояния платежей пользователя."""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для использования этой команды.", parse_mode=ParseMode.MARKDOWN)
        return
    
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    target_user_id = int(args[0]) if args and args[0].isdigit() else user_id
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("""
                SELECT user_id, username, first_name, generations_left, avatar_left, 
                       first_purchase, referrer_id, created_at
                FROM users WHERE user_id = ?
            """, (target_user_id,))
            user_data = await c.fetchone()
            
            if not user_data:
                await message.answer(f"❌ Пользователь {target_user_id} не найден", parse_mode=ParseMode.MARKDOWN)
                return
            
            await c.execute("""
                SELECT payment_id, plan, amount, status, created_at
                FROM payments 
                WHERE user_id = ? 
                ORDER BY created_at DESC
                LIMIT 10
            """, (target_user_id,))
            payments = await c.fetchall()
            
            await c.execute("""
                SELECT referrer_id, referred_id, status, created_at, completed_at
                FROM referrals
                WHERE referrer_id = ? OR referred_id = ?
                LIMIT 10
            """, (target_user_id, target_user_id))
            referrals = await c.fetchall()
        
        message_text = f"🔍 Отладка платежей для user_id={target_user_id}\n\n"
        message_text += f"👤 Пользователь:\n"
        message_text += f"• Username: @{user_data['username'] or 'нет'}\n"
        message_text += f"• Имя: {user_data['first_name'] or 'нет'}\n"
        message_text += f"• Фото: {user_data['generations_left']}\n"
        message_text += f"• Аватары: {user_data['avatar_left']}\n"
        message_text += f"• Первая покупка: {'Нет' if user_data['first_purchase'] else 'Была'}\n"
        message_text += f"• Реферер: {user_data['referrer_id'] or 'нет'}\n"
        message_text += f"• Регистрация: {user_data['created_at']}\n\n"
        
        message_text += f"💰 Платежи ({len(payments)}):\n"
        if payments:
            for p in payments[:5]:
                message_text += f"• {p['created_at']}: {p['plan']} - {p['amount']}₽ ({p['status']})\n"
        else:
            message_text += "• Платежей нет\n"
        
        if referrals:
            message_text += f"\n👥 Рефералы:\n"
            for r in referrals[:5]:
                if r['referrer_id'] == target_user_id:
                    message_text += f"• Пригласил: {r['referred_id']} ({r['status']})\n"
                else:
                    message_text += f"• Пригласил его: {r['referrer_id']} ({r['status']})\n"
        
        if len(message_text) > 4000:
            await message.answer(message_text[:4000], parse_mode=ParseMode.MARKDOWN)
            await message.answer(message_text[4000:], parse_mode=ParseMode.MARKDOWN)
        else:
            await message.answer(message_text, parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Ошибка в debug_payment для user_id={user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка выполнения команды: {str(e)}", parse_mode=ParseMode.MARKDOWN)

async def fix_first_purchase(message: Message, state: FSMContext) -> None:
    """Исправляет флаг first_purchase для пользователя."""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды.", parse_mode=ParseMode.MARKDOWN)
        return
    
    args = message.text.split()[1:] if len(message.text.split()) > 1 else []
    if not args or not args[0].isdigit():
        await message.answer("Использование: /fix_first_passed <user_id>", parse_mode=ParseMode.MARKDOWN)
        return
    
    target_user_id = int(args[0])
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            await c.execute("SELECT first_purchase FROM users WHERE user_id = ?", (target_user_id,))
            current_state = await c.fetchone()
            
            if not current_state:
                await message.answer(f"❌ Пользователь {target_user_id} не найден", parse_mode=ParseMode.MARKDOWN)
                return
            
            await c.execute("SELECT COUNT(*) FROM payments WHERE user_id = ? AND status = 'succeeded'", (target_user_id,))
            payment_count = (await c.fetchone())[0]
            
            correct_value = 0 if payment_count > 0 else 1
            current_value = current_state[0]
            
            if current_value == correct_value:
                await message.answer(
                    f"ℹ️ Пользователь {target_user_id}:\n"
                    f"• Платежей: {payment_count}\n"
                    f"• first_purchase: {'Была' if current_value == 0 else 'Нет'} (корректно)\n"
                    f"Изменения не требуются.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await c.execute("UPDATE users SET first_purchase = ? WHERE user_id = ?", (correct_value, target_user_id))
                await conn.commit()
                await message.answer(
                    f"✅ Исправлено для user_id={target_user_id}\n"
                    f"• Платежей: {payment_count}\n"
                    f"• first_purchase изменен: {'Была' if current_value == 0 else 'Нет'} → {'Была' if correct_value == 0 else 'Нет'}\n"
                    f"• Статус: {'Были покупки' if correct_value == 0 else 'Не было покупок'}",
                    parse_mode=ParseMode.MARKDOWN
                )
                
    except Exception as e:
        logger.error(f"Ошибка в fix_first_purchase для user_id={user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {str(e)}", parse_mode=ParseMode.MARKDOWN)

async def fix_all_first_purchase(message: Message, state: FSMContext) -> None:
    """Исправляет флаг first_purchase для всех пользователей с платежами."""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав для этой команды.", parse_mode=ParseMode.MARKDOWN)
        return
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            c = await conn.cursor()
            await c.execute("""
                SELECT DISTINCT u.user_id, u.first_purchase, COUNT(p.payment_id) as payment_count
                FROM users u
                LEFT JOIN payments p ON u.user_id = p.user_id AND p.status = 'succeeded'
                GROUP BY u.user_id
                HAVING (payment_count > 0 AND u.first_purchase = 1) OR (payment_count = 0 AND u.first_purchase = 0)
            """)
            users_to_fix = await c.fetchall()
            
            if not users_to_fix:
                await message.answer("✅ Все пользователи имеют корректные значения first_purchase", parse_mode=ParseMode.MARKDOWN)
                return
            
            fixed_count = 0
            for user_row in users_to_fix:
                uid, current_fp, pcount = user_row
                correct_value = 0 if pcount > 0 else 1
                await c.execute("UPDATE users SET first_purchase = ? WHERE user_id = ?", (correct_value, uid))
                fixed_count += 1
            
            await conn.commit()
            
            await message.answer(
                f"✅ Исправлено {fixed_count} пользователей:\n"
                f"• Установлен first_purchase = 0 для пользователей с платежами\n"
                f"• Установлен first_purchase = 1 для пользователей без платежей",
                parse_mode=ParseMode.MARKDOWN
            )
            
    except Exception as e:
        logger.error(f"Ошибка в fix_all_first_purchase для user_id={user_id}: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {str(e)}", parse_mode=ParseMode.MARKDOWN)

# Обновляем регистрацию обработчиков
fsm_router.message(Command("cancel"))(cancel)
fsm_router.message(Command("start"))(start)
fsm_router.message(Command("menu"))(menu)
fsm_router.message(Command("help"))(help_command)
fsm_router.message(Command("check_training"))(check_training)
fsm_router.message(Command("debug_avatars"))(debug_avatars)
fsm_router.message(Command("botname"))(cmd_bot_name)
fsm_router.message(Command("debug_payment"))(debug_payment)
fsm_router.message(Command("fix_first_purchase"))(fix_first_purchase)
fsm_router.message(Command("fix_all_first_purchase"))(fix_all_first_purchase)

# Обработчик для видео
fsm_router.message(
    lambda message: message.content_type == ContentType.VIDEO
)(handle_video)

# Обработчик для фото в состоянии AWAITING_BROADCAST_MEDIA_CONFIRM
fsm_router.message(
    StateFilter(BotStates.AWAITING_BROADCAST_MEDIA_CONFIRM),
    lambda message: message.content_type == ContentType.PHOTO and message.from_user.id in ADMIN_IDS
)(handle_broadcast_media)

# Обработчик для фото в состоянии AWAITING_CONFIRM_QUALITY
fsm_router.message(
    StateFilter(BotStates.AWAITING_CONFIRM_QUALITY),
    lambda message: message.content_type == ContentType.PHOTO
)(handle_photo)

# Обработчик для текстовых сообщений в состоянии AWAITING_BROADCAST_MESSAGE
fsm_router.message(
    StateFilter(BotStates.AWAITING_BROADCAST_MESSAGE),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_broadcast_message)

# Обработчик для текстовых сообщений в состоянии AWAITING_BROADCAST_SCHEDULE
fsm_router.message(
    StateFilter(BotStates.AWAITING_BROADCAST_SCHEDULE),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_broadcast_schedule_time)

# Обработчик для текстовых сообщений в состоянии AWAITING_BROADCAST_BUTTON_INPUT
fsm_router.message(
    StateFilter(BotStates.AWAITING_BROADCAST_BUTTON_INPUT),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_broadcast_button_input)

# Обработчик для текстовых сообщений в состоянии AWAITING_PAYMENT_DATES
fsm_router.message(
    StateFilter(BotStates.AWAITING_PAYMENT_DATES),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_payments_date_input)

# Обработчик для текстовых сообщений в состоянии AWAITING_BALANCE_CHANGE
fsm_router.message(
    StateFilter(BotStates.AWAITING_BALANCE_CHANGE),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_balance_change_input)

# Обработчик для текстовых сообщений в состоянии AWAITING_ACTIVITY_DATES
fsm_router.message(
    StateFilter(BotStates.AWAITING_ACTIVITY_DATES),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_activity_dates_input)

# Обработчик для текстовых сообщений в состоянии AWAITING_BLOCK_REASON
fsm_router.message(
    StateFilter(BotStates.AWAITING_BLOCK_REASON),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_block_reason_input)

# Обработчик для текстовых сообщений в состоянии AWAITING_ADMIN_PROMPT
fsm_router.message(
    StateFilter(BotStates.AWAITING_ADMIN_PROMPT),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_admin_custom_prompt)

# Обработчик для текстовых сообщений в состоянии AWAITING_USER_SEARCH
fsm_router.message(
    StateFilter(BotStates.AWAITING_USER_SEARCH),
    lambda message: message.content_type == ContentType.TEXT and message.from_user.id in ADMIN_IDS
)(handle_user_search_input)

# Обработчик для текстовых сообщений в состоянии AWAITING_CONFIRMATION
fsm_router.message(
    StateFilter(TrainingStates.AWAITING_CONFIRMATION)
)(handle_confirmation)

# Обработчик для текстовых сообщений в состоянии AWAITING_VIDEO_PROMPT
fsm_router.message(
    StateFilter(VideoStates.AWAITING_VIDEO_PROMPT),
    lambda message: message.content_type == ContentType.TEXT
)(handle_video_prompt)

# Обработчик для фото в состоянии AWAITING_VIDEO_PHOTO
fsm_router.message(
    StateFilter(VideoStates.AWAITING_VIDEO_PHOTO),
    lambda message: message.content_type == ContentType.PHOTO
)(handle_video_photo)

# Обработчик для команды /skip в состоянии AWAITING_VIDEO_PHOTO
fsm_router.message(
    StateFilter(VideoStates.AWAITING_VIDEO_PHOTO),
    Command("skip")
)(handle_skip_photo)

# Обработчик для админских текстовых команд (например, "123 премиум")
fsm_router.message(
    lambda message: message.from_user.id in ADMIN_IDS and bool(re.match(r'^\d+\s+(премиум|платина|аватар)(?:\s+\d+)?$', message.text))
)(handle_admin_text)

# Обработчик для всех остальных фото (должен быть после специфичных обработчиков)
fsm_router.message(
    lambda message: message.content_type == ContentType.PHOTO
)(handle_photo)

# Обработчик для всех остальных текстовых сообщений (должен быть последним)
fsm_router.message(
    lambda message: message.content_type == ContentType.TEXT
)(handle_text)

# Callback-обработчики остаются без изменений
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_CONFIRM_QUALITY),
    lambda c: c.data and c.data.startswith("confirm_photo_quality")
)(handle_confirm_photo_quality_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_CONFIRM_QUALITY),
    lambda c: c.data and c.data.startswith("continue_upload")
)(handle_continue_upload_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data and c.data.startswith(("select_new_male_avatar_styles", "select_new_female_avatar_styles"))
)(handle_style_selection_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data and c.data.startswith("style_")
)(handle_style_choice_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data and c.data.startswith("male_styles_page_")
)(handle_male_styles_page_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data and c.data.startswith("female_styles_page_")
)(handle_female_styles_page_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data == "page_info"
)(handle_style_selection_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data == "enter_custom_prompt_manual"
)(handle_custom_prompt_manual_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data == "enter_custom_prompt_llama"
)(handle_custom_prompt_llama_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data and c.data.startswith("aspect_")
)(handle_aspect_ratio_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data == "back_to_aspect_selection"
)(handle_back_to_aspect_selection_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data == "confirm_assisted_prompt"
)(handle_confirm_assisted_prompt_callback)
fsm_router.callback_query(
    StateFilter(BotStates.AWAITING_STYLE_SELECTION),
    lambda c: c.data and c.data.startswith("rate_")
)(handle_rating_callback)
fsm_router.callback_query(
    StateFilter(VideoStates.AWAITING_VIDEO_CONFIRMATION),
    lambda c: c.data == "confirm_video_prompt"
)(handle_confirm_video_prompt)
fsm_router.callback_query(
    StateFilter(VideoStates.AWAITING_VIDEO_CONFIRMATION),
    lambda c: c.data == "edit_video_prompt"
)(handle_edit_video_prompt)
fsm_router.callback_query(
    StateFilter(VideoStates.AWAITING_VIDEO_CONFIRMATION),
    lambda c: c.data == "edit_video_photo"
)(handle_edit_video_photo)
fsm_router.callback_query(
    lambda c: c.data == "generate_with_avatar"
)(handle_generate_with_avatar_callback)
fsm_router.callback_query(
    StateFilter(TrainingStates.AWAITING_CONFIRMATION),
    lambda c: c.data in ["confirm_start_training", "user_profile"]
)(handle_confirm_training_callback)

def setup_conversation_handler(dp: Router) -> None:
    """Настройка роутера FSM."""
    dp.include_router(fsm_router)
    logger.debug("Роутер FSM настроен")