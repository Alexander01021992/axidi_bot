import asyncio
import os
import random
import aiosqlite
import logging
import uuid
import json
import pytz
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CommandHandler, CallbackQueryHandler
from telegram.constants import ParseMode
from excel_utils import create_payments_excel, create_registrations_excel
from database import (
    get_payments_by_date, check_subscription, get_all_users_stats, get_user_trainedmodels,
    get_user_payments, get_generation_log_for_cost, get_total_remaining_photos,
    get_paid_users, get_non_paid_users, get_user_generation_stats,
    get_user_rating_and_registration, delete_user, block_user, is_user_blocked, log_user_action,
    get_active_trainedmodel, update_user_balance, get_user_activity_stats,
    get_referral_stats, search_users, get_user_logs, schedule_broadcast,
    get_scheduled_broadcasts, get_registrations_by_date
)
from config import IMAGE_GENERATION_MODELS, ADMIN_IDS, DATABASE_PATH
from keyboards import create_admin_keyboard, create_admin_user_actions_keyboard, create_avatar_style_choice_keyboard
from handlers.utils import (
    safe_escape_markdown as escape_md, send_message_with_fallback, check_resources,
    truncate_text, safe_escape_markdown, create_isolated_context, clean_admin_context
)
from generation import generate_image, handle_generate_video

logger = logging.getLogger(__name__)

# Состояния для FSM
(
    AWAITING_BROADCAST_MESSAGE, AWAITING_BROADCAST_MEDIA_CONFIRM,
    AWAITING_PAYMENT_DATES, AWAITING_USER_SEARCH, AWAITING_BALANCE_CHANGE,
    AWAITING_BROADCAST_FILTERS, AWAITING_BROADCAST_SCHEDULE,
    AWAITING_BLOCK_REASON
) = range(8)
# === В handlers/admin.py ЗАМЕНИТЕ функции get_all_failed_avatars и delete_all_failed_avatars ===

# === В handlers/admin.py ЗАМЕНИТЕ функцию get_all_failed_avatars ===

async def get_all_failed_avatars() -> List[Dict]:
    """Получает список ВСЕХ аватаров с ошибками из базы данных."""
    failed_avatars = []
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT 
                tm.avatar_id,
                tm.user_id,
                tm.model_id,
                tm.model_version,
                tm.status,
                tm.prediction_id,
                tm.avatar_name,
                tm.created_at,
                u.username,
                u.first_name
            FROM user_trainedmodels tm
            LEFT JOIN users u ON tm.user_id = u.user_id
            WHERE tm.status = 'failed' 
               OR tm.status = 'error' 
               OR tm.status IS NULL
               OR tm.status = ''
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
    
    return failed_avatars

async def delete_all_failed_avatars() -> int:
    """Удаляет ВСЕ аватары с ошибками из базы данных."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute("""
                DELETE FROM user_trainedmodels 
                WHERE status = 'failed' 
                   OR status = 'error' 
                   OR status IS NULL
                   OR status = ''
            """)
            
            deleted_count = cursor.rowcount
            await db.commit()
            
            logger.info(f"Admin deleted {deleted_count} failed avatars")
            return deleted_count
            
    except Exception as e:
        logger.error(f"Error deleting all failed avatars: {e}")
        return 0

async def admin_show_failed_avatars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает админу все проблемные аватары"""
    query = update.callback_query if hasattr(update, 'callback_query') else None
    user_id = update.effective_user.id if update.effective_user else None
    
    # Проверка на админа
    if user_id not in ADMIN_IDS:
        return
    
    # Получаем все проблемные аватары
    failed_avatars = await get_all_failed_avatars()
    
    if not failed_avatars:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
        ])
        
        await send_message_with_fallback(
            context.bot,
            user_id,
            "✅ Нет аватаров с ошибками\\!",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2,
            update_or_query=update
        )
        return
    
    # Группируем по пользователям
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
    
    # Формируем сообщение
    text = f"❌ *Всего аватаров с ошибками: {len(failed_avatars)}*\n"
    text += f"👥 *Затронуто пользователей: {len(users_with_errors)}*\n\n"
    
    # Показываем первых 10 пользователей
    for i, (user_id_key, user_data) in enumerate(list(users_with_errors.items())[:10], 1):
        user_info = f"{user_data['full_name'] or 'Без имени'}"
        if user_data['username']:
            user_info += f" \\(@{user_data['username']}\\)"
        user_info += f" \\[ID: {user_id_key}\\]"
        
        text += f"*{i}\\. {user_info}*\n"
        text += f"   Ошибок: {len(user_data['avatars'])}\n"
        
        # Показываем первые 3 ошибки
        for j, avatar in enumerate(user_data['avatars'][:3], 1):
            text += f"   • {avatar['avatar_name']} \\({avatar['status']}\\)\n"
        
        if len(user_data['avatars']) > 3:
            text += f"   • \\.\\.\\. и еще {len(user_data['avatars']) - 3}\n"
        text += "\n"
    
    if len(users_with_errors) > 10:
        text += f"\n_\\.\\.\\.и еще {len(users_with_errors) - 10} пользователей_"
    
    # Создаем клавиатуру
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Удалить ВСЕ проблемные аватары", callback_data="admin_delete_all_failed")],
        [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
    ])
    
    await send_message_with_fallback(
        context.bot,
        user_id,
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2,
        update_or_query=update
    )

async def admin_confirm_delete_all_failed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запрос подтверждения удаления всех проблемных аватаров"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Проверка на админа
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    # Получаем статистику
    failed_avatars = await get_all_failed_avatars()
    total_count = len(failed_avatars)
    
    text = (
        "⚠️ *ВНИМАНИЕ\\!*\n\n"
        f"Вы собираетесь удалить *{total_count}* аватаров с ошибками\\.\n\n"
        "Это действие *НЕЛЬЗЯ ОТМЕНИТЬ*\\!\n\n"
        "Вы уверены?"
    )
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ДА, УДАЛИТЬ ВСЕ", callback_data="admin_confirm_delete_all"),
            InlineKeyboardButton("❌ ОТМЕНА", callback_data="admin_failed_avatars")
        ]
    ])
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def admin_execute_delete_all_failed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выполняет удаление всех проблемных аватаров"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Проверка на админа
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    # Показываем уведомление о процессе
    await query.answer("🔄 Удаляю аватары...", show_alert=False)
    
    # Удаляем
    deleted_count = await delete_all_failed_avatars()
    
    if deleted_count > 0:
        text = (
            f"✅ *Успешно\\!*\n\n"
            f"Удалено аватаров с ошибками: *{deleted_count}*\n\n"
            f"База данных очищена от проблемных записей\\."
        )
    else:
        text = "❌ *Ошибка при удалении*\n\nПопробуйте еще раз\\."
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Админ панель", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает главное меню админ-панели."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    text = escape_md(
        "🛠 Админ-панель\n\n"
        "Выберите действие:"
    )
    reply_markup = await create_admin_keyboard()
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )

