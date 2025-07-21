import os
from dotenv import load_dotenv
import sys
from typing import Dict, Any, List, Optional
import logging

# Загрузка переменных окружения из файла .env
load_dotenv()

# === ОСНОВНЫЕ НАСТРОЙКИ ===
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
REPLICATE_API_TOKEN = os.getenv('REPLICATE_API_TOKEN')
YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID')
YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY')
REPLICATE_USERNAME_OR_ORG_NAME = os.getenv('REPLICATE_USERNAME_OR_ORG_NAME', 'axidiagensy')

# Алиасы для совместимости
BOT_TOKEN = TOKEN
TELEGRAM_BOT_TOKEN = TOKEN
BOT_USERNAME = "axidi_test_bot"
BOT_URL = f"https://t.me/{BOT_USERNAME}"
YOOKASSA_RETURN_URL = BOT_URL
YOOKASSA_WEBHOOK_SECRET = os.getenv('YOOKASSA_SECRET', '')
REFERRAL_REWARD_AMOUNT = 10

# === НАСТРОЙКИ БЕЗОПАСНОСТИ ===
RATE_LIMIT_MAX_REQUESTS = int(os.getenv('RATE_LIMIT_MAX_REQUESTS', '50'))
RATE_LIMIT_WINDOW_MINUTES = int(os.getenv('RATE_LIMIT_WINDOW_MINUTES', '1'))
MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', '50'))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# === НАСТРОЙКИ ПРОИЗВОДИТЕЛЬНОСТИ ===
CACHE_TTL_SECONDS = int(os.getenv('CACHE_TTL_SECONDS', '300'))
DATABASE_PATH = os.getenv('DATABASE_PATH', 'users.db')
BACKUP_ENABLED = os.getenv('BACKUP_ENABLED', 'True').lower() == 'true'
BACKUP_INTERVAL_HOURS = int(os.getenv('BACKUP_INTERVAL_HOURS', '24'))

# Webhook настройки
WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://axidiphoto.ru/webhook')

# Ограничение на количество одновременных задач
MAX_CONCURRENT_TASKS = 200
MAX_CONCURRENT_GENERATIONS = 10

# === ID АДМИНИСТРАТОРОВ ===
ADMIN_IDS = [706527960, 1154672896, 444593004, 331123326, 7787636839]

ADMIN_PANEL_BUTTON_NAMES = {
    706527960: " ✅ Панель Админа!",
    1154672896: "⚠️ Анальная ПриборПанель ⚠️!"
    # Добавьте другие user_id и названия при необходимости
}

# Состояния для FSM
AWAITING_BROADCAST_MESSAGE = 1
AWAITING_BROADCAST_CONFIRM = 2
AWAITING_PAYMENT_DATES = 3
AWAITING_USER_SEARCH = 4
AWAITING_BALANCE_CHANGE = 5
AWAITING_BROADCAST_SCHEDULE = 6
AWAITING_ACTIVITY_DATES = 7
AWAITING_ADMIN_PROMPT = 8
AWAITING_BLOCK_REASON = 9
AWAITING_CONFIRM_QUALITY = 10
AWAITING_STYLE_SELECTION = 11
AWAITING_CUSTOM_PROMPT_MANUAL = 12
AWAITING_CUSTOM_PROMPT_LLaMA = 13
AWAITING_PHOTO = 14
AWAITING_VIDEO_PROMPT = 15
AWAITING_MASK = 16
AWAITING_AVATAR_NAME = 17
AWAITING_TRIGGER_WORD = 18

