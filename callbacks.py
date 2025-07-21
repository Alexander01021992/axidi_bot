import aiosqlite
import os
import re
import logging
import asyncio
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from datetime import datetime, timedelta
from config import (
    ADMIN_IDS, TARIFFS, IMAGE_GENERATION_MODELS,
    GENERATION_STYLES, NEW_MALE_AVATAR_STYLES, NEW_FEMALE_AVATAR_STYLES,
    style_prompts, new_male_avatar_prompts, new_female_avatar_prompts
)
from database import (
    check_subscription, update_resources, add_rating,
    get_user_trainedmodels, get_active_trainedmodel,
    delete_trained_model, get_user_video_tasks, get_user_rating_and_registration,
    get_user_generation_stats, get_user_payments
)
from keyboards import (
    create_main_menu_keyboard, create_subscription_keyboard,
    create_user_profile_keyboard, create_generate_menu_keyboard,
    create_admin_keyboard, create_aspect_ratio_keyboard,
    create_avatar_selection_keyboard, create_training_keyboard,
    create_prompt_selection_keyboard, create_avatar_style_choice_keyboard,
    create_new_male_avatar_styles_keyboard, create_new_female_avatar_styles_keyboard,
    create_confirmation_keyboard, create_back_keyboard,
    create_referral_keyboard, create_admin_user_actions_keyboard
)
from generation import (
    reset_generation_context, generate_image, start_training,
    handle_generate_video, check_training_status
)
from handlers.utils import (
    safe_escape_markdown as escape_md,
    send_message_with_fallback, safe_answer_callback,
    check_resources, check_active_avatar,
    check_style_config, create_payment_link,
    get_tariff_text, send_typing_action
)
from handlers.admin import (
    show_admin_stats, show_user_actions,
    show_user_profile_admin, show_user_avatars_admin,
    show_replicate_costs, broadcast_message_admin,
    broadcast_to_paid_users, broadcast_to_non_paid_users,
    show_payments_menu, handle_payments_date, handle_manual_date_input,
    generate_photo_for_user, generate_video_for_user,
    delete_user_admin, confirm_delete_user,
    block_user_admin, confirm_block_user, is_user_blocked,
    show_activity_stats, show_referral_stats, show_visualization,
    visualize_payments, visualize_registrations, visualize_generations,
    change_balance_admin, show_user_logs, initiate_filtered_broadcast,
    search_users_admin, handle_activity_stats,
    admin_show_failed_avatars, admin_confirm_delete_all_failed, admin_execute_delete_all_failed
)

logger = logging.getLogger(__name__)

