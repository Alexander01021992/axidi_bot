"""
Улучшенный модуль для генерации изображений по одному фото через Replicate API
PixelPie AI - Фото Преображение
Версия с безопасными промптами для избежания sensitive content флагов
"""

import replicate
import asyncio
import aiohttp
import os
import logging
import base64
from typing import Optional, Dict, Any, Union, List
from datetime import datetime
from PIL import Image
import io
import re

logger = logging.getLogger(__name__)

class PhotoTransformGenerator:
    """Класс для генерации изображений по одному фото через Replicate"""
    
    def __init__(self, replicate_api_key: str):
        """
        Инициализация генератора
        
        Args:
            replicate_api_key: API ключ для Replicate
        """
        self.api_key = replicate_api_key
        os.environ["REPLICATE_API_TOKEN"] = replicate_api_key
        
        # Обновленные стили генерации с безопасными промптами
        # Избегаем слов: Hollywood, blockbuster, Blade Runner, violent, action
        self.styles = {
            "photoshop": {
                "name": "🤍 Фотошоп Pro",
                "description": "Профессиональная ретушь и улучшение",
                "prompt_template": "Professional enhanced portrait of @person, natural beauty enhancement, magazine quality retouching with preserved skin texture, soft studio lighting, authentic expression, subtle improvements, premium quality, expert color grading, sharp focus, clear details, polished look, refined appearance, high resolution clarity",
                "model": "runwayml/gen4-image",
                "model_type": "gen4"
            },
            "art": {
                "name": "🎨 AI Art",
                "description": "Цифровое искусство в стиле Artstation",
                "prompt_template": "Beautiful digital art portrait of @person, artistic painting style, vibrant colors with soft glowing effects, dreamy atmosphere with floating light particles, fantasy art aesthetic, beautiful rim lighting, concept art quality, detailed brushwork, warm color palette, ethereal beauty, professional digital illustration, creative masterpiece, gallery quality artwork",
                "model": "runwayml/gen4-image",
                "model_type": "gen4"
            },
            "cinema": {
                "name": "🎬 Кино",
                "description": "Кинематографический портрет",
                "prompt_template": "Cinematic portrait of @person, professional movie production quality, dramatic lighting setup, warm and teal color grading, atmospheric depth, film grain texture, professional camera work, emotional storytelling through lighting, moody atmosphere, artistic composition, high production value, dramatic shadows, premium visual quality",
                "model": "runwayml/gen4-image",
                "model_type": "gen4"
            },
            "portrait": {
                "name": "🖼️ Портрет",
                "description": "Психологический портрет высокого искусства",
                "prompt_template": "Intimate fine art portrait of @person, professional photography style, natural window lighting, emotional depth, medium format camera quality, authentic expression, environmental context, artistic composition, museum quality, powerful presence, timeless elegance, sophisticated mood, professional studio work",
                "model": "runwayml/gen4-image",
                "model_type": "gen4"
            },
            "fantasy": {
                "name": "⚡ Киберпанк",
                "description": "Футуристический стиль",
                "prompt_template": "Futuristic portrait of @person, cyberpunk aesthetic, neon pink and blue lighting, digital enhancement effects, urban night scene, glowing elements, tech fashion style, atmospheric fog, metallic accents, modern sci-fi mood, creative lighting, advanced technology theme, stylized future vision",
                "model": "runwayml/gen4-image",
                "model_type": "gen4"
            },
            "lego": {
                "name": "🧱 LEGO",
                "description": "Стиль конструктора LEGO",
                "prompt_template": "Toy brick construction figure style portrait of @person, plastic toy aesthetic, bright primary colors, simplified geometric shapes, playful design, clean studio lighting, collectible figure quality, fun and friendly appearance, smooth plastic texture, modular design elements, creative interpretation, family-friendly style",
                "model": "runwayml/gen4-image",
                "model_type": "gen4"
            }
        }
        
        # Альтернативные промпты для повторной попытки при sensitive content
        self.fallback_prompts = {
            "cinema": [
                "Professional film portrait of @person, cinematic lighting, movie quality, artistic direction, emotional depth, premium production",
                "Dramatic portrait of @person, professional cinematography, artistic color grading, atmospheric mood, high quality film look",
                "Movie star portrait of @person, professional lighting setup, cinematic atmosphere, elegant composition, premium quality"
            ],
            "fantasy": [
                "Future style portrait of @person, creative neon lighting, modern aesthetic, artistic interpretation, colorful atmosphere",
                "Sci-fi themed portrait of @person, creative lighting effects, futuristic fashion, artistic vision, modern style",
                "Digital age portrait of @person, contemporary lighting, tech-inspired aesthetic, creative color palette"
            ],
            "art": [
                "Artistic portrait of @person, painted style, colorful interpretation, creative brushwork, gallery quality",
                "Digital painting of @person, artistic style, warm colors, creative atmosphere, professional artwork",
                "Creative portrait of @person, artistic interpretation, beautiful colors, painted aesthetic"
            ]
        }
        
        # Доступные соотношения сторон с эмодзи
        self.aspect_ratios = {
            "9:16": "📱 9:16",
            "4:3": "📺 4:3",
            "3:4": "🖼️ 3:4",
            "1:1": "⬜ 1:1",
            "16:9": "🖥️ 16:9"
        }
        
        # Маппинг для нормализации сокращенных aspect_ratio
        self.aspect_ratio_map = {
            "9": "9:16",
            "16": "16:9",
            "4": "4:3",
            "3": "3:4",
            "1": "1:1"
        }
        
        # Доступные разрешения (только 720p для фиксации)
        self.resolutions = {
            "720p": "720p"
        }
        
        # Клиент Replicate
        self.client = replicate.Client(api_token=replicate_api_key)
        
        # Счетчик попыток для каждого пользователя
        self.user_attempts = {}
    
    def _sanitize_prompt(self, prompt: str) -> str:
        """
        Очистка промпта от потенциально проблемных слов
        
        Args:
            prompt: Исходный промпт
            
        Returns:
            Очищенный промпт
        """
        # Список проблемных слов и их замены
        replacements = {
            "Hollywood": "professional film",
            "blockbuster": "high budget production",
            "Blade Runner": "futuristic sci-fi",
            "violent": "intense",
            "action movie": "dramatic film",
            "weapon": "prop",
            "blood": "dramatic effect",
            "gore": "special effect",
            "death": "dramatic scene",
            "kill": "dramatic moment"
        }
        
        result = prompt
        for old, new in replacements.items():
            result = re.sub(old, new, result, flags=re.IGNORECASE)
        
        return result
    
    async def generate_image(self, image_bytes: bytes, style: str, user_id: int, aspect_ratio: str = "3:4", resolution: str = "720p", attempt: int = 0) -> Dict[str, Any]:
        """
        Генерация изображения в выбранном стиле с поддержкой aspect_ratio и обработкой sensitive content
        
        Args:
            image_bytes: Байты изображения
            style: Выбранный стиль генерации
            user_id: ID пользователя
            aspect_ratio: Соотношение сторон
            resolution: Разрешение (по умолчанию и фиксировано "720p")
            attempt: Номер попытки (для использования альтернативных промптов)
            
        Returns:
            Словарь с результатами генерации
        """
        try:
            logger.info(f"Начало генерации для пользователя {user_id} в стиле {style}, попытка {attempt + 1}")
            
            if style not in self.styles:
                raise ValueError(f"Неизвестный стиль: {style}")
            
            # Нормализация aspect_ratio если передан сокращенный вариант
            if aspect_ratio in self.aspect_ratio_map:
                aspect_ratio = self.aspect_ratio_map[aspect_ratio]
                logger.info(f"Нормализовали aspect_ratio к {aspect_ratio}")
            
            if aspect_ratio not in self.aspect_ratios:
                raise ValueError(f"Неподдерживаемое соотношение сторон: {aspect_ratio}")
            
            if resolution not in self.resolutions:
                raise ValueError(f"Неподдерживаемое разрешение: {resolution}")
            
            style_config = self.styles[style]
            
            # Выбор промпта в зависимости от попытки
            if attempt > 0 and style in self.fallback_prompts:
                fallback_list = self.fallback_prompts[style]
                prompt_index = min(attempt - 1, len(fallback_list) - 1)
                prompt = fallback_list[prompt_index]
                logger.info(f"Используем альтернативный промпт #{prompt_index + 1} для стиля {style}")
            else:
                prompt = style_config['prompt_template']
            
            # Дополнительная санитизация промпта
            prompt = self._sanitize_prompt(prompt)
            
            # Предобработка изображения: конвертация в JPG
            logger.info(f"Предобработка изображения: конвертация в JPG")
            try:
                img = Image.open(io.BytesIO(image_bytes))
            except Exception as pil_err:
                raise ValueError(f"Невозможно открыть изображение: {str(pil_err)}")
            
            buffer = io.BytesIO()
            img.convert('RGB').save(buffer, format="JPEG", quality=95)
            buffer.seek(0)
            processed_image_bytes = buffer.read()
            
            # Создаем data URI
            data_uri = self._create_data_uri(processed_image_bytes)
            
            # Параметры для модели
            input_params = {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "reference_tags": ["person"],
                "reference_images": [data_uri],
                "output_resolution": resolution
            }
            
            # Создаем prediction
            logger.info(f"Создание prediction для модели {style_config['model']}")
            
            prediction = await asyncio.to_thread(
                self.client.predictions.create,
                model=style_config['model'],
                input=input_params
            )
            
            logger.info(f"Prediction создан: {prediction.id}")
            
            # Ждем завершения
            while prediction.status not in ["succeeded", "failed", "canceled"]:
                await asyncio.sleep(2)
                prediction = await asyncio.to_thread(
                    self.client.predictions.get,
                    prediction.id
                )
                logger.info(f"Статус: {prediction.status}")
            
            if prediction.status == "succeeded":
                output_url = prediction.output
                
                logger.info(f"Тип output: {type(output_url)}")
                logger.info(f"Output значение: {output_url}")
                
                if isinstance(output_url, str):
                    result_url = output_url
                else:
                    result_url = str(output_url)
                    logger.info(f"Преобразовали {type(output_url)} в строку: {result_url}")
                
                logger.info(f"Генерация завершена успешно, URL: {result_url}")
                
                # Сбрасываем счетчик попыток при успехе
                if user_id in self.user_attempts:
                    del self.user_attempts[user_id]
                
                return {
                    "success": True,
                    "result_url": result_url,
                    "style": style,
                    "style_name": style_config['name'],
                    "timestamp": datetime.now().isoformat(),
                    "prediction_id": prediction.id,
                    "attempt": attempt
                }
            else:
                error_msg = f"Генерация завершилась со статусом: {prediction.status}"
                if hasattr(prediction, 'error') and prediction.error:
                    error_msg += f" - {prediction.error}"
                
                logger.error(f"Ошибка генерации: {error_msg}")
                
                # Проверяем, является ли это ошибкой sensitive content
                is_sensitive_error = any(phrase in error_msg.lower() for phrase in [
                    "sensitive", "flagged", "e005", "inappropriate", "policy"
                ])
                
                # Если это sensitive content и есть еще попытки с альтернативными промптами
                if is_sensitive_error and style in self.fallback_prompts and attempt < len(self.fallback_prompts[style]):
                    logger.info(f"Обнаружена ошибка sensitive content, пробуем альтернативный промпт")
                    # Рекурсивный вызов с увеличенным счетчиком попыток
                    return await self.generate_image(
                        image_bytes, style, user_id, aspect_ratio, resolution, attempt + 1
                    )
                
                return {
                    "success": False,
                    "error": error_msg,
                    "style": style,
                    "timestamp": datetime.now().isoformat(),
                    "is_sensitive": is_sensitive_error,
                    "attempts_made": attempt + 1
                }
                
        except Exception as e:
            logger.error(f"Ошибка генерации для пользователя {user_id}: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "style": style,
                "timestamp": datetime.now().isoformat()
            }
    
    def _create_data_uri(self, image_bytes: bytes) -> str:
        """
        Создает data URI для изображения.
        
        Args:
            image_bytes: Байты изображения
            
        Returns:
            str: data:image/jpeg;base64,BASE64STRING
        """
        b64_string = base64.b64encode(image_bytes).decode('utf-8')
        return f"data:image/jpeg;base64,{b64_string}"
    
    async def download_generated_image(self, url: str) -> bytes:
        """
        Скачивание сгенерированного изображения по public URL (без Auth).
        
        Args:
            url: Public URL изображения
            
        Returns:
            Байты изображения
        """
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        image_bytes = await response.read()
                        logger.debug(f"Первые 10 байт скачанного: {image_bytes[:10]}")
                        return image_bytes
                    else:
                        error_text = await response.text()
                        raise Exception(f"Ошибка скачивания: HTTP {response.status} - {error_text}")
        except Exception as e:
            logger.error(f"Ошибка при скачивании: {str(e)}")
            raise
    
    def get_style_keyboard(self) -> List[List[Dict[str, str]]]:
        """
        Получение клавиатуры со стилями для inline-кнопок
        
        Returns:
            Список кнопок для InlineKeyboardMarkup
        """
        keyboard = []
        
        row = []
        for style_key, style_info in self.styles.items():
            button_data = {
                "text": style_info["name"],
                "callback_data": f"transform_style:{style_key}"
            }
            row.append(button_data)
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        keyboard.append([{"text": "❌ Отменить", "callback_data": "transform_cancel"}])
        
        return keyboard
    
    def get_style_info(self, style: str) -> Optional[Dict[str, str]]:
        return self.styles.get(style)
    
    def get_style_description(self, style: str) -> str:
        """
        Обновленные описания стилей без упоминания проблемных брендов
        """
        descriptions = {
            "photoshop": """🤍 Фотошоп Pro / Журнальная ретушь

Превращаем твое фото в обложку премиум-журнала! 
✨ Идеальная кожа с естественным сиянием
✨ Профессиональная цветокоррекция 
✨ Улучшение черт лица и контуров
✨ Отбеливание зубов и блеск в глазах
✨ Удаление всех недостатков

📌 Результат: Ты, но в лучшей версии себя
⚡ PixelPie AI сделает тебя звездой глянца!""",
            
            "art": """🎨 AI Art / Цифровое искусство

Твой портрет в стиле топовых цифровых художников!
🎨 Живописные мазки и текстуры
🎨 Волшебное свечение и частицы
🎨 Яркие, сочные цвета
🎨 Фэнтези атмосфера
🎨 Качество цифрового шедевра

📌 Результат: Арт для NFT или принта на холсте
🎨 PixelPie AI превратит фото в произведение искусства!""",
            
            "cinema": """🎬 Кино / Кинематографический портрет

Ты — звезда большого кино!
🎬 Профессиональная кинематография
🎬 Драматичный свет премиум-качества
🎬 Эффект дорогой камеры
🎬 Атмосфера большого кино
🎬 Качество постера

📌 Результат: Кадр из фильма высокого класса
🎥 PixelPie AI сделает тебя кинозвездой!""",
            
            "portrait": """🖼️ Портрет / Fine Art Photography

Психологический портрет музейного уровня
📸 Глубина и характер в каждой детали
📸 Классическое освещение для объема
📸 Эмоциональная подача
📸 Музейное качество
📸 Вневременная классика

📌 Результат: Портрет для галереи или выставки
📸 PixelPie AI раскроет твою душу в кадре!""",
            
            "fantasy": """⚡ Киберпанк / Футуристический стиль

Добро пожаловать в будущее!
⚡ Неоновая подсветка розовый/синий
⚡ Футуристические элементы
⚡ Отражения ночного мегаполиса
⚡ Световые эффекты
⚡ Атмосфера научной фантастики

📌 Результат: Ты — житель города будущего
🚀 PixelPie AI отправит тебя в киберпанк-приключение!""",
            
            "lego": """🧱 LEGO / Конструктор

Стань коллекционной фигуркой конструктора!
🧱 Точные пропорции игрушки
🧱 Яркий пластиковый стиль
🧱 Детализация блочной фигурки
🧱 Игрушечный дизайн
🧱 Качество коллекционной модели

📌 Результат: Эксклюзивная фигурка конструктора
🧱 PixelPie AI создаст твою игрушечную версию!"""
        }
        
        return descriptions.get(style, "Описание недоступно")
    
    def get_aspect_ratio_keyboard(self, style_key: str) -> List[List[Dict[str, str]]]:
        keyboard = []
    
        # Все форматы в столбик с подписями
        keyboard.append([{"text": "📱 9:16 • Вертикальный (Stories)", "callback_data": f"transform_ratio:{style_key}:9:16"}])
        keyboard.append([{"text": "🖼️ 3:4 • Портрет (Классика)", "callback_data": f"transform_ratio:{style_key}:3:4"}])
        keyboard.append([{"text": "⬜ 1:1 • Квадрат (Instagram)", "callback_data": f"transform_ratio:{style_key}:1:1"}])
        keyboard.append([{"text": "📺 4:3 • Стандарт (Универсальный)", "callback_data": f"transform_ratio:{style_key}:4:3"}])
        keyboard.append([{"text": "🖥️ 16:9 • Широкий (YouTube)", "callback_data": f"transform_ratio:{style_key}:16:9"}])
    
        keyboard.append([{"text": "❌ Отменить", "callback_data": "transform_cancel"}])
    
        return keyboard
    
    def get_resolution_keyboard(self, style_key: str, aspect_ratio: str) -> List[List[Dict[str, str]]]:
        keyboard = []
        row = []
        for res_key in self.resolutions:
            button_data = {
                "text": f"{res_key}",
                "callback_data": f"transform_resolution:{style_key}:{aspect_ratio}:{res_key}"
            }
            row.append(button_data)
        
        if row:
            keyboard.append(row)
        
        keyboard.append([{"text": "❌ Отменить", "callback_data": "transform_cancel"}])
        
        return keyboard
    
    def get_aspect_ratio_description(self) -> str:
        return """
📐 Выберите формат изображения:

📱 9:16 — Вертикальный
Идеально для Stories, Reels, TikTok.

🖼️ 3:4 — Портрет  
Классика для фото, удобно для печати.

⬜ 1:1 — Квадрат
Фото, аватарки.

📺 4:3 — Стандартный
Универсальный формат для всего

🖥️ 16:9 — Широкоэкранный
YouTube, обложки, баннеры

Выберите подходящий формат для вашего шедевра! ✨
"""
    
    def get_sensitive_content_message(self) -> str:
        """
        Сообщение для пользователя при ошибке sensitive content
        """
        return """
⚠️ Система безопасности заблокировала генерацию

Возможные причины:
• На фото есть элементы, которые система считает неподходящими
• Выбранный стиль конфликтует с политикой безопасности

Что делать:
1. Попробуйте другое фото
2. Выберите другой стиль генерации
3. Убедитесь, что на фото нет запрещенного контента

Печенька возвращена на баланс! 🍪

Попробуйте еще раз с другим фото или стилем.
"""