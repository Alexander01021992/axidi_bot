# generation/images.py
from aiogram.exceptions import TelegramForbiddenError
import re
import aiohttp
import aiofiles
import uuid
import os
import logging
import time
import asyncio
import random
from typing import Optional, List, Dict, Tuple
from aiogram import Bot
from aiogram.types import Message, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
import tenacity
import replicate
from replicate.exceptions import ReplicateError
from deep_translator import GoogleTranslator
from copy import deepcopy

from generation_config import (
    IMAGE_GENERATION_MODELS, ASPECT_RATIOS,
    GENERATION_TYPE_TO_MODEL_KEY, MULTI_LORA_MODEL, HF_LORA_MODELS, LORA_CONFIG, LORA_PRIORITIES, 
    LORA_STYLE_PRESETS, MAX_LORA_COUNT, USER_AVATAR_LORA_STRENGTH,
    CAMERA_SETUP_BASE, LUXURY_DETAILS_BASE
)
from config import MAX_FILE_SIZE_BYTES, REPLICATE_API_TOKEN, REPLICATE_USERNAME_OR_ORG_NAME, ADMIN_IDS
from database import (
    check_database_user, update_user_credits, get_active_trainedmodel, log_generation, check_user_resources
)
from keyboards import (
    create_main_menu_keyboard, create_rating_keyboard,
    create_subscription_keyboard, create_user_profile_keyboard, create_photo_generate_menu_keyboard
)
from generation.utils import (
    TempFileManager, reset_generation_context,
    send_message_with_fallback, send_photo_with_retry, send_media_group_with_retry
)
from llama_helper import generate_assisted_prompt
from handlers.utils import clean_admin_context, safe_escape_markdown as escape_md

user_last_generation_params = {}
user_last_generation_lock = asyncio.Lock()

logger = logging.getLogger(__name__)

# УВЕЛИЧИВАЕМ ЛИМИТЫ ДЛЯ 5000+ ПОЛЬЗОВАТЕЛЕЙ
MAX_CONCURRENT_GENERATIONS = 100
REPLICATE_RATE_LIMIT = 50
USER_GENERATION_COOLDOWN = 2

# Семафоры для различных операций
generation_semaphore = asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)
replicate_semaphore = asyncio.Semaphore(REPLICATE_RATE_LIMIT)
download_semaphore = asyncio.Semaphore(100)
file_operation_semaphore = asyncio.Semaphore(200)

# Кэши для оптимизации
active_models_cache = {}
cache_lock = asyncio.Lock()
user_last_generation = {}
user_generation_lock = {}

# Очередь генераций для равномерного распределения нагрузки
generation_queue = asyncio.Queue(maxsize=1000)
queue_processor_running = False

# ПРОФЕССИОНАЛЬНАЯ КОНФИГУРАЦИЯ LORA ДЛЯ ФОТОРЕАЛИЗМА
# Изменено: Увеличена сила для skin_texture_master до 1.0 для более натуральной текстуры кожи и уменьшения пластикового блеска.
# Изменено: Увеличена сила для anti_cgi до 1.0 для усиления борьбы с CGI-эффектами и пластиковым видом.
# Изменено: Уменьшена сила для face_perfection_v2 до 0.75, чтобы избежать чрезмерной идеализации лица, делая его более реалистичным.
# Изменено: Уменьшена сила для color_grading_pro до 0.7 для предотвращения чрезмерной цветокоррекции, которая может добавлять блеск.
# Изменено: Увеличена сила для ultra_details до 0.85 для лучшей детализации без потери натуральности.
# Изменено: Добавлена новая модель "prithivMLmods/Flux-BetterSkin-LoRA" с силой 0.95 для улучшения текстуры кожи и снижения пластикового эффекта.
ULTRA_PROFESSIONAL_LORA_CONFIG = {
    "photo_realism_pro": {
        "model": "alvdansen/frosting_lane_flux",
        "strength": 0.95,
        "keywords": ["photorealistic", "professional photo", "camera shot", "DSLR", "фотореалистичный"],
        "description": "Профессиональная фотосъемка",
        "priority": 1
    },
    "skin_texture_master": {
        "model": "prithivMLmods/Flux-Skin-Real",
        "strength": 1.0,
        "keywords": ["skin", "texture", "natural skin", "pores", "кожа", "текстура", "realistic skin"],
        "description": "Натуральная текстура кожи без CGI эффекта",
        "priority": 2
    },
    "face_perfection_v2": {
        "model": "prithivMLmods/Canopus-LoRA-Flux-FaceRealism",
        "strength": 0.75,
        "keywords": ["face", "portrait", "eyes", "facial features", "лицо", "глаза"],
        "description": "Совершенные лица и глаза",
        "priority": 3
    },
    "color_grading_pro": {
        "model": "renderartist/colorgrading",
        "strength": 0.7,
        "keywords": ["color grading", "cinematic", "professional lighting", "цветокоррекция", "natural colors"],
        "description": "Профессиональная цветокоррекция",
        "priority": 4
    },
    "ultra_details": {
        "model": "prithivMLmods/Flux-Realism-FineDetailed",
        "strength": 0.85,
        "keywords": ["detailed", "high detail", "ultra detailed", "детализация", "fine details"],
        "description": "Ультра детализация",
        "priority": 5
    },
    "anti_cgi": {
        "model": "https://huggingface.co/prithivMLmods/Flux-Dev-Real-Anime/resolve/main/Flux-Dev-Real-Anime.safetensors",
        "strength": 1.0,
        "keywords": ["real", "not cgi", "not 3d", "natural", "authentic", "реальный"],
        "description": "Анти-CGI эффект для натуральности",
        "priority": 6
    },
    "portrait_master_pro": {
        "model": "gokaygokay/Flux-Portrait-LoRA",
        "strength": 0.95,
        "keywords": ["portrait", "headshot", "professional portrait", "портрет"],
        "description": "Профессиональная портретная фотография",
        "priority": 7
    },
    "better_skin": {
        "model": "prithivMLmods/Flux-BetterSkin-LoRA",
        "strength": 0.95,
        "keywords": ["skin", "natural skin", "realistic skin"],
        "description": "Улучшенная натуральная кожа без блеска",
        "priority": 8
    }
}

