import asyncio
import logging
import os
import io
from datetime import datetime, timedelta
from typing import List, Dict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.filters import Command
from database import get_payments_by_date, get_user_activity_metrics, get_generation_cost_log
from config import ADMIN_IDS, DATABASE_PATH
from generation_config import IMAGE_GENERATION_MODELS
from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback
import aiosqlite
from keyboards import create_admin_keyboard, create_admin_user_actions_keyboard


logger = logging.getLogger(__name__)

# Создание роутера для визуализации
visualization_router = Router()

async def show_visualization(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает меню выбора визуализации данных."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        await query.message.answer(
            escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN
        )
        return

    await state.clear()
    text = escape_md(
        "📉 Визуализация данных\n\n"
        "Выберите тип графика:"
    )
    keyboard = [
        [InlineKeyboardButton("📈 Платежи", callback_data="visualize_payments")],
        [InlineKeyboardButton("📊 Регистрации", callback_data="visualize_registrations")],
        [InlineKeyboardButton("📸 Генерации", callback_data="visualize_generations")],
        [InlineKeyboardButton("🔙 Админ-панель", callback_data="admin_panel")]
    ]
    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )

async def visualize_payments(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает график платежей за последние 30 дней."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        await query.message.answer(
            escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=30)
        payments = await get_payments_by_period(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))

        logger.info(f"Найдено {len(payments)} платежей за период {start_date} - {end_date}")

        dates = []
        amounts = []
        current_date = start_date
        while current_date <= end_date:
            dates.append(current_date)
            amounts.append(0.0)
            current_date += timedelta(days=1)

        for payment in payments:
            if payment[4] is None:
                logger.warning(f"Платеж {payment[3]} имеет пустую дату created_at")
                continue
            try:
                payment_date = datetime.strptime(payment[4], '%Y-%m-%d %H:%M:%S').date()
                if start_date <= payment_date <= end_date:
                    index = dates.index(payment_date)
                    amounts[index] += float(payment[2])
            except (ValueError, AttributeError) as e:
                logger.warning(f"Ошибка обработки даты платежа {payment[3]}: {e}")

        if not any(amounts):
            text = escape_md("⚠️ Нет данных о платежах за последние 30 дней.")
            await state.clear()
            await send_message_with_fallback(
                query.bot, user_id, text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        plt.figure(figsize=(12, 6))
        sns.set_style("whitegrid")
        plt.plot(dates, amounts, color='#4CAF50', linewidth=2, marker='o')
        plt.fill_between(dates, amounts, color=(76/255, 175/255, 80/255, 0.2))
        plt.title("Динамика платежей за последние 30 дней", fontsize=14, pad=10)
        plt.xlabel("Дата", fontsize=12)
        plt.ylabel("Сумма (RUB)", fontsize=12)
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=5))
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', dpi=100)
        buffer.seek(0)
        plt.close()

        text = escape_md("📈 График платежей за последние 30 дней:")
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")],
            [InlineKeyboardButton("🏠 Админ-панель", callback_data="admin_panel")]
        ])
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
        )

        await query.bot.send_photo(
            chat_id=user_id, photo=buffer, caption="График платежей"
        )
        buffer.close()
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при визуализации платежей: {e}", exc_info=True)
        text = escape_md("❌ Ошибка создания графика. Проверьте логи.")
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")]]),
            parse_mode=ParseMode.MARKDOWN
        )

async def visualize_registrations(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает график регистраций за последние 30 дней."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        await query.message.answer(
            escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        if not os.path.exists(DATABASE_PATH):
            logger.error(f"Файл базы данных не найден: {DATABASE_PATH}")
            text = escape_md("❌ Файл базы данных не найден. Обратитесь к администратору.")
            await state.clear()
            await send_message_with_fallback(
                query.bot, user_id, text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute(
                """
                SELECT DATE(created_at) as reg_date, COUNT(*) as count
                FROM users
                WHERE created_at BETWEEN ? AND ?
                GROUP BY DATE(created_at)
                ORDER BY reg_date
                """,
                (start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
            )
            registrations = await c.fetchall()

        dates = []
        counts = []
        current_date = start_date
        while current_date <= end_date:
            dates.append(current_date.strftime('%Y-%m-%d'))
            counts.append(0)
            current_date += timedelta(days=1)

        for reg in registrations:
            reg_date = reg['reg_date']
            if reg_date in dates:
                counts[dates.index(reg_date)] = reg['count']

        plt.figure(figsize=(12, 6))
        sns.set_style("whitegrid")
        plt.bar(dates, counts, color='#2196F3', edgecolor='#1976D2')
        plt.title("Динамика регистраций за последние 30 дней", fontsize=14, pad=10)
        plt.xlabel("Дата", fontsize=12)
        plt.ylabel("Количество регистраций", fontsize=12)
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', dpi=100)
        buffer.seek(0)
        plt.close()

        text = escape_md("📊 График регистраций за последние 30 дней:")
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")],
            [InlineKeyboardButton("🏠 Админ-панель", callback_data="admin_panel")]
        ])
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
        )

        await query.bot.send_photo(
            chat_id=user_id, photo=buffer, caption="График регистраций"
        )
        buffer.close()
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при визуализации регистраций: {e}", exc_info=True)
        text = escape_md("❌ Ошибка создания графика. Проверьте логи.")
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")]]),
            parse_mode=ParseMode.MARKDOWN
        )

