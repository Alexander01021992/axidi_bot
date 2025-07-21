# handlers/payments.py

import asyncio
import logging
import os
import uuid
import pytz
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict
from aiogram import Router, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.filters import Command
from database import get_payments_by_date, get_generation_cost_log, get_registrations_by_date
from config import ADMIN_IDS, DATABASE_PATH
from generation_config import IMAGE_GENERATION_MODELS
from excel_utils import create_payments_excel, create_registrations_excel
from keyboards import create_admin_keyboard
from handlers.utils import safe_escape_markdown as escape_md, send_message_with_fallback

logger = logging.getLogger(__name__)

# Создание роутера для платежей
payments_router = Router()

# Функция для экранирования символов в Markdown V2
def escape_md_v2(text: str) -> str:
    """Экранирует специальные символы для ParseMode.MARKDOWN_V2."""
    characters_to_escape = r'_[]()*~`#+-=|{}!.>'
    for char in characters_to_escape:
        text = text.replace(char, f'\\{char}')
    return text

async def show_payments_menu(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает меню выбора периода для статистики платежей и регистраций."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            query.bot, user_id, escape_md_v2("❌ У вас нет прав для доступа к этой функции\."),
            reply_markup=await create_admin_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        msk_tz = pytz.timezone('Europe/Moscow')
        today = datetime.now(msk_tz).strftime('%Y-%m-%d')
        yesterday = (datetime.now(msk_tz) - timedelta(days=1)).strftime('%Y-%m-%d')
        last_7_days_start = (datetime.now(msk_tz) - timedelta(days=7)).strftime('%Y-%m-%d')
        last_30_days_start = (datetime.now(msk_tz) - timedelta(days=30)).strftime('%Y-%m-%d')

        # Проверка корректности формата дат
        for date_str in [today, yesterday, last_7_days_start, last_30_days_start]:
            datetime.strptime(date_str, '%Y-%m-%d')

        # Изменение: Формат дат не экранируем, так как он внутри ` ` и безопасен
        text = (
            escape_md("📈 Статистика платежей и регистраций\n\n"
                         "Выберите период для получения статистики или введите даты вручную в формате:\n") +
            "`YYYY-MM-DD` (для одного дня)\nили\n`YYYY-MM-DD YYYY-MM-DD` (для диапазона)\.\n\n" +
            escape_md_v2("Пример:\n") +
            f"`{today}` или `{last_30_days_start} {today}`"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data=f"payments_date_{today}_{today}")],
            [InlineKeyboardButton(text="Вчера", callback_data=f"payments_date_{yesterday}_{yesterday}")],
            [InlineKeyboardButton(text="Последние 7 дней", callback_data=f"payments_date_{last_7_days_start}_{today}")],
            [InlineKeyboardButton(text="Последние 30 дней", callback_data=f"payments_date_{last_30_days_start}_{today}")],
            [InlineKeyboardButton(text="Ввести даты вручную", callback_data="payments_manual_date")],
            [InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_panel")]
        ])

        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Ошибка в show_payments_menu для user_id={user_id}: {e}", exc_info=True)
        await send_message_with_fallback(
            query.bot, user_id, escape_md_v2("❌ Ошибка при отображении меню\. Попробуйте снова\."),
            reply_markup=await create_admin_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_payments_date(query: CallbackQuery, state: FSMContext, start_date: str, end_date: str) -> None:
    """Обрабатывает запрос статистики платежей и регистраций за указанный период."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            query.bot, user_id, escape_md_v2("❌ У вас нет прав для доступа к этой функции\."),
            reply_markup=await create_admin_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        logger.warning(f"Неверный формат дат от user_id={user_id}: {start_date} - {end_date}")
        text = escape_md_v2("❌ Неверный формат дат\. Используйте YYYY-MM-DD, например, 2025-05-26\.")
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_keyboard(user_id), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        payments = await get_payments_by_date(start_date, end_date)
        registrations = await get_registrations_by_date(start_date, end_date)

        payments_file_path = None
        registrations_file_path = None

        if payments:
            payments_filename = f"payments_{start_date}_{end_date}_{uuid.uuid4().hex[:8]}.xlsx"
            payments_file_path = create_payments_excel(payments, payments_filename, start_date, end_date)
        else:
            logger.info(f"Платежи за период {start_date} - {end_date} не найдены.")

        if registrations:
            registrations_filename = f"registrations_{start_date}_{end_date}_{uuid.uuid4().hex[:8]}.xlsx"
            registrations_file_path = create_registrations_excel(
                registrations, registrations_filename, start_date if start_date == end_date else f"{start_date} - {end_date}"
            )
        else:
            logger.info(f"Регистрации за период {start_date} - {end_date} не найдены.")

        total_payments = len(payments)
        total_amount = sum(p[2] for p in payments if p[2]) if payments else 0
        total_registrations = len(registrations)

        period_text = start_date if start_date == end_date else f"{start_date} - {end_date}"
        text = (
            f"📈 Статистика за {period_text} (MSK)\n\n" +
            escape_md_v2("💰 Платежи:\n") +
            f"🔢 Всего платежей: {total_payments}\n" +
            f"💵 Общая сумма: {total_amount:.2f} RUB\n\n" +
            escape_md_v2("👥 Регистрации:\n") +
            f"🔢 Всего новых пользователей: {total_registrations}\n\n" +
            escape_md("📊 Детали в прикрепленных файлах\.")
        )

        if not payments and not registrations:
            text = f"🚫 За {period_text} (MSK) нет ни платежей, ни новых регистраций\."
            await send_message_with_fallback(
                query.bot, user_id, escape_md_v2(text),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 К выбору периода", callback_data="admin_payments")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К выбору периода", callback_data="admin_payments")],
                [InlineKeyboardButton(text="🏠 Админ-панель", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        if payments_file_path and os.path.exists(payments_file_path):
            document = FSInputFile(path=payments_file_path, filename=payments_filename)
            await query.bot.send_document(
                chat_id=user_id,
                document=document,
                caption=escape_md_v2("Отчет по платежам за ") + f"{period_text} (MSK)"
            )
            os.remove(payments_file_path)
            logger.info(f"Временный файл платежей {payments_file_path} удален.")

        if registrations_file_path and os.path.exists(registrations_file_path):
            document = FSInputFile(path=registrations_file_path, filename=registrations_filename)
            await query.bot.send_document(
                chat_id=user_id,
                document=document,
                caption=escape_md_v2("Отчет по новым регистрациям за ") + f"{period_text} (MSK)"
            )
            os.remove(registrations_file_path)
            logger.info(f"Временный файл регистраций {registrations_file_path} удален.")

    except Exception as e:
        logger.error(f"Ошибка обработки отчета за {start_date} - {end_date} для user_id={user_id}: {e}", exc_info=True)
        text = escape_md_v2("❌ Ошибка при создании отчета\. Проверьте логи\.")
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_keyboard(user_id), parse_mode=ParseMode.MARKDOWN_V2
        )
        for file_path in [payments_file_path, registrations_file_path]:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Временный файл {file_path} удален после ошибки.")
                except Exception as e_remove:
                    logger.error(f"Ошибка удаления файла {file_path}: {e_remove}")

async def handle_manual_date_input(query: CallbackQuery, state: FSMContext) -> None:
    """Инициирует ввод дат вручную для статистики платежей и регистраций."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            query.bot, user_id, escape_md_v2("❌ У вас нет прав для доступа к этой функции\."),
            reply_markup=await create_admin_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    await state.update_data(awaiting_payments_date=True)
    # Изменение: Формат дат не экранируем, так как он внутри ` ` и безопасен
    text = (
        escape_md("📅 Введите даты для статистики платежей и регистраций в формате:\n") +
        "`YYYY-MM-DD` (для одного дня)\nили\n`YYYY-MM-DD YYYY-MM-DD` (для диапазона)\.\n\n" +
        escape_md("Пример:\n`2025-05-26` или `2025-05-01 2025-05-26`\n\n"
                     "Для отмены нажмите кнопку ниже\.")
    )

    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_payments")]])

    await send_message_with_fallback(
        query.bot, user_id, text,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_payments_date_input(message: Message, state: FSMContext) -> None:
    """Обрабатывает ввод дат для статистики платежей и регистраций."""
    user_id = message.from_user.id
    logger.debug(f"handle_payments_date_input: user_id={user_id}")
    user_data = await state.get_data()
    if not user_data.get('awaiting_payments_date'):
        logger.warning(f"handle_payments_date_input вызвана без awaiting_payments_date для user_id={user_id}")
        await state.clear()
        return

    await state.update_data(awaiting_payments_date=None)
    text = message.text.strip()

    try:
        dates = text.split()
        if len(dates) == 1:
            start_date = end_date = dates[0]
        elif len(dates) == 2:
            start_date, end_date = dates
        else:
            raise ValueError("Неверное количество дат.")

        await handle_payments_date(
            CallbackQuery(query_id=None, from_user=message.from_user, message=message, data=None, bot=message.bot),
            state, start_date, end_date
        )

    except ValueError as e:
        logger.warning(f"Неверный формат дат от user_id={user_id}: {text}, error: {e}")
        # Изменение: Формат дат не экранируем, так как он внутри ` ` и безопасен
        text = (
            escape_md_v2("⚠️ Неверный формат дат\. Используйте ") +
            "`YYYY-MM-DD` или `YYYY-MM-DD YYYY-MM-DD`\. " +
            escape_md_v2("Пример: `2025-05-26` или `2025-05-01 2025-05-26`\.")
        )
        await message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 К выбору периода", callback_data="admin_payments")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    await state.clear()

async def show_replicate_costs(query: CallbackQuery, state: FSMContext) -> None:
    """Показывает расходы на Replicate."""
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.message.answer(
            escape_md_v2("❌ У вас нет прав\."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        log_entries_all_time = await get_generation_cost_log()
        msk_tz = pytz.timezone('Europe/Moscow')
        thirty_days_ago = (datetime.now(msk_tz) - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        log_entries_30_days = await get_generation_cost_log(start_date_str=thirty_days_ago)

        total_cost_all_time = Decimal(0)
        costs_by_model_all_time = {}
        for entry in log_entries_all_time:
            model_id, _, cost, _ = entry
            cost_decimal = Decimal(str(cost)) if cost is not None else Decimal(0)
            total_cost_all_time += cost_decimal
            key = model_id if model_id else "unknown_model_id"
            costs_by_model_all_time[key] = costs_by_model_all_time.get(key, Decimal(0)) + cost_decimal

        total_cost_30_days = Decimal(0)
        costs_by_model_30_days = {}
        for entry in log_entries_30_days:
            model_id, _, cost, _ = entry
            cost_decimal = Decimal(str(cost)) if cost is not None else Decimal(0)
            total_cost_30_days += cost_decimal
            key = model_id if model_id else "unknown_model_id"
            costs_by_model_30_days[key] = costs_by_model_30_days.get(key, Decimal(0)) + cost_decimal

        text = escape_md_v2("💰 Расходы на Replicate (USD):\n\n")
        text += f"За все время:\n  Общая сумма: ${total_cost_all_time:.4f}\n"
        if costs_by_model_all_time:
            text += escape_md_v2("  По моделям:\n")
            for model_id, cost in costs_by_model_all_time.items():
                model_name = "Неизвестная модель (ID отсутствует)"
                if model_id and model_id != "unknown_model_id":
                    model_name = next(
                        (m_data.get('name', model_id) for _, m_data in IMAGE_GENERATION_MODELS.items() if m_data.get('id') == model_id),
                        model_id
                    )
                elif model_id == "unknown_model_id":
                    model_name = "Неизвестная модель (ID не записан)"
                text += f"    • {model_name}: ${cost:.4f}\n"

        text += f"\nЗа последние 30 дней:\n  Общая сумма: ${total_cost_30_days:.4f}\n"
        if costs_by_model_30_days:
            text += escape_md_v2("  По моделям:\n")
            for model_id, cost in costs_by_model_30_days.items():
                model_name = "Неизвестная модель (ID отсутствует)"
                if model_id and model_id != "unknown_model_id":
                    model_name = next(
                        (m_data.get('name', model_id) for _, m_data in IMAGE_GENERATION_MODELS.items() if m_data.get('id') == model_id),
                        model_id
                    )
                elif model_id == "unknown_model_id":
                    model_name = "Неизвестная модель (ID не записан)"
                text += f"    • {model_name}: ${cost:.4f}\n"

        text += escape_md_v2("\n_Примечание: Расчеты основаны на данных из лога генераций и могут быть приблизительными\._")

        reply_markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в админ-панель", callback_data="admin_panel")]])

        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при расчете расходов Replicate: {e}", exc_info=True)
        text = escape_md_v2("❌ Не удалось рассчитать расходы\. Проверьте логи\.")
        await send_message_with_fallback(
            query.bot, user_id, text,
            reply_markup=await create_admin_keyboard(user_id), parse_mode=ParseMode.MARKDOWN_V2
        )

async def cancel_payments(message: Message, state: FSMContext) -> None:
    """Обрабатывает отмену ввода дат для платежей."""
    user_id = message.from_user.id
    await state.update_data(awaiting_payments_date=None)
    await message.answer(
        escape_md_v2("✅ Ввод дат отменён\."),
        reply_markup=await create_admin_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Ввод дат отменён для user_id={user_id}")
    await state.clear()

# Регистрация обработчиков
@payments_router.callback_query(
    lambda c: c.data and c.data.startswith("payments_date_") or c.data in ["payments_manual_date", "admin_payments"]
)
async def payments_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
    callback_data = query.data
    logger.debug(f"payments_callback_handler: user_id={query.from_user.id}, callback_data={callback_data}")
    try:
        if callback_data.startswith("payments_date_"):
            parts = callback_data.split("_")
            start_date, end_date = parts[2], parts[3]
            await handle_payments_date(query, state, start_date, end_date)
        elif callback_data == "payments_manual_date":
            await handle_manual_date_input(query, state)
        elif callback_data == "admin_payments":
            await show_payments_menu(query, state)
    except Exception as e:
        logger.error(f"Ошибка в payments_callback_handler: {e}", exc_info=True)
        await query.message.answer(
            escape_md_v2("❌ Произошла ошибка\. Попробуйте снова или обратитесь в поддержку\."),
            reply_markup=await create_admin_keyboard(query.from_user.id),
            parse_mode=ParseMode.MARKDOWN_V2
        )