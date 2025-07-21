import asyncio
import logging
import uuid
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Optional
from aiogram import Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from database import (
    get_all_users_stats, get_total_remaining_photos, get_payments_by_date,
    get_user_trainedmodels, get_registrations_by_date
)
from config import ADMIN_IDS, DATABASE_PATH
from keyboards import create_admin_keyboard, create_main_menu_keyboard
from handlers.utils import (
    safe_escape_markdown as escape_md, truncate_text, safe_edit_message, debug_markdown_text
)
import aiosqlite
from excel_utils import create_payments_excel, create_registrations_excel
import os

logger = logging.getLogger(__name__)

async def admin_panel(message: Message, state: FSMContext, user_id: Optional[int] = None) -> None:
    """Показывает главное меню админ-панели."""
    user_id = user_id or message.from_user.id
    logger.info(f"Попытка входа в админ-панель: user_id={user_id}, ADMIN_IDS={ADMIN_IDS}")
    if user_id not in ADMIN_IDS:
        await message.answer(
            escape_md("❌ У вас нет прав.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    await state.clear()
    text = (
        "🛠 Админ\-панель:\n\n"
        "Выберите действие:"
    )
    reply_markup = await create_admin_keyboard()
    await message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Админ-панель открыта для user_id={user_id}")

async def show_admin_stats(callback_query: CallbackQuery, state: FSMContext, page: int = 1) -> None:
    """Показывает общую статистику бота с пагинацией."""
    user_id = callback_query.from_user.id
    bot = callback_query.bot
    if user_id not in ADMIN_IDS:
        await callback_query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    page_size = 5
    try:
        users_data, total_users = await get_all_users_stats(page=page, page_size=page_size)
        all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)
        total_photos_left = await get_total_remaining_photos()

        # Логируем данные из базы для отладки
        logger.debug(f"Данные пользователей из get_all_users_stats: {[dict(zip(['user_id', 'username', 'first_name', 'generations_left', 'avatar_left', 'first_purchase', 'active_avatar_id', 'email', 'referrer_id', 'referrals_made_count', 'payments_count', 'total_spent'], user)) for user in users_data]}")

        paying_users = sum(1 for user in all_users_data if len(user) >= 11 and user[10] > 0)
        non_paying_users = total_users - paying_users
        paying_percent = (paying_users / total_users * 100) if total_users > 0 else 0
        non_paying_percent = (non_paying_users / total_users * 100) if total_users > 0 else 0

        # Форматируем числовые значения заранее как строки
        paying_percent_str = f"{paying_percent:.2f}"
        non_paying_percent_str = f"{non_paying_percent:.2f}"

        # Формируем текст статистики
        stats_text = (
            f"📊 Общая статистика бота:\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"💳 Платящих пользователей: {paying_users} ({paying_percent_str}%)\n"
            f"🆓 Неплатящих пользователей: {non_paying_users} ({non_paying_percent_str}%)\n"
            f"📸 Суммарный остаток печенек у всех: {total_photos_left}\n\n"
        )

        # Вычисляем max_pages
        max_pages = (total_users + page_size - 1) // page_size or 1
        stats_text += f"📄 Пользователи (Страница {page} из {max_pages}):\n"

        keyboard_buttons = []

        if not users_data:
            stats_text += "_Нет данных о пользователях._\n"
        else:
            for user in users_data:
                if len(user) < 12:
                    logger.warning(f"Неполные данные пользователя: {user}")
                    continue

                # Распаковка с правильным порядком полей
                u_id, u_name, f_name, g_left, a_left, f_purchase, act_avatar, email, ref_id, refs_made, pays_count, spent_total = user

                # Экранируем только необходимые символы для Markdown V2
                def clean_text(text: Optional[str]) -> str:
                    if not text:
                        return ""
                    # Экранируем символы, которые могут нарушить Markdown V2 синтаксис, исключая '_' и '.'
                    for char in ['*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '=', '|', '{', '}', '!']:
                        text = text.replace(char, f'\\{char}')
                    return text

                name_display = clean_text(f_name or u_name or f"ID {u_id}")
                username_display = f"@{clean_text(u_name)}" if u_name and u_name != "Без имени" else ""
                email_display = clean_text(email or "Не указан")
                # Форматируем spent_total как строку заранее
                spent_total_str = f"{spent_total:.2f}" if pays_count > 0 and spent_total is not None else "0.00"

                # Формируем текст с обычным разделителем
                separator = '-' * 30
                stats_text += f"\n{separator}\n"
                stats_text += f"👤 {name_display} {username_display}\n"
                stats_text += f"🆔 ID: {u_id}\n"
                stats_text += f"💰 Баланс: {g_left} печенек, {a_left} аватаров\n"
                stats_text += (
                    f"💳 Покупок: {pays_count}, потрачено: {spent_total_str} RUB\n"
                    if pays_count > 0
                    else "💳 Покупок: нет\n"
                )
                if ref_id:
                    stats_text += f"👥 Приглашен: ID {ref_id}\n"
                if refs_made > 0:
                    stats_text += f"🎯 Привел рефералов: {refs_made}\n"
                stats_text += f"📧 Email: {email_display}\n"
                if act_avatar:
                    stats_text += f"🌟 Активный аватар ID: {act_avatar}\n"

                # Для кнопок используем неэкранированное имя
                button_name = truncate_text(f_name or u_name or f"ID {u_id}", 20)
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"👤 {button_name} (ID: {u_id})",
                        callback_data=f"user_actions_{u_id}"
                    )
                ])

        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(
                text="⬅️ Пред.",
                callback_data=f"admin_stats_page_{page-1}"
            ))
        if page * page_size < total_users:
            nav_buttons.append(InlineKeyboardButton(
                text="След. ➡️",
                callback_data=f"admin_stats_page_{page+1}"
            ))
        if nav_buttons:
            keyboard_buttons.append(nav_buttons)

        keyboard_buttons.append([
            InlineKeyboardButton(
                text="🔙 В админ-панель",
                callback_data="admin_panel"
            )
        ])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        # Отладка текста перед отправкой
        logger.debug(f"Итоговый текст stats_text перед отправкой: {stats_text[:200]}...")
        debug_markdown_text(stats_text)
        # Используем safe_edit_message для отправки
        await safe_edit_message(
            message=callback_query.message,
            text=stats_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Статистика отправлена для user_id={user_id}, page={page}")

    except Exception as e:
        logger.error(f"Ошибка при получении статистики для user_id={user_id}: {e}", exc_info=True)
        await state.clear()
        error_text = escape_md("❌ Ошибка получения статистики. Попробуйте позже.", version=2)
        reply_markup = await create_admin_keyboard()
        await safe_edit_message(
            message=callback_query.message,
            text=error_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await callback_query.answer("❌ Ошибка получения данных", show_alert=True)

async def get_all_failed_avatars() -> List[Dict]:
    """Получает список всех аватаров с ошибками из базы данных."""
    failed_avatars = []
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT 
                    tm.avatar_id, tm.user_id, tm.model_id, tm.model_version, tm.status,
                    tm.prediction_id, tm.avatar_name, tm.created_at, u.username, u.first_name
                FROM user_trainedmodels tm
                LEFT JOIN users u ON tm.user_id = u.user_id
                WHERE tm.status IN ('failed', 'error') OR tm.status IS NULL OR tm.status = ''
                ORDER BY tm.created_at DESC
            """)
            rows = await cursor.fetchall()
            for row in rows:
                failed_avatars.append({
                    'avatar_id': row['avatar_id'],
                    'user_id': row['user_id'],
                    'model_id': row['model_id'],
                    'model_version': row['model_version'],
                    'status': row['status'] or 'unknown',
                    'prediction_id': row['prediction_id'],
                    'avatar_name': row['avatar_name'] or 'Без имени',
                    'created_at': row['created_at'],
                    'username': row['username'],
                    'full_name': row['first_name'] or 'Без имени'
                })
        logger.info(f"Получено {len(failed_avatars)} проблемных аватаров")
        return failed_avatars
    except Exception as e:
        logger.error(f"Ошибка при получении проблемных аватаров: {e}", exc_info=True)
        return []

async def delete_all_failed_avatars() -> int:
    """Удаляет все аватары с ошибками из базы данных."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute("""
                DELETE FROM user_trainedmodels 
                WHERE status IN ('failed', 'error') OR status IS NULL OR status = ''
            """)
            deleted_count = cursor.rowcount
            await db.commit()
            logger.info(f"Удалено {deleted_count} проблемных аватаров")
            return deleted_count
    except Exception as e:
        logger.error(f"Ошибка при удалении проблемных аватаров: {e}", exc_info=True)
        return 0

async def admin_show_failed_avatars(callback_query: CallbackQuery, state: FSMContext) -> None:
    """Показывает админу все проблемные аватары."""
    user_id = callback_query.from_user.id
    if user_id not in ADMIN_IDS:
        await callback_query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    failed_avatars = await get_all_failed_avatars()
    if not failed_avatars:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
        ])
        text = "✅ Нет аватаров с ошибками!"
        debug_markdown_text(text)
        await safe_edit_message(
            message=callback_query.message,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.debug(f"Сообщение об отсутствии проблемных аватаров отправлено для user_id={user_id}")
        return

    users_with_errors = {}
    for avatar in failed_avatars:
        user_id_key = avatar['user_id']
        if user_id_key not in users_with_errors:
            users_with_errors[user_id_key] = {
                'username': avatar['username'],
                'full_name': avatar['full_name'],
                'avatars': []
            }
        users_with_errors[user_id_key]['avatars'].append(avatar)

    # Экранируем пользовательские данные
    def clean_text(text: Optional[str]) -> str:
        if not text:
            return ""
        # Экранируем символы, которые могут нарушить Markdown V2 синтаксис, исключая '_' и '.'
        for char in ['*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '=', '|', '{', '}', '!']:
            text = text.replace(char, f'\\{char}')
        return text

    text = (
        f"❌ Всего аватаров с ошибками: {len(failed_avatars)}\n"
        f"👥 Затронуто пользователей: {len(users_with_errors)}\n\n"
    )

    for i, (user_id_key, user_data) in enumerate(list(users_with_errors.items())[:10], 1):
        user_info = clean_text(user_data['full_name'])
        if user_data['username']:
            user_info += f" (@{clean_text(user_data['username'])})"
        user_info += f" \[ID: {user_id_key}\]"
        text += f"*{i}. {user_info}*\n"
        text += f"   Ошибок: {len(user_data['avatars'])}\n"
        for j, avatar in enumerate(user_data['avatars'][:3], 1):
            text += f"   • {clean_text(avatar['avatar_name'])} ({clean_text(avatar['status'])})\n"
        if len(user_data['avatars']) > 3:
            text += f"   • ... и еще {len(user_data['avatars']) - 3}\n"
        text += "\n"

    if len(users_with_errors) > 10:
        text += f"\n_...и еще {len(users_with_errors) - 10} пользователей_\n"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить ВСЕ проблемные аватары", callback_data="admin_delete_all_failed")],
        [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
    ])

    debug_markdown_text(text)
    await safe_edit_message(
        message=callback_query.message,
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Список проблемных аватаров отправлен для user_id={user_id}")

async def admin_confirm_delete_all_failed(callback_query: CallbackQuery, state: FSMContext) -> None:
    """Запрос подтверждения удаления всех проблемных аватаров."""
    user_id = callback_query.from_user.id
    if user_id not in ADMIN_IDS:
        await callback_query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    failed_avatars = await get_all_failed_avatars()
    total_count = len(failed_avatars)
    text = (
        f"⚠️ *ВНИМАНИЕ!*:\n\n"
        f"Вы собираетесь удалить *{total_count}* аватаров с ошибками.\n\n"
        f"Это действие *НЕЛЬЗЯ ОТМЕНИТЬ*!:\n\n"
        f"Вы уверены?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ ДА, УДАЛИТЬ ВСЕ", callback_data="admin_confirm_delete_all"),
            InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="admin_failed_avatars")
        ]
    ])

    debug_markdown_text(text)
    await safe_edit_message(
        message=callback_query.message,
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Запрос подтверждения удаления аватаров отправлен для user_id={user_id}")

async def admin_execute_delete_all_failed(callback_query: CallbackQuery, state: FSMContext) -> None:
    """Выполняет удаление всех проблемных аватаров."""
    user_id = callback_query.from_user.id
    if user_id not in ADMIN_IDS:
        await callback_query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    await callback_query.answer("🔄 Удаляю аватары...")
    deleted_count = await delete_all_failed_avatars()

    if deleted_count > 0:
        text = (
            f"✅ *Успешно!*:\n\n"
            f"Удалено аватаров с ошибками: *{deleted_count}*\n\n"
            f"База данных очищена от проблемных записей."
        )
    else:
        text = f"❌ *Ошибка при удалении*:\n\nПопробуйте еще раз."

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")]
    ])

    debug_markdown_text(text)
    await safe_edit_message(
        message=callback_query.message,
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Удаление аватаров завершено для user_id={user_id}, удалено={deleted_count}")

async def send_daily_payments_report(bot: Bot) -> None:
    """Генерирует и отправляет ежедневный отчет о платежах и новых регистрациях за предыдущий день."""
    msk_tz = pytz.timezone('Europe/Moscow')
    yesterday = (datetime.now(msk_tz) - timedelta(days=1)).strftime('%Y-%m-%d')

    try:
        # Получаем данные о платежах и регистрациях за вчера
        payments = await get_payments_by_date(yesterday, yesterday)
        registrations = await get_registrations_by_date(yesterday)

        payments_file_path = None
        registrations_file_path = None

        # Генерируем Excel-файлы, если есть данные
        if payments:
            payments_filename = f"payments_{yesterday}_{uuid.uuid4().hex[:8]}.xlsx"
            payments_file_path = create_payments_excel(payments, payments_filename, yesterday)
        else:
            logger.info(f"Платежи за {yesterday} не найдены.")

        if registrations:
            registrations_filename = f"registrations_{yesterday}_{uuid.uuid4().hex[:8]}.xlsx"
            registrations_file_path = create_registrations_excel(registrations, registrations_filename, yesterday)
        else:
            logger.info(f"Регистрации за {yesterday} не найдены.")

        # Подсчитываем статистику
        total_payments = len(payments)
        total_amount = sum(p[2] for p in payments if p[2]) if payments else 0
        total_registrations = len(registrations)

        # Форматируем числовые значения как строки для корректного экранирования
        total_amount_str = f"{total_amount:.2f}"

        # Формируем текст отчета с использованием safe_escape_markdown
        text = (
            escape_md(f"📈 Ежедневная статистика за {yesterday} (MSK):", version=2) + "\n\n" +
            escape_md("💰 Платежи:", version=2) + "\n" +
            escape_md(f"🔢 Всего платежей: {total_payments}", version=2) + "\n" +
            escape_md(f"💵 Общая сумма: {total_amount_str} RUB", version=2) + "\n\n" +
            escape_md("👥 Новые регистрации:", version=2) + "\n" +
            escape_md(f"🔢 Всего новых пользователей: {total_registrations}", version=2) + "\n\n" +
            escape_md("📊 Детали в прикрепленных файлах.", version=2)
        )

        # Если нет данных, отправляем сообщение об этом
        if not payments and not registrations:
            text = escape_md(f"🚫 За {yesterday} (MSK) нет ни платежей, ни новых регистраций.", version=2)
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=text,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")
            return

        # Отправляем отчет и файлы каждому админу
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN_V2
                )

                if payments_file_path and os.path.exists(payments_file_path):
                    await bot.send_document(
                        chat_id=admin_id,
                        document=FSInputFile(payments_file_path, filename=payments_filename),
                        caption=escape_md(f"Отчет по платежам за {yesterday} (MSK)", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )

                if registrations_file_path and os.path.exists(registrations_file_path):
                    await bot.send_document(
                        chat_id=admin_id,
                        document=FSInputFile(registrations_file_path, filename=registrations_filename),
                        caption=escape_md(f"Отчет по новым регистрациям за {yesterday} (MSK)", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )

            except Exception as e:
                logger.error(f"Ошибка отправки отчета админу {admin_id}: {e}")

        # Удаляем временные файлы
        if payments_file_path and os.path.exists(payments_file_path):
            os.remove(payments_file_path)
            logger.info(f"Временный файл платежей {payments_file_path} удален.")
        if registrations_file_path and os.path.exists(registrations_file_path):
            os.remove(registrations_file_path)
            logger.info(f"Временный файл регистраций {registrations_file_path} удален.")

    except Exception as e:
        logger.error(f"Ошибка генерации отчета за {yesterday}: {e}", exc_info=True)
        error_text = escape_md("❌ Ошибка генерации отчета. Проверьте логи.", version=2)
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=error_text,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение об ошибке админу {admin_id}: {e}")

async def cancel(message: Message, state: FSMContext) -> None:
    """Отменяет все активные действия и сбрасывает контекст."""
    user_id = message.from_user.id
    await state.clear()
    text = escape_md("✅ Все действия отменены.", version=2)
    reply_markup = await create_admin_keyboard() if user_id in ADMIN_IDS else await create_main_menu_keyboard(user_id)
    await message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.debug(f"Действия отменены для user_id={user_id}")