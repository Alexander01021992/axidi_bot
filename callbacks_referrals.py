# handlers/callbacks_referrals.py

import asyncio
import logging
import aiosqlite
from aiogram import Router, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from config import ADMIN_IDS, DATABASE_PATH
from database import check_database_user, is_user_blocked
from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback, safe_answer_callback
from keyboards import create_main_menu_keyboard, create_referral_keyboard, create_admin_keyboard

logger = logging.getLogger(__name__)

# Создание роутера для реферальных callback'ов
referrals_callbacks_router = Router()

async def handle_referrals_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает callback-запросы реферальной системы."""
    user_id = query.from_user.id
    await query.answer()

    if await is_user_blocked(user_id):
        await query.answer("🚫 Ваш аккаунт заблокирован.", show_alert=True)
        await query.message.answer(
            escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался выполнить callback: {query.data}")
        return

    callback_data = query.data
    logger.info(f"Callback от user_id={user_id}: {callback_data}")

    try:
        if callback_data == "referrals":
            await handle_referrals_menu_callback(query, state, user_id)
        elif callback_data == "referral_info":
            await handle_referral_info_callback(query, state, user_id)
        elif callback_data == "copy_referral_link":
            await handle_copy_referral_link_callback(query, state, user_id)
        elif callback_data == "referral_help":
            await handle_referral_help_callback(query, state, user_id)
        elif callback_data == "my_referrals":
            await handle_my_referrals_callback(query, state, user_id)
        else:
            logger.error(f"Неизвестный callback_data: {callback_data} для user_id={user_id}")
            await query.message.answer(
                escape_md("❌ Неизвестное действие. Попробуйте снова или обратитесь в поддержку.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )

    except Exception as e:
        logger.error(f"Ошибка в обработчике callback для user_id={user_id}, data={callback_data}: {e}", exc_info=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку.", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_referrals_menu_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Меню реферальной программы."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            await cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
            total_referrals = (await cursor.fetchone())[0]
            await cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND status = 'completed'", (user_id,))
            paid_referrals = (await cursor.fetchone())[0]
            await cursor.execute("SELECT SUM(reward_photos) FROM referral_rewards WHERE referrer_id = ?", (user_id,))
            bonus_photos_result = await cursor.fetchone()
            bonus_photos = bonus_photos_result[0] if bonus_photos_result[0] is not None else 0
    except Exception as e:
        logger.error(f"Ошибка получения данных рефералов для user_id={user_id}: {e}")
        total_referrals = 0
        paid_referrals = 0
        bonus_photos = 0
    
    bot_username = (await query.bot.get_me()).username.lstrip('@')
    referral_link = f"t.me/{bot_username}?start=ref_{user_id}"
    text = (
        escape_md("👥 Реферальная программа", version=2) + "\n\n" +
        escape_md("📊 Ваша статистика:", version=2) + "\n" +
        escape_md(f"• Приглашено друзей: {total_referrals}", version=2) + "\n" +
        escape_md(f"• Совершили покупку: {paid_referrals}", version=2) + "\n" +
        escape_md(f"• Получено бонусов: {bonus_photos} печенек", version=2) + "\n\n" +
        escape_md("🎁 За каждую покупку друга вы получаете 10% от суммы в виде печенек!", version=2) + "\n\n" +
        escape_md("🔗 Ваша реферальная ссылка:", version=2) + "\n" +
        f"`t\.me/{escape_md(bot_username, version=2)}?start=ref_{escape_md(str(user_id), version=2)}`"
    )
    keyboard = await create_referral_keyboard(user_id, bot_username)
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Меню рефералов отправлено для user_id={user_id}: {text}")

async def handle_referral_info_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Информация о реферальной программе."""
    text = (
        escape_md("🎁 Как работает реферальная программа:", version=2) + "\n\n" +
        escape_md("1. Поделитесь своей ссылкой с друзьями", version=2) + "\n" +
        escape_md("2. Друг регистрируется по вашей ссылке", version=2) + "\n" +
        escape_md("3. За каждую покупку друга вы получаете 10% от суммы в виде печенек", version=2) + "\n" +
        escape_md("4. Друг получает 1 бонусную печеньку при первой покупке", version=2) + "\n\n" +
        escape_md("💡 Советы:", version=2) + "\n" +
        escape_md("• Расскажите друзьям о возможностях бота", version=2) + "\n" +
        escape_md("• Покажите примеры своих генераций", version=2) + "\n" +
        escape_md("• Поделитесь ссылкой в соцсетях", version=2) + "\n\n" +
        escape_md("🚀 Приглашайте больше друзей - получайте больше бонусов!", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", callback_data="copy_referral_link")],
        [InlineKeyboardButton(text="🔙 К рефералам", callback_data="referrals")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Информация о рефералах отправлена для user_id={user_id}: {text}")

async def handle_copy_referral_link_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Копирование реферальной ссылки."""
    bot_username = (await query.bot.get_me()).username.lstrip('@')
    referral_link = f"t.me/{bot_username}?start=ref_{user_id}"
    text = (
        escape_md("🔗 Ваша реферальная ссылка:", version=2) + "\n\n" +
        f"`t\.me/{escape_md(bot_username, version=2)}?start=ref_{escape_md(str(user_id), version=2)}`\n\n" +
        escape_md("📋 Скопируйте и поделитесь с друзьями!", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться в Telegram", url=f"https://t.me/share/url?url={referral_link}&text=Попробуй крутой AI-бот! 🤖")],
        [InlineKeyboardButton(text="🔙 К рефералам", callback_data="referrals")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    await safe_answer_callback(query, "📋 Ссылка готова к копированию!", show_alert=True)
    logger.debug(f"Реферальная ссылка отправлена для user_id={user_id}: {text}")

async def handle_referral_help_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Помощь по рефералам."""
    text = (
        escape_md("❓ Помощь по рефералам", version=2) + "\n\n" +
        escape_md("🔗 Как пригласить друга:", version=2) + "\n" +
        escape_md("1. Скопируйте свою реферальную ссылку", version=2) + "\n" +
        escape_md("2. Отправьте её другу", version=2) + "\n" +
        escape_md("3. Друг должен перейти по ссылке и запустить бота", version=2) + "\n" +
        escape_md("4. После первой покупки друга вы получите бонус", version=2) + "\n\n" +
        escape_md("❓ Частые вопросы:", version=2) + "\n" +
        escape_md("• Сколько можно пригласить? Без ограничений!", version=2) + "\n" +
        escape_md("• Когда начисляется бонус? Сразу после покупки", version=2) + "\n" +
        escape_md("• Сгорают ли бонусы? Нет, остаются навсегда", version=2) + "\n\n" +
        escape_md("💬 Если остались вопросы - обратитесь в поддержку!", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="🔙 К рефералам", callback_data="referrals")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Помощь по рефералам отправлена для user_id={user_id}: {text}")

async def handle_my_referrals_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показ рефералов пользователя и бонусов."""
    logger.debug(f"handle_my_referrals: user_id={user_id}")
    try:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("SELECT referred_id, status, created_at, completed_at FROM referrals WHERE referrer_id = ?", (user_id,))
            my_referrals = await c.fetchall()
            await c.execute("SELECT SUM(reward_photos) FROM referral_rewards WHERE referrer_id = ?", (user_id,))
            total_bonuses_result = await c.fetchone()
            total_bonuses = total_bonuses_result[0] if total_bonuses_result[0] is not None else 0
        logger.debug(f"Найдено {len(my_referrals)} рефералов для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка получения рефералов для user_id={user_id}: {e}", exc_info=True)
        my_referrals = []
        total_bonuses = 0
    
    bot_username = (await query.bot.get_me()).username.lstrip('@')
    referral_link = f"t.me/{bot_username}?start=ref_{user_id}"
    text = (
        escape_md("👥 Твои рефералы:", version=2) + "\n\n"
    )
    active_referrals = 0
    if my_referrals:
        text += escape_md(f"Всего приглашено: {len(my_referrals)} человек", version=2) + "\n\n"
        for ref in my_referrals[-10:]:
            ref_user_id = ref['referred_id']
            ref_date = ref['created_at']
            ref_status = ref['status']
            completed_at = ref['completed_at']
            ref_data = await check_database_user(ref_user_id)
            has_purchased = ref_status == 'completed'
            status = "💳 Совершил покупку" if has_purchased else "⏳ Без покупок"
            if has_purchased:
                active_referrals += 1
            text += (
                escape_md(f"• ID {ref_user_id} - {ref_date} ({status})", version=2) + "\n"
            )
            if completed_at and has_purchased:
                text += escape_md(f"  Завершено: {completed_at}", version=2) + "\n"
    else:
        text += escape_md("_Ты еще никого не пригласил_", version=2) + "\n"
        logger.info(f"Нет рефералов для user_id={user_id}")
    
    text += (
        "\n" +
        escape_md("📊 Статистика бонусов:", version=2) + "\n" +
        escape_md(f"👥 Рефералов с покупками: {active_referrals}", version=2) + "\n" +
        escape_md(f"🎁 Получено бонусных печенек: {total_bonuses}", version=2) + "\n\n" +
        escape_md("🔗 Твоя реферальная ссылка:", version=2) + "\n" +
        f"`t\.me/{escape_md(bot_username, version=2)}?start=ref_{escape_md(str(user_id), version=2)}`\n\n" +
        escape_md("_За каждую покупку друга ты получишь 10% от суммы в виде печенек!_", version=2)
    )
    await query.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться ссылкой", url=f"https://t.me/share/url?url={referral_link}&text=Попробуй крутой AI-бот! 🤖")],
            [InlineKeyboardButton(text="🔙 В статистику", callback_data="user_stats")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Рефералы отправлены для user_id={user_id}: {text}")

async def cancel(message: Message, state: FSMContext) -> None:
    """Отменяет все активные действия и сбрасывает контекст."""
    user_id = message.from_user.id
    await state.clear()
    text = escape_md("✅ Все действия отменены.", version=2)
    reply_markup = await create_admin_keyboard() if user_id in ADMIN_IDS else await create_main_menu_keyboard(user_id)
    await message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )

# Регистрация обработчиков
@referrals_callbacks_router.callback_query(lambda c: c.data and (c.data.startswith("referral") or c.data == "my_referrals" or c.data == "copy_referral_link"))
async def referrals_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает callback-запросы реферальной системы."""
    await handle_referrals_callback(query, state)