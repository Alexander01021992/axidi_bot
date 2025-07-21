# states.py
from aiogram.fsm.state import State, StatesGroup

class BotStates(StatesGroup):
    AWAITING_BROADCAST_MESSAGE = State()
    AWAITING_BROADCAST_MEDIA_CONFIRM = State()
    AWAITING_BROADCAST_CONFIRM = State()
    AWAITING_BROADCAST_SCHEDULE = State()
    AWAITING_BROADCAST_MANAGE_ACTION = State()
    AWAITING_BROADCAST_DELETE_CONFIRM = State()
    AWAITING_PAYMENT_DATES = State()
    AWAITING_USER_SEARCH = State()
    AWAITING_BALANCE_CHANGE = State()
    AWAITING_ACTIVITY_DATES = State()
    AWAITING_ADMIN_PROMPT = State()
    AWAITING_BLOCK_REASON = State()
    AWAITING_CONFIRM_QUALITY = State()
    AWAITING_STYLE_SELECTION = State()
    AWAITING_DELETE_REASON = State()
    AWAITING_VIDEO_PROMPT = State()
    AWAITING_VIDEO_STYLE = State()  # Состояние для выбора стиля видео
    AWAITING_BROADCAST_AUDIENCE = State()
    AWAITING_BROADCAST_BUTTONS = State()  # Новое состояние для выбора количества кнопок
    AWAITING_BROADCAST_BUTTON_INPUT = State()  # Новое состояние для ввода данных кнопки

class VideoStates(StatesGroup):
    AWAITING_VIDEO_PROMPT = State()
    AWAITING_VIDEO_PHOTO = State()
    AWAITING_VIDEO_CONFIRMATION = State()  # Новое состояние для подтверждения

class TrainingStates(StatesGroup):
    AWAITING_CONFIRMATION = State()