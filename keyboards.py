# keyboards.py

import os
import asyncio
import logging
from typing import List, Optional, Dict, Any
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.enums import ParseMode
from database import check_user_resources, get_user_payments, get_user_trainedmodels, get_active_trainedmodel, update_user_balance, update_user_credits, check_database_user
from config import ADMIN_IDS, TARIFFS, ADMIN_PANEL_BUTTON_NAMES, ALLOWED_BROADCAST_CALLBACKS
from generation_config import GENERATION_STYLES, NEW_MALE_AVATAR_STYLES, NEW_FEMALE_AVATAR_STYLES

logger = logging.getLogger(__name__)

# Расширенные соотношения сторон
ASPECT_RATIOS = {
    "1:1": {
        "display": "1:1 📱 Квадрат",
        "description": "Идеально для Instagram постов и аватаров",
        "width": 1024,
        "height": 1024
    },
    "16:9": {
        "display": "16:9 🖥️ Широкоформатный",
        "description": "Стандарт для YouTube и презентаций",
        "width": 1920,
        "height": 1080
    },
    "4:3": {
        "display": "4:3 📺 Классический",
        "description": "Традиционный формат для фотографий",
        "width": 1024,
        "height": 768
    },
    "5:4": {
        "display": "5:4 🖼️ Альбомный",
        "description": "Отлично для печати фотографий",
        "width": 1280,
        "height": 1024
    },
    "9:16": {
        "display": "9:16 📲 Stories",
        "description": "Для Instagram Stories и TikTok",
        "width": 1080,
        "height": 1920
    },
    "9:21": {
        "display": "9:21 📱 Ультра-вертикальный",
        "description": "Для длинных вертикальных изображений",
        "width": 1080,
        "height": 2520
    },
    "3:4": {
        "display": "3:4 👤 Портретный",
        "description": "Классический портретный формат",
        "width": 768,
        "height": 1024
    },
    "4:5": {
        "display": "4:5 📖 Книжный",
        "description": "Популярный в Instagram для фото",
        "width": 1080,
        "height": 1350
    },
    "21:9": {
        "display": "21:9 🎬 Кинематографический",
        "description": "Широкий кинематографический формат",
        "width": 2560,
        "height": 1097
    },
    "2:3": {
        "display": "2:3 📷 Фото",
        "description": "Стандартный фотографический формат",
        "width": 1024,
        "height": 1536
    },
    "1.1:1": {
        "display": "1.1:1 📐 Слегка горизонтальный",
        "description": "Почти квадрат с небольшим уклоном",
        "width": 1126,
        "height": 1024
    }
}