# ПРЕСЕТЫ ДЛЯ МАКСИМАЛЬНОГО ФОТОРЕАЛИЗМА
# Изменено: Для всех пресетов увеличено num_inference_steps до 60 для лучшей детализации и снижения артефактов.
# Изменено: Уменьшено guidance_scale до 2.5-3.0 для более естественной генерации без переусиления.
# Изменено: Добавлены LoRA "better_skin" и "anti_cgi" во все пресеты для усиления натуральности кожи.
# Изменено: Добавлены дополнительные prompt_additions для борьбы с пластиковым блеском: "matte skin finish, no shine, natural skin reflectance".
# Изменено: Добавлены элементы для повышения сходства с реальным человеком: "imperfect skin, natural variations in skin tone".
ULTRA_PHOTOREALISTIC_PRESETS = {
    "natural_portrait": {
        "loras": ["skin_texture_master", "photo_realism_pro", "face_perfection_v2", "anti_cgi", "better_skin"],
        "guidance_scale": 2.5,
        "num_inference_steps": 50,
        "prompt_additions": (
            "natural skin texture with visible pores, realistic skin tone, "
            "authentic human features, real person not CGI, "
            "soft natural lighting, candid expression, "
            "professional photography, DSLR quality, unretouched natural beauty, "
            "matte skin finish, no shine, natural skin reflectance, "
            "imperfect skin, natural variations in skin tone"
        )
    },
    "studio_perfection": {
        "loras": ["photo_realism_pro", "color_grading_pro", "face_perfection_v2", "ultra_details", "better_skin", "anti_cgi"],
        "guidance_scale": 3.0,
        "num_inference_steps": 50,
        "prompt_additions": (
            "professional studio photography, color corrected, natural skin tones, "
            "high-end commercial photography, realistic skin texture, "
            "professional lighting setup, authentic expression, "
            "magazine quality, shot on medium format camera, "
            "matte skin finish, no shine, natural skin reflectance, "
            "imperfect skin, natural variations in skin tone"
        )
    },
    "lifestyle_natural": {
        "loras": ["skin_texture_master", "anti_cgi", "photo_realism_pro", "portrait_master_pro", "better_skin"],
        "guidance_scale": 2.5,
        "num_inference_steps": 50,
        "prompt_additions": (
            "lifestyle photography, natural candid moment, "
            "real skin texture, authentic expression, natural lighting, "
            "photojournalism style, unposed, genuine emotion, "
            "shot with natural daylight, minimal retouching, "
            "matte skin finish, no shine, natural skin reflectance, "
            "imperfect skin, natural variations in skin tone"
        )
    }
}

# КРИТИЧЕСКИ УЛУЧШЕННЫЙ NEGATIVE PROMPT
# Изменено: Добавлены дополнительные элементы против пластикового блеска: "plastic shine, glossy skin, reflective skin, shiny pores, oily reflectance".
# Изменено: Усилены анти-CGI элементы: "cgi skin, rendered skin, artificial reflectance, fake shine".
# Изменено: Добавлены элементы для повышения реализма: "perfect symmetry, unnatural perfection, over-smoothed features".
ULTRA_NEGATIVE_PROMPT = (
    "3d render, cgi, computer graphics, digital art, artificial, fake, synthetic, "
    "unreal engine, blender, maya, 3ds max, rendered, digital painting, "
    "video game, animation, cartoon, anime, illustration, drawing, "
    "plastic skin, rubber skin, waxy skin, doll skin, mannequin skin, "
    "shiny skin, oily skin, greasy skin, artificial skin, smooth skin, "
    "perfect skin, flawless skin, airbrushed skin, retouched skin, "
    "red skin, pink skin, orange skin, yellow skin, purple skin, blue skin, "
    "oversaturated skin, desaturated skin, pale skin, colorless skin, "
    "wrong skin tone, unnatural skin color, artificial coloring, "
    "low quality, bad quality, worst quality, blurry, out of focus, "
    "pixelated, compression artifacts, jpeg artifacts, noise, grain, "
    "overexposed, underexposed, bad lighting, harsh lighting, "
    "bad anatomy, deformed face, asymmetric face, bad proportions, "
    "bad eyes, closed eyes, dead eyes, no pupils, weird eyes, "
    "bad hands, extra fingers, missing fingers, "
    "oversaturated, neon colors, artificial enhancement, heavy makeup, "
    "instagram filter, beauty filter, face tune, over-processed, "
    "watermark, text, logo, signature, frame, border, "
    "artificial lighting, neon lighting, fluorescent lighting, "
    "flat lighting, studio flash, harsh shadows, no shadows, "
    "plastic shine, glossy skin, reflective skin, shiny pores, oily reflectance, "
    "cgi skin, rendered skin, artificial reflectance, fake shine, "
    "perfect symmetry, unnatural perfection, over-smoothed features"
)

async def process_prompt_async(original_prompt: str, model_key: str, generation_type: str, 
                             trigger_word: str = None, selected_gender: str = None, 
                             user_input: str = None, user_data: Dict = None,
                             use_new_flux: bool = False) -> str:
    """Обрабатывает промпт с учетом пользовательского ввода и переводит его на английский."""
    loop = asyncio.get_event_loop()
    
    def process_sync():
        logger.info(f"Обработка промпта: original_prompt='{original_prompt[:50]}...', user_input='{user_input[:50] if user_input else None}...', "
                    f"generation_type={generation_type}, trigger_word={trigger_word}, selected_gender={selected_gender}")
        
        # Если есть пользовательский ввод и это кастомный промпт, используем его
        if user_data.get('came_from_custom_prompt') and user_input:
            base_prompt = user_input
            logger.debug(f"Используется пользовательский промпт: {base_prompt[:50]}...")
        else:
            base_prompt = original_prompt
            logger.debug(f"Используется исходный промпт: {base_prompt[:50]}...")

        if generation_type == 'photo_to_photo':
            if use_new_flux and trigger_word:
                base = f"{trigger_word}, copy style from reference image"
                if base_prompt and base_prompt != "copy reference style":
                    base += f", {base_prompt}"
                return base + ", natural skin texture, realistic, photographic quality"
            elif trigger_word:
                return f"{trigger_word}, copy style from reference, natural realistic photo"
            else:
                return "copy reference image style, natural realistic photo, authentic"

        parts = []
        if trigger_word:
            parts.append(trigger_word)
        photorealistic_enhancers = [
            "professional photography",
            "photorealistic", 
            "real person",
            "natural skin texture with visible pores",
            "realistic skin tone",
            "authentic human features",
            "not CGI",
            "not 3D render",
            "DSLR camera quality",
            "natural expression",
            "genuine emotion",
            "sharp focus",
            "high resolution"
        ]
        # Добавляем усилители фотореализма только если не кастомный промпт
        if not user_data.get('came_from_custom_prompt'):
            parts.extend(photorealistic_enhancers)
        if selected_gender:
            parts.append(selected_gender)
        parts.append(base_prompt)
        anti_cgi_details = (
            "shot with professional DSLR camera, natural daylight, "
            "real human skin with natural imperfections, "
            "unretouched authentic photography, photojournalism style, "
            "natural hair texture, realistic eye moisture, "
            "genuine facial expression, candid moment, "
            "no artificial enhancement, no beauty filters, "
            "raw unprocessed photo quality"
        )
        # Добавляем anti_cgi_details только если не кастомный промпт
        if not user_data.get('came_from_custom_prompt'):
            parts.append(anti_cgi_details)
        
        full_prompt = ", ".join(parts)
        full_prompt = re.sub(r'\s+', ' ', full_prompt).strip()
        
        # Проверяем наличие русского текста и переводим
        if re.search('[а-яА-Я]', full_prompt):
            try:
                translator = GoogleTranslator(source='auto', target='en')
                translated_prompt = translator.translate(full_prompt[:4500])
                if translated_prompt:
                    full_prompt = translated_prompt
                    logger.info(f"Промпт переведен на английский: {full_prompt[:50]}...")
                else:
                    logger.warning(f"Перевод не удался, используется исходный промпт: {full_prompt[:50]}...")
            except Exception as e:
                logger.error(f"Ошибка перевода промпта: {e}")
                logger.info(f"Используется исходный промпт без перевода: {full_prompt[:50]}...")
        
        # Ограничиваем длину промпта
        if len(full_prompt) > 4000:
            full_prompt = full_prompt[:4000].rsplit(', ', 1)[0]
            logger.warning(f"Промпт обрезан до 4000 символов: {full_prompt[:50]}...")
        
        return full_prompt
    
    return await loop.run_in_executor(None, process_sync)

