import asyncio
import logging
import os
import tenacity
import traceback
from aiogram import Bot
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, FSInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest, TelegramNetworkError
from contextlib import asynccontextmanager
from aiogram.enums import ParseMode
import replicate
from replicate.exceptions import ReplicateError
from config import REPLICATE_API_TOKEN
from handlers.utils import safe_escape_markdown as escape_md

logger = logging.getLogger(__name__)

class TempFileManager:
    """Менеджер для управления временными файлами"""
    def __init__(self):
        self.files: list[str] = []
    
    def add(self, filepath: str):
        """Добавляет файл в список для последующего удаления"""
        self.files.append(filepath)
    
    async def cleanup(self):
        """Удаляет все временные файлы"""
        for filepath in self.files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    logger.debug(f"Removed temp file: {filepath}")
            except Exception as e:
                logger.error(f"Failed to remove {filepath}: {e}")
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()

async def reset_generation_context(state: 'aiogram.fsm.context.FSMContext' = None, generation_type: str = None, partial: bool = False, user_id: int = None):
    """Сбрасывает контекст генерации в FSM, защищая необходимые данные."""
    
    user_id_for_log = user_id if user_id is not None else 'unknown'
    
    if state is None:
        logger.info(f"reset_generation_context вызван с state=None, тип: {generation_type or 'не указан'}, user_id={user_id_for_log}, пропускаем очистку FSM")
        return

    user_data = await state.get_data()
    user_id_from_state = user_data.get('generation_target_user', user_data.get('message_recipient', user_data.get('user_id', 'unknown')))
    user_id_for_log = user_id if user_id is not None else user_id_from_state

    if generation_type not in ["menu_command", "generate_menu", "start_command"]:
        stack = traceback.format_stack()
        logger.warning("=== СБРОС КОНТЕКСТА ===")
        logger.warning(f"Тип: {generation_type or 'НЕ УКАЗАН'}")
        logger.warning(f"User ID: {user_id_for_log}")
        logger.warning(f"До очистки: user_data={user_data}")
        logger.warning("Стек вызовов:")
        for line in stack[-5:]:
            logger.warning(line.strip())
        logger.warning("=== КОНЕЦ СТЕКА ===")

    protected_data = {}
    if user_data.get('training_step') in ['upload_photos', 'enter_avatar_name', 'confirm_training_quality', 'confirm_training']:
        protected_keys = ['avatar_name', 'training_photos', 'trigger_word', 'training_step']
        protected_data = {k: user_data.get(k) for k in protected_keys if k in user_data}
        logger.debug(f"Защищённые данные аватара сохранены: {protected_data}")

    # Защищаем данные видеогенерации
    if generation_type == 'ai_video_v2_1':
        protected_keys = ['video_prompt', 'awaiting_video_photo', 'start_image', 'video_cost', 'style_name', 'model_key', 'user_id', 'came_from_custom_prompt']
        protected_data.update({k: user_data.get(k) for k in protected_keys if k in user_data})
        logger.debug(f"Защищённые данные видеогенерации сохранены: {protected_data}")

    if not partial and generation_type and generation_type not in ['error', 'back_to_menu', 'menu_command', 'start_command', 'generate_menu']:
        required_for_repeat = ['prompt', 'aspect_ratio', 'generation_type', 'model_key']
        if all(field in user_data and user_data[field] for field in required_for_repeat):
            try:
                from generation.images import user_last_generation_params, user_last_generation_lock
                last_params = {
                    'prompt': user_data.get('prompt'),
                    'aspect_ratio': user_data.get('aspect_ratio'),
                    'generation_type': user_data.get('generation_type'),
                    'model_key': user_data.get('model_key'),
                    'selected_gender': user_data.get('selected_gender'),
                    'user_input_for_llama': user_data.get('user_input_for_llama'),
                    'current_style_set': user_data.get('current_style_set'),
                    'came_from_custom_prompt': user_data.get('came_from_custom_prompt', False)
                }
                async with user_last_generation_lock:
                    user_last_generation_params[user_id_for_log] = last_params
                    logger.info(f"Сохранены параметры для повтора для user_id={user_id_for_log}")
            except Exception as e:
                logger.error(f"Ошибка сохранения параметров для повтора: {e}")

    if partial:
        keys_to_remove = [
            'waiting_for_custom_prompt_manual', 'waiting_for_custom_prompt_llama', 
            'waiting_for_photo', 'waiting_for_video_prompt', 'waiting_for_mask',
            'back_from_custom_prompt'
        ]
        logger.info(f"Частичная очистка контекста (тип: {generation_type or 'не указан'}) для user_id={user_id_for_log}")
    else:
        keys_to_remove = [
            'prompt', 'aspect_ratio', 'waiting_for_custom_prompt_manual',
            'waiting_for_custom_prompt_llama', 'user_input_for_llama',
            'waiting_for_photo', 'photo_path', 'training_step', 'training_photos',
            'trigger_word', 'avatar_name', 'generation_type',
            'model_key', 'waiting_for_mask', 'mask_path',
            'user_input', 'current_style_set', 'selected_gender',
            'back_from_custom_prompt', 'reference_image_url', 'model_version',
            'old_model_id', 'old_model_version', 'last_generation_params',
            'style_key', 'selected_prompt'
        ]
        logger.info(f"Полная очистка контекста (тип: {generation_type or 'не указан'}) для user_id={user_id_for_log}")

    removed_keys = []
    for key in keys_to_remove:
        if key in user_data and key not in protected_data:
            removed_keys.append(key)

    if removed_keys:
        await state.update_data({k: None for k in removed_keys})
        logger.debug(f"Удалены ключи: {removed_keys}")

    if protected_data:
        await state.update_data(protected_data)
        logger.debug(f"Восстановлены защищённые данные: {protected_data}")

    # Сохраняем user_id в состоянии, если он был передан
    if user_id is not None:
        await state.update_data(user_id=user_id)
        logger.debug(f"Сохранен user_id={user_id} в состоянии FSM")

    updated_data = await state.get_data()
    logger.info(f"После очистки: user_data={updated_data}")