async def create_style_selection_keyboard(generation_type: str = 'with_avatar') -> InlineKeyboardMarkup:
   
    try:
        prefix = 'admin_style' if generation_type == 'admin_with_user_avatar' else 'style'
        keyboard = [
            [
                InlineKeyboardButton(text="👤 Портрет", callback_data=f"{prefix}_portrait"),
                InlineKeyboardButton(text="😊 Повседневное", callback_data=f"{prefix}_casual")
            ],
            [
                InlineKeyboardButton(text="🎨 Художественное", callback_data=f"{prefix}_artistic"),
                InlineKeyboardButton(text="💼 Деловое", callback_data=f"{prefix}_business")
            ],
            [
                InlineKeyboardButton(text="🌅 На природе", callback_data=f"{prefix}_outdoor"),
                InlineKeyboardButton(text="🏠 В интерьере", callback_data=f"{prefix}_indoor")
            ],
            [
                InlineKeyboardButton(text="✏️ Свой промпт", callback_data=f"{prefix}_custom")
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data="back_to_generation_menu" if generation_type != 'admin_with_user_avatar' else "admin_users_list"
                )
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_style_selection_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру главного меню для пользователя."""
    try:
        admin_panel_button_text = ADMIN_PANEL_BUTTON_NAMES.get(user_id, "Админ-панель")
        logger.debug(f"Создание главного меню для user_id={user_id} с админ-кнопкой '{admin_panel_button_text}'")
        
        keyboard = [
            [InlineKeyboardButton(text="📸 Фотогенерация", callback_data="photo_generate_menu")],
            [InlineKeyboardButton(text="🎬 Видеогенерация", callback_data="video_generate_menu")],
            [InlineKeyboardButton(text="🎭 Фото Преображение", callback_data="photo_transform")],  # НОВАЯ КНОПКА
            [InlineKeyboardButton(text="👥 Мои аватары", callback_data="my_avatars")],
            [
                InlineKeyboardButton(text="👤 Личный кабинет", callback_data="user_profile"),
                InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="referrals")
            ],
            [
                InlineKeyboardButton(text="💳 Купить пакет", callback_data="subscribe"),
                InlineKeyboardButton(text="💬 Поддержка", callback_data="support")
            ],
            [InlineKeyboardButton(text="❓ Частые вопросы", callback_data="faq")]
        ]
        
        if user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton(text=admin_panel_button_text, callback_data="admin_panel")])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_main_menu_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_photo_generate_menu_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру меню фотогенерации."""
    try:
        keyboard = [
            [InlineKeyboardButton(text="📸 Фотосессия (с аватаром)", callback_data="generate_with_avatar")],
            [InlineKeyboardButton(text="🖼 Фото по референсу", callback_data="photo_to_photo")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_photo_generate_menu_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_video_generate_menu_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру меню видеогенерации."""
    try:
        keyboard = [
            [InlineKeyboardButton(text="🎬 AI-видео (Kling 2.1)", callback_data="ai_video_v2_1")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_video_generate_menu_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_video_styles_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора стилей для видеогенерации."""
    try:
        video_styles = [
            ("dynamic_action", "🏃‍♂️ Динамичное действие"),
            ("slow_motion", "🐢 Замедленное движение"),
            ("cinematic_pan", "🎥 Кинематографический панорамный вид"),
            ("facial_expression", "😊 Выразительная мимика"),
            ("object_movement", "⏳ Движение объекта"),
            ("dance_sequence", "💃 Танцевальная последовательность"),
            ("nature_flow", "🌊 Естественное течение"),
            ("urban_vibe", "🏙 Городская атмосфера"),
            ("fantasy_motion", "✨ Фантастическое движение"),
            ("retro_wave", "📼 Ретро-волна")
        ]
        
        keyboard = []
        row = []
        for style_key, style_name in video_styles:
            row.append(InlineKeyboardButton(text=style_name, callback_data=f"video_style_{style_key}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        
        keyboard.extend([
            [InlineKeyboardButton(text="✍️ Свой промпт (вручную)", callback_data="enter_custom_prompt_manual")],
            [InlineKeyboardButton(text="🤖 Свой промпт (Помощник AI)", callback_data="enter_custom_prompt_llama")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="video_generate_menu")]
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_video_styles_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_avatar_style_choice_keyboard() -> InlineKeyboardMarkup:
   
    try:
        keyboard = [
            [
                InlineKeyboardButton(text="👨 Мужчина", callback_data="select_new_male_avatar_styles"),
                InlineKeyboardButton(text="👩 Женщина", callback_data="select_new_female_avatar_styles")
            ],
            [InlineKeyboardButton(text="🔙 В меню генерации", callback_data="generate_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_avatar_style_choice_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_new_male_avatar_styles_keyboard(page: int = 1) -> InlineKeyboardMarkup:
   
    try:
        keyboard = []
        row = []
        
        styles_per_page = 20
        total_styles = len(NEW_MALE_AVATAR_STYLES)
        total_pages = (total_styles + styles_per_page - 1) // styles_per_page
        
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * styles_per_page
        end_idx = min(start_idx + styles_per_page, total_styles)
        
        styles_items = list(NEW_MALE_AVATAR_STYLES.items())
        styles_to_show = styles_items[start_idx:end_idx]
        
        for style_key, style_name in styles_to_show:
            row.append(InlineKeyboardButton(text=style_name, callback_data=f"style_new_male_{style_key}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        nav_row = []
        if total_pages > 1:
            if page > 1:
                nav_row.append(InlineKeyboardButton(text="⏮ Первая", callback_data="male_styles_page_1"))
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"male_styles_page_{page-1}"))
            
            nav_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="page_info"))
            
            if page < total_pages:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"male_styles_page_{page+1}"))
                nav_row.append(InlineKeyboardButton(text="⏭ Последняя", callback_data=f"male_styles_page_{total_pages}"))
        
        if nav_row:
            keyboard.append(nav_row)
        
        keyboard.extend([
            [InlineKeyboardButton(text="🤖 Свой промпт (Помощник AI)", callback_data="enter_custom_prompt_llama")],
            [InlineKeyboardButton(text="✍️ Свой промпт (вручную)", callback_data="enter_custom_prompt_manual")],
            [InlineKeyboardButton(text="🔙 Выбор категории", callback_data="generate_with_avatar")]
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_new_male_avatar_styles_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_new_female_avatar_styles_keyboard(page: int = 1) -> InlineKeyboardMarkup:
   
    try:
        keyboard = []
        row = []
        
        styles_per_page = 20
        total_styles = len(NEW_FEMALE_AVATAR_STYLES)
        total_pages = (total_styles + styles_per_page - 1) // styles_per_page
        
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * styles_per_page
        end_idx = min(start_idx + styles_per_page, total_styles)
        
        styles_items = list(NEW_FEMALE_AVATAR_STYLES.items())
        styles_to_show = styles_items[start_idx:end_idx]
        
        for style_key, style_name in styles_to_show:
            row.append(InlineKeyboardButton(text=style_name, callback_data=f"style_new_female_{style_key}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        nav_row = []
        if total_pages > 1:
            if page > 1:
                nav_row.append(InlineKeyboardButton(text="⏮ Первая", callback_data="female_styles_page_1"))
                nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"female_styles_page_{page-1}"))
            
            nav_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="page_info"))
            
            if page < total_pages:
                nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"female_styles_page_{page+1}"))
                nav_row.append(InlineKeyboardButton(text="⏭ Последняя", callback_data=f"female_styles_page_{total_pages}"))
        
        if nav_row:
            keyboard.append(nav_row)
        
        keyboard.extend([
            [InlineKeyboardButton(text="🤖 Свой промпт (Помощник AI)", callback_data="enter_custom_prompt_llama")],
            [InlineKeyboardButton(text="✍️ Свой промпт (вручную)", callback_data="enter_custom_prompt_manual")],
            [InlineKeyboardButton(text="🔙 Выбор категории", callback_data="generate_with_avatar")]
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_new_female_avatar_styles_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_aspect_ratio_keyboard(back_callback: str = "back_to_style_selection") -> InlineKeyboardMarkup:
   
    try:
        logger.debug(f"Создание клавиатуры соотношений сторон с back_callback={back_callback}")
        keyboard = []
        
        square_ratios = ["1:1"]
        landscape_ratios = ["16:9", "21:9", "4:3", "5:4"]
        portrait_ratios = ["9:16", "9:21", "3:4", "4:5", "2:3"]

        keyboard.append([InlineKeyboardButton(text="📐 КВАДРАТНЫЕ ФОРМАТЫ", callback_data="category_info")])
        for ratio in square_ratios:
            if ratio in ASPECT_RATIOS:
                display = f"{ratio} 📱 {'Квадрат' if ratio == 'square' else 'Квадратный'}"
                keyboard.append([InlineKeyboardButton(text=display, callback_data=f"aspect_{ratio}")])

        keyboard.append([InlineKeyboardButton(text="🖥️ ГОРИЗОНТАЛЬНЫЕ ФОРМАТЫ", callback_data="category_info")])
        row = []
        for ratio in landscape_ratios:
            if ratio in ASPECT_RATIOS:
                display = f"{ratio} 🖥️ {'Альбом' if ratio == 'landscape' else 'Горизонтальный'}"
                row.append(InlineKeyboardButton(text=display, callback_data=f"aspect_{ratio}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton(text="📱 ВЕРТИКАЛЬНЫЕ ФОРМАТЫ", callback_data="category_info")])
        row = []
        for ratio in portrait_ratios:
            if ratio in ASPECT_RATIOS:
                display = f"{ratio} 📲 {'Портрет' if ratio == 'portrait' else 'Вертикальный'}"
                row.append(InlineKeyboardButton(text=display, callback_data=f"aspect_{ratio}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
        if row:
            keyboard.append(row)

        keyboard.extend([
            [InlineKeyboardButton(text="ℹ️ Информация о форматах", callback_data="aspect_ratio_info")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        ])

        logger.debug(f"Клавиатура соотношений сторон создана успешно: {len(keyboard)} строк")
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_aspect_ratio_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_user_profile_keyboard(user_id: int, bot: Bot) -> InlineKeyboardMarkup:
   
    try:
        subscription_data = await check_database_user(user_id)
        generations_left, avatar_left = (0, 0)
        
        if subscription_data and len(subscription_data) >= 2:
            generations_left, avatar_left = subscription_data[0], subscription_data[1]
    except Exception as e:
        logger.error(f"Ошибка получения подписки в create_user_profile_keyboard для user_id={user_id}: {e}")
        generations_left, avatar_left = ('?', '?')
    
    try:
        keyboard = [
            [
                InlineKeyboardButton(
                    text=f"💰 Баланс: {generations_left} печенек, {avatar_left} аватар",
                    callback_data="check_subscription"
                )
            ],
            [InlineKeyboardButton(text="📊 Моя статистика", callback_data="user_stats")],
            [InlineKeyboardButton(text="💳 История платежей", callback_data="payment_history")],
            [InlineKeyboardButton(text="📋 Статус обучения", callback_data="check_training")],
            [InlineKeyboardButton(text="👥 Мои аватары", callback_data="my_avatars")],
            [InlineKeyboardButton(text="➕ Создать аватар", callback_data="train_flux")],
            [InlineKeyboardButton(text="📧 Изменить email", callback_data="change_email")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        ]
        
        logger.debug(f"Клавиатура личного кабинета создана для user_id={user_id}")
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_user_profile_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_avatar_selection_keyboard(user_id: int) -> InlineKeyboardMarkup:
   
    try:
        models = await get_user_trainedmodels(user_id)
        active_model_data = await get_active_trainedmodel(user_id)
        active_avatar_id = active_model_data[0] if active_model_data else None
    except Exception as e:
        logger.error(f"Ошибка получения моделей в create_avatar_selection_keyboard для user_id={user_id}: {e}")
        models = []
        active_avatar_id = None
    
    try:
        keyboard = []
        ready_avatars_exist = False
        
        if models:
            for model_tuple in models:
                if len(model_tuple) >= 9:
                    avatar_id, _, _, status, _, _, _, _, avatar_name = model_tuple[:9]
                    display_name = avatar_name if avatar_name else f"Аватар {avatar_id}"
                    
                    if status == 'success':
                        ready_avatars_exist = True
                        if avatar_id == active_avatar_id:
                            button_text = f"✅ {display_name} (активный)"
                        else:
                            button_text = f"🔘 Выбрать: {display_name}"
                        keyboard.append([
                            InlineKeyboardButton(text=button_text, callback_data=f"select_avatar_{avatar_id}")
                        ])
                else:
                    logger.warning(f"Неполные данные модели для user_id={user_id}: {model_tuple}")
        
        if not ready_avatars_exist:
            keyboard.append([InlineKeyboardButton(text="ℹ️ У вас нет готовых аватаров", callback_data="no_ready_avatars_info")])
        
        keyboard.extend([
            [InlineKeyboardButton(text="➕ Создать новый аватар", callback_data="train_flux")],
            [InlineKeyboardButton(text="📋 Статус всех аватаров", callback_data="check_training")],
            [InlineKeyboardButton(text="🔙 В личный кабинет", callback_data="user_profile")]
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_avatar_selection_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_training_keyboard(user_id: int, photo_count: int) -> InlineKeyboardMarkup:
   
    try:
        keyboard = []
        
        if photo_count >= 10:
            keyboard.append([InlineKeyboardButton(text="🚀 Начать обучение!", callback_data="confirm_start_training")])
        
        if photo_count < 20:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"📸 Добавить еще ({photo_count}/20)",
                    callback_data="continue_upload"
                )
            ])
        
        keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="user_profile")])
        
        logger.debug(f"Клавиатура обучения создана для user_id={user_id}, photo_count={photo_count}")
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_training_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_admin_keyboard(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
   
    try:
        admin_panel_button_text = ADMIN_PANEL_BUTTON_NAMES.get(user_id, "Админ-панель")
        logger.debug(f"Создание админ-клавиатуры для user_id={user_id}")
        
        keyboard = [
            [
                InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats"),
                InlineKeyboardButton(text="🔍 Поиск пользователей", callback_data="admin_search_user")
            ],
            [
                InlineKeyboardButton(text="📈 Статистика платежей", callback_data="admin_payments"),
                InlineKeyboardButton(text="📊 Активность", callback_data="admin_activity_stats")
            ],
            [
                InlineKeyboardButton(text="🔗 Рефералы", callback_data="admin_referral_stats"),
                InlineKeyboardButton(text="📉 Визуализация", callback_data="admin_visualization")
            ],
            [
                InlineKeyboardButton(text="💰 Расходы Replicate", callback_data="admin_replicate_costs"),
                InlineKeyboardButton(text="🧹 Проблемные аватары", callback_data="admin_failed_avatars")
            ],
            [
                InlineKeyboardButton(text="📢 Рассылка всем", callback_data="broadcast_all"),
                InlineKeyboardButton(text="📢 Оплатившим", callback_data="broadcast_paid")
            ],
            [
                InlineKeyboardButton(text="📢 Не оплатившим", callback_data="broadcast_non_paid"),
                InlineKeyboardButton(text="📢 Рассылка с оплатой", callback_data="broadcast_with_payment")
            ],
            [InlineKeyboardButton(text="🗂 Управление рассылками", callback_data="list_broadcasts")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        ]
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_admin_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_admin_user_actions_keyboard(target_user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    
    try:
        block_text = "🔓 Разблокировать" if is_blocked else "🔒 Заблокировать"
        block_callback = f"block_user_{target_user_id}_unblock" if is_blocked else f"block_user_{target_user_id}_block"
        
        keyboard = [
            [
                InlineKeyboardButton(text="👤 Профиль", callback_data=f"view_user_profile_{target_user_id}"),
                InlineKeyboardButton(text="🖼 Аватары", callback_data=f"user_avatars_{target_user_id}")
            ],
            [
                InlineKeyboardButton(text="📸 Генерация фото", callback_data=f"admin_generate:{target_user_id}"),
                InlineKeyboardButton(text="🎬 Генерация видео", callback_data=f"admin_video:{target_user_id}")
            ],
            [
                InlineKeyboardButton(text="💰 Баланс", callback_data=f"change_balance_{target_user_id}"),
                InlineKeyboardButton(text="📜 Логи", callback_data=f"user_logs_{target_user_id}")
            ],
            [
                InlineKeyboardButton(text="💬 Написать", callback_data=f"chat_with_user_{target_user_id}"),
                InlineKeyboardButton(text=block_text, callback_data=block_callback)
            ],
            [
                InlineKeyboardButton(text="🔄 Сброс аватаров", callback_data=f"reset_avatar_{target_user_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_user_{target_user_id}")
            ]
        ]
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_admin_user_actions_keyboard для target_user_id={target_user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_subscription_keyboard(hide_mini_tariff: bool = False) -> InlineKeyboardMarkup:
    
    try:
        keyboard = [
            [
                InlineKeyboardButton(
                    text="💎 Выберите тариф",
                    callback_data="ignore"
                )
            ]
        ]
        
        # Определяем, какие тарифы показывать
        available_tariffs = {k: v for k, v in TARIFFS.items() if k != "admin_premium"}
        if hide_mini_tariff:
            available_tariffs = {k: v for k, v in available_tariffs.items() if k != "мини"}
        
        for plan_key, plan_details in available_tariffs.items():
            keyboard.append([
                InlineKeyboardButton(
                    text=plan_details["display"],
                    callback_data=plan_details["callback"]
                )
            ])
        
        keyboard.append([
            InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu")
        ])
        
        logger.debug(f"Клавиатура тарифов создана успешно: {len(keyboard)} строк, hide_mini_tariff={hide_mini_tariff}")
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_subscription_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_rating_keyboard(
    generation_type: Optional[str] = None,
    model_key: Optional[str] = None,
    user_id: Optional[int] = None,
    bot: Optional[Bot] = None
) -> InlineKeyboardMarkup:
    
    try:
        keyboard = [
            [
                InlineKeyboardButton(text="1⭐", callback_data="rate_1"),
                InlineKeyboardButton(text="2⭐", callback_data="rate_2"),
                InlineKeyboardButton(text="3⭐", callback_data="rate_3"),
                InlineKeyboardButton(text="4⭐", callback_data="rate_4"),
                InlineKeyboardButton(text="5⭐", callback_data="rate_5")
            ],
            [
                InlineKeyboardButton(text="🔄 Повторить", callback_data="repeat_last_generation"),
                InlineKeyboardButton(text="✨ Новая генерация", callback_data="generate_menu")
            ]
        ]

        if user_id and bot:
            try:
                subscription_data = await check_user_resources(bot, user_id, required_photos=5)
                if isinstance(subscription_data, tuple) and len(subscription_data) >= 2:
                    generations_left = subscription_data[0]
                    if generations_left < 5:
                        keyboard.append([InlineKeyboardButton(text="💳 Пополнить", callback_data="subscribe")])
                else:
                    logger.warning(f"Некорректные данные подписки для user_id={user_id}: {subscription_data}")
            except Exception as e:
                logger.error(f"Ошибка проверки баланса в create_rating_keyboard для user_id={user_id}: {e}", exc_info=True)

        keyboard.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_rating_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_confirmation_keyboard(
    confirm_callback: str = "confirm_action",
    cancel_callback: str = "cancel_action",
    confirm_text: str = "✅ Да",
    cancel_text: str = "❌ Нет"
) -> InlineKeyboardMarkup:
   
    try:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=confirm_text, callback_data=confirm_callback),
                InlineKeyboardButton(text=cancel_text, callback_data=cancel_callback)
            ]
        ])
    except Exception as e:
        logger.error(f"Ошибка в create_confirmation_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_back_keyboard(
    callback_data: str = "back_to_menu",
    text: str = "🔙 Назад"
) -> InlineKeyboardMarkup:
    
    try:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=callback_data)]
        ])
    except Exception as e:
        logger.error(f"Ошибка в create_back_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_prompt_selection_keyboard(
    back_callback_data: str = "back_to_menu", 
    style_source_dict: Optional[dict] = None, 
    style_prefix: str = "style_"
) -> InlineKeyboardMarkup:
   
    try:
        keyboard = []
        row = []
        
        if not style_source_dict:
            style_source_dict = GENERATION_STYLES
        
        if not style_source_dict:
            keyboard.append([InlineKeyboardButton(text="⚠️ Стили не настроены", callback_data="no_styles_configured")])
        else:
            styles_to_show = list(style_source_dict.items())
            
            for style_key, style_name in styles_to_show:
                row.append(InlineKeyboardButton(text=style_name, callback_data=f"{style_prefix}{style_key}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            
            if row:
                keyboard.append(row)
        
        keyboard.extend([
            [InlineKeyboardButton(text="✍️ Свой промпт (вручную)", callback_data="enter_custom_prompt_manual")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback_data)]
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_prompt_selection_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_video_status_keyboard() -> InlineKeyboardMarkup:
    
    try:
        keyboard = [
            [InlineKeyboardButton(text="📋 Мои видео", callback_data="my_videos")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_video_status_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_payment_success_keyboard(user_id: int) -> InlineKeyboardMarkup:
    
    try:
        keyboard = [
            [InlineKeyboardButton(text="➕ Создать аватар", callback_data="train_flux")],
            [InlineKeyboardButton(text="✨ Сгенерировать фото", callback_data="generate_menu")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_payment_success_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_photo_upload_keyboard() -> InlineKeyboardMarkup:
    
    try:
        keyboard = [
            [InlineKeyboardButton(text="❌ Отмена загрузки", callback_data="cancel_upload")],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="help_upload")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_photo_upload_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_generation_in_progress_keyboard() -> InlineKeyboardMarkup:
   
    try:
        keyboard = [
            [InlineKeyboardButton(text="⏸ Отмена (в меню)", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_generation_in_progress_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_broadcast_keyboard() -> InlineKeyboardMarkup:
   
    try:
        keyboard = [
            [InlineKeyboardButton(text="📤 Отправить без текста", callback_data="send_broadcast_no_text")],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_broadcast_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_faq_keyboard() -> InlineKeyboardMarkup:
   
    try:
        keyboard = [
            [InlineKeyboardButton(text="📸 Как создать фото?", callback_data="faq_photo")],
            [InlineKeyboardButton(text="🎬 Как создать видео?", callback_data="faq_video")],
            [InlineKeyboardButton(text="👤 Как создать аватар?", callback_data="faq_avatar")],
            [InlineKeyboardButton(text="💡 Советы по промптам", callback_data="faq_prompts")],
            [InlineKeyboardButton(text="❓ Частые проблемы", callback_data="faq_problems")],
            [InlineKeyboardButton(text="💎 О подписке", callback_data="faq_subscription")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_faq_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_support_keyboard() -> InlineKeyboardMarkup:
    
    try:
        keyboard = [
            [InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/AXIDI_Help")],
            [InlineKeyboardButton(text="❓ Частые вопросы", callback_data="faq")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_support_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_error_keyboard() -> InlineKeyboardMarkup:
   
    try:
        keyboard = [
            [InlineKeyboardButton(text="💬 Поддержка", callback_data="support")],
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="back_to_menu")],
            [InlineKeyboardButton(text="❓ Частые вопросы", callback_data="faq")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_error_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def create_referral_keyboard(user_id: int, bot_username: str) -> InlineKeyboardMarkup:
    
    try:
        referral_link = f"t.me/{bot_username}?start=ref_{user_id}"
        
        keyboard = [
            [InlineKeyboardButton(text="🎁 Получай печеньки за друга!", callback_data="ignore")],
            [
                InlineKeyboardButton(
                    text="📤 Поделиться ссылкой",
                    url=f"https://t.me/share/url?url={referral_link}&text=Попробуй этот крутой бот для создания AI фото и видео! Получи бонусные печеньки при регистрации!"
                )
            ],
            [InlineKeyboardButton(text="📋 Скопировать ссылку", callback_data="copy_referral_link")],
            [InlineKeyboardButton(text="📊 Мои рефералы", callback_data="my_referrals")],
            [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_menu")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_referral_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])

async def send_avatar_training_message(bot, user_id: int, text: str, reply_markup=None, parse_mode=ParseMode.MARKDOWN) -> None:
   
    try:
        avatar_image_path = "/root/axidi_test/images/avatar.img"
        
        if os.path.exists(avatar_image_path):
            try:
                with open(avatar_image_path, 'rb') as photo:
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=photo,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                return
            except Exception as e:
                logger.error(f"Не удалось отправить avatar.img для user_id={user_id}: {e}", exc_info=True)
        
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.error(f"Ошибка в send_avatar_training_message для user_id={user_id}: {e}", exc_info=True)
        await bot.send_message(
            chat_id=user_id,
            text="❌ Произошла ошибка при отправке сообщения. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )
    
async def create_payment_only_keyboard(user_id: int, time_since_registration: float, days_since_registration: int, last_reminder_type: str = None, is_old_user: bool = False) -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру с кнопками оплаты для неоплативших пользователей.

    Args:
        user_id: ID пользователя
        time_since_registration: Время с момента регистрации в секундах
        days_since_registration: Количество дней с момента регистрации
        last_reminder_type: Тип последнего отправленного напоминания
        is_old_user: Флаг, указывающий, является ли пользователь старым (зарегистрирован до отсечки)

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками оплаты
    """
    try:
        # Проверяем статус оплаты
        subscription_data = await check_database_user(user_id)
        payments = await get_user_payments(user_id)
        is_paying_user = bool(payments) or (subscription_data and len(subscription_data) > 5 and not bool(subscription_data[5]))
        logger.debug(f"create_payment_only_keyboard: user_id={user_id}, is_paying_user={is_paying_user}, days_since_registration={days_since_registration}, time_since_registration={time_since_registration}, is_old_user={is_old_user}")

        if is_paying_user:
            return await create_subscription_keyboard(hide_mini_tariff=False)

        keyboard = []
        available_tariffs = {k: v for k, v in TARIFFS.items() if k != "admin_premium"}

        # Для старых пользователей показываем все тарифы
        if is_old_user:
            keyboard.extend([
                [InlineKeyboardButton(text=available_tariffs["комфорт"]["display"], callback_data="pay_1199")],
                [InlineKeyboardButton(text=available_tariffs["лайт"]["display"], callback_data="pay_599")],
                [InlineKeyboardButton(text=available_tariffs["мини"]["display"], callback_data="pay_399")],
                [InlineKeyboardButton(text=available_tariffs["аватар"]["display"], callback_data="pay_590")]
            ])
            logger.debug(f"Создана клавиатура с полным списком тарифов для старого пользователя user_id={user_id}")
        else:
            # Логика для новых пользователей: показываем один тариф
            if days_since_registration == 0:
                logger.debug(f"Day 0: time_since_registration={time_since_registration}")
                if time_since_registration <= 1800:  # До 30 минут
                    tariff_key = "комфорт"
                    callback_data = "pay_1199"
                elif time_since_registration <= 5400:  # 30–90 минут
                    tariff_key = "лайт"
                    callback_data = "pay_599"
                else:  # После 90 минут
                    tariff_key = "мини"
                    callback_data = "pay_399"
            elif days_since_registration == 1:
                tariff_key = "лайт"
                callback_data = "pay_599"
            elif 2 <= days_since_registration <= 4:
                tariff_key = "мини"
                callback_data = "pay_399"
            else:
                # Для новых пользователей с days_since_registration >= 5 показываем все тарифы
                keyboard.extend([
                    [InlineKeyboardButton(text=available_tariffs["комфорт"]["display"], callback_data="pay_1199")],
                    [InlineKeyboardButton(text=available_tariffs["лайт"]["display"], callback_data="pay_599")],
                    [InlineKeyboardButton(text=available_tariffs["мини"]["display"], callback_data="pay_399")],
                    [InlineKeyboardButton(text=available_tariffs["аватар"]["display"], callback_data="pay_590")]
                ])
                logger.debug(f"Создана клавиатура с полным списком тарифов для нового пользователя user_id={user_id} с days_since_registration={days_since_registration}")
                keyboard.append([InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu_safe")])
                keyboard.append([InlineKeyboardButton(text="ℹ️ Информация о тарифах", callback_data="tariff_info")])
                return InlineKeyboardMarkup(inline_keyboard=keyboard)

            tariff = TARIFFS.get(tariff_key)
            if not tariff:
                logger.error(f"Тариф {tariff_key} не найден для user_id={user_id}")
                return InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Ошибка", callback_data="error")]
                ])

            keyboard.append([InlineKeyboardButton(text=tariff["display"], callback_data=callback_data)])

        # Условное добавление кнопок "В меню" и "Информация о тарифах"
        generations_left = subscription_data[0] if subscription_data and len(subscription_data) > 0 else 0
        avatar_left = subscription_data[1] if subscription_data and len(subscription_data) > 1 else 0
        if generations_left > 0 or avatar_left > 0 or user_id in ADMIN_IDS:
            keyboard.append([InlineKeyboardButton(text="🔙 В меню", callback_data="back_to_menu_safe")])
            keyboard.append([InlineKeyboardButton(text="ℹ️ Информация о тарифах", callback_data="tariff_info")])
        else:
            keyboard.append([InlineKeyboardButton(text="🔐 Купи пакет для доступа", callback_data="subscribe")])

        logger.debug(f"Клавиатура оплаты создана для user_id={user_id}: days={days_since_registration}, time={time_since_registration}, is_old_user={is_old_user}")
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_payment_only_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка", callback_data="error")]
        ])
        
async def create_broadcast_with_payment_audience_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора аудитории для рассылки с кнопкой оплаты."""
    try:
        keyboard = [
            [InlineKeyboardButton(text="👥 Всем", callback_data="broadcast_with_payment_all")],
            [InlineKeyboardButton(text="💳 Оплатившим", callback_data="broadcast_with_payment_paid")],
            [InlineKeyboardButton(text="🆓 Не оплатившим", callback_data="broadcast_with_payment_non_paid")],
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
        ]
        logger.debug("Клавиатура выбора аудитории для рассылки с кнопкой оплаты создана успешно")
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_broadcast_with_payment_audience_keyboard: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Ошибка, вернуться в меню", callback_data="back_to_menu")]
        ])
        
async def create_dynamic_broadcast_keyboard(buttons: List[Dict[str, str]], user_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для рассылки на основе списка кнопок с учётом статуса оплаты пользователя."""
    try:
        keyboard = []
        row = []
        # Проверяем статус оплаты и ресурсы пользователя
        subscription_data = await check_database_user(user_id)
        payments = await get_user_payments(user_id)
        is_paying_user = bool(payments) or (subscription_data and len(subscription_data) > 5 and not bool(subscription_data[5]))
        has_resources = subscription_data and len(subscription_data) > 1 and (subscription_data[0] > 0 or subscription_data[1] > 0)
        is_admin = user_id in ADMIN_IDS

        for button in buttons[:3]:  # Ограничиваем до 3 кнопок
            button_text = button["text"][:64]  # Ограничиваем длину текста кнопки
            callback_data = button["callback_data"][:64]  # Ограничиваем длину callback
            # Заменяем все callback'и из ALLOWED_BROADCAST_CALLBACKS (кроме 'subscribe') на 'subscribe' для неоплативших без ресурсов
            if (not is_paying_user and not has_resources and not is_admin and 
                callback_data in ALLOWED_BROADCAST_CALLBACKS and callback_data != "subscribe"):
                callback_data = "subscribe"
                logger.debug(f"Заменён callback_data='{button['callback_data']}' на 'subscribe' для user_id={user_id}")
            row.append(InlineKeyboardButton(text=button_text, callback_data=callback_data))
            if len(row) == 2:  # Максимум 2 кнопки в строке
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        logger.debug(f"Создана динамическая клавиатура для user_id={user_id} с {len(buttons)} кнопками: {buttons}")
        return InlineKeyboardMarkup(inline_keyboard=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в create_dynamic_broadcast_keyboard для user_id={user_id}: {e}", exc_info=True)
        return InlineKeyboardMarkup(inline_keyboard=[])