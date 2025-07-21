# handlers/messages.py
import aiosqlite
import os
import uuid
import re
import time
import logging
from typing import Optional
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Router
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from transliterate import translit
from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback, create_payment_link, get_tariff_text, safe_escape_markdown
from config import ADMIN_IDS, TARIFFS, DATABASE_PATH
from generation_config import IMAGE_GENERATION_MODELS
from database import (
    check_database_user, update_user_credits, add_resources_on_payment,
    log_generation, search_users_by_query, is_user_blocked, delete_user_activity
)
from keyboards import (
    create_main_menu_keyboard, create_subscription_keyboard,
    create_training_keyboard, create_confirmation_keyboard,
    create_admin_keyboard, create_broadcast_keyboard,
    create_aspect_ratio_keyboard, create_photo_generate_menu_keyboard,
    create_video_generate_menu_keyboard, create_user_profile_keyboard, create_back_keyboard
)
from generation.training import start_training
from generation.videos import generate_video
from llama_helper import generate_assisted_prompt
from handlers.commands import menu
from handlers.broadcast import broadcast_message_admin, broadcast_to_paid_users, broadcast_to_non_paid_users
from handlers.admin_panel import admin_panel
from states import BotStates, VideoStates
from handlers.photo_transform import PhotoTransformStates
logger = logging.getLogger(__name__)