async def visualize_generations(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает график генераций за последние 30 дней."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        await query.message.answer(
            escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        if not os.path.exists(DATABASE_PATH):
            logger.error(f"Файл базы данных не найден: {DATABASE_PATH}")
            text = escape_md("❌ Файл базы данных не найден. Обратитесь к администратору.")
            await state.clear()
            await send_message_with_fallback(
                query.bot, user_id, text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        log_entries = await get_generation_cost_log(start_date_str=start_date, end_date_str=end_date)

        dates = []
        generation_counts: Dict[str, List[int]] = {}
        current_date = datetime.strptime(start_date, '%Y-%m-%d')
        end_date_dt = datetime.strptime(end_date, '%Y-%m-%d')
        while current_date <= end_date_dt:
            dates.append(current_date.strftime('%Y-%m-%d'))
            current_date += timedelta(days=1)

        for entry in log_entries:
            model_id, units, _, created_at = entry
            date_str = str(created_at).split(' ')[0]
            if date_str in dates:
                if model_id not in generation_counts:
                    generation_counts[model_id] = [0] * len(dates)
                generation_counts[model_id][dates.index(date_str)] += units

        plt.figure(figsize=(12, 6))
        sns.set_style("whitegrid")
        colors = sns.color_palette("husl", len(generation_counts))

        for idx, (model_id, counts) in enumerate(generation_counts.items()):
            model_name = next(
                (m_data.get('name', model_id) for _, m_data in IMAGE_GENERATION_MODELS.items() if m_data.get('id') == model_id),
                model_id
            )
            plt.plot(dates, counts, label=model_name, color=colors[idx], linewidth=2)

        plt.title("Динамика генераций за последние 30 дней", fontsize=14, pad=10)
        plt.xlabel("Дата", fontsize=12)
        plt.ylabel("Количество генераций", fontsize=12)
        plt.xticks(rotation=45, ha='right')
        plt.legend(title="Модели", bbox_to_anchor=(1.05, 1), loc='upper left')
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', dpi=100)
        buffer.seek(0)
        plt.close()

        text = escape_md("📸 График генераций за последние 30 дней:")
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")],
            [InlineKeyboardButton("🏠 Админ-панель", callback_data="admin_panel")]
        ])
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
        )

        await query.bot.send_photo(
            chat_id=user_id, photo=buffer, caption="График генераций"
        )
        buffer.close()
        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при визуализации генераций: {e}", exc_info=True)
        text = escape_md("❌ Ошибка создания графика. Проверьте логи.")
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К визуализации", callback_data="admin_visualization")]]),
            parse_mode=ParseMode.MARKDOWN
        )