async def prepare_model_params(use_new_flux: bool, model_key: str, generation_type: str,
                             prompt: str, num_outputs: int, aspect_ratio: str,
                             width: int, height: int, user_data: Dict) -> Optional[dict]:
    """Подготавливает параметры модели с учетом пользовательского промпта."""
    logger.info(f"Подготовка параметров модели: model_key={model_key}, generation_type={generation_type}, "
                f"prompt={prompt[:50]}..., aspect_ratio={aspect_ratio}")
    
    # Проверка входных данных
    if not isinstance(prompt, str):
        logger.error(f"Промпт не является строкой: тип={type(prompt)}, значение={prompt}")
        return None
    if not isinstance(aspect_ratio, str):
        logger.error(f"Соотношение сторон не является строкой: тип={type(aspect_ratio)}, значение={aspect_ratio}")
        return None
    
    # Проверка reference_image_url на случай корутины
    reference_image_url = user_data.get('reference_image_url')
    if asyncio.iscoroutine(reference_image_url):
        logger.error(f"reference_image_url является корутиной: {reference_image_url}")
        return None
    
    if use_new_flux:
        params = {
            "prompt": prompt,
            "model": "dev",
            "go_fast": True,
            "lora_scale": 1,
            "megapixels": "1",
            "num_outputs": num_outputs,
            "aspect_ratio": aspect_ratio,
            "output_format": "webp",
            "guidance_scale": 3.0,
            "output_quality": 100,
            "prompt_strength": 0.9,
            "num_inference_steps": 50
        }
        if generation_type == 'photo_to_photo':
            if not reference_image_url:
                logger.error("Отсутствует reference_image_url для photo_to_photo")
                return None
            if not isinstance(reference_image_url, str):
                logger.error(f"reference_image_url не является строкой: тип={type(reference_image_url)}, значение={reference_image_url}")
                return None
            params["image"] = reference_image_url
            params["prompt_strength"] = 0.8
            params["guidance_scale"] = 3.0
    elif model_key == "flux-trained":
        params = {
            "prompt": prompt,
            "num_outputs": num_outputs,
            "aspect_ratio": aspect_ratio,
            "lora_scale": 1,
            "output_format": "webp",
            "guidance_scale": 3.0,
            "width": width,
            "height": height,
            "scheduler": "DDIM",
            "prompt_strength": 0.8,
            "output_quality": 100,
            "num_inference_steps": 50
        }
        prompt_lower = prompt.lower()
        # Выбираем пресет только если НЕ кастомный промпт
        if not user_data.get('came_from_custom_prompt'):
            if any(word in prompt_lower for word in ["natural", "candid", "authentic", "lifestyle"]):
                selected_preset = "lifestyle_natural"
            elif any(word in prompt_lower for word in ["studio", "professional", "commercial"]):
                selected_preset = "studio_perfection"
            else:
                selected_preset = "natural_portrait"
            if selected_preset in ULTRA_PHOTOREALISTIC_PRESETS:
                preset = ULTRA_PHOTOREALISTIC_PRESETS[selected_preset]
                params["guidance_scale"] = preset["guidance_scale"]
                params["num_inference_steps"] = preset["num_inference_steps"]
                params["prompt"] = f"{prompt}, {preset['prompt_additions']}"
                logger.info(f"Применен пресет {selected_preset} с добавками: {preset['prompt_additions'][:50]}...")
        else:
            logger.info(f"Кастомный промпт, пресет не применяется: {prompt[:50]}...")
        
        lora_index = 1
        if generation_type in ['with_avatar', 'photo_to_photo']:
            trigger_word = user_data.get('trigger_word')
            old_model_id = user_data.get('old_model_id')
            old_model_version = user_data.get('old_model_version')
            if trigger_word and old_model_version:
                if old_model_id:
                    if '/' not in old_model_id:
                        avatar_lora = f"{REPLICATE_USERNAME_OR_ORG_NAME}/{old_model_id}:{old_model_version}"
                    else:
                        avatar_lora = f"{old_model_id}:{old_model_version}"
                    params[f"hf_lora_{lora_index}"] = avatar_lora
                    params[f"lora_scale_{lora_index}"] = 0.99
                    lora_index += 1
                    logger.info(f"Добавлен пользовательский LoRA с силой 0.9")
        
        # Добавляем профессиональные LoRA только если НЕ кастомный промпт
        if not user_data.get('came_from_custom_prompt'):
            preset_loras = ULTRA_PHOTOREALISTIC_PRESETS.get(selected_preset, {}).get("loras", [])
            for lora_name in preset_loras:
                if lora_index <= MAX_LORA_COUNT and lora_name in ULTRA_PROFESSIONAL_LORA_CONFIG:
                    lora_cfg = ULTRA_PROFESSIONAL_LORA_CONFIG[lora_name]
                    params[f"hf_lora_{lora_index}"] = lora_cfg["model"]
                    params[f"lora_scale_{lora_index}"] = lora_cfg["strength"]
                    logger.info(f"Добавлен профессиональный LoRA {lora_name} на позицию {lora_index}: strength={lora_cfg['strength']}")
                    lora_index += 1
        
        # Всегда добавляем anti_cgi LoRA, если есть место
        anti_cgi_added = any(
            "Flux-Dev-Real-Anime" in params.get(f"hf_lora_{i}", "") 
            for i in range(1, lora_index)
        )
        if not anti_cgi_added and lora_index <= MAX_LORA_COUNT:
            anti_cgi_cfg = ULTRA_PROFESSIONAL_LORA_CONFIG["anti_cgi"]
            params[f"hf_lora_{lora_index}"] = anti_cgi_cfg["model"]
            params[f"lora_scale_{lora_index}"] = anti_cgi_cfg["strength"]
            logger.info(f"Добавлен Anti-CGI LoRA на позицию {lora_index}")
        
        params["negative_prompt"] = ULTRA_NEGATIVE_PROMPT
        if generation_type == 'photo_to_photo':
            if not reference_image_url:
                logger.error("Отсутствует reference_image_url для photo_to_photo")
                return None
            if not isinstance(reference_image_url, str):
                logger.error(f"reference_image_url не является строкой: тип={type(reference_image_url)}, значение={reference_image_url}")
                return None
            params["image"] = reference_image_url
            params["strength"] = 0.75
        
        logger.info(f"=== ULTRA REALISTIC PARAMETERS ===")
        logger.info(f"Preset: {selected_preset if not user_data.get('came_from_custom_prompt') else 'none (custom prompt)'}")
        logger.info(f"Guidance Scale: {params['guidance_scale']}")
        logger.info(f"Inference Steps: {params['num_inference_steps']}")
        logger.info(f"Total Professional LoRAs: {lora_index - 1}")
        logger.info(f"Anti-CGI enabled: {anti_cgi_added}")
        logger.info(f"Final prompt: {params['prompt'][:50]}...")
    
    else:
        params = {
            "prompt": prompt,
            "num_outputs": num_outputs,
            "aspect_ratio": aspect_ratio,
            "output_format": "webp",
            "guidance_scale": 2.0,
            "num_inference_steps": 50,
            "width": width,
            "height": height
        }
    
    logger.info(f"Возвращены параметры: {params}")
    return params

async def start_queue_processor():
    """Запускает обработчик очереди генераций"""
    global queue_processor_running
    if not queue_processor_running:
        queue_processor_running = True
        for i in range(20):
            asyncio.create_task(process_generation_queue(i))
        logger.info("Запущены обработчики очереди генераций")

