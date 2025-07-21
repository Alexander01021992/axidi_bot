import aiosqlite
import asyncio
import logging
import os
import requests
import uuid
import random
from aiogram import Bot, Router
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import ContentType
from aiogram.enums import ParseMode
from deep_translator import GoogleTranslator
import replicate
from replicate.exceptions import ReplicateError
from states import BotStates
from config import REPLICATE_API_TOKEN, DATABASE_PATH
from generation_config import IMAGE_GENERATION_MODELS, GENERATION_TYPE_TO_MODEL_KEY, get_ultra_negative_prompt
from database import check_database_user, update_user_credits, save_video_task, update_video_task_status, log_generation, check_user_resources
from keyboards import create_main_menu_keyboard, create_rating_keyboard, create_video_generate_menu_keyboard, create_subscription_keyboard, create_back_keyboard, create_confirmation_keyboard
from generation.images import upload_image_to_replicate
from generation.utils import TempFileManager, reset_generation_context, send_message_with_fallback, send_video_with_retry
from handlers.utils import clean_admin_context, escape_message_parts, safe_escape_markdown as escape_md

logger = logging.getLogger(__name__)

class VideoStates(StatesGroup):
    AWAITING_VIDEO_PROMPT = State()
    AWAITING_VIDEO_PHOTO = State()
    AWAITING_VIDEO_CONFIRMATION = State()

video_router = Router()

async def get_video_progress_message(elapsed_minutes: int, model_name: str, style_name: str = "custom", total_minutes: int = 5) -> str:
    """Генерирует сообщение о прогрессе генерации видео."""
    VIDEO_PROGRESS_MESSAGES = {
        "1": [
            "🎬 Видео '{model_name}' ({style_name}) начало генерироваться! ИИ анализирует твой промпт...",
            "⚡ Подготовка кадров! ИИ создаёт основу для '{model_name}' ({style_name})...",
            "🎥 Запуск генерации! Нейросеть работает над '{model_name}' ({style_name})..."
        ],
        "2": [
            "🔥 Генерация в процессе! {elapsed} мин из ~{total} ({style_name})",
            "💫 Видео '{model_name}' ({style_name}) обрабатывается! Создаём динамику...",
            "⚙️ Нейросеть трудится! {elapsed}/{total} мин для '{model_name}' ({style_name})"
        ],
        "3": [
            "🎞️ Отрисовка кадров! Видео '{model_name}' ({style_name}) почти готово...",
            "✨ Последние штрихи! Ещё немного, и '{model_name}' ({style_name}) будет готово...",
            "🏁 Совсем скоро! Завершаем генерацию '{model_name}' ({style_name})..."
        ],
        "4": [
            "💎 Финальная обработка видео '{model_name}' ({style_name})...",
            "🔍 Проверка качества '{model_name}' ({style_name})...",
            "⏰ Финальная проверка для '{model_name}' ({style_name})..."
        ]
    }
    PROGRESS_BARS = [
        lambda p: f"{'█' * p}{'░' * (10-p)}",
        lambda p: f"{'▰' * p}{'▱' * (10-p)}",
        lambda p: f"{'●' * p}{'○' * (10-p)}",
        lambda p: f"{'◆' * p}{'◇' * (10-p)}",
        lambda p: f"{'■' * p}{'□' * (10-p)}"
    ]
    STAGE_EMOJIS = {
        1: ["🎬", "⚡", "🎥", "💥"],
        2: ["🔥", "💫", "⚙️", "✨"],
        3: ["🎞️", "🏁", "🎉", "🎊"],
        4: ["💎", "🔍", "⏰", "✅"]
    }

    message_key = str(min(elapsed_minutes, 4))
    if message_key not in VIDEO_PROGRESS_MESSAGES:
        keys = sorted([int(k) for k in VIDEO_PROGRESS_MESSAGES.keys()])
        for k in keys:
            if elapsed_minutes <= k:
                message_key = str(k)
                break
        else:
            message_key = str(keys[-1])

    messages = VIDEO_PROGRESS_MESSAGES[message_key]
    message = random.choice(messages)

    message = message.format(model_name=model_name, style_name=style_name, elapsed=elapsed_minutes, total=total_minutes)

    progress = min(int((elapsed_minutes / total_minutes) * 10), 10)
    progress_bar_func = random.choice(PROGRESS_BARS)
    progress_bar = progress_bar_func(progress)

    stage_emoji = random.choice(STAGE_EMOJIS.get(int(message_key), ["⏳"]))

    percentage = min(int((elapsed_minutes / total_minutes) * 100), 95)

    final_message = f"{stage_emoji} {message}\n\n"
    final_message += f"Прогресс: {progress_bar} {percentage}%\n"

    if elapsed_minutes >= 3:
        final_message += "\n🔔 Видео почти готово! Ещё чуть-чуть!"
    elif elapsed_minutes >= 2:
        final_message += f"\n⏱️ Осталось примерно {total_minutes - elapsed_minutes} мин"

    return final_message

