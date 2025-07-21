import logging
from aiogram import Bot
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode
from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback

logger = logging.getLogger(__name__)

async def error_handler(update: Message | CallbackQuery, bot: Bot, error: Exception) -> None:
    """Логирует ошибки, вызванные обновлениями."""
    logger.error(msg="Exception while handling an update:", exc_info=error)
    
    user_id = None
    if isinstance(update, Message):
        if update.from_user:
            user_id = update.from_user.id
        elif update.chat:
            user_id = update.chat.id
    elif isinstance(update, CallbackQuery):
        if update.from_user:
            user_id = update.from_user.id

    if user_id:
        if isinstance(error, TelegramBadRequest):
            error_message = str(error).lower()
            ignored_errors = [
                "message is not modified",
                "message to edit not found",
                "message to delete not found", 
                "query is too old",
                "message can't be edited",
                "message identifier not specified"
            ]
            
            if any(ignored_error in error_message for ignored_error in ignored_errors):
                logger.info(f"Suppressed user notification for a common BadRequest: {error} for user {user_id}")
                return
        
        try:
            error_message_user = escape_md(
                "😔 Произошла внутренняя ошибка. Мы уже работаем над ней. "
                "Пожалуйста, попробуйте позже или свяжитесь с поддержкой через /menu, "
                "если проблема не исчезнет."
            )
            
            await send_message_with_fallback(
                bot,
                user_id,
                text=error_message_user,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e_send:
            logger.error(f"Не удалось отправить сообщение об ошибке пользователю {user_id}: {str(e_send)}")
    else:
        logger.warning("Не удалось определить user_id для отправки ответа об ошибке.")