from asyncio import Lock
import re
import aiosqlite
import asyncio
import logging
import os
import uuid
import random
import zipfile
from typing import Dict, Optional, List
from aiogram import Bot, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode
import replicate
from replicate.exceptions import ReplicateError

from config import REPLICATE_USERNAME_OR_ORG_NAME, ADMIN_IDS, REPLICATE_API_TOKEN, DATABASE_PATH
from generation_config import IMAGE_GENERATION_MODELS
from database import check_database_user, update_user_credits, save_user_trainedmodel, update_trainedmodel_status, log_generation, check_user_resources
from keyboards import create_main_menu_keyboard, create_training_keyboard, create_user_profile_keyboard, create_subscription_keyboard, create_confirmation_keyboard
from generation.utils import TempFileManager, reset_generation_context, send_message_with_fallback
from generation.images import upload_image_to_replicate
from handlers.utils import clean_admin_context, safe_escape_markdown as escape_md

logger = logging.getLogger(__name__)

# Глобальная блокировка для каждого пользователя
user_locks = {}

class TrainingStates(StatesGroup):
    AWAITING_AVATAR_NAME = State()
    AWAITING_PHOTOS = State()
    AWAITING_CONFIRMATION = State()

training_router = Router()

TRAINING_PROGRESS_MESSAGES = {
    "1": [
        "🚀 ИИ создает модель аватара - '{name}' начал обучение! ИИ анализирует твои фото...",
        "⚡ Создание деталей! ИИ для вашей модели аватара '{name}' изучает черты лица...",
        "🎯 Запуск! Нейросеть проанализировала для вашей модели аватара '{name}' детали..."
    ],
    "2": [
        "🔥 Обработка на высокой мощности! {elapsed} мин из ~{total}",
        "💫 Обработка идёт отлично! Пробуем детали модели аватара '{name}' ...",
        "⚙️ Нейросеть трудится! {elapsed}/{total} мин для '{name}'"
    ],
    "3": [
        "🎭 Отрисовка модели и нейродеталей! Аватар - '{name}' почти готов...",
        "✨ Последние штрихи! Ещё немного, и '{name}' будет готов...",
        "🏁 Совсем скоро! Заканчиваю обучение '{name}'..."
    ],
    "4": [
        "💎 Обрабатываю детали для модели аватара '{name}'...",
        "🔍 Проверка качества обучения модели аватара '{name}'...",
        "⏰ Финальная проверка для '{name}'..."
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
    1: ["🚀", "⚡", "🎯", "💥"],
    2: ["🔥", "💫", "⚙️", "✨"],
    3: ["🎭", "🏁", "🎉", "🎊"],
    4: ["💎", "🔍", "⏰", "✅"]
}

TRAINER_VERSION = "replicate/fast-flux-trainer:8b10794665aed907bb98a1a5324cd1d3a8bea0e9b31e65210967fb9c9e2e08ed"

def generate_trigger_word(user_id: int, avatar_name: str) -> str:
    """Генерирует уникальное триггер-слово автоматически."""
    clean_name = re.sub(r'[^a-zA-Z0-9]', '', avatar_name.lower())
    if not clean_name:
        clean_name = "avatar"
    trigger_base = clean_name[:10]
    unique_suffix = f"{user_id % 1000}{uuid.uuid4().hex[:4]}"
    trigger_word = f"{trigger_base}{unique_suffix}"
    trigger_word = re.sub(r'[^a-z0-9]', '', trigger_word.lower())
    return trigger_word

async def get_training_progress_message(elapsed_minutes: int, avatar_name: str, total_minutes: int = 5) -> str:
    """Генерирует разнообразное сообщение о прогрессе обучения."""
    message_key = str(elapsed_minutes)
    if message_key not in TRAINING_PROGRESS_MESSAGES:
        keys = sorted([int(k) for k in TRAINING_PROGRESS_MESSAGES.keys()])
        for k in keys:
            if elapsed_minutes <= k:
                message_key = str(k)
                break
        else:
            message_key = str(keys[-1])
    
    messages = TRAINING_PROGRESS_MESSAGES[message_key]
    message = random.choice(messages)
    
    message = message.format(name=avatar_name, elapsed=elapsed_minutes, total=total_minutes)
    
    progress = min(int((elapsed_minutes / total_minutes) * 10), 10)
    progress_bar_func = random.choice(PROGRESS_BARS)
    progress_bar = progress_bar_func(progress)
    
    stage_emoji = random.choice(STAGE_EMOJIS.get(int(message_key), ["⏳"]))
    
    percentage = min(int((elapsed_minutes / total_minutes) * 100), 95)
    
    final_message = f"{stage_emoji} {message}\n\n"
    final_message += f"Прогресс: {progress_bar} {percentage}%\n"
    
    if elapsed_minutes >= 3:
        final_message += "\n🔔 Буквально минута — и аватар будет готов!"
    elif elapsed_minutes >= 2:
        final_message += f"\n⏱️ Осталось примерно {total_minutes - elapsed_minutes} мин"
    
    return final_message

async def send_training_progress(bot: Bot, user_id: int, elapsed_minutes: int, avatar_name: str, total_minutes: int = 5) -> None:
    """Отправляет улучшенное сообщение о прогрессе обучения."""
    try:
        message = await get_training_progress_message(elapsed_minutes, avatar_name, total_minutes)
        await send_message_with_fallback(bot, user_id, message, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения о прогрессе обучения пользователю {user_id}: {e}")

async def send_training_progress_with_delay(bot: Bot, user_id: int, elapsed_minutes: int, avatar_name: str, total_minutes: int, delay: int) -> None:
    """Отправляет сообщение о прогрессе обучения с задержкой."""
    await asyncio.sleep(delay)
    await send_training_progress(bot, user_id, elapsed_minutes, avatar_name, total_minutes)

async def schedule_training_notifications(bot: Bot, user_id: int, avatar_name: str, avatar_id: int, training_id: str, model_name: str, total_minutes: int = 5):
    """Планирует серию уведомлений о прогрессе обучения."""
    notification_schedule = [1, 2, 3, 4]
    for minutes in notification_schedule:
        if minutes < total_minutes:
            asyncio.create_task(send_training_progress_with_delay(
                bot, user_id, minutes, avatar_name, total_minutes, delay=minutes * 60
            ))
    asyncio.create_task(check_training_status_with_delay(
        bot, {'user_id': user_id, 'prediction_id': training_id, 'model_name': model_name, 'avatar_id': avatar_id}, delay=total_minutes * 60
    ))

async def start_training(message: Message, state: FSMContext) -> None:
    """Запускает обучение аватара с использованием Replicate trainings API."""
    # Получаем user_id из сообщения или состояния FSM
    user_id = message.from_user.id
    user_data = await state.get_data()
    stored_user_id = user_data.get('user_id')

    # Проверяем, не является ли user_id ID бота
    bot = message.bot
    bot_info = await bot.get_me()
    bot_id = bot_info.id
    if user_id == bot_id:
        logger.error(f"Попытка запуска обучения от бота с ID {bot_id}")
        if stored_user_id and stored_user_id != bot_id:
            logger.info(f"Используем user_id из состояния FSM: {stored_user_id}")
            user_id = stored_user_id
        else:
            logger.error(f"Невозможно запустить обучение: user_id ({user_id}) совпадает с ID бота и нет сохранённого user_id")
            return

    logger.info(f"Запуск обучения для user_id={user_id}")
    training_photos = user_data.get('training_photos', [])
    avatar_name = user_data.get('avatar_name')

    status_message = None

    if not avatar_name:
        logger.error(f"Отсутствует avatar_name для user_id={user_id} перед запуском обучения.")
        await message.reply(
            escape_md("❌ Ошибка: имя аватара не установлено. Пожалуйста, начни процесс создания аватара заново.", version=2),
            reply_markup=await create_user_profile_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await reset_generation_context(state, 'train_flux_no_avatar_name')
        return

    trigger_word = generate_trigger_word(user_id, avatar_name)
    await state.update_data(trigger_word=trigger_word, user_id=user_id)  # Сохраняем user_id в состоянии
    logger.info(f"Сгенерировано триггер-слово: {trigger_word} для user_id={user_id}")

    if len(training_photos) < 10:
        await message.reply(
            escape_md(f"❌ Нужно минимум 10 фото! Загружено {len(training_photos)}. Добавь ещё.", version=2),
            reply_markup=await create_training_keyboard(user_id, len(training_photos)),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if not await check_user_resources(bot, user_id, required_avatars=1):
        return

    status_message = await send_message_with_fallback(
        bot, user_id, escape_md("🚀 Подготовка фотографий для обучения нейросети...", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )

    model_name_for_db = f"{REPLICATE_USERNAME_OR_ORG_NAME}/fastnew"

    async with TempFileManager() as temp_manager:
        try:
            await update_user_credits(user_id, "decrement_avatar", amount=1)
            logger.info(f"Списан 1 аватар для user_id={user_id} ПЕРЕД запуском обучения.")

            zip_dir = f"uploads/{user_id}"
            os.makedirs(zip_dir, exist_ok=True)
            zip_filename = f"train_photos_{trigger_word}_{uuid.uuid4().hex[:6]}.zip"
            zip_path = os.path.join(zip_dir, zip_filename)
            temp_manager.add(zip_path)

            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for photo_path_item in training_photos:
                        if os.path.exists(photo_path_item):
                            zipf.write(photo_path_item, os.path.basename(photo_path_item))
                            temp_manager.add(photo_path_item)
                        else:
                            logger.warning(f"Файл фото {photo_path_item} не найден при создании ZIP для user_id={user_id}")
                logger.info(f"ZIP-архив создан: {zip_path} с {len(training_photos)} файлами.")
            except Exception as e_zip:
                logger.error(f"Ошибка создания ZIP для user_id={user_id}: {e_zip}", exc_info=True)
                raise RuntimeError(f"Ошибка создания ZIP-архива: {e_zip}")

            replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

            await status_message.edit_text(
                escape_md("📤 Загружаю твои фотографии в облако...", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )

            zip_url = await upload_image_to_replicate(zip_path)

            await status_message.edit_text(
                escape_md("✅ Фотографии загружены. Запускаю обучение нейросети...", version=2),
                parse_mode=ParseMode.MARKDOWN_V2
            )

            training_params = {"lora_type": "subject", "input_images": zip_url, "training_steps": 1000}

            logger.info(f"Запуск обучения. Destination: {model_name_for_db}, Version: {TRAINER_VERSION}, Params: {training_params}")

            training_id = None
            try:
                training = replicate_client.trainings.create(
                    destination=model_name_for_db, version=TRAINER_VERSION, input=training_params
                )
                training_id = training.id
                if not training_id:
                    raise ValueError("Не удалось получить ID для задачи обучения!")
                logger.info(f"Обучение запущено через trainings API: user_id={user_id}, training_id={training_id}, destination={model_name_for_db}")
            except Exception as e:
                logger.warning(f"Не удалось создать обучение через trainings API: {e}")
                try:
                    prediction = replicate_client.run(
                        TRAINER_VERSION, input={**training_params, "trigger_word": trigger_word}
                    )
                    training_id = prediction.id if hasattr(prediction, 'id') else f"training_{uuid.uuid4().hex[:8]}"
                    logger.info(f"Альтернативный запуск обучения как предикции: training_id={training_id}")
                except Exception as e_alt:
                    logger.error(f"Ошибка альтернативного запуска: {e_alt}")
                    raise

            if not training_id:
                raise ValueError("Не удалось получить ID обучения ни одним способом!")

            new_avatar_id = await save_user_trainedmodel(
                user_id, training_id, trigger_word, training_photos, avatar_name, training_step="started"
            )

            if not new_avatar_id:
                raise RuntimeError("Не удалось сохранить информацию о запуске обучения в БД.")

            await update_trainedmodel_status(avatar_id=new_avatar_id, model_id=model_name_for_db, status='starting')

            await log_generation(user_id, 'train_flux', TRAINER_VERSION, units_generated=1)

            final_user_message = (
                escape_md(f"🚀 Обучение аватара '{avatar_name}' запущено!", version=2) + "\n\n" +
                escape_md("⚡ Это займёт всего около 3-х минут благодаря нашей продвинутой нейросети!", version=2) + "\n" +
                escape_md("📱 Я буду присылать уведомления о прогрессе.", version=2) + "\n" +
                escape_md("🔔 Ты получишь уведомление, как только аватар будет готов!", version=2) + "\n\n" +
                escape_md("✨ Наша нейросеть создает аватары высочайшего качества в соответствии с вашими фото!", version=2)
            )

            await status_message.edit_text(
                final_user_message,
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )

            status_message = None

            await schedule_training_notifications(
                bot, user_id, avatar_name, new_avatar_id, training_id, model_name_for_db, total_minutes=5
            )

            await reset_generation_context(state, 'train_flux_started_success')

        except ReplicateError as e_replicate:
            logger.error(f"Ошибка Replicate при запуске обучения для user_id={user_id}: "
                        f"{e_replicate.detail if hasattr(e_replicate, 'detail') else e_replicate}", exc_info=True)
            await update_user_credits(user_id, "increment_avatar", amount=1)
            logger.info(f"Возвращен 1 аватар для user_id={user_id} из-за ReplicateError при обучении.")
            user_message_error = (
                escape_md(f"❌ Ошибка запуска обучения нейросети. ", version=2) +
                escape_md(f"Аватар '{avatar_name}' возвращён на баланс. Попробуй снова.", version=2)
            )
            if status_message:
                await status_message.edit_text(
                    user_message_error,
                    reply_markup=await create_user_profile_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await send_message_with_fallback(
                    bot, user_id, user_message_error,
                    reply_markup=await create_user_profile_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            await reset_generation_context(state, 'train_flux_replicate_error')

        except Exception as e_train:
            logger.error(f"Общая ошибка запуска обучения для user_id={user_id}: {e_train}", exc_info=True)
            await update_user_credits(user_id, "increment_avatar", amount=1)
            logger.info(f"Возвращен 1 аватар для user_id={user_id} из-за общей ошибки обучения.")
            user_message_error_general = (
                escape_md(f"❌ Ошибка запуска обучения нейросети. ", version=2) +
                escape_md(f"Аватар '{avatar_name}' возвращён на баланс. Попробуй снова через несколько минут.", version=2)
            )
            if status_message:
                await status_message.edit_text(
                    user_message_error_general,
                    reply_markup=await create_user_profile_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await send_message_with_fallback(
                    bot, user_id, user_message_error_general,
                    reply_markup=await create_user_profile_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            await reset_generation_context(state, 'train_flux_general_error')

        finally:
            if status_message:
                try:
                    await status_message.delete()
                except Exception:
                    pass

async def check_training_status(bot: Bot, data: Dict[str, any]) -> None:
    """Проверяет статус тренировки модели с улучшенными уведомлениями."""
    user_id = data['user_id']
    training_id = data.get('prediction_id', data.get('training_id'))
    model_name = data['model_name']
    avatar_id = data['avatar_id']

    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        c = await conn.cursor()
        await c.execute(
            "SELECT avatar_name, trigger_word, photo_paths FROM user_trainedmodels WHERE avatar_id = ?",
            (avatar_id,)
        )
        avatar_info = await c.fetchone()

    if not avatar_info:
        logger.error(f"Не найдена информация об аватаре avatar_id={avatar_id}")
        return

    avatar_name = avatar_info['avatar_name']
    trigger_word = avatar_info['trigger_word']

    replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

    try:
        logger.info(f"Проверка статуса тренировки для user_id={user_id}, avatar_id={avatar_id}, training_id={training_id}")

        training_status = None
        output = None

        try:
            training = replicate_client.trainings.get(training_id)
            training_status = training.status
            output = training.output if hasattr(training, 'output') else None
            logger.info(f"Получен статус через trainings API: {training_status}")
            logger.debug(f"Training output: {output}")
        except Exception as e:
            logger.warning(f"Не удалось получить статус через trainings API: {e}")
            try:
                prediction = replicate_client.predictions.get(training_id)
                training_status = prediction.status
                output = prediction.output
                logger.info(f"Получен статус через predictions API: {training_status}")
                logger.debug(f"Prediction output: {output}")
            except Exception as e2:
                logger.error(f"Не удалось получить статус ни через trainings, ни через predictions API: {e2}")
                training_status = 'failed'
                output = None

        if training_status == 'succeeded':
            model_version = None
            if output:
                logger.info(f"Анализируем output: type={type(output)}, content={output}")
                if isinstance(output, dict):
                    if 'version' in output:
                        model_version = output['version']
                        logger.info(f"Версия найдена в output['version']: {model_version}")
                    elif 'weights' in output:
                        weights_url = output['weights']
                        logger.info(f"Weights URL: {weights_url}")
                        if 'pbxt/' in weights_url:
                            version_match = re.search(r'pbxt/([a-f0-9]{64})', weights_url)
                            if version_match:
                                model_version = version_match.group(1)
                                logger.info(f"Версия извлечена из weights URL: {model_version}")
                    elif 'model' in output:
                        model_path = output['model']
                        if ':' in model_path:
                            model_version = model_path.split(':')[-1]
                            logger.info(f"Версия извлечена из model path: {model_version}")
                elif isinstance(output, str):
                    if len(output) == 64 and all(c in '0123456789abcdef' for c in output):
                        model_version = output
                        logger.info(f"Output является версией: {model_version}")
                    elif 'pbxt/' in output:
                        version_match = re.search(r'pbxt/([a-f0-9]{64})', output)
                        if version_match:
                            model_version = version_match.group(1)
                            logger.info(f"Версия извлечена из output URL: {model_version}")
                    elif ':' in output:
                        model_version = output.split(':')[-1]
                        logger.info(f"Версия извлечена из полного пути: {model_version}")

            if not model_version:
                logger.error(f"Не удалось извлечь версию модели из output: {output}")
                try:
                    model_base = model_name.split(':')[0] if ':' in model_name else model_name
                    model = replicate_client.models.get(model_base)
                    if model and hasattr(model, 'latest_version'):
                        latest_version = model.latest_version
                        if hasattr(latest_version, 'id'):
                            model_version = latest_version.id
                            logger.info(f"Версия получена из latest_version: {model_version}")
                except Exception as e:
                    logger.error(f"Не удалось получить версию через models API: {e}")
                if not model_version:
                    model_version = f"temp-{uuid.uuid4().hex}"
                    logger.warning(f"Используется временная версия: {model_version}")

            logger.info(f"Финальная версия модели: {model_version}")

            model_base_name = model_name.split(':')[0] if ':' in model_name else model_name

            await update_trainedmodel_status(
                avatar_id, model_base_name, model_version, 'success', training_id
            )

            await update_user_credits(user_id, "set_trained_model", amount=1)
            await update_user_credits(user_id, "set_active_avatar", amount=avatar_id)

            # Экранируем все специальные символы в статическом тексте
            safe_avatar_name = escape_md(avatar_name, version=2)
            success_message = (
                escape_md(f"🎉🎊 ГОТОВО! Ваша модель аватара - '{safe_avatar_name}' успешно обучена!\n\n", version=2) +
                escape_md("✅ Обучение в соответствии с вашими фото!\n", version=2) +
                escape_md("🔑 Создавай фотосессии теперь со своим аватаром\n", version=2) +
                escape_md("⚡ Выбирай готовые стили для фотографий!\n\n", version=2) +
                escape_md("🎨 Теперь ты можешь создавать красивые фото!\n", version=2) +
                escape_md("Просто выбери 'Сгенерировать' и начинай!\n\n", version=2) +
                escape_md("💡 Совет: Каждая генерация это уникальный процесс НЕ повторяющий предыдущую!", version=2)
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✨ Создать первое фото!", callback_data="generate_menu")],
                [InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_menu")]
            ])

            await send_message_with_fallback(
                bot, user_id, success_message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2, is_escaped=True
            )

            for admin_id in ADMIN_IDS:
                try:
                    safe_model_base_name = escape_md(model_base_name, version=2)
                    safe_model_version = escape_md(model_version[:16], version=2)
                    safe_training_id = escape_md(training_id, version=2)
                    admin_message = (
                        escape_md(f"✅ Успешное обучение модели!\n", version=2) +
                        escape_md(f"👤 User: {user_id}\n", version=2) +
                        escape_md(f"🏷 Аватар: {safe_avatar_name}\n", version=2) +
                        escape_md(f"🔑 Триггер: {escape_md(trigger_word, version=2)}\n", version=2) +
                        escape_md(f"📦 Модель: {safe_model_base_name}\n", version=2) +
                        escape_md(f"🔖 Версия: {safe_model_version}...\n", version=2) +
                        escape_md(f"📝 Training ID: {safe_training_id}", version=2)
                    )
                    await bot.send_message(admin_id, admin_message, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")

        elif training_status in ['failed', 'canceled']:
            logger.error(f"Обучение провалилось со статусом: {training_status}")
            await update_trainedmodel_status(avatar_id, status='failed')
            safe_avatar_name = escape_md(avatar_name, version=2)
            error_message = (
                escape_md(f"😔 К сожалению, обучение аватара '{safe_avatar_name}' не удалось.\n\n", version=2) +
                escape_md("🔄 Аватар возвращён на твой баланс.\n", version=2) +
                escape_md("💡 Возможные причины:\n", version=2) +
                escape_md("- Недостаточное качество фото\n", version=2) +
                escape_md("- Слишком разные ракурсы\n", version=2) +
                escape_md("- Технические проблемы\n\n", version=2) +
                escape_md("Попробуй снова с другими фото!", version=2)
            )
            await send_message_with_fallback(
                bot, user_id, error_message, reply_markup=await create_main_menu_keyboard(user_id), parse_mode=ParseMode.MARKDOWN_V2, is_escaped=True
            )
            await update_user_credits(user_id, "increment_avatar", amount=1)

        else:
            logger.info(f"Тренировка для user_id={user_id}, avatar_id={avatar_id} всё ещё в процессе: {training_status}")
            safe_avatar_name = escape_md(avatar_name, version=2)
            progress_message = (
                escape_md(f"⏳ Аватар '{safe_avatar_name}' почти готов! Проверю снова через 30 секунд...", version=2)
            )
            await send_message_with_fallback(
                bot, user_id, progress_message, parse_mode=ParseMode.MARKDOWN_V2, is_escaped=True
            )
            asyncio.create_task(check_training_status_with_delay(bot, data, delay=30))

    except Exception as e:
        logger.error(f"Ошибка проверки статуса для user_id={user_id}: {e}", exc_info=True)
        await update_user_credits(user_id, "increment_avatar", amount=1)
        safe_avatar_name = escape_md(avatar_name, version=2)
        error_message = (
            escape_md(f"❌ Ошибка проверки обучения аватара '{safe_avatar_name}'. ", version=2) +
            escape_md("Аватар возвращён на баланс. Попробуй снова позже.", version=2)
        )
        await send_message_with_fallback(
            bot, user_id, error_message, reply_markup=await create_main_menu_keyboard(user_id), parse_mode=ParseMode.MARKDOWN_V2, is_escaped=True
        )

async def check_training_status_with_delay(bot: Bot, data: Dict[str, any], delay: int) -> None:
    """Проверка статуса обучения с задержкой."""
    await asyncio.sleep(delay)
    await check_training_status(bot, data)

async def check_pending_trainings(bot: Bot) -> None:
    """Проверяет и возобновляет незавершенные задачи обучения."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("""
                SELECT user_id, prediction_id, avatar_id, model_id, trigger_word, avatar_name
                FROM user_trainedmodels
                WHERE status IN ('pending', 'starting', 'processing')
            """)
            pending_trainings = await c.fetchall()
        
        if not pending_trainings:
            logger.info("Нет незавершенных задач обучения для проверки.")
            return
        
        logger.info(f"Найдено {len(pending_trainings)} незавершенных задач обучения для проверки.")
        
        for row in pending_trainings:
            user_id = row['user_id']
            training_id = row['prediction_id']
            avatar_id = row['avatar_id']
            model_id_db = row['model_id']
            trigger_word_db = row['trigger_word']
            avatar_name = row['avatar_name'] or f"Avatar {avatar_id}"
            
            if not training_id:
                logger.warning(f"Пропуск проверки обучения для avatar_id={avatar_id}, user_id={user_id}: отсутствует training_id.")
                continue
            
            model_name_for_check = model_id_db
            if not model_name_for_check:
                logger.warning(f"Отсутствует model_id для avatar_id={avatar_id}, user_id={user_id}. Используем дефолтное имя.")
                model_name_for_check = f"{REPLICATE_USERNAME_OR_ORG_NAME}/fastnew"
                logger.info(f"Восстановлен model_name для проверки: {model_name_for_check}")
            
            logger.info(f"Возобновление проверки статуса обучения для user_id={user_id}, "
                        f"avatar_id={avatar_id}, training_id={training_id}, model_name='{model_name_for_check}'")
            
            asyncio.create_task(check_training_status_with_delay(bot, {
                'user_id': user_id, 'prediction_id': training_id, 'model_name': model_name_for_check, 'avatar_id': avatar_id
            }, delay=random.randint(10, 30)))
            
    except Exception as e:
        logger.error(f"Ошибка при проверке незавершенных задач обучения: {e}", exc_info=True)

@training_router.callback_query(lambda c: c.data and c.data.startswith("train_new_avatar"))
async def initiate_training(query: CallbackQuery, state: FSMContext):
    """Инициирует процесс создания нового аватара."""
    await query.answer()
    user_id = query.from_user.id
    bot = query.bot

    await query.message.edit_text(
        escape_md("📝 Введите имя для вашего нового аватара:", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(TrainingStates.AWAITING_AVATAR_NAME)

@training_router.message(TrainingStates.AWAITING_AVATAR_NAME)
async def handle_avatar_name(message: Message, state: FSMContext):
    """Обрабатывает ввод имени аватара."""
    user_id = message.from_user.id
    bot = message.bot
    avatar_name = message.text.strip()

    if not avatar_name or len(avatar_name) > 50:
        await message.reply(
            escape_md("❌ Имя аватара должно быть от 1 до 50 символов. Попробуй снова.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Получаем текущие данные
    user_data = await state.get_data()
    training_photos = user_data.get('training_photos', [])
    photo_count = len(training_photos)

    # Сохраняем имя аватара
    await state.update_data(avatar_name=avatar_name, training_photos=training_photos, processed_media_groups=set())

    if photo_count >= 10:
        # Если загружено достаточно фотографий, переходим к подтверждению
        text = (
            escape_md(f"👍 Отлично! Давай проверим финальные данные:\n\n", version=2) +
            escape_md(f"👤 Имя аватара: {avatar_name}\n", version=2) +
            escape_md(f"📸 Загружено фото: {photo_count} шт.\n\n", version=2) +
            escape_md(f"🚀 Все готово для запуска обучения!\n", version=2) +
            escape_md(f"⏱ Это займет около 3-5 минут.\n", version=2) +
            escape_md(f"💎 Будет списан 1 аватар с твоего баланса.\n\n", version=2) +
            escape_md(f"Начинаем?", version=2)
        )
        await message.reply(
            text,
            reply_markup=await create_confirmation_keyboard(
                confirm_callback="confirm_start_training",
                cancel_callback="user_profile",
                confirm_text="🚀 Начать обучение!",
                cancel_text="✏️ Изменить данные"
            ),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(TrainingStates.AWAITING_CONFIRMATION)
    else:
        # Если фотографий недостаточно, запрашиваем загрузку
        text = (
            escape_md(f"✅ Имя аватара: {avatar_name}\n\n", version=2) +
            escape_md(f"📸 Загружено {photo_count} фото. Нужно минимум 10. Загрузи ещё {10 - photo_count}.", version=2) + "\n" +
            escape_md("Требования:\n", version=2) +
            escape_md("- Чёткие фото лица\n", version=2) +
            escape_md("- Разные ракурсы\n", version=2) +
            escape_md("- Хорошее освещение\n", version=2) +
            escape_md("- Без фильтров\n\n", version=2) +
            escape_md("Отправляйте фото по одной или группой.", version=2)
        )
        await message.reply(
            text,
            reply_markup=await create_training_keyboard(user_id, photo_count),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.set_state(TrainingStates.AWAITING_PHOTOS)

@training_router.message(TrainingStates.AWAITING_CONFIRMATION)
async def handle_confirmation(message: Message, state: FSMContext):
    """Обрабатывает текстовые сообщения в состоянии подтверждения."""
    user_id = message.from_user.id
    await message.reply(
        escape_md("❌ Пожалуйста, используйте кнопки для подтверждения или отмены.", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )

@training_router.callback_query(TrainingStates.AWAITING_CONFIRMATION, lambda c: c.data == "confirm_start_training")
async def handle_confirm_training_callback(query: CallbackQuery, state: FSMContext):
    """Обрабатывает подтверждение начала обучения."""
    user_id = query.from_user.id
    await query.answer("Запускаем обучение!")
    await start_training(query.message, state)
    await state.clear()

@training_router.callback_query(TrainingStates.AWAITING_CONFIRMATION, lambda c: c.data == "user_profile")
async def handle_cancel_training_callback(query: CallbackQuery, state: FSMContext):
    """Обрабатывает отмену обучения."""
    user_id = query.from_user.id
    await state.clear()
    await query.answer("Создание аватара отменено")
    await query.message.answer(
        escape_md("✅ Создание аватара отменено. Возвращаемся в личный кабинет.", version=2),
        reply_markup=await create_user_profile_keyboard(user_id, query.bot),
        parse_mode=ParseMode.MARKDOWN_V2
    )   

@training_router.message(TrainingStates.AWAITING_PHOTOS, lambda message: message.content_type == ContentType.PHOTO)
async def handle_training_photos(message: Message, state: FSMContext):
    """Обрабатывает загрузку фотографий для обучения, используя только фото с максимальным разрешением."""
    user_id = message.from_user.id
    bot = message.bot
    media_group_id = message.media_group_id

    user_data = await state.get_data()
    training_photos = user_data.get('training_photos', [])
    processed_media_groups = user_data.get('processed_media_groups', set())

    # Пропускаем, если медиагруппа уже обработана
    if media_group_id and media_group_id in processed_media_groups:
        logger.debug(f"Медиагруппа {media_group_id} уже обработана для user_id={user_id}")
        return

    # Берем только фото с максимальным разрешением (последний элемент в message.photo)
    photos = message.photo
    if not photos:
        logger.error(f"Нет фотографий в сообщении для user_id={user_id}")
        await message.reply(
            escape_md("❌ Ошибка: нет фотографии в сообщении. Попробуй загрузить снова.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Используем фото с наивысшим разрешением
    photo = photos[-1]  # Последний элемент имеет максимальное разрешение
    try:
        file = await bot.get_file(photo.file_id)
        photo_path = f"temp/{user_id}_{uuid.uuid4()}.jpg"
        os.makedirs(os.path.dirname(photo_path), exist_ok=True)
        await bot.download_file(file.file_path, photo_path)
        if photo_path not in training_photos:
            training_photos.append(photo_path)
            logger.debug(f"Добавлено фото {photo_path} для user_id={user_id}")
        else:
            logger.debug(f"Фото {photo_path} уже было добавлено для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка обработки фото для user_id={user_id}: {e}")
        await message.reply(
            escape_md("❌ Ошибка при обработке фото. Попробуй загрузить другое.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Обновляем список обработанных медиагрупп
    if media_group_id:
        processed_media_groups.add(media_group_id)
        await state.update_data(processed_media_groups=processed_media_groups)

    await state.update_data(training_photos=training_photos)
    count = len(training_photos)

    logger.debug(f"Загружено {count} фото для user_id={user_id}, media_group_id={media_group_id}")

    if count >= 10:
        text = (
            escape_md(f"📸 Загружено {count} фото. Можно начать обучение или загрузить ещё.", version=2) + "\n" +
            escape_md("Для запуска обучения нажми 'Начать обучение'.", version=2)
        )
        await message.reply(
            text,
            reply_markup=await create_training_keyboard(user_id, count),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        text = (
            escape_md(f"📸 Загружено {count} фото. Нужно минимум 10. Загрузи ещё {10 - count}.", version=2)
        )
        await message.reply(
            text,
            reply_markup=await create_training_keyboard(user_id, count),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    logger.debug(f"Сообщение о загрузке фото отправлено для user_id={user_id}: {text}")