async def send_daily_payments_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Генерирует и отправляет ежедневный отчет о платежах и регистрациях за предыдущий день всем админам."""
    bot = context.bot
    msk_tz = pytz.timezone('Europe/Moscow')
    today = datetime.now(msk_tz)
    yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')

    logger.info(f"Начало генерации ежедневного отчета за {yesterday}")

    try:
        # Получаем данные о платежах
        payments = await get_payments_by_date(yesterday, yesterday)
        registrations = await get_registrations_by_date(yesterday, yesterday)

        # Проверяем наличие данных
        if not payments and not registrations:
            text = escape_md(f"🚫 Платежи и регистрации за {yesterday} не найдены.")
            for admin_id in ADMIN_IDS:
                try:
                    await send_message_with_fallback(
                        bot, admin_id, text, parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")
            return

        # Создаем файлы Excel
        payments_filename = f"payments_{yesterday}_{uuid.uuid4().hex[:8]}.xlsx"
        payments_file_path = create_payments_excel(payments, payments_filename, yesterday, yesterday)

        registrations_filename = f"registrations_{yesterday}_{uuid.uuid4().hex[:8]}.xlsx"
        registrations_file_path = create_registrations_excel(registrations, registrations_filename, yesterday, yesterday)

        # Проверяем создание файлов
        files_created = []
        if payments_file_path and os.path.exists(payments_file_path):
            files_created.append(('payments', payments_file_path, payments_filename))
        else:
            logger.error(f"Файл платежей {payments_file_path} не создан или не существует.")

        if registrations_file_path and os.path.exists(registrations_file_path):
            files_created.append(('registrations', registrations_file_path, registrations_filename))
        else:
            logger.error(f"Файл регистраций {registrations_file_path} не создан или не существует.")

        if not files_created:
            error_text = escape_md("❌ Ошибка создания отчетов. Проверьте логи.")
            for admin_id in ADMIN_IDS:
                try:
                    await send_message_with_fallback(
                        bot, admin_id, error_text, parse_mode=ParseMode.MARKDOWN_V2
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")
            return

        # Формируем текстовое сообщение
        total_payments = len(payments)
        total_amount = sum(p[2] for p in payments if p[2]) if payments else 0.0
        total_registrations = len(registrations)
        text = (
            f"📈 Ежедневная статистика за {yesterday}\n\n"
            f"💰 Платежи:\n"
            f"🔢 Всего платежей: {total_payments}\n"
            f"💵 Общая сумма: {total_amount:.2f} RUB\n\n"
            f"👥 Регистрации:\n"
            f"🔢 Всего новых пользователей: {total_registrations}\n\n"
            f"📊 Excel-файлы с деталями отправлены ниже."
        )

        # Отправляем сообщение и файлы каждому админу
        for admin_id in ADMIN_IDS:
            try:
                await send_message_with_fallback(
                    bot, admin_id, escape_md(text), parse_mode=ParseMode.MARKDOWN_V2
                )

                for file_type, file_path, filename in files_created:
                    with open(file_path, 'rb') as f:
                        caption = f"{'Отчет по платежам' if file_type == 'payments' else 'Отчет по регистрациям'} за {yesterday}"
                        await bot.send_document(
                            chat_id=admin_id, document=f, filename=filename, caption=caption
                        )
            except Exception as e:
                logger.error(f"Ошибка отправки отчета админу {admin_id}: {e}")

        # Удаляем временные файлы
        for _, file_path, _ in files_created:
            try:
                os.remove(file_path)
                logger.info(f"Временный файл {file_path} удален.")
            except Exception as e_remove:
                logger.error(f"Ошибка удаления файла {file_path}: {e_remove}")

    except Exception as e:
        logger.error(f"Ошибка генерации ежедневного отчета за {yesterday}: {e}", exc_info=True)
        error_text = escape_md("❌ Ошибка генерации ежедневного отчета. Проверьте логи.")
        for admin_id in ADMIN_IDS:
            try:
                await send_message_with_fallback(
                    bot, admin_id, error_text, parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e_admin:
                logger.error(f"Не удалось уведомить админа {admin_id}: {e_admin}")

async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1) -> None:
    user_id = update.effective_user.id
    page_size = 5

    try:
        # Получаем данные для текущей страницы
        users_data_tuples, total_users = await get_all_users_stats(page=page, page_size=page_size)
        # Получаем все данные пользователей для подсчета платящих/неплатящих
        all_users_data, _ = await get_all_users_stats(page=1, page_size=1000000)  # Полный список
        total_photos_left = await get_total_remaining_photos()

        # Подсчет платящих и неплатящих пользователей
        paying_users = sum(1 for user_data in all_users_data if len(user_data) >= 11 and user_data[10] > 0)
        non_paying_users = total_users - paying_users
        paying_percent = (paying_users / total_users * 100) if total_users > 0 else 0
        non_paying_percent = (non_paying_users / total_users * 100) if total_users > 0 else 0

        stats_text = (
            f"📊 Общая статистика бота\n\n"
            f"👥 Всего пользователей: `{total_users}`\n"
            f"💳 Платящих пользователей: `{paying_users}` ({paying_percent:.2f}%)\n"
            f"🆓 Неплатящих пользователей: `{non_paying_users}` ({non_paying_percent:.2f}%)\n"
            f"📸 Суммарный остаток фото у всех: `{total_photos_left}`\n\n"
        )

        max_pages = (total_users + page_size - 1) // page_size or 1

        stats_text += f"📄 Пользователи (Страница {page} из {max_pages}):\n"
        keyboard_buttons = []

        if not users_data_tuples:
            stats_text += "_Нет данных о пользователях на этой странице._\n"
        else:
            for u_data_tuple in users_data_tuples:
                if len(u_data_tuple) < 12:
                    logger.warning(f"Неполные данные пользователя: {u_data_tuple}")
                    continue

                u_id, u_name, f_name, g_left, a_left, f_purchase_val, act_avatar, email_val, ref_id_val, refs_made, pays_count, spent_total = u_data_tuple

                name_display = f_name or u_name or f"ID {u_id}"
                username_display = f"@{u_name}" if u_name and u_name != "Без имени" else ""

                stats_text += f"\n{'─' * 30}\n"
                stats_text += f"👤 {name_display}"
                if username_display:
                    stats_text += f" {username_display}"
                stats_text += f"\n🆔 ID: `{u_id}`\n"
                stats_text += f"💰 Баланс: {g_left} фото, {a_left} аватар{'ов' if a_left != 1 else ''}\n"

                if pays_count and pays_count > 0:
                    spent_display = f"{spent_total:.2f}" if spent_total is not None else "0.00"
                    stats_text += f"💳 Покупок: {pays_count}, потрачено: {spent_display} RUB\n"
                else:
                    stats_text += f"💳 Покупок: нет\n"

                if ref_id_val:
                    stats_text += f"👥 Приглашен: ID {ref_id_val}\n"
                if refs_made and refs_made > 0:
                    stats_text += f"🎯 Привел рефералов: {refs_made}\n"

                stats_text += f"📧 Email: {email_val or 'Не указан'}\n"

                if act_avatar:
                    stats_text += f"🌟 Активный аватар ID: {act_avatar}\n"

                button_name = truncate_text(name_display, 20)
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        f"👤 {button_name} (ID: {u_id})",
                        callback_data=f"user_actions_{u_id}"
                    )
                ])

        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Пред.", callback_data=f"admin_stats_page_{page-1}"))
        if page * page_size < total_users:
            nav_buttons.append(InlineKeyboardButton("След. ➡️", callback_data=f"admin_stats_page_{page+1}"))

        if nav_buttons:
            keyboard_buttons.append(nav_buttons)

        keyboard_buttons.extend([
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ])

        reply_markup_stats = InlineKeyboardMarkup(keyboard_buttons)

        await send_message_with_fallback(
            context.bot, user_id, stats_text, update_or_query=update,
            reply_markup=reply_markup_stats, parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при получении общей статистики: {e}", exc_info=True)
        text = escape_md("❌ Ошибка получения статистики. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

async def show_user_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    user_id = update.effective_user.id

    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, user_id, f"❌ Пользователь ID `{target_user_id}` не найден.",
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    gen_stats = await get_user_generation_stats(target_user_id)
    payments = await get_user_payments(target_user_id)
    avatars = await get_user_trainedmodels(target_user_id)

    g_left, a_left, _, u_name, _, f_purchase_val, email_val, act_avatar_id, f_name, _ = target_user_info
    display_name_raw = f_name or u_name or f"ID {target_user_id}"
    username_display_raw = u_name if u_name and u_name != "Без имени" else ""
    username_display = f" (@{escape_md(username_display_raw)})" if username_display_raw else ""
    email_display_raw = email_val if email_val else "Не указан"

    text = f"👤 Детальная информация о пользователе\n\n"
    text += f"Имя: {display_name_raw}{username_display}\n"
    text += f"ID: `{target_user_id}`\n"
    text += f"Email: {email_display_raw}\n"
    text += f"\n💰 Баланс:\n"
    text += f"  • Фото: `{g_left}`\n"
    text += f"  • Аватары: `{a_left}`\n"

    if gen_stats:
        text += f"\n📊 Статистика генераций:\n"
        for gen_type, count in gen_stats.items():
            type_name = {
                'with_avatar': 'Фото с аватаром',
                'photo_to_photo': 'Фото по референсу',
                'ai_video': 'AI-видео (1.6)',
                'ai_video_v2': 'AI-видео (2.0)',
                'train_flux': 'Обучение аватаров',
                'prompt_assist': 'Помощь с промптами'
            }.get(gen_type, gen_type)
            text += f"  • {(type_name)}: `{count}`\n"

    if avatars:
        text += f"\n🎭 Аватары ({len(avatars)}):\n"
        for avatar_tuple in avatars[:3]:
            if len(avatar_tuple) >= 9:
                avatar_id, _, _, status, _, _, _, _, avatar_name = avatar_tuple[:9]
                name_raw = avatar_name or f"Аватар {avatar_id}"
                status_icon = "✅" if status == "success" else "⏳" if status in ["pending", "starting", "processing"] else "❌"
                text += f"  • {escape_md(name_raw)}: {status_icon} {escape_md(status)}\n"
        if len(avatars) > 3:
            text += f"  _...и еще {len(avatars) - 3}_\n"

    if payments:
        total_spent = sum(p[2] for p in payments if p[2] is not None)
        text += f"\n💳 История платежей ({len(payments)}):\n"
        text += f"  • Всего потрачено: `{total_spent:.2f}` RUB\n"
        for _, plan, amount, p_date in payments[:3]:
            date_str = datetime.strptime(str(p_date).split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y') if p_date else "N/A"
            plan_raw = plan.capitalize() if plan else "Неизвестный план"
            amount_display = f"{amount:.2f}" if amount is not None else "0.00"
            text += f"  • `{date_str}`: {escape_md(plan_raw)} - `{amount_display}` RUB\n"

    text += f"\nВыберите действие:"

    is_blocked = await is_user_blocked(target_user_id)
    keyboard_buttons = await create_admin_user_actions_keyboard(target_user_id, is_blocked)

    admin_view_source = context.user_data.get('admin_view_source', 'admin_stats')
    back_button_text = "🔙 В админ-панель"
    back_button_callback = "admin_panel"

    if admin_view_source == 'admin_stats':
        back_button_text = "🔙 К статистике"
        back_button_callback = "admin_stats"
    elif admin_view_source == 'admin_search_user':
        back_button_text = "🔙 К поиску"
        back_button_callback = "admin_search_user"

    keyboard_buttons.append([InlineKeyboardButton(back_button_text, callback_data=back_button_callback)])

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )

async def show_user_profile_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Показывает профиль пользователя для админа."""
    user_id = update.effective_user.id

    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, user_id, f"❌ Пользователь ID `{target_user_id}` не найден.",
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    g_left, a_left, _, u_name, _, f_purchase_val, email_val, act_avatar_id, f_name, _ = target_user_info

    name_display_raw = f_name or u_name or f"ID {target_user_id}"
    username_display_raw = u_name if u_name and u_name != "Без имени" else ""
    username_display = f" (@{escape_md(username_display_raw)})" if username_display_raw else ""
    email_display_raw = email_val if email_val else "Не указан"

    active_avatar_name_raw = "Не выбран"
    if act_avatar_id:
        active_model_data = await get_active_trainedmodel(target_user_id)
        if active_model_data and active_model_data[3] == 'success':
            avatar_name_db = active_model_data[8]
            active_avatar_name_raw = avatar_name_db if avatar_name_db else f"Аватар {act_avatar_id}"

    avg_rating, rating_count, registration_date = await get_user_rating_and_registration(target_user_id)
    rating_text = f"⭐ Средний рейтинг: {avg_rating:.2f} ({rating_count} оценок)" if avg_rating is not None and rating_count > 0 else "⭐ Средний рейтинг: Нет оценок"
    registration_text = f"📅 Дата регистрации: {registration_date}" if registration_date else "📅 Дата регистрации: Не указана"

    payments = await get_user_payments(target_user_id)
    payments_history = "\n_Нет истории покупок._"

    if payments:
        payments_history = "\nПоследние покупки:\n"
        for _, plan, amount, p_date in payments[:3]:
            p_date_formatted = datetime.strptime(str(p_date).split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M') if p_date else "N/A"
            p_amount_formatted = f"{amount:.2f} RUB" if amount is not None else "N/A"
            plan_raw = plan.capitalize() if plan else "Неизвестный план"
            payments_history += f"  • {escape_md(plan_raw)} ({p_amount_formatted}) - {p_date_formatted}\n"

    profile_text = (
        f"👤 Профиль пользователя: {name_display_raw}{username_display} (ID: `{target_user_id}`)\n\n"
        f"💰 Баланс:\n  📸 Фото: `{g_left}`\n  👤 Аватары: `{a_left}`\n\n"
        f"🌟 Активный аватар: {escape_md(active_avatar_name_raw)}\n"
        f"📧 Email: {(email_display_raw)}\n"
        f"{rating_text}\n"
        f"{registration_text}\n"
        f"🛒 Первая покупка: {'Нет' if f_purchase_val == 0 else 'Да'}\n"
        f"{payments_history}"
    )

    await send_message_with_fallback(
        context.bot, user_id, profile_text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям с пользователем", callback_data=f"user_actions_{target_user_id}")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def show_user_avatars_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Показывает все аватары пользователя для админа."""
    user_id = update.effective_user.id
    
    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, user_id, f"❌ Пользователь ID `{target_user_id}` не найден.",
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    
    name_display_raw = target_user_info[8] or target_user_info[3] or f"ID {target_user_id}"
    
    avatars_text = f"🖼️ Аватары пользователя {(name_display_raw)} (ID: `{target_user_id}`)\n\n"
    
    user_avatars_full = await get_user_trainedmodels(target_user_id)
    if not user_avatars_full:
        avatars_text += "_У пользователя нет аватаров._"
    else:
        for avatar_tuple in user_avatars_full:
            if len(avatar_tuple) < 9:
                continue
                
            avatar_id, model_id, model_version, status, prediction_id, trigger_word, _, _, avatar_name = avatar_tuple[:9]
            
            avatar_name_raw = avatar_name if avatar_name else f"Аватар {avatar_id}"
            status_raw = status if status else "N/A"
            icon = "✅" if status == "success" else "⏳" if status in ["pending", "starting", "processing"] else "❌"
            
            avatars_text += f"{escape_md(avatar_name_raw)} (ID: {avatar_id})\n"
            avatars_text += f"  • Статус: {icon} {escape_md(status_raw)}\n"
            avatars_text += f"  • Триггер: `{escape_md(trigger_word)}`\n"
            if model_id:
                avatars_text += f"  • Модель: `{(model_id)}`\n"
            if model_version:
                avatars_text += f"  • Версия: `{escape_md(model_version)}`\n"
            if prediction_id:
                avatars_text += f"  • Training ID: `{escape_md(prediction_id)}`\n"
            avatars_text += "\n"
    
    await send_message_with_fallback(
        context.bot, user_id, avatars_text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям с пользователем", callback_data=f"user_actions_{target_user_id}")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def show_replicate_costs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расходы на Replicate."""
    user_id = update.effective_user.id
    
    try:
        log_entries_all_time = await get_generation_log_for_cost()
        thirty_days_ago_dt = datetime.now() - timedelta(days=30)
        thirty_days_ago_str = thirty_days_ago_dt.strftime('%Y-%m-%d %H:%M:%S')
        log_entries_30_days = await get_generation_log_for_cost(start_date_str=thirty_days_ago_str)
        
        total_cost_all_time = Decimal(0)
        costs_by_model_all_time = {}
        for entry in log_entries_all_time:
            model_id_entry, _, cost, _ = entry
            cost_decimal = Decimal(str(cost)) if cost is not None else Decimal(0)
            total_cost_all_time += cost_decimal
            key_for_dict = model_id_entry if model_id_entry else "unknown_model_id"
            costs_by_model_all_time[key_for_dict] = costs_by_model_all_time.get(key_for_dict, Decimal(0)) + cost_decimal
        
        total_cost_30_days = Decimal(0)
        costs_by_model_30_days = {}
        for entry in log_entries_30_days:
            model_id_entry, _, cost, _ = entry
            cost_decimal = Decimal(str(cost)) if cost is not None else Decimal(0)
            total_cost_30_days += cost_decimal
            key_for_dict = model_id_entry if model_id_entry else "unknown_model_id"
            costs_by_model_30_days[key_for_dict] = costs_by_model_30_days.get(key_for_dict, Decimal(0)) + cost_decimal

        text = "💰 Расходы на Replicate (USD):\n\n"
        text += f"За все время:\n  Общая сумма: `${total_cost_all_time:.4f}`\n"
        
        if costs_by_model_all_time:
            text += "  По моделям:\n"
            for model_id_from_log, cost_val in costs_by_model_all_time.items():
                model_name = "Неизвестная модель (ID отсутствует)"
                if model_id_from_log and model_id_from_log != "unknown_model_id":
                    model_name = next(
                        (m_data.get('name', model_id_from_log)
                         for _, m_data in IMAGE_GENERATION_MODELS.items()
                         if m_data.get('id') == model_id_from_log),
                        model_id_from_log
                    )
                elif model_id_from_log == "unknown_model_id":
                    model_name = "Неизвестная модель (ID не записан в лог)"
                
                text += f"    • {escape_md(model_name)}: `${cost_val:.4f}`\n"
        
        text += "\n"
        text += f"За последние 30 дней:\n  Общая сумма: `${total_cost_30_days:.4f}`\n"
        
        if costs_by_model_30_days:
            text += "  По моделям:\n"
            for model_id_from_log, cost_val in costs_by_model_30_days.items():
                model_name = "Неизвестная модель (ID отсутствует)"
                if model_id_from_log and model_id_from_log != "unknown_model_id":
                    model_name = next(
                        (m_data.get('name', model_id_from_log)
                         for _, m_data in IMAGE_GENERATION_MODELS.items()
                         if m_data.get('id') == model_id_from_log),
                        model_id_from_log
                    )
                elif model_id_from_log == "unknown_model_id":
                    model_name = "Неизвестная модель (ID не записан в лог)"
                
                text += f"    • {escape_md(model_name)}: `${cost_val:.4f}`\n"
        
        text += "\n_Примечание: Расчеты основаны на данных из лога генераций и могут быть приблизительными._"
        
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]])
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
        
    except Exception as e:
        logger.error(f"Ошибка при расчете расходов Replicate: {e}", exc_info=True)
        error_text = "❌ Не удалось рассчитать расходы. См. логи."
        admin_kb = await create_admin_keyboard()
        await send_message_with_fallback(
            context.bot, user_id, error_text, update_or_query=update,
            reply_markup=admin_kb, parse_mode=ParseMode.MARKDOWN_V2
        )