# === ОСНОВНОЙ ОБРАБОТЧИК CALLBACK ЗАПРОСОВ ===

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Основной обработчик всех callback-кнопок."""
    query = update.callback_query
    user_id = update.effective_user.id

    if not query:
        logger.warning(f"button вызван без callback_query для user_id={user_id}")
        return None

    # Проверяем блокировку пользователя
    if await is_user_blocked(user_id):
        await query.answer("🚫 Ваш аккаунт заблокирован.", show_alert=True)
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку: @AXIDI_Help"),
            update_or_query=update,
            parse_mode=ParseMode.MARKDOWN_V2
        )
        logger.info(f"Заблокированный пользователь user_id={user_id} пытался выполнить callback: {query.data}")
        return None

    await query.answer()
    callback_data = query.data
    logger.info(f"Callback от user_id={user_id}: {callback_data}")

    bot = context.bot
    context._user_id = user_id

    try:
        # === ПРИОРИТЕТНЫЕ ОБРАБОТЧИКИ ОСНОВНОГО ИНТЕРФЕЙСА ===
        
        # Возврат в главное меню
        if callback_data == "back_to_menu":
            reset_generation_context(context, "back_to_menu")
            from handlers.commands import menu
            await menu(update, context)
            return None

        # Поддержка
        elif callback_data == "support":
            await handle_support(query, context, user_id)
            return None

        # FAQ
        elif callback_data == "faq":
            await handle_faq(query, context, user_id)
            return None

        # FAQ темы
        elif callback_data.startswith("faq_"):
            topic = callback_data.replace("faq_", "")
            await handle_faq_topic(query, context, user_id, topic)
            return None

        # === ОСНОВНОЙ ФУНКЦИОНАЛ ГЕНЕРАЦИИ ===
        
        # Переход к оплате
        elif callback_data == "proceed_to_payment":
            await handle_proceed_to_payment(query, context, user_id)
            return None

        # Меню генерации
        elif callback_data == "generate_menu":
            await handle_generate_menu(query, context, user_id)
            return None

        # Генерация с аватаром
        elif callback_data == "generate_with_avatar":
            await handle_generate_with_avatar(query, context, user_id)
            return None

        # Фото по референсу
        elif callback_data == "photo_to_photo":
            await handle_photo_to_photo(query, context, user_id)
            return None

        # AI-видео
        elif callback_data in ["ai_video", "ai_video_v2"]:
            await handle_ai_video(query, context, user_id, callback_data)
            return None

        # Повтор последней генерации
        elif callback_data == "repeat_last_generation":
            try:
                # Импортируем глобальное хранилище
                from generation.images import user_last_generation_params, user_last_generation_lock
                
                async with user_last_generation_lock:
                    last_params = user_last_generation_params.get(user_id)
                
                if not last_params:
                    await send_message_with_fallback(
                        bot, user_id,
                        escape_md("❌ Нет данных для повторной генерации. Начни заново через /menu → 'Сгенерировать'."),
                        update_or_query=update,
                        reply_markup=await create_main_menu_keyboard(user_id),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    return None
                
                # Очищаем контекст и восстанавливаем данные
                reset_generation_context(context, "repeat_generation")
                context.user_data.update(last_params)
                
                # Устанавливаем model_key если его нет
                if not context.user_data.get('model_key'):
                    generation_type = context.user_data.get('generation_type')
                    if generation_type in ['with_avatar', 'photo_to_photo']:
                        context.user_data['model_key'] = 'flux-trained'
                    else:
                        context.user_data['model_key'] = 'flux-trained'
                
                logger.info(f"Повтор генерации для user_id={user_id}, данные восстановлены: {list(context.user_data.keys())}")
                logger.info(f"Повтор с параметрами: generation_type={context.user_data.get('generation_type')}, model_key={context.user_data.get('model_key')}")
                
                # Запускаем генерацию напрямую
                await generate_image(update, context, num_outputs=2)
                return None
                
            except Exception as e:
                logger.error(f"Ошибка в repeat_last_generation: {e}", exc_info=True)
                await send_message_with_fallback(
                    bot, user_id,
                    escape_md("❌ Ошибка при повторе генерации. Попробуй еще раз через /menu."),
                    update_or_query=update,
                    reply_markup=await create_main_menu_keyboard(user_id),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return None


        # === ВЫБОР СТИЛЕЙ И ПРОМПТОВ ===
        
        # Выбор категории стилей
        elif callback_data in ["select_generic_avatar_styles", "select_new_male_avatar_styles", "select_new_female_avatar_styles"]:
            await handle_style_selection(query, context, user_id, callback_data)
            return None

        # Выбор стиля
        elif callback_data.startswith("style_"):
            await handle_style_choice(query, context, user_id, callback_data)
            return None
            
        # Пагинация мужских стилей
        elif callback_data.startswith("male_styles_page_"):
            page = int(callback_data.split("_")[-1])
            await handle_male_styles_page(query, context, user_id, page)
            return None

        # Пагинация женских стилей
        elif callback_data.startswith("female_styles_page_"):
            page = int(callback_data.split("_")[-1])
            await handle_female_styles_page(query, context, user_id, page)
            return None

        # Информация о странице
        elif callback_data == "page_info":
            await safe_answer_callback(query, "Используйте кнопки навигации для перехода между страницами")
            return None

        # Ручной ввод промпта
        elif callback_data == "enter_custom_prompt_manual":
            await handle_custom_prompt_manual(query, context, user_id)
            return None

        # Ввод промпта с AI-помощником
        elif callback_data == "enter_custom_prompt_llama":
            await handle_custom_prompt_llama(query, context, user_id)
            return None

        # Подтверждение AI-промпта
        elif callback_data == "confirm_assisted_prompt":
            await handle_confirm_assisted_prompt(query, context, user_id)
            return None

        # Редактирование AI-промпта
        elif callback_data == "edit_assisted_prompt":
            await handle_edit_assisted_prompt(query, context, user_id)
            return None

        # Пропуск промпта
        elif callback_data == "skip_prompt":
           # Сохраняем важные данные
           generation_type = context.user_data.get('generation_type', 'photo_to_photo')
           model_key = context.user_data.get('model_key', 'flux-trained')
           reference_image_url = context.user_data.get('reference_image_url')
           photo_path = context.user_data.get('photo_path')
    
           context.user_data['prompt'] = "copy reference style"
    
           # Восстанавливаем важные данные
           context.user_data['generation_type'] = generation_type
           context.user_data['model_key'] = model_key
           if reference_image_url:
               context.user_data['reference_image_url'] = reference_image_url
           if photo_path:
               context.user_data['photo_path'] = photo_path
    
           logger.info(f"skip_prompt: Установлен стандартный промпт для user_id={user_id}")
           logger.info(f"  Сохранены данные: generation_type={generation_type}, model_key={model_key}")
    
           await ask_for_aspect_ratio(update, context)

        # === ВЫБОР СООТНОШЕНИЯ СТОРОН ===
        
        # Выбор соотношения сторон
        elif callback_data.startswith("aspect_"):
            await handle_aspect_ratio(query, context, user_id, callback_data)
            return None

        # Информация о соотношениях сторон
        elif callback_data == "aspect_ratio_info":
            await handle_aspect_ratio_info(query, context, user_id)
            return None

        # Возврат к выбору соотношения сторон
        elif callback_data == "back_to_aspect_selection":
            await handle_back_to_aspect_selection(query, context, user_id)
            return None

        # Возврат к выбору стиля
        elif callback_data == "back_to_style_selection":
            await handle_back_to_style_selection(query, context, user_id)
            return None

        # === ПОДТВЕРЖДЕНИЕ И ЗАПУСК ГЕНЕРАЦИИ ===
        
        # Подтверждение генерации - КРИТИЧЕСКИ ВАЖНО: НЕ СБРАСЫВАЕМ КОНТЕКСТ!
        elif callback_data == "confirm_generation":
            await handle_confirm_generation(query, context, user_id, update)
            return None

        # Подтверждение качества фото
        elif callback_data == "confirm_photo_quality":
            await handle_confirm_photo_quality(query, context, user_id, update)
            return None

        # Пропуск маски
        elif callback_data == "skip_mask":
            await handle_skip_mask(query, context, user_id)
            return None

        # === ОЦЕНКА И РЕЙТИНГ ===
        
        # Оценка результата
        elif callback_data.startswith("rate_"):
            await handle_rating(query, context, user_id, callback_data)
            return None

        # === ПРОФИЛЬ И ПОДПИСКА ===
        
        # Профиль пользователя
        elif callback_data == "user_profile":
            await handle_user_profile(query, context, user_id)
            return None

        # Проверка подписки
        elif callback_data == "check_subscription":
            await handle_check_subscription(query, context, user_id)
            return None

        # Статистика пользователя
        elif callback_data == "user_stats":
            await handle_user_stats(query, context, user_id)
            return None

        # Покупка пакета
        elif callback_data == "subscribe":
            await handle_subscribe(query, context, user_id)
            return None

        # Оплата тарифа
        elif callback_data.startswith("pay_"):
            await handle_payment(query, context, user_id, callback_data)
            return None

        # Изменение email
        elif callback_data == "change_email":
            await handle_change_email(query, context, user_id)
            return None

        # Подтверждение изменения email
        elif callback_data == "confirm_change_email":
            await handle_confirm_change_email(query, context, user_id)
            return None

        # === АВАТАРЫ ===
        
        # Мои аватары
        elif callback_data == "my_avatars":
            await handle_my_avatars(query, context, user_id)
            return None

        # Выбор аватара
        elif callback_data.startswith("select_avatar_"):
            await handle_select_avatar(query, context, user_id, callback_data)
            return None

        # Обучение аватара
        elif callback_data == "train_flux":
            await handle_train_flux(query, context, user_id)
            return None

        # Продолжение загрузки фото
        elif callback_data == "continue_upload":
            await handle_continue_upload(query, context, user_id)
            return None

        # Начало обучения
        elif callback_data == "start_training":
            await handle_start_training(query, context, user_id, update)
            return None

        # Подтверждение обучения
        elif callback_data == "confirm_start_training":
            await handle_confirm_start_training(query, context, user_id, update)
            return None

        # Возврат к вводу имени аватара
        elif callback_data == "back_to_avatar_name_input":
            await handle_back_to_avatar_name(query, context, user_id)
            return None

        # Использование триггер-слова
        elif callback_data.startswith("use_suggested_trigger_"):
            await handle_use_suggested_trigger(query, context, user_id, callback_data)
            return None

        # Проверка статуса обучения
        elif callback_data == "check_training":
            from handlers.commands import check_training
            await check_training(update, context)
            return None

        # === РЕФЕРАЛЫ ===
        
        # Рефералы
        elif callback_data == "referrals":
            await handle_referrals_menu(query, context, user_id)
            return None

        # Информация о рефералах
        elif callback_data == "referral_info":
            await handle_referral_info(query, context, user_id)
            return None

        # Копирование реферальной ссылки
        elif callback_data == "copy_referral_link":
            await handle_copy_referral_link(query, context, user_id)
            return None

        # Помощь по рефералам
        elif callback_data == "referral_help":
            await handle_referral_help(query, context, user_id)
            return None

        # Мои рефералы
        elif callback_data == "my_referrals":
            await handle_my_referrals(query, context, user_id)
            return None

        # === ИНФОРМАЦИОННЫЕ РАЗДЕЛЫ ===
        
        # История платежей
        elif callback_data == "payment_history":
            await handle_payment_history(query, context, user_id)
            return None

        # Информация о тарифах
        elif callback_data == "tariff_info":
            await handle_tariff_info(query, context, user_id)
            return None

        # Информация о категориях
        elif callback_data == "category_info":
            await handle_category_info(query, context, user_id)
            return None

        # Сравнение тарифов
        elif callback_data == "compare_tariffs":
            await handle_compare_tariffs(query, context, user_id)
            return None

        # Поддержка
        elif callback_data == "help":
            from handlers.commands import help_command
            await help_command(update, context)
            return None

        # === АДМИНСКИЕ ФУНКЦИИ ===
        
        # Админ-панель
        elif callback_data == "admin_panel" and user_id in ADMIN_IDS:
            await handle_admin_panel(query, context, user_id)
            return None

        # Статистика админа
        elif callback_data == "admin_stats" and user_id in ADMIN_IDS:
            await show_admin_stats(update, context)
            return None

        # Пагинация статистики
        elif callback_data.startswith("admin_stats_page_") and user_id in ADMIN_IDS:
            page = int(callback_data.split("_")[-1])
            await show_admin_stats(update, context, page)
            return None

        # Действия с пользователем
        elif callback_data.startswith("user_actions_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await show_user_actions(update, context, target_user_id)
            return None

        # Профиль пользователя (админ)
        elif callback_data.startswith("user_profile_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await show_user_profile_admin(update, context, target_user_id)
            return None

        # Аватары пользователя (админ)
        elif callback_data.startswith("user_avatars_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await show_user_avatars_admin(update, context, target_user_id)
            return None

        # Расходы Replicate
        elif callback_data == "admin_replicate_costs" and user_id in ADMIN_IDS:
            await show_replicate_costs(update, context)
            return None

        # Статистика платежей
        elif callback_data == "admin_payments" and user_id in ADMIN_IDS:
            await show_payments_menu(update, context)
            return None

        # Выбор периода платежей
        elif callback_data.startswith("payments_date_") and user_id in ADMIN_IDS:
            dates = callback_data.replace("payments_date_", "").split("_")
            start_date, end_date = dates[0], dates[1]
            await handle_payments_date(update, context, start_date, end_date)
            return None

        # Ввод дат вручную
        elif callback_data == "payments_manual_date" and user_id in ADMIN_IDS:
            return await handle_manual_date_input(update, context)

        # Статистика активности
        elif callback_data == "admin_activity_stats" and user_id in ADMIN_IDS:
            await show_activity_stats(update, context)
            return None

        # Статистика активности за период
        elif callback_data == "activity_30_days" and user_id in ADMIN_IDS:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            await handle_activity_stats(update, context, start_date, end_date)
            return None
        
        elif callback_data == "activity_7_days" and user_id in ADMIN_IDS:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            await handle_activity_stats(update, context, start_date, end_date)
            return None

        # Реферальная статистика
        elif callback_data == "admin_referral_stats" and user_id in ADMIN_IDS:
            await show_referral_stats(update, context)
            return None

        # Визуализация данных
        elif callback_data == "admin_visualization" and user_id in ADMIN_IDS:
            await show_visualization(update, context)
            return None

        # График платежей
        elif callback_data == "visualize_payments" and user_id in ADMIN_IDS:
            await visualize_payments(update, context)
            return None

        # График регистраций
        elif callback_data == "visualize_registrations" and user_id in ADMIN_IDS:
            await visualize_registrations(update, context)
            return None

        # График генераций
        elif callback_data == "visualize_generations" and user_id in ADMIN_IDS:
            await visualize_generations(update, context)
            return None

        # Изменение баланса
        elif callback_data.startswith("change_balance_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            return await change_balance_admin(update, context, target_user_id)

        # Логи пользователя
        elif callback_data.startswith("user_logs_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await show_user_logs(update, context, target_user_id)
            return None

        # Рассылка с фильтрами
        elif callback_data == "admin_filtered_broadcast" and user_id in ADMIN_IDS:
            return await initiate_filtered_broadcast(update, context)

        # Планировщик рассылок
        elif callback_data == "admin_scheduled_broadcast" and user_id in ADMIN_IDS:
            return await initiate_scheduled_broadcast(update, context)

        # Поиск пользователей
        elif callback_data == "admin_search_user" and user_id in ADMIN_IDS:
            return await search_users_admin(update, context)

        # Генерация фото для пользователя
        elif callback_data.startswith("generate_photo_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            from handlers.utils import clean_admin_context
            clean_admin_context(context)
            context.user_data['admin_target_user_id'] = target_user_id
            await generate_photo_for_user(update, context, target_user_id)
            return None

        # Отправка генерации пользователю
        elif callback_data.startswith("admin_send_gen:") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split(':')[1])
            generation_data = context.user_data.get(f'last_admin_generation_{target_user_id}')
            
            if generation_data and generation_data.get('image_urls'):
                try:
                    await context.bot.send_photo(
                        chat_id=target_user_id,
                        photo=generation_data['image_urls'][0],
                        caption=escape_md("🎁 Для вас создано новое изображение!"),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    await query.answer("✅ Изображение отправлено пользователю!", show_alert=True)
                except Exception as e:
                    await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            else:
                await query.answer("❌ Данные генерации не найдены", show_alert=True)
            return None
        
        # Повторная генерация админом
        elif callback_data.startswith("admin_generate:") and user_id in ADMIN_IDS:
            if ':' in callback_data:
                target_user_id = int(callback_data.split(':')[1])
                await generate_photo_for_user(update, context, target_user_id)
            return None

        # Генерация видео для пользователя
        elif callback_data.startswith("generate_video_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            context.user_data['admin_target_user_id'] = target_user_id
            await generate_video_for_user(update, context, target_user_id)
            return None

        # Удаление пользователя
        elif callback_data.startswith("delete_user_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await delete_user_admin(update, context, target_user_id)
            return None

        # Подтверждение удаления
        elif callback_data.startswith("confirm_delete_user_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await confirm_delete_user(update, context, target_user_id)
            return None

        # Блокировка/разблокировка
        elif callback_data.startswith("block_user_") and user_id in ADMIN_IDS:
            parts = callback_data.split("_")
            target_user_id = int(parts[2])
            action = parts[3]
            await block_user_admin(update, context, target_user_id, block=(action == "block"))
            return None

        # Подтверждение блокировки
        elif callback_data.startswith("confirm_block_user_"):
            parts = callback_data.split("_")
            if len(parts) < 4:
                logger.error(f"Неверный формат callback_data={callback_data}")
                await send_message_with_fallback(
                    bot, user_id,
                    safe_escape_markdown("❌ Ошибка обработки команды. Проверьте формат данных."),
                    update_or_query=update,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return None

            try:
                target_user_id = int(parts[3])
                action = parts[4]
                reason = None if action == "block_no_reason" else None
                await confirm_block_user(update, context, target_user_id, block=(action.startswith("block")), block_reason=reason)
            except ValueError:
                logger.error(f"Неверный формат target_user_id в callback_data={callback_data}")
                await send_message_with_fallback(
                    bot, user_id,
                    safe_escape_markdown("❌ Ошибка обработки команды. Проверьте формат данных."),
                    update_or_query=update,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            return None

        # Сброс аватаров
        elif callback_data.startswith("reset_avatar_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await handle_admin_reset_avatar(query, context, user_id, target_user_id)
            return None
        
        # Админские функции очистки аватаров
        elif callback_data == "admin_failed_avatars" and user_id in ADMIN_IDS:
            await admin_show_failed_avatars(update, context)
            return None
        elif callback_data == "admin_delete_all_failed" and user_id in ADMIN_IDS:
            await admin_confirm_delete_all_failed(update, context)
            return None
        elif callback_data == "admin_confirm_delete_all" and user_id in ADMIN_IDS:
            await admin_execute_delete_all_failed(update, context)
            return None

        # Рассылки
        elif callback_data in ["broadcast_all", "broadcast_paid", "broadcast_non_paid"] and user_id in ADMIN_IDS:
            return await initiate_broadcast(update, context, callback_data)

        # Отправка рассылки без текста
        elif callback_data == "send_broadcast_no_text" and user_id in ADMIN_IDS:
            broadcast_type = context.user_data.get('broadcast_type')
            media_type = context.user_data.get('admin_media_type')
            media_id = context.user_data.get('admin_media_id')

            if not broadcast_type:
                await query.answer("❌ Тип рассылки не определён.")
                await query.message.edit_text(
                    escape_md("❌ Ошибка: тип рассылки не определён."),
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=await create_admin_keyboard(user_id)
                )
                return None

            await query.answer("📢 Запускаю рассылку без текста...")
            if broadcast_type == 'broadcast_all':
                asyncio.create_task(broadcast_message_admin(context, "", user_id, media_type, media_id))
            elif broadcast_type == 'broadcast_paid':
                asyncio.create_task(broadcast_to_paid_users(context, "", user_id, media_type, media_id))
            elif broadcast_type == 'broadcast_non_paid':
                asyncio.create_task(broadcast_to_non_paid_users(context, "", user_id, media_type, media_id))

            context.user_data.pop(f'awaiting_{broadcast_type}_message', None)
            context.user_data.pop('broadcast_type', None)
            context.user_data.pop('admin_media_type', None)
            context.user_data.pop('admin_media_id', None)

            await query.message.edit_text(
                escape_md("📢 Рассылка запущена!"),
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=await create_admin_keyboard(user_id)
            )
            return None

        # Устаревшие админские действия (для совместимости)
        elif callback_data == "admin_give_subscription" and user_id in ADMIN_IDS:
            await handle_admin_give_subscription(query, context, user_id)
            return None

        elif callback_data.startswith("give_subscription_for_user_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await handle_admin_give_sub_to_user(query, context, user_id, target_user_id)
            return None

        elif callback_data.startswith("add_photos_to_user_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await handle_admin_add_resources(query, context, user_id, target_user_id, "photo", 20)
            return None

        elif callback_data.startswith("add_avatar_to_user_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await handle_admin_add_resources(query, context, user_id, target_user_id, "avatar", 1)
            return None

        elif callback_data.startswith("chat_with_user_") and user_id in ADMIN_IDS:
            target_user_id = int(callback_data.split("_")[-1])
            await handle_admin_chat_with_user(query, context, user_id, target_user_id)
            return None

        # Неизвестная команда
        else:
            logger.warning(f"Неизвестный callback_data: {callback_data} от user_id={user_id}")
            await safe_answer_callback(query, "⚠️ Неизвестная команда")
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике callback для user_id={user_id}, data={callback_data}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot,
            user_id,
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку."),
            update_or_query=update,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )

# === ОБРАБОТЧИКИ ОСНОВНОГО МЕНЮ ===

async def handle_proceed_to_payment(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработка перехода к оплате из приветствия."""
    subscription_data = await check_subscription(user_id)
    first_purchase = bool(subscription_data[5]) if subscription_data and len(subscription_data) > 5 else True

    tariff_text = get_tariff_text(first_purchase)
    subscription_kb = await create_subscription_keyboard()

    await send_message_with_fallback(
        context.bot, user_id, tariff_text, update_or_query=query,
        reply_markup=subscription_kb, parse_mode=ParseMode.MARKDOWN_V2
    )