async def send_video_progress(bot: Bot, user_id: int, elapsed_minutes: int, model_name: str, style_name: str = "custom", total_minutes: int = 5) -> None:
    """Отправляет сообщение о прогрессе генерации видео."""
    try:
        message = await get_video_progress_message(elapsed_minutes, model_name, style_name, total_minutes)
        await send_message_with_fallback(bot, user_id, message, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения о прогрессе видео пользователю {user_id}: {e}")

async def send_video_progress_with_delay(bot: Bot, user_id: int, elapsed_minutes: int, model_name: str, style_name: str = "custom", total_minutes: int = 5, delay: int = 0) -> None:
    """Отправляет сообщение о прогрессе с задержкой."""
    await asyncio.sleep(delay)
    await send_video_progress(bot, user_id, elapsed_minutes, model_name, style_name, total_minutes)

async def schedule_video_notifications(bot: Bot, user_id: int, model_name: str, style_name: str, task_id: int, prediction_id: str, total_minutes: int = 5):
    """Планирует уведомления о прогрессе генерации видео."""
    notification_schedule = [1, 2, 3, 4]
    for minutes in notification_schedule:
        if minutes < total_minutes:
            asyncio.create_task(send_video_progress_with_delay(
                bot, user_id, minutes, model_name, style_name, total_minutes, delay=minutes * 30
            ))

async def generate_video(message: Message, state: FSMContext, task_id: int = None, prediction_id: str = None):
    """Генерация видео."""
    user_data = await state.get_data()
    user_id = user_data.get('user_id', message.from_user.id)  # Используем user_id из состояния
    is_admin_generation = user_data.get('is_admin_generation', False)
    target_user_id = user_data.get('admin_generation_for_user', user_id)
    admin_user_id = message.from_user.id if is_admin_generation and user_id != message.from_user.id else None
    bot = message.bot
    generation_type = user_data.get('generation_type', 'ai_video_v2_1')
    model_key = user_data.get('model_key')
    style_name = user_data.get('style_name', 'custom')

    logger.debug(f"Генерация видео для user_id={user_id}, target_user_id={target_user_id}" + 
                 (f", admin_user_id={admin_user_id}" if admin_user_id else "") +
                 f", стиль: {style_name}, generation_type={generation_type}")

    # Проверка user_id
    if user_id == bot.id:
        logger.error(f"Попытка генерации видео для bot_id={user_id}, заменяем на target_user_id={target_user_id}")
        user_id = target_user_id

    # Проверка ресурсов для target_user_id
    required_photos = user_data.get('video_cost', IMAGE_GENERATION_MODELS.get(model_key, {}).get('cost', 20))
    if not await check_user_resources(bot, target_user_id, required_photos=required_photos):
        logger.error(f"Недостаточно ресурсов для target_user_id={target_user_id}")
        await state.update_data(user_id=user_id)
        return

    if not model_key or model_key not in IMAGE_GENERATION_MODELS:
        logger.error(f"Некорректный или отсутствующий model_key для видео: {model_key} для user_id={user_id}")
        await send_message_with_fallback(
            bot, user_id, 
            f"❌ Ошибка конфигурации видеомодели. Пожалуйста, выберите снова.", 
            reply_markup=await create_video_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        if admin_user_id:
            await send_message_with_fallback(
                bot, admin_user_id,
                f"❌ Неверная конфигурация модели для пользователя ID `{user_id}`.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                parse_mode=ParseMode.MARKDOWN
            )
        await reset_generation_context(state, generation_type)
        return

    model_config = IMAGE_GENERATION_MODELS[model_key]
    model_name_display = model_config.get('name', "AI-Видео (Kling 2.1)")
    replicate_video_model_id = model_config['id']
    required_photos = user_data.get('video_cost', model_config.get('cost', 20))
    logger.debug(f"required_photos для user_id={user_id}: {required_photos}")
    video_path_local_db_entry = None

    if not await check_user_resources(bot, user_id, required_photos=required_photos):
        return

    async with TempFileManager() as temp_manager:
        try:
            if not task_id:
                if 'video_prompt' not in user_data:
                    await send_message_with_fallback(
                        bot, user_id,
                        f"❌ Напиши описание для видео через /menu → Видеогенерация → {model_name_display}!",
                        reply_markup=await create_video_generate_menu_keyboard(),
                        parse_mode=ParseMode.MARKDOWN
                    )
                    if admin_user_id:
                        await send_message_with_fallback(
                            bot, admin_user_id,
                            f"❌ Отсутствует промпт для видео пользователя ID `{user_id}`.",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                            parse_mode=ParseMode.MARKDOWN
                        )
                    await reset_generation_context(state, generation_type)
                    return

                prompt = user_data['video_prompt']
                start_image_path = user_data.get('start_image')

                video_path_local_db_entry = f"generated/video_{user_id}_{uuid.uuid4()}.mp4"
                os.makedirs(os.path.dirname(video_path_local_db_entry), exist_ok=True)

                current_task_id = await save_video_task(
                    user_id, 
                    prediction_id=None, 
                    model_key=replicate_video_model_id, 
                    video_path=video_path_local_db_entry, 
                    status='pending_submission'
                )

                if not current_task_id:
                    raise Exception("Не удалось сохранить задачу видео в БД.")

                task_id = current_task_id

                await send_message_with_fallback(
                    bot, user_id,
                    f"🎬 Запускаю создание видео ({model_name_display}, {style_name})! "
                    f"Это займёт 3-5 минут. Я буду присылать уведомления о прогрессе! "
                    f"(будет списано {required_photos} печенек)",
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN
                )
                if admin_user_id:
                    await send_message_with_fallback(
                        bot, admin_user_id,
                        f"🎬 Начата генерация видео для пользователя ID `{user_id}` (стиль: {style_name}).",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                        parse_mode=ParseMode.MARKDOWN
                    )

                logger.info(f"Запущена генерация видео для user_id={user_id}, task_id={task_id}, "
                          f"prompt='{prompt[:50]}...', style_name={style_name}, start_image='{start_image_path}', model='{model_name_display}'")

                await schedule_video_notifications(
                    bot, user_id, model_name_display, style_name, task_id, prediction_id, total_minutes=5
                )

            else:
                async with aiosqlite.connect(DATABASE_PATH) as conn_check:
                    c_check = await conn_check.cursor()
                    await c_check.execute(
                        "SELECT video_path, prediction_id FROM video_tasks WHERE id = ? AND user_id = ?", 
                        (task_id, user_id)
                    )
                    task_data = await c_check.fetchone()

                if not task_data:
                    logger.error(f"Задача видео task_id={task_id} не найдена для user_id={user_id}")
                    return

                video_path_local_db_entry, prediction_id_from_db = task_data
                prediction_id = prediction_id_from_db
                prompt = user_data.get('video_prompt', "Видео по изображению")
                start_image_path = user_data.get('start_image')

                logger.info(f"Попытка продолжить/проверить видео для user_id={user_id}, task_id={task_id}, style_name={style_name}")

            translated_prompt = GoogleTranslator(source='auto', target='en').translate(prompt)

            input_params_video = {
                "mode": "pro",
                "prompt": translated_prompt,
                "duration": 5,
                "negative_prompt": get_ultra_negative_prompt(generation_type),
                "aspect_ratio": "16:9"
            }

            if start_image_path and os.path.exists(start_image_path):
                logger.info(f"Загрузка start_image для видео: {start_image_path}")
                uploaded_image_url = await upload_image_to_replicate(start_image_path)
                input_params_video["start_image"] = uploaded_image_url
                logger.info(f"Start_image загружен: {uploaded_image_url}")
                temp_manager.add(start_image_path)

            await update_user_credits(user_id, "decrement_photo", amount=required_photos)
            logger.info(f"Списано {required_photos} фото для видео user_id={user_id}, task_id={task_id}")

            replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

            if not prediction_id:
                logger.info(f"Создание нового предсказания Replicate для видео task_id={task_id}")

                prediction_instance = replicate_client.predictions.create(
                    version=replicate_video_model_id,
                    input=input_params_video
                )

                prediction_id = prediction_instance.id
                if not prediction_id:
                    raise ValueError("Replicate API не вернул prediction_id для видео.")

                await update_video_task_status(task_id, status='processing', prediction_id=prediction_id)
                logger.info(f"Видео предсказание создано: prediction_id={prediction_id}, task_id={task_id}")

            asyncio.create_task(check_video_status_with_delay(
                bot,
                {
                    'user_id': user_id,
                    'task_id': task_id,
                    'prediction_id': prediction_id,
                    'attempt': 1,
                    'generation_type': generation_type,
                    'model_key': model_key,
                    'style_name': style_name,
                    'admin_user_id': admin_user_id
                },
                delay=60
            ))

        except Exception as e:
            logger.error(f"Ошибка запуска генерации видео для user_id={user_id}, task_id={task_id}: {e}", exc_info=True)

            await send_message_with_fallback(
                bot, user_id,
                f"❌ Не удалось начать создание видео ({model_name_display}, {style_name})! "
                f"{required_photos} печеньки возвращены на баланс. Попробуй снова.",
                reply_markup=await create_video_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN
            )
            if admin_user_id:
                await send_message_with_fallback(
                    bot, admin_user_id,
                    f"❌ Ошибка генерации видео для пользователя ID `{user_id}`: {str(e)}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                    parse_mode=ParseMode.MARKDOWN
                )

            if task_id:
                await update_video_task_status(task_id, status='failed')

            try:
                await update_user_credits(user_id, "increment_photo", amount=required_photos)
                logger.info(f"Возвращено {required_photos} фото для user_id={user_id} из-за ошибки запуска видео.")
            except Exception as db_e:
                logger.error(f"Ошибка возврата {required_photos} фото для user_id={user_id}: {db_e}")
                await send_message_with_fallback(
                    bot, user_id, 
                    f"❌ Ошибка базы данных при возврате {required_photos} печенек Свяжитесь с поддержкой!", 
                    reply_markup=await create_video_generate_menu_keyboard(),
                    parse_mode=ParseMode.MARKDOWN
                )
                if admin_user_id:
                    await send_message_with_fallback(
                        bot, admin_user_id,
                        f"❌ Ошибка базы данных при возврате ресурсов для пользователя ID `{user_id}`.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                        parse_mode=ParseMode.MARKDOWN
                    )

            await reset_generation_context(state, generation_type)

        finally:
            if video_path_local_db_entry and task_id:
                async with aiosqlite.connect(DATABASE_PATH) as conn_clean:
                    c_clean = await conn_clean.cursor()
                    await c_clean.execute("SELECT status FROM video_tasks WHERE id = ?", (task_id,))
                    final_status_row = await c_clean.fetchone()

                    if final_status_row and final_status_row[0] != 'completed' and os.path.exists(video_path_local_db_entry):
                        try:
                            os.remove(video_path_local_db_entry)
                            logger.info(f"Удален пустой/неудачный файл видео: {video_path_local_db_entry}")
                        except Exception as e_clean_db_path:
                            logger.error(f"Ошибка удаления файла видео из БД {video_path_local_db_entry}: {e_clean_db_path}")

@video_router.callback_query(lambda c: c.data == 'ai_video_v2_1')
async def handle_generate_video_callback(query: CallbackQuery, state: FSMContext):
    """Обработка начала генерации видео через callback."""
    await query.answer()
    user_id = query.from_user.id
    bot = query.bot

    generation_type = 'ai_video_v2_1'
    await state.update_data(generation_type=generation_type, user_id=user_id)

    model_key = GENERATION_TYPE_TO_MODEL_KEY.get(generation_type)
    if not model_key:
        text = escape_message_parts(
            "❌ Ошибка: неизвестный тип генерации видео.",
            version=2
        )
        await send_message_with_fallback(
            bot, user_id,
            text,
            reply_markup=await create_video_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    await state.update_data(model_key=model_key, user_id=user_id)
    model_config = IMAGE_GENERATION_MODELS.get(model_key, {})
    cost = model_config.get('cost', 20)
    await state.update_data(video_cost=cost, user_id=user_id)

    subscription_data = await check_database_user(user_id)
    photos_balance = subscription_data[0]

    if photos_balance < cost:
        text_parts = [
            "❌ Недостаточно печенек на балансе.\n",
            f"Ваш баланс: {photos_balance} печенек\n",
            f"Стоимость видео: {cost} печенек\n\n",
            "Пополните баланс для продолжения."
        ]
        text = escape_message_parts(*text_parts, version=2)
        logger.debug(f"handle_generate_video_callback: сформирован текст: {text[:200]}...")
        keyboard = await create_subscription_keyboard()
        await query.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)
        return

    text_parts = [
        f"🎥 AI-видео (Kling 2.1)\n\n",
        f"Для создания видео потребуется *{cost} печенек* с твоего баланса.\n\n",
        f"Выбери стиль или введи свой промпт для видео:"
    ]
    text = escape_message_parts(*text_parts, version=2)
    logger.debug(f"handle_generate_video_callback: сформирован текст: {text[:200]}...")
    await query.message.edit_text(
        text,
        reply_markup=await create_video_styles_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(VideoStates.AWAITING_VIDEO_PROMPT)
    await state.update_data(user_id=user_id)

@video_router.callback_query(lambda c: c.data == 'confirm_video_prompt')
async def handle_confirm_video_prompt(query: CallbackQuery, state: FSMContext):
    """Обработка подтверждения промпта и фото для видео."""
    await query.answer()
    user_id = query.from_user.id
    bot = query.bot

    user_data = await state.get_data()
    prompt = user_data.get('video_prompt')
    start_image = user_data.get('start_image')
    model_key = user_data.get('model_key')
    model_config = IMAGE_GENERATION_MODELS.get(model_key, {})
    model_name_display = model_config.get('name', "AI-Видео (Kling 2.1)")
    style_name = user_data.get('style_name', 'custom')
    cost = user_data.get('video_cost', model_config.get('cost', 20))

    if not prompt:
        text = escape_message_parts(
            "❌ Промпт отсутствует. Пожалуйста, введите описание видео заново.",
            version=2
        )
        logger.debug(f"handle_confirm_video_prompt: сформирован текст: {text[:200]}...")
        await send_message_with_fallback(
            bot, user_id,
            text,
            reply_markup=await create_video_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.clear()
        await state.update_data(user_id=user_id)
        return

    await query.message.delete()
    await generate_video(query.message, state)
    await state.update_data(user_id=user_id)

@video_router.callback_query(lambda c: c.data == 'edit_video_prompt')
async def handle_edit_video_prompt(query: CallbackQuery, state: FSMContext):
    """Обработка редактирования промпта."""
    await query.answer()
    user_id = query.from_user.id
    bot = query.bot

    await state.update_data(waiting_for_video_prompt=True, user_id=user_id)
    await query.message.delete()
    text = escape_message_parts(
        "📝 Введи новое описание для видео:",
        version=2
    )
    logger.debug(f"handle_edit_video_prompt: сформирован текст: {text[:200]}...")
    await send_message_with_fallback(
        bot, user_id,
        text,
        reply_markup=await create_back_keyboard("video_generate_menu"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(VideoStates.AWAITING_VIDEO_PROMPT)
    await state.update_data(user_id=user_id)

@video_router.callback_query(lambda c: c.data == 'edit_video_photo')
async def handle_edit_video_photo(query: CallbackQuery, state: FSMContext):
    """Обработка редактирования фото."""
    await query.answer()
    user_id = query.from_user.id
    bot = query.bot

    await state.update_data(awaiting_video_photo=True, user_id=user_id)
    await query.message.delete()
    text = escape_message_parts(
        "📸 Загрузи новое фото для анимации или пропусти (/skip).",
        version=2
    )
    logger.debug(f"handle_edit_video_photo: сформирован текст: {text[:200]}...")
    await send_message_with_fallback(
        bot, user_id,
        text,
        reply_markup=await create_back_keyboard("video_generate_menu"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(VideoStates.AWAITING_VIDEO_PHOTO)
    await state.update_data(user_id=user_id)

@video_router.message(VideoStates.AWAITING_VIDEO_PROMPT)
async def handle_video_prompt(message: Message, state: FSMContext):
    """Обработка ввода промпта для видео."""
    user_id = message.from_user.id
    bot = message.bot
    prompt = message.text.strip()

    if not prompt:
        await message.reply(
            escape_md("❌ Пожалуйста, введи описание для видео!", version=2),
            reply_markup=await create_back_keyboard("ai_video_v2_1"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    user_data = await state.get_data()
    generation_type = user_data.get('generation_type', 'ai_video_v2_1')
    model_key = user_data.get('model_key', 'kwaivgi/kling-v2.1')
    style_name = user_data.get('style_name', 'custom')
    start_image_path = user_data.get('start_image')
    use_llama_prompt = user_data.get('use_llama_prompt', False)

    if use_llama_prompt:
        try:
            gender = user_data.get('selected_gender', 'person')
            assisted_prompt = await generate_assisted_prompt(prompt, gender, generation_type=generation_type)
            prompt = assisted_prompt
            logger.info(f"AI-промпт сгенерирован для user_id={user_id}: {prompt[:50]}...")
            await state.update_data(assisted_prompt=prompt)
        except Exception as e:
            logger.error(f"Ошибка генерации AI-промпта для user_id={user_id}: {e}", exc_info=True)
            prompt = prompt

    await state.update_data(video_prompt=prompt, style_name=style_name, awaiting_video_prompt=False, user_id=user_id)

    model_name = IMAGE_GENERATION_MODELS.get(model_key, {}).get('name', 'AI-Видео (Kling 2.1)')
    photo_status = "с фото" if start_image_path else "без фото"
    prompt_preview = prompt[:150] + '...' if len(prompt) > 150 else prompt
    confirm_text = (
        escape_md(f"📋 Проверь параметры генерации видео:\n\n", version=2) +
        escape_md(f"🎬 Тип: {model_name}\n", version=2) +
        escape_md(f"🎨 Стиль: {style_name}\n", version=2) +
        escape_md(f"📸 Фото: {photo_status}\n", version=2) +
        escape_md(f"💭 Промпт: _{prompt_preview}_\n\n", version=2) +
        escape_md(f"Всё верно?", version=2)
    )

    await message.reply(
        confirm_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, генерировать!", callback_data="confirm_video_generation")],
            [InlineKeyboardButton(text="✏️ Изменить промпт", callback_data="edit_video_prompt")],
            [InlineKeyboardButton(text="📸 Изменить фото", callback_data="edit_video_photo")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="ai_video_v2_1")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(VideoStates.AWAITING_VIDEO_CONFIRMATION)
    logger.info(f"Запрошено подтверждение параметров видео для user_id={user_id}")

async def show_video_confirmation(message: Message, state: FSMContext):
    """Показывает подтверждение промпта и фото перед генерацией."""
    user_id = message.from_user.id
    bot = message.bot
    user_data = await state.get_data()
    prompt = user_data.get('video_prompt')
    start_image = user_data.get('start_image')
    model_key = user_data.get('model_key')
    model_config = IMAGE_GENERATION_MODELS.get(model_key, {})
    model_name_display = model_config.get('name', "AI-Видео (Kling 2.1)")
    style_name = user_data.get('style_name', 'custom')
    cost = user_data.get('video_cost', model_config.get('cost', 20))

    text_parts = [
        f"🎥 Подтверждение генерации видео ({model_name_display}, {style_name}):\n\n",
        f"📝 Промпт: `{prompt}`\n",
        f"📸 Фото: {'Загружено' if start_image else 'Отсутствует'}\n",
        f"💰 Стоимость: {cost} печенек\n\n",
        f"Всё верно?"
    ]
    text = escape_message_parts(*text_parts, version=2)
    logger.debug(f"show_video_confirmation: сформирован текст: {text[:200]}...")

    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_video_prompt")],
        [InlineKeyboardButton(text="✏️ Редактировать промпт", callback_data="edit_video_prompt")],
        [InlineKeyboardButton(text="📸 Редактировать фото", callback_data="edit_video_photo")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="video_generate_menu")]
    ])

    if start_image and os.path.exists(start_image):
        photo_file = FSInputFile(path=start_image)
        await bot.send_photo(
            chat_id=user_id,
            photo=photo_file,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await send_message_with_fallback(
            bot, user_id,
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )

    await state.set_state(VideoStates.AWAITING_VIDEO_CONFIRMATION)
    await state.update_data(user_id=user_id)

@video_router.message(VideoStates.AWAITING_VIDEO_PHOTO, lambda message: message.content_type == ContentType.PHOTO)
async def handle_video_photo(message: Message, state: FSMContext) -> None:
    """Обработка загруженного фото для видеогенерации."""
    user_id = message.from_user.id
    bot = message.bot
    logger.info(f"handle_video_photo: user_id={user_id}, data={await state.get_data()}")

    try:
        if not message.photo:
            logger.warning(f"handle_video_photo вызван без фото для user_id={user_id}")
            await send_message_with_fallback(
                bot, user_id,
                escape_md("❌ Пожалуйста, отправь фото или пропусти с помощью /skip.", version=2),
                reply_markup=await create_back_keyboard("ai_video_v2_1"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        photo_file_id = message.photo[-1].file_id
        logger.info(f"Получено фото от user_id={user_id}, file_id={photo_file_id}")

        photo_file = await bot.get_file(photo_file_id)
        uploads_dir = f"generated/{user_id}"
        os.makedirs(uploads_dir, exist_ok=True)
        photo_path = os.path.join(uploads_dir, f"video_photo_{uuid.uuid4()}.jpg")
        await bot.download_file(photo_file.file_path, photo_path)

        user_data = await state.get_data()
        is_admin_generation = user_data.get('is_admin_generation', False)
        target_user_id = user_data.get('admin_generation_for_user', user_id)
        generation_type = user_data.get('generation_type', 'ai_video_v2_1')
        model_key = user_data.get('model_key', 'kwaivgi/kling-v2.1')
        use_llama_prompt = user_data.get('use_llama_prompt', False)
        awaiting_llama_after_photo = user_data.get('awaiting_llama_after_photo', False)
        came_from_custom_prompt = user_data.get('came_from_custom_prompt', False)

        logger.debug(f"Параметры: use_llama_prompt={use_llama_prompt}, awaiting_llama_after_photo={awaiting_llama_after_photo}, came_from_custom_prompt={came_from_custom_prompt}")

        await state.update_data(start_image=photo_path)

        if use_llama_prompt and awaiting_llama_after_photo:
            # Если используется AI-помощник, запрашиваем текст для генерации промпта
            await state.update_data(
                waiting_for_custom_prompt_llama=True,
                awaiting_video_photo=False,
                awaiting_llama_after_photo=False,
                video_prompt=None,
                user_id=user_id
            )
            text = escape_md(
                f"🤖 AI-помощник поможет создать детальный промпт для генерации видео{' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}!\n\n"
                f"Опиши свою идею простыми словами, например: _\"мужчина танцует на улице\"_ или _\"девушка идёт по пляжу\"_",
                version=2
            )
            await message.answer(
                text,
                reply_markup=await create_back_keyboard("ai_video_v2_1"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(BotStates.AWAITING_STYLE_SELECTION)
            logger.info(f"Запрошен ввод текста для AI-помощника после загрузки фото, user_id={user_id}, target_user_id={target_user_id}")
        elif came_from_custom_prompt and not user_data.get('video_prompt'):
            # Если используется кастомный промпт, запрашиваем его
            await state.update_data(
                waiting_for_video_prompt=True,
                awaiting_video_photo=False,
                user_id=user_id
            )
            text = escape_md(
                f"📝 Введи описание для видео (например: \"Танцующий человек в стиле киберпанк\"){' для пользователя ID ' + str(target_user_id) if is_admin_generation else ''}:",
                version=2
            )
            await message.answer(
                text,
                reply_markup=await create_back_keyboard("ai_video_v2_1"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(VideoStates.AWAITING_VIDEO_PROMPT)
            logger.info(f"Запрошен ввод промпта после загрузки фото, user_id={user_id}, target_user_id={target_user_id}")
        else:
            prompt = user_data.get('video_prompt', '')
            style_name = user_data.get('style_name', 'custom')
            if not prompt:
                logger.error(f"Отсутствует video_prompt для user_id={user_id}, target_user_id={target_user_id}")
                await message.answer(
                    escape_md("❌ Ошибка: отсутствует описание видео. Начни заново.", version=2),
                    reply_markup=await create_video_generate_menu_keyboard(),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await state.clear()
                await state.update_data(user_id=user_id)
                return
            text = escape_md(
                f"🎬 Подтверди параметры видео:\n\n"
                f"📸 Стиль: {style_name}\n"
                f"📝 Промпт: {prompt[:50]}{'...' if len(prompt) > 50 else ''}\n"
                f"🖼 Фото: {'Загружено' if photo_path else 'Отсутствует'}\n\n"
                f"💰 Стоимость: 20 печенек\n\n"
                f"Все верно? Нажми 'Да, генерировать!'",
                version=2
            )
            await message.answer(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Да, генерировать!", callback_data="confirm_video_generation")],
                    [InlineKeyboardButton(text="✏️ Изменить промпт", callback_data="edit_video_prompt")],
                    [InlineKeyboardButton(text="📸 Изменить фото", callback_data="edit_video_photo")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="ai_video_v2_1")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await state.set_state(VideoStates.AWAITING_VIDEO_CONFIRMATION)
            logger.info(f"Запрошено подтверждение параметров после загрузки фото для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка в handle_video_photo для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        await send_message_with_fallback(
            bot, user_id,
            escape_md("❌ Ошибка при обработке фото. Попробуй снова или обратись в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(user_id=user_id)

@video_router.message(VideoStates.AWAITING_VIDEO_PHOTO, Command("skip"))
async def handle_skip_photo(message: Message, state: FSMContext):
    """Обработка пропуска загрузки фото."""
    user_id = message.from_user.id
    user_data = await state.get_data()
    came_from_custom_prompt = user_data.get('came_from_custom_prompt', False)

    await state.update_data(awaiting_video_photo=False)

    if came_from_custom_prompt:
        # Запрашиваем ввод промпта
        text = escape_md("✍️ Введи описание для видео:", version=2)
        await message.reply(
            text,
            reply_markup=await create_back_keyboard("ai_video_v2_1"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(VideoStates.AWAITING_VIDEO_PROMPT)
        logger.info(f"Запрошен ввод промпта после пропуска фото для user_id={user_id}")
    else:
        # Переходим к подтверждению
        model_key = user_data.get('model_key', 'kwaivgi/kling-v2.1')
        style_name = user_data.get('style_name', 'custom')
        prompt = user_data.get('video_prompt', '')
        model_name = IMAGE_GENERATION_MODELS.get(model_key, {}).get('name', 'AI-Видео (Kling 2.1)')
        prompt_preview = prompt[:150] + '...' if len(prompt) > 150 else prompt
        confirm_text = (
            escape_md(f"📋 Проверь параметры генерации видео:\n\n", version=2) +
            escape_md(f"🎬 Тип: {model_name}\n", version=2) +
            escape_md(f"🎨 Стиль: {style_name}\n", version=2) +
            escape_md(f"📸 Фото: без фото\n", version=2) +
            escape_md(f"💭 Промпт: _{prompt_preview}_\n\n", version=2) +
            escape_md(f"Всё верно?", version=2)
        )
        await message.reply(
            confirm_text,
            reply_markup=await create_confirmation_keyboard(
                confirm_callback="confirm_video_generation",
                cancel_callback="ai_video_v2_1",
                confirm_text="✅ Да, генерировать!",
                cancel_text="🔙 Изменить"
            ),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(BotStates.AWAITING_STYLE_SELECTION)
        logger.info(f"Запрошено подтверждение параметров после пропуска фото для user_id={user_id}")

async def check_video_status(bot: Bot, data: dict):
    """Проверяет статус генерации видео."""
    user_id = data['user_id']
    task_id = data['task_id']
    prediction_id = data['prediction_id']
    attempt = data.get('attempt', 1)
    generation_type = data.get('generation_type', 'ai_video_v2_1')
    model_key = data.get('model_key')
    style_name = data.get('style_name', 'custom')
    admin_user_id = data.get('admin_user_id')

    logger.info(f"Проверка статуса видео: user_id={user_id}, task_id={task_id}, "
                f"prediction_id={prediction_id}, attempt={attempt}, style_name={style_name}")

    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute(
                "SELECT status, video_path FROM video_tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id)
            )
            task_info = await c.fetchone()

        if not task_info:
            logger.error(f"Задача видео task_id={task_id} не найдена для user_id={user_id}")
            return

        current_status_db = task_info['status']
        video_path = task_info['video_path']

        if current_status_db in ['completed', 'failed']:
            logger.info(f"Видео task_id={task_id} уже имеет финальный статус: {current_status_db}")
            return

        replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)
        prediction = replicate_client.predictions.get(prediction_id)
        current_replicate_status = prediction.status

        logger.info(f"Статус видео на Replicate для prediction_id={prediction_id}: {current_replicate_status}")

        if current_replicate_status == 'succeeded':
            video_url = None
            if prediction.output:
                if isinstance(prediction.output, str):
                    video_url = prediction.output
                elif isinstance(prediction.output, list) and prediction.output:
                    video_url = prediction.output[0]
                elif isinstance(prediction.output, dict) and 'video' in prediction.output:
                    video_url = prediction.output['video']

            if video_url:
                try:
                    logger.info(f"Скачивание видео с URL: {video_url}")
                    response = requests.get(video_url, timeout=300)
                    response.raise_for_status()

                    os.makedirs(os.path.dirname(video_path), exist_ok=True)
                    with open(video_path, 'wb') as f:
                        f.write(response.content)
                    logger.info(f"Видео сохранено локально: {video_path}")

                    await update_video_task_status(task_id, status='completed', video_path=video_path)

                    if model_key:
                        await log_generation(user_id, generation_type, model_key, units_generated=1)

                    model_name = IMAGE_GENERATION_MODELS.get(model_key, {}).get('name', 'AI-Видео (Kling 2.1)') if model_key else 'AI-Видео (Kling 2.1)'

                    text_parts = [
                        f"🎬 Твоё видео ({model_name}, {style_name}) готово! ",
                        "Оцени результат:"
                    ]
                    text = escape_message_parts(*text_parts, version=2)
                    logger.debug(f"check_video_status: сформирован текст: {text[:200]}...")

                    video_file = FSInputFile(path=video_path)
                    logger.debug(f"Отправка видео: path={video_path}, user_id={user_id}")
                    await send_video_with_retry(
                        bot,
                        user_id,
                        video_file,
                        caption=text,
                        reply_markup=await create_rating_keyboard(generation_type, model_key),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )

                    logger.info(f"Видео успешно отправлено пользователю {user_id}")

                    if admin_user_id:
                        text_admin = escape_message_parts(
                            f"✅ Видео для пользователя ID `{user_id}` (стиль: {style_name}) успешно сгенерировано.",
                            version=2
                        )
                        await send_message_with_fallback(
                            bot, admin_user_id,
                            text_admin,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )

                except Exception as e_download:
                    logger.error(f"Ошибка скачивания/отправки видео для task_id={task_id}: {e_download}", exc_info=True)
                    await update_video_task_status(task_id, status='failed')

                    text = escape_message_parts(
                        f"❌ Ошибка при скачивании видео.",
                        f" Пожалуйста, обратитесь в поддержку с ID задачи: {task_id}",
                        version=2
                    )
                    await send_message_with_fallback(
                        bot, user_id,
                        text,
                        reply_markup=await create_video_generate_menu_keyboard(),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    if admin_user_id:
                        text_admin = escape_message_parts(
                            f"❌ Ошибка скачивания видео для пользователя ID `{user_id}`: {str(e_download)}",
                            version=2
                        )
                        await send_message_with_fallback(
                            bot, admin_user_id,
                            text_admin,
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
            else:
                logger.error(f"Видео URL не найден в output для prediction_id={prediction_id}")
                await update_video_task_status(task_id, status='failed')

                text = escape_message_parts(
                    "❌ Ошибка: видео сгенерировано, но ссылка не получена.",
                    " Обратитесь в поддержку.",
                    version=2
                )
                await send_message_with_fallback(
                    bot, user_id,
                    text,
                    reply_markup=await create_video_generate_menu_keyboard(),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                if admin_user_id:
                    text_admin = escape_message_parts(
                        f"❌ Видео для пользователя ID `{user_id}` не содержит ссылку.",
                        version=2
                    )
                    await send_message_with_fallback(
                        bot, admin_user_id,
                        text_admin,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )

        elif current_replicate_status in ['failed', 'canceled']:
            error_details = prediction.error or "Причина неизвестна"
            logger.error(f"Генерация видео не удалась для prediction_id={prediction_id}: {error_details}")

            await update_video_task_status(task_id, status='failed')

            video_cost = IMAGE_GENERATION_MODELS.get(model_key, {}).get('cost', 20)
            logger.debug(f"Возвращаем {video_cost} фото для user_id={user_id}")

            text_parts = [
                f"❌ Генерация видео ({style_name}) не удалась.",
                f" Причина: {error_details}.",
                f" {video_cost} печеньки возвращены на баланс."
            ]
            text = escape_message_parts(*text_parts, version=2)
            await send_message_with_fallback(
                bot, user_id,
                text,
                reply_markup=await create_video_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await update_user_credits(user_id, "increment_photo", amount=video_cost)

            if admin_user_id:
                text_admin = escape_message_parts(
                    f"❌ Генерация видео для пользователя ID `{user_id}` (стиль: {style_name}) не удалась: {error_details}.",
                    version=2
                )
                await send_message_with_fallback(
                    bot, admin_user_id,
                    text_admin,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                    parse_mode=ParseMode.MARKDOWN_V2
                )

        else:
            max_attempts = 30

            if attempt >= max_attempts:
                logger.error(f"Превышено максимальное количество попыток проверки для task_id={task_id}")
                await update_video_task_status(task_id, status='timeout')

                video_cost = IMAGE_GENERATION_MODELS.get(model_key, {}).get('cost', 20)
                logger.debug(f"Возвращаем {video_cost} фото для user_id={user_id} из-за таймаута")

                text_parts = [
                    f"❌ Превышено время ожидания генерации видео ({style_name}).",
                    f" {video_cost} печеньки возвращены на баланс.",
                    " Обратитесь в поддержку."
                ]
                text = escape_message_parts(*text_parts, version=2)
                await send_message_with_fallback(
                    bot, user_id,
                    text,
                    reply_markup=await create_video_generate_menu_keyboard(),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await update_user_credits(user_id, "increment_photo", amount=video_cost)

                if admin_user_id:
                    text_admin = escape_message_parts(
                        f"❌ Превышено время ожидания для пользователя ID `{user_id}` (стиль: {style_name}).",
                        version=2
                    )
                    await send_message_with_fallback(
                        bot, admin_user_id,
                        text_admin,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{user_id}")]]),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                return

            next_delay = 60

            logger.info(f"Видео для task_id={task_id} все еще генерируется. "
                        f"Следующая проверка через {next_delay} сек (попытка {attempt + 1}/{max_attempts})")

            asyncio.create_task(check_video_status_with_delay(
                bot,
                {
                    'user_id': user_id,
                    'task_id': task_id,
                    'prediction_id': prediction_id,
                    'attempt': attempt + 1,
                    'generation_type': generation_type,
                    'model_key': model_key,
                    'style_name': style_name,
                    'admin_user_id': admin_user_id
                },
                delay=next_delay
            ))

    except Exception as e:
        logger.error(f"Ошибка при проверке статуса видео для task_id={task_id}: {e}", exc_info=True)

        if attempt < 10:
            asyncio.create_task(check_video_status_with_delay(
                bot,
                {
                    'user_id': user_id,
                    'task_id': task_id,
                    'prediction_id': prediction_id,
                    'attempt': attempt + 1,
                    'generation_type': generation_type,
                    'model_key': model_key,
                    'style_name': style_name,
                    'admin_user_id': admin_user_id
                },
                delay=120
            ))

async def check_video_status_with_delay(bot: Bot, data: dict, delay: int):
    """Проверка статуса видео с задержкой."""
    await asyncio.sleep(delay)
    await check_video_status(bot, data)

async def check_pending_video_tasks(bot: Bot):
    """Проверяет и возобновляет незавершенные задачи видео."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            
            await c.execute("""
                SELECT id, user_id, video_path, prediction_id, model_key
                FROM video_tasks
                WHERE status IN ('pending', 'starting', 'processing')
            """)
            pending_tasks = await c.fetchall()

        if not pending_tasks:
            logger.info("Нет незавершенных задач видео для проверки.")
            return
        
        logger.info(f"Найдено {len(pending_tasks)} незавершенных задач видео для проверки.")
        
        for row in pending_tasks:
            task_id = row['id']
            user_id = row['user_id']
            prediction_id = row['prediction_id']
            model_key = row['model_key']
            
            if not prediction_id:
                logger.warning(f"Пропуск проверки видео для task_id={task_id}, user_id={user_id}: отсутствует prediction_id.")
                continue
            
            generation_type = 'ai_video_v2_1' if model_key == IMAGE_GENERATION_MODELS.get("kwaivgi/kling-v2.1", {}).get("id") else 'ai_video_v2_1'
            
            asyncio.create_task(check_video_status_with_delay(
                bot,
                {
                    'user_id': user_id,
                    'task_id': task_id,
                    'prediction_id': prediction_id,
                    'attempt': 1,
                    'generation_type': generation_type,
                    'model_key': model_key,
                    'style_name': 'custom'
                },
                delay=random.randint(15, 45)
            ))
            
    except Exception as e:
        logger.error(f"Ошибка при проверке незавершенных задач видео: {e}", exc_info=True)

async def create_video_styles_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для выбора стилей видео."""
    buttons = [
        [InlineKeyboardButton(text="✍️ Ввести свой промпт", callback_data="custom_video_prompt")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="video_generate_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@video_router.callback_query(lambda c: c.data == 'custom_video_prompt')
async def handle_custom_video_prompt_callback(query: CallbackQuery, state: FSMContext):
    """Обработка выбора ввода собственного промпта."""
    await query.answer()
    user_id = query.from_user.id
    bot = query.bot

    await state.update_data(came_from_custom_prompt=True, waiting_for_video_prompt=True, user_id=user_id)
    text = escape_message_parts(
        "📝 Введи описание для видео (например: \"Танцующий человек в стиле киберпанк\"):",
        version=2
    )
    logger.debug(f"handle_custom_video_prompt_callback: сформирован текст: {text[:200]}...")
    await query.message.edit_text(
        text,
        reply_markup=await create_back_keyboard("video_generate_menu"),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(VideoStates.AWAITING_VIDEO_PROMPT)
    await state.update_data(user_id=user_id)