async def show_activity_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику активности пользователей за указанный период."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    context.user_data['awaiting_activity_dates'] = True
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
        context.bot, user_id, text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_activity_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, start_date: str, end_date: str) -> None:
    """Обрабатывает запрос статистики активности."""
    user_id = update.effective_user.id
    try:
        stats = await get_user_activity_stats(start_date, end_date)
        if not stats:
            text = escape_md(f"🚫 Нет данных об активности за период {start_date} - {end_date}.")
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_activity_stats")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        text = f"📊 Активность пользователей ({start_date} - {end_date})\n\n"
        for stat in stats[:10]:  # Ограничим до 10 пользователей
            user_id_stat, username, messages, photos, videos, purchases = stat
            username_display = f"@{username}" if username else f"ID {user_id_stat}"
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
            [InlineKeyboardButton("🔙 Админ-панель", callback_data="admin_panel")]
        ])
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при получении статистики активности: {e}", exc_info=True)
        text = escape_md("❌ Ошибка получения статистики. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

async def show_referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику реферальной программы."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        stats = await get_referral_stats()
        total_referrals = stats.get('total_referrals', 0)
        paid_referrals = stats.get('paid_referrals', 0)
        conversion_rate = (paid_referrals / total_referrals * 100) if total_referrals > 0 else 0
        top_referrers = stats.get('top_referrers', [])[:5]

        text = (
            f"🔗 Реферальная статистика\n\n"
            f"👥 Всего рефералов: `{total_referrals}`\n"
            f"💸 Оплативших рефералов: `{paid_referrals}` ({conversion_rate:.2f}%)\n"
        )

        if top_referrers:
            text += "\n🏆 Топ-5 приглашающих:\n"
            for referrer in top_referrers:
                user_id_ref, username, referral_count = referrer
                username_display = f"@{username}" if username else f"ID {user_id_ref}"
                text += f"  • {(username_display)}: `{referral_count}` рефералов\n"

        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Админ-панель", callback_data="admin_panel")]
        ])
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при получении реферальной статистики: {e}", exc_info=True)
        text = escape_md("❌ Ошибка получения статистики. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

async def show_visualization(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает меню выбора визуализации данных."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

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
        context.bot, user_id, text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )

async def visualize_payments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        msk_tz = pytz.timezone('Europe/Moscow')
        end_date = datetime.now(msk_tz).date()
        start_date = end_date - timedelta(days=30)
        payments = await get_payments_by_date(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))

        logger.info(f"Найдено {len(payments)} платежей за период {start_date} - {end_date}")
        if payments:
            logger.debug(f"Пример платежа: {payments[0]}")

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
                payment_date = payment[4]
                if payment_date.tzinfo:
                    payment_date = payment_date.astimezone(msk_tz).date()
                else:
                    payment_date = payment_date.date()
                logger.debug(f"Обработка платежа {payment[3]}: дата={payment_date}, сумма={payment[2]}")
                if start_date <= payment_date <= end_date:
                    payment_datetime = datetime(payment_date.year, payment_date.month, payment_date.day)
                    if payment_datetime.date() in dates:
                        index = dates.index(payment_datetime.date())
                        amounts[index] += float(payment[2]) if payment[2] is not None else 0.0
                        logger.debug(f"Добавлен платеж: дата={payment_date}, сумма={payment[2] if payment[2] is not None else 0.0}, индекс={index}")
                    else:
                        logger.warning(f"Дата платежа {payment_date} не найдена в списке dates")
                else:
                    logger.debug(f"Платеж вне диапазона: дата={payment_date}")
            except (ValueError, AttributeError) as e:
                logger.warning(f"Ошибка обработки даты платежа {payment[3]}: {e}")

        if not any(amounts):
            text = escape_md("⚠️ Нет данных о платежах за последние 30 дней.")
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
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
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

        await context.bot.send_photo(
            chat_id=user_id,
            photo=buffer,
            caption="График платежей"
        )

        buffer.close()

    except Exception as e:
        logger.error(f"Ошибка при визуализации платежей: {e}", exc_info=True)
        text = escape_md("❌ Ошибка создания графика. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

async def change_balance_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> int:
    """Инициирует изменение баланса пользователя."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data['awaiting_balance_change'] = True
    context.user_data['target_user_id'] = target_user_id
    text = escape_md(
        f"💰 Введите изменение баланса для ID `{target_user_id}`:\n"
        "Пример: `+10 фото` или `-3 аватара`"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]])
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_BALANCE_CHANGE

async def handle_balance_change_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод изменения баланса."""
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_balance_change'):
        return ConversationHandler.END

    target_user_id = context.user_data.pop('target_user_id', None)
    context.user_data.pop('awaiting_balance_change', None)
    if not target_user_id:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ Ошибка: пользователь не указан."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    input_text = update.message.text.strip()
    try:
        operation = 'add' if input_text.startswith('+') else 'subtract'
        amount = int(input_text[1:].split()[0])
        resource = input_text.split()[1].lower()
        if resource not in ['фото', 'аватара', 'аватар']:
            raise ValueError("Неверный тип ресурса")

        photos = amount if resource == 'фото' else 0
        avatars = amount if resource in ['аватара', 'аватар'] else 0

        success = await update_user_balance(target_user_id, photos, avatars, operation)
        user_info = await check_subscription(target_user_id)
        if success and user_info:
            text = escape_md(
                f"✅ Баланс ID `{target_user_id}` изменен: {input_text}\n"
                f"Текущий баланс: `{user_info[0]}` фото, `{user_info[1]}` аватаров"
            )
        else:
            text = escape_md(f"❌ Не удалось изменить баланс ID `{target_user_id}`")
    except Exception as e:
        text = escape_md(f"❌ Ошибка: {str(e)}. Пример: `+10 фото`")

    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]])
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    return ConversationHandler.END

