# generation/generation.py

import asyncio
import logging
from typing import Optional, List, Dict, Tuple
from aiogram import Router, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from database import check_database_user, get_active_trainedmodel, update_user_credits
from config import ADMIN_IDS
from handlers.utils import (
    safe_escape_markdown as escape_md, send_message_with_fallback, check_resources, clean_admin_context
)
from keyboards import (
    create_admin_keyboard, create_main_menu_keyboard, create_avatar_style_choice_keyboard,
    create_new_male_avatar_styles_keyboard, create_new_female_avatar_styles_keyboard,
    create_aspect_ratio_keyboard, create_rating_keyboard
)
from generation.images import generate_image, process_prompt_async, prepare_model_params
from generation.utils import reset_generation_context

logger = logging.getLogger(__name__)

# Создание роутера для генерации
generation_router = Router()

async def generate_photo_for_user(query: CallbackQuery, state: FSMContext, target_user_id: int) -> None:
   
    admin_id = query.from_user.id
    bot_id = (await query.bot.get_me()).id
    logger.debug(f"Инициирована генерация фото для target_user_id={target_user_id} администратором user_id={admin_id}")

    # Проверка прав администратора
    if admin_id not in ADMIN_IDS:
        await query.answer("⛔ Недостаточно прав", show_alert=True)
        return

    # Проверка, что target_user_id не является ID бота
    if target_user_id == bot_id:
        logger.error(f"Некорректный target_user_id: {target_user_id} (ID бота)")
        await send_message_with_fallback(
            query.bot, admin_id, 
            escape_md(f"❌ Неверный ID пользователя: `{target_user_id}`.", version=2),
            update_or_query=query, 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    # Проверяем существование пользователя
    target_user_info = await check_database_user(target_user_id)
    if not target_user_info or (target_user_info[3] is None and target_user_info[8] is None):
        await send_message_with_fallback(
            query.bot, admin_id, 
            escape_md(f"❌ Пользователь ID `{target_user_id}` не найден.", version=2),
            update_or_query=query, 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    # Проверяем наличие активного аватара у целевого пользователя
    active_model_data = await get_active_trainedmodel(target_user_id)
    if not active_model_data or active_model_data[3] != 'success':
        await send_message_with_fallback(
            query.bot, admin_id, 
            escape_md(f"❌ У пользователя ID `{target_user_id}` нет активного аватара.", version=2),
            update_or_query=query,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    # Очищаем контекст перед началом генерации
    await clean_admin_context(state)
    logger.info(f"Контекст очищен перед админской генерацией для user_id={target_user_id}")

    # Сохраняем данные для генерации
    await state.update_data(
        admin_generation_for_user=target_user_id,
        generation_type='with_avatar',
        model_key='flux-trained',
        active_model_version=active_model_data[0],  # model_version
        active_trigger_word=active_model_data[1],   # trigger_word
        active_avatar_name=active_model_data[2],    # avatar_name
        old_model_id=active_model_data[4],         # model_id
        old_model_version=active_model_data[0],    # model_version
        is_admin_generation=True,
        message_recipient=admin_id,
        generation_target_user=target_user_id,
        original_admin_user=admin_id
    )

    # Отправляем сообщение с выбором категории стилей (мужской/женский)
    text = escape_md(
        f"👤 Генерация фото для пользователя ID `{target_user_id}`.\n\n"
        f"Выберите категорию стилей для генерации:", version=2
    )
    await send_message_with_fallback(
        query.bot, admin_id, text,
        reply_markup=await create_avatar_style_choice_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await query.answer()

async def handle_admin_style_selection(query: CallbackQuery, state: FSMContext) -> None:
   
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("❌ Нет доступа.", show_alert=True)
        return

    callback_data = query.data
    user_data = await state.get_data()
    target_user_id = user_data.get('admin_generation_for_user')

    if not target_user_id:
        await query.answer("❌ Ошибка: не найден целевой пользователь.", show_alert=True)
        await query.message.edit_text(
            escape_md("❌ Ошибка: не найден целевой пользователь.", version=2),
            reply_markup=await create_admin_keyboard(admin_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Обработка выбора категории стилей
    if callback_data == "select_new_male_avatar_styles":
        await state.update_data(selected_gender="male")
        await query.message.edit_text(
            escape_md(f"👨 Выберите мужской стиль для пользователя ID `{target_user_id}`:", version=2),
            reply_markup=await create_new_male_avatar_styles_keyboard(page=1),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    elif callback_data == "select_new_female_avatar_styles":
        await state.update_data(selected_gender="female")
        await query.message.edit_text(
            escape_md(f"👩 Выберите женский стиль для пользователя ID `{target_user_id}`:", version=2),
            reply_markup=await create_new_female_avatar_styles_keyboard(page=1),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    elif callback_data.startswith("style_new_male_") or callback_data.startswith("style_new_female_"):
        style_key = callback_data.replace("style_new_male_", "").replace("style_new_female_", "")
        await state.update_data(style_key=style_key, prompt=style_key, style_name=NEW_MALE_AVATAR_STYLES.get(style_key, NEW_FEMALE_AVATAR_STYLES.get(style_key, style_key)))
        # Переход к выбору соотношения сторон
        await query.message.edit_text(
            escape_md(f"📐 Выберите соотношение сторон для генерации:", version=2),
            reply_markup=await create_aspect_ratio_keyboard(back_callback="generate_with_avatar"),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    elif callback_data.startswith("male_styles_page_") or callback_data.startswith("female_styles_page_"):
        page = int(callback_data.split("_")[-1])
        if callback_data.startswith("male_styles_page_"):
            reply_markup = await create_new_male_avatar_styles_keyboard(page=page)
            text = escape_md(f"👨 Выберите мужской стиль для пользователя ID `{target_user_id}`:", version=2)
        else:
            reply_markup = await create_new_female_avatar_styles_keyboard(page=page)
            text = escape_md(f"👩 Выберите женский стиль для пользователя ID `{target_user_id}`:", version=2)
        await query.message.edit_text(
            text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )
    elif callback_data == "enter_custom_prompt_manual":
        await query.message.edit_text(
            escape_md(f"✏️ Введите свой промпт для генерации:\n\nТриггер-слово `{user_data.get('active_trigger_word', '')}` будет добавлено автоматически.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(awaiting_admin_prompt=True, admin_generation_style='custom', came_from_custom_prompt=True)
    elif callback_data == "enter_custom_prompt_llama":
        await query.message.edit_text(
            escape_md(f"🤖 Введите описание для генерации с помощью AI-помощника:\n\nТриггер-слово `{user_data.get('active_trigger_word', '')}` будет добавлено автоматически.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await state.update_data(awaiting_admin_prompt=True, admin_generation_style='custom', use_llama_prompt=True)
    else:
        logger.warning(f"Неизвестный callback в handle_admin_style_selection: {callback_data}")
        await query.message.edit_text(
            escape_md("❌ Неизвестное действие. Попробуйте снова.", version=2),
            reply_markup=await create_admin_keyboard(admin_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    await query.answer()

async def handle_admin_custom_prompt(message: Message, state: FSMContext) -> None:
    
    user_data = await state.get_data()
    if not user_data.get('awaiting_admin_prompt'):
        return

    admin_id = message.from_user.id
    if admin_id not in ADMIN_IDS:
        return

    target_user_id = user_data.get('admin_generation_for_user')
    if not target_user_id:
        await message.answer(
            escape_md("❌ Ошибка: не найден целевой пользователь.", version=2),
            reply_markup=await create_admin_keyboard(admin_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    custom_prompt = message.text.strip()
    if not custom_prompt:
        await message.answer(
            escape_md("❌ Промпт не может быть пустым. Введите описание.", version=2),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    status_message = await message.answer(
        escape_md("⏳ Обрабатываю ваш промпт...", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )

    if user_data.get('use_llama_prompt', False):
        try:
            from llama_helper import generate_assisted_prompt
            processed_prompt = await generate_assisted_prompt(custom_prompt)
            await state.update_data(prompt=processed_prompt, user_input_for_llama=custom_prompt)
        except Exception as e:
            logger.error(f"Ошибка при обработке промпта через LLaMA для user_id={admin_id}: {e}", exc_info=True)
            await status_message.edit_text(
                escape_md("❌ Ошибка обработки промпта AI-помощником. Попробуйте снова.", version=2),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
    else:
        processed_prompt = await process_prompt_async(
            custom_prompt,
            user_data.get('model_key', 'flux-trained'),
            user_data.get('generation_type', 'with_avatar'),
            user_data.get('active_trigger_word'),
            user_data.get('selected_gender'),
            custom_prompt,
            user_data,
            use_new_flux=user_data.get('model_key') == 'flux-trained'
        )
        await state.update_data(prompt=processed_prompt)

    await state.update_data(awaiting_admin_prompt=False, admin_generation_style='custom')
    
    # Переход к выбору соотношения сторон
    await status_message.edit_text(
        escape_md(f"📐 Выберите соотношение сторон для генерации:", version=2),
        reply_markup=await create_aspect_ratio_keyboard(back_callback="generate_with_avatar"),
        parse_mode=ParseMode.MARKDOWN_V2
    )

async def handle_admin_aspect_ratio_selection(query: CallbackQuery, state: FSMContext) -> None:
    
    admin_id = query.from_user.id
    if admin_id not in ADMIN_IDS:
        await query.answer("❌ Нет доступа.", show_alert=True)
        return

    callback_data = query.data
    if not callback_data.startswith("aspect_"):
        await query.answer("❌ Неверный формат соотношения сторон.", show_alert=True)
        return

    aspect_ratio = callback_data.replace("aspect_", "")
    await state.update_data(aspect_ratio=aspect_ratio)

    user_data = await state.get_data()
    target_user_id = user_data.get('admin_generation_for_user')
    if not target_user_id:
        await query.message.edit_text(
            escape_md("❌ Ошибка: не найден целевой пользователь.", version=2),
            reply_markup=await create_admin_keyboard(admin_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # Проверяем наличие активного аватара
    active_model_data = await get_active_trainedmodel(target_user_id)
    if not active_model_data or active_model_data[3] != 'success':
        await query.message.edit_text(
            escape_md(f"❌ У пользователя ID `{target_user_id}` нет активного аватара.", version=2),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await query.answer()
        return

    # Гарантируем сохранение админского контекста перед генерацией
    await state.update_data(
        is_admin_generation=True,
        admin_generation_for_user=target_user_id,
        message_recipient=admin_id,
        generation_target_user=target_user_id,
        original_admin_user=admin_id
    )

    await query.message.edit_text(
        escape_md("⏳ Генерирую изображение...", version=2),
        parse_mode=ParseMode.MARKDOWN_V2
    )

    try:
        # Запуск генерации
        await generate_image(query.message, state, num_outputs=2)

    except Exception as e:
        logger.error(f"Ошибка генерации изображения для admin_id={admin_id}: {e}", exc_info=True)
        await query.message.edit_text(
            escape_md(f"❌ Ошибка генерации: {str(e)}.", version=2),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await reset_generation_context(state, user_data.get('generation_type'))

async def handle_admin_generation_result(state: FSMContext, admin_id: int, target_user_id: int, result_data: Dict, bot: Bot) -> None:
    
    try:
        user_data = await state.get_data()
        generation_type = user_data.get('generation_type', 'with_avatar')
        model_key = user_data.get('model_key', 'flux-trained')

        if result_data.get('success') and result_data.get('image_urls'):
            caption = escape_md(
                f"✅ Генерация для пользователя `{target_user_id}` завершена!\n"
                f"👤 Аватар: {user_data.get('active_avatar_name', 'Неизвестно')}\n"
                f"🎨 Стиль: {result_data.get('style', user_data.get('style_key', 'custom'))}\n"
                f"📝 Промпт: {result_data.get('prompt', 'Не указан')[:100]}...", version=2
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Еще раз", callback_data=f"admin_generate:{target_user_id}")],
                [InlineKeyboardButton(text="📤 Отправить пользователю", callback_data=f"admin_send_gen:{target_user_id}")],
                [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ])

            await bot.send_photo(
                chat_id=admin_id,
                photo=result_data['image_urls'][0],
                caption=caption,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=keyboard
            )

            await state.update_data(**{f'last_admin_generation_{target_user_id}': {
                'image_urls': result_data.get('image_urls'),
                'prompt': result_data.get('prompt'),
                'style': result_data.get('style', user_data.get('style_key', 'custom'))
            }})
        else:
            error_msg = result_data.get('error', 'Неизвестная ошибка')
            await state.clear()
            await send_message_with_fallback(
                bot, admin_id, 
                escape_md(f"❌ Ошибка генерации: {error_msg}.", version=2),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            await reset_generation_context(state, generation_type)

    except Exception as e:
        logger.error(f"Ошибка обработки результата админской генерации: {e}", exc_info=True)
        text = escape_md(f"❌ Ошибка при обработке результата: {str(e)}.", version=2)
        await state.clear()
        await send_message_with_fallback(
            bot, admin_id, text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
            ]),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        await reset_generation_context(state, generation_type)

async def process_image_generation(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    image_paths: List[str],
    duration: float,
    aspect_ratio: str,
    generation_type: str,
    model_key: str,
    admin_user_id: Optional[int] = None
) -> None:
    from keyboards import create_rating_keyboard, create_admin_user_actions_keyboard
    from generation.utils import send_message_with_fallback, send_media_group_with_retry, cleanup_files
    from aiogram.types import InputMediaPhoto, FSInputFile

    logger.info(f"Начало process_image_generation: user_id={user_id}, admin_user_id={admin_user_id}, generation_type={generation_type}")

    try:
        user_data = await state.get_data()
        logger.debug(f"Данные состояния: {user_data}")
        
        is_admin_generation = user_data.get('is_admin_generation', False) or (admin_user_id and user_id != admin_user_id)
        style_name = user_data.get('style_name', 'Кастомный стиль')
        active_avatar_name = user_data.get('active_avatar_name', 'Без имени')

        # Восстанавливаем админский контекст, если он отсутствует
        if admin_user_id and user_id != admin_user_id:
            logger.info(f"Восстановление админского контекста: user_id={user_id}, admin_user_id={admin_user_id}")
            is_admin_generation = True
            await state.update_data(
                is_admin_generation=True,
                admin_generation_for_user=user_id,
                message_recipient=admin_user_id,
                generation_target_user=user_id,
                original_admin_user=admin_user_id
            )

        logger.debug(f"is_admin_generation={is_admin_generation}, admin_user_id={admin_user_id}")

        # Проверяем, что изображения существуют
        if not image_paths:
            logger.error(f"Пустой список image_paths для user_id={user_id}")
            await send_message_with_fallback(
                bot, user_id,
                escape_md("❌ Ошибка: изображения не получены. Попробуйте снова.", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            if is_admin_generation and admin_user_id and admin_user_id != user_id:
                await send_message_with_fallback(
                    bot, admin_user_id,
                    escape_md(f"❌ Ошибка: изображения не получены для пользователя ID `{user_id}`.", version=2),
                    reply_markup=await create_admin_user_actions_keyboard(user_id, False),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            return

        # Формируем подпись для пользователя
        caption = escape_md(
            f"📸 {len(image_paths)} ваших фотографий созданы! ({duration:.1f} сек)\n"
            f"🎨 Стиль: {style_name}\n"
            f"👤 Аватар: {active_avatar_name}\n"
            f"⚡ Сделано при помощи PixelPie_AI", version=2
        )

        # Отправляем изображения пользователю
        logger.info(f"Отправка изображений пользователю user_id={user_id}")
        try:
            if len(image_paths) == 1:
                photo_file = FSInputFile(path=image_paths[0])
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file,
                    caption=caption,
                    reply_markup=await create_rating_keyboard(generation_type, model_key, user_id, bot),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            else:
                media = []
                for i, path in enumerate(image_paths):
                    photo_file = FSInputFile(path=path)
                    if i == 0:
                        media.append(InputMediaPhoto(media=photo_file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2))
                    else:
                        media.append(InputMediaPhoto(media=photo_file))
                await send_media_group_with_retry(bot, user_id, media)
                await send_message_with_fallback(
                    bot, user_id,
                    escape_md("⭐ Оцени результат ИИ фотогенерации:", version=2),
                    reply_markup=await create_rating_keyboard(generation_type, model_key, user_id, bot),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            logger.info(f"Изображения успешно отправлены пользователю user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки изображений пользователю user_id={user_id}: {e}", exc_info=True)
            await send_message_with_fallback(
                bot, user_id,
                escape_md("❌ Ошибка при отправке результатов. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
                reply_markup=await create_main_menu_keyboard(user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            if is_admin_generation and admin_user_id and admin_user_id != user_id:
                await send_message_with_fallback(
                    bot, admin_user_id,
                    escape_md(f"❌ Ошибка при отправке изображений пользователю ID `{user_id}`.", version=2),
                    reply_markup=await create_admin_user_actions_keyboard(user_id, False),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            return

        # Отправляем уведомление администратору, если это админская генерация
        if is_admin_generation and admin_user_id and admin_user_id != user_id:
            logger.info(f"Отправка уведомления администратору admin_user_id={admin_user_id} для user_id={user_id}")
            try:
                admin_notification = escape_md(
                    f"✅ Фото успешно отправлены пользователю ID `{user_id}`.\n\n"
                    f"🎨 Стиль: {style_name}\n"
                    f"👤 Аватар: {active_avatar_name}", version=2
                )
                await send_message_with_fallback(
                    bot, admin_user_id,
                    admin_notification,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{user_id}")]
                    ]),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                logger.info(f"Уведомление успешно отправлено администратору admin_user_id={admin_user_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления администратору admin_user_id={admin_user_id}: {e}", exc_info=True)
                # Не прерываем выполнение, просто логируем ошибку

        # Сохраняем параметры для повторной генерации
        await state.update_data(
            last_generation_params={
                'prompt': user_data.get('prompt'),
                'aspect_ratio': aspect_ratio,
                'generation_type': generation_type,
                'model_key': model_key,
                'style_name': style_name,
                'selected_gender': user_data.get('selected_gender'),
                'user_input_for_llama': user_data.get('user_input_for_llama'),
                'current_style_set': user_data.get('current_style_set'),
                'came_from_custom_prompt': user_data.get('came_from_custom_prompt', False),
                'use_llama_prompt': user_data.get('use_llama_prompt', False)
            },
            **{f'last_admin_generation_{user_id}': {
                'prompt': user_data.get('prompt'),
                'aspect_ratio': aspect_ratio,
                'generation_type': generation_type,
                'model_key': model_key,
                'style': style_name,
                'image_urls': user_data.get(f'last_admin_generation_{user_id}', {}).get('image_urls', []),
                'selected_gender': user_data.get('selected_gender'),
                'user_input_for_llama': user_data.get('user_input_for_llama'),
                'duration': duration
            }} if is_admin_generation else {}
        )

        # Очищаем временные файлы
        asyncio.create_task(cleanup_files(image_paths))
        logger.info(f"Результаты генерации обработаны для user_id={user_id}, state={user_data.get('state')}")

    except Exception as e:
        logger.error(f"Критическая ошибка в process_image_generation для user_id={user_id}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, user_id,
            escape_md("❌ Ошибка при отправке результатов. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(user_id),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        if is_admin_generation and admin_user_id and admin_user_id != user_id:
            await send_message_with_fallback(
                bot, admin_user_id,
                escape_md(f"❌ Ошибка при отправке изображений пользователю ID `{user_id}`.", version=2),
                reply_markup=await create_admin_user_actions_keyboard(user_id, False),
                parse_mode=ParseMode.MARKDOWN_V2
            )

async def cancel(message: Message, state: FSMContext) -> None:
    
    user_id = message.from_user.id
    await state.clear()
    text = escape_md("✅ Все действия отменены.", version=2)
    reply_markup = await create_admin_keyboard() if user_id in ADMIN_IDS else await create_main_menu_keyboard(user_id)
    await message.answer(
        text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
    )

# Регистрация обработчиков
@generation_router.callback_query(
    lambda c: c.data and c.data.startswith((
        "admin_generate:", "admin_send_gen:", "select_new_male_avatar_styles", 
        "select_new_female_avatar_styles", "style_new_male_", "style_new_female_", 
        "male_styles_page_", "female_styles_page_", "enter_custom_prompt_manual", 
        "enter_custom_prompt_llama", "aspect_"
    ))
)
async def generation_callback_handler(query: CallbackQuery, state: FSMContext) -> None:
   
    callback_data = query.data
    logger.info(f"Получен callback: {callback_data} от user_id={query.from_user.id}")
    try:
        if callback_data.startswith("admin_generate:"):
            target_user_id = int(callback_data.split(':')[1])
            await generate_photo_for_user(query, state, target_user_id)
        elif callback_data.startswith("admin_send_gen:"):
            target_user_id = int(callback_data.split(':')[1])
            user_data = await state.get_data()
            last_gen_data = user_data.get(f'last_admin_generation_{target_user_id}', {})
            if not last_gen_data or not last_gen_data.get('image_urls'):
                logger.error(f"Нет данных последней генерации для target_user_id={target_user_id}")
                await query.message.edit_text(
                    escape_md(f"❌ Нет результатов генерации для пользователя ID `{target_user_id}`.", version=2),
                    reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await query.answer()
                return
            image_paths = user_data.get('last_admin_image_paths', [])
            if not image_paths:
                logger.error(f"Нет локальных путей изображений для target_user_id={target_user_id}")
                await query.message.edit_text(
                    escape_md(f"❌ Ошибка: изображения недоступны для пользователя ID `{target_user_id}`.", version=2),
                    reply_markup=await create_admin_user_actions_keyboard(target_user_id, False),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                await query.answer()
                return
            await process_image_generation(
                bot=query.bot,
                state=state,
                user_id=target_user_id,
                image_paths=image_paths,
                duration=0.0,  # Duration недоступен, используем 0.0
                aspect_ratio=user_data.get('last_admin_generation', {}).get('aspect_ratio', '1:1'),
                generation_type=user_data.get('last_admin_generation', {}).get('generation_type', 'with_avatar'),
                model_key=user_data.get('last_admin_generation', {}).get('model_key', 'flux-trained'),
                admin_user_id=query.from_user.id
            )
            await query.answer()
        elif callback_data.startswith(("select_new_male_avatar_styles", "select_new_female_avatar_styles", 
                                       "style_new_male_", "style_new_female_", "male_styles_page_", 
                                       "female_styles_page_", "enter_custom_prompt_manual", 
                                       "enter_custom_prompt_llama")):
            await handle_admin_style_selection(query, state)
        elif callback_data.startswith("aspect_"):
            await handle_admin_aspect_ratio_selection(query, state)
    except Exception as e:
        logger.error(f"Ошибка в generation_callback_handler: {e}", exc_info=True)
        await query.message.answer(
            escape_md("❌ Произошла ошибка. Попробуйте снова или обратитесь в поддержку.", version=2),
            reply_markup=await create_admin_keyboard(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

@generation_router.message(lambda m: m.text and not m.text.startswith('/'))
async def handle_admin_prompt_message(message: Message, state: FSMContext) -> None:
    
    user_data = await state.get_data()
    if user_data.get('awaiting_admin_prompt'):
        await handle_admin_custom_prompt(message, state)