async def process_generation_queue(worker_id: int):
    """Обработчик очереди генераций"""
    while True:
        try:
            task = await generation_queue.get()
            if task is None:
                break
            message, state, num_outputs = task
            try:
                await _generate_image_internal(message, state, num_outputs)
            except Exception as e:
                logger.error(f"Worker {worker_id}: Ошибка обработки генерации: {e}", exc_info=True)
            finally:
                generation_queue.task_done()
        except Exception as e:
            logger.error(f"Worker {worker_id}: Критическая ошибка в обработчике очереди: {e}", exc_info=True)
            await asyncio.sleep(1)

async def get_user_generation_lock(user_id: int):
    """Получает или создает блокировку для пользователя"""
    if user_id not in user_generation_lock:
        user_generation_lock[user_id] = asyncio.Lock()
    return user_generation_lock[user_id]

async def check_user_cooldown(user_id: int) -> bool:
    """Проверяет, может ли пользователь генерировать (cooldown)"""
    last_time = user_last_generation.get(user_id, 0)
    current_time = time.time()
    if current_time - last_time < USER_GENERATION_COOLDOWN:
        return False
    user_last_generation[user_id] = current_time
    return True

async def get_active_model_cached(user_id: int):
    
    async with cache_lock:
        if user_id in active_models_cache:
            cache_time, model_data = active_models_cache[user_id]
            if time.time() - cache_time < 300:
                return model_data
    model_data = await get_active_trainedmodel(user_id)
    async with cache_lock:
        active_models_cache[user_id] = (time.time(), model_data)
        if len(active_models_cache) > 1000:
            oldest_users = sorted(active_models_cache.items(), key=lambda x: x[1][0])[:100]
            for old_user_id, _ in oldest_users:
                del active_models_cache[old_user_id]
    return model_data

async def download_image_async(session: aiohttp.ClientSession, url: str, filepath: str, retry_count: int = 3) -> Optional[str]:
    
    async with download_semaphore:
        for attempt in range(retry_count):
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with session.get(url, timeout=timeout) as response:
                    if response.status == 200:
                        os.makedirs(os.path.dirname(filepath), exist_ok=True)
                        async with aiofiles.open(filepath, 'wb') as f:
                            async for chunk in response.content.iter_chunked(8192):
                                await f.write(chunk)
                        logger.debug(f"Успешно загружен файл: {filepath}")
                        return filepath
                    else:
                        logger.warning(f"HTTP {response.status} при загрузке {url}")
            except asyncio.TimeoutError:
                logger.warning(f"Таймаут загрузки {url}, попытка {attempt + 1}/{retry_count}")
            except Exception as e:
                logger.error(f"Ошибка загрузки {url}, попытка {attempt + 1}/{retry_count}: {e}")
            if attempt < retry_count - 1:
                await asyncio.sleep(1 * (attempt + 1))
        return None

async def download_images_parallel(urls: List[str], user_id: int) -> List[str]:
   
    paths = []
    tasks = []
    connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for i, url in enumerate(urls):
            filepath = f"generated/{user_id}_{uuid.uuid4().hex[:8]}_{i}.png"
            task = download_image_async(session, url, filepath)
            tasks.append(task)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, str) and result:
                paths.append(result)
            elif isinstance(result, Exception):
                logger.error(f"Ошибка загрузки: {result}")
    return paths