async def show_user_logs(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Показывает логи действий пользователя."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        logs = await get_user_logs(target_user_id, limit=50)
        if not logs:
            text = f"📜 Логи для ID `{target_user_id}` не найдены."
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        text = f"📜 Логи пользователя ID `{target_user_id}` (последние 50):\n\n"
        for log in logs:
            timestamp, action_type, details = log
            timestamp_str = datetime.strptime(str(timestamp).split('.')[0], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
            text += f"• `{timestamp_str}`: {(action_type)} - {(truncate_text(str(details), 50))}\n"

        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]])
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при получении логов для ID {target_user_id}: {e}", exc_info=True)
        text = escape_md("❌ Ошибка получения логов. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def initiate_filtered_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Инициирует рассылку с фильтрами."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    # Очищаем предыдущие данные рассылки
    context.user_data.pop('broadcast_filters', None)
    context.user_data.pop('broadcast_message', None)
    context.user_data.pop('broadcast_media', None)
    context.user_data.pop('awaiting_broadcast_message', None)
    context.user_data.pop('awaiting_broadcast_media_confirm', None)

    context.user_data['awaiting_broadcast_filters'] = True
    text = escape_md(
        "🎯 Укажите критерии для рассылки в формате:\n"
        "min_photos=X max_photos=Y min_avatars=Z active_days=N min_generations=M\n"
        "Пример: `min_photos=0 max_photos=10 min_avatars=1 active_days=7 min_generations=5`\n"
        "Оставьте поле пустым для игнорирования критерия.\n"
        "Для отмены используйте /cancel."
    )
    keyboard = [
        [InlineKeyboardButton("Без фильтров", callback_data="broadcast_no_filters")],
        [InlineKeyboardButton("🔙 Админ-панель", callback_data="admin_panel")]
    ]
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_BROADCAST_FILTERS

async def handle_broadcast_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод фильтров для рассылки."""
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_broadcast_filters'):
        logger.warning(f"handle_broadcast_filters вызвана без awaiting_broadcast_filters для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка: действие рассылки не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data.pop('awaiting_broadcast_filters', None)
    
    # Обработка callback_query для кнопки "Без фильтров"
    if update.callback_query:
        await update.callback_query.answer()
        text = ""
    else:
        text = update.message.text.strip() if update.message and update.message.text else ""

    filters_dict = {
        'min_photos': None,
        'max_photos': None,
        'min_avatars': None,
        'active_days': None,
        'min_generations': None
    }

    if text:
        try:
            for part in text.split():
                key, value = part.split('=')
                if key in filters_dict:
                    filters_dict[key] = int(value)
        except (ValueError, KeyError) as e:
            logger.warning(f"Неверный формат фильтров от user_id={user_id}: {text}, ошибка: {e}")
            text = escape_md(
                "⚠️ Неверный формат фильтров. Используйте:\n"
                "`min_photos=X max_photos=Y min_avatars=Z active_days=N min_generations=M`\n"
                "Пример: `min_photos=0 max_photos=10 min_avatars=1 active_days=7 min_generations=5`"
            )
            keyboard = [
                [InlineKeyboardButton("Без фильтров", callback_data="broadcast_no_filters")],
                [InlineKeyboardButton("🔙 Админ-панель", callback_data="admin_panel")]
            ]
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
            )
            return AWAITING_BROADCAST_FILTERS

    context.user_data['broadcast_filters'] = filters_dict
    context.user_data['awaiting_broadcast_message'] = True

    text = escape_md(
        "📝 Введите текст сообщения для рассылки.\n"
        "Поддерживается форматирование Markdown V2.\n"
        "Для отмены используйте /cancel."
    )
    keyboard = [
        [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
    ]
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_BROADCAST_MESSAGE

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает текст сообщения для рассылки."""
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_broadcast_message'):
        logger.warning(f"handle_broadcast_message вызвана без awaiting_broadcast_message для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка: действие рассылки не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data.pop('awaiting_broadcast_message', None)
    message_text = update.message.text.strip() if update.message and update.message.text else ""

    if not message_text:
        logger.warning(f"Пустое сообщение рассылки от user_id={user_id}")
        text = escape_md("⚠️ Сообщение не может быть пустым. Введите текст сообщения.")
        keyboard = [
            [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
        ]
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        context.user_data['awaiting_broadcast_message'] = True
        return AWAITING_BROADCAST_MESSAGE

    context.user_data['broadcast_message'] = message_text
    context.user_data['awaiting_broadcast_media_confirm'] = True

    text = escape_md(
        "📸 Хотите прикрепить медиа к рассылке?\n"
        "Отправьте фото/видео или выберите 'Без медиа'."
    )
    keyboard = [
        [InlineKeyboardButton("Без медиа", callback_data="broadcast_no_media")],
        [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
    ]
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_BROADCAST_MEDIA_CONFIRM

async def handle_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает медиа для рассылки."""
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_broadcast_media_confirm'):
        logger.warning(f"handle_broadcast_media вызвана без awaiting_broadcast_media_confirm для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка: действие рассылки не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data.pop('awaiting_broadcast_media_confirm', None)
    media = None
    media_type = None

    if update.message:
        if update.message.photo:
            media = update.message.photo[-1].file_id
            media_type = 'photo'
        elif update.message.video:
            media = update.message.video.file_id
            media_type = 'video'
    elif update.callback_query and update.callback_query.data == "broadcast_no_media":
        await update.callback_query.answer()
        media = None
        media_type = None

    if media:
        context.user_data['broadcast_media'] = {'file_id': media, 'type': media_type}
    else:
        context.user_data.pop('broadcast_media', None)

    filters_dict = context.user_data.get('broadcast_filters', {})
    message_text = context.user_data.get('broadcast_message', '')

    # Получаем пользователей по фильтрам
    users = await get_paid_users() if filters_dict.get('min_photos', 0) > 0 else await get_non_paid_users()
    target_users = [user[0] for user in users]  # Предполагается, что user[0] - user_id

    text = escape_md(
        f"📢 Подтверждение рассылки\n\n"
        f"👥 Получатели: {len(target_users)} пользователей\n"
        f"📝 Сообщение:\n{message_text}\n\n"
        f"📸 Медиа: {'Есть' if media else 'Нет'}\n"
        f"⏰ Отправить сейчас или запланировать?"
    )
    keyboard = [
        [InlineKeyboardButton("📤 Отправить сейчас", callback_data="broadcast_send_now")],
        [InlineKeyboardButton("⏰ Запланировать", callback_data="broadcast_schedule")],
        [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
    ]
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_BROADCAST_SCHEDULE

async def handle_broadcast_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор времени отправки рассылки."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "broadcast_send_now":
        # Отправка рассылки сразу
        filters_dict = context.user_data.get('broadcast_filters', {})
        message_text = context.user_data.get('broadcast_message', '')
        media = context.user_data.get('broadcast_media', None)

        # Получаем пользователей по фильтрам
        users = await get_paid_users() if filters_dict.get('min_photos', 0) > 0 else await get_non_paid_users()
        target_users = [user[0] for user in users]

        success_count = 0
        error_count = 0
        
        # Начинаем рассылку
        await query.edit_message_text(
            escape_md("⏳ Выполняется рассылка..."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        for target_user_id in target_users:
            try:
                if media:
                    if media['type'] == 'photo':
                        await context.bot.send_photo(
                            chat_id=target_user_id,
                            photo=media['file_id'],
                            caption=message_text,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    elif media['type'] == 'video':
                        await context.bot.send_video(
                            chat_id=target_user_id,
                            video=media['file_id'],
                            caption=message_text,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                else:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=message_text,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                success_count += 1
                
                # Небольшая задержка между сообщениями
                if success_count % 20 == 0:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}")
                error_count += 1

        # Отчет о результатах
        text = escape_md(
            f"✅ Рассылка завершена!\n\n"
            f"📤 Успешно отправлено: {success_count}\n"
            f"❌ Ошибок: {error_count}\n"
            f"👥 Всего получателей: {len(target_users)}"
        )
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Очистка контекста
        context.user_data.pop('broadcast_filters', None)
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('broadcast_media', None)
        return ConversationHandler.END

    elif query.data == "broadcast_schedule":
        # Планирование рассылки на будущее
        context.user_data['awaiting_broadcast_schedule'] = True
        
        text = escape_md(
            "⏰ Введите дату и время для запланированной рассылки\n\n"
            "📅 Формат: `YYYY-MM-DD HH:MM`\n"
            "Пример: `2025-06-15 14:30`\n\n"
            "Время указывается в часовом поясе сервера (MSK)."
        )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        return AWAITING_BROADCAST_SCHEDULE
    
    else:
        logger.warning(f"Неизвестная callback_data в handle_broadcast_schedule: {query.data}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Неизвестная команда."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

async def handle_broadcast_schedule_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод времени для запланированной рассылки."""
    user_id = update.effective_user.id
    
    if not context.user_data.get('awaiting_broadcast_schedule'):
        logger.warning(f"handle_broadcast_schedule_input вызвана без awaiting_broadcast_schedule для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка: действие рассылки не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data.pop('awaiting_broadcast_schedule', None)
    text = update.message.text.strip()

    try:
        # Парсим введенную дату и время
        schedule_time = datetime.strptime(text, '%Y-%m-%d %H:%M')
        
        # Проверяем, что время не в прошлом
        if schedule_time < datetime.now():
            raise ValueError("Время рассылки не может быть в прошлом.")

        # Получаем данные рассылки из контекста
        filters_dict = context.user_data.get('broadcast_filters', {})
        message_text = context.user_data.get('broadcast_message', '')
        media = context.user_data.get('broadcast_media', None)

        # Сохраняем запланированную рассылку (используем функцию из database.py)
        await schedule_broadcast(schedule_time, message_text, media, filters_dict)
        
        text = escape_md(
            f"✅ Рассылка запланирована на {text}!\n"
            f"👥 Получатели будут определены по фильтрам на момент отправки."
        )
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Очистка контекста
        context.user_data.pop('broadcast_filters', None)
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('broadcast_media', None)
        
        return ConversationHandler.END

    except ValueError as e:
        logger.warning(f"Неверный формат времени рассылки от user_id={user_id}: {text}, ошибка: {e}")
        
        text = escape_md(
            f"⚠️ Неверный формат времени: {str(e)}\n\n"
            f"Используйте формат `YYYY-MM-DD HH:MM`\n"
            f"Например: `2025-06-14 14:30`"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
        ]
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Возвращаем состояние для повторного ввода
        context.user_data['awaiting_broadcast_schedule'] = True
        return AWAITING_BROADCAST_SCHEDULE

async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает отмену рассылки."""
    user_id = update.effective_user.id
    logger.debug(f"Отмена рассылки для user_id={user_id}")

    context.user_data.pop('awaiting_broadcast_filters', None)
    context.user_data.pop('awaiting_broadcast_message', None)
    context.user_data.pop('awaiting_broadcast_media_confirm', None)
    context.user_data.pop('awaiting_broadcast_schedule', None)
    context.user_data.pop('broadcast_filters', None)
    context.user_data.pop('broadcast_message', None)
    context.user_data.pop('broadcast_media', None)

    await send_message_with_fallback(
        context.bot, user_id,
        escape_md("✅ Рассылка отменена."),
        update_or_query=update,
        reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    logger.info(f"Рассылка отменена для user_id={user_id}")
    return ConversationHandler.END

# ===================== CONVERSATION HANDLER ДЛЯ РАССЫЛКИ =====================

broadcast_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(initiate_filtered_broadcast, pattern="^admin_broadcast$")
    ],
    states={
        AWAITING_BROADCAST_FILTERS: [
            CallbackQueryHandler(handle_broadcast_filters, pattern="^broadcast_no_filters$"),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_IDS),
                handle_broadcast_filters
            )
        ],
        AWAITING_BROADCAST_MESSAGE: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_IDS),
                handle_broadcast_message
            )
        ],
        AWAITING_BROADCAST_MEDIA_CONFIRM: [
            MessageHandler(
                filters.PHOTO | filters.VIDEO & filters.User(user_id=ADMIN_IDS),
                handle_broadcast_media
            ),
            CallbackQueryHandler(handle_broadcast_media, pattern="^broadcast_no_media$")
        ],
        AWAITING_BROADCAST_SCHEDULE: [
            CallbackQueryHandler(handle_broadcast_schedule, pattern="^broadcast_send_now$|^broadcast_schedule$"),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_IDS),
                handle_broadcast_schedule_input
            )
        ]
    },
    fallbacks=[
        CommandHandler("cancel", cancel_broadcast),
        CallbackQueryHandler(cancel_broadcast, pattern="^admin_panel$")
    ],
    per_user=True,
    per_chat=True,
    per_message=False
)
async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Направляет текстовые сообщения админа в правильный обработчик на основе текущего состояния."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        logger.warning(f"Несанкционированный доступ к handle_admin_text_input: user_id={user_id}")
        return ConversationHandler.END

    text = update.message.text.strip()
    logger.debug(f"Получено текстовое сообщение от админа user_id={user_id}: {text}")

    # Проверяем состояния
    if context.user_data.get('awaiting_block_reason'):
        logger.debug(f"Направление сообщения в handle_block_reason_input для user_id={user_id}")
        return await handle_block_reason_input(update, context)
    
    if context.user_data.get('awaiting_broadcast_filters'):
        logger.debug(f"Направление сообщения в handle_broadcast_filters для user_id={user_id}")
        return await handle_broadcast_filters(update, context)
    
    if context.user_data.get('awaiting_broadcast_message'):
        logger.debug(f"Направление сообщения в handle_broadcast_message для user_id={user_id}")
        return await handle_broadcast_message(update, context)
    
    if context.user_data.get('awaiting_broadcast_schedule'):
        logger.debug(f"Направление сообщения в handle_broadcast_schedule_input для user_id={user_id}")
        return await handle_broadcast_schedule_input(update, context)
    
    if context.user_data.get('awaiting_payments_date'):
        logger.debug(f"Направление сообщения в handle_payments_date_input для user_id={user_id}")
        return await handle_payments_date_input(update, context)
    
    if context.user_data.get('awaiting_user_search'):
        logger.debug(f"Направление сообщения в handle_user_search для user_id={user_id}")
        return await handle_user_search(update, context)
    
    if context.user_data.get('awaiting_balance_change'):
        logger.debug(f"Направление сообщения в handle_balance_change_input для user_id={user_id}")
        return await handle_balance_change_input(update, context)
    
    if context.user_data.get('awaiting_activity_dates'):
        logger.debug(f"Направление сообщения в handle_activity_dates_input для user_id={user_id}")
        return await handle_activity_dates_input(update, context)

    # Если нет активного состояния, уведомляем админа
    logger.warning(f"Текстовое сообщение от user_id={user_id} не соответствует ни одному активному состоянию: {text}")
    await send_message_with_fallback(
        context.bot, user_id,
        escape_md("⚠️ Нет активного действия. Выберите действие в админ-панели или завершите текущую операцию."),
        update_or_query=update,
        reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return ConversationHandler.END

admin_text_handler = MessageHandler(
    filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_IDS),
    handle_admin_text_input
)

# ПОСЛЕ admin_text_handler добавьте эту функцию:
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает callback-запросы админ-панели."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    data = query.data
    
    # Обработка различных callback
    if data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_stats":
        await show_admin_stats(update, context)
    elif data.startswith("admin_stats_page_"):
        page = int(data.split("_")[-1])
        await show_admin_stats(update, context, page)
    elif data == "admin_replicate_costs":
        await show_replicate_costs(update, context)
    elif data == "admin_activity_stats":
        await show_activity_stats(update, context)
    elif data == "admin_referral_stats":
        await show_referral_stats(update, context)
    elif data == "admin_visualization":
        await show_visualization(update, context)
    elif data == "visualize_payments":
        await visualize_payments(update, context)
    elif data == "visualize_registrations":
        await visualize_registrations(update, context)
    elif data == "visualize_generations":
        await visualize_generations(update, context)
    elif data == "admin_payments":
        await show_payments_menu(update, context)
    elif data == "admin_search_user":
        await search_users_admin(update, context)
    elif data == "admin_failed_avatars":
        await admin_show_failed_avatars(update, context)
    elif data == "admin_delete_all_failed":
        await admin_confirm_delete_all_failed(update, context)
    elif data == "admin_confirm_delete_all":
        await admin_execute_delete_all_failed(update, context)
    elif data.startswith("activity_"):
        if data == "activity_7_days":
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            await handle_activity_stats(update, context, start_date, end_date)
        elif data == "activity_30_days":
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            await handle_activity_stats(update, context, start_date, end_date)
    elif data.startswith("payments_date_"):
        parts = data.split("_")
        if len(parts) >= 4:
            start_date = parts[2]
            end_date = parts[3]
            await handle_payments_date(update, context, start_date, end_date)
    elif data == "payments_manual_date":
        await handle_manual_date_input(update, context)
    elif data.startswith("user_actions_"):
        target_user_id = int(data.split("_")[-1])
        await show_user_actions(update, context, target_user_id)
    elif data.startswith("user_profile_"):
        target_user_id = int(data.split("_")[-1])
        await show_user_profile_admin(update, context, target_user_id)
    elif data.startswith("user_avatars_"):
        target_user_id = int(data.split("_")[-1])
        await show_user_avatars_admin(update, context, target_user_id)
    elif data.startswith("change_balance_"):
        target_user_id = int(data.split("_")[-1])
        await change_balance_admin(update, context, target_user_id)
    elif data.startswith("user_logs_"):
        target_user_id = int(data.split("_")[-1])
        await show_user_logs(update, context, target_user_id)
    elif data.startswith("admin_generate:"):
        target_user_id = int(data.split(":")[1])
        await generate_photo_for_user(update, context, target_user_id)
    elif data.startswith("admin_video:"):
        target_user_id = int(data.split(":")[1])
        await generate_video_for_user(update, context, target_user_id)
    elif data.startswith("delete_user_"):
        target_user_id = int(data.split("_")[-1])
        await delete_user_admin(update, context, target_user_id)
    elif data.startswith("confirm_delete_user_"):
        target_user_id = int(data.split("_")[-1])
        await confirm_delete_user(update, context, target_user_id)
    elif data.startswith("block_user_"):
        target_user_id = int(data.split("_")[-1])
        await block_user_admin(update, context, target_user_id, block=True)
    elif data.startswith("unblock_user_"):
        target_user_id = int(data.split("_")[-1])
        await block_user_admin(update, context, target_user_id, block=False)
    elif data.startswith("confirm_block_user_"):
        parts = data.split("_")
        target_user_id = int(parts[3])
        action = parts[4]
        if action == "block":
            await confirm_block_user(update, context, target_user_id, block=True, block_reason="Без причины")
        elif action == "unblock":
            await confirm_block_user(update, context, target_user_id, block=False)
    elif data.startswith("reset_avatars_"):
        target_user_id = int(data.split("_")[-1])
        await confirm_reset_avatar(update, context, target_user_id)

async def handle_broadcast_schedule_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод времени для запланированной рассылки."""
    user_id = update.effective_user.id
    
    if not context.user_data.get('awaiting_broadcast_schedule'):
        logger.warning(f"handle_broadcast_schedule_input вызвана без awaiting_broadcast_schedule для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка: действие рассылки не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data.pop('awaiting_broadcast_schedule', None)
    text = update.message.text.strip()

    try:
        # Парсим введенную дату и время
        schedule_time = datetime.strptime(text, '%Y-%m-%d %H:%M')
        
        # Проверяем, что время не в прошлом
        if schedule_time < datetime.now():
            raise ValueError("Время рассылки не может быть в прошлом.")

        # Получаем данные рассылки из контекста
        filters_dict = context.user_data.get('broadcast_filters', {})
        message_text = context.user_data.get('broadcast_message', '')
        media = context.user_data.get('broadcast_media', None)

        # Сохраняем запланированную рассылку
        await schedule_broadcast(schedule_time, message_text, media, filters_dict)
        
        text = escape_md(
            f"✅ Рассылка запланирована на {text}!\n"
            f"👥 Получатели будут определены по фильтрам на момент отправки."
        )
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Очистка контекста
        context.user_data.pop('broadcast_filters', None)
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('broadcast_media', None)
        
        return ConversationHandler.END

    except ValueError as e:
        logger.warning(f"Неверный формат времени рассылки от user_id={user_id}: {text}, ошибка: {e}")
        
        text = escape_md(
            f"⚠️ Неверный формат времени: {str(e)}\n\n"
            f"Используйте формат `YYYY-MM-DD HH:MM`\n"
            f"Например: `2025-06-14 14:30`"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
        ]
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Возвращаем состояние для повторного ввода
        context.user_data['awaiting_broadcast_schedule'] = True
        return AWAITING_BROADCAST_SCHEDULE


# Также добавьте вспомогательную функцию schedule_broadcast, если её нет:
async def schedule_broadcast(schedule_time: datetime, message_text: str, media: Optional[Dict], filters_dict: Dict[str, Any]) -> None:
    """Сохраняет запланированную рассылку в базу данных."""
    try:
        broadcast_data = {
            'message': message_text,
            'media': media,
            'filters': filters_dict,
            'created_at': datetime.now().isoformat()
        }
        
        async with aiosqlite.connect('users.db') as conn:
            # Создаем таблицу если её нет
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scheduled_time TEXT NOT NULL,
                    broadcast_data TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await conn.execute(
                """
                INSERT INTO scheduled_broadcasts (scheduled_time, broadcast_data, status)
                VALUES (?, ?, 'pending')
                """,
                (schedule_time.isoformat(), json.dumps(broadcast_data, ensure_ascii=False))
            )
            await conn.commit()
            
        logger.info(f"Рассылка запланирована на {schedule_time}")
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении запланированной рассылки: {e}")
        raise
async def handle_broadcast_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор времени отправки рассылки."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "broadcast_send_now":
        # Отправка рассылки сразу
        filters_dict = context.user_data.get('broadcast_filters', {})
        message_text = context.user_data.get('broadcast_message', '')
        media = context.user_data.get('broadcast_media', None)

        # Получаем пользователей по фильтрам
        users = await get_paid_users() if filters_dict.get('min_photos', 0) > 0 else await get_non_paid_users()
        target_users = [user[0] for user in users]

        success_count = 0
        error_count = 0
        
        # Начинаем рассылку
        await query.edit_message_text(
            escape_md("⏳ Выполняется рассылка..."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        for target_user_id in target_users:
            try:
                if media:
                    if media['type'] == 'photo':
                        await context.bot.send_photo(
                            chat_id=target_user_id,
                            photo=media['file_id'],
                            caption=message_text,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    elif media['type'] == 'video':
                        await context.bot.send_video(
                            chat_id=target_user_id,
                            video=media['file_id'],
                            caption=message_text,
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                else:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=message_text,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                success_count += 1
                
                # Небольшая задержка между сообщениями
                if success_count % 20 == 0:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения пользователю {target_user_id}: {e}")
                error_count += 1

        # Отчет о результатах
        text = escape_md(
            f"✅ Рассылка завершена!\n\n"
            f"📤 Успешно отправлено: {success_count}\n"
            f"❌ Ошибок: {error_count}\n"
            f"👥 Всего получателей: {len(target_users)}"
        )
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Очистка контекста
        context.user_data.pop('broadcast_filters', None)
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('broadcast_media', None)
        return ConversationHandler.END

    elif query.data == "broadcast_schedule":
        # Планирование рассылки на будущее
        context.user_data['awaiting_broadcast_schedule'] = True
        
        text = escape_md(
            "⏰ Введите дату и время для запланированной рассылки\n\n"
            "📅 Формат: `YYYY-MM-DD HH:MM`\n"
            "Пример: `2025-06-15 14:30`\n\n"
            "Время указывается в часовом поясе сервера (MSK)."
        )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        return AWAITING_BROADCAST_SCHEDULE
    
    else:
        logger.warning(f"Неизвестная callback_data в handle_broadcast_schedule: {query.data}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Неизвестная команда."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END
    
    async def get_filtered_users_count(filters_dict: Dict[str, Any]) -> int:
        """Получает количество пользователей по заданным фильтрам."""
    try:
        query = "SELECT COUNT(*) FROM users WHERE 1=1"
        params = []
        
        if filters_dict:
            if 'has_payment' in filters_dict:
                if filters_dict['has_payment']:
                    query += " AND user_id IN (SELECT DISTINCT user_id FROM payments WHERE status = 'completed')"
                else:
                    query += " AND user_id NOT IN (SELECT DISTINCT user_id FROM payments WHERE status = 'completed')"
            
            if 'registration_after' in filters_dict:
                query += " AND registration_date >= ?"
                params.append(filters_dict['registration_after'])
            
            if 'registration_before' in filters_dict:
                query += " AND registration_date <= ?"
                params.append(filters_dict['registration_before'])
            
            if 'min_balance' in filters_dict:
                query += " AND balance >= ?"
                params.append(filters_dict['min_balance'])
            
            if 'max_balance' in filters_dict:
                query += " AND balance <= ?"
                params.append(filters_dict['max_balance'])
            
            if 'is_blocked' in filters_dict:
                query += " AND is_blocked = ?"
                params.append(1 if filters_dict['is_blocked'] else 0)
        
        async with aiosqlite.connect('users.db') as conn:
            cursor = await conn.cursor()
            await cursor.execute(query, params)
            result = await cursor.fetchone()
            return result[0] if result else 0
            
    except Exception as e:
        logger.error(f"Ошибка при подсчете пользователей: {e}")
        return 0
    
async def handle_broadcast_schedule_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод времени для запланированной рассылки."""
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_broadcast_schedule'):
        logger.warning(f"handle_broadcast_schedule_input вызвана без awaiting_broadcast_schedule для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка: действие рассылки не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data.pop('awaiting_broadcast_schedule', None)
    text = update.message.text.strip()

    try:
        schedule_time = datetime.strptime(text, '%Y-%m-%d %H:%M')
        if schedule_time < datetime.now():
            raise ValueError("Время рассылки не может быть в прошлом.")

        filters_dict = context.user_data.get('broadcast_filters', {})
        message_text = context.user_data.get('broadcast_message', '')
        media = context.user_data.get('broadcast_media', None)

        await schedule_broadcast(schedule_time, message_text, media, filters_dict)
        text = escape_md(
            f"✅ Рассылка запланирована на {text}!\n"
            f"👥 Получатели будут определены по фильтрам на момент отправки."
        )
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Очистка контекста
        context.user_data.pop('broadcast_filters', None)
        context.user_data.pop('broadcast_message', None)
        context.user_data.pop('broadcast_media', None)
        return ConversationHandler.END

    except ValueError as e:
        logger.warning(f"Неверный формат времени рассылки от user_id={user_id}: {text}, ошибка: {e}")
        text = escape_md(
            f"⚠️ Неверный формат времени: {str(e)}. Используйте `YYYY-MM-DD HH:MM` (например, `2025-06-14 14:30`)."
        )
        keyboard = [
            [InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]
        ]
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        context.user_data['awaiting_broadcast_schedule'] = True
        return AWAITING_BROADCAST_SCHEDULE        

async def search_users_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Инициирует поиск пользователей."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."), parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data['awaiting_user_search'] = True
    context.user_data['admin_view_source'] = 'admin_search_user'
    text = escape_md(
        "🔍 Введите запрос для поиска пользователей (ID, имя, username или email):"
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]])
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_USER_SEARCH

async def handle_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает поиск пользователей.

    Args:
        update: Объект обновления от Telegram.
        context: Контекст бота.

    Returns:
        int: Код завершения диалога (ConversationHandler.END).
    """
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_user_search'):
        return ConversationHandler.END

    context.user_data.pop('awaiting_user_search', None)
    query = update.message.text.strip()

    try:
        users = await search_users(query)
        if not users:
            text = escape_md(f"🚫 Пользователи по запросу `{query}` не найдены.")
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Новый поиск", callback_data="admin_search_user")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return ConversationHandler.END

        text = f"🔍 Результаты поиска для `{escape_md(query)}`:\n\n"
        keyboard = []
        for user in users[:10]:
            # Проверяем, что запись содержит минимум 4 поля
            if len(user) < 4:
                logger.warning(f"Недостаточно данных для пользователя: {user}")
                continue

            # Извлекаем только нужные поля, игнорируя лишние
            u_id = user[0]
            u_name = user[1] if len(user) > 1 else None
            f_name = user[2] if len(user) > 2 else None
            email = user[3] if len(user) > 3 else None

            display_name = f_name or u_name or f"ID {u_id}"
            username_display = f"@{u_name}" if u_name and u_name != "Без имени" else ""
            text += f"👤 {escape_md(display_name)} {username_display} (ID: `{u_id}`)\n"
            text += f"📧 {escape_md(email or 'Не указан')}\n\n"
            keyboard.append([InlineKeyboardButton(
                f"👤 {truncate_text(display_name, 20)} (ID: {u_id})",
                callback_data=f"user_actions_{u_id}"
            )])

        if len(users) > 10:
            text += f"_...и еще {len(users) - 10} пользователей._"

        keyboard.append([InlineKeyboardButton("🔍 Новый поиск", callback_data="admin_search_user")])
        keyboard.append([InlineKeyboardButton("🔙 Админ-панель", callback_data="admin_panel")])
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка при поиске пользователей: {e}", exc_info=True)
        text = escape_md("❌ Ошибка поиска. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

    return ConversationHandler.END

async def broadcast_message_admin(context: ContextTypes.DEFAULT_TYPE, message_text: str, admin_user_id: int, media_type: str = None, media_id: str = None) -> None:
    """Рассылка сообщения всем пользователям с поддержкой фото/видео."""
    bot = context.bot
    all_users_data, total_users_count = await get_all_users_stats(page_size=1000000)
    all_user_ids = [user_data[0] for user_data in all_users_data]
    
    sent_count = 0
    failed_count = 0
    total_to_send = len(all_user_ids)
    
    logger.info(f"Начало рассылки сообщения от админа {admin_user_id} для {total_to_send} пользователей.")
    
    await send_message_with_fallback(
        bot, admin_user_id, escape_md(f"🚀 Начинаю рассылку для ~{total_to_send} пользователей..."),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    for target_user_id_broadcast in all_user_ids:
        try:
            if media_type == 'photo' and media_id:
                await bot.send_photo(
                    chat_id=target_user_id_broadcast, photo=media_id,
                    caption=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            elif media_type == 'video' and media_id:
                await bot.send_video(
                    chat_id=target_user_id_broadcast, video=media_id,
                    caption=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await bot.send_message(
                    chat_id=target_user_id_broadcast,
                    text=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            sent_count += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение пользователю {target_user_id_broadcast}: {type(e).__name__} - {e}")
            failed_count += 1
        
        if sent_count % 20 == 0:
            await asyncio.sleep(1)
    
    summary_text = (
        f"🏁 Рассылка завершена!\n"
        f"✅ Отправлено: {sent_count}\n"
        f"❌ Не удалось отправить: {failed_count}"
    )
    
    await send_message_with_fallback(
        bot, admin_user_id, escape_md(summary_text), reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    logger.info(f"Рассылка завершена. Отправлено: {sent_count}, Ошибок: {failed_count}")

# Продолжение в следующей части
# === ПРОДОЛЖЕНИЕ ФАЙЛА admin.py, ЧАСТЬ 2/3 ===
async def broadcast_to_paid_users(context: ContextTypes.DEFAULT_TYPE, message_text: str, admin_user_id: int, media_type: str = None, media_id: str = None) -> None:
    """Рассылка сообщения только оплатившим пользователям."""
    bot = context.bot
    paid_user_ids = await get_paid_users()
    
    sent_count = 0
    failed_count = 0
    total_to_send = len(paid_user_ids)
    
    logger.info(f"Начало рассылки для оплативших пользователей от админа {admin_user_id} для {total_to_send} пользователей.")
    
    await send_message_with_fallback(
        bot, admin_user_id, escape_md(f"🚀 Начинаю рассылку для ~{total_to_send} оплативших пользователей..."),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    for target_user_id in paid_user_ids:
        try:
            if media_type == 'photo' and media_id:
                await bot.send_photo(
                    chat_id=target_user_id, photo=media_id,
                    caption=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            elif media_type == 'video' and media_id:
                await bot.send_video(
                    chat_id=target_user_id, video=media_id,
                    caption=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await bot.send_message(
                    chat_id=target_user_id,
                    text=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            sent_count += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение пользователю {target_user_id}: {type(e).__name__} - {e}")
            failed_count += 1
        
        if sent_count % 20 == 0:
            await asyncio.sleep(1)
    
    summary_text = (
        f"🏁 Рассылка для оплативших завершена!\n"
        f"✅ Отправлено: {sent_count}\n"
        f"❌ Не удалось отправить: {failed_count}"
    )
    
    await send_message_with_fallback(
        bot, admin_user_id, escape_md(summary_text), reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    logger.info(f"Рассылка для оплативших завершена. Отправлено: {sent_count}, Ошибок: {failed_count}")

async def broadcast_to_non_paid_users(context: ContextTypes.DEFAULT_TYPE, message_text: str, admin_user_id: int, media_type: str = None, media_id: str = None) -> None:
    """Рассылка сообщения только не оплатившим пользователям."""
    bot = context.bot
    non_paid_user_ids = await get_non_paid_users()
    
    sent_count = 0
    failed_count = 0
    total_to_send = len(non_paid_user_ids)
    
    logger.info(f"Начало рассылки для не оплативших пользователей от админа {admin_user_id} для {total_to_send} пользователей.")
    
    await send_message_with_fallback(
        bot, admin_user_id, escape_md(f"🚀 Начинаю рассылку для ~{total_to_send} не оплативших пользователей..."),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    for target_user_id in non_paid_user_ids:
        try:
            if media_type == 'photo' and media_id:
                await bot.send_photo(
                    chat_id=target_user_id, photo=media_id,
                    caption=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            elif media_type == 'video' and media_id:
                await bot.send_video(
                    chat_id=target_user_id, video=media_id,
                    caption=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                await bot.send_message(
                    chat_id=target_user_id,
                    text=escape_md(message_text + "\n\n— Уведомление"),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            sent_count += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение пользователю {target_user_id}: {type(e).__name__} - {e}")
            failed_count += 1
        
        if sent_count % 20 == 0:
            await asyncio.sleep(1)
    
    summary_text = (
        f"🏁 Рассылка для не оплативших завершена!\n"
        f"✅ Отправлено: {sent_count}\n"
        f"❌ Не удалось отправить: {failed_count}"
    )
    
    await send_message_with_fallback(
        bot, admin_user_id, escape_md(summary_text), reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    logger.info(f"Рассылка для не оплативших завершена. Отправлено: {sent_count}, Ошибок: {failed_count}")

async def initiate_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, broadcast_type: str) -> int:
    """Инициирует рассылку (общую, для оплативших или не оплативших)."""
    user_id = update.effective_user.id
    
    context.user_data[f'awaiting_{broadcast_type}_message'] = True
    context.user_data['broadcast_type'] = broadcast_type
    
    text = (
        "📢 Введите текст сообщения для рассылки.\n"
        "Вы также можете отправить фото или видео, и они будут включены в рассылку.\n"
        "Для отмены нажмите кнопку ниже."
    )
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="admin_panel")]])
    
    await send_message_with_fallback(
        context.bot, user_id, escape_md(text), update_or_query=update,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_BROADCAST_MESSAGE

async def handle_admin_chat_message(context: ContextTypes.DEFAULT_TYPE, target_user_id: int, message_text: str, media_type: str = None, media_id: str = None) -> None:
    """Отправка сообщения конкретному пользователю с поддержкой фото/видео."""
    bot = context.bot
    admin_user_id = context.user_data.get('admin_user_id')
    
    try:
        if media_type == 'photo' and media_id:
            await bot.send_photo(
                chat_id=target_user_id, photo=media_id,
                caption=escape_md(message_text + "\n\n— Уведомление"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif media_type == 'video' and media_id:
            await bot.send_video(
                chat_id=target_user_id, video=media_id,
                caption=escape_md(message_text + "\n\n— Уведомление"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await bot.send_message(
                chat_id=target_user_id,
                text=escape_md(message_text + "\n\n— Уведомление"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        await send_message_with_fallback(
            bot, admin_user_id, escape_md(f"✅ Сообщение отправлено пользователю ID {target_user_id}."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения от админа {admin_user_id} пользователю {target_user_id}: {e}")
        await send_message_with_fallback(
            bot, admin_user_id, escape_md(f"❌ Не удалось отправить сообщение: {e}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )

# Дополнительная функция для обработки результата генерации админа
async def handle_admin_generation_result(context: ContextTypes.DEFAULT_TYPE, admin_id: int, target_user_id: int, result_data: dict) -> None:
    """Обрабатывает результат генерации, выполненной админом для пользователя."""
    try:
        if result_data.get('success'):
            # Отправляем результат админу
            if result_data.get('image_url'):
                caption = escape_md(
                    f"✅ Генерация для пользователя {target_user_id} завершена!\n"
                    f"👤 Аватар: {context.user_data.get('active_avatar_name', 'Неизвестно')}\n"
                    f"🎨 Стиль: {result_data.get('style', 'custom')}\n"
                    f"📝 Промпт: {result_data.get('prompt', 'Не указан')}"
                )
                
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Еще раз", callback_data=f"admin_generate:{target_user_id}")],
                    [InlineKeyboardButton("📤 Отправить пользователю", callback_data=f"admin_send_gen:{target_user_id}:{result_data.get('generation_id')}")],
                    [InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ])
                
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=result_data['image_url'],
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard
                )
                
                # Сохраняем информацию о генерации в контексте для возможной отправки пользователю
                context.user_data[f'last_admin_generation_{target_user_id}'] = {
                    'image_url': result_data['image_url'],
                    'generation_id': result_data.get('generation_id'),
                    'style': result_data.get('style'),
                    'prompt': result_data.get('prompt')
                }
                
            else:
                await send_message_with_fallback(
                    context.bot, admin_id,
                    escape_md(f"✅ Генерация завершена, но изображение не получено."),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        else:
            error_msg = result_data.get('error', 'Неизвестная ошибка')
            await send_message_with_fallback(
                context.bot, admin_id,
                escape_md(f"❌ Ошибка генерации: {error_msg}"),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
    except Exception as e:
        logger.error(f"Ошибка обработки результата админской генерации: {e}")
        await send_message_with_fallback(
            context.bot, admin_id,
            escape_md(f"❌ Ошибка при обработке результата: {str(e)}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
# Обработчик для отправки сгенерированного изображения пользователю
async def process_image_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, num_outputs: int) -> None:
    """Отправляет сгенерированное админом изображение пользователю."""
    admin_id = update.effective_user.id
    parts = callback_data.split(':')
    target_user_id = int(parts[1])
    generation_id = parts[2] if len(parts) > 2 else None
    
    # Получаем данные последней генерации из контекста
    generation_data = context.user_data.get(f'last_admin_generation_{target_user_id}')
    if is_admin_generation and result_urls:
        # Импортируем только когда нужно, чтобы избежать циклического импорта
        from handlers.admin import handle_admin_generation_result
        
        result_data = {
            'success': True,
            'image_urls': result_urls,
            'prompt': context.user_data.get('prompt', ''),
            'style': context.user_data.get('style_name', 'custom')
        }
        
        await handle_admin_generation_result(context, actual_user_id, target_user_id, result_data)
        
        # Очищаем флаги админской генерации
        context.user_data.pop('is_admin_generation', None)
        context.user_data.pop('admin_generation_for_user', None)
    if not generation_data:
        await update.callback_query.answer("❌ Данные генерации не найдены", show_alert=True)
        return
    
    try:
        # Отправляем изображение пользователю
        caption = escape_md(
            f"🎁 Для вас создано новое изображение!\n"
            f"✨ Создано в подарок Нейросетью\n"
            f"🎨 Стиль: {generation_data.get('style', 'custom')}"
        )
        
        await context.bot.send_photo(
            chat_id=target_user_id,
            photo=generation_data['image_url'],
            caption=caption,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        await update.callback_query.answer("✅ Изображение отправлено пользователю!", show_alert=True)
        
        # Уведомляем админа
        await send_message_with_fallback(
            context.bot, admin_id,
            escape_md(f"✅ Изображение успешно отправлено пользователю {target_user_id}"),
            update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
    except Exception as e:
        logger.error(f"Ошибка отправки генерации пользователю: {e}")
        await update.callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

async def generate_video_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Инициирует генерацию видео для указанного пользователя."""
    user_id = update.effective_user.id
    logger.debug(f"Инициирована генерация видео для target_user_id={target_user_id} администратором user_id={user_id}")

    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, user_id, escape_md(f"❌ Пользователь ID `{target_user_id}` не найден."),
            update_or_query=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    video_cost = 20
    if not await check_resources(context, target_user_id, required_photos=video_cost):
        await send_message_with_fallback(
            context.bot, user_id, escape_md(f"❌ У пользователя ID `{target_user_id}` недостаточно фото на балансе для генерации видео."),
            update_or_query=update, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    context.user_data['admin_target_user_id'] = target_user_id
    context.user_data['generation_type'] = 'ai_video'
    context.user_data['model_key'] = 'kwaivgi/kling-v1.6-pro'
    context.user_data['video_cost'] = video_cost
    context.user_data['waiting_for_video_prompt'] = True

    text = escape_md(
        f"🎬 Генерация видео для пользователя ID `{target_user_id}`\n\n"
        f"📝 Опишите, какое движение или действие должно происходить в видео:"
    )
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def delete_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Подтверждение удаления пользователя."""
    user_id = update.effective_user.id
    logger.debug(f"Запрошено подтверждение удаления для target_user_id={target_user_id} администратором user_id={user_id}")
    
    text = (
        f"⚠️ Подтверждение удаления\n\n"
        f"Вы уверены, что хотите удалить пользователя ID `{target_user_id}`?\n"
        f"Это действие необратимо и удалит все данные пользователя, включая аватары, платежи и логи."
    )
    await send_message_with_fallback(
        context.bot, user_id, escape_md(text), update_or_query=update,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Да, удалить", callback_data=f"confirm_delete_user_{target_user_id}")],
            [InlineKeyboardButton("🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]), parse_mode=ParseMode.MARKDOWN_V2
    )

async def confirm_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Выполняет удаление пользователя с уведомлением."""
    user_id = update.effective_user.id
    bot = context.bot
    logger.debug(f"Подтверждено удаление target_user_id={target_user_id} администратором user_id={user_id}")

    try:
        target_user_info = await check_subscription(target_user_id)
        if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
            text = escape_md(f"❌ Пользователь ID `{target_user_id}` не найден.")
            reply_markup = await create_admin_keyboard()
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        try:
            await bot.send_message(
                chat_id=target_user_id,
                text=escape_md("⚠️ Ваш аккаунт был удален администратором. Для уточнения причин обратитесь в поддержку: @AXIDI_Help"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"Уведомление об удалении отправлено пользователю user_id={target_user_id}")
        except Exception as e_notify:
            logger.warning(f"Не удалось уведомить пользователя {target_user_id} об удалении: {e_notify}")

        success = await delete_user(target_user_id)
        if success:
            text = escape_md(f"✅ Пользователь ID `{target_user_id}` успешно удален из всех таблиц.")
            reply_markup = await create_admin_keyboard()
            logger.info(f"Пользователь user_id={target_user_id} удален администратором user_id={user_id}")
        else:
            text = escape_md(f"❌ Не удалось удалить пользователя ID `{target_user_id}`. Возможно, пользователь уже удален.")
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В админ-панель", callback_data="admin_panel")]])
            logger.error(f"Не удалось удалить пользователя user_id={target_user_id}")

        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Критическая ошибка при удалении пользователя user_id={target_user_id}: {e}", exc_info=True)
        text = escape_md(f"❌ Произошла ошибка при удалении пользователя ID `{target_user_id}`: {str(e)}. Проверьте логи.")
        reply_markup = await create_admin_keyboard()
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Направляет текстовые сообщения админа в правильный обработчик на основе текущего состояния."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        logger.warning(f"Несанкционированный доступ к handle_admin_text_input: user_id={user_id}")
        return ConversationHandler.END

    text = update.message.text.strip()
    logger.debug(f"Получено текстовое сообщение от админа user_id={user_id}: {text}")

    # Проверяем состояния
    if context.user_data.get('awaiting_block_reason'):
        logger.debug(f"Направление сообщения в handle_block_reason_input для user_id={user_id}")
        return await handle_block_reason_input(update, context)
    
    if context.user_data.get('awaiting_broadcast_filters'):
        logger.debug(f"Направление сообщения в handle_broadcast_filters для user_id={user_id}")
        return await handle_broadcast_filters(update, context)
    
    if context.user_data.get('awaiting_broadcast_message'):
        logger.debug(f"Направление сообщения в handle_broadcast_message для user_id={user_id}")
        return await handle_broadcast_message(update, context)
    
    if context.user_data.get('awaiting_payments_date'):
        logger.debug(f"Направление сообщения в handle_payments_date_input для user_id={user_id}")
        return await handle_payments_date_input(update, context)
    
    if context.user_data.get('awaiting_user_search'):
        logger.debug(f"Направление сообщения в handle_user_search_input для user_id={user_id}")
        return await handle_user_search_input(update, context)  # Предполагается существование этой функции
    
    if context.user_data.get('awaiting_balance_change'):
        logger.debug(f"Направление сообщения в handle_balance_change_input для user_id={user_id}")
        return await handle_balance_change_input(update, context)
    
    if context.user_data.get('awaiting_activity_dates'):
        logger.debug(f"Направление сообщения в handle_activity_dates_input для user_id={user_id}")
        return await handle_activity_dates_input(update, context)

    # Если нет активного состояния, уведомляем админа
    logger.warning(f"Текстовое сообщение от user_id={user_id} не соответствует ни одному активному состоянию: {text}")
    await send_message_with_fallback(
        context.bot, user_id,
        escape_md("⚠️ Нет активного действия. Выберите действие в админ-панели или завершите текущую операцию."),
        update_or_query=update,
        reply_markup=await create_admin_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return ConversationHandler.END

async def block_user_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int, block: bool = True) -> Optional[int]:
    """Запрашивает подтверждение блокировки/разблокировки пользователя."""
    user_id = update.effective_user.id
    action = "заблокировать" if block else "разблокировать"
    action_emoji = "🔒" if block else "🔓"
    logger.debug(f"Запрошено подтверждение {action} для target_user_id={target_user_id} администратором user_id={user_id}")

    if user_id not in ADMIN_IDS:
        await update.callback_query.answer("⛔ Недостаточно прав", show_alert=True)
        logger.warning(f"Попытка {action} без прав: user_id={user_id}")
        return None

    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, user_id,
            safe_escape_markdown(f"❌ Пользователь ID `{target_user_id}` не найден."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.warning(f"Пользователь target_user_id={target_user_id} не найден")
        return None

    is_already_blocked = await is_user_blocked(target_user_id)
    if block and is_already_blocked:
        await send_message_with_fallback(
            context.bot, user_id,
            safe_escape_markdown(f"⚠️ Пользователь ID `{target_user_id}` уже заблокирован."),
            update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Попытка повторной блокировки: target_user_id={target_user_id}")
        return None
    elif not block and not is_already_blocked:
        await send_message_with_fallback(
            context.bot, user_id,
            safe_escape_markdown(f"⚠️ Пользователь ID `{target_user_id}` не заблокирован."),
            update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Попытка повторной разблокировки: target_user_id={target_user_id}")
        return None

    if block:
        context.user_data['awaiting_block_reason'] = {'target_user_id': target_user_id}
        text = safe_escape_markdown(
            f"⚠️ Укажите причину блокировки пользователя ID `{target_user_id}`.\n"
            "Введите текст причины или нажмите 'Без причины'.\n"
            "Для отмены используйте /cancel."
        )
        keyboard = [
            [InlineKeyboardButton("Без причины", callback_data=f"confirm_block_user_{target_user_id}_block_no_reason")],
            [InlineKeyboardButton("🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Ожидается ввод причины блокировки для target_user_id={target_user_id} от user_id={user_id}")
        return AWAITING_BLOCK_REASON
    else:
        text = safe_escape_markdown(
            f"⚠️ Подтверждение действия\n\n"
            f"Вы уверены, что хотите {action} пользователя ID `{target_user_id}`?\n"
            "Разблокировка восстановит полный доступ."
        )
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{action_emoji} Да, {action}", callback_data=f"confirm_block_user_{target_user_id}_unblock")],
                [InlineKeyboardButton("🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
            ]), parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Запрошено подтверждение разблокировки для target_user_id={target_user_id}")
        return None

async def handle_block_reason_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод причины блокировки."""
    user_id = update.effective_user.id
    logger.debug(f"Вызвана handle_block_reason_input для user_id={user_id}")

    if not context.user_data.get('awaiting_block_reason'):
        logger.warning(f"handle_block_reason_input вызвана без awaiting_block_reason для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            safe_escape_markdown("❌ Ошибка: действие блокировки не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    target_user_id = context.user_data['awaiting_block_reason']['target_user_id']
    context.user_data.pop('awaiting_block_reason', None)
    logger.debug(f"Обработка причины блокировки для target_user_id={target_user_id}")

    if not update.message or not update.message.text:
        logger.warning(f"Отсутствует текст сообщения для user_id={user_id}, target_user_id={target_user_id}")
        error_text = safe_escape_markdown(
            "⚠️ Пожалуйста, введите текстовую причину блокировки или выберите 'Без причины'.\n"
            "Для отмены используйте /cancel."
        )
        keyboard = [
            [InlineKeyboardButton("Без причины", callback_data=f"confirm_block_user_{target_user_id}_block_no_reason")],
            [InlineKeyboardButton("🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]
        await send_message_with_fallback(
            context.bot, user_id, error_text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        context.user_data['awaiting_block_reason'] = {'target_user_id': target_user_id}
        return AWAITING_BLOCK_REASON

    reason = update.message.text.strip()
    if not reason or len(reason) > 255:
        logger.warning(f"Некорректная причина блокировки от user_id={user_id} для target_user_id={target_user_id}: {reason}")
        error_text = safe_escape_markdown(
            "⚠️ Причина должна быть текстом длиной до 255 символов.\n"
            "Попробуйте снова или выберите 'Без причины'.\n"
            "Для отмены используйте /cancel."
        )
        keyboard = [
            [InlineKeyboardButton("Без причины", callback_data=f"confirm_block_user_{target_user_id}_block_no_reason")],
            [InlineKeyboardButton("🔙 Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]
        await send_message_with_fallback(
            context.bot, user_id, error_text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
        context.user_data['awaiting_block_reason'] = {'target_user_id': target_user_id}
        return AWAITING_BLOCK_REASON

    logger.info(f"Причина блокировки для target_user_id={target_user_id}: {reason}")
    await confirm_block_user(update, context, target_user_id, block=True, block_reason=reason)
    return ConversationHandler.END

async def confirm_block_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int, block: bool, block_reason: Optional[str] = None) -> None:
    """Выполняет блокировку/разблокировку пользователя."""
    user_id = update.effective_user.id
    action = "заблокирован" if block else "разблокирован"
    action_emoji = "🔒" if block else "🔓"
    logger.debug(f"Подтверждено {action} target_user_id={target_user_id} администратором user_id={user_id}")

    # Проверка прав администратора
    if user_id not in ADMIN_IDS:
        await update.callback_query.answer("⛔ Недостаточно прав", show_alert=True)
        logger.warning(f"Попытка {action} без прав: user_id={user_id}")
        return

    # Проверка существования пользователя
    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, user_id,
            safe_escape_markdown(f"❌ Пользователь ID `{target_user_id}` не найден."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.warning(f"Пользователь target_user_id={target_user_id} не найден")
        return

    try:
        # Выполняем блокировку/разблокировку
        success = await block_user(target_user_id, block=block, block_reason=block_reason)
        if success:
            if block:
                # Очищаем контекст пользователя
                isolated_context = create_isolated_context(context, target_user_id)
                clean_admin_context(isolated_context)
                if target_user_id in context.user_data:
                    clean_admin_context(context.user_data[target_user_id])
                    logger.debug(f"Контекст пользователя user_id={target_user_id} очищен из context.user_data")

                # Уведомляем пользователя
                reason_text = f"\nПричина: {block_reason}" if block_reason else ""
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=safe_escape_markdown(
                            f"🚫 Ваш аккаунт заблокирован. Для уточнения причин обратитесь в поддержку: @AXIDI_Help{reason_text}"
                        ),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    logger.info(f"Уведомление о блокировке отправлено пользователю user_id={target_user_id}")
                except Exception as e_notify:
                    logger.warning(f"Не удалось уведомить пользователя {target_user_id} о блокировке: {e_notify}")

            text = safe_escape_markdown(
                f"✅ Пользователь ID `{target_user_id}` успешно {action}."
                f"{f' Причина: {block_reason}' if block_reason else ''}"
            )
            logger.info(f"Пользователь user_id={target_user_id} {action} администратором user_id={user_id}")
        else:
            text = safe_escape_markdown(f"❌ Не удалось {action} пользователя ID `{target_user_id}`. Пользователь не найден.")
            logger.error(f"Не удалось {action} пользователя user_id={target_user_id}")

        # Отправляем результат админу
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        text = safe_escape_markdown(f"❌ Ошибка при {action} пользователя ID `{target_user_id}`: {str(e)}. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.error(f"Критическая ошибка при {action} пользователя user_id={target_user_id}: {e}", exc_info=True)

async def cancel_block_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает отмену ввода причины блокировки."""
    user_id = update.effective_user.id
    logger.debug(f"Отмена ввода причины блокировки для user_id={user_id}")

    if 'awaiting_block_reason' in context.user_data:
        target_user_id = context.user_data['awaiting_block_reason']['target_user_id']
        context.user_data.pop('awaiting_block_reason', None)
        await send_message_with_fallback(
            context.bot, user_id,
            safe_escape_markdown("✅ Ввод причины блокировки отменён."),
            update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Ввод причины блокировки отменён для target_user_id={target_user_id} от user_id={user_id}")
    else:
        await send_message_with_fallback(
            context.bot, user_id,
            safe_escape_markdown("❌ Нет активного действия для отмены."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.warning(f"Попытка отмены без активного действия для user_id={user_id}")

    return ConversationHandler.END

block_reason_handler = ConversationHandler(
    entry_points=[
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_IDS),
            handle_block_reason_input
        )
    ],
    states={
        AWAITING_BLOCK_REASON: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.User(user_id=ADMIN_IDS),
                handle_block_reason_input
            )
        ]
    },
    fallbacks=[
        CommandHandler("cancel", cancel_block_reason)
    ],
    per_user=True,
    per_chat=True
)

async def confirm_reset_avatar(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Сбрасывает все аватары пользователя."""
    user_id = update.effective_user.id
    logger.debug(f"Подтвержден сброс аватаров для target_user_id={target_user_id} администратором user_id={user_id}")

    try:
        target_user_info = await check_subscription(target_user_id)
        if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
            text = escape_md(f"❌ Пользователь ID `{target_user_id}` не найден.")
            reply_markup = await create_admin_keyboard()
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        avatars = await get_user_trainedmodels(target_user_id)
        if not avatars:
            text = escape_md(f"❌ У пользователя ID `{target_user_id}` нет аватаров для сброса.")
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]])
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        for avatar_tuple in avatars:
            if len(avatar_tuple) >= 9:
                avatar_id = avatar_tuple[0]
                await delete_trained_model(target_user_id, avatar_id)

        await update_resources(target_user_id, "set_active_avatar", amount=0)
        
        text = escape_md(f"✅ Все аватары пользователя ID `{target_user_id}` успешно сброшены.")
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]])
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Аватары пользователя user_id={target_user_id} сброшены администратором user_id={user_id}")

    except Exception as e:
        logger.error(f"Ошибка при сбросе аватаров пользователя user_id={target_user_id}: {e}", exc_info=True)
        text = escape_md(f"❌ Произошла ошибка при сбросе аватаров пользователя ID `{target_user_id}`: {str(e)}. Проверьте логи.")
        reply_markup = await create_admin_keyboard()
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

async def show_payments_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает меню выбора периода для статистики платежей."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав для доступа к этой функции."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    text = (
        "📈 Статистика платежей и регистраций\n\n"
        "Выберите период для получения статистики или введите даты вручную в формате:\n"
        "`YYYY-MM-DD` (для одного дня)\nили\n`YYYY-MM-DD YYYY-MM-DD` (для диапазона).\n\n"
        "Пример:\n`2025-05-26` или `2025-05-01 2025-05-26`"
    )

    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    last_7_days_start = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    last_30_days_start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    keyboard = [
        [InlineKeyboardButton("Сегодня", callback_data=f"payments_date_{today}_{today}")],
        [InlineKeyboardButton("Вчера", callback_data=f"payments_date_{yesterday}_{yesterday}")],
        [InlineKeyboardButton("Последние 7 дней", callback_data=f"payments_date_{last_7_days_start}_{today}")],
        [InlineKeyboardButton("Последние 30 дней", callback_data=f"payments_date_{last_30_days_start}_{today}")],
        [InlineKeyboardButton("Ввести даты вручную", callback_data="payments_manual_date")],
        [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
    ]

    await send_message_with_fallback(
        context.bot, user_id, escape_md(text), update_or_query=update,
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_payments_date(update: Update, context: ContextTypes.DEFAULT_TYPE, start_date: str, end_date: str) -> None:
    """Обрабатывает запрос статистики платежей и регистраций за указанный период."""
    user_id = update.effective_user.id
    bot = context.bot

    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав для доступа к этой функции."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        # Валидация дат
        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        logger.warning(f"Неверный формат дат от user_id={user_id}: {start_date} - {end_date}")
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ Неверный формат дат. Используйте YYYY-MM-DD, например, 2025-05-26."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        # Получаем данные о платежах и регистрациях
        payments = await get_payments_by_date(start_date, end_date)
        registrations = await get_registrations_by_date(start_date, end_date)

        # Проверяем наличие данных
        if not payments and not registrations:
            text = escape_md(f"🚫 Платежи и регистрации за период с {start_date} по {end_date} не найдены.")
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_payments")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        # Создаем файлы Excel
        payments_filename = f"payments_{start_date}_{end_date}_{uuid.uuid4().hex[:8]}.xlsx"
        payments_file_path = create_payments_excel(payments, payments_filename, start_date, end_date)

        registrations_filename = f"registrations_{start_date}_{end_date}_{uuid.uuid4().hex[:8]}.xlsx"
        registrations_file_path = create_registrations_excel(registrations, registrations_filename, start_date, end_date)

        # Проверяем создание файлов
        files_created = []
        if payments_file_path and os.path.exists(payments_file_path):
            files_created.append(('payments', payments_file_path, payments_filename))
        else:
            logger.error(f"Файл платежей {payments_file_path} не создан или не существует.")

        if registrations_file_path and os.path.exists(registrations_file_path):
            files_created.append(('registrations', registrations_file_path, registrations_filename))
        else:
            logger.error(f"Файл регистраций {registrations_file_path} не создан или не существует.")

        if not files_created:
            logger.error(f"Ни один отчет не создан для периода {start_date} - {end_date}")
            await send_message_with_fallback(
                context.bot, user_id, escape_md("❌ Ошибка создания отчетов. Проверьте логи."),
                update_or_query=update, reply_markup=await create_admin_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        # Формируем текстовое сообщение
        total_payments = len(payments)
        total_amount = sum(p[2] for p in payments if p[2]) if payments else 0.0
        total_registrations = len(registrations)
        text = (
            f"📈 Статистика за период с {start_date} по {end_date}\n\n"
            f"💰 Платежи:\n"
            f"🔢 Всего платежей: {total_payments}\n"
            f"💵 Общая сумма: {total_amount:.2f} RUB\n\n"
            f"👥 Регистрации:\n"
            f"🔢 Всего новых пользователей: {total_registrations}\n\n"
            f"📊 Excel-файлы с деталями отправлены ниже."
        )

        # Отправляем сообщение и файлы
        await send_message_with_fallback(
            context.bot, user_id, escape_md(text), update_or_query=update,
            parse_mode=ParseMode.MARKDOWN_V2
        )

        for file_type, file_path, filename in files_created:
            try:
                with open(file_path, 'rb') as f:
                    caption = f"{'Отчет по платежам' if file_type == 'payments' else 'Отчет по регистрациям'} с {start_date} по {end_date}"
                    await bot.send_document(
                        chat_id=user_id, document=f, filename=filename, caption=caption
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки файла {filename} пользователю {user_id}: {e}")

        # Удаляем временные файлы
        for _, file_path, _ in files_created:
            try:
                os.remove(file_path)
                logger.info(f"Временный файл {file_path} удален.")
            except Exception as e_remove:
                logger.error(f"Ошибка удаления файла {file_path}: {e_remove}")

        # Отправляем клавиатуру для возврата
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 К выбору периода", callback_data="admin_payments")],
            [InlineKeyboardButton("🏠 Админ-панель", callback_data="admin_panel")]
        ])
        await send_message_with_fallback(
            context.bot, user_id, escape_md("✅ Отчеты отправлены!"),
            parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Ошибка обработки статистики за {start_date} - {end_date}: {e}", exc_info=True)
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ Ошибка обработки статистики. Проверьте логи."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_manual_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Инициирует ввод дат вручную для статистики платежей."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав для доступа к этой функции."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data['awaiting_payments_date'] = True
    text = (
        "📅 Введите даты для статистики платежей в формате:\n"
        "`YYYY-MM-DD` (для одного дня)\nили\n`YYYY-MM-DD YYYY-MM-DD` (для диапазона).\n\n"
        "Пример:\n`2025-05-26` или `2025-05-01 2025-05-26`\n\n"
        "Для отмены нажмите кнопку ниже."
    )

    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="admin_payments")]])

    await send_message_with_fallback(
        context.bot, user_id, escape_md(text), update_or_query=update,
        reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )
    return AWAITING_PAYMENT_DATES

async def handle_payments_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ввод дат для статистики платежей и регистраций."""
    user_id = update.effective_user.id
    bot = context.bot
    text = update.message.text.strip()

    context.user_data['awaiting_payments_date'] = False

    try:
        dates = text.split()
        if len(dates) == 1:
            start_date = end_date = dates[0]
        elif len(dates) == 2:
            start_date, end_date = dates
        else:
            raise ValueError("Неверное количество дат.")

        await handle_payments_date(update, context, start_date, end_date)

    except ValueError as e:
        logger.warning(f"Неверный формат дат от user_id={user_id}: {text}, ошибка: {e}")
        await send_message_with_fallback(
            bot, user_id,
            escape_md("⚠️ Неверный формат дат. Используйте `YYYY-MM-DD` или `YYYY-MM-DD YYYY-MM-DD`. Пример: `2025-05-26` или `2025-05-01 2025-05-26`."),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К выбору периода", callback_data="admin_payments")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    return ConversationHandler.END

async def visualize_registrations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        logger.info(f"Попытка подключения к базе данных: {DATABASE_PATH}")
        if not os.path.exists(DATABASE_PATH):
            logger.error(f"Файл базы данных не найден: {DATABASE_PATH}")
            text = escape_md("❌ Файл базы данных не найден. Обратитесь к администратору.")
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        async with aiosqlite.connect(DATABASE_PATH) as conn:
            logger.info("Успешное подключение к базе данных")
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
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

        await context.bot.send_photo(
            chat_id=user_id,
            photo=buffer,
            caption="График регистраций"
        )

        buffer.close()

    except Exception as e:
        logger.error(f"Ошибка при визуализации регистраций: {e}", exc_info=True)
        text = escape_md("❌ Ошибка создания графика. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

async def visualize_generations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Показывает график генераций за последние 30 дней как изображение.

    Args:
        update (Update): Объект обновления от Telegram.
        context (ContextTypes.DEFAULT_TYPE): Контекст бота.

    Returns:
        None
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await send_message_with_fallback(
            context.bot, user_id, escape_md("❌ У вас нет прав."),
            update_or_query=update, reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        logger.info(f"Попытка подключения к базе данных для визуализации генераций: {DATABASE_PATH}")
        if not os.path.exists(DATABASE_PATH):
            logger.error(f"Файл базы данных не найден: {DATABASE_PATH}")
            text = escape_md("❌ Файл базы данных не найден. Обратитесь к администратору.")
            await send_message_with_fallback(
                context.bot, user_id, text, update_or_query=update,
                reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        log_entries = await get_generation_log_for_cost(start_date_str=start_date, end_date_str=end_date)

        dates = []
        generation_counts = {}
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
            context.bot, user_id, text, update_or_query=update,
            reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

        await context.bot.send_photo(
            chat_id=user_id,
            photo=buffer,
            caption="График генераций"
        )

        buffer.close()

    except Exception as e:
        logger.error(f"Ошибка при визуализации генераций: {e}", exc_info=True)
        text = escape_md("❌ Ошибка создания графика. Проверьте логи.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_activity_dates_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    
    user_id = update.effective_user.id
    if not context.user_data.get('awaiting_activity_dates'):
        logger.warning(f"handle_activity_dates_input вызвана без awaiting_activity_dates для user_id={user_id}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка: действие не ожидается. Вернитесь в админ-панель."),
            update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ConversationHandler.END

    context.user_data.pop('awaiting_activity_dates', None)
    text = update.message.text.strip()

    try:
        dates = text.split()
        if len(dates) != 2:
            raise ValueError("Требуется две даты в формате `YYYY-MM-DD YYYY-MM-DD`")
        start_date, end_date = dates

        # Валидация формата дат
        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date, '%Y-%m-%d')

        # Проверка, что start_date не позже end_date
        if start_date > end_date:
            raise ValueError("Дата начала не может быть позже даты окончания")

        logger.info(f"Обработка статистики активности для user_id={user_id} с {start_date} по {end_date}")
        await handle_activity_stats(update, context, start_date, end_date)
    except ValueError as e:
        logger.warning(f"Неверный формат дат от user_id={user_id}: {text}, ошибка: {e}")
        text = escape_md(
            f"⚠️ Неверный формат дат: {str(e)}. "
            "Используйте `YYYY-MM-DD YYYY-MM-DD`. Пример: `2025-05-01 2025-05-26`."
        )
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 К активности", callback_data="admin_activity_stats")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Ошибка обработки дат для user_id={user_id}: {e}", exc_info=True)
        text = escape_md("❌ Произошла ошибка при обработке дат. Попробуйте снова.")
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=update,
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    return ConversationHandler.END
async def generate_photo_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE, target_user_id: int) -> None:
    """Инициирует генерацию фото для указанного пользователя."""
    admin_id = update.effective_user.id
    logger.debug(f"Инициирована генерация фото для target_user_id={target_user_id} администратором user_id={admin_id}")

    # Проверяем существование пользователя
    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, admin_id, escape_md(f"❌ Пользователь ID `{target_user_id}` не найден."),
            update_or_query=update, 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Проверяем наличие активного аватара у пользователя
    active_model_data = await get_active_trainedmodel(target_user_id)
    if not active_model_data or active_model_data[3] != 'success':
        await send_message_with_fallback(
            context.bot, admin_id, 
            escape_md(f"❌ У пользователя ID `{target_user_id}` нет активного аватара."),
            update_or_query=update,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    from handlers.utils import clean_admin_context
    clean_admin_context(context)
    logger.info(f"Контекст очищен после админской генерации для user_id={target_user_id}")
    # Сохраняем данные для генерации в контекст админа
    context.user_data['admin_generation_for_user'] = target_user_id
    context.user_data['generation_type'] = 'admin_with_user_avatar'
    context.user_data['model_key'] = 'flux-trained'
    context.user_data['active_model_version'] = active_model_data[0]  # model_version
    context.user_data['active_trigger_word'] = active_model_data[1]    # trigger_word
    context.user_data['active_avatar_name'] = active_model_data[2]     # avatar_name
    context.user_data['is_admin_generation'] = True
    
    # Импортируем handle_style_selection чтобы избежать циклического импорта
    from handlers.callbacks import handle_style_selection
    
    # Переходим к выбору стиля
    await handle_style_selection(update.callback_query, context, admin_id, "select_generic_avatar_styles")


# ===== ТАКЖЕ ДОБАВЬТЕ ФУНКЦИЮ handle_admin_generation_result =====
# Добавьте после generate_photo_for_user

async def handle_admin_generation_result(context: ContextTypes.DEFAULT_TYPE, admin_id: int, target_user_id: int, result_data: dict) -> None:
    """Обрабатывает результат генерации, выполненной админом для пользователя."""
    try:
        if result_data.get('success') and result_data.get('image_urls'):
            caption = escape_md(
                f"✅ Генерация для пользователя {target_user_id} завершена!\n"
                f"👤 Аватар: {context.user_data.get('active_avatar_name', 'Неизвестно')}\n"
                f"🎨 Стиль: {result_data.get('style', 'custom')}\n"
                f"📝 Промпт: {result_data.get('prompt', 'Не указан')[:100]}..."
            )
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Еще раз", callback_data=f"admin_generate:{target_user_id}")],
                [InlineKeyboardButton("📤 Отправить пользователю", callback_data=f"admin_send_gen:{target_user_id}")],
                [InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ])
            
            # Отправляем первое изображение админу
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=result_data['image_urls'][0],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard
            )
            
            # Сохраняем результат для возможной отправки пользователю
            context.user_data[f'last_admin_generation_{target_user_id}'] = {
                'image_urls': result_data.get('image_urls'),
                'prompt': result_data.get('prompt'),
                'style': result_data.get('style')
            }
            
        else:
            error_msg = result_data.get('error', 'Неизвестная ошибка')
            await send_message_with_fallback(
                context.bot, admin_id,
                escape_md(f"❌ Ошибка генерации: {error_msg}"),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            
    except Exception as e:
        logger.error(f"Ошибка обработки результата админской генерации: {e}")
        await send_message_with_fallback(
            context.bot, admin_id,
            escape_md(f"❌ Ошибка при обработке результата: {str(e)}"),
            parse_mode=ParseMode.MARKDOWN_V2
        )