async def delete_all_videos(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаляет все видео (меню и генерации), если они есть."""
    if 'menu_video_message_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['menu_video_message_id'])
            context.user_data.pop('menu_video_message_id', None)
        except Exception as e:
            logger.debug(f"Не удалось удалить видео меню: {e}")

    if 'generation_video_message_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=context.user_data['generation_video_message_id'])
            context.user_data.pop('generation_video_message_id', None)
        except Exception as e:
            logger.debug(f"Не удалось удалить видео генерации: {e}")

async def handle_generate_menu(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработка меню генерации."""
    await delete_all_videos(context, user_id)
    # НЕ СБРАСЫВАЕМ КОНТЕКСТ здесь - только очищаем видео

    text = (
        "✨ Выбери, что хочешь создать:\n\n"
        "📸 Фотосессия с аватаром\n"
        "Создай уникальные фото с твоим личным AI-аватаром. "
        "Выбери стиль и получи профессиональные снимки за секунды!\n\n"
        "🖼 Фото по референсу\n"
        "Загрузи любое фото и преврати его в шедевр с твоим аватаром. "
        "Идеально для воссоздания понравившихся образов!\n\n"
        "🎬 AI-видео\n"
        "Оживи статичное изображение! Превращаем фото в короткое "
        "динамичное видео с реалистичными движениями."
    )

    generation_video_path = "images/generation.mp4"
    try:
        if os.path.exists(generation_video_path):
            with open(generation_video_path, "rb") as video_file:
                video_message = await context.bot.send_video(
                    chat_id=user_id, video=video_file,
                    caption=escape_md(text),
                    reply_markup=await create_generate_menu_keyboard(),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                context.user_data['generation_video_message_id'] = video_message.message_id
        else:
            logger.warning(f"Видео генерации не найдено по пути: {generation_video_path}")
            await send_message_with_fallback(
                context.bot, user_id, escape_md(text), update_or_query=query,
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Не удалось отправить видео генерации для user_id={user_id}: {e}")
        await send_message_with_fallback(
            context.bot, user_id, escape_md(text), update_or_query=query,
            reply_markup=await create_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_generate_with_avatar(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработка генерации с аватаром."""
    await delete_all_videos(context, user_id)
    if not await check_active_avatar(context, user_id):
        return

    if not await check_resources(context, user_id, required_photos=2):
        return

    context.user_data['generation_type'] = 'with_avatar'
    context.user_data['model_key'] = "flux-trained"

    text = escape_md("👤 Выбери категорию стилей для генерации с аватаром:")

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=await create_avatar_style_choice_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_style_selection(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Обработка выбора категории стилей."""
    if callback_data == "select_generic_avatar_styles":
        context.user_data['current_style_set'] = 'generic_avatar'
        
        # Вариант 1: Убрать проверку для generic_avatar и сразу переходить к выбору
        # Переходим сразу к выбору мужского или женского стиля
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👨 Мужские стили", callback_data="select_new_male_avatar_styles")],
            [InlineKeyboardButton("👩 Женские стили", callback_data="select_new_female_avatar_styles")],
            [InlineKeyboardButton("✍️ Свой промпт", callback_data="custom_prompt_for_avatar")],
            [InlineKeyboardButton("🔙 Назад", callback_data="generate_with_avatar")]
        ])
        text = escape_md("👤 Выбери категорию стилей для генерации с аватаром:")
        
        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=query,
            reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    elif callback_data == "select_new_male_avatar_styles":
        context.user_data['current_style_set'] = 'new_male_avatar'
        context.user_data['selected_gender'] = 'man'
        if not check_style_config('new_male_avatar'):
            await send_message_with_fallback(
                context.bot, user_id,
                escape_md("❌ Ошибка конфигурации мужских стилей. Обратитесь к администратору."),
                update_or_query=query,
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        keyboard = await create_new_male_avatar_styles_keyboard(page=1)
        text = escape_md("👨 Выбери мужской стиль или введи свой промпт:")

    elif callback_data == "select_new_female_avatar_styles":
        context.user_data['current_style_set'] = 'new_female_avatar'
        context.user_data['selected_gender'] = 'woman'
        if not check_style_config('new_female_avatar'):
            await send_message_with_fallback(
                context.bot, user_id,
                escape_md("❌ Ошибка конфигурации женских стилей. Обратитесь к администратору."),
                update_or_query=query,
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        keyboard = await create_new_female_avatar_styles_keyboard(page=1)
        text = escape_md("👩 Выбери женский стиль или введи свой промпт:")

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

# Добавить новый обработчик для кастомного промпта
async def handle_custom_prompt_for_avatar(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработка перехода к вводу своего промпта для аватара."""
    context.user_data['awaiting_custom_prompt'] = True
    context.user_data['current_style_set'] = 'custom_avatar'
    
    text = escape_md("✍️ Напиши свой промпт для генерации с аватаром:")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад к стилям", callback_data="generate_with_avatar")]
    ])
    
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_style_choice(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Обработка выбора конкретного стиля."""
    logger.debug(f"handle_style_choice: callback_data = {callback_data}")

    if callback_data.startswith("style_generic_"):
        style_key = callback_data.replace("style_generic_", "")
        prompt = style_prompts.get(style_key)
        style_name = GENERATION_STYLES.get(style_key, style_key)
        logger.debug(f"Generic style: key={style_key}, name={style_name}")

    elif callback_data.startswith("style_new_male_"):
        style_key = callback_data.replace("style_new_male_", "")
        prompt = new_male_avatar_prompts.get(style_key)
        style_name = NEW_MALE_AVATAR_STYLES.get(style_key, style_key)
        logger.debug(f"Male style: key={style_key}, name={style_name}")

    elif callback_data.startswith("style_new_female_"):
        style_key = callback_data.replace("style_new_female_", "")
        prompt = new_female_avatar_prompts.get(style_key)
        style_name = NEW_FEMALE_AVATAR_STYLES.get(style_key, style_key)
        logger.debug(f"Female style: key={style_key}, name={style_name}")

    else:
        logger.error(f"Неизвестный формат callback_data для стиля: {callback_data}")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка выбора стиля. Попробуйте еще раз."),
            update_or_query=query,
            reply_markup=await create_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    if not prompt:
        logger.error(f"Промпт не найден для стиля '{style_key}'")
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md(f"❌ Промпт для стиля '{style_name}' не найден."),
            update_or_query=query,
            reply_markup=await create_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    context.user_data['prompt'] = prompt
    logger.info(f"Выбран стиль '{style_name}' для user_id={user_id}")

    await send_message_with_fallback(
        context.bot, user_id,
        escape_md(f"✅ Выбран стиль: {style_name}"),
        update_or_query=query,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await ask_for_aspect_ratio(query, context)

async def handle_male_styles_page(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int):
    """Обработка перехода на страницу мужских стилей."""
    context.user_data['current_style_set'] = 'new_male_avatar'
    context.user_data['selected_gender'] = 'man'

    keyboard = await create_new_male_avatar_styles_keyboard(page)
    text = escape_md("👨 Выбери мужской стиль или введи свой промпт:")

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_female_styles_page(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, page: int):
    """Обработка перехода на страницу женских стилей."""
    context.user_data['current_style_set'] = 'new_female_avatar'
    context.user_data['selected_gender'] = 'woman'

    keyboard = await create_new_female_avatar_styles_keyboard(page)
    text = escape_md("👩 Выбери женский стиль или введи свой промпт:")

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_photo_to_photo(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработка photo-to-photo генерации."""
    await delete_all_videos(context, user_id)
    if not await check_active_avatar(context, user_id):
        return

    if not await check_resources(context, user_id, required_photos=2):
        return

    # ВАЖНО: Используем частичную очистку вместо полной
    reset_generation_context(context, "photo_to_photo", partial=True)
    
    # Устанавливаем параметры генерации
    context.user_data['generation_type'] = 'photo_to_photo'
    context.user_data['model_key'] = "flux-trained"
    context.user_data['waiting_for_photo'] = True

    text = (
        "🖼Фото по референсу\n\n"
        "Загрузи фото-референс, которое хочешь воспроизвести с твоим аватаром. "
        "📝PixelPie Ai создаст твое фото сам!."
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="generate_menu")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )
async def handle_skip_prompt(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Пропуск ввода промпта для photo-to-photo."""
    logger.info(f"skip_prompt: Установлен стандартный промпт для user_id={user_id}")
    
    # Устанавливаем стандартный промпт
    context.user_data['prompt'] = 'copy reference style'
    
    # Логируем текущее состояние контекста для отладки
    logger.info(f"  Сохранены данные: generation_type={context.user_data.get('generation_type')}, model_key={context.user_data.get('model_key')}")
    
    await send_message_with_fallback(
        context.bot, user_id,
        escape_md("✅ Использую стандартный промпт. Выбери соотношение сторон:"),
        update_or_query=query,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await ask_for_aspect_ratio(query, context)

async def handle_skip_mask(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Пропуск загрузки маски."""
    context.user_data['mask_path'] = None
    context.user_data['waiting_for_mask'] = False

    context.user_data['prompt'] = "copy"

    await send_message_with_fallback(
        context.bot, user_id,
        escape_md("✅ Маска пропущена. Начинаю обработку изображения..."),
        update_or_query=query,
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await ask_for_aspect_ratio(query, context)

async def handle_ai_video(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Обработка генерации AI видео."""
    # TODO: Рефакторинг: рассмотреть объединение с handle_generate_video для устранения дублирования логики и текста.
    if callback_data == "ai_video_v2":
        model_key = "kwaivgi/kling-v2.0"
        required_photos = 30
        generation_type = "ai_video_v2"
    else:
        model_key = "kwaivgi/kling-v1.6-pro"
        required_photos = 20
        generation_type = "ai_video"

    if not await check_resources(context, user_id, required_photos=required_photos):
        return

    reset_generation_context(context, generation_type)
    context.user_data['generation_type'] = generation_type
    context.user_data['model_key'] = model_key
    context.user_data['video_cost'] = required_photos
    context.user_data['waiting_for_video_prompt'] = True

    model_name = IMAGE_GENERATION_MODELS.get(model_key, {}).get('name', 'AI-Видео')

    # Формируем текст, идентичный handle_generate_video
    if generation_type in ['ai_video', 'ai_video_v2']:
        text = (
            f"🎬 {(model_name)}\n\n"
            f"Для создания видео потребуется *{required_photos} фото* с твоего баланса.\n\n"
            "Для начала давай придумаем описание, что будет происходить на видео, "
            "после чего ты сможешь добавить фото, взятое за основу.\n\n"
            "📝 1. Опиши, какое движение или действие должно происходить в видео:\n\n"
            "_Например: камера медленно приближается к объекту, "
            "человек поворачивает голову и улыбается, "
            "дым плавно поднимается вверх_"
        )
    else:  # photo_to_video
        text = (
            f"🎥 {(model_name)}\n\n"
            f"Для создания видео потребуется *{required_photos} фото* с твоего баланса.\n\n"
            "Этот режим превращает твое фото в живое видео.\n\n"
            "📝 1. Опиши, какое движение должно происходить:\n\n"
            "_После описания ты сможешь загрузить фото, "
            "которое будет анимировано согласно твоему описанию_"
        )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="generate_menu")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_custom_prompt_manual(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Ручной ввод промпта."""
    context.user_data['waiting_for_custom_prompt_manual'] = True
    context.user_data['came_from_custom_prompt'] = True

    text = escape_md("✍️ Введи свой промпт (описание того, что хочешь увидеть на фото):")

    back_callback = "back_to_style_selection"
    if context.user_data.get('generation_type') == 'photo_to_photo':
        back_callback = "photo_to_photo"

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=back_callback)]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_custom_prompt_llama(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Ввод идеи для AI помощника с генерацией с аватаром."""
    if not await check_active_avatar(context, user_id):
        return

    context.user_data['waiting_for_custom_prompt_llama'] = True
    context.user_data['generation_type'] = 'with_avatar'
    context.user_data['use_llama_prompt'] = True

    text = (
        "🤖 AI-помощник поможет создать детальный промпт для генерации с твоим аватаром!\n\n"
        "Опиши свою идею простыми словами, а я превращу её в профессиональный промпт.\n\n"
        "Например: _\"деловой человек в офисе\"_ или _\"девушка на пляже на закате\"_"
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_style_selection")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_confirm_assisted_prompt(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Подтверждение промпта от AI помощника."""
    context.user_data['model_key'] = "flux-trained"
    context.user_data['generation_type'] = 'with_avatar'

    await send_message_with_fallback(
        context.bot, user_id,
        escape_md("✅ Промпт подтвержден! Выбери соотношение сторон:"),
        update_or_query=query,
        reply_markup=await create_aspect_ratio_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_edit_assisted_prompt(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Редактирование промпта от AI помощника."""
    context.user_data['waiting_for_custom_prompt_manual'] = True
    context.user_data['came_from_custom_prompt'] = True
    context.user_data.pop('user_input_for_llama', None)

    current_prompt = context.user_data.get('prompt', '')
    text = (
        f"✏️ Отредактируй промпт или введи свой:\n\n"
        f"Текущий промпт:\n`{escape_md(current_prompt)}`"
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_style_selection")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def ask_for_aspect_ratio(update, context: ContextTypes.DEFAULT_TYPE):
    """Запрос соотношения сторон."""
    user_id = update.effective_user.id if isinstance(update, Update) else update.from_user.id
    came_from_custom = context.user_data.get('came_from_custom_prompt', False)
    back_callback = "enter_custom_prompt_manual" if came_from_custom else "back_to_style_selection"

    text = escape_md("📐 Выбери соотношение сторон для изображения:")

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=update,
        reply_markup=await create_aspect_ratio_keyboard(back_callback),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_aspect_ratio(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Обработка выбора соотношения сторон."""
    aspect_ratio = callback_data.replace("aspect_", "")
    context.user_data['aspect_ratio'] = aspect_ratio

    generation_type = context.user_data.get('generation_type', 'unknown')
    prompt = context.user_data.get('prompt', 'Не указан')

    generation_type_display = {
        'with_avatar': 'Фотосессия с аватаром',
        'photo_to_photo': 'Фото по референсу',
        'ai_video': 'AI-видео (Kling 1.6)',
        'ai_video_v2': 'AI-видео (Kling 2.0)',
        'prompt_assist': 'С помощником AI'
    }.get(generation_type, generation_type)

    prompt_source = ""
    selected_gender = context.user_data.get('selected_gender')
    current_style_set = context.user_data.get('current_style_set')

    if current_style_set == 'new_male_avatar':
        prompt_source = "👨 Мужской стиль"
        for style_key, style_name in NEW_MALE_AVATAR_STYLES.items():
            if new_male_avatar_prompts.get(style_key) == prompt:
                prompt_source += f": {style_name}"
                break
    elif current_style_set == 'new_female_avatar':
        prompt_source = "👩 Женский стиль"
        for style_key, style_name in NEW_FEMALE_AVATAR_STYLES.items():
            if new_female_avatar_prompts.get(style_key) == prompt:
                prompt_source += f": {style_name}"
                break
    elif current_style_set == 'generic_avatar':
        prompt_source = "🎨 Общий стиль"
        for style_key, style_name in GENERATION_STYLES.items():
            if style_prompts.get(style_key) == prompt:
                prompt_source += f": {style_name}"
                break

    if context.user_data.get('came_from_custom_prompt'):
        if context.user_data.get('user_input_for_llama'):
            prompt_source = "🤖 Промпт от AI-помощника"
        else:
            prompt_source = "✍️ Свой промпт"

    prompt_preview = prompt[:150] + '...' if len(prompt) > 150 else prompt

    confirm_text_parts = [
        f"📋 Проверь параметры генерации:\n\n",
        f"🎨 Тип: {escape_md(generation_type_display)}\n"
    ]

    if prompt_source:
        confirm_text_parts.append(f"📝 Выбор: {escape_md(prompt_source)}\n")

    confirm_text_parts.extend([
        f"📐 Формат: {escape_md(aspect_ratio)}\n",
        f"\n💭 Промпт: _{escape_md(prompt_preview)}_\n\n",
        f"Всё верно?"
    ])

    confirm_text = "".join(confirm_text_parts)

    await send_message_with_fallback(
        context.bot, user_id, confirm_text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, генерировать!", callback_data="confirm_generation")],
            [InlineKeyboardButton("🔙 Изменить", callback_data="back_to_style_selection")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_back_to_style_selection(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Возврат к выбору стиля."""
    current_style_set = context.user_data.get('current_style_set', 'generic_avatar')

    if current_style_set == 'new_male_avatar':
        await handle_style_selection(query, context, user_id, "select_new_male_avatar_styles")
    elif current_style_set == 'new_female_avatar':
        await handle_style_selection(query, context, user_id, "select_new_female_avatar_styles")
    else:
        await handle_style_selection(query, context, user_id, "select_generic_avatar_styles")

# Замените функцию handle_confirm_generation в вашем файле callbacks.py на эту исправленную версию:

async def handle_confirm_generation(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, update: Update):
    """Обработка подтверждения генерации."""
    generation_type = context.user_data.get('generation_type')
    
    # КРИТИЧЕСКИ ВАЖНО: Исправляем тип генерации для админской генерации
    if generation_type == 'admin_with_user_avatar':
        # Для админской генерации с аватаром пользователя меняем тип на 'with_avatar'
        context.user_data['generation_type'] = 'with_avatar'
        generation_type = 'with_avatar'
        logger.info(f"Изменен тип генерации с 'admin_with_user_avatar' на 'with_avatar' для админской генерации")
    
    # КРИТИЧЕСКИ ВАЖНО: Устанавливаем model_key если его нет
    if not context.user_data.get('model_key'):
        if generation_type in ['with_avatar', 'photo_to_photo']:
            context.user_data['model_key'] = 'flux-trained'
        elif generation_type in ['ai_video', 'ai_video_v2']:
            # Получаем model_key из GENERATION_TYPE_TO_MODEL_KEY
            from config import GENERATION_TYPE_TO_MODEL_KEY
            model_key = None
            for gt, mk in GENERATION_TYPE_TO_MODEL_KEY.items():
                if generation_type == gt:
                    # Находим model_key по model_id
                    for mk_candidate, model_config in IMAGE_GENERATION_MODELS.items():
                        if model_config['id'] == mk:
                            model_key = mk_candidate
                            break
                    break
            
            if model_key:
                context.user_data['model_key'] = model_key
            else:
                # Fallback для видео
                context.user_data['model_key'] = 'kling-v1.6-pro' if generation_type == 'ai_video' else 'kling-v2.0'
        else:
            # Для других типов генерации
            context.user_data['model_key'] = 'flux-trained'
        
        logger.info(f"Установлен model_key='{context.user_data['model_key']}' для generation_type='{generation_type}'")
    
    # НОВОЕ: Специальная проверка для photo_to_photo
    if generation_type == 'photo_to_photo':
        # Проверяем наличие всех необходимых данных
        required_fields = ['reference_image_url', 'prompt', 'aspect_ratio']
        missing_fields = [f for f in required_fields if not context.user_data.get(f)]
        
        if missing_fields:
            logger.error(f"Отсутствуют поля для photo_to_photo: {missing_fields}")
            await send_message_with_fallback(
                context.bot, user_id,
                escape_md(f"❌ Ошибка: отсутствуют данные. Начните заново через меню."),
                update_or_query=query,
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        
        # Дополнительная проверка URL изображения
        reference_url = context.user_data.get('reference_image_url')
        if not reference_url or not reference_url.startswith('http'):
            logger.error(f"Некорректный reference_image_url: {reference_url}")
            await send_message_with_fallback(
                context.bot, user_id,
                escape_md("❌ Ошибка: референсное изображение не загружено. Попробуйте снова."),
                update_or_query=query,
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
    
    logger.info(f"Запуск генерации для user_id={user_id}, generation_type={generation_type}, "
                f"model_key={context.user_data.get('model_key')}")
    
    # Логируем все доступные данные для отладки
    logger.info(f"Доступные данные в контексте: {list(context.user_data.keys())}")
    logger.info(f"prompt: {context.user_data.get('prompt', 'НЕТ')}")
    logger.info(f"aspect_ratio: {context.user_data.get('aspect_ratio', 'НЕТ')}")
    logger.info(f"generation_type: {context.user_data.get('generation_type', 'НЕТ')}")
    logger.info(f"model_key: {context.user_data.get('model_key', 'НЕТ')}")
    
    # НОВОЕ: Специальное логирование для photo_to_photo
    if generation_type == 'photo_to_photo':
        logger.info(f"reference_image_url: {context.user_data.get('reference_image_url', 'НЕТ')}")
        logger.info(f"photo_path: {context.user_data.get('photo_path', 'НЕТ')}")
    
    # Используем переданный update объект для генерации
    try:
        if generation_type in ['with_avatar', 'photo_to_photo']:
            await generate_image(update, context, num_outputs=2)
        elif generation_type in ['ai_video', 'ai_video_v2']:
            await handle_generate_video(update, context)
        else:
            await send_message_with_fallback(
                context.bot, user_id,
                escape_md("❌ Неизвестный тип генерации. Попробуйте снова."),
                update_or_query=query,
                reply_markup=await create_generate_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.error(f"Неизвестный тип генерации: {generation_type}")
            return
    except Exception as e:
        logger.error(f"Ошибка в confirm_generation: {e}", exc_info=True)
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Произошла ошибка. Попробуйте еще раз."),
            update_or_query=query,
            reply_markup=await create_generate_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_rating(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Обработка оценки результата."""
    rating = int(callback_data.split('_')[1])
    generation_type = context.user_data.get('generation_type', 'unknown')
    model_key = context.user_data.get('model_key', 'unknown')

    await add_rating(user_id, generation_type, model_key, rating)

    await safe_answer_callback(query, f"Спасибо за оценку {rating} ⭐!", show_alert=True)

    await send_message_with_fallback(
        context.bot, user_id,
        escape_md(f"Спасибо за оценку {rating} ⭐! Твой отзыв поможет нам стать лучше."),
        update_or_query=query,
        reply_markup=await create_main_menu_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# === ПРОФИЛЬ И ПОДПИСКА ===

async def handle_user_profile(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Показ личного кабинета."""
    await delete_all_videos(context, user_id)
    reset_generation_context(context, "user_profile")

    subscription_data = await check_subscription(user_id)
    if not subscription_data or len(subscription_data) < 9:
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md("❌ Ошибка получения данных профиля. Попробуйте позже."),
            update_or_query=query,
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    generations_left, avatar_left = subscription_data[0], subscription_data[1]

    text = (
        f"👤 Личный кабинет\n\n"
        f"💰 Баланс: {generations_left} фото, {avatar_left} аватар{'ов' if avatar_left != 1 else ''}"
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=await create_user_profile_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_check_subscription(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Проверка подписки."""
    subscription_data = await check_subscription(user_id)
    if not subscription_data or len(subscription_data) < 9:
        await safe_answer_callback(query, "❌ Ошибка получения данных", show_alert=True)
        return

    generations_left, avatar_left, _, username, _, _, email, _, _, _ = subscription_data

    email_text = f"\n📧 Email: {email}" if email else ""

    text = (
        f"💳 Твоя подписка:\n\n"
        f"📸 Фото на балансе: {generations_left}\n"
        f"👤 Аватары на балансе: {avatar_left}"
        f"{email_text}\n\n"
        f"_Фото тратятся на генерацию изображений и видео._\n"
        f"_Аватары нужны для создания персональных моделей._"
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Пополнить баланс", callback_data="subscribe")],
            [InlineKeyboardButton("🔙 В личный кабинет", callback_data="user_profile")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_user_stats(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Показ статистики пользователя."""
    logger.debug(f"handle_user_stats: user_id={user_id}")

    try:
        gen_stats = await get_user_generation_stats(user_id)
        logger.debug(f"gen_stats для user_id={user_id}: {gen_stats}")
    except Exception as e:
        logger.error(f"Ошибка получения gen_stats для user_id={user_id}: {e}", exc_info=True)
        gen_stats = {}

    try:
        payments = await get_user_payments(user_id)
        total_spent = sum(p[2] for p in payments if p[2] is not None)
        logger.debug(f"payments для user_id={user_id}: {len(payments)} платежей, total_spent={total_spent}")
    except Exception as e:
        logger.error(f"Ошибка получения payments для user_id={user_id}: {e}", exc_info=True)
        payments = []
        total_spent = 0.0

    try:
        async with aiosqlite.connect('users.db') as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("SELECT referred_id, status, completed_at FROM referrals WHERE referrer_id = ?", (user_id,))
            my_referrals = await c.fetchall()
        logger.debug(f"Найдено {len(my_referrals)} рефералов для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка получения рефералов для user_id={user_id}: {e}", exc_info=True)
        my_referrals = []

    active_referrals = 0
    total_bonuses = 0

    for ref in my_referrals:
        ref_user_id = ref['referred_id']
        ref_status = ref['status']
        ref_data = await check_subscription(ref_user_id)
        has_purchased = ref_status == 'completed' or (ref_data and len(ref_data) > 5 and not bool(ref_data[5]))
        if has_purchased:
            active_referrals += 1
            total_bonuses += 5

    stats_text = escape_md("📊 Твоя статистика:\n\n")

    if gen_stats:
        stats_text += escape_md("Генерации:\n")
        type_names = {
            'with_avatar': 'Фото с аватаром',
            'photo_to_photo': 'Фото по референсу',
            'ai_video': 'AI-видео (1.6)',
            'ai_video_v2': 'AI-видео (2.0)',
            'train_flux': 'Обучение аватаров',
            'prompt_assist': 'Помощь с промптами'
        }
        for gen_type, count in gen_stats.items():
            type_name = type_names.get(gen_type, gen_type)
            stats_text += escape_md(f"  • {type_name}: {count}\n")
    else:
        stats_text += escape_md("_Ты еще ничего не генерировал_\n")

    stats_text += escape_md(f"\n💵 Всего потрачено: {total_spent:.2f} RUB\n")
    stats_text += escape_md(f"💳 Всего покупок: {len(payments)}\n")
    stats_text += escape_md(f"👥 Рефералов (с покупкой): {active_referrals}\n")
    stats_text += escape_md(f"🎁 Бонусных фото за рефералов: {total_bonuses}\n")

    bot_username = context.bot.username
    stats_text += escape_md(f"\n🔗 Твоя реферальная ссылка:\n`t.me/{bot_username}?start=ref_{user_id}`")

    await send_message_with_fallback(
        context.bot, user_id, stats_text, update_or_query=query,
        reply_markup=await create_referral_keyboard(user_id, bot_username),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_subscribe(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Показ тарифов."""
    await delete_all_videos(context, user_id)
    subscription_data = await check_subscription(user_id)
    first_purchase = bool(subscription_data[5]) if subscription_data and len(subscription_data) > 5 else True

    # Новый текст пакетов с обновленными тарифами
    text = (
        "🔥 Горячий выбор для идеальных фото\\!\n\n"
        "Хочешь крутые кадры без лишних хлопот\\? "
        "Выбирай выгодный пакет и получай фото в один клик\\!\n\n"
        "💎 **НАШИ ПАКЕТЫ:**\n"
        "📸 399₽ за 10 фото\n"
        "📸 599₽ за 30 фото\n"
        "📸 1199₽ за 70 фото\n"
        "📸 3119₽ за 170 фото \\+ 1 аватар\n"
        "📸 4599₽ за 250 фото \\+ 1 аватар\n"
        "👤 590₽ за 1 аватар\n\n"
    )
    
    if first_purchase:
        text += "🎁 **При первой покупке к любому купленному тарифу впервые 1 Аватар в подарок\\!**\n\n"
    
    text += "Выбирай свой пакет и начинай творить\\! 🚀"
    
    # Создаем кнопки тарифов с новыми ценами
    keyboard = [
        [InlineKeyboardButton("📸 10 фото - 399₽", callback_data="pay_399")],
        [InlineKeyboardButton("📸 30 фото - 599₽", callback_data="pay_599")],
        [InlineKeyboardButton("📸 70 фото - 1199₽", callback_data="pay_1199")],
        [InlineKeyboardButton("📸 170 фото + аватар - 3119₽", callback_data="pay_3119")],
        [InlineKeyboardButton("📸 250 фото + аватар - 4599₽", callback_data="pay_4599")],
        [InlineKeyboardButton("👤 1 аватар - 590₽", callback_data="pay_590")],
        [InlineKeyboardButton("ℹ️ Информация о тарифах", callback_data="tariff_info")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")]
    ]

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_payment(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Обработка выбора тарифа для оплаты."""
    amount_str = callback_data.replace("pay_", "")

    tariff_key = None
    for key, details in TARIFFS.items():
        if str(int(details["amount"])) == amount_str:
            tariff_key = key
            break

    if not tariff_key:
        await safe_answer_callback(query, "❌ Неверный тариф", show_alert=True)
        return

    tariff = TARIFFS[tariff_key]
    amount = tariff["amount"]
    description = tariff["display"]

    context.user_data['payment_amount'] = amount
    context.user_data['payment_description'] = description
    context.user_data['payment_tariff_key'] = tariff_key

    subscription_data = await check_subscription(user_id)
    email = subscription_data[6] if subscription_data and len(subscription_data) > 6 else None

    if email:
        context.user_data['email'] = email
        try:
            bot_username = context.bot.username
            payment_url = await create_payment_link(user_id, email, amount, description, bot_username)

            is_first_purchase = bool(subscription_data[5]) if subscription_data and len(subscription_data) > 5 else True
            bonus_text = " (+ 1 аватар в подарок!)" if is_first_purchase and tariff.get("photos", 0) > 0 else ""

            payment_text = (
                f"💳 Оплата пакета\n"
                f"✨ Вы выбрали: {escape_md(description)}{escape_md(bonus_text)}\n"
                f"💰 Сумма: {amount:.2f} RUB\n\n"
                f"🔗 [Нажмите здесь для безопасной оплаты через YooKassa]({payment_url})\n\n"
                f"_После успешной оплаты ресурсы будут начислены автоматически._"
            )

            await send_message_with_fallback(
                context.bot, user_id, payment_text, update_or_query=query,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к пакетам", callback_data="subscribe")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Ошибка создания платежной ссылки: {e}")
            await send_message_with_fallback(
                context.bot, user_id,
                escape_md(f"❌ Ошибка создания платежной ссылки: {str(e)}"),
                update_or_query=query,
                reply_markup=await create_subscription_keyboard(),
                parse_mode=ParseMode.MARKDOWN_V2
            )
    else:
        context.user_data['awaiting_email'] = True
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md(f"📧 Для оформления покупки \"{description}\" ({amount:.2f} RUB) введите ваш email:"),
            update_or_query=query,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к пакетам", callback_data="subscribe")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_change_email(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Изменение email."""
    subscription_data = await check_subscription(user_id)
    current_email = subscription_data[6] if subscription_data and len(subscription_data) > 6 else None
    
    if current_email:
        text = (
            f"📧 Изменение email\n\n"
            f"Текущий email: `{current_email}`\n\n"
            f"Введите новый email адрес:"
        )
    else:
        text = (
            f"📧 Установка email\n\n"
            f"У вас еще не указан email.\n"
            f"Введите email адрес:"
        )
    
    context.user_data['awaiting_email_change'] = True
    
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В профиль", callback_data="user_profile")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_confirm_change_email(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Подтверждение изменения email."""
    new_email = context.user_data.get('new_email_to_confirm')
    
    if not new_email:
        await safe_answer_callback(query, "❌ Ошибка: email не найден", show_alert=True)
        return
    
    await update_resources(user_id, "update_email", email=new_email)
    context.user_data.pop('new_email_to_confirm', None)
    
    await safe_answer_callback(query, "✅ Email успешно изменен!", show_alert=True)
    
    await send_message_with_fallback(
        context.bot, user_id,
        escape_md(f"✅ Email успешно изменен на: {new_email}"),
        update_or_query=query,
        reply_markup=await create_user_profile_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# === АВАТАРЫ ===

async def handle_my_avatars(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Показ аватаров пользователя."""
    await delete_all_videos(context, user_id)
    reset_generation_context(context, "my_avatars")

    text = escape_md("👥 Мои аватары\n\nЗдесь ты можешь выбрать активный аватар или создать новый.")

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=await create_avatar_selection_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_select_avatar(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Выбор активного аватара."""
    avatar_id = int(callback_data.split('_')[2])

    await update_resources(user_id, "set_active_avatar", amount=avatar_id)

    await safe_answer_callback(query, "✅ Аватар активирован!", show_alert=True)

    await handle_my_avatars(query, context, user_id)

async def handle_train_flux(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Начало обучения нового аватара."""
    if not await check_resources(context, user_id, required_avatars=1):
        return

    reset_generation_context(context, "train_flux")
    context.user_data['training_step'] = 'upload_photos'
    context.user_data['training_photos'] = []

    text = (
    "🎨 СОЗДАНИЕ ВАШЕГО-АВАТАРА\n\n"
    "Для создания высококачественного аватара мне нужно минимум 10 твоих фотографий (оптимально 15-20) с АКЦЕНТОМ на лицо. "
    "Каждая фотография должна быть четкой и профессиональной, чтобы PixelPie точно воспроизвел ваши черты.\n\n"
    "📸 РЕКОМЕНДАЦИИ ДЛЯ ИДЕАЛЬНОГО РЕЗУЛЬТАТА:\n"
    "• ФОТОГРАФИИ ДОЛЖНЫ БЫТЬ ПРЯМЫМИ, ЧЕТКИМИ, БЕЗ ИСКАЖЕНИЙ И РАЗМЫТИЯ. Используй камеру с высоким разрешением.\n"
    "• Снимай в праильных ракурсах:Лицо должно быть полностью видно, без обрезки.\n"
    "• Используй разнообразное освещение: дневной свет, золотой час, мягкий студийный свет. ИЗБЕГАЙ ТЕМНЫХ ТЕНЕЙ И ПЕРЕСВЕТОВ.\n"
    "• Фон должен быть чистым, без лишних объектов (мебель, растения, животные). НЕ ДОПУСКАЮТСЯ ЗЕРКАЛА И ОТРАЖЕНИЯ.\n"
    "• Снимай только себя. ГРУППОВЫЕ ФОТО ИЛИ ФОТО С ДРУГИМИ ЛЮДЬМИ НЕ ПОДХОДЯТ.\n"
    "• НЕ ИСПОЛЬЗУЙ ОЧКИ, ШЛЯПЫ, МАСКИ ИЛИ ДРУГИЕ АКСЕССУАРЫ, закрывающие лицо. Макияж должен быть минимальным.\n"
    "• Выражение лица: нейтральное или легкая улыбка. ИЗБЕГАЙ КРИВЛЯНИЙ ИЛИ ЭКСТРЕМАЛЬНЫХ ЭМОЦИЙ.\n"
    "• Чем больше разнообразных фотографий (ракурсы, освещение, фон), тем точнее будет аватар.\n\n"
    "⚠️ ВАЖНО: Каждая фотография должна быть хорошего качества, без фильтров, цифрового шума или артефактов. "
    "Фотографии с низким разрешением, искажениями или посторонними объектами будут влиять на КАЧЕСТВО АВТАРА.\n\n"
    "📤 Начинай загружать фотографии! Я проверю и сообщу, когда будет достаточно."
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="user_profile")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_continue_upload(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Продолжение загрузки фото."""
    photo_count = len(context.user_data.get('training_photos', []))

    text = escape_md(f"📸 Загружено {photo_count} фото. Продолжай загружать или нажми \"Начать обучение\".")

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=await create_training_keyboard(user_id, photo_count),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_start_training(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, update: Update):
    """Переход к вводу имени аватара."""
    photo_count = len(context.user_data.get('training_photos', []))

    if photo_count < 10:
        await safe_answer_callback(query, f"Нужно минимум 10 фото! Сейчас {photo_count}.", show_alert=True)
        return

    context.user_data['training_step'] = 'enter_avatar_name'

    text = (
        f"✅ Отлично! Загружено {photo_count} фото.\n\n"
        f"🏷 Теперь Придумайте Имя или Название для Вашего Аватара"
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к загрузке фото", callback_data="continue_upload")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_confirm_start_training(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, update: Update):
    """Запуск обучения аватара."""
    await start_training(update, context)

async def handle_back_to_avatar_name(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Возврат к вводу имени аватара."""
    context.user_data['training_step'] = 'enter_avatar_name'

    photo_count = len(context.user_data.get('training_photos', []))
    text = (
        f"🏷 Придумай имя для своего аватара (например: \"Мой стиль\", \"Бизнес-образ\").\n"
        f"У тебя загружено {photo_count} фото."
    )

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к загрузке фото", callback_data="continue_upload")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_use_suggested_trigger(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, callback_data: str):
    """Использование предложенного триггер-слова."""
    trigger_word = callback_data.replace("use_suggested_trigger_", "")
    context.user_data['trigger_word'] = trigger_word
    context.user_data['training_step'] = 'confirm_training'

    from handlers.messages import handle_trigger_word_input
    await handle_trigger_word_input(query, context, trigger_word)

async def handle_confirm_photo_quality(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, update: Update):
    """Подтверждение качества фото перед обучением."""
    avatar_name = context.user_data.get('avatar_name', 'Без имени')
    photo_count = len(context.user_data.get('training_photos', []))

    final_confirm_text = (
        f"👍 Отлично\\! Давай проверим финальные данные:\n\n"
        f"👤 Имя аватара: {escape_md(avatar_name)}\n"
        f"📸 Загружено фото: {photo_count} шт\\.\n\n"
        f"🚀 Все готово для запуска обучения\\!\n"
        f"⏱️ Это займет около 3\\-5 минут\\.\n"
        f"💎 Будет списан 1 аватар с твоего баланса\\.\n\n"
        f"Начинаем?"
    )

    await send_message_with_fallback(
        context.bot, user_id, final_confirm_text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Начать обучение!", callback_data="confirm_start_training")],
            [InlineKeyboardButton("✏️ Изменить данные", callback_data="train_flux")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# === РЕФЕРАЛЫ ===

async def handle_referrals_menu(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Меню реферальной программы."""
    try:
        # Получаем количество рефералов
        async with aiosqlite.connect('users.db') as conn:
            cursor = await conn.cursor()
            
            # Всего рефералов
            await cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
            total_referrals = (await cursor.fetchone())[0]
            
            # Оплативших рефералов
            await cursor.execute("""
                SELECT COUNT(*) FROM users 
                WHERE referrer_id = ? AND first_purchase IS NOT NULL
            """, (user_id,))
            paid_referrals = (await cursor.fetchone())[0]
            
            # Заработано бонусов
            bonus_photos = paid_referrals * 5  # 5 фото за каждого оплатившего
    
    except Exception as e:
        logger.error(f"Ошибка получения данных рефералов для user_id={user_id}: {e}")
        total_referrals = 0
        paid_referrals = 0
        bonus_photos = 0
    
    bot_username = context.bot.username or "bot"
    referral_link = f"t.me/{bot_username}?start=ref_{user_id}"
    
    text = (
        f"👥 Реферальная программа\n\n"
        f"📊 Ваша статистика:\n"
        f"• Приглашено друзей: {total_referrals}\n"
        f"• Совершили покупку: {paid_referrals}\n"
        f"• Получено бонусов: {bonus_photos} фото\n\n"
        f"🎁 За каждого друга, который совершит покупку, вы получите 5 бесплатных фото!\n\n"
        f"🔗 Ваша реферальная ссылка:\n"
        f"`{referral_link}`"
    )
    
    keyboard = await create_referral_keyboard(user_id, bot_username)
    
    await send_message_with_fallback(
        context.bot, user_id, escape_md(text), update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_referral_info(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Информация о реферальной программе."""
    text = (
        "🎁 Как работает реферальная программа:\n\n"
        "1\\. Поделитесь своей ссылкой с друзьями\n"
        "2\\. Друг регистрируется по вашей ссылке\n"
        "3\\. Когда друг делает первую покупку \\- вы получаете 5 фото\n"
        "4\\. Друг получает 1 бонусное фото\n\n"
        "💡 Советы:\n"
        "• Расскажите друзьям о возможностях бота\n"
        "• Покажите примеры своих генераций\n"
        "• Поделитесь ссылкой в соцсетях\n\n"
        "🚀 Приглашайте больше друзей \\- получайте больше бонусов\\!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Поделиться", callback_data="copy_referral_link")],
        [InlineKeyboardButton("🔙 К рефералам", callback_data="referrals")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_copy_referral_link(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Копирование реферальной ссылки."""
    bot_username = context.bot.username or "bot"
    referral_link = f"t.me/{bot_username}?start=ref_{user_id}"

    text = (
        f"🔗 Ваша реферальная ссылка:\n\n"
        f"`{referral_link}`\n\n"
        f"📋 Скопируйте и поделитесь с друзьями\\!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Поделиться в Telegram", 
         url=f"https://t.me/share/url?url={referral_link}&text=Попробуй крутой AI-бот! 🤖")],
        [InlineKeyboardButton("🔙 К рефералам", callback_data="referrals")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

    await safe_answer_callback(query, "📋 Ссылка готова к копированию!", show_alert=True)

async def handle_referral_help(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Помощь по рефералам."""
    text = (
        "❓ Помощь по рефералам\n\n"
        "🔗 Как пригласить друга:\n"
        "1\\. Скопируйте свою реферальную ссылку\n"
        "2\\. Отправьте её другу\n"
        "3\\. Друг должен перейти по ссылке и запустить бота\n"
        "4\\. После первой покупки друга вы получите бонус\n\n"
        "❓ Частые вопросы:\n"
        "• Сколько можно пригласить? Без ограничений\\!\n"
        "• Когда начисляется бонус? Сразу после покупки\n"
        "• Сгорают ли бонусы? Нет, остаются навсегда\n\n"
        "💬 Если остались вопросы \\- обратитесь в поддержку\\!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Поддержка", callback_data="support")],
        [InlineKeyboardButton("🔙 К рефералам", callback_data="referrals")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_my_referrals(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Показ рефералов пользователя и бонусов."""
    logger.debug(f"handle_my_referrals: user_id={user_id}")

    try:
        async with aiosqlite.connect('users.db') as conn:
            conn.row_factory = aiosqlite.Row
            c = await conn.cursor()
            await c.execute("SELECT referred_id, status, created_at, completed_at FROM referrals WHERE referrer_id = ?", (user_id,))
            my_referrals = await c.fetchall()
        logger.debug(f"Найдено {len(my_referrals)} рефералов для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка получения рефералов для user_id={user_id}: {e}", exc_info=True)
        my_referrals = []

    text = f"👥 Твои рефералы:\n\n"

    total_bonuses = 0
    active_referrals = 0

    if my_referrals:
        text += f"Всего приглашено: {len(my_referrals)} человек\n\n"

        for ref in my_referrals[-10:]:
            ref_user_id = ref['referred_id']
            ref_date = ref['created_at']
            ref_status = ref['status']
            completed_at = ref['completed_at']

            ref_data = await check_subscription(ref_user_id)
            has_purchased = ref_status == 'completed' or (ref_data and len(ref_data) > 5 and not bool(ref_data[5]))
            status = "💳 Совершил покупку" if has_purchased else "⏳ Без покупок"

            if has_purchased:
                active_referrals += 1
                total_bonuses += 5

            text += f"• ID {ref_user_id} - {ref_date} ({escape_md(status)})\n"
            if completed_at and has_purchased:
                text += f"  Завершено: {completed_at}\n"
    else:
        text += "_Ты еще никого не пригласил_\n"
        logger.info(f"Нет рефералов для user_id={user_id}")

    text += f"\n📊 Статистика бонусов:\n"
    text += f"👥 Активных рефералов (с покупкой): {active_referrals}\n"
    text += f"🎁 Получено бонусных фото: {total_bonuses}\n"

    bot_username = context.bot.username
    text += f"\n🔗 Твоя реферальная ссылка:\n`t.me/{bot_username}?start=ref_{user_id}`\n\n"
    text += "_За каждого друга, который совершит первую покупку, ты получишь +5 фото!_"

    await send_message_with_fallback(
        context.bot, user_id, escape_md(text), update_or_query=query,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Поделиться ссылкой",
                url=f"https://t.me/share/url?url=t.me/{bot_username}?start=ref_{user_id}&text=Присоединяйся к PixelPie!")],
            [InlineKeyboardButton("🔙 В статистику", callback_data="user_stats")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# === ИНФОРМАЦИОННЫЕ РАЗДЕЛЫ ===

async def handle_payment_history(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """История платежей."""
    try:
        payments = await get_user_payments(user_id, limit=10)

        if not payments:
            text = (
                "💳 История платежей\n\n"
                "У вас пока нет платежей\\.\n"
                "Оформите первый пакет\\!"
            )
        else:
            text = "💳 История платежей\n\n"

            for payment in payments:
                payment_id, amount, payment_type, created_at = payment[:4]

                date_str = created_at.strftime("%d.%m.%Y") if created_at else "Неизвестно"
                amount_str = f"{amount:.0f}₽" if amount > 0 else f"{amount:.0f}₽"

                text += f"📅 {date_str} • {amount_str}\n"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Пополнить", callback_data="subscribe")],
            [InlineKeyboardButton("🔙 В профиль", callback_data="user_profile")]
        ])

        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=query,
            reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка получения истории платежей: {e}")
        await safe_answer_callback(query, "❌ Ошибка получения истории", show_alert=True)

async def handle_tariff_info(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Информация о тарифах."""
    text = (
        "💎 Информация о тарифах\n\n"
        "📸 Фото \\- ресурс для генерации изображений\n"
        "👤 Аватары \\- ресурс для создания персональных моделей\n\n"
        "🔄 Как это работает:\n"
        "1\\. Покупаете пакет фото\n"
        "2\\. Создаете аватар \\(тратится 1 аватар или 590₽\\)\n"
        "3\\. Генерируете фото с аватаром \\(тратится фото\\)\n\n"
        "💰 Наши цены:\n"
        "📸 От 399₽ за 10 фото \\(стартовый\\)\n"
        "📸 До 4599₽ за 250 фото \\+ аватар \\(максимум\\)\n"
        "👤 Отдельный аватар \\- 590₽\n\n"
        "🎁 При первой покупке \\- аватар в подарок\\!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Выбрать пакет", callback_data="subscribe")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_category_info(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Информация о категориях."""
    text = (
        "📋 Категории контента\n\n"
        "🎨 Фотосессия \\- создание фото с вашим аватаром\n"
        "🖼 Фото по референсу \\- генерация по загруженному изображению\n"
        "🎬 AI\\-видео \\- создание видеороликов\n\n"
        "ℹ️ Для фотосессии нужен обученный аватар\\.\n"
        "ℹ️ Для остальных функций аватар не требуется\\."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Попробовать", callback_data="generate_menu")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_compare_tariffs(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Сравнение тарифов."""
    text = (
        "💎 Сравнение тарифов\n\n"
        "📸 10 фото \\- 399₽ \\(39\\.9₽ за фото\\)\n"
        "📸 30 фото \\- 599₽ \\(20₽ за фото\\)\n"
        "📸 70 фото \\- 1199₽ \\(17\\.1₽ за фото\\)\n"
        "📸 170 фото \\+ аватар \\- 3119₽ \\(18\\.3₽ за фото\\)\n"
        "📸 250 фото \\+ аватар \\- 4599₽ \\(18\\.4₽ за фото\\)\n"
        "👤 1 аватар \\- 590₽\n\n"
        "💡 Самый выгодный: 70 фото за 1199₽\\!\n"
        "🎁 Больше всего контента: 250 фото \\+ аватар\\!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Выбрать пакет", callback_data="subscribe")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_aspect_ratio_info(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Информация о соотношениях сторон."""
    text = (
        "📐 Соотношения сторон\n\n"
        "📱 Квадратные: идеально для соцсетей\n"
        "🖥️ Горизонтальные: для широких кадров\n"
        "📲 Вертикальные: для портретов и Stories\n\n"
        "💡 Выберите подходящий формат в зависимости от того, где планируете использовать изображение\\."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 К выбору формата", callback_data="back_to_aspect_selection")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_back_to_aspect_selection(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Возврат к выбору соотношения сторон."""
    await ask_for_aspect_ratio(query, context)

async def handle_support(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Поддержка."""
    text = (
        "💬 Поддержка\n\n"
        "Если у вас возникли вопросы или проблемы:\n\n"
        "📞 Напишите в поддержку\n"
        "❓ Изучите частые вопросы\n"
        "📖 Прочитайте инструкции\n\n"
        "🤝 Мы поможем решить любую проблему\\!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Написать в поддержку", url="https://t.me/AXIDI_Help")],
        [InlineKeyboardButton("❓ Частые вопросы", callback_data="faq")],
        [InlineKeyboardButton("📖 Руководство", callback_data="user_guide")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_faq(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Частые вопросы."""
    text = (
        "❓ Частые вопросы\n\n"
        "Выберите интересующую вас тему:"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Как создать фото?", callback_data="faq_photo")],
        [InlineKeyboardButton("🎬 Как создать видео?", callback_data="faq_video")],
        [InlineKeyboardButton("👤 Как создать аватар?", callback_data="faq_avatar")],
        [InlineKeyboardButton("💡 Советы по промптам", callback_data="faq_prompts")],
        [InlineKeyboardButton("❓ Частые проблемы", callback_data="faq_problems")],
        [InlineKeyboardButton("💎 О подписке", callback_data="faq_subscription")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_menu")]
    ])

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_faq_topic(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, topic: str):
    """Обработчик конкретной темы FAQ."""
    
    faq_texts = {
        "photo": {
            "title": "📸 Как создать фото?",
            "text": (
                "📸 Создание фото\n\n"
                "1️⃣ Нажмите кнопку 'Сгенерировать'\n"
                "2️⃣ Выберите способ генерации:\n"
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
                "• Нереалистичных ожиданий"
            )
        },
        "problems": {
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
        await safe_answer_callback(query, "❌ Тема не найдена")
        return
    
    info = faq_texts[topic]
    escaped_text = escape_md(info["text"])
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❓ Другие вопросы", callback_data="faq")],
        [InlineKeyboardButton("💬 Поддержка", callback_data="support")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
    ])
    
    await send_message_with_fallback(
        context.bot, user_id, escaped_text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

# === АДМИНСКИЕ ФУНКЦИИ ===

async def handle_admin_panel(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработчик админ панели."""
    if user_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return
    
    text = (
        "👨‍💼 *Панель администратора*\n\n"
        "Выберите действие:"
    )
    
    keyboard = await create_admin_keyboard(user_id)
    
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_admin_give_subscription(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Выдача подписки - ввод ID пользователя."""
    context.user_data['giving_sub_to_user'] = True

    text = (
        "💎 Выдача пакета пользователю\n\n"
        "Введите ID пользователя и ключ тарифа через пробел.\n\n"
        "Формат: `ID_пользователя ключ_тарифа`\n\n"
        "Доступные ключи тарифов:\n"
    )

    for key, details in TARIFFS.items():
        if key != "admin_premium":
            text += f"• `{key}` - {escape_md(details['display'])}\n"

    text += "\nПример: `123456789 премиум`"

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В админ-панель", callback_data="admin_panel")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_admin_give_sub_to_user(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, target_user_id: int):
    """Выдача подписки конкретному пользователю."""
    context.user_data['giving_sub_to_user'] = target_user_id

    text = (
        f"💎 Выдача пакета пользователю ID {target_user_id}\n\n"
        f"Введите ключ тарифа:\n"
    )

    for key, details in TARIFFS.items():
        if key != "admin_premium":
            text += f"• `{key}` - {escape_md(details['display'])}\n"

    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_admin_add_resources(query, context: ContextTypes.DEFAULT_TYPE, user_id: int,
                                   target_user_id: int, resource_type: str, amount: int):
    """Добавление ресурсов (фото или аватары) для указанного пользователя."""
    logger.debug(f"Добавление {amount} {resource_type} для target_user_id={target_user_id} администратором user_id={user_id}")

    await send_typing_action(context.bot, user_id)

    target_user_info = await check_subscription(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md(f"❌ Пользователь ID `{target_user_id}` не найден."),
            update_or_query=query,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    action = "increment_photo" if resource_type == "photo" else "increment_avatar"
    resource_name = "фото" if resource_type == "photo" else "аватар"

    try:
        success = await update_resources(target_user_id, action, amount=amount)
        logger.debug(f"update_resources для user_id={target_user_id}, action={action}, amount={amount}, результат={success}")

        if not success:
            raise Exception("Не удалось обновить ресурсы в базе данных")

        text = escape_md(f"✅ Добавлено {amount} {resource_name} пользователю ID `{target_user_id}`.")

        await send_message_with_fallback(
            context.bot, user_id, text, update_or_query=query,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=escape_md(f"🎉 Администратор добавил вам {amount} {resource_name}!"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {target_user_id}: {e}")

        logger.info(f"Успешно добавлено {amount} {resource_type} для user_id={target_user_id}")

    except Exception as e:
        logger.error(f"Ошибка добавления {resource_type} для user_id={target_user_id}: {e}", exc_info=True)
        await send_message_with_fallback(
            context.bot, user_id,
            escape_md(f"❌ Ошибка при добавлении {resource_name} для пользователя ID `{target_user_id}`: {str(e)}"),
            update_or_query=query,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def handle_admin_chat_with_user(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, target_user_id: int) -> None:
    """Отправка сообщения пользователю."""
    context.user_data['awaiting_chat_message'] = target_user_id
    
    text = escape_md(f"💬 Отправка сообщения пользователю ID {target_user_id}\n\nВведите текст сообщения:")
    
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data=f"user_actions_{target_user_id}")]]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_admin_reset_avatar(query, context: ContextTypes.DEFAULT_TYPE, user_id: int, target_user_id: int) -> None:
    """Сброс аватаров пользователя."""
    confirm_text = (
        f"⚠️ Вы уверены?\n\n"
        f"Это действие удалит ВСЕ аватары пользователя ID {target_user_id} и сбросит активный аватар.\n\n"
        f"Это действие необратимо!"
    )
    
    await send_message_with_fallback(
        context.bot, user_id, escape_md(confirm_text), update_or_query=query,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Да, удалить все", callback_data=f"reset_avatar_{target_user_id}")],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"user_actions_{target_user_id}")]
        ]),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# === РАССЫЛКИ ===

async def initiate_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, broadcast_type: str) -> int:
    """Инициализация рассылки."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await safe_answer_callback(query, "❌ У вас нет прав.", show_alert=True)
        return ConversationHandler.END
    
    context.user_data['broadcast_type'] = broadcast_type
    context.user_data[f'awaiting_{broadcast_type}_message'] = True
    
    broadcast_type_display = {
        'broadcast_all': 'всем пользователям',
        'broadcast_paid': 'пользователям с подпиской',
        'broadcast_non_paid': 'пользователям без подписки'
    }.get(broadcast_type, 'всем пользователям')
    
    text = (
        f"📢 Рассылка {escape_md(broadcast_type_display)}\n\n"
        f"Введите текст сообщения для рассылки или отправьте фото/видео.\n"
        f"Для отправки только медиа без текста выберите соответствующую кнопку."
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Отправить без текста", callback_data="send_broadcast_no_text")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_panel")]
    ])
    
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )
    
    return 0  # Возвращаем 0 для ConversationHandler

# === ДОПОЛНИТЕЛЬНЫЕ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

async def delete_menu_video_if_exists(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Удаляет видео меню, если оно было отправлено."""
    if 'menu_video_message_id' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=user_id,
                message_id=context.user_data['menu_video_message_id']
            )
            context.user_data.pop('menu_video_message_id', None)
        except Exception as e:
            logger.debug(f"Не удалось удалить видео меню: {e}")

async def create_payment_success_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру после успешной оплаты."""
    try:
        subscription_data = await check_subscription(user_id)
        has_avatars = subscription_data and len(subscription_data) >= 2 and subscription_data[1] > 0
        
        keyboard = []
        
        if has_avatars:
            keyboard.extend([
                [InlineKeyboardButton("➕ Создать аватар", callback_data="train_flux")],
                [InlineKeyboardButton("✨ Сгенерировать фото", callback_data="generate_menu")]
            ])
        else:
            keyboard.extend([
                [InlineKeyboardButton("✨ Попробовать генерацию", callback_data="generate_menu")],
                [InlineKeyboardButton("💎 Купить аватар", callback_data="subscribe")]
            ])
        
        keyboard.extend([
            [InlineKeyboardButton("👤 Мой профиль", callback_data="user_profile")],
            [InlineKeyboardButton("❓ Как пользоваться?", callback_data="faq")],
            [InlineKeyboardButton("🏠 В главное меню", callback_data="back_to_menu")]
        ])
        
        return InlineKeyboardMarkup(keyboard)
        
    except Exception as e:
        logger.error(f"Ошибка создания клавиатуры успешной оплаты: {e}")
        # Fallback клавиатура
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("✨ Генерация", callback_data="generate_menu")],
            [InlineKeyboardButton("👤 Профиль", callback_data="user_profile")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
        ])

async def handle_admin_send_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для отправки генерации пользователю."""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        return
        
    parts = query.data.split(':')
    target_user_id = int(parts[1])
    generation_data = context.user_data.get(f'last_admin_generation_{target_user_id}')
    
    if generation_data and generation_data.get('image_urls'):
        try:
            caption = escape_md("🎁 Для вас создано новое изображение!")
            
            await context.bot.send_photo(
                chat_id=target_user_id,
                photo=generation_data['image_urls'][0],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await query.answer("✅ Изображение отправлено пользователю!", show_alert=True)
            
            # Уведомляем админа
            await send_message_with_fallback(
                context.bot, query.from_user.id,
                escape_md(f"✅ Изображение успешно отправлено пользователю {target_user_id}"),
                update_or_query=update,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Ошибка отправки генерации пользователю: {e}")
            await query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
    else:
        await query.answer("❌ Данные генерации не найдены", show_alert=True)

async def handle_admin_regenerate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для повторной генерации админом."""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        return
    
    target_user_id = int(query.data.split(':')[1])
    await generate_photo_for_user(update, context, target_user_id)

async def handle_admin_style_selection(query, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора стиля для админской генерации."""
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Нет доступа", show_alert=True)
        return
    
    style = query.data.replace('admin_style_', '')
    target_user_id = context.user_data.get('admin_generation_for_user')
    
    if not target_user_id:
        await query.answer("❌ Ошибка: не найден целевой пользователь", show_alert=True)
        return
    
    if style == 'custom':
        # Запрашиваем кастомный промпт
        await query.message.edit_text(
            escape_md("✏️ Введите свой промпт для генерации:\n\n"
                     f"Триггер-слово '{context.user_data.get('active_trigger_word', '')}' будет добавлено автоматически."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        context.user_data['awaiting_admin_prompt'] = True
        context.user_data['admin_generation_style'] = 'custom'
    else:
        # Генерируем с предустановленным стилем
        await query.message.edit_text(
            escape_md(f"⏳ Генерирую изображение в стиле '{style}'..."),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        
        # Устанавливаем стиль и переходим к генерации
        context.user_data['style_name'] = style
        context.user_data['prompt'] = get_style_prompt(style)  # Получаем промпт для стиля
        
        # Запускаем генерацию
        await generate_image(query, context)

def get_style_prompt(style: str) -> str:
    """Получает промпт для заданного стиля."""
    style_prompts_dict = {
        'portrait': "professional portrait photo, studio lighting, high quality",
        'casual': "casual photo, natural lighting, relaxed pose",
        'artistic': "artistic photo, creative composition, dramatic lighting",
        'business': "business portrait, formal attire, professional setting",
        'outdoor': "outdoor photo, natural environment, golden hour lighting",
        'indoor': "indoor photo, cozy interior, warm lighting",
    }
    return style_prompts_dict.get(style, "high quality photo")

async def handle_admin_custom_prompt(message, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кастомного промпта от админа."""
    if not context.user_data.get('awaiting_admin_prompt'):
        return
    
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    
    custom_prompt = message.text
    target_user_id = context.user_data.get('admin_generation_for_user')
    
    if not target_user_id:
        return
    
    # Отправляем сообщение о начале генерации
    status_message = await message.reply_text(
        escape_md("⏳ Генерирую изображение с вашим промптом..."),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    
    # Очищаем флаг ожидания промпта
    context.user_data['awaiting_admin_prompt'] = False
    context.user_data['prompt'] = custom_prompt
    context.user_data['style_name'] = 'custom'
    
    # Выполняем генерацию
    await generate_image(message, context)
    
    # Удаляем сообщение о статусе
    try:
        await status_message.delete()
    except:
        pass

# === КОНСТАНТЫ И КОНФИГУРАЦИЯ ===

# Константы для состояний ConversationHandler
(
    AWAITING_BROADCAST_MESSAGE, AWAITING_BROADCAST_CONFIRM,
    AWAITING_PAYMENT_DATES, AWAITING_USER_SEARCH, AWAITING_BALANCE_CHANGE,
    AWAITING_BROADCAST_FILTERS, AWAITING_BROADCAST_SCHEDULE,
    AWAITING_ACTIVITY_DATES
) = range(8)

# === ОБРАБОТЧИКИ УВЕДОМЛЕНИЙ И СТАТУСОВ ===

async def handle_training_notification(context: ContextTypes.DEFAULT_TYPE, user_id: int, status: str, model_name: str = ""):
    """Отправляет уведомление о статусе обучения аватара."""
    try:
        if status == "success":
            text = (
                f"🎉Отлично! Твой аватар '{model_name}' готов к использованию!\n\n"
                f"Теперь ты можешь создавать уникальные фото с помощью своего персонального AI-аватара.\n\n"
                f"Попробуй сгенерировать первое фото!"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✨ Создать фото", callback_data="generate_with_avatar")],
                [InlineKeyboardButton("👥 Мои аватары", callback_data="my_avatars")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
            ])
        elif status == "failed":
            text = (
                f"😔 К сожалению, обучение аватара '{model_name}' не удалось.\n\n"
                f"Возможные причины:\n"
                f"•Недостаточно качественных фото\n"
                f"•Фото слишком разные по качеству\n"
                f"•Лица плохо видны на фотографиях\n\n"
                f"💎 Аватар возвращен на ваш баланс. Попробуйте еще раз с другими фото."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="train_flux")],
                [InlineKeyboardButton("💬 Поддержка", callback_data="support")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
            ])
        else:
            return  # Неизвестный статус
            
        await send_message_with_fallback(
            context.bot, user_id, escape_md(text),
            reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об обучении пользователю {user_id}: {e}")

async def handle_generation_notification(context: ContextTypes.DEFAULT_TYPE, user_id: int, 
                                      generation_type: str, success: bool, result_data: dict = None):
    """Отправляет уведомление о завершении генерации."""
    try:
        if success and result_data:
            if generation_type in ['ai_video', 'ai_video_v2']:
                text = (
                    f"🎬 Твое AI-видео готово!\n\n"
                    f"Видео успешно сгенерировано и отправлено выше.\n"
                    f"Поделись результатом с друзьями!"
                )
            else:
                text = (
                    f"📸 Твои фото готовы!\n\n"
                    f"Изображения успешно сгенерированы и отправлены.\n"
                    f"Оцени результат и поделись с друзьями!"
                )
                
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Создать еще", callback_data="generate_menu")],
                [InlineKeyboardButton("👥 Поделиться", callback_data="share_result")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
            ])
        else:
            text = (
                f"😔 К сожалению, генерация не удалась.\n\n"
                f"Попробуйте еще раз или измените параметры.\n"
                f"Если проблема повторяется - обратитесь в поддержку."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="generate_menu")],
                [InlineKeyboardButton("💬 Поддержка", callback_data="support")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
            ])
            
        await send_message_with_fallback(
            context.bot, user_id, escape_md(text),
            reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
        )
        
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о генерации пользователю {user_id}: {e}")

# === ОБРАБОТЧИКИ ОШИБОК И ИСКЛЮЧЕНИЙ ===

async def handle_callback_error(update: Update, context: ContextTypes.DEFAULT_TYPE, error: Exception):
    """Обработчик ошибок в callback функциях."""
    user_id = update.effective_user.id if update.effective_user else 0
    callback_data = update.callback_query.data if update.callback_query else "unknown"
    
    logger.error(f"Ошибка в callback '{callback_data}' для пользователя {user_id}: {error}", exc_info=True)
    
    # Пытаемся очистить состояние пользователя
    if hasattr(context, 'user_data') and context.user_data:
        # Сохраняем только критически важные данные
        important_keys = ['user_id', 'username', 'email']
        filtered_data = {k: v for k, v in context.user_data.items() if k in important_keys}
        context.user_data.clear()
        context.user_data.update(filtered_data)
    
    error_text = (
        "❌ Произошла неожиданная ошибка.\n\n"
        "Мы уже работаем над её исправлением.\n"
        "Попробуйте начать заново или обратитесь в поддержку."
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 В Меню", callback_data="back_to_menu")],
        [InlineKeyboardButton("💬 Поддержка", callback_data="support")],
        [InlineKeyboardButton("❓ Помощь", callback_data="faq")]
    ])
    
    try:
        if update.callback_query:
            await update.callback_query.answer("❌ Произошла ошибка", show_alert=True)
            await send_message_with_fallback(
                context.bot, user_id, escape_md(error_text),
                update_or_query=update, reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await send_message_with_fallback(
                context.bot, user_id, escape_md(error_text),
                reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
            )
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение об ошибке пользователю {user_id}: {e}")

# === ФУНКЦИИ ВАЛИДАЦИИ И ПРОВЕРКИ ===

async def validate_user_permissions(user_id: int, required_permission: str = "user") -> bool:
    """Проверяет права пользователя."""
    try:
        if required_permission == "admin":
            return user_id in ADMIN_IDS
        elif required_permission == "user":
            # Проверяем, что пользователь существует в базе
            user_data = await check_subscription(user_id)
            return user_data is not None
        return False
    except Exception as e:
        logger.error(f"Ошибка проверки прав пользователя {user_id}: {e}")
        return False

async def validate_callback_data(callback_data: str) -> bool:
    """Проверяет валидность callback данных."""
    try:
        # Базовая проверка на безопасность
        if not callback_data or len(callback_data) > 64:
            return False
        
        # Проверяем на недопустимые символы
        import string
        allowed_chars = string.ascii_letters + string.digits + "_:-"
        if not all(c in allowed_chars for c in callback_data):
            return False
            
        return True
    except Exception:
        return False

# === УТИЛИТЫ ДЛЯ ОБРАБОТКИ МЕДИА ===

async def process_media_for_broadcast(context: ContextTypes.DEFAULT_TYPE, media_type: str, media_id: str):
    """Обрабатывает медиа для рассылки."""
    try:
        if media_type == "photo":
            return await context.bot.get_file(media_id)
        elif media_type == "video":
            return await context.bot.get_file(media_id)
        elif media_type == "document":
            return await context.bot.get_file(media_id)
        return None
    except Exception as e:
        logger.error(f"Ошибка обработки медиа {media_type}:{media_id}: {e}")
        return None

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СТАТИСТИКИ ===

async def get_user_activity_summary(user_id: int) -> dict:
    """Получает сводку активности пользователя."""
    try:
        summary = {
            'total_generations': 0,
            'total_avatars': 0,
            'total_spent': 0.0,
            'registration_date': None,
            'last_activity': None,
            'favorite_generation_type': 'unknown'
        }
        
        async with aiosqlite.connect('users.db') as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.cursor()
            
            # Получаем дату регистрации из первого платежа или первой активности
            await cursor.execute("""
                SELECT MIN(created_at) as first_activity 
                FROM (
                    SELECT created_at FROM payments WHERE user_id = ?
                    UNION ALL
                    SELECT timestamp FROM user_ratings WHERE user_id = ?
                    UNION ALL
                    SELECT timestamp FROM generation_log WHERE user_id = ?
                    UNION ALL
                    SELECT created_at FROM trained_models WHERE user_id = ?
                )
            """, (user_id, user_id, user_id, user_id))
            
            first_activity_result = await cursor.fetchone()
            if first_activity_result and first_activity_result['first_activity']:
                summary['registration_date'] = first_activity_result['first_activity']
            
            # Получаем последнюю активность
            await cursor.execute("""
                SELECT MAX(last_activity) as last_activity 
                FROM (
                    SELECT created_at as last_activity FROM payments WHERE user_id = ?
                    UNION ALL
                    SELECT timestamp as last_activity FROM user_ratings WHERE user_id = ?
                    UNION ALL
                    SELECT timestamp as last_activity FROM generation_log WHERE user_id = ?
                    UNION ALL
                    SELECT updated_at as last_activity FROM trained_models WHERE user_id = ?
                )
            """, (user_id, user_id, user_id, user_id))
            
            last_activity_result = await cursor.fetchone()
            if last_activity_result and last_activity_result['last_activity']:
                summary['last_activity'] = last_activity_result['last_activity']
            
            # Получаем количество созданных аватаров
            await cursor.execute("""
                SELECT COUNT(*) as total_avatars 
                FROM trained_models 
                WHERE user_id = ? AND status = 'success'
            """, (user_id,))
            
            avatars_result = await cursor.fetchone()
            if avatars_result:
                summary['total_avatars'] = avatars_result['total_avatars']
        
        # Получаем статистику генераций
        gen_stats = await get_user_generation_stats(user_id)
        if gen_stats:
            summary['total_generations'] = sum(gen_stats.values())
            # Определяем любимый тип генерации
            if gen_stats:
                summary['favorite_generation_type'] = max(gen_stats.items(), key=lambda x: x[1])[0]
        
        # Получаем данные о платежах
        payments = await get_user_payments(user_id)
        if payments:
            summary['total_spent'] = sum(p[2] for p in payments if p[2] is not None)
        
        return summary
    except Exception as e:
        logger.error(f"Ошибка получения сводки активности для user_id={user_id}: {e}")
        return {}

# === ФУНКЦИИ ОЧИСТКИ И ОБСЛУЖИВАНИЯ ===

async def cleanup_user_context(context: ContextTypes.DEFAULT_TYPE, user_id: int, keep_essential: bool = True):
    """Очищает контекст пользователя."""
    try:
        if not hasattr(context, 'user_data') or not context.user_data:
            return
        
        if keep_essential:
            # Сохраняем только основные данные
            essential_keys = [
                'user_id', 'username', 'email', 'registration_date',
                'subscription_data', 'active_model_version', 'active_trigger_word'
            ]
            filtered_data = {k: v for k, v in context.user_data.items() if k in essential_keys}
            context.user_data.clear()
            context.user_data.update(filtered_data)
        else:
            context.user_data.clear()
            
        logger.debug(f"Контекст пользователя {user_id} очищен (keep_essential={keep_essential})")
    except Exception as e:
        logger.error(f"Ошибка очистки контекста пользователя {user_id}: {e}")

async def validate_generation_context(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет валидность контекста генерации."""
    try:
        required_fields = ['generation_type', 'model_key']
        return all(field in context.user_data for field in required_fields)
    except Exception:
        return False

# === ОБРАБОТЧИКИ СПЕЦИАЛЬНЫХ СОБЫТИЙ ===

async def handle_user_guide(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Показывает руководство пользователя."""
    text = (
        "📖 Руководство пользователя\n\n"
        "🎯Быстрый старт:\n"
        "1\\.Купите пакет фото\n"
        "2\\.Создайте свой аватар\n"
        "3\\.Генерируйте уникальные фото\n\n"
        "📸Типы генерации:\n"
        "•С аватаром \\- персональные фото\n"
        "•По референсу \\- копирование стиля\n"
        "•AI\\-видео \\- анимированные ролики\n\n"
        "💡Советы для лучших результатов:\n"
        "•Используйте детальные описания\n"
        "•Экспериментируйте со стилями\n"
        "•Загружайте качественные фото для аватара"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Попробовать", callback_data="generate_menu")],
        [InlineKeyboardButton("❓ Вопросы", callback_data="faq")],
        [InlineKeyboardButton("🔙 Назад", callback_data="support")]
    ])
    
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_share_result(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработчик поделиться результатом."""
    bot_username = context.bot.username or "bot"
    share_text = "Посмотри, какие крутые фото я создал с помощью AI! 🤖✨"
    share_url = f"https://t.me/share/url?url=t.me/{bot_username}&text={share_text}"
    
    text = (
        "📤 Поделись своими результатами!\n\n"
        "Покажи друзьям, какие крутые фото ты создаешь с помощью AI\\!\n"
        "Возможно, они тоже захотят попробовать\\."
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Поделиться в Telegram", url=share_url)],
        [InlineKeyboardButton("🔄 Создать еще", callback_data="generate_menu")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="back_to_menu")]
    ])
    
    await send_message_with_fallback(
        context.bot, user_id, text, update_or_query=query,
        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2
    )

# === ФИНАЛЬНЫЕ ОБРАБОТЧИКИ ===

async def handle_unknown_callback(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обработчик неизвестных callback данных."""
    await safe_answer_callback(query, "⚠️ Неизвестная команда. Попробуйте еще раз.")
    
    # Возвращаем пользователя в главное меню
    await send_message_with_fallback(
        context.bot, user_id,
        escape_md("🔄 Что-то пошло не так. Давайте начнем сначала!"),
        update_or_query=query,
        reply_markup=await create_main_menu_keyboard(user_id),
        parse_mode=ParseMode.MARKDOWN_V2
    )

# === ЭКСПОРТ ФУНКЦИЙ ===

__all__ = [
    'button',
    'handle_proceed_to_payment',
    'handle_generate_menu',
    'handle_generate_with_avatar',
    'handle_style_selection',
    'handle_style_choice',
    'handle_photo_to_photo',
    'handle_ai_video',
    'handle_custom_prompt_manual',
    'handle_custom_prompt_llama',
    'handle_confirm_assisted_prompt',
    'handle_edit_assisted_prompt',
    'handle_aspect_ratio',
    'handle_back_to_style_selection',
    'handle_confirm_generation',
    'handle_rating',
    'handle_user_profile',
    'handle_check_subscription',
    'handle_user_stats',
    'handle_subscribe',
    'handle_payment',
    'handle_my_avatars',
    'handle_select_avatar',
    'handle_train_flux',
    'handle_continue_upload',
    'handle_start_training',
    'handle_confirm_start_training',
    'handle_back_to_avatar_name',
    'handle_use_suggested_trigger',
    'handle_confirm_photo_quality',
    'handle_my_referrals',
    'handle_admin_panel',
    'handle_admin_give_subscription',
    'handle_admin_give_sub_to_user',
    'handle_admin_add_resources',
    'handle_admin_chat_with_user',
    'handle_admin_reset_avatar',
    'handle_change_email',
    'handle_confirm_change_email',
    'handle_referrals_menu',
    'handle_referral_info',
    'handle_copy_referral_link',
    'handle_referral_help',
    'handle_payment_history',
    'handle_tariff_info',
    'handle_category_info',
    'handle_compare_tariffs',
    'handle_aspect_ratio_info',
    'handle_back_to_aspect_selection',
    'handle_support',
    'handle_faq',
    'handle_faq_topic',
    'handle_skip_mask',
    'handle_male_styles_page',
    'handle_female_styles_page',
    'ask_for_aspect_ratio',
    'delete_all_videos',
    'delete_menu_video_if_exists',
    'initiate_broadcast',
    'create_payment_success_keyboard',
    'handle_admin_send_generation',
    'handle_admin_regenerate',
    'handle_admin_style_selection',
    'handle_admin_custom_prompt',
    'get_style_prompt',
    'handle_training_notification',
    'handle_generation_notification',
    'handle_callback_error',
    'validate_user_permissions',
    'validate_callback_data',
    'process_media_for_broadcast',
    'get_user_activity_summary',
    'cleanup_user_context',
    'validate_generation_context',
    'handle_user_guide',
    'handle_share_result',
    'handle_unknown_callback'
]