async def show_activity_stats(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает меню для запроса статистики активности пользователей."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        await query.message.answer(
            escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN
        )
        return

    await state.clear()
    await state.update_data(awaiting_activity_dates=True)
    text = escape_md(
        "📊 Введите даты для статистики активности в формате:\n"
        "`YYYY-MM-DD YYYY-MM-DD` (например, `2025-05-01 2025-05-26`)\n"
        "Или выберите предустановленный период:"
    )
    keyboard = [
        [InlineKeyboardButton("Последние 7 дней", callback_data="activity_7_days")],
        [InlineKeyboardButton("Последние 30 дней", callback_data="activity_30_days")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN
    )
    await state.set_state(BotStates.AWAITING_ACTIVITY_DATES)

async def handle_activity_stats(query: CallbackQuery, state: FSMContext, start_date: str, end_date: str) -> None:
    """Обрабатывает запрос статистики активности за указанный период."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await state.clear()
        await query.message.answer(
            escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        stats = await get_user_activity_metrics(start_date, end_date)
        if not stats:
            text = escape_md(f"🚫 Нет данных об активности за период {start_date} - {end_date}.")
            await state.clear()
            await send_message_with_fallback(
                query.bot, user_id, text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К активности", callback_data="admin_activity_stats")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        text = f"📊 Активность пользователей ({start_date} - {end_date})\n\n"
        for stat in stats[:10]:
            user_id_stat, username, messages, photos, videos, purchases = stat
            username_display = f"@{escape_md(username)}" if username else f"ID {user_id_stat}"
            text += (
                f"👤 {username_display} (ID: `{user_id_stat}`)\n"
                f"  • Сообщений: `{messages}`\n"
                f"  • Фото: `{photos}`\n"
                f"  • Видео: `{videos}`\n"
                f"  • Покупок: `{purchases}`\n\n"
            )

        if len(stats) > 10:
            text += f"_...и еще {len(stats) - 10} пользователей._"

        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Повторить запрос", callback_data="admin_activity_stats")],
            [InlineKeyboardButton("🔙 В админ-панель", callback_data="admin_panel")]
        ])
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Ошибка при получении статистики активности: {e}", exc_info=True)
        text = escape_md("❌ Ошибка получения данных. Проверьте логи.")
        await state.clear()
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К активности", callback_data="admin_activity_stats")]]),
            parse_mode=ParseMode.MARKDOWN
        )

async def handle_activity_dates_input(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод дат для статистики активности."""
    user_id = message.from_user.id
    user_data = await state.get_data()
    if not user_data.get('awaiting_activity_dates'):
        logger.warning(f"handle_activity_dates_input invoked without state for user_id={user_id}")
        await state.clear()
        await message.answer(
            escape_md("❌ Ошибка: действие не ожидается."),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К активности", callback_data="admin_activity_stats")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await state.update_data(awaiting_activity_dates=None)
    text = message.text.strip()

    try:
        dates = text.split()
        if len(dates) != 2:
            raise ValueError("Требуется две даты в формате `YYYY-MM-DD YYYY-MM-DD`")
        start_date, end_date = dates

        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date, '%Y-%m-%d')
        if start_date > end_date:
            raise ValueError("Дата начала не может быть позже даты окончания")

        logger.info(f"Обработка статистики активности для user_id={user_id} с {start_date} по {end_date}")
        await handle_activity_stats(
            CallbackQuery(query_id=None, from_user=message.from_user, message=message, data=None, bot=message.bot),
            state, start_date, end_date
        )
        await state.clear()
    except ValueError as e:
        logger.warning(f"Неверный формат дат от user_id={user_id}: {text}, error: {e}")
        text = escape_md(
            f"⚠️ Неверный формат: {str(e)}. Используйте `YYYY-MM-DD YYYY-MM-DD` (например, `2025-05-01 2025-05-26`)."
        )
        await state.update_data(awaiting_activity_dates=True)
        await message.answer(
            text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К активности", callback_data="admin_activity_stats")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        await state.set_state(BotStates.AWAITING_ACTIVITY_DATES)
    except Exception as e:
        logger.error(f"Ошибка обработки дат для user_id={user_id}: {e}", exc_info=True)
        text = escape_md("❌ Произошла ошибка при обработке дат. Попробуйте снова.")
        await state.clear()
        await message.answer(
            text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К активности", callback_data="admin_activity_stats")]]),
            parse_mode=ParseMode.MARKDOWN
        )

async def cancel(message: Message, state: FSMContext) -> None:
    """Отменяет все активные действия и сбрасывает контекст."""
    user_id = message.from_user.id
    await state.clear()
    text = escape_md("✅ Все действия отменены.")
    reply_markup = await create_admin_main() if user_id in ADMIN_IDS else await create_user_main_menu(user_id)
    await message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN
    )

# Регистрация обработчиков
@visualization_router.callback_query(
    lambda c: c.data in [
        "admin_visualization", "visualize_payments", "visualize_registrations",
        "visualize_generations", "admin_activity_stats"
    ] or c.data.startswith("activity_")
)
async def visualization_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    callback_data = query.data
    logger.debug(f"visualization_callback_handler: user_id={query.from_user.id}, callback_data={callback_data}")
    try:
        if callback_data == "admin_visualization":
            await show_visualization(query, state)
        elif callback_data == "visualize_payments":
            await visualize_payments(query, state)
        elif callback_data == "visualize_registrations":
            await visualize_registrations(query, state)
        elif callback_data == "visualize_generations":
            await visualize_generations(query, state)
        elif callback_data == "admin_activity_stats":
            await show_activity_stats(query, state)
        elif callback_data.startswith("activity_"):
            days = 7 if callback_data == "activity_7_days" else 30
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            await handle_activity_stats(query, state, start_date, end_date)
    except Exception as e:
        logger.error(f"Ошибка в visualization_callback_handler: {e}", exc_info=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку."),
            reply_markup=await create_admin_main(),
            parse_mode=ParseMode.MARKDOWN
        )