retry_telegram_send = tenacity.retry(
    retry=tenacity.retry_if_exception_type((TelegramRetryAfter, TelegramBadRequest, TelegramNetworkError)),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
    stop=tenacity.stop_after_attempt(3),
    before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
    retry_error_callback=lambda retry_state: isinstance(retry_state.outcome.exception(), TelegramBadRequest) and
                                           "message is not modified" in str(retry_state.outcome.exception())
)

@retry_telegram_send
async def send_message_with_fallback(bot: Bot, chat_id: int, text: str, reply_markup=None, parse_mode=None, is_escaped: bool = False) -> Message:
    
    # Проверка, не является ли chat_id ID бота
    bot_info = await bot.get_me()
    bot_id = bot_info.id
    if chat_id == bot_id:
        logger.error(f"Попытка отправить сообщение боту с chat_id={chat_id}. Отправка отменена.")
        raise TelegramForbiddenError(message="Cannot send message to bot itself")

    try:
        # Экранируем текст только если parse_mode=ParseMode.MARKDOWN_V2 и is_escaped=False
        if parse_mode == ParseMode.MARKDOWN_V2 and not is_escaped:
            logger.debug(f"Экранирование текста для chat_id={chat_id}: {text[:100]}...")
            text = escape_md(text, version=2)
            logger.debug(f"Экранированный текст: {text[:100]}...")
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
        logger.debug(f"Сообщение успешно отправлено для chat_id={chat_id}")
        return message
    except TelegramBadRequest as e:
        if "message is too long" in str(e).lower():
            logger.warning(f"Сообщение слишком длинное для chat_id={chat_id}, обрезаем до 4000 символов")
            text = text[:4000] + "..."
            message = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            return message
        elif parse_mode == ParseMode.MARKDOWN_V2 and not is_escaped:
            logger.warning(f"Ошибка MarkdownV2 для chat_id={chat_id}: {e}, отправка без разметки")
            message = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=None
            )
            return message
        raise
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения для chat_id={chat_id}: {e}", exc_info=True)
        raise

@retry_telegram_send
async def send_photo_with_retry(bot: Bot, chat_id: int, photo: FSInputFile, caption: str = None, reply_markup=None, parse_mode=None) -> Message:
    """Отправка фото с повторными попытками."""
    if parse_mode == ParseMode.MARKDOWN_V2 and caption:
        caption = escape_md(caption, version=2)
    try:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except TelegramBadRequest as e:
        if "message is too long" in str(e).lower() and caption:
            logger.warning(f"Подпись слишком длинная для chat_id={chat_id}, обрезаем до 1024 символов")
            caption = caption[:1024] + "..."
            return await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        elif parse_mode == ParseMode.MARKDOWN_V2 and caption:
            logger.warning(f"Ошибка MarkdownV2 для chat_id={chat_id}: {e}, отправка без разметки")
            return await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        raise
    except Exception as e:
        logger.error(f"Ошибка отправки фото для chat_id={chat_id}: {e}", exc_info=True)
        raise

@retry_telegram_send
async def send_media_group_with_retry(bot: Bot, chat_id: int, media: list):
    """Отправка группы медиа с повторными попытками"""
    return await bot.send_media_group(chat_id=chat_id, media=media)

@retry_telegram_send
async def send_video_with_retry(bot: Bot, chat_id: int, video, caption: str = None, reply_markup=None, parse_mode=None):
    """Отправка видео с повторными попытками"""
    return await bot.send_video(chat_id=chat_id, video=video, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)

@tenacity.retry(
    retry=tenacity.retry_if_exception_type(ReplicateError),
    wait=tenacity.wait_exponential(multiplier=2, min=2, max=30),
    stop=tenacity.stop_after_attempt(4),
    before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
    reraise=True
)
async def run_replicate_async(model_id: str, input_params: dict):
    """Асинхронный запуск модели Replicate"""
    loop = asyncio.get_event_loop()
    replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    
    prompt_preview = input_params.get('prompt', 'No prompt')
    if isinstance(prompt_preview, str):
        prompt_preview = prompt_preview[:100] + ('...' if len(prompt_preview) > 100 else '')
    
    logger.info(f"Запуск Replicate model: {model_id} с параметрами (промпт): {prompt_preview}...")
    
    try:
        output = await loop.run_in_executor(None, lambda: replicate_client.run(model_id, input=input_params))
        logger.info(f"Replicate model {model_id} успешно завершен.")
        return output
    except Exception as e:
        logger.error(f"Ошибка выполнения Replicate model {model_id}: {e}")
        raise