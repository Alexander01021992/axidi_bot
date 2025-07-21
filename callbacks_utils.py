import asyncio
import logging
import os
import aiosqlite
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from datetime import datetime
from config import ADMIN_IDS
from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback, safe_answer_callback
from database import is_user_blocked, get_user_payments
from keyboards import create_main_menu_keyboard, create_admin_keyboard
from bot_counter import bot_counter
import os
logger = logging.getLogger(__name__)

# Создание роутера для утилитарных callback'ов
utils_callbacks_router = Router()

async def handle_utils_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает общие и вспомогательные callback-запросы."""
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
        if callback_data == "back_to_menu":
            await handle_back_to_menu_callback(query, state, user_id)
        elif callback_data == "support":
            await handle_support_callback(query, state, user_id)
        elif callback_data == "faq":
            await handle_faq_callback(query, state, user_id)
        elif callback_data.startswith("faq_"):
            topic = callback_data.replace("faq_", "")
            await handle_faq_topic_callback(query, state, user_id, topic)
        elif callback_data == "help":
            from handlers.commands import help_command
            await help_command(query.message, state)
        elif callback_data == "user_guide":
            await handle_user_guide_callback(query, state, user_id)
        elif callback_data == "share_result":
            await handle_share_result_callback(query, state, user_id)
        elif callback_data == "payment_history":
            await handle_payment_history_callback(query, state, user_id)
        elif callback_data == "tariff_info":
            await handle_tariff_info_callback(query, state, user_id)
        elif callback_data == "category_info":
            await handle_category_info_callback(query, state, user_id)
        elif callback_data == "compare_tariffs":
            await handle_compare_tariffs_callback(query, state, user_id)
        elif callback_data == "aspect_ratio_info":
            await handle_aspect_ratio_info_callback(query, state, user_id)
        elif callback_data == "check_training":
            from handlers.commands import check_training
            await check_training(query.message, state, user_id)  # Исправлено: добавлен user_id
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

async def handle_back_to_menu_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Возврат в главное меню."""
    await state.clear()
    await query.answer()
    
    # Проверка данных подписки
    try:
        subscription_data = await check_database_user(user_id)
        if not subscription_data or len(subscription_data) < 11:
            logger.error(f"Неполные данные подписки для user_id={user_id}")
            await query.message.answer(
                escape_md("❌ Ошибка сервера! Попробуйте позже.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
            
        generations_left = subscription_data[0]
        avatar_left = subscription_data[1]
        
        # Формируем текст меню с счётчиком
        total = await bot_counter.get_total_count()
        formatted = bot_counter.format_number(total)
        menu_text = (
            f"🎨 PixelPie | 👥 {formatted} Пользователей\n"
            f"🌈 Добро пожаловать в главное меню!\n"
            f"Что вы хотите создать сегодня? 😊\n"
            f"📸 Сгенерировать — Создайте Ваши фото или Видео\n"
            f"🎭 Мои аватары — создайте новый аватар или выберите активный\n"
            f"💎 Купить пакет — пополните баланс для новых генераций\n"
            f"👤 Личный кабинет — Ваш баланс, Статистика и История\n"
            f"💬 Поддержка — мы всегда готовы помочь! 24/7\n"
            f"Выберите нужный пункт с помощью кнопок ниже 👇"
        )
        
        main_menu_keyboard = await create_main_menu_keyboard(user_id)
        
        # Удаляем старые видео если есть
        await delete_all_videos(state, user_id, query.bot)
        
        # Отправляем видео с меню
        if generations_left > 0 or avatar_left > 0 or user_id in ADMIN_IDS:
            menu_video_path = "images/welcome1.mp4"
            try:
                if os.path.exists(menu_video_path):
                    from aiogram.types import FSInputFile
                    video_file = FSInputFile(menu_video_path)
                    video_message = await query.bot.send_video(
                        chat_id=user_id,
                        video=video_file,
                        caption=escape_md(menu_text, version=2),
                        reply_markup=main_menu_keyboard,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    await state.update_data(menu_video_message_id=video_message.message_id)
                else:
                    logger.warning(f"Видео меню не найдено: {menu_video_path}")
                    await query.message.answer(
                        escape_md(menu_text, version=2),
                        reply_markup=main_menu_keyboard,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки видео: {e}")
                await query.message.answer(
                    escape_md(menu_text, version=2),
                    reply_markup=main_menu_keyboard,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        else:
            await query.message.answer(
                escape_md(menu_text, version=2),
                reply_markup=main_menu_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
    except Exception as e:
        logger.error(f"Ошибка в back_to_menu: {e}")
        await query.message.answer(
            escape_md("❌ Ошибка. Используйте /menu", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )

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

async def handle_support_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Поддержка."""
    await query.answer()
    text = (
        escape_md("💬 Поддержка", version=2) + "\n\n" +
        escape_md("Если у вас возникли вопросы или проблемы:", version=2) + "\n\n" +
        escape_md("📞 Напишите в поддержку", version=2) + "\n" +
        escape_md("❓ Изучите частые вопросы", version=2) + "\n" +
        escape_md("📖 Прочитайте инструкции", version=2) + "\n\n" +
        escape_md("🤝 Мы поможем решить любую проблему!", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/AXIDI_Help")],
        [InlineKeyboardButton(text="❓ Частые вопросы", callback_data="faq")],
        [InlineKeyboardButton(text="📖 Руководство", callback_data="user_guide")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Сообщение поддержки отправлено для user_id={user_id}: {text}")

async def handle_faq_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Частые вопросы."""
    text = (
        escape_md("❓ Частые вопросы", version=2) + "\n\n" +
        escape_md("Выберите интересующую вас тему:", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Как создать фото?", callback_data="faq_photo")],
        [InlineKeyboardButton(text="🎬 Как создать видео?", callback_data="faq_video")],
        [InlineKeyboardButton(text="👤 Как создать аватар?", callback_data="faq_avatar")],
        [InlineKeyboardButton(text="💡 Советы по промптам", callback_data="faq_prompts")],
        [InlineKeyboardButton(text="❓ Частые проблемы", callback_data="faq_comments")],
        [InlineKeyboardButton(text="💎 О подписке", callback_data="faq_subscription")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"FAQ отправлено для user_id={user_id}: {text}")

async def handle_faq_topic_callback(query: CallbackQuery, state: FSMContext, user_id: int, topic: str) -> None:
    """Обработчик конкретной темы FAQ."""
    faq_texts = {
        "photo": {
            "title": "📸 Как создать фото?",
            "text": (
                "📸 Создание фото\n\n"
                "1️⃣ Нажмите кнопку 'Сгенерировать'\n"
                "2️⃣ Выберите способ создания:\n"
                "   • С аватаром - для персонализированных фото\n"
                "   • По референсу - для похожих изображений\n\n"
                "3️⃣ Выберите стиль или введите свой промпт\n"
                "4️⃣ Укажите соотношение сторон\n"
                "5️⃣ Дождитесь результата\n\n"
                "💡 Совет: Чем детальнее промпт, тем лучше результат!"
            )
        },
        "video": {
            "title": "🎬 Как создать видео?",
            "text": (
                "🎬 Создание видео\n\n"
                "1️⃣ Нажмите кнопку 'Сгенерировать'\n"
                "2️⃣ Выберите 'AI-видео'\n"
                "3️⃣ Загрузите исходное изображение\n"
                "4️⃣ Опишите желаемую анимацию\n"
                "5️⃣ Дождитесь обработки\n\n"
                "⏱ Генерация видео занимает 5-15 минут\n"
                "📹 Длительность видео: 3-5 секунд"
            )
        },
        "avatar": {
            "title": "👤 Как создать аватар?",
            "text": (
                "👤 Создание аватара\n\n"
                "1️⃣ Нажмите 'Создать аватар' в личном кабинете\n"
                "2️⃣ Загрузите 10-20 фото:\n"
                "   • Разные ракурсы\n"
                "   • Хорошее освещение\n"
                "   • Четкое лицо\n\n"
                "3️⃣ Укажите имя и триггер-слово\n"
                "4️⃣ Дождитесь обучения (30-40 минут)\n\n"
                "✅ После готовности используйте аватар для генераций!"
            )
        },
        "prompts": {
            "title": "💡 Советы по промптам",
            "text": (
                "💡 Советы по промптам\n\n"
                "✅ Хорошие практики:\n"
                "• Описывайте детально\n"
                "• Указывайте стиль и настроение\n"
                "• Добавляйте технические детали\n\n"
                "📝 Пример хорошего промпта:\n"
                "'Портрет в стиле ренессанс, мягкое освещение, "
                "детализированный фон, профессиональное фото'\n\n"
                "❌ Избегайте:\n"
                "• Слишком коротких описаний\n"
                "• Противоречивых требований\n"
                "• Нереалистичнных ожиданий"
            )
        },
        "comments": {
            "title": "❓ Решение проблем",
            "text": (
                "❓ Частые проблемы и решения\n\n"
                "🔴 Плохое качество фото:\n"
                "→ Используйте более детальный промпт\n\n"
                "🔴 Аватар не похож:\n"
                "→ Загрузите больше качественных фото\n\n"
                "🔴 Долгая генерация:\n"
                "→ Это нормально, видео требует времени\n\n"
                "🔴 Ошибка генерации:\n"
                "→ Попробуйте еще раз или обратитесь в поддержку\n\n"
                "💬 Не нашли ответ? Напишите в поддержку!"
            )
        },
        "subscription": {
            "title": "💎 О подписке",
            "text": (
                "💎 Информация о подписке\n\n"
                "📦 Доступные пакеты:\n"
                "• Старт - для знакомства с сервисом\n"
                "• Стандарт - оптимальный выбор\n"
                "• Премиум - максимум возможностей\n\n"
                "✅ Что входит:\n"
                "• Генерации фото\n"
                "• Создание аватаров\n"
                "• Генерация видео\n\n"
                "💰 Ресурсы не сгорают и остаются навсегда!"
            )
        }
    }
    if topic not in faq_texts:
        await safe_answer_callback(query, "❌ Тема не найдена", show_alert=True)
        return
    info = faq_texts[topic]
    escaped_text = escape_md(info["text"], version=2)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❓ Другие вопросы", callback_data="faq")],
        [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        escaped_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"FAQ тема {topic} отправлена для user_id={user_id}: {escaped_text}")

async def handle_user_guide_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Показывает руководство пользователя."""
    text = (
        escape_md("📖 Руководство пользователя", version=2) + "\n\n" +
        escape_md("🎯 Быстрый старт:", version=2) + "\n" +
        escape_md("1. Купите пакет печенек", version=2) + "\n" +
        escape_md("2. Создайте свой аватар", version=2) + "\n" +
        escape_md("3. Генерируйте уникальные фото", version=2) + "\n\n" +
        escape_md("📸 Типы генерации:", version=2) + "\n" +
        escape_md("• С аватаром - персональные фото", version=2) + "\n" +
        escape_md("• По референсу - копирование стиля", version=2) + "\n" +
        escape_md("• AI-видео - анимированные ролики", version=2) + "\n\n" +
        escape_md("💡 Советы для лучших результатов:", version=2) + "\n" +
        escape_md("• Используйте детальные описания", version=2) + "\n" +
        escape_md("• Экспериментируйте со стилями", version=2) + "\n" +
        escape_md("• Загружайте качественные фото для аватара", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Попробовать", callback_data="generate_menu")],
        [InlineKeyboardButton(text="❓ Вопросы", callback_data="faq")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="support")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Руководство пользователя отправлено для user_id={user_id}: {text}")

async def handle_share_result_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Обработчик поделиться результатом."""
    bot_username = (await query.bot.get_me()).username.lstrip('@')
    share_text = escape_md("Посмотри, какие крутые фото я создал с помощью AI! 🤖✨", version=2)
    share_url = f"https://t.me/share/url?url=t.me/{bot_username}&text={share_text}"
    text = (
        escape_md("📤 Поделись своими результатами!", version=2) + "\n\n" +
        escape_md("Покажи друзьям, какие крутые фото ты создаешь с помощью AI!", version=2) + "\n" +
        escape_md("Возможно, они тоже захотят попробовать.", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Поделиться в Telegram", url=share_url)],
        [InlineKeyboardButton(text="🔄 Создать еще", callback_data="generate_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Сообщение для поделиться результатами отправлено для user_id={user_id}: {text}")

async def handle_payment_history_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """История платежей."""
    try:
        payments = await get_user_payments(user_id, limit=10)
        if not payments:
            text = (
                escape_md("💳 История платежей", version=2) + "\n\n" +
                escape_md("У вас пока нет платежей.", version=2) + "\n" +
                escape_md("Оформите первый пакет!", version=2)
            )
        else:
            text = escape_md("💳 История платежей", version=2) + "\n\n"
            for payment in payments:
                payment_id, payment_type, amount, created_at = payment[:4]
                if created_at:
                    try:
                        created_at_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                        date_str = created_at_dt.strftime("%d.%m.%Y")
                    except ValueError as e:
                        logger.warning(f"Ошибка формата created_at для payment_id={payment_id}: {created_at}, ошибка: {e}")
                        date_str = "Некорректная дата"
                else:
                    date_str = "Неизвестно"
                amount_str = f"{amount:.0f}₽" if amount is not None else "0₽"
                text += escape_md(f"📅 {date_str} • {amount_str}", version=2) + "\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить", callback_data="subscribe")],
            [InlineKeyboardButton(text="🔙 В профиль", callback_data="user_profile")]
        ])
        await query.message.answer(
            text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"История платежей отправлена для user_id={user_id}: {text}")
    except Exception as e:
        logger.error(f"Ошибка получения истории платежей для user_id={user_id}: {e}", exc_info=True)
        await safe_answer_callback(query, "❌ Ошибка получения истории", show_alert=True)
        await query.message.answer(
            escape_md("❌ Ошибка получения истории платежей. Попробуйте позже.", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_tariff_info_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Информация о тарифах."""
    text = (
        escape_md("💎 Информация о тарифах", version=2) + "\n\n" +
        escape_md("📸 Печеньки - ресурс для генерации изображений", version=2) + "\n" +
        escape_md("👤 Аватары - ресурс для создания персональных моделей", version=2) + "\n\n" +
        escape_md("🔄 Как это работает:", version=2) + "\n" +
        escape_md("1. Покупаете пакет печенек", version=2) + "\n" +
        escape_md("2. Создаете аватар (тратится 1 аватар или 590₽)", version=2) + "\n" +
        escape_md("3. Генерируете фото с аватаром (тратятся печеньки)", version=2) + "\n\n" +
        escape_md("💰 Наши цены:", version=2) + "\n" +
        escape_md("📸 От 399₽ за 10 печенек (стартовый)", version=2) + "\n" +
        escape_md("📸 До 4599₽ за 250 печенек + аватар (максимум)", version=2) + "\n" +
        escape_md("👤 Отдельный аватар - 590₽", version=2) + "\n\n" +
        escape_md("🎁 При первой покупке - аватар в подарок!", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Выбрать пакет", callback_data="subscribe")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Информация о тарифах отправлена для user_id={user_id}: {text}")

async def handle_category_info_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Информация о категориях."""
    text = (
        escape_md("📋 Категории контента", version=2) + "\n\n" +
        escape_md("🎨 Фотосессия - создание фото с вашим аватаром", version=2) + "\n" +
        escape_md("🖼 Фото по референсу - генерация по загруженному изображению", version=2) + "\n" +
        escape_md("🎬 AI-видео - создание видеороликов", version=2) + "\n\n" +
        escape_md("ℹ️ Для фотосессии нужен обученный аватар.", version=2) + "\n" +
        escape_md("ℹ️ Для остальных функций аватар не требуется.", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Попробовать", callback_data="generate_menu")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Информация о категориях отправлена для user_id={user_id}: {text}")

async def handle_compare_tariffs_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Сравнение тарифов."""
    text = (
        escape_md("💎 Сравнение тарифов", version=2) + "\n\n" +
        escape_md("📸 10 печенек - 399₽ (39.9₽ за фото)", version=2) + "\n" +
        escape_md("📸 30 печенек - 599₽ (20₽ за фото)", version=2) + "\n" +
        escape_md("📸 70 печенек - 1199₽ (17.1₽ за фото)", version=2) + "\n" +
        escape_md("📸 170 печенек + аватар - 3119₽ (18.3₽ за фото)", version=2) + "\n" +
        escape_md("📸 250 печенек + аватар - 4599₽ (18.4₽ за фото)", version=2) + "\n" +
        escape_md("👤 1 аватар - 590₽", version=2) + "\n\n" +
        escape_md("💡 Самый выгодный: 70 печенек за 1199₽!", version=2) + "\n" +
        escape_md("🎁 Больше всего контента: 250 печенек + аватар!", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Выбрать пакет", callback_data="subscribe")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Сравнение тарифов отправлено для user_id={user_id}: {text}")

async def handle_aspect_ratio_info_callback(query: CallbackQuery, state: FSMContext, user_id: int) -> None:
    """Информация о соотношениях сторон."""
    text = (
        escape_md("📐 Соотношения сторон", version=2) + "\n\n" +
        escape_md("📱 Квадратные: идеально для соцсетей", version=2) + "\n" +
        escape_md("🖥️ Горизонтальные: для широких кадров", version=2) + "\n" +
        escape_md("📲 Вертикальные: для портретов и Stories", version=2) + "\n\n" +
        escape_md("💡 Выберите подходящий формат в зависимости от того, где планируете использовать изображение.", version=2)
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 К выбору формата", callback_data="back_to_aspect_selection")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
    ])
    await query.message.answer(
        text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Информация о соотношениях сторон отправлена для user_id={user_id}: {text}")

async def cancel(message: Message, state: FSMContext) -> None:
    """Отменяет все активные действия и сбрасывает контекст."""
    user_id = message.from_user.id
    await state.clear()
    text = escape_md("✅ Все действия отменены.", version=2)
    reply_markup = await create_admin_keyboard() if user_id in ADMIN_IDS else await create_main_menu_keyboard(user_id)
    await message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Действия отменены для user_id={user_id}: {text}")

# Регистрация обработчиков
@utils_callbacks_router.callback_query(
    lambda c: c.data in [
        "back_to_menu", "support", "faq", "help", "user_guide", "share_result",
        "payment_history", "tariff_info", "category_info", "compare_tariffs",
        "aspect_ratio_info", "check_training"
    ] or c.data.startswith("faq_")
)
async def utils_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    await handle_utils_callback(query, state)