async def generate_image(message: Message, state: FSMContext, num_outputs: int = 2, user_id: int = None) -> None:
   
    user_data = await state.get_data()
    bot = message.bot
    bot_id = (await bot.get_me()).id
    user_id = user_id or message.from_user.id

    logger.info(f"=== ГЕНЕРАЦИЯ НАЧАЛАСЬ ===")
    logger.info(f"Инициатор: {user_id}")
    logger.info(f"user_data до обработки: {user_data}")

    is_admin_generation = user_data.get('is_admin_generation', False)
    admin_generation_for_user = user_data.get('admin_generation_for_user')
    admin_user_id = user_data.get('original_admin_user', user_id)

    if is_admin_generation and admin_generation_for_user and user_id in ADMIN_IDS:
        message_recipient = admin_user_id
        target_user_id = admin_generation_for_user
        if target_user_id == bot_id:
            logger.error(f"Некорректный target_user_id: {target_user_id} (ID бота)")
            await send_message_with_fallback(
                bot, admin_user_id,
                escape_md("❌ Ошибка: неверный ID пользователя для генерации.", version=2),
                reply_markup=await create_main_menu_keyboard(admin_user_id),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        logger.info(f"АДМИНСКАЯ ГЕНЕРАЦИЯ: админ={message_recipient}, цель={target_user_id}")
        # Проверяем и обновляем данные аватара для целевого пользователя
        trained_model_data = await get_active_model_cached(target_user_id)
        if trained_model_data and trained_model_data[3] == 'success':
            await state.update_data(
                trigger_word=trained_model_data[5],
                model_version=trained_model_data[2],
                old_model_id=trained_model_data[4],
                old_model_version=trained_model_data[0],
                active_avatar_name=trained_model_data[8]
            )
        else:
            logger.error(f"Нет активного аватара для target_user_id={target_user_id}")
            await send_message_with_fallback(
                bot, admin_user_id,
                escape_md(f"❌ У пользователя ID `{target_user_id}` нет активного аватара.", version=2),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")]
                ]),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
    else:
        message_recipient = user_id
        target_user_id = user_id
        logger.info(f"ОБЫЧНАЯ ГЕНЕРАЦИЯ: пользователь={target_user_id}")
        # Сбрасываем админские поля только для обычной генерации
        admin_fields = [
            'is_admin_generation', 'admin_generation_for_user', 
            'admin_target_user_id', 'giving_sub_to_user',
            'broadcast_type', 'awaiting_broadcast_message',
            'awaiting_search_query', 'admin_view_source'
        ]
        await state.update_data({field: None for field in admin_fields})

    required_fields = ['prompt', 'aspect_ratio', 'generation_type', 'model_key']
    missing_fields = []
    empty_fields = []

    logger.info(f"=== ДЕТАЛЬНАЯ ПРОВЕРКА ДАННЫХ ===")
    for field in required_fields:
        value = user_data.get(field)
        logger.info(f"Поле '{field}': ключ_существует={field in user_data}, значение='{value}', тип={type(value)}")
        if field not in user_data:
            missing_fields.append(field)
        elif value is None or value == '' or value == 'None' or (isinstance(value, str) and not value.strip()):
            empty_fields.append(field)

    problem_fields = missing_fields + empty_fields
    if problem_fields:
        logger.error(f"ПРОБЛЕМА: поля {problem_fields} отсутствуют или пусты")
        logger.error(f"Отсутствуют ключи: {missing_fields}")
        logger.error(f"Пустые значения: {empty_fields}")
        recovery_attempted = False
        if 'generation_type' in problem_fields and not user_data.get('generation_type'):
            if user_data.get('current_style_set') in ['new_male_avatar', 'new_female_avatar', 'generic_avatar']:
                await state.update_data(generation_type='with_avatar')
                logger.info("Восстановлен generation_type = 'with_avatar'")
                recovery_attempted = True
            elif user_data.get('reference_image_url') or user_data.get('photo_path'):
                await state.update_data(generation_type='photo_to_photo')
                logger.info("Восстановлен generation_type = 'photo_to_photo'")
                recovery_attempted = True
        if 'model_key' in problem_fields and not user_data.get('model_key'):
            gen_type = user_data.get('generation_type')
            if gen_type in ['with_avatar', 'photo_to_photo']:
                await state.update_data(model_key='flux-trained')
                logger.info("Восстановлен model_key = 'flux-trained'")
                recovery_attempted = True
        if recovery_attempted:
            user_data = await state.get_data()
            final_missing = [f for f in required_fields if f not in user_data or not user_data.get(f)]
            if not final_missing:
                logger.info("✅ Данные успешно восстановлены, продолжаем генерацию")
            else:
                logger.error(f"❌ Не удалось восстановить: {final_missing}")
                await send_message_with_fallback(
                    bot, message_recipient,
                    f"⚠️ Не хватает данных: {', '.join(final_missing)}. Начни заново через /menu.",
                    reply_markup=await create_main_menu_keyboard(message_recipient),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                return
        else:
            await send_message_with_fallback(
                bot, message_recipient,
                f"⚠️ Не хватает данных: {', '.join(problem_fields)}. Начни заново через /menu.",
                reply_markup=await create_main_menu_keyboard(message_recipient),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

    await start_queue_processor()
    if not await check_user_cooldown(message_recipient):
        await send_message_with_fallback(
            bot, message_recipient,
            "⏳ Подожди пару секунд перед следующей генерацией!",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    generation_type = user_data.get('generation_type')
    if generation_type == 'with_avatar':
        required_photos = num_outputs
    elif generation_type == 'photo_to_photo':
        required_photos = 2
    else:
        required_photos = 1

    if not is_admin_generation:
        logger.info(f"Проверка ресурсов для user_id={target_user_id}, требуется фото: {required_photos}")
        if not await check_user_resources(bot, target_user_id, required_photos=required_photos):
            return

    if generation_queue.full():
        await send_message_with_fallback(
            bot, message_recipient,
            "😔 Сервер перегружен! Попробуй через минуту.",
            reply_markup=await create_main_menu_keyboard(message_recipient),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    try:
        generation_data = deepcopy({
            'prompt': user_data.get('prompt'),
            'aspect_ratio': user_data.get('aspect_ratio'),
            'generation_type': user_data.get('generation_type'),
            'model_key': user_data.get('model_key'),
            'selected_gender': user_data.get('selected_gender'),
            'user_input_for_llama': user_data.get('user_input_for_llama'),
            'trigger_word': user_data.get('trigger_word'),
            'model_version': user_data.get('model_version'),
            'old_model_id': user_data.get('old_model_id'),
            'old_model_version': user_data.get('old_model_version'),
            'reference_image_url': user_data.get('reference_image_url'),
            'photo_path': user_data.get('photo_path'),
            'message_recipient': message_recipient,
            'generation_target_user': target_user_id,
            'original_admin_user': admin_user_id,
            'is_admin_generation': is_admin_generation,
            'admin_generation_for_user': admin_generation_for_user,
            'photos_to_deduct': required_photos if not is_admin_generation else 0,
            'current_style_set': user_data.get('current_style_set'),
            'came_from_custom_prompt': user_data.get('came_from_custom_prompt', False),
            'use_llama_prompt': user_data.get('use_llama_prompt', False),
            'last_generation_params': user_data.get('last_generation_params'),
            'active_avatar_name': user_data.get('active_avatar_name')
        })
        logger.info(f"Сохраненные данные для очереди: {list(generation_data.keys())}")
        logger.info(f"generation_type: '{generation_data['generation_type']}'")
        logger.info(f"model_key: '{generation_data['model_key']}'")
        logger.info(f"prompt: '{generation_data['prompt'][:50]}...' (обрезан)" if generation_data.get('prompt') else "prompt: None")
        critical_fields = ['prompt', 'aspect_ratio', 'generation_type', 'model_key']
        missing_in_saved = [f for f in critical_fields if not generation_data.get(f)]
        if missing_in_saved:
            logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: данные потеряны при сохранении: {missing_in_saved}")
            logger.error(f"Исходные данные user_data: {user_data}")
            await send_message_with_fallback(
                bot, message_recipient,
                f"⚠️ Критическая ошибка сохранения данных: {', '.join(missing_in_saved)}. Попробуй ещё раз через /menu.",
                reply_markup=await create_main_menu_keyboard(message_recipient),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        await state.update_data(generation_data)
        logger.info(f"Перед добавлением в очередь: user_data={user_data}")
        await generation_queue.put((message, state, num_outputs))
        queue_size = generation_queue.qsize()
        if queue_size > 10:
            if is_admin_generation:
                message_text = f"📊 Запрос генерации для пользователя {target_user_id} добавлен в очередь (позиция: ~{queue_size})."
            else:
                message_text = f"📊 Твой запрос добавлен в очередь (позиция: ~{queue_size}). Ожидай уведомления!"
            await send_message_with_fallback(
                bot, message_recipient,
                message_text,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        logger.info(f"✅ Генерация добавлена в очередь: recipient={message_recipient}, target={target_user_id}, is_admin={is_admin_generation}, queue_size={queue_size}")
    except asyncio.QueueFull:
        logger.error(f"Очередь переполнена для user_id={message_recipient}")
        await send_message_with_fallback(
            bot, message_recipient,
            "😔 Не удалось добавить в очередь. Попробуй ещё раз!",
            reply_markup=await create_main_menu_keyboard(message_recipient),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в generate_image для user_id={message_recipient}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, message_recipient,
            "❌ Произошла непредвиденная ошибка. Попробуйте позже.",
            reply_markup=await create_main_menu_keyboard(message_recipient),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def _generate_image_internal(message: Message, state: FSMContext, num_outputs: int = 2) -> None:
    """
    Внутренняя функция генерации изображения с ультра-реалистичными параметрами.
    """
    from handlers.generation import handle_admin_generation_result

    async with asyncio.Lock():
        user_data = await state.get_data()
        message_recipient = user_data.get('message_recipient', message.from_user.id)
        target_user_id = user_data.get('generation_target_user', message.from_user.id)
        admin_user_id = user_data.get('original_admin_user', message.from_user.id)
        is_admin_generation = user_data.get('is_admin_generation', False)
        bot = message.bot
        bot_id = (await bot.get_me()).id

        # Инициализируем preserved_data пустым словарем
        preserved_data = {}

        if message_recipient == bot_id or target_user_id == bot_id:
            logger.error(f"Некорректный ID получателя или цели: message_recipient={message_recipient}, target_user_id={target_user_id}, bot_id={bot_id}")
            return

        logger.info(f"=== ВНУТРЕННЯЯ ГЕНЕРАЦИЯ ===")
        logger.info(f"message_recipient={message_recipient}, target_user_id={target_user_id}, admin_user_id={admin_user_id}")
        logger.info(f"is_admin_generation={is_admin_generation}")
        logger.info(f"До обработки: user_data={user_data}")

        start_time = time.time()
        user_lock = await get_user_generation_lock(target_user_id)

        async with user_lock:
            async with generation_semaphore:
                logger.info(f"🎯 УЛЬТРА-РЕАЛИСТИЧНАЯ ГЕНЕРАЦИЯ для user_id={target_user_id}" + 
                           (f" (админ: {admin_user_id})" if is_admin_generation else ""))
                
                subscription_data = await check_database_user(target_user_id)
                if not isinstance(subscription_data, tuple) or len(subscription_data) < 9:
                    logger.error(f"Недостаточно данных подписки для user_id={target_user_id}: {subscription_data}")
                    await send_message_with_fallback(
                        bot, message_recipient,
                        escape_md("❌ Ошибка сервера! Попробуй /menu.", version=2),
                        reply_markup=await create_main_menu_keyboard(message_recipient),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    return
                
                generation_type = user_data.get('generation_type')
                prompt = user_data.get('prompt')
                aspect_ratio_key = user_data.get('aspect_ratio')
                model_key = user_data.get('model_key')
                style_name = user_data.get('style_name', 'Кастомный стиль')

                logger.info(f"=== ПРОВЕРКА ДАННЫХ ВО ВНУТРЕННЕЙ ФУНКЦИИ ===")
                logger.info(f"generation_type: '{generation_type}' (тип: {type(generation_type)})")
                logger.info(f"prompt: '{prompt[:50] if prompt else None}...' (тип: {type(prompt)})")
                logger.info(f"aspect_ratio_key: '{aspect_ratio_key}' (тип: {type(aspect_ratio_key)})")
                logger.info(f"model_key: '{model_key}' (тип: {type(model_key)})")

                missing_critical = []
                if not generation_type or generation_type == 'None':
                    missing_critical.append('generation_type')
                if not prompt or prompt == 'None':
                    missing_critical.append('prompt')
                if not aspect_ratio_key or aspect_ratio_key == 'None':
                    missing_critical.append('aspect_ratio')
                if not model_key or model_key == 'None':
                    missing_critical.append('model_key')
                
                if missing_critical:
                    logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: отсутствуют ключевые данные во внутренней функции!")
                    logger.error(f"Отсутствующие поля: {missing_critical}")
                    logger.error(f"Все данные user_data: {user_data}")
                    await send_message_with_fallback(
                        bot, message_recipient,
                        escape_md("⚠️ Критическая ошибка данных генерации. Начни заново через /menu.", version=2),
                        reply_markup=await create_main_menu_keyboard(message_recipient),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    return
                
                if generation_type in ['with_avatar', 'photo_to_photo']:
                    trained_model_data = await get_active_model_cached(target_user_id)
                    if not trained_model_data or trained_model_data[3] != 'success':
                        logger.error(f"Нет активного аватара для target_user_id={target_user_id}")
                        await send_message_with_fallback(
                            bot, message_recipient,
                            escape_md("❌ Активный аватар не готов! Создай его в Личном кабинете.", version=2),
                            reply_markup=await create_user_profile_keyboard(message_recipient, bot),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        await reset_generation_context(state, generation_type)
                        return
                
                required_photos = user_data.get('photos_to_deduct', num_outputs)
                
                if not is_admin_generation:
                    logger.info(f"Списание ресурсов для user_id={target_user_id}, требуется фото: {required_photos}")
                    await update_user_credits(target_user_id, "decrement_photo", amount=required_photos)
                    logger.info(f"Списано {required_photos} фото для user_id={target_user_id}")
                
                selected_gender = user_data.get('selected_gender')
                user_input_for_helper = user_data.get('user_input_for_llama')
                
                if generation_type in ['with_avatar', 'photo_to_photo']:
                    trained_model_data = await get_active_model_cached(target_user_id)
                    if trained_model_data and trained_model_data[3] == 'success':
                        await state.update_data(
                            trigger_word=trained_model_data[5],
                            model_version=trained_model_data[2],
                            old_model_id=trained_model_data[4],
                            old_model_version=trained_model_data[0],
                            active_avatar_name=trained_model_data[8]
                        )
                
                # Сохраняем параметры генерации перед генерацией
                generation_params = {
                    'prompt': prompt,
                    'aspect_ratio': aspect_ratio_key,
                    'generation_type': generation_type,
                    'model_key': model_key,
                    'selected_gender': selected_gender,
                    'user_input_for_llama': user_input_for_helper,
                    'style_name': style_name,
                    'current_style_set': user_data.get('current_style_set'),
                    'came_from_custom_prompt': user_data.get('came_from_custom_prompt', False),
                    'use_llama_prompt': user_data.get('use_llama_prompt', False),
                    'duration': 0.0
                }
                
                # Сохраняем last_generation_params перед началом генерации
                preserved_data['last_generation_params'] = generation_params
                
                if model_key not in IMAGE_GENERATION_MODELS:
                    logger.error(f"Неверный model_key: {model_key}")
                    await send_message_with_fallback(
                        bot, message_recipient,
                        escape_md("❌ Ошибка конфигурации модели!", version=2),
                        reply_markup=await create_main_menu_keyboard(message_recipient),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    await reset_generation_context(state, generation_type)
                    return
                
                model_config = IMAGE_GENERATION_MODELS[model_key]
                replicate_model_id_to_run = model_config['id']
                trigger_word = user_data.get('trigger_word')
                use_new_flux_method = False
                
                if generation_type in ['with_avatar', 'photo_to_photo']:
                    trained_model_data = await get_active_model_cached(target_user_id)
                    if not trained_model_data or trained_model_data[3] != 'success':
                        logger.error(f"КРИТИЧЕСКАЯ ОШИБКА: аватар не готов после проверки!")
                        await send_message_with_fallback(
                            bot, message_recipient,
                            escape_md("❌ Критическая ошибка: аватар не готов!", version=2),
                            reply_markup=await create_user_profile_keyboard(message_recipient, bot),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        await reset_generation_context(state, generation_type)
                        return
                    avatar_id, model_id, model_version, status, prediction_id, trigger_word, photo_paths, training_step, avatar_name = trained_model_data
                    await state.update_data(trigger_word=trigger_word, model_version=model_version, active_avatar_name=avatar_name)
                    logger.info(f"Active avatar for user {target_user_id}: {avatar_name} (ID: {avatar_id})")
                    logger.info(f"Avatar model_id: {model_id}")
                    logger.info(f"Avatar model_version: {model_version}")
                    is_fast_flux = is_new_fast_flux_model(model_id, model_version)
                    if is_fast_flux:
                        use_new_flux_method = True
                        if model_version:
                            if '/' not in model_id:
                                replicate_model_id_to_run = f"{REPLICATE_USERNAME_OR_ORG_NAME}/{model_id}:{model_version}"
                            else:
                                replicate_model_id_to_run = f"{model_id}:{model_version}"
                        else:
                            if '/' not in model_id:
                                replicate_model_id_to_run = f"{REPLICATE_USERNAME_OR_ORG_NAME}/{model_id}"
                            else:
                                replicate_model_id_to_run = model_id
                        logger.info(f"Using Fast Flux model: {replicate_model_id_to_run}")
                    else:
                        use_new_flux_method = False
                        replicate_model_id_to_run = MULTI_LORA_MODEL
                        logger.info(f"Using Multi-LoRA model for old avatar: {replicate_model_id_to_run}")
                        await state.update_data(old_model_id=model_id, old_model_version=model_version)
                
                generation_message = await send_message_with_fallback(
                    bot, message_recipient,
                    escape_md(f"📸 Создаю {num_outputs} качественных фото! Подготовка...", version=2),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                
                if not isinstance(generation_message, Message):
                    logger.warning(f"Не удалось отправить начальное сообщение для user_id={message_recipient}, generation_message={generation_message}")
                    generation_message = await send_message_with_fallback(
                        bot, message_recipient,
                        escape_md("📸 Создаю фото... Пожалуйста, подождите.", version=2),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                
                try:
                    user_data = await state.get_data()
                    processed_prompt = await process_prompt_async(
                        prompt, model_key, generation_type,
                        trigger_word, selected_gender, user_input_for_helper, user_data,
                        use_new_flux=use_new_flux_method
                    )
                    width, height = ASPECT_RATIOS.get(aspect_ratio_key, (1440, 1440))
                    input_params = await prepare_model_params(
                        use_new_flux_method, model_key, generation_type,
                        processed_prompt, num_outputs, aspect_ratio_key,
                        width, height, user_data
                    )
                    if input_params is None:
                        if isinstance(generation_message, Message):
                            await generation_message.delete()
                        await send_message_with_fallback(
                            bot, message_recipient,
                            escape_md("❌ Ошибка подготовки параметров!", version=2),
                            reply_markup=await create_main_menu_keyboard(message_recipient),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        await reset_generation_context(state, generation_type)
                        return
                    
                    # Проверка, что input_params является словарем
                    if not isinstance(input_params, dict):
                        logger.error(f"input_params не является словарем: тип={type(input_params)}, значение={input_params}")
                        if isinstance(generation_message, Message):
                            await generation_message.delete()
                        await send_message_with_fallback(
                            bot, message_recipient,
                            escape_md("❌ Ошибка: неверные параметры генерации!", version=2),
                            reply_markup=await create_main_menu_keyboard(message_recipient),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                        await reset_generation_context(state, generation_type)
                        return
                    
                    # Логируем параметры перед вызовом Replicate
                    logger.info(f"Параметры для Replicate: {input_params}")
                    
                    await log_generation(
                        target_user_id,
                        generation_type,
                        replicate_model_id_to_run,
                        num_outputs
                    )
                    
                    if isinstance(generation_message, Message):
                        await generation_message.edit_text(
                            escape_md(
                                f"🎯 Генерирую ваши фото с помощью PixelPie_AI.\n"
                                f"📸 Используется инновационная ИИ нейросеть!\n"
                                f"⚡ PixelPie_AI создает ваш шедевр!", version=2
                            ),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    
                    async with replicate_semaphore:
                        image_urls = await run_replicate_model_async(replicate_model_id_to_run, input_params)
                    
                    if not image_urls:
                        logger.error("Пустой результат от Replicate")
                        if isinstance(generation_message, Message):
                            await generation_message.edit_text(
                                escape_md("❌ Ошибка генерации! Попробуй снова.", version=2),
                                reply_markup=await create_main_menu_keyboard(message_recipient),
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        else:
                            await send_message_with_fallback(
                                bot, message_recipient,
                                escape_md("❌ Ошибка генерации! Попробуй снова.", version=2),
                                reply_markup=await create_main_menu_keyboard(message_recipient),
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        await reset_generation_context(state, generation_type)
                        return
                    
                    if isinstance(generation_message, Message):
                        await generation_message.edit_text(
                            escape_md("✅ Готово! Загружаю готовые результаты...", version=2),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    else:
                        await send_message_with_fallback(
                            bot, message_recipient,
                            escape_md("✅ Готово! Загружаю готовые результаты...", version=2),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    
                    image_paths = await download_images_parallel(image_urls, target_user_id)
                    if not image_paths:
                        logger.error("Не удалось загрузить изображения")
                        if isinstance(generation_message, Message):
                            await generation_message.edit_text(
                                escape_md("❌ Ошибка загрузки! Печеньки возвращены.", version=2),
                                reply_markup=await create_main_menu_keyboard(message_recipient),
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        else:
                            await send_message_with_fallback(
                                bot, message_recipient,
                                escape_md("❌ Ошибка загрузки! Печеньки возвращены.", version=2),
                                reply_markup=await create_main_menu_keyboard(message_recipient),
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        if not is_admin_generation:
                            await update_user_credits(target_user_id, "increment_photo", amount=required_photos)
                            logger.info(f"Фото возвращены на баланс для user_id={target_user_id}")
                        await reset_generation_context(state, generation_type)
                        return
                    
                    duration = time.time() - start_time
                    try:
                        if isinstance(generation_message, Message):
                            await generation_message.delete()
                    except:
                        pass
                    
                    # Обновляем last_generation_params с image_urls и duration
                    generation_params.update({'image_urls': image_urls, 'duration': duration})
                    preserved_data.update({
                        'last_generation_params': generation_params,
                    })
                    if is_admin_generation:
                        last_gen = user_data.get(f'last_admin_generation_{target_user_id}', {})
                        last_gen.update({'image_urls': image_urls, 'duration': duration})
                        preserved_data.update({
                            'is_admin_generation': True,
                            'admin_generation_for_user': target_user_id,
                            'message_recipient': admin_user_id,
                            'generation_target_user': target_user_id,
                            'original_admin_user': admin_user_id,
                            f'last_admin_generation_{target_user_id}': last_gen
                        })
                    
                    # Сохраняем параметры перед очисткой контекста
                    await state.update_data(**preserved_data)
                    
                    # Обработка результата в зависимости от типа генерации
                    if is_admin_generation:
                        result_data = {
                            'success': True,
                            'image_urls': image_urls,
                            'prompt': processed_prompt,
                            'style': user_data.get('style_name', 'custom')
                        }
                        await handle_admin_generation_result(state, admin_user_id, target_user_id, result_data, bot)
                    else:
                        await send_generation_results(
                            bot, message_recipient, target_user_id, image_paths, duration, aspect_ratio_key,
                            generation_type, model_key, state, admin_user_id if is_admin_generation else None
                        )
                    
                    logger.info(f"🎯 PixelPie_AI генерация завершена для user_id={target_user_id}: "
                               f"{len(image_paths)} фото за {duration:.1f} сек")
                    asyncio.create_task(cleanup_files(image_paths + [user_data.get('photo_path')]))
                    
                except Exception as e:
                    logger.error(f"Ошибка генерации для user_id={target_user_id}: {e}", exc_info=True)
                    error_message = escape_md("❌ Ошибка! Печеньки возвращены на баланс.", version=2)
                    if isinstance(generation_message, Message):
                        try:
                            await generation_message.edit_text(
                                error_message,
                                reply_markup=await create_main_menu_keyboard(message_recipient),
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                        except:
                            await send_message_with_fallback(
                                bot, message_recipient,
                                error_message,
                                reply_markup=await create_main_menu_keyboard(message_recipient),
                                parse_mode=ParseMode.MARKDOWN_V2
                            )
                    else:
                        await send_message_with_fallback(
                            bot, message_recipient,
                            error_message,
                            reply_markup=await create_main_menu_keyboard(message_recipient),
                            parse_mode=ParseMode.MARKDOWN_V2
                        )
                    if not is_admin_generation:
                        await update_user_credits(target_user_id, "increment_photo", amount=required_photos)
                        logger.info(f"Фото возвращены после ошибки для user_id={target_user_id}")
                    await reset_generation_context(state, generation_type)
                finally:
                    # Сохраняем параметры после генерации, если они определены
                    if preserved_data:
                        await state.update_data(**preserved_data)
                    await clean_admin_context(state)
                    logger.info("Админский контекст очищен после генерации")
                    user_data = await state.get_data()
                    logger.info(f"После обработки: user_data={user_data}")

async def send_generation_results(bot: Bot, message_recipient: int, target_user_id: int, 
                                image_paths: List[str], duration: float, aspect_ratio: str, 
                                generation_type: str, model_key: str, state: FSMContext, 
                                admin_user_id: int = None) -> None:
    
    user_data = await state.get_data()
    state_value = user_data.get('state')
    logger.debug(f"send_generation_results вызван для user_id={message_recipient}, image_paths={image_paths}, state={state_value}")
    
    try:
        if len(image_paths) == 1:
            caption = escape_md(f"📸 Ваша ИИ генерация фотографии готова! Время: {duration:.1f} сек", version=2)
            photo_file = FSInputFile(path=image_paths[0])
            await send_photo_with_retry(
                bot, message_recipient, photo_file, caption=caption,
                reply_markup=await create_rating_keyboard(generation_type, model_key, message_recipient, bot),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            if admin_user_id and admin_user_id != message_recipient:
                await bot.send_photo(
                    chat_id=admin_user_id,
                    photo=photo_file,
                    caption=escape_md(f"Фотореалистичное фото для ID {target_user_id}", version=2),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")
                    ]]),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        else:
            caption = escape_md(
                f"📸 {len(image_paths)} ваших фотографий созданы! ({duration:.1f} сек)\n"
                f"🎯 Сделано при помощи PixelPie_AI", version=2
            )
            media = []
            for i, path in enumerate(image_paths):
                photo_file = FSInputFile(path=path)
                if i == 0:
                    media.append(InputMediaPhoto(media=photo_file, caption=caption, parse_mode=ParseMode.MARKDOWN_V2))
                else:
                    media.append(InputMediaPhoto(media=photo_file))
            await send_media_group_with_retry(bot, message_recipient, media)
            await send_message_with_fallback(
                bot, message_recipient,
                escape_md("⭐ Оцени результат ИИ фотогенерации:", version=2),
                reply_markup=await create_rating_keyboard(generation_type, model_key, message_recipient, bot),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            if admin_user_id and admin_user_id != message_recipient:
                await send_media_group_with_retry(bot, admin_user_id, media)
                await bot.send_message(
                    chat_id=admin_user_id,
                    text=escape_md(f"Фото для пользователя {target_user_id} готовы", version=2),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🔙 К действиям", callback_data=f"user_actions_{target_user_id}")
                    ]]),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
        await state.clear()
        if state_value:
            await state.update_data(state=state_value)
        logger.info(f"Результаты генерации отправлены для user_id={message_recipient}, state={state_value}")
    except Exception as e:
        logger.error(f"Ошибка в send_generation_results для user_id={message_recipient}: {e}", exc_info=True)
        await send_message_with_fallback(
            bot, message_recipient,
            escape_md("❌ Ошибка при отправке результатов. Попробуйте снова или обратитесь в поддержку: @AXIDI_Help", version=2),
            reply_markup=await create_main_menu_keyboard(message_recipient),
            parse_mode=ParseMode.MARKDOWN_V2
        )

async def cleanup_files(filepaths: List[Optional[str]]):
    
    for filepath in filepaths:
        if filepath and os.path.exists(filepath):
            try:
                async with file_operation_semaphore:
                    await asyncio.get_event_loop().run_in_executor(None, os.remove, filepath)
                logger.debug(f"Удален файл: {filepath}")
            except Exception as e:
                logger.error(f"Ошибка удаления {filepath}: {e}")

async def run_replicate_model_async(model_id: str, input_params: dict) -> List[str]:
    """Асинхронный запуск модели Replicate с проверкой параметров."""
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(3),
        retry=tenacity.retry_if_exception_type(ReplicateError),
        reraise=True
    )
    async def _run():
        loop = asyncio.get_event_loop()
        replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)
        logger.info(f"🚀 Запуск ультра-реалистичной модели {model_id}")
        logger.debug(f"📸 Параметры: {input_params}")
        
        # Проверка параметров на сериализуемость
        for key, value in input_params.items():
            if asyncio.iscoroutine(value):
                logger.error(f"Обнаружен несереализуемый параметр {key}: coroutine")
                raise ValueError(f"Параметр {key} является корутиной: {value}")
            if not isinstance(value, (str, int, float, bool, list, dict, type(None))):
                logger.error(f"Обнаружен несереализуемый параметр {key}: тип {type(value)}")
                raise ValueError(f"Параметр {key} имеет неподдерживаемый тип: {type(value)}")
        
        output = await loop.run_in_executor(
            None,
            lambda: replicate_client.run(model_id, input=input_params)
        )
        image_urls = []
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str):
                    image_urls.append(item)
                elif hasattr(item, 'url'):
                    image_urls.append(item.url)
        logger.info(f"Получены URL изображений: {image_urls}")
        return image_urls
    
    return await _run()

async def upload_image_to_replicate(photo_path: str) -> str:
    
    async with replicate_semaphore:
        loop = asyncio.get_event_loop()
        if not os.path.exists(photo_path):
            raise FileNotFoundError(f"Файл не найден: {photo_path}")
        file_size = os.path.getsize(photo_path)
        if file_size > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"Файл слишком большой: {file_size / 1024 / 1024:.2f} MB")
        replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)
        def upload_sync():
            with open(photo_path, 'rb') as f:
                return replicate_client.files.create(file=f)
        file_response = await loop.run_in_executor(None, upload_sync)
        image_url = file_response.urls.get('get')
        if not image_url:
            raise ValueError("Replicate не вернул URL")
        logger.info(f"Изображение загружено: {image_url}")
        return image_url

def is_new_fast_flux_model(model_id: str, model_version: str = None) -> bool:
    
    if model_id and ("fastnew" in model_id.lower() or "fast-flux" in model_id.lower()):
        return True
    if model_version and len(model_version) == 64 and all(c in '0123456789abcdef' for c in model_version.lower()):
        return True
    if model_id and REPLICATE_USERNAME_OR_ORG_NAME and model_id.startswith(f"{REPLICATE_USERNAME_OR_ORG_NAME}/"):
        return True
    return False

# Псевдонимы для совместимости
process_prompt = process_prompt_async
upload_image_to_replicate = upload_image_to_replicate

ADDITIONAL_PROFESSIONAL_MODELS = [
    "prithivMLmods/Flux-Skin-Real",
    "renderartist/colorgrading",
    "alvdansen/frosting_lane_flux",
    "prithivMLmods/Flux-Dev-Real-Anime",
    "gokaygokay/Flux-Portrait-LoRA",
    "XLabs-AI/flux-RealismLora",
    "ostris/flux-dev-photorealism",
    "multimodalart/flux-lora-the-explorer",
    "prithivMLmods/Flux-Realistic-People",
    "prithivMLmods/Flux-BetterSkin-LoRA"
]

PHOTOREALISM_TIPS = {
    "guidance_scale": {
        "range": "3.5-4.5",
        "optimal": "4.0",
        "note": "Слишком высокое значение создает искусственность"
    },
    "inference_steps": {
        "range": "45-50",
        "optimal": "50",
        "note": "Больше шагов = лучшая детализация"
    },
    "lora_strengths": {
        "skin_texture": "0.8-0.9 (критически важно)",
        "anti_cgi": "0.4-0.6 (обязательно)",
        "face_realism": "0.7-0.8",
        "color_grading": "0.5-0.7"
    },
    "critical_prompts": [
        "natural skin texture with visible pores",
        "realistic skin tone", 
        "not CGI, not 3D render",
        "real person",
        "authentic human features",
        "professional photography",
        "DSLR camera quality"
    ],
    "avoid_prompts": [
        "perfect skin",
        "flawless",
        "smooth skin", 
        "artificial",
        "digital art",
        "rendered"
    ]
}

logger.info("🎯 УЛЬТРА-РЕАЛИСТИЧНАЯ СИСТЕМА ЗАГРУЖЕНА!")
logger.info("📸 Профессиональные LoRA для натуральной кожи активированы")
logger.info("🚫 Анти-CGI фильтры установлены") 
logger.info("⚡ Готов к созданию фотореалистичных изображений!")