async def notify_startup() -> None:
    """Уведомляет админов о запуске бота."""
    bot = Bot.get_current()
    for admin_id in ADMIN_IDS:
        try:
            await send_message_with_fallback(
                bot, admin_id,
                escape_md("✅ Бот успешно запущен!", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Уведомление о запуске отправлено админу {admin_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")

async def send_daily_payments_report(bot: Bot) -> None:
    """Отправляет ежедневный отчет о платежах админам."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            await c.execute("""
                SELECT user_id, plan, amount, created_at
                FROM payments
                WHERE DATE(created_at) = ? AND status = 'succeeded'
            """, (yesterday,))
            payments = await c.fetchall()

        if not payments:
            report_text = escape_md(f"📊 Отчет за `{yesterday}`: Платежей не было.", version=2)
        else:
            total_amount = sum(p['amount'] for p in payments)
            report_text = (
                escape_md(f"📊 Отчет за `{yesterday}`:\n\n", version=2) +
                escape_md(f"Всего платежей: `{len(payments)}`\n", version=2) +
                escape_md(f"Общая сумма: `{total_amount:.2f}` RUB\n\n", version=2)
            )
            for p in payments[:5]:
                report_text += escape_md(f"• User `{p['user_id']}`: `{p['plan']}` - `{p['amount']}`₽ (`{p['created_at']}`)\n", version=2)
            if len(payments) > 5:
                report_text += escape_md(f"• И еще `{len(payments) - 5}` платежей...", version=2)

        for admin_id in ADMIN_IDS:
            await send_message_with_fallback(
                bot, admin_id, report_text, parse_mode=ParseMode.MARKDOWN_V2
            )
        logger.info(f"Ежедневный отчет о платежах отправлен за {yesterday}")
    except Exception as e:
        logger.error(f"Ошибка отправки отчета о платежах: {e}", exc_info=True)

async def send_welcome_message(bot: Bot) -> None:
    """Отправляет приветственное сообщение новым пользователям."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            time_threshold = (datetime.now() - timedelta(minutes=2)).strftime('%Y-%m-%d %H:%M:%S')
            await c.execute("""
                SELECT user_id, username, first_name
                FROM users
                WHERE created_at >= ? AND welcome_sent = 0
            """, (time_threshold,))
            new_users = await c.fetchall()

        if not new_users:
            logger.debug("Нет новых пользователей для приветственного сообщения")
            return

        welcome_text = (
            "👋 Добро пожаловать в PixelPie_AI!\n\n"
            "🎨 Создавайте уникальные фото и видео с помощью нашей нейросети!\n"
            "📸 Загрузите свои фото и начните генерировать шедевры.\n"
            "💡 Используйте /menu для доступа ко всем функциям.\n\n"
            "🚀 Попробуйте прямо сейчас!"
        )

        for user in new_users:
            user_id = user['user_id']
            try:
                await send_message_with_fallback(
                    bot, user_id, escape_md(welcome_text, version=2),
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                async with aiosqlite.connect(DATABASE_PATH) as conn:
                    c = await conn.cursor()
                    await c.execute("UPDATE users SET welcome_sent = 1 WHERE user_id = ?", (user_id,))
                    await conn.commit()
                logger.info(f"Приветственное сообщение отправлено user_id={user_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки приветственного сообщения user_id={user_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка обработки новых пользователей: {e}", exc_info=True)

async def process_scheduled_broadcasts(bot: Bot) -> None:
    """Обрабатывает запланированные рассылки."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            await c.execute("""
                SELECT id, message_text, media_type, media_id, target_group, scheduled_time
                FROM scheduled_broadcasts
                WHERE scheduled_time <= ? AND status = 'pending'
            """, (current_time,))
            broadcasts = await c.fetchall()

        if not broadcasts:
            logger.debug("Нет запланированных рассылок для обработки")
            return

        for broadcast in broadcasts:
            broadcast_id = broadcast['id']
            message_text = broadcast['message_text']
            media_type = broadcast['media_type']
            media_id = broadcast['media_id']
            target_group = broadcast['target_group']

            try:
                if target_group == 'all':
                    await broadcast_message_admin(None, message_text, None, media_type, media_id, broadcast_id=broadcast_id)
                elif target_group == 'paid':
                    await broadcast_to_paid_users(None, message_text, None, media_type, media_id, broadcast_id=broadcast_id)
                elif target_group == 'non_paid':
                    await broadcast_to_non_paid_users(None, message_text, None, media_type, media_id, broadcast_id=broadcast_id)

                async with aiosqlite.connect(DATABASE_PATH) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET status = 'completed' WHERE id = ?",
                        (broadcast_id,)
                    )
                    await conn.commit()
                logger.info(f"Рассылка {broadcast_id} выполнена для группы {target_group}")

            except Exception as e:
                logger.error(f"Ошибка выполнения рассылки {broadcast_id}: {e}", exc_info=True)
                async with aiosqlite.connect(DATABASE_PATH) as conn:
                    c = await conn.cursor()
                    await c.execute(
                        "UPDATE scheduled_broadcasts SET status = 'failed' WHERE id = ?",
                        (broadcast_id,)
                    )
                    await conn.commit()
                for admin_id in ADMIN_IDS:
                    await send_message_with_fallback(
                        bot, admin_id,
                        escape_md(f"⚠️ Ошибка выполнения рассылки ID `{broadcast_id}`: {str(e)}", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )

    except Exception as e:
        logger.error(f"Ошибка обработки запланированных рассылок: {e}", exc_info=True)

async def handle_photo(message: Message, state: FSMContext) -> None:
    """Обработка загруженных фото."""
    user_id = message.from_user.id
    bot = message.bot

    if await is_user_blocked(user_id):
        await send_message_with_fallback(
            bot, user_id, escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался отправить фото")
        return

    if not message.photo:
        logger.warning(f"handle_photo вызван без фото для user_id={user_id}")
        return
    
    photo_file_id = message.photo[-1].file_id
    logger.info(f"Получено фото от user_id={user_id}, file_id={photo_file_id}")
    user_data = await state.get_data()
    current_state = await state.get_state()
    logger.debug(f"handle_photo: user_id={user_id}, state={current_state}, user_data={user_data}")
    
    if current_state == PhotoTransformStates.waiting_for_photo:
        from handlers.photo_transform import handle_photo as handle_transform_photo
        await handle_transform_photo(message, state)
        return

    # Обработка фото от админа в состоянии рассылки
    if user_id in ADMIN_IDS and current_state == BotStates.AWAITING_BROADCAST_MEDIA_CONFIRM:
        from handlers.broadcast import handle_broadcast_media
        await handle_broadcast_media(message, state)
        return

    # Обработка фото от админа для рассылки или чата с пользователем
    if user_id in ADMIN_IDS and user_data.get('awaiting_chat_message'):
        await state.update_data(admin_media_type='photo', admin_media_id=photo_file_id)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("✅ Фото получено. Теперь отправьте текст сообщения или нажмите 'Отправить без текста'.", version=2),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data=f"user_actions_{user_data.get('awaiting_chat_message')}")]]
            ),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if user_data.get('training_step') in ['upload_photos', 'confirm_training']:
        from generation.training import handle_training_photos
        await handle_training_photos(message, state)
        return

    elif user_data.get('waiting_for_photo') and user_data.get('generation_type') == 'prompt_based':
        await handle_prompt_based_photo(message, state, photo_file_id)
        return

    elif user_data.get('generation_type') == 'photo_to_photo' and user_data.get('waiting_for_photo'):
        await handle_photo_to_photo_reference(message, state, photo_file_id)
        return

    elif user_data.get('generation_type') == 'photo_to_photo' and user_data.get('waiting_for_mask'):
        await handle_photo_to_photo_mask(message, state, photo_file_id)
        return

    elif user_data.get('awaiting_video_photo') or current_state == VideoStates.AWAITING_VIDEO_PHOTO:
        from generation.videos import handle_video_photo
        await handle_video_photo(message, state)
        return
    
    else:
        logger.info(f"Фото от user_id={user_id} получено вне контекста.")
        await send_message_with_fallback(
            bot, user_id,
            escape_md("📸 Я получил твое фото! Если ты хотел что-то сделать с ним, выбери опцию в /menu.", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_video(message: Message, state: FSMContext) -> None:
    """Обработка загруженных видео."""
    user_id = message.from_user.id
    bot = message.bot

    if await is_user_blocked(user_id):
        await send_message_with_fallback(
            bot, user_id, escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался отправить видео")
        return

    if not message.video:
        logger.warning(f"handle_video вызван без видео для user_id={user_id}")
        return
    
    video_file_id = message.video.file_id
    logger.info(f"Получено видео от user_id={user_id}, file_id={video_file_id}")

    user_data = await state.get_data()
    current_state = await state.get_state()
    logger.debug(f"handle_video: user_id={user_id}, state={current_state}, user_data={user_data}")

    if user_id in ADMIN_IDS and (user_data.get('awaiting_broadcast_media_confirm') or current_state == BotStates.AWAITING_BROADCAST_MEDIA_CONFIRM):
        from handlers.broadcast import handle_broadcast_media
        await handle_broadcast_media(message, state)
        return
    
    if user_id in ADMIN_IDS and (user_data.get('awaiting_broadcast_message') or current_state == BotStates.AWAITING_BROADCAST_MESSAGE):
        await state.update_data(admin_media_type='video', admin_media_id=video_file_id)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("✅ Видео получено. Теперь отправьте текст сообщения.", version=2),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_BROADCAST_MESSAGE)
        return
    
    if user_id in ADMIN_IDS and user_data.get('awaiting_chat_message'):
        await state.update_data(admin_media_type='video', admin_media_id=video_file_id)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("✅ Видео получено. Теперь отправьте текст сообщения или нажмите 'Отправить без текста'.", version=2),
            reply_markup=await create_broadcast_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    else:
        logger.info(f"Видео от user_id={user_id} получено вне контекста.")
        await send_message_with_fallback(
            bot, user_id,
            escape_md("🎥 Я получил твое видео! Для создания AI-видео (Kling 2.1) используй /menu → Сгенерировать → AI-видео (Kling 2.1).", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_training_photo(message: Message, state: FSMContext, photo_file_id: str) -> None:
    """Обработка фото для обучения аватара."""
    user_id = message.from_user.id
    bot = message.bot
    logger.warning(f"handle_training_photo устарела, перенаправление в training.handle_training_photos для user_id={user_id}")
    from generation.training import handle_training_photos
    await handle_training_photos(message, state)

async def handle_prompt_based_photo(message: Message, state: FSMContext, photo_file_id: str) -> None:
    """Обработка фото для prompt_based генерации."""
    user_id = message.from_user.id
    bot = message.bot
    
    try:
        photo_file = await bot.get_file(photo_file_id)
        uploads_dir = f"generated/{user_id}"
        os.makedirs(uploads_dir, exist_ok=True)
        photo_path = os.path.join(uploads_dir, f"prompt_photo_{uuid.uuid4()}.jpg")
        await bot.download_file(photo_file.file_path, photo_path)
        
        from generation.images import upload_image_to_replicate
        image_url = await upload_image_to_replicate(photo_path)
        
        user_data = await state.get_data()
        await state.update_data(
            photo_path=photo_path,
            reference_image_url=image_url,
            waiting_for_photo=False,
            original_generation_type=user_data.get('generation_type', 'prompt_based'),
            original_model_key=user_data.get('model_key')
        )
        
        prompt_text = escape_md(
            "✅ Фото загружено и обработано.\n\n"
            "📝 Теперь введите описание (промпт) того, что хотите получить:",
            version=2
        )
        await send_message_with_fallback(
            bot, user_id, prompt_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад в меню генерации", callback_data="generate_menu")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        await state.update_data(waiting_for_custom_prompt_photo=True)
        
    except Exception as e:
        logger.error(f"Ошибка загрузки фото для prompt_based генерации user_id={user_id}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("❌ Ошибка при обработке фото. Попробуйте еще раз.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_photo_to_photo_reference(message: Message, state: FSMContext, photo_file_id: str) -> None:
    """Обработка референсного фото для photo-to-photo."""
    user_id = message.from_user.id
    bot = message.bot
    
    try:
        photo_file = await bot.get_file(photo_file_id)
        uploads_dir = f"generated/{user_id}"
        os.makedirs(uploads_dir, exist_ok=True)
        p2p_photo_path = os.path.join(uploads_dir, f"p2p_ref_{uuid.uuid4()}.jpg")
        await bot.download_file(photo_file.file_path, p2p_photo_path)
        
        from generation.images import upload_image_to_replicate
        image_url = await upload_image_to_replicate(p2p_photo_path)
        
        await state.update_data(
            photo_path=p2p_photo_path,
            reference_image_url=image_url,
            waiting_for_photo=False
        )
        
        p2p_text = escape_md(
            "✅ Фото-референс загружено.\n\n"
            "📝 Введи описание (промпт) для изменений или нажми 'Пропустить промпт' для копирования стиля.",
            version=2
        )
        await send_message_with_fallback(
            bot, user_id, p2p_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭️ Пропустить промпт", callback_data="skip_prompt")],
                [InlineKeyboardButton(text="🔙 Назад в меню генерации", callback_data="generate_menu")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Ошибка загрузки фото-референса для user_id={user_id}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("❌ Ошибка при обработке фото-референса. Попробуй еще раз.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_photo_to_photo_mask(message: Message, state: FSMContext, photo_file_id: str) -> None:
    """Обработка маски для photo-to-photo."""
    user_id = message.from_user.id
    bot = message.bot
    
    try:
        photo_file = await bot.get_file(photo_file_id)
        uploads_dir = f"generated/{user_id}"
        os.makedirs(uploads_dir, exist_ok=True)
        p2p_mask_path = os.path.join(uploads_dir, f"p2p_mask_{uuid.uuid4()}.png")
        await bot.download_file(photo_file.file_path, p2p_mask_path)
        
        await state.update_data(
            mask_path=p2p_mask_path,
            waiting_for_mask=False,
            prompt="copy"
        )
        
        await send_message_with_fallback(
            bot, user_id,
            escape_md("✅ Маска загружена. Начинаю обработку изображения...", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        from handlers.callbacks_user import ask_for_aspect_ratio_callback
        await ask_for_aspect_ratio_callback(message, state)
        
    except Exception as e:
        logger.error(f"Ошибка загрузки маски для user_id={user_id}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("❌ Ошибка при обработке маски. Попробуй еще раз или пропусти этот шаг.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_text(message: Message, state: FSMContext) -> None:
    """Обработка текстовых сообщений."""
    user_id = message.from_user.id
    bot = message.bot
    text = message.text.strip()
    logger.info(f"Текст от user_id={user_id}: '{text[:50]}...'")

    if await is_user_blocked(user_id):
        await send_message_with_fallback(
            bot, user_id,
            escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался отправить текст: {text}")
        return

    user_data = await state.get_data()
    current_state = await state.get_state()
    logger.debug(f"handle_text: user_id={user_id}, state={current_state}, user_data={user_data}")

    # Проверяем состояния FSM в порядке приоритета
    if user_data.get('waiting_for_custom_prompt_manual'):
        await handle_manual_prompt_input(message, state, text)
        return

    if user_data.get('waiting_for_video_prompt') or current_state == VideoStates.AWAITING_VIDEO_PROMPT:
        from generation.videos import handle_video_prompt
        await handle_video_prompt(message, state)
        return

    if user_data.get('waiting_for_custom_prompt_llama'):
        await handle_llama_prompt_input(message, state, text)
        return

    if user_data.get('waiting_for_custom_prompt_photo'):
        await handle_custom_prompt_photo_input(message, state, text)
        return

    if user_data.get('training_step') == 'enter_avatar_name':
        from generation.training import handle_avatar_name
        await handle_avatar_name(message, state)
        return

    if user_id in ADMIN_IDS and current_state == BotStates.AWAITING_BROADCAST_MESSAGE:
        from handlers.broadcast import handle_broadcast_message
        await handle_broadcast_message(message, state)
        return

    if user_id in ADMIN_IDS and current_state == BotStates.AWAITING_BROADCAST_SCHEDULE:
        from handlers.broadcast import handle_broadcast_schedule_time
        await handle_broadcast_schedule_time(message, state)
        return

    if user_id in ADMIN_IDS and user_data.get('awaiting_chat_message'):
        target_user_id = user_data['awaiting_chat_message']
        media_type = user_data.get('admin_media_type')
        media_id = user_data.get('admin_media_id')
        await state.update_data(admin_user_id=user_id)
        await handle_admin_chat_message(bot, state, target_user_id, text, media_type, media_id)
        await state.update_data(
            awaiting_chat_message=None,
            admin_media_type=None,
            admin_media_id=None,
            admin_user_id=None
        )
        return

    if user_id in ADMIN_IDS and user_data.get('awaiting_balance_change'):
        from handlers.user_management import handle_balance_change_input
        await handle_balance_change_input(message, state)
        return

    if user_id in ADMIN_IDS and user_data.get('awaiting_block_reason'):
        from handlers.user_management import handle_block_reason_input
        await handle_block_reason_input(message, state)
        return

    if user_id in ADMIN_IDS and user_data.get('awaiting_user_search'):
        from handlers.user_management import handle_user_search_input
        await handle_user_search_input(message, state)
        return

    if user_data.get('awaiting_email'):
        await handle_email_input(message, state, text)
        return

    if user_data.get('awaiting_email_change'):
        await handle_email_change_input(message, state, text)
        return

    # Проверяем, действует ли администратор как обычный пользователь
    acting_as_user = user_data.get('acting_as_user', False)
    if user_id in ADMIN_IDS and not acting_as_user:
        await handle_admin_text(message, state)
        return

    logger.info(f"Текст от user_id={user_id} ('{text[:50]}...') не соответствует ни одному ожидаемому вводу.")
    await send_message_with_fallback(
        bot, user_id,
        escape_md("👋 Используйте /menu для доступа к функциям бота.", version=2),
        reply_markup=await create_main_menu_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_email_input(message: Message, state: FSMContext, email: str = None) -> None:
    """Обработка ввода email для оплаты."""
    user_id = message.from_user.id
    bot = message.bot
    if email is None:
        email = message.text.strip()
    logger.info(f"Текст от user_id={user_id}: '{email}'...")

    user_data = await state.get_data()
    payment_amount = user_data.get('payment_amount')
    payment_description = user_data.get('payment_description')
    tariff_key = user_data.get('payment_tariff_key')

    if not payment_amount or not payment_description or not tariff_key:
        logger.error(f"Отсутствуют данные платежа для user_id={user_id}: {user_data}")
        await state.clear()
        await send_message_with_fallback(
            bot,
            user_id,
            safe_escape_markdown("❌ Ошибка: данные платежа отсутствуют. Начните заново.", version=2),
            reply_markup=await create_subscription_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        logger.warning(f"Некорректный email от user_id={user_id}: {email}")
        await send_message_with_fallback(
            bot,
            user_id,
            safe_escape_markdown("❌ Пожалуйста, введите корректный email.", version=2),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад к пакетам", callback_data="subscribe")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        # Сохраняем email в базе данных
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            await conn.execute(
                "UPDATE users SET email = ? WHERE user_id = ?",
                (email, user_id)
            )
            await conn.commit()
        logger.info(f"Email `{email}` сохранен для user_id={user_id}")

        # Проверяем конфигурацию YooKassa
        bot_username = (await bot.get_me()).username
        payment_url = await create_payment_link(user_id, email, payment_amount, payment_description, bot_username)
        subscription_data = await check_database_user(user_id)
        is_first_purchase = bool(subscription_data[5]) if subscription_data and len(subscription_data) > 5 else True
        bonus_text = " (+ 1 аватар в подарок!)" if is_first_purchase and TARIFFS[tariff_key].get("photos", 0) > 0 else ""

        # Формируем текст с явным экранированием суммы
        payment_amount_str = f"{float(payment_amount):.2f}".replace(".", "\\.")  # Явно экранируем точку
        text_parts = [
            "💳 Оплата пакета\n",
            f"✨ Вы выбрали: {safe_escape_markdown(payment_description, version=2)}{safe_escape_markdown(bonus_text, version=2)}\n",
            f"💰 Сумма: {payment_amount_str} RUB\n\n",
            f"🔗 [Нажмите здесь для безопасной оплаты через YooKassa]({payment_url})\n\n",
            "_После успешной оплаты ресурсы будут начислены автоматически._"
        ]
        payment_text = "".join(text_parts)
        logger.debug(f"handle_email_input: сформирован payment_text для user_id={user_id}: {payment_text[:200]}...")

        # Отправляем сообщение с использованием send_message_with_fallback
        await send_message_with_fallback(
            bot,
            user_id,
            payment_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад к пакетам", callback_data="subscribe")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Платежная ссылка отправлена для user_id={user_id}: {payment_url}")

        # Очищаем состояние
        await state.clear()
        await state.update_data(user_id=user_id)

    except Exception as e:
        logger.error(f"Ошибка обработки email для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await send_message_with_fallback(
            bot,
            user_id,
            safe_escape_markdown("❌ Ошибка при сохранении email. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_subscription_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

async def handle_email_change_input(message: Message, state: FSMContext, email: str) -> None:
    """Обработка изменения email в профиле."""
    user_id = message.from_user.id
    bot = message.bot

    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        logger.warning(f"Некорректный email от user_id={user_id}: {email}")
        await message.answer(
            escape_md("❌ Пожалуйста, введите корректный email.", version=2),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В личный кабинет", callback_data="user_profile")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            await conn.execute(
                "UPDATE users SET email = ? WHERE user_id = ?",
                (email, user_id)
            )
            await conn.commit()
        logger.info(f"Email изменен на `{email}` для user_id={user_id}")
        await state.clear()
        await message.answer(
            escape_md("✅ Email успешно изменен!", version=2),
            reply_markup=await create_user_profile_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Ошибка изменения email для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await message.answer(
            escape_md("❌ Ошибка при изменении email. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_user_profile_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_manual_prompt_input(message: Message, state: FSMContext, text: str) -> None:
    """Обработка ручного ввода промпта для фото- или видеогенерации."""
    user_id = message.from_user.id
    bot = message.bot

    if not text or len(text.strip()) < 5:
        logger.warning(f"Слишком короткий промпт от user_id={user_id}: {text}")
        user_data = await state.get_data()
        back_callback = "video_generate_menu" if user_data.get('generation_type') == 'ai_video_v2_1' else "back_to_style_selection"
        await message.answer(
            escape_md("❌ Промпт слишком короткий. Введите описание не менее 5 символов.", version=2),
            reply_markup=await create_back_keyboard(back_callback),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    user_data = await state.get_data()
    generation_type = user_data.get('generation_type', 'with_avatar')
    model_key = user_data.get('model_key', 'flux-trained')
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

    await state.update_data(
        prompt=text,
        style_name='custom',
        generation_type=generation_type,
        model_key=model_key,
        came_from_custom_prompt=True,
        waiting_for_custom_prompt_manual=False
    )

    if generation_type == 'ai_video_v2_1':
        video_cost = user_data.get('video_cost', 30)
        await state.update_data(video_cost=video_cost, awaiting_video_photo=True)
        await message.answer(
            escape_md(
                f"✅ Промпт сохранен: `{text[:50]}{'...' if len(text) > 50 else ''}`\n\n"
                f"📸 Загрузи фото для генерации видео или пропусти (/skip){' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}.",
                version=2
            ),
            reply_markup=await create_back_keyboard("video_generate_menu"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(VideoStates.AWAITING_VIDEO_PHOTO)
    else:
        await message.answer(
            escape_md(
                f"✅ Промпт сохранен: `{text[:50]}{'...' if len(text) > 50 else ''}`\n\n"
                f"📐 Выбери соотношение сторон для изображения{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:",
                version=2
            ),
            reply_markup=await create_aspect_ratio_keyboard("back_to_style_selection"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_STYLE_SELECTION)

    logger.info(f"Ручной промпт сохранен для user_id={user_id}, target_user_id={target_user_id}: {text[:50]}...")

async def handle_llama_prompt_input(message: Message, state: FSMContext, text: str) -> None:
    """Обработка ввода идеи для AI-помощника."""
    user_id = message.from_user.id
    bot = message.bot
    user_data = await state.get_data()

    if not text or len(text.strip()) < 5:
        logger.warning(f"Слишком короткая идея для AI-помощника от user_id={user_id}: {text}")
        await message.answer(
            escape_md("❌ Идея слишком короткая. Введите описание не менее 5 символов.", version=2),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data="video_generate_menu" if user_data.get('generation_type') == 'ai_video_v2_1' else "back_to_style_selection"
                )]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        # Отправляем начальное сообщение о начале генерации
        await send_message_with_fallback(
            bot, user_id,
            escape_md("⏳ Обращаюсь к AI-помощнику для создания детального промпта... Это может занять некоторое время.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        generation_type = user_data.get('generation_type', 'with_avatar')
        model_key = user_data.get('model_key', 'flux-trained')
        video_cost = user_data.get('video_cost', 30) if generation_type == 'ai_video_v2_1' else None
        gender = user_data.get('selected_gender', None)
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)

        logger.debug(f"handle_llama_prompt_input: user_id={user_id}, generation_type={generation_type}, text={text[:50]}...")

        # Определяем пол на основе текста, если selected_gender отсутствует
        if not gender:
            text_lower = text.lower()
            male_keywords = ['мужчина', 'парень', 'мужик', 'мужской']
            female_keywords = ['женщина', 'девушка', 'женский']
            if any(keyword in text_lower for keyword in male_keywords):
                gender = 'man'
                logger.info(f"Пол определен как 'man' на основе текста: {text}")
            elif any(keyword in text_lower for keyword in female_keywords):
                gender = 'woman'
                logger.info(f"Пол определен как 'woman' на основе текста: {text}")
            else:
                gender = 'person'
                logger.warning(f"Пол не указан для user_id={user_id}, используется значение по умолчанию 'person'")
            await state.update_data(selected_gender=gender)

        # Запускаем генерацию промпта асинхронно
        start_time = time.time()
        task = asyncio.create_task(generate_assisted_prompt(text, gender, generation_type=generation_type))
        
        # Ждём 3 секунды и отправляем промежуточное сообщение, если задача не завершена
        await asyncio.sleep(3)
        if not task.done():
            await send_message_with_fallback(
                bot, user_id,
                escape_md("⏳ Генерация в процессе, пожалуйста, подождите...", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.debug(f"Промежуточное сообщение отправлено для user_id={user_id}, генерация ещё не завершена")

        # Ожидаем завершения генерации
        assisted_prompt = await task
        generation_time = time.time() - start_time
        logger.debug(f"Время генерации промпта для user_id={user_id}: {generation_time:.2f} секунд")

        if not assisted_prompt:
            logger.error(f"Не удалось сгенерировать промпт для user_id={user_id}")
            await message.answer(
                escape_md("❌ Ошибка генерации промпта. Попробуйте снова или введите промпт вручную.", version=2),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data="enter_custom_prompt_manual")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="video_generate_menu" if generation_type == 'ai_video_v2_1' else "back_to_style_selection")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        # Сохраняем сгенерированный промпт
        await state.update_data(
            prompt=assisted_prompt,
            user_input_for_llama=text,
            came_from_custom_prompt=True,
            generation_type=generation_type,
            model_key=model_key,
            selected_gender=gender,
            video_cost=video_cost
        )
        if is_admin_generation:
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=target_user_id,
                message_recipient=user_id,
                generation_target_user=target_user_id,
                original_admin_user=user_id
            )

        # Формируем сообщение с полным промптом, разбивая на части при необходимости
        MAX_MESSAGE_LENGTH = 4000
        prompt_parts = []
        current_part = ""
        for char in assisted_prompt:
            if len(current_part) + len(char) < MAX_MESSAGE_LENGTH - 100:  # Резерв для форматирования
                current_part += char
            else:
                prompt_parts.append(current_part)
                current_part = char
        if current_part:
            prompt_parts.append(current_part)

        # Отправляем сообщения с частями промпта
        for i, part in enumerate(prompt_parts):
            text_msg = escape_md(
                f"🤖 {'Предложенный промпт' if i == 0 else 'Продолжение промпта'}:\n\n"
                f"`{part}`\n\n"
                f"{'Подходит?' if i == len(prompt_parts) - 1 else ''}",
                version=2
            )
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_assisted_prompt")],
                [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_assisted_prompt")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="video_generate_menu" if generation_type == 'ai_video_v2_1' else "back_to_style_selection")]
            ]) if i == len(prompt_parts) - 1 else None
            await send_message_with_fallback(
                bot, user_id,
                text_msg,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Часть {i+1}/{len(prompt_parts)} AI-промпта отправлена для user_id={user_id}: {part[:50]}...")

        await state.set_state(BotStates.AWAITING_STYLE_SELECTION)
    except Exception as e:
        logger.error(f"Ошибка генерации AI-промпта для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await message.answer(
            escape_md("❌ Ошибка при генерации промпта. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

async def handle_custom_prompt_photo_input(message: Message, state: FSMContext, text: str) -> None:
    """Обработка промпта для фото по референсу."""
    user_id = message.from_user.id
    bot = message.bot

    if not text or len(text.strip()) < 5:
        logger.warning(f"Слишком короткий промпт для фото по референсу от user_id={user_id}: {text}")
        await message.answer(
            escape_md("❌ Промпт слишком короткий. Введите описание не менее 5 символов.", version=2),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в меню генерации", callback_data="generate_menu")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    user_data = await state.get_data()
    reference_image_url = user_data.get('reference_image_url')
    photo_path = user_data.get('photo_path')

    if not reference_image_url or not photo_path:
        logger.error(f"Отсутствуют данные для photo_to_photo для user_id={user_id}: {user_data}")
        await state.clear()
        await message.answer(
            escape_md("❌ Ошибка: отсутствует референсное изображение. Начните заново.", version=2),
            reply_markup=await create_photo_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    await state.update_data(
        prompt=text,
        waiting_for_custom_prompt_photo=False
    )

    await message.answer(
        escape_md(f"✅ Промпт сохранен: `{text[:50]}{'...' if len(text) > 50 else '}'}`", version=2),
        reply_markup=await create_aspect_ratio_keyboard("generate_menu"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Промпт для photo_to_photo сохранен для user_id={user_id}: {text[:50]}")

async def handle_admin_text(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state in [BotStates.AWAITING_BROADCAST_MESSAGE, BotStates.AWAITING_BROADCAST_CONFIRM]:
        logger.debug(f"Пропуск handle_admin_text для user_id={message.from_user.id}, state={current_state}")
        return
    logger.info(f"Админский текст от user_id={message.from_user.id}: {message.text[:50]}")
    await message.answer(
        escape_md("👋 Админ, используйте /admin для доступа к панели управления.", version=2),
        reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_admin_chat_message(bot: Bot, state: FSMContext, target_user_id: int, text: str, media_type: str = None, media_id: str = None) -> None:
    """Отправка сообщения пользователю от админа."""
    user_data = await state.get_data()
    admin_user_id = user_data.get('admin_user_id')
    
    try:
        if media_type == 'photo' and media_id:
            await bot.send_photo(
                chat_id=target_user_id,
                photo=media_id,
                caption=escape_md(text, version=2) if text else None,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif media_type == 'video' and media_id:
            await bot.send_video(
                chat_id=target_user_id,
                video=media_id,
                caption=escape_md(text, version=2) if text else None,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await bot.send_message(
                chat_id=target_user_id,
                text=escape_md(text, version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        logger.info(f"Сообщение отправлено от админа `{admin_user_id}` пользователю `{target_user_id}`")

        await send_message_with_fallback(
            bot, admin_user_id,
            escape_md(f"✅ Сообщение успешно отправлено пользователю ID `{target_user_id}`", version=2),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except TelegramBadRequest as e:
        logger.error(f"Ошибка отправки сообщения пользователю `{target_user_id}` от админа `{admin_user_id}`: {e}")
        await send_message_with_fallback(
            bot, admin_user_id,
            escape_md(f"❌ Не удалось отправить сообщение пользователю ID `{target_user_id}`: {str(e)}.", version=2),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка при отправке сообщения пользователю `{target_user_id}`: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, admin_user_id,
            escape_md(f"❌ Произошла ошибка при отправке сообщения: {str(e)}.", version=2),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def award_referral_bonuses(user_id: int, bot: Bot, plan_key: str) -> None:
    """Начисление реферальных бонусов (удалено, так как логика реализована в add_resources_on_payment)."""
    logger.warning(f"Функция award_referral_bonuses устарела и не используется, так как реферальные бонусы начисляются в add_resources_on_payment для user_id={user_id}")
    return