# === ВАЛИДАЦИЯ КОНФИГУРАЦИИ ===
def validate_config():
    """Полная валидация конфигурации при запуске"""
    errors = []
    warnings = []
    
    required_vars = {
        'TOKEN': ('Telegram Bot Token', TOKEN),
        'REPLICATE_API_TOKEN': ('Replicate API Token', REPLICATE_API_TOKEN),
        'YOOKASSA_SHOP_ID': ('YooKassa Shop ID', YOOKASSA_SHOP_ID),
        'YOOKASSA_SECRET_KEY': ('YooKassa Secret Key', YOOKASSA_SECRET_KEY)
    }
    
    for var_name, (description, value) in required_vars.items():
        if not value:
            errors.append(f"Missing {description} ({var_name})")
    
    if not REPLICATE_USERNAME_OR_ORG_NAME or REPLICATE_USERNAME_OR_ORG_NAME == 'your-replicate-username':
        warnings.append(
            f"REPLICATE_USERNAME_OR_ORG_NAME не установлен корректно (текущее значение: '{REPLICATE_USERNAME_OR_ORG_NAME}'). "
            "Обучение моделей может не работать."
        )
    
    if errors:
        print("❌ КРИТИЧЕСКИЕ ОШИБКИ КОНФИГУРАЦИИ:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    
    if warnings:
        print("⚠️ ПРЕДУПРЕЖДЕНИЯ КОНФИГУРАЦИИ:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("✅ Конфигурация проверена успешно")

# Вызов функции после её определения
validate_config()

# === НАСТРОЙКА YOOKASSA ===
YOOKASSA_ENABLED = bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)

if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    try:
        from yookassa import Configuration
        Configuration.account_id = YOOKASSA_SHOP_ID
        Configuration.secret_key = YOOKASSA_SECRET_KEY
        print("✅ YooKassa настроена успешно")
    except ImportError:
        print("⚠️ Библиотека yookassa не установлена. Платежи не будут работать.")
    except Exception as e:
        print(f"❌ Ошибка конфигурации YooKassa: {e}")

# Доступные callback'и для динамических кнопок рассылки
ALLOWED_BROADCAST_CALLBACKS = [
    'back_to_menu',
    'photo_generate_menu',
    'video_generate_menu',
    'user_profile',
    'subscribe',
    'tariff_info',
    'my_avatars',
    'train_flux',
    'check_training'
]

# Алиасы для callback'ов рассылки
BROADCAST_CALLBACK_ALIASES = {
    'menu': 'back_to_menu',
    'photo': 'photo_generate_menu',
    'video': 'video_generate_menu',
    'profile': 'user_profile',
    'tariff': 'subscribe',
    'tariff_info': 'tariff_info',
    'avatars': 'my_avatars',
    'train': 'train_flux',
    'check': 'check_training'
}
# === ТАРИФЫ ===
TARIFFS = {
    "мини": {
        "name": "💎 Мини",
        "amount": 399.00,
        "price": 399.00,
        "photos": 10,
        "avatars": 0,
        "videos": 0,
        "display": "💎 399₽ за 10 Печенек",
        "callback": "pay_399",
        "description": "Отлично для начинающих"
    },
    "лайт": {
        "name": "⚡ Лайт",
        "amount": 599.00,
        "price": 599.00,
        "photos": 30,
        "avatars": 0,
        "videos": 0,
        "display": "💎 599₽ за 30 Печенек",
        "callback": "pay_599",
        "description": "Популярный выбор",
        "popular": True
    },
    "комфорт": {
        "name": "🌟 Комфорт",
        "amount": 1199.00,
        "price": 1199.00,
        "photos": 70,
        "avatars": 0,
        "videos": 0,
        "display": "💎 1199₽ за 70 Печенек",
        "callback": "pay_1199",
        "description": "Оптимальное соотношение"
    },
    "премиум": {
        "name": "👑 Премиум",
        "amount": 3199.00,
        "price": 3199.00,
        "photos": 170,
        "avatars": 0,
        "videos": 0,
        "display": "💎 3199₽ за 170 Печенек",
        "callback": "pay_3199",
        "description": "Максимальная выгода",
        "popular": True
    },
    "платина": {
        "name": "💎 Платина",
        "amount": 4599.00,
        "price": 4599.00,
        "photos": 340,
        "avatars": 0,
        "videos": 0,
        "display": "💎 4599₽ за 340 Печенек",
        "callback": "pay_4599",
        "description": "Для профессионалов"
    },
    "аватар": {
        "name": "👤 Аватар",
        "amount": 590.00,
        "price": 590.00,
        "photos": 0,
        "avatars": 1,
        "videos": 0,
        "display": "💎 590₽ за 1 аватар",
        "callback": "pay_590",
        "description": "Создание персонального аватара"
    },
    "admin_premium": {
        "name": "🔧 Admin Test",
        "amount": 0.00,
        "price": 0.00,
        "photos": 10,
        "avatars": 1,
        "videos": 5,
        "display": "Admin Test Package",
        "callback": "admin_give_premium",
        "description": "Тестовый пакет для администраторов"
    }
}

# === НАСТРОЙКИ ДЛЯ СТАТИСТИКИ ===
STATS_UPDATE_INTERVAL = 3600
METRICS_RETENTION_DAYS = 90

# === ЭКСПОРТ КОНСТАНТ ДЛЯ МЕТРИК ===
METRICS_CONFIG = {
    'user_actions': [
        'start_bot',
        'generate_image',
        'train_avatar',
        'make_payment',
        'rate_generation',
        'use_referral'
    ],
    'generation_types': [],  # Будет заполнено из generation_config
    'payment_plans': list(TARIFFS.keys())
}

# === РЕФЕРАЛЬНАЯ СИСТЕМА ===
REFERRAL_BONUS_PHOTOS = 10
REFERRAL_BONUS_FOR_REFERRER = 5

# === НАСТРОЙКИ УВЕДОМЛЕНИЙ ===
NOTIFICATION_HOUR = 12
TIMEZONE = 'Europe/Moscow'

# === НАСТРОЙКИ АНТИСПАМА ===
ANTISPAM_MESSAGE_LIMIT = 10
ANTISPAM_GENERATION_LIMIT = 5

# === ТЕКСТЫ СООБЩЕНИЙ ===
WELCOME_MESSAGE = """
👋 Добро пожаловать в AI PixelPie МИР ТВОРЧЕСТВА!
Я создаю изображения ультра-высокого разрешения с профессиональной детализацией.
🎨 Что я умею:
• Генерировать изображения МАКСИМАЛЬНОГО качества
• Создавать персональные аватары с фотореализмом
• Преобразовывать ваши фото с ультра-детализацией
• Использовать профессиональные модели
⚡ МАКСИМАЛЬНЫЕ параметры:
• Шаги inference (на максимум)
• Разрешение высокого качества БЕЗ сжатия
• Фото формат без потерь
• Профессиональные модели генерации
Используйте Меню для выбора следующего шага!
"""

HELP_TEXT = """
📖 Помощь по генерации:

🎨 Фотосессии - Создать изображение
👤 Мои Аватары - Управление аватарами
💎 Тарифы - Купить подписку
👥 Пригласить - Реферальная программа
❓ Помощь - Показать справку

💡 Советы для МАКСИМАЛЬНОГО качества:
• Используйте детальные описания
• Указывайте технические параметры камеры
• Экспериментируйте с профессиональными стилями
• Все изображения генерируются в максимальном качестве

🎯 УЛЬТРА-НАСТРОЙКИ:
• Фотогенерация (максимум)
• Высокая обработка(лучший для детализации)
• Фото без сжатия
• Профессиональные модели

Поддержка: @AXIDI_Help
"""

# === СООБЩЕНИЯ ОБ ОШИБКАХ ===
ERROR_MESSAGES = {
    'no_subscription': '❌ У вас закончились генерации. Пожалуйста, оформите подписку /subscribe',
    'generation_failed': '❌ Произошла ошибка при генерации. Попробуйте еще раз.',
    'invalid_photo': '❌ Неверный формат фото. Поддерживаются только JPG и PNG.',
    'file_too_large': f'❌ Файл слишком большой. Максимальный размер: {MAX_FILE_SIZE_MB} МБ',
    'rate_limit': '⚠️ Слишком много запросов. Подождите немного.',
    'maintenance': '🔧 Бот на техническом обслуживании. Попробуйте позже.'
}

# === ЭКСПОРТ КОНСТАНТ ===
__all__ = [
    'TOKEN', 'ADMIN_IDS', 'DATABASE_PATH', 'BOT_URL', 'WEBHOOK_URL',
    'YOOKASSA_SHOP_ID', 'YOOKASSA_SECRET_KEY', 'YOOKASSA_RETURN_URL', 'YOOKASSA_ENABLED',
    'REPLICATE_API_TOKEN', 'REPLICATE_USERNAME_OR_ORG_NAME',
    'TARIFFS', 'MAX_FILE_SIZE_BYTES', 'CACHE_TTL_SECONDS', 'BACKUP_ENABLED',
    'BACKUP_INTERVAL_HOURS', 'ADMIN_PANEL_BUTTON_NAMES', 'WELCOME_MESSAGE',
    'HELP_TEXT', 'MAX_CONCURRENT_GENERATIONS', 'validate_config',
    'REFERRAL_BONUS_PHOTOS', 'REFERRAL_BONUS_FOR_REFERRER', 'NOTIFICATION_HOUR',
    'TIMEZONE', 'ANTISPAM_MESSAGE_LIMIT', 'ANTISPAM_GENERATION_LIMIT',
    'ERROR_MESSAGES', 'STATS_UPDATE_INTERVAL', 'METRICS_RETENTION_DAYS',
    'METRICS_CONFIG', 'RATE_LIMIT_MAX_REQUESTS', 'RATE_LIMIT_WINDOW_MINUTES',
    'MAX_CONCURRENT_TASKS', 'AWAITING_BROADCAST_MESSAGE', 'AWAITING_BROADCAST_CONFIRM',
    'AWAITING_PAYMENT_DATES', 'AWAITING_USER_SEARCH', 'AWAITING_BALANCE_CHANGE',
    'AWAITING_BROADCAST_SCHEDULE', 'AWAITING_ACTIVITY_DATES', 'AWAITING_ADMIN_PROMPT',
    'AWAITING_BLOCK_REASON', 'AWAITING_CONFIRM_QUALITY', 'AWAITING_STYLE_SELECTION',
    'AWAITING_CUSTOM_PROMPT_MANUAL', 'AWAITING_CUSTOM_PROMPT_LLaMA', 'AWAITING_PHOTO',
    'AWAITING_VIDEO_PROMPT', 'AWAITING_MASK', 'AWAITING_AVATAR_NAME',
    'AWAITING_TRIGGER_WORD'
]

print("🚀 ОСНОВНАЯ КОНФИГУРАЦИЯ БОТА ЗАГРУЖЕНА!")