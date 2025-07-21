from typing import Dict, Any, List, Optional

# === ОСНОВНАЯ МОДЕЛЬ ДЛЯ ГЕНЕРАЦИИ ===
MULTI_LORA_MODEL = "lucataco/flux-dev-multi-lora:2389224e115448d9a77c07d7d45672b3f0aa45acacf1c5bcf51857ac295e3aec"

# === ПОЛНЫЙ СПИСОК ПРОФЕССИОНАЛЬНЫХ LORA МОДЕЛЕЙ ===
HF_LORA_MODELS = [
    # === ОРИГИНАЛЬНЫЕ МОДЕЛИ ===
    "prithivMLmods/Fashion-Hut-Modeling-LoRA",
    "Heartsync/Flux-NSFW-uncensored",
    "prithivMLmods/Flux-Realism-FineDetailed",
    "prithivMLmods/Canopus-LoRA-Flux-FaceRealism",
    "strangerzonehf/Flux-Super-Realism-LoRA",
    "strangerzonehf/Flux-Super-Portrait-LoRA",
    "alvdansen/frosting_lane_flux",
    "prithivMLmods/Flux-Skin-Real",
    "renderartist/colorgrading",
    "prithivMLmods/Flux-Dev-Real-Anime",
    "gokaygokay/Flux-Portrait-LoRA",
    "XLabs-AI/flux-RealismLora",
    "prithivMLmods/Flux-Realistic-People",
    "prithivMLmods/Flux-BetterSkin-LoRA",
    "ostris/flux-dev-photorealism",
    "multimodalart/flux-lora-the-explorer",
    "prithivMLmods/Flux-Ultra-Realism",
    "prithivMLmods/Flux-Perfect-Face",
    "renderartist/flux-professional-lighting",
    "prithivMLmods/Flux-Natural-Skin-Texture",
    "XLabs-AI/flux-professional-photography",
    "strangerzonehf/Flux-Hyperrealistic-Portrait",
    "https://huggingface.co/LHRuig/realismlora/resolve/main/realismlora-sexy%20checkpoint.safetensors"
]

# === МАКСИМАЛЬНАЯ КОНФИГУРАЦИЯ LORA ДЛЯ УЛЬТРА-КАЧЕСТВА ===
LORA_CONFIG = {
    "skin_texture_master": {
        "model": "prithivMLmods/Flux-Skin-Real", 
        "strength": 0.9,
        "keywords": ["skin", "texture", "natural skin", "pores", "кожа", "текстура", "realistic skin", "natural texture"],
        "description": "Натуральная текстура кожи без CGI эффекта - ПРИОРИТЕТ #1",
        "priority": 1
    },
    "photo_realism_pro": {
        "model": "alvdansen/frosting_lane_flux",
        "strength": 1.0,
        "keywords": ["photorealistic", "professional photo", "camera shot", "DSLR", "фотореалистичный", "professional photography"],
        "description": "Профессиональная фотосъемка высочайшего качества",
        "priority": 2
    },
    "anti_cgi": {
        "model": "prithivMLmods/Flux-Dev-Real-Anime",
        "strength": 0.9,
        "keywords": ["real", "not cgi", "not 3d", "natural", "authentic", "реальный", "not artificial"],
        "description": "Анти-CGI эффект для натуральности - ОБЯЗАТЕЛЬНО",
        "priority": 3
    },
    "ultra_realism": {
        "model": "https://huggingface.co/LHRuig/realismlora/resolve/main/realismlora-sexy%20checkpoint.safetensors",
        "strength": 0.9,
        "keywords": ["super realistic", "hyperrealistic", "extreme detail", "photographic", "гиперреализм", "супер", "8k", "uhd", "ultra hd", "masterpiece", "best quality"],
        "description": "Ультра-реализм высочайшего качества",
        "priority": 4
    },
    "face_perfection": {
        "model": "prithivMLmods/Canopus-LoRA-Flux-FaceRealism",
        "strength": 0.9,
        "keywords": ["face", "portrait", "person", "man", "woman", "headshot", "лицо", "портрет", "человек", "eyes", "глаза", "detailed eyes", "perfect eyes"],
        "description": "Идеальная детализация лиц и глаз",
        "priority": 5
    },
    "color_grading_pro": {
        "model": "renderartist/colorgrading",
        "strength": 0.9,
        "keywords": ["color grading", "cinematic", "professional lighting", "цветокоррекция", "natural colors", "color correction"],
        "description": "Профессиональная цветокоррекция",
        "priority": 6
    },
    "portrait_master_pro": {
        "model": "gokaygokay/Flux-Portrait-LoRA",
        "strength": 0.95,
        "keywords": ["portrait", "headshot", "professional portrait", "портрет", "studio portrait"],
        "description": "Профессиональная портретная фотография",
        "priority": 7
    },
    "fine_details": {
        "model": "prithivMLmods/Flux-Realism-FineDetailed",
        "strength": 0.8,
        "keywords": ["detailed", "fine", "texture", "skin", "fabric", "детали", "текстура", "кожа", "fine details"],
        "description": "Тонкая детализация текстур",
        "priority": 8
    },
    "super_realism": {
        "model": "strangerzonehf/Flux-Super-Realism-LoRA",
        "strength": 0.75,
        "keywords": ["super realistic", "hyperrealistic", "extreme detail", "photographic", "гиперреализм", "супер", "natural", "lifelike"],
        "description": "Супер-реализм высочайшего качества",
        "priority": 9
    },
    "fashion": {
        "model": "prithivMLmods/Fashion-Hut-Modeling-LoRA",
        "strength": 0.8,
        "keywords": ["fashion", "style", "outfit", "dress", "suit", "clothes", "wear", "elegant", "стиль", "одежда", "наряд", "luxury", "designer", "high fashion"],
        "description": "Модные стили и роскошная одежда",
        "priority": 10
    },
    "super_portrait": {
        "model": "strangerzonehf/Flux-Super-Portrait-LoRA",
        "strength": 0.7,
        "keywords": ["portrait", "close-up", "facial", "beauty", "glamour", "studio", "крупный план", "красота", "professional", "magazine"],
        "description": "Профессиональные портреты",
        "priority": 11
    },
    "realism_xl": {
        "model": "XLabs-AI/flux-RealismLora",
        "strength": 0.85,
        "keywords": ["realistic", "natural", "authentic", "real person", "hyperrealistic"],
        "description": "Дополнительный слой реализма",
        "priority": 12
    },
    "realistic_people": {
        "model": "prithivMLmods/Flux-Realistic-People",
        "strength": 0.8,
        "keywords": ["people", "human", "person", "realistic person", "natural person"],
        "description": "Реалистичные люди",
        "priority": 13
    },
    "better_skin": {
        "model": "prithivMLmods/Flux-BetterSkin-LoRA",
        "strength": 0.9,
        "keywords": ["skin", "better skin", "improved skin", "skin quality", "natural skin"],
        "description": "Улучшенная кожа высочайшего качества",
        "priority": 14
    },
    "artistic": {
        "model": "Heartsync/Flux-NSFW-uncensored",
        "strength": 0.3,
        "keywords": ["artistic", "creative", "fantasy", "surreal", "abstract", "nude", "арт", "фантазия", "художественный"],
        "description": "Художественные и креативные стили",
        "priority": 15
    }
}

# === ПРИОРИТЕТЫ ДЛЯ МАКСИМАЛЬНОГО КАЧЕСТВА ===
LORA_PRIORITIES = {
    "skin_texture_master": 1,
    "photo_realism_pro": 2,
    "anti_cgi": 3,
    "ultra_realism": 4,
    "face_perfection": 5,
    "color_grading_pro": 6,
    "portrait_master_pro": 7,
    "fine_details": 8,
    "super_realism": 9,
    "fashion": 10,
    "super_portrait": 11,
    "realism_xl": 12,
    "realistic_people": 13,
    "better_skin": 14,
    "artistic": 15
}

# Максимальные настройки
USER_AVATAR_LORA_STRENGTH = 1.0
MAX_LORA_COUNT = 7

# === УЛЬТРА-РЕАЛИСТИЧНЫЕ СТИЛИ ===
LORA_STYLE_PRESETS = {
    "ultra_photorealistic_max": [
        "skin_texture_master", 
        "photo_realism_pro", 
        "anti_cgi", 
        "face_perfection",
        "ultra_realism",
        "better_skin",
        "color_grading_pro"
    ],
    "natural_portrait_max": [
        "skin_texture_master",
        "face_perfection",
        "portrait_master_pro", 
        "anti_cgi",
        "photo_realism_pro",
        "fine_details",
        "color_grading_pro"
    ],
    "professional_photo_max": [
        "photo_realism_pro",
        "skin_texture_master",
        "color_grading_pro", 
        "realistic_people",
        "better_skin",
        "ultra_realism",
        "face_perfection"
    ],
    "fashion_shoot_max": [
        "fashion",
        "photo_realism_pro",
        "skin_texture_master",
        "face_perfection",
        "color_grading_pro",
        "super_portrait",
        "anti_cgi"
    ],
    "portrait_pro": ["ultra_realism", "face_perfection", "super_portrait", "fine_details"],
    "fashion_shoot": ["fashion", "ultra_realism", "super_realism", "face_perfection"],
    "artistic_portrait": ["face_perfection", "artistic", "fine_details", "ultra_realism"],
    "ultra_realistic": ["ultra_realism", "super_realism", "face_perfection", "fine_details"],
    "glamour": ["super_portrait", "fashion", "face_perfection", "ultra_realism"]
}

# === ПАРАМЕТРЫ МАКСИМАЛЬНОГО КАЧЕСТВА ===
GENERATION_QUALITY_PARAMS = {
    "ultra_max_quality": {
        "guidance_scale": 4.0,
        "num_inference_steps": 50,
        "scheduler": "DDIM",
        "output_quality": 100,
        "width": 1440,
        "height": 1440,
        "output_format": "png",
        "lora_scale": 1.0
    },
    "photorealistic_max": {
        "guidance_scale": 4.0,
        "num_inference_steps": 50,
        "scheduler": "DDIM",
        "output_quality": 100,
        "width": 1440,
        "height": 1440,
        "output_format": "png",
        "lora_scale": 1.0
    },
    "portrait_ultra": {
        "guidance_scale": 3.5,
        "num_inference_steps": 50,
        "scheduler": "DDIM",
        "output_quality": 100,
        "width": 1440,
        "height": 1440,
        "output_format": "png",
        "lora_scale": 1.0
    },
    "default": {
        "guidance_scale": 4.0,
        "num_inference_steps": 50,
        "scheduler": "DDIM",
        "output_quality": 100,
        "width": 1440,
        "height": 1440,
        "output_format": "png",
        "lora_scale": 1.0
    },
    "fast": {
        "guidance_scale": 3.5,
        "num_inference_steps": 50,
        "scheduler": "DPMSolverMultistep",
        "output_quality": 100,
        "width": 1440,
        "height": 1440,
        "output_format": "png",
        "lora_scale": 0.99
    }
}

# === МАКСИМАЛЬНО ДЕТАЛЬНЫЕ NEGATIVE PROMPTS ===
NEGATIVE_PROMPTS = {
    "ultra_realistic_max": (
        "3d render, cgi, computer graphics, digital art, artificial, fake, synthetic, "
        "unreal engine, blender, maya, 3ds max, rendered, digital painting, "
        "video game, animation, cartoon, anime, illustration, drawing, sketch, "
        "digital artwork, concept art, fantasy art, sci-fi art, "
        "plastic skin, rubber skin, waxy skin, doll skin, mannequin skin, porcelain skin, "
        "shiny skin, oily skin, greasy skin, artificial skin, smooth skin, "
        "perfect skin, flawless skin, airbrushed skin, retouched skin, "
        "silicon skin, latex skin, metallic skin, reflective skin, "
        "red skin, pink skin, orange skin, yellow skin, purple skin, blue skin, "
        "green skin, grey skin, white skin, black skin, metallic skin, "
        "oversaturated skin, desaturated skin, pale skin, colorless skin, "
        "wrong skin tone, unnatural skin color, artificial coloring, "
        "neon skin, glowing skin, luminous skin, "
        "low quality, bad quality, worst quality, poor quality, amateur quality, "
        "blurry, out of focus, soft focus, motion blur, depth of field blur, "
        "pixelated, compression artifacts, jpeg artifacts, noise, grain, "
        "aliasing, moiré, chromatic aberration, distortion, "
        "overexposed, underexposed, blown highlights, crushed blacks, "
        "bad exposure, wrong exposure, "
        "bad eyes, ugly eyes, deformed eyes, distorted eyes, asymmetric eyes, "
        "cross-eyed, lazy eye, uneven eyes, bloodshot eyes, dead eyes, empty eyes, "
        "no pupils, missing pupils, white eyes, blank eyes, closed eyes, "
        "bad iris, no iris detail, flat eyes, lifeless eyes, dull eyes, "
        "glowing eyes, neon eyes, artificial eyes, robot eyes, "
        "multiple pupils, wrong eye color, unrealistic eyes, "
        "bad anatomy, deformed face, asymmetric face, bad proportions, "
        "malformed, mutated, mutation, extra limbs, missing limbs, "
        "bad hands, deformed hands, extra fingers, missing fingers, "
        "fused fingers, wrong number of fingers, "
        "bad nose, crooked nose, missing nose, extra nose, "
        "bad mouth, crooked mouth, missing teeth, extra teeth, "
        "oversaturated, undersaturated, neon colors, artificial enhancement, "
        "heavy makeup, excessive makeup, instagram filter, beauty filter, "
        "face tune, over-processed, HDR, over-sharpened, "
        "watermark, text, logo, signature, username, copyright, "
        "border, frame, cropped, cut off, partial, incomplete, "
        "bad lighting, harsh lighting, flat lighting, artificial lighting, "
        "neon lighting, fluorescent lighting, cold lighting, "
        "wrong shadows, no shadows, harsh shadows, "
        "studio flash, direct flash, overlit, underlit, "
        "painting style, artistic style, stylized, non-photographic, "
        "illustration style, comic style, manga style, "
        "unrealistic proportions, exaggerated features, "
        "fantasy elements, magical elements, supernatural, "
        "impossible anatomy, impossible pose, "
        "multiple people, crowd, group, many people, "
        "duplicated features, cloned face, copy-paste, "
        "low resolution, small size, thumbnail, icon"
    ),
    "default": (
        "low quality, worst quality, bad anatomy, bad proportions, extra limbs, missing limbs, "
        "deformed, mutated, mutation, ugly, disgusting, blurry, watermark, text, logo, "
        "cross-eyed, lazy eye, uneven eyes, deformed eyes, distorted eyes, asymmetric eyes, "
        "bad eyes, weird eyes, dead eyes, closed eyes, half-closed eyes, bloodshot eyes, "
        "deformed face, distorted face, asymmetric face, uneven face, bad face, ugly face, "
        "bad skin, acne, blemishes, spots, moles, scars, wrinkles, "
        "plastic skin, shiny skin, oily skin, unrealistic skin, bad skin texture, "
        "jpeg artifacts, pixelated, compression artifacts, low resolution, "
        "overexposed, underexposed, oversaturated, washed out colors, "
        "bad hands, deformed hands, extra fingers, missing fingers, "
        "amateur, unprofessional, bad lighting, harsh shadows, "
        "3d render, cgi, computer graphics, digital art, artificial, fake"
    ),
    "fashion": (
        "torn clothes, dirty clothes, wrinkled clothes, poorly fitted clothes, "
        "low quality fabric, cheap materials, bad fashion, outdated style, "
        "mismatched colors, poor tailoring, visible seams, loose threads, "
        "baggy fit, tight fit, unflattering cut, poor draping, "
        "plastic clothing, fake materials, artificial textures"
    ),
    "portrait": (
        "bad teeth, missing teeth, yellow teeth, crooked teeth, "
        "bad makeup, smudged makeup, excessive makeup, unnatural makeup, "
        "bad hair, messy hair, greasy hair, unnatural hair color, "
        "unflattering angle, bad posing, awkward expression, forced smile, "
        "plastic skin, artificial skin, doll-like skin, waxy skin"
    )
}

# === НАСТРОЙКИ КАМЕРЫ ДЛЯ МАКСИМАЛЬНОГО КАЧЕСТВА ===
CAMERA_SETUP_ENHANCED = {
    "ultra_professional": (
        "shot on Hasselblad H6D-400c MS with HC 80mm f/2.8 lens, medium format sensor, "
        "ISO 64, f/2.8, 1/250s, studio lighting with Profoto strobes, "
        "three-point lighting setup, key light with 120cm octabox, "
        "fill light with 90cm softbox, rim light with beauty dish, "
        "color temperature 5600K, shot in 16-bit RAW format, "
        "tethered capture, Phase One Capture One Pro processing, "
        "400 megapixel resolution, ultra-high definition"
    ),
    "portrait_max": (
        "shot on Canon EOS R5 with RF 85mm f/1.2L lens, full-frame sensor, "
        "natural window lighting mixed with LED panels, "
        "f/1.4, 1/200s, ISO 100, 45MP resolution, "
        "shallow depth of field, creamy bokeh, perfect eye focus, "
        "professional color grading, shot in RAW format, "
        "skin retouching with frequency separation, "
        "micro-contrast enhancement, professional dodge and burn"
    ),
    "fashion_ultra": (
        "shot on Sony A7R V with GM 85mm f/1.4 lens, 61MP full-frame sensor, "
        "professional studio lighting with Broncolor strobes, "
        "fashion editorial setup, key light at 45 degrees with 150cm softbox, "
        "fill light with reflector, background light with snoot, "
        "f/2.8, 1/125s, ISO 100, tethered capture, "
        "Phase One processing, 16-bit color depth, "
        "professional retouching, magazine quality"
    )
}

CAMERA_SETUP_BASE = (
    "Ultra-professional photography setup: Hasselblad H6D-400c MS medium format camera "
    "with HC 80mm f/2.8 lens, 400 megapixel resolution, "
    "professional studio lighting with Profoto strobes, "
    "three-point lighting: key light with 120cm octabox at 45°, "
    "fill light with 90cm softbox, rim light with beauty dish, "
    "color temperature 5600K, perfect color balance, "
    "settings: f/2.8, 1/250s, ISO 64 (base ISO), "
    "shot in 16-bit RAW format, tethered capture, "
    "post-processing: Phase One Capture One Pro, "
    "professional skin retouching with frequency separation, "
    "micro-contrast enhancement, advanced dodge and burn, "
    "color grading with professional LUTs, "
    "export: 16-bit ProPhoto RGB color space, 400dpi, "
    "uncompressed TIFF format, museum quality archival"
)

LUXURY_DETAILS_BASE = (
    "ultra-luxury style, haute couture fashion, bespoke designer clothing, "
    "perfect tailoring and fit, premium materials, "
    "natural healthy skin with visible realistic texture and pores, "
    "professional makeup by top artists, "
    "perfect eyes with crystal-clear iris detail and natural moisture, "
    "detailed eye reflections, symmetrical features, "
    "cinematic lighting setup, Rembrandt lighting technique, "
    "magazine cover quality, Vogue editorial standard, "
    "natural skin finish with subtle highlights, "
    "luxury accessories, premium jewelry, "
    "professional hair styling, perfect grooming"
)

# === СООТНОШЕНИЯ СТОРОН ДЛЯ МАКСИМАЛЬНОГО КАЧЕСТВА ===
ASPECT_RATIOS = {
    "1:1": (1440, 1440),
    "3:4": (1080, 1440),
    "4:3": (1440, 1080),
    "9:16": (810, 1440),
    "16:9": (1440, 810),
    "2:3": (960, 1440),
    "3:2": (1440, 960),
    "5:4": (1152, 1440),
    "4:5": (1440, 1152),
    "5:7": (1029, 1440),
    "7:5": (1440, 1029),
    "8:10": (1152, 1440),
    "10:8": (1440, 1152),
    "square": (1440, 1440),
    "portrait": (1080, 1440),
    "landscape": (1440, 1080),
}

# === МОДЕЛИ ГЕНЕРАЦИИ ===
IMAGE_GENERATION_MODELS = {
    "flux-trained": {
        "name": "✨ Генерация с аватаром / Фото-референс (МАКС КАЧЕСТВО)",
        "id": MULTI_LORA_MODEL,
        "api": "replicate",
        "max_quality": True,
        "optimal_resolution": (1440, 1440),
        "supports_ultra_realism": True
    },
    "flux-trainer": {
        "name": "🛠 Обучение аватара",
        "id": "ostris/flux-dev-lora-trainer:4ffd32160efd92e956d39c5338a9b8fbafca58e03f791f6d8011f3e20e8ea6fa",
        "api": "replicate"
    },
    "kwaivgi/kling-v2.1": {
        "name": "🎥 AI-видео (Kling 2.1)",
        "id": "kwaivgi/kling-v2.1",
        "api": "replicate",
        "cost": 20
    },
    "meta-llama-3-8b-instruct": {
        "name": "📝 Помощник Промтов (Llama 3)",
        "id": "meta/meta-llama-3-8b-instruct",
        "api": "replicate"
    }
}

# === СТОИМОСТЬ ГЕНЕРАЦИЙ ===
REPLICATE_COSTS = {
    MULTI_LORA_MODEL: 0.000725 * 30,
    "ostris/flux-dev-lora-trainer:4ffd32160efd92e956d39c5338a9b8fbafca58e03f791f6d8011f3e20e8ea6fa": 0.001525,
    "meta/meta-llama-3-8b-instruct": 0.0005,
    "kwaivgi/kling-v2.1": 0.0028 * 5
}

# === МАППИНГ ТИПОВ ГЕНЕРАЦИИ ===
GENERATION_TYPE_TO_MODEL_KEY = {
    'with_avatar': MULTI_LORA_MODEL,
    'photo_to_photo': MULTI_LORA_MODEL,
    'ai_video_v2_1': "kwaivgi/kling-v2.1",
    'train_flux': "ostris/flux-dev-lora-trainer:4ffd32160efd92e956d39c5338a9b8fbafca58e03f791f6d8011f3e20e8ea6fa",
    'prompt_assist': "meta/meta-llama-3-8b-instruct",
    'prompt_based': MULTI_LORA_MODEL
}

# === СТИЛИ ===
GENERATION_STYLES = {}

# === МУЖСКИЕ СТИЛИ АВАТАРОВ ===
NEW_MALE_AVATAR_STYLES = {
    "profile_office": "🏢 Деловой",
    "traveler_mountains": "🏔 Путешественник",
    "sportsman_stadium": "🏟 Спортсмен",
    "musician_stage": "🎸 Музыкант",
    "scientist_lab": "🔬 Ученый",
    "chef_kitchen": "👨‍🍳 Шеф-повар",
    "pilot_cockpit": "✈️ Пилот",
    "writer_library": "📚 Писатель",
    "gamer_neon": "🎮 Геймер",
    "historical_knight": "🛡 Рыцарь",
    "cowboy_wildwest": "🤠 Ковбой",
    "detective_noir": "🕵️ Детектив",
    "firefighter_uniform": "👨‍🚒 Пожарный",
    "casual_city_walk": "🚶‍♂️ Городской стиль",
    "elegant_tuxedo": "🤵 Элегантный",
    "biker_motorcycle": "🏍 Байкер",
    "fantasy_warrior": "⚔️ Фэнтези-воин",
    "astronaut_space": "🧑‍🚀 Астронавт",
    "dj_club": "🎧 Диджей",
    "artist_studio": "🎨 Художник в студии",
    "beach_relaxed": "🌴 Пляж",
    "fantasy_realm": "✨ Фэнтези",
    "vintage_classic": "🕰️ Винтаж",
    "style_modern": "🧥 Стиль",
    "urban_street": "🏙️ Город",
    "nature_outdoor": "🏞️ Природа",
    "cyberpunk_future": "🤖 Киберпанк",
    "luxury_premium": "💎 Люкс",
    "classic_formal": "🎩 Классика",
    "adventure_explorer": "🗺️ Приключение",
    "space_cosmic": "🚀 Космос",
    "retro_nostalgic": "📼 Ретро",
    "street_style": "🛹 Стрит-стайл",
    "artist_creative": "🎨 Арт",
    "futuristic_tech": "🔮 Футуризм",
    "elegant_sophisticated": "👔 Элегантно",
    "photographer_camera": "📷 Фотограф",
    "doctor_medical": "👨‍⚕️ Врач",
    "teacher_classroom": "👨‍🏫 Учитель",
    "businessman_portrait": "💼 Бизнес-портрет"
}

# === ЖЕНСКИЕ СТИЛИ АВАТАРОВ ===
NEW_FEMALE_AVATAR_STYLES = {
    "vintage": "🖼️ Винтаж", 
    "style": "🦋 Стильно", 
    "vacation": "📸 Портрет", 
    "profession": "𓂃🖌 Скетч", 
    "flowers": "🌸 Цветы", 
    "universe": "🏞️ Мир", 
    "zarz": "✍Шарж", 
    "zarza": "✌Карикатура", 
    "message": "👠 Люкс", 
    "cyberpunk": "🌃 Киберпанк",
    "fantasy": "🐉 Фантазия",
    "beach": "🌴 Пляж",
    "business_woman_city": "🏙 Бизнес-леди",
    "traveler_beach": "🏖 Путешественница",
    "fitness_girl_gym": "💪 Фитнес-модель",
    "singer_microphone": "🎤 Певица",
    "doctor_hospital": "👩‍⚕️ Врач",
    "pastry_chef_cafe": "🍰 Кондитер",
    "ballerina_stage": "🩰 Балерина",
    "journalist_reporter": "📰 Журналистка",
    "fashion_model_runway": "💃 Модель",
    "historical_queen": "👑 Королева",
    "boho_style_nature": "🌿 Бохо-стиль",
    "spy_secret_mission": "🕵️‍♀️ Шпионка",
    "teacher_classroom": "👩‍🏫 Учительница",
    "urban_streetwear": "👟 Стритвир",
    "evening_gown_event": "👗 Вечерний образ",
    "rockstar_guitar": "🤘 Рок-звезда",
    "fantasy_elf": "🧝‍♀️ Эльфийка",
    "scientist_future_lab": "🧪 Ученая (будущее)",
    "yogini_sunset": "🧘‍♀️ Йога на закате",
    "artist_gallery": "🖼️ Художница в галерее",
    "beach_sunset": "🌴 Пляж",
    "fantasy_magical": "✨ Фэнтези",
    "vintage_retro": "🕰️ Винтаж",
    "style_chic": "🧥 Стиль",
    "urban_modern": "🏙️ Город",
    "nature_beauty": "🏞️ Природа",
    "cyberpunk_neon": "🤖 Киберпанк",
    "luxury_glamour": "💎 Люкс",
    "floral_romantic": "🌸 Цветы",
    "classic_elegance": "🎩 Классика",
    "adventure_travel": "🗺️ Приключение",
    "space_futuristic": "🚀 Космос",
    "retro_vintage": "📼 Ретро",
    "street_fashion": "🛹 Стрит-стайл",
    "artist_painter": "🎨 Арт",
    "futuristic_style": "🔮 Футуризм",
    "elegant_dress": "👗 Элегантно",
    "photographer_artist": "📷 Фотограф",
    "barista_cafe": "☕ Бариста",
    "pilot_aviator": "✈️ Пилот"
}

# === ПРОМПТЫ ДЛЯ СОВМЕСТИМОСТИ ===
style_prompts = {}

# Мужские промпты (оставлены без изменений для совместимости с исходным файлом)
new_male_avatar_prompts = {
    "LEGO": "A 30-year-old man with short, tousled dark brown hair and striking blue eyes, dressed in a vibrant red and black Lego-inspired outfit, consisting of a fitted jacket with a 3D Lego brick pattern, black cargo pants with red stud accents, and matching black sneakers with Lego-themed laces, posing confidently on a colorful Lego-themed background, with a mix of large and small Lego bricks, plates, and tiles, arranged to resemble a bustling Lego cityscape with skyscrapers and vehicles, illuminated by warm sunlight casting a soft, golden glow from the upper left corner, creating a shallow depth of field, with the subject’s face and upper body in sharp focus, while the background is gently blurred, with a slight motion blur on the subject’s hair and jacket, capturing a sense of energy and playfulness, with a subtle nostalgia and Lego-inspired whimsy, using a 35mm camera with a 50mm lens, set to f/2.8, ISO 100, and a 1/125th of a second shutter speed, with a warm, balanced color palette, emphasizing the bright, saturated colors of the Lego bricks and the subject’s outfit, ultra-realistic, 8K resolution, no artifacts, flawless composition",
    "profile_office": "man, mid-30s, professional business headshot, sharp jawline, neatly styled short dark hair, wearing a tailored navy pinstripe suit, crisp white dress shirt, silk burgundy tie, subtle gold cufflinks, confident and approachable gaze, standing in a sleek modern office with floor-to-ceiling windows, blurred cityscape of skyscrapers at dusk in the background, warm ambient lighting with soft golden highlights, dramatic chiaroscuro effect, ultra-realistic 8K resolution, shot with 85mm lens, shallow depth of field, tack-sharp focus on face, beautiful bokeh with glowing light orbs, luxury fashion editorial style, sophisticated color grading with deep blues and warm golds, magazine-quality aesthetic",
    "traveler_mountains": "man, late 20s, rugged traveler with a short beard, tousled dark hair, wearing a weatherproof khaki hiking jacket, sturdy cargo pants, heavy-duty hiking boots, carrying a large expedition backpack, standing on a rocky mountain peak, breathtaking snow-capped peaks stretching into the horizon, morning sunlight casting golden rays through scattered clouds, ultra-realistic 8K resolution, cinematic lighting with soft mist in the valleys, shot with 50mm lens, shallow depth of field, sharp focus on the man, ethereal bokeh with natural light flares, adventure photography style, rich color grading with vibrant greens and cool blues, National Geographic aesthetic",
    "sportsman_stadium": "man, early 30s, athletic build, post-match football player with sweat-glistened skin, short cropped hair, wearing a vibrant red and white sports uniform, shin guards, and cleats, standing on a lush green stadium pitch, dramatic stadium floodlights casting dynamic shadows, roaring crowd blurred in the background, ultra-realistic 8K resolution, shot with 70-200mm lens, shallow depth of field, tack-sharp focus on the athlete, vivid bokeh with glowing light flares, cinematic sports photography style, high contrast with rich greens and warm yellows, ESPN magazine aesthetic",
    "musician_stage": "man, late 20s, charismatic musician with shoulder-length wavy hair, wearing a fitted black leather jacket, distressed jeans, holding a vintage electric guitar, performing on a dimly lit stage, swirling stage smoke illuminated by a single spotlight, vibrant stage lighting with hues of purple and blue, ultra-realistic 8K resolution, shot with 50mm lens, shallow depth of field, sharp focus on the musician, dreamy bokeh with colorful light orbs, concert photography style, moody color grading with deep blacks and vibrant accents, Rolling Stone magazine aesthetic",
    "scientist_lab": "man, mid-40s, scientist with neatly combed hair and glasses, wearing a pristine white lab coat, blue nitrile gloves, standing in a high-tech laboratory, surrounded by glowing monitors, intricate lab equipment, and vials of colorful liquids, focused expression, ultra-realistic 8K resolution, cinematic lighting with cool blue ambient glow and soft key light on the face, shot with 85mm lens, shallow depth of field, tack-sharp focus on the scientist, subtle bokeh with geometric light patterns, scientific journal photography style, clean color grading with whites and blues, futuristic aesthetic",
    "chef_kitchen": "man, early 30s, chef with a trimmed beard, wearing a double-breasted white chef’s uniform, black apron, and chef’s hat, actively cooking a gourmet dish with visible flames and steam, standing in a bustling professional kitchen with stainless steel counters, fresh herbs, and colorful ingredients, ultra-realistic 8K resolution, warm cinematic lighting with golden highlights, shot with 35mm lens, shallow depth of field, sharp focus on the chef, vibrant bokeh with kitchen textures, food photography style, rich color grading with warm oranges and greens, Michelin-star restaurant aesthetic",
    "pilot_cockpit": "man, late 30s, pilot with short military-style hair, wearing a dark navy pilot uniform with gold insignia, aviator sunglasses resting on his head, seated in a detailed airplane cockpit, surrounded by intricate instruments, glowing control panels, and a view of fluffy clouds through the windshield, ultra-realistic 8K resolution, cinematic lighting with cool blue cockpit glow and warm sunlight filtering through the window, shot with 50mm lens, shallow depth of field, tack-sharp focus on the pilot, subtle bokeh with instrument lights, aviation photography style, rich color grading with blues and golds, Top Gun aesthetic",
    "writer_library": "man, mid-40s, writer with salt-and-pepper hair, wearing a tweed blazer, open-collared white shirt, seated at a large oak desk cluttered with leather-bound books, quill pens, and manuscripts, in an ancient cozy library with towering bookshelves, warm candlelight casting soft shadows, ultra-realistic 8K resolution, cinematic lighting with golden ambient glow, shot with 85mm lens, shallow depth of field, sharp focus on the writer, dreamy bokeh with warm light orbs, literary portrait style, sepia-toned color grading with rich browns, vintage aesthetic",
    "gamer_neon": "man, mid-20s, gamer with short dyed hair, wearing noise-canceling headphones, a sleek black hoodie, intensely focused on a triple-monitor gaming setup, vibrant RGB lighting casting neon pink, blue, and green glows, high-tech gaming peripherals visible, ultra-realistic 8K resolution, cinematic lighting with dynamic neon reflections, shot with 35mm lens, shallow depth of field, tack-sharp focus on the gamer, vibrant bokeh with glowing light patterns, esports photography style, bold color grading with neon accents, futuristic gaming aesthetic",
    "historical_knight": "man, early 30s, knight with a rugged face and short beard, wearing gleaming silver medieval armor with intricate engravings, a flowing red cape, holding a polished longsword and a crest-embossed shield, standing before a stone medieval castle with banners fluttering in the wind, ultra-realistic 8K resolution, cinematic lighting with golden sunlight casting dramatic shadows, shot with 50mm lens, shallow depth of field, sharp focus on the knight, epic bokeh with castle textures, historical portrait style, rich color grading with silvers and reds, Game of Thrones aesthetic",
    "cowboy_wildwest": "man, late 30s, cowboy with weathered skin, wearing a wide-brimmed Stetson hat, dusty leather duster coat, denim shirt, and spurred boots, standing in a windswept prairie with golden grass, a horse grazing in the background, vibrant sunset casting warm oranges, ultra-realistic 8K resolution, cinematic lighting with golden hour glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on the cowboy, rustic bokeh with natural textures, Western photography style, rich color grading with warm yellows and browns, Clint Eastwood film aesthetic",
    "detective_noir": "man, mid-40s, noir detective with a five-o’clock shadow, wearing a dark trench coat, fedora tilted slightly, cigarette dangling from lips, standing on a rain-soaked city street at night, neon signs reflecting on wet asphalt, ultra-realistic 8K resolution, cinematic lighting with moody blue and red neon glow, shot with 35mm lens, shallow depth of field, sharp focus on the detective, atmospheric bokeh with rain droplets, film noir photography style, high-contrast color grading with deep blacks, Blade Runner aesthetic",
    "firefighter_uniform": "man, early 30s, firefighter with a strong jawline, wearing full firefighting gear, red helmet, and oxygen tank, standing proudly beside a gleaming fire truck, smoke and embers faintly visible in the background, ultra-realistic 8K resolution, cinematic lighting with warm red and orange hues, shot with 50mm lens, shallow depth of field, tack-sharp focus on the firefighter, dramatic bokeh with emergency lights, heroic portrait style, rich color grading with reds and blacks, first-responder aesthetic",
    "casual_city_walk": "man, late 20s, casually dressed in a fitted navy bomber jacket, white t-shirt, slim-fit jeans, and white sneakers, walking through a vibrant autumn city street, golden leaves falling, cozy cafes and boutique shops in the background, ultra-realistic 8K resolution, cinematic lighting with soft golden sunlight, shot with 35mm lens, shallow depth of field, sharp focus on the man, warm bokeh with urban textures, lifestyle photography style, vibrant color grading with oranges and browns, urban fashion aesthetic",
    "elegant_tuxedo": "man, mid-30s, in a sleek black tuxedo with a satin lapel, crisp white dress shirt, black bow tie, holding a crystal champagne flute, standing in a luxurious ballroom with crystal chandeliers and marble floors, ultra-realistic 8K resolution, cinematic lighting with warm golden chandelier glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on the man, opulent bokeh with sparkling lights, high-society portrait style, sophisticated color grading with golds and blacks, James Bond aesthetic",
    "biker_motorcycle": "man, early 30s, biker with a short beard, wearing a black leather jacket, distressed jeans, and aviator sunglasses, leaning against a powerful black motorcycle on an open desert road, vibrant sunset casting purple and orange hues, ultra-realistic 8K resolution, cinematic lighting with warm golden glow, shot with 50mm lens, shallow depth of field, sharp focus on the biker, rugged bokeh with road textures, motorcycle lifestyle photography style, rich color grading with oranges and purples, Sons of Anarchy aesthetic",
    "fantasy_warrior": "man, late 20s, fantasy warrior with long braided hair, wearing intricate dark steel armor with runic engravings, wielding a massive double-headed axe, standing in a misty enchanted forest with glowing bioluminescent plants, ultra-realistic 8K resolution, cinematic lighting with ethereal green and blue glow, shot with 50mm lens, shallow depth of field, sharp focus on the warrior, mystical bokeh with forest textures, epic fantasy portrait style, rich color grading with greens and silvers, Lord of the Rings aesthetic",
    "astronaut_space": "man, mid-30s, astronaut with short hair, wearing a detailed white spacesuit with NASA patches, floating in outer space, vibrant blue Earth and a swirling nebula in the background, ultra-realistic 8K resolution, cinematic lighting with cool starlight and warm Earth glow, shot with 35mm lens, shallow depth of field, sharp focus on the astronaut, cosmic bokeh with starry textures, sci-fi photography style, rich color grading with blues and purples, Interstellar aesthetic",
    "dj_club": "man, late 20s, DJ with slicked-back hair, wearing a fitted black shirt, gold chain necklace, standing behind a high-tech DJ deck in a pulsating nightclub, vibrant neon lights in pink and blue, dancing crowd blurred in the background, ultra-realistic 8K resolution, cinematic lighting with dynamic neon reflections, shot with 35mm lens, shallow depth of field, sharp focus on the DJ, vibrant bokeh with club lights, nightlife photography style, bold color grading with neons and blacks, EDM festival aesthetic",
    "artist_studio": "man, early 40s, artist with messy hair, wearing a paint-splattered smock over a linen shirt, standing in a cluttered art studio filled with easels, vibrant oil paints, and half-finished canvases, natural light streaming through a large skylight, ultra-realistic 8K resolution, cinematic lighting with soft daylight glow, shot with 50mm lens, shallow depth of field, sharp focus on the artist, colorful bokeh with paint textures, creative portrait style, rich color grading with warm whites and vibrant hues, bohemian aesthetic",
    "beach_relaxed": "man, late 20s, relaxed on a tropical beach at sunset, short tousled hair, wearing a white linen shirt unbuttoned, khaki shorts, barefoot, standing near swaying palm trees, turquoise waves crashing gently, ultra-realistic 8K resolution, cinematic golden hour lighting with soft pinks and oranges, shot with 50mm lens, shallow depth of field, sharp focus on the man, vibrant bokeh with ocean sparkles, travel photography style, rich color grading with blues and golds, tropical paradise aesthetic",
    "fantasy_realm": "man, early 30s, in a mystical forest with ancient towering trees, short beard, wearing a dark green cloak with silver clasps, holding a glowing staff, surrounded by fireflies and faint magical runes in the air, ultra-realistic 8K resolution, cinematic lighting with ethereal green glow, shot with 50mm lens, shallow depth of field, sharp focus on the man, dreamy bokeh with forest textures, fantasy art style, rich color grading with greens and golds, ethereal aesthetic",
    "vintage_classic": "man, mid-40s, in a retro 1920s study, neatly combed hair, wearing a three-piece tweed suit, round glasses, seated at a wooden desk with a typewriter and vintage books, warm sepia tones, ultra-realistic 8K resolution, cinematic lighting with soft amber glow, shot with 85mm lens, shallow depth of field, sharp focus on the man, nostalgic bokeh with book textures, vintage portrait style, rich color grading with browns and creams, Great Gatsby aesthetic",
    "style_modern": "man, early 30s, in a modern urban loft with panoramic floor-to-ceiling windows, short styled hair, wearing a fitted charcoal blazer, black turtleneck, slim-fit trousers, standing against a sleek minimalist interior, ultra-realistic 8K resolution, cinematic lighting with cool blue city glow, shot with 50mm lens, shallow depth of field, sharp focus on the man, elegant bokeh with urban lights, fashion editorial style, sophisticated color grading with blacks and blues, GQ magazine aesthetic",
    "urban_street": "man, late 20s, on a bustling city street at night, short buzzed hair, wearing a black leather jacket, graphic tee, ripped jeans, standing under glowing neon signs, wet asphalt reflecting lights, ultra-realistic 8K resolution, cinematic lighting with vibrant pink and blue neon glow, shot with 35mm lens, shallow depth of field, sharp focus on the man, dynamic bokeh with urban textures, cyberpunk photography style, bold color grading with neons and blacks, cyberpunk aesthetic",
    "nature_outdoor": "man, mid-30s, in a majestic mountain landscape at dawn, short beard, wearing a flannel shirt, hiking vest, and sturdy boots, standing on a cliff with mist in the valley, towering pines in the background, ultra-realistic 8K resolution, cinematic lighting with soft pink and blue dawn glow, shot with 50mm lens, shallow depth of field, sharp focus on the man, serene bokeh with natural textures, nature photography style, rich color grading with greens and purples, outdoor adventure aesthetic",
    "cyberpunk_future": "man, early 30s, in a futuristic city at night, slicked-back hair, wearing a high-collared black coat, augmented reality glasses, standing among flying cars and holographic billboards, vibrant neon lights in pink and blue, ultra-realistic 8K resolution, cinematic lighting with dynamic neon reflections, shot with 35mm lens, shallow depth of field, sharp focus on the man, immersive bokeh with holographic textures, sci-fi photography style, bold color grading with neons and blacks, Cyberpunk 2077 aesthetic",
    "luxury_premium": "man, late 30s, in a luxurious penthouse with marble floors, short styled hair, wearing a tailored navy suit, white dress shirt, gold watch, standing against a panoramic view of a glittering city skyline at night, ultra-realistic 8K resolution, cinematic lighting with soft golden ambient glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on the man, opulent bokeh with city lights, luxury portrait style, sophisticated color grading with golds and blacks, billionaire aesthetic",
    "classic_formal": "man, mid-40s, in a grand ballroom with towering columns, neatly combed hair, wearing a black tailcoat, white wing-collar shirt, bow tie, standing under sparkling crystal chandeliers, ultra-realistic 8K resolution, cinematic lighting with warm golden chandelier glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on the man, luxurious bokeh with chandelier sparkles, classical portrait style, rich color grading with blacks and golds, royal aesthetic",
    "adventure_explorer": "man, early 30s, explorer with a short beard, wearing a rugged khaki jacket, cargo pants, leather boots, standing in dense jungle ruins, overgrown vines, sunlight filtering through canopy, ultra-realistic 8K resolution, cinematic lighting with warm green and golden hues, shot with 50mm lens, shallow depth of field, tack-sharp focus on the explorer, epic bokeh with jungle textures, adventure photography style, rich color grading with greens and browns, Indiana Jones aesthetic",
    "space_cosmic": "man, late 30s, in a futuristic spaceship interior, short buzzed hair, wearing a sleek silver jumpsuit with glowing patches, standing near a holographic star map, vibrant nebula visible through a porthole, ultra-realistic 8K resolution, cinematic lighting with cool purple and blue cosmic glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on the man, cosmic bokeh with starry textures, sci-fi photography style, rich color grading with purples and blues, Star Trek aesthetic",
    "retro_nostalgic": "man, mid-30s, in a 1950s diner, slicked-back hair, wearing a white t-shirt, blue jeans, leather jacket, standing near a glowing neon jukebox, chrome details and checkered floors, ultra-realistic 8K resolution, cinematic lighting with warm neon pink and blue glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on the man, nostalgic bokeh with diner textures, retro photography style, rich color grading with pinks and blues, Grease aesthetic",
    "street_style": "man, late 20s, in a gritty urban alley with vibrant graffiti walls, short fade haircut, wearing a black bomber jacket, graphic hoodie, ripped jeans, high-top sneakers, standing with confident swagger, ultra-realistic 8K resolution, cinematic lighting with dynamic orange and blue streetlight glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on the man, edgy bokeh with graffiti textures, streetwear photography style, bold color grading with oranges and blacks, urban fashion aesthetic",
    "artist_creative": "man, early 40s, in a sunlit studio with large windows, messy hair, wearing a loose linen shirt, paint-splattered jeans, standing near an easel with a vibrant abstract painting, natural light streaming in, ultra-realistic 8K resolution, cinematic lighting with soft daylight glow, shot with 50mm lens, shallow depth of field, sharp focus on the artist, colorful bokeh with paint textures, creative portrait style, rich color grading with warm whites and vibrant hues, artistic aesthetic",
    "futuristic_tech": "man, mid-30s, in a high-tech laboratory with holographic interfaces, short slicked-back hair, wearing a sleek black jumpsuit with glowing accents, interacting with a transparent touchscreen, ultra-realistic 8K resolution, cinematic lighting with cool blue and purple glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on the man, futuristic bokeh with holographic textures, sci-fi photography style, bold color grading with blues and silvers, Minority Report aesthetic",
    "elegant_sophisticated": "man, late 30s, in a chic Parisian café, neatly styled hair, wearing a tailored grey suit, open-collared white shirt, sipping espresso, soft ambient lighting, ultra-realistic 8K resolution, cinematic lighting with warm golden glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on the man, elegant bokeh with café textures, lifestyle photography style, sophisticated color grading with creams and browns, Parisian aesthetic",
    "photographer_camera": "man, early 30s, photographer with short tousled hair, wearing a black jacket, jeans, holding a professional DSLR camera, standing in an urban setting at golden hour, cityscape blurred in background, ultra-realistic 8K resolution, cinematic lighting with warm golden sunlight, shot with 50mm lens, shallow depth of field, tack-sharp focus on the photographer, artistic bokeh with urban textures, photography portrait style, rich color grading with oranges and blues, creative aesthetic",
    "doctor_medical": "man, mid-40s, doctor with neatly combed hair, wearing a white lab coat, blue scrubs, stethoscope around neck, standing in a modern hospital corridor with clean white walls, medical charts, kind yet professional expression, ultra-realistic 8K resolution, cinematic lighting with soft white ambient glow, warm key light on face, shot with 85mm lens, shallow depth of field, tack-sharp focus on the doctor, subtle bokeh with hospital textures, medical portrait style, clean color grading with whites and blues, professional aesthetic",
    "teacher_classroom": "man, mid-30s, teacher with short neat hair, wearing a navy sweater, white shirt, khaki trousers, standing at a chalkboard in a bright classroom, surrounded by books, educational posters, warm smile, ultra-realistic 8K resolution, cinematic lighting with soft daylight glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on the teacher, warm bokeh with classroom textures, educational portrait style, rich color grading with whites and browns, academic aesthetic",
    "businessman_portrait": "man, early 40s, confident businessman with short grey hair, wearing a tailored black suit, crisp white shirt, red tie, standing against a backdrop of modern glass skyscrapers at twilight, stylish silver watch, ultra-realistic 8K resolution, cinematic lighting with cool blue city glow, warm key light on face, shot with 85mm lens, shallow depth of field, tack-sharp focus on face, elegant bokeh with city lights, corporate portrait style, sophisticated color grading with blues and golds, Forbes magazine aesthetic"
}

# Женские промпты (оставлены без изменений для совместимости с исходным файлом)
new_female_avatar_prompts = {
    "LEGO": "Here is a comprehensive prompt for generating an 8K ultra-realistic photograph of a woman in a Lego style: \"Create a photorealistic 8K image of a woman, constructed entirely from Lego bricks, posed in a relaxed, casual stance, with a subtle smile, gazing directly at the camera, amidst a warm, golden-lit, minimalist room with a beige-colored wall, a single, small, round table with a vintage-style lamp, and a few scattered Lego bricks, with a soft, diffused light source coming from the upper left corner, creating a shallow depth of field, with the woman's face and hands in sharp focus, and the background slightly blurred, with intricate, high-contrast textures on the Lego bricks, showcasing their matte, plastic surface, and the subtle ridges and imperfections of the bricks' connection points, capturing a sense of playfulness, nostalgia, and whimsy, with a warm, comforting atmosphere, evoking a sense of childhood wonder, and a hint of steampunk-inspired, industrial-era elegance, with a focus on capturing the intricate details of the Lego construction, the soft folds of the woman's clothing, and the gentle, soft focus of the background, to create a truly unique, photorealistic image",
    "beach": "woman,Ultra-detailed and realistic photo woman, on a sunny beach with waves, high clarity, cinematic lighting",
    "zarza": "woman,Ultra-detailed 8k resolution caricature art of woman, bursting with satirical charm and vibrant humor, rendered in a high-definition digital illustration style with a playful, hand-drawn aesthetic. Created with a whimsical twist on professional photography techniques, using cinematic lighting to amplify the comedic effect—soft spotlight with exaggerated highlights and cheeky shadows for a dramatic, stage-like vibe. Imagine a Sony A7R IV + 85mm f/1.4 GM lens capturing every exaggerated detail, or a Hasselblad medium format amplifying the textures in 8k glory, with ultra-crisp facial features that pop like a cartoon brought to life. The scene unfolds on a slightly crumpled, doodle-strewn notebook page, giving it a faux-handcrafted look, as if a mischievous artist went wild with their sketchbook. At the center is a young woman, transformed into a hilariously over-the-top caricature: her eyes are comically huge, sparkling with a mischievous glint, her cheekbones exaggerated to skyscraper heights, and her grin stretches ear-to-ear in a cheeky, self-aware smirk. She’s decked out in a trendy T-shirt with a garish, oversized avocado print (because, of course, she’s that kind of millennial), paired with high-waisted trousers that are so comically tight they look like they’re about to launch her into orbit. Her pose is pure drama—one hand on her hip, thrusting it out like she’s auditioning for a reality TV show, the other hand twirling a lock of her wild, gravity-defying hair that’s styled into a chaotic explosion of curls, each strand practically bouncing with personality. The background features faint, scribbled notebook lines with tiny doodles of coffee cups and hashtags (#YOLO, anyone?), adding to the satirical vibe. The illustration style is a mix of bold, exaggerated outlines and playful shading—think visible, cartoonish pencil strokes, over-the-top cross-hatching that looks like it’s laughing at itself, and splashes of vibrant color that scream ‘look at me!’ The edges of the artwork are intentionally jagged and messy, as if the artist got carried away in a fit of giggles, leaving smudges and stray lines for that ‘just-sketched’ charm. A warm, golden glow bathes the page, as if it’s lit by a quirky desk lamp, casting playful shadows that make the caricature pop with a larger-than-life, satirical energy.",
    "fantasy": "woman,Highly detailed (8k) woman portrait photo in a magical forest with dragons, ultra-quality textures, cinematic lighting, Shot with Sony A7R IV+ 85mm f/1.4 GM lens or Canon EOS R5 with 50mm f/1.2 L lens. Hasselblad medium format",
    "vintage": "woman, Highly detailed (8k) Ultra super resolution Photo of a woman in 1920s retro style, high clarity, ultra-quality skin texture and pores, cinematic lighting, Shot with Sony A7R IV+ 85mm f/1.4 GM lens or Canon EOS R5 with 50mm f/1.2 L lens. Hasselblad medium format",
    "style": "woman,The photo shows wearing an elegant pink coat and a wide-brim hat. She stands in the center, looking directly at the camera, creating a sense of mystery and confidence. The coat emphasizes her style and sophistication, while the hat adds a retro-chic touch. In her hands, she holds a fluffy white cat, which adds to the overall charm of the picture. The background is monochrome, adding to the harmony of the scene. The lighting and focus are soft, highlighting the details of the woman's face and coat, as well as the texture of the cat. The mood of the photo is sophisticated and mysterious, with a hint of harmony. Technical parameters include portrait orientation, a close-up shot, an 85mm focal length, and high-quality detail (8K)",
    "vacation": "woman,Ultra realistic super portrait photo of nice woman, hyper-detailed, extreme photorealism, 8K resolution clear skin texture, visible pores, high definition photography, cinematic lighting, high contrast, dramatic shadows. Shot with Sony A7R IV+ 85mm f/1.4 GM lens or Canon EOS R5 with 50mm f/1.2 L lens. Hasselblad medium format, ultra-high resolution. Studio lighting with soft shadows. Cinematic lighting, Rembrandt lighting. High contrast lighting, soft shadows",
    "profession": "woman, Ultra hyper-detailed 8k resolution, high definition photography, cinematic lighting, high contrast, dramatic shadows. Shot with Sony A7R IV+ 85mm f/1.4 GM lens or Canon EOS R5 with 50mm f/1.2 L lens. Hasselblad medium format, ultra-high resolution 8k, ultra quakity of face and face textures, a meticulously rendered pencil drawing of a young woman is presented on a textured notebook page. The woman is attired in a fashionable T-shirt and high-waisted trousers. Her posture exudes confidence and composure, with one hand in her pocket and the other positioned casually at her side. Her wavy hair, styled in a relaxed manner, frames her delicate facial features. The background consists of subtle horizontal lines created by the texture of the notebook. The drawing exhibits a high level of detail while maintaining the inherent imperfection characteristic of handcrafted artistry. This effect is achieved through visible pencil strokes, fine cross-hatching, and subtle shading techniques. The edges of the image are intentionally left unrefined, accentuating the manual nature of the creation process. Natural light illuminates the page, contributing to a warm and artistic ambiance.",
    "zarz": "woman,Super-duper zany 8k mega-toon resolution, bursting with wacky colors and goofy vibes! Lit up like a Saturday morning cartoon with wild, bouncy shadows. Snapped with a Turbo-ToonCam 3000 + 85mm f/1.4 ChuckleLens or a Gigglesnap EOS R5 with a 50mm f/1.2 Whimsy L lens. Hasselblad’s cartoon-o-tron format, cranking out ultra-crisp 8k zaniness, with face textures so quirky they pop off the page! Picture a hilariously exaggerated pencil sketch of a spunky young gal, doodled on a crinkly, doodle-covered notebook page. She’s rocking a funky T-shirt with a giant pizza print and sky-high trousers that scream ‘fashion explosion!’ Her pose is pure sass—hand stuffed in her pocket like she owns the joint, the other flopping dramatically by her side. Her hair? A wild, wavy mop that bounces like it’s got a life of its own, framing a face with comically huge eyes and a cheeky grin. The background’s a scribbly mess of loopy notebook lines, all wonky and hand-drawn. The sketch is packed with nutty details but keeps that lovable, slightly bonkers hand-drawn charm—think bold, loony pencil scribbles, wiggly cross-hatching, and shading that’s more ‘BOOM!’ than subtle. The edges are gloriously messy, like the artist got carried away and spilled some fun. A burst of sunshiney light hits the page, giving it a warm, kooky glow that’s ready to leap out and tickle your funny bone!",
    "flowers": "woman,High-quality photograph with a bouquet of flowers in a garden, high clarity, ultra-quality skin texture and pores, cinematic lighting",
    "universe": "woman,Ultra-detailed and ultra-realistic photo, a determined expression, in a futuristic space, with glowing blue accents and ultra-hight resolution 8k extreme quality, ",
    "message": "woman,The article, structured in a documentary film format, explores the subject of fashion. The central figure is a woman attired in an opulent Parisian ensemble. She is positioned adjacent to a high-end black sport utility vehicle on a paved thoroughfare in a prestigious urban district. Her attire includes a crimson cropped jacket characterized by structured shoulder pads, gilded buttons, and snow-white cuffs that discreetly emerge from beneath her sleeves. Beneath the jacket, she is wearing a pristine white blouse. The jacket is complemented by a black pleated high-waisted skirt that gracefully falls just above her knees. She is also wearing sheer black tights and an elegant leather beret artfully tilted to one side, evoking a vintage aesthetic. The woman's ensemble is further accentuated by refined accessories. She is wearing oval-shaped sunglasses with slim golden frames, and in her left hand, she is holding a quilted black micro-suede handbag adorned with luxurious gold chains. Her right hand, featuring a slender golden ring, is adjusting her sunglasses.",
    "cyberpunk": "woman,Futuristic cyberpunk cityscape with neon lights, high clarity, ultra-quality textures, cinematic lighting",
    "business_woman_city": "woman, early 30s, confident businesswoman with sleek straight black hair in a low bun, flawless fair skin, wearing a tailored charcoal blazer, white silk blouse, pencil skirt, stiletto heels, holding a leather briefcase, standing against a backdrop of modern glass skyscrapers at twilight, stylish gold watch, minimalist diamond earrings, ultra-realistic 8K resolution, cinematic lighting with cool blue city glow, warm key light on face, shot with 85mm lens, shallow depth of field, tack-sharp focus on face, elegant bokeh with city lights, fashion editorial style, sophisticated color grading with blues and golds, Forbes magazine aesthetic",
    "traveler_beach": "woman, late 20s, traveler with sun-kissed skin, long wavy blonde hair loose under a wide-brimmed straw hat, wearing a flowing white maxi dress with lace details, barefoot, standing on an exotic tropical beach, turquoise ocean waves lapping gently, swaying palm trees in background, ultra-realistic 8K resolution, cinematic golden hour lighting with soft pinks and oranges, shot with 50mm lens, shallow depth of field, sharp focus on woman, dreamy bokeh with ocean sparkles, travel photography style, vibrant color grading with blues and golds, Condé Nast Traveler aesthetic",
    "fitness_girl_gym": "woman, mid-20s, fitness model with toned physique, short dark ponytail, wearing sleek black leggings, vibrant coral sports bra, neon running shoes, posing confidently in a modern gym with weight racks, mirrors, sweat-glistened skin, ultra-realistic 8K resolution, cinematic lighting with cool blue ambient glow, warm key light on body, shot with 35mm lens, shallow depth of field, tack-sharp focus on woman, dynamic bokeh with gym equipment, fitness photography style, bold color grading with blacks and neons, Women’s Health magazine aesthetic",
    "singer_microphone": "woman, late 20s, charismatic singer with long dark curls, wearing a shimmering sequined silver stage dress, dramatic smoky eyeshadow, bold red lipstick, holding a vintage microphone, performing passionately on a grand stage, dramatic spotlights casting beams through swirling stage smoke, blurred audience in background, ultra-realistic 8K resolution, cinematic lighting with vibrant purple and red hues, shot with 50mm lens, shallow depth of field, sharp focus on singer, vibrant bokeh with stage lights, concert photography style, rich color grading with deep blacks and bold colors, Billboard magazine aesthetic",
    "doctor_hospital": "woman, mid-30s, doctor with neatly tied brown hair, wearing a pristine white lab coat, blue scrubs, stethoscope around neck, standing in a modern hospital corridor with clean white walls, medical charts, kind yet professional expression, ultra-realistic 8K resolution, cinematic lighting with soft white ambient glow, warm key light on face, shot with 85mm lens, shallow depth of field, tack-sharp focus on doctor, subtle bokeh with hospital textures, medical portrait style, clean color grading with whites and blues, professional aesthetic",
    "pastry_chef_cafe": "woman, early 30s, pastry chef with a warm smile, flour-dusted cheeks, wearing a white chef’s uniform, black apron, hairnet, decorating an intricate multi-tiered cake with colorful icing, standing in a charming patisserie with pastel decor, fresh pastries displayed, ultra-realistic 8K resolution, warm cinematic lighting with golden highlights, shot with 35mm lens, shallow depth of field, sharp focus on chef, vibrant bokeh with pastry textures, food photography style, rich color grading with pinks and creams, Bon Appétit magazine aesthetic",
    "ballerina_stage": "woman, late 20s, ballerina with graceful posture, sleek blonde hair in a tight bun, wearing a delicate white tutu, pointe shoes, performing an elegant arabesque on a grand theatre stage, ethereal theatrical lighting with soft pinks and blues, velvet curtains in background, ultra-realistic 8K resolution, cinematic lighting with dramatic spotlight, shot with 50mm lens, shallow depth of field, tack-sharp focus on ballerina, dreamy bokeh with stage glow, ballet photography style, soft color grading with pastels, classical aesthetic",
    "journalist_reporter": "woman, early 30s, journalist with shoulder-length dark hair, wearing a tailored navy blazer, white blouse, holding a microphone with a news logo, reporting live from a bustling city street with blurred crowds, news vans, confident expression, ultra-realistic 8K resolution, cinematic lighting with natural daylight, subtle artificial glow, shot with 35mm lens, shallow depth of field, sharp focus on journalist, dynamic bokeh with urban textures, news photography style, vibrant color grading with blues and neutrals, CNN broadcast aesthetic",
    "fashion_model_runway": "woman, mid-20s, fashion model with striking features, high cheekbones, long straight black hair, wearing an avant-garde designer gown with bold geometric patterns, dramatic metallic eyeshadow, strutting confidently on a high-fashion runway, camera flashes illuminating the scene, ultra-realistic 8K resolution, cinematic lighting with stark white runway lights, shot with 50mm lens, shallow depth of field, tack-sharp focus on model, vibrant bokeh with light flares, fashion photography style, bold color grading with blacks and metallics, Vogue magazine aesthetic",
    "historical_queen": "woman, late 30s, regal queen with porcelain skin, long auburn hair in intricate braids, wearing an opulent velvet gown with gold embroidery, a jeweled crown adorned with rubies, seated on an ornate throne in a majestic palace hall, surrounded by marble columns, tapestries, ultra-realistic 8K resolution, cinematic lighting with warm golden chandelier glow, shot with 85mm lens, shallow depth of field, sharp focus on queen, luxurious bokeh with palace textures, historical portrait style, rich color grading with golds and reds, Renaissance painting aesthetic",
    "boho_style_nature": "woman, late 20s, bohemian beauty with long wavy chestnut hair, adorned with a floral crown, wearing a flowing cream maxi dress with lace sleeves, layered turquoise necklaces, standing in a lush field of wildflowers at golden hour, ultra-realistic 8K resolution, cinematic lighting with warm golden sunlight, shot with 50mm lens, shallow depth of field, tack-sharp focus on woman, dreamy bokeh with floral textures, bohemian photography style, vibrant color grading with greens and golds, Free People aesthetic",
    "spy_secret_mission": "woman, early 30s, enigmatic spy with sleek black hair in a high ponytail, wearing a fitted black leather catsuit, high-heeled boots, holding a silenced pistol, standing on a rooftop overlooking a neon-lit cityscape at night, ultra-realistic 8K resolution, cinematic lighting with cool blue and red neon glow, shot with 35mm lens, shallow depth of field, sharp focus on spy, atmospheric bokeh with city lights, action photography style, high-contrast color grading with blacks and neons, Mission Impossible aesthetic",
    "teacher_classroom": "woman, mid-30s, teacher with shoulder-length blonde hair, wearing a navy cardigan, white blouse, pencil skirt, standing at a chalkboard in a bright classroom, surrounded by books, educational posters, warm smile, ultra-realistic 8K resolution, cinematic lighting with soft daylight glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on teacher, warm bokeh with classroom textures, educational portrait style, rich color grading with whites and browns, academic aesthetic",
    "urban_streetwear": "woman, late 20s, in trendy urban streetwear, oversized black hoodie, ripped jeans, high-top sneakers, backwards cap, posing confidently against a vibrant graffiti-covered wall in a bustling city alley, ultra-realistic 8K resolution, cinematic lighting with dynamic orange and blue streetlight glow, shot with 35mm lens, shallow depth of field, sharp focus on woman, edgy bokeh with graffiti textures, streetwear photography style, bold color grading with oranges and blacks, urban fashion aesthetic",
    "evening_gown_event": "woman, early 30s, in a stunning emerald green evening gown with a plunging neckline, adorned with a sparkling diamond choker, long dangling earrings, standing on a red carpet at a glamorous gala, camera flashes in background, ultra-realistic 8K resolution, cinematic lighting with warm golden spotlight, shot with 85mm lens, shallow depth of field, tack-sharp focus on woman, opulent bokeh with light flares, fashion editorial style, sophisticated color grading with greens and golds, Oscars red carpet aesthetic",
    "rockstar_guitar": "woman, mid-20s, rockstar with long tousled black hair, wearing a black leather jacket, ripped band tee, studded boots, playing an electric guitar on a concert stage, vibrant stage lighting in red and purple, swirling smoke, ultra-realistic 8K resolution, cinematic lighting with dynamic stage glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on rockstar, vibrant bokeh with stage lights, concert photography style, bold color grading with reds and blacks, Joan Jett aesthetic",
    "fantasy_elf": "woman, late 20s, elven archer with pointed ears, long silver hair in a braid, wearing intricate green leather armor with silver accents, holding a carved longbow, standing in an enchanted forest with glowing bioluminescent plants, ultra-realistic 8K resolution, cinematic lighting with ethereal green and blue glow, shot with 50mm lens, shallow depth of field, sharp focus on elf, mystical bokeh with forest textures, fantasy portrait style, rich color grading with greens and silvers, Lord of the Rings aesthetic",
    "scientist_future_lab": "woman, early 30s, scientist with sleek black hair in a bun, wearing a futuristic white lab coat, augmented reality glasses, interacting with holographic displays in a cutting-edge laboratory, glowing blue interfaces, ultra-realistic 8K resolution, cinematic lighting with cool blue and purple glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on scientist, futuristic bokeh with holographic textures, sci-fi photography style, clean color grading with blues and whites, futuristic aesthetic",
    "yogini_sunset": "woman, late 20s, yogini with long wavy brown hair, wearing a fitted white yoga top, lavender leggings, performing a graceful tree pose on a serene beach at sunset, turquoise waves in background, ultra-realistic 8K resolution, cinematic golden hour lighting with soft pinks and oranges, shot with 50mm lens, shallow depth of field, tack-sharp focus on yogini, dreamy bokeh with ocean sparkles, wellness photography style, vibrant color grading with purples and golds, tranquil aesthetic",
    "artist_gallery": "woman, mid-30s, artist with short bob haircut, wearing a paint-splattered smock over a black dress, standing in a vibrant art gallery surrounded by colorful abstract paintings, natural light streaming through large windows, ultra-realistic 8K resolution, cinematic lighting with soft daylight glow, shot with 50mm lens, shallow depth of field, sharp focus on artist, colorful bokeh with canvas textures, creative portrait style, rich color grading with vibrant hues, bohemian aesthetic",
    "beach_sunset": "woman, late 20s, on a tropical beach at sunset, long wavy blonde hair, wearing a flowing coral sarong, gold anklet, standing near swaying palm trees, turquoise waves crashing gently, ultra-realistic 8K resolution, cinematic golden hour lighting with soft pinks and oranges, shot with 50mm lens, shallow depth of field, tack-sharp focus on woman, vibrant bokeh with ocean sparkles, travel photography style, rich color grading with blues and golds, tropical paradise aesthetic",
    "fantasy_magical": "woman, early 30s, in a mystical forest with ancient towering trees, long silver hair adorned with pearls, wearing a flowing lavender gown with intricate embroidery, surrounded by glowing fireflies, ultra-realistic 8K resolution, cinematic lighting with ethereal purple and green glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on woman, dreamy bokeh with forest textures, fantasy art style, rich color grading with purples and greens, ethereal aesthetic",
    "vintage_retro": "woman, mid-30s, in a retro 1920s study, sleek bob haircut, wearing a beaded flapper dress, long pearl necklace, seated at a wooden desk with vintage books, warm sepia tones, ultra-realistic 8K resolution, cinematic lighting with soft amber glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on woman, nostalgic bokeh with book textures, vintage portrait style, rich color grading with browns and creams, Great Gatsby aesthetic",
    "style_chic": "woman, early 30s, in a modern urban loft with panoramic floor-to-ceiling windows, long straight brunette hair, wearing a tailored white blazer, black silk top, high-waisted trousers, standing against a sleek minimalist interior, ultra-realistic 8K resolution, cinematic lighting with cool blue city glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on woman, elegant bokeh with urban lights, fashion editorial style, sophisticated color grading with blacks and whites, Elle magazine aesthetic",
    "urban_modern": "woman, late 20s, on a bustling city street at night, shoulder-length dark hair, wearing a black leather jacket, white crop top, high-waisted jeans, standing under glowing neon signs, wet asphalt reflecting lights, ultra-realistic 8K resolution, cinematic lighting with vibrant pink and blue neon glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on woman, dynamic bokeh with urban textures, cyberpunk photography style, bold color grading with neons and blacks, cyberpunk aesthetic",
    "nature_beauty": "woman, mid-30s, in a majestic mountain landscape at dawn, long wavy auburn hair, wearing a flowing cream sweater, khaki trousers, standing on a cliff with mist in the valley, towering pines in background, ultra-realistic 8K resolution, cinematic lighting with soft pink and blue dawn glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on woman, serene bokeh with natural textures, nature photography style, rich color grading with greens and purples, outdoor serene aesthetic",
    "cyberpunk_neon": "woman, early 30s, in a futuristic city at night, sleek black hair with neon streaks, wearing a high-collared black jumpsuit, holographic earrings, standing among flying cars, vibrant neon lights in pink and teal, ultra-realistic 8K resolution, cinematic lighting with dynamic neon reflections, shot with 35mm lens, shallow depth of field, tack-sharp focus on woman, immersive bokeh with holographic textures, sci-fi photography style, bold color grading with neons and blacks, Cyberpunk 2077 aesthetic",
    "luxury_glamour": "woman, late 30s, in a luxurious penthouse with marble floors, long sleek brunette hair, wearing a shimmering gold gown, diamond bracelet, panoramic view of a glittering city skyline at night, ultra-realistic 8K resolution, cinematic lighting with soft golden ambient glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on woman, opulent bokeh with city lights, luxury portrait style, sophisticated color grading with golds and blacks, billionaire aesthetic",
    "floral_romantic": "woman, early 30s, in a blooming rose garden, long wavy blonde hair, wearing a soft pink chiffon dress, delicate pearl earrings, surrounded by dew-kissed roses, ultra-realistic 8K resolution, cinematic lighting with soft diffused light, shot with 85mm lens, shallow depth of field, tack-sharp focus on woman, delicate bokeh with petal textures, romantic photography style, rich color grading with pinks and greens, Bridgerton aesthetic",
    "classic_elegance": "woman, mid-30s, in a grand ballroom with towering columns, sleek updo, wearing a navy velvet gown with a sweetheart neckline, sapphire necklace, standing under sparkling crystal chandeliers, ultra-realistic 8K resolution, cinematic lighting with warm golden chandelier glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on woman, luxurious bokeh with chandelier sparkles, classical portrait style, rich color grading with blues and golds, royal aesthetic",
    "adventure_travel": "woman, late 20s, explorer with short chestnut hair, wearing a rugged khaki jacket, cargo pants, leather boots, standing in dense jungle ruins, overgrown vines, sunlight filtering through canopy, ultra-realistic 8K resolution, cinematic lighting with warm green and golden hues, shot with 50mm lens, shallow depth of field, tack-sharp focus on explorer, epic bokeh with jungle textures, adventure photography style, rich color grading with greens and browns, Lara Croft aesthetic",
    "space_futuristic": "woman, early 30s, astronaut with sleek bob haircut, wearing a detailed spacesuit with glowing patches, standing inside a spaceship, vibrant nebula and twinkling stars visible through a large porthole, ultra-realistic 8K resolution, cinematic lighting with cool purple and blue cosmic glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on astronaut, cosmic bokeh with starry textures, sci-fi photography style, rich color grading with purples and blues, Star Trek aesthetic",
    "retro_vintage": "woman, mid-30s, in a 1950s diner, sleek ponytail, wearing a polka-dot swing dress, red lipstick, standing near a glowing neon jukebox, chrome details and checkered floors, ultra-realistic 8K resolution, cinematic lighting with warm neon pink and blue glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on woman, nostalgic bokeh with diner textures, retro photography style, rich color grading with pinks and blues, Grease aesthetic",
    "street_fashion": "woman, late 20s, in a gritty urban alley with vibrant graffiti walls, short bob haircut, wearing a cropped leather jacket, plaid skirt, combat boots, standing with confident swagger, ultra-realistic 8K resolution, cinematic lighting with dynamic orange and blue streetlight glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on woman, edgy bokeh with graffiti textures, streetwear photography style, bold color grading with oranges and blacks, urban fashion aesthetic",
    "artist_painter": "woman, early 40s, artist with long tied-back hair, wearing a loose linen dress, paint-splattered apron, standing in a sunlit studio with easels, vibrant paints, large canvases, natural light streaming through a large window, ultra-realistic 8K resolution, cinematic lighting with soft daylight glow, shot with 50mm lens, shallow depth of field, tack-sharp focus on artist, colorful bokeh with paint textures, creative portrait style, rich color grading with warm whites and vibrant hues, artistic aesthetic",
    "futuristic_style": "woman, mid-30s, in a futuristic interior with holographic interfaces, sleek platinum blonde hair, wearing a sleek silver jumpsuit with glowing accents, interacting with a transparent touchscreen, ultra-realistic 8K resolution, cinematic lighting with cool blue and purple glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on woman, futuristic bokeh with holographic textures, sci-fi photography style, bold color grading with blues and silvers, Minority Report aesthetic",
    "elegant_dress": "woman, late 30s, in a chic Parisian café, sleek updo, wearing a tailored black dress, pearl earrings, sipping espresso, soft ambient lighting, ultra-realistic 8K resolution, cinematic lighting with warm golden glow, shot with 85mm lens, shallow depth of field, tack-sharp focus on woman, elegant bokeh with café textures, lifestyle photography style, sophisticated color grading with creams and browns, Parisian aesthetic",
    "photographer_artist": "woman, early 30s, photographer with shoulder-length dark hair, wearing a black jacket, jeans, holding a professional DSLR camera, standing in an urban setting at golden hour, cityscape blurred in background, ultra-realistic 8K resolution, cinematic lighting with warm golden sunlight, shot with 50mm lens, shallow depth of field, tack-sharp focus on photographer, artistic bokeh with urban textures, photography portrait style, rich color grading with oranges and blues, creative aesthetic",
    "barista_cafe": "woman, late 20s, barista with a high ponytail, wearing a green apron over a white shirt, making latte art in a cozy coffee shop, warm wooden counters, fresh pastries displayed, ultra-realistic 8K resolution, cinematic lighting with soft golden glow, shot with 35mm lens, shallow depth of field, tack-sharp focus on barista, warm bokeh with café textures, lifestyle photography style, rich color grading with browns and creams, cozy aesthetic",
    "pilot_aviator": "woman, mid-30s, professional pilot with sleek bob haircut, wearing a dark navy pilot uniform with gold captain stripes, aviator sunglasses resting on head, seated in a detailed aircraft cockpit, intricate instruments, glowing control panels, clouds visible through windshield, ultra-realistic 8K resolution, cinematic lighting with cool blue cockpit glow, warm sunlight through window, shot with 50mm lens, shallow depth of field, tack-sharp focus on pilot, subtle bokeh with instrument lights, aviation photography style, rich color grading with blues and golds, Top Gun aesthetic"
}

# === НАСТРОЙКИ ДЛЯ ОБУЧЕНИЯ МОДЕЛЕЙ ===
FLUX_TRAINER_MODEL = "ostris/flux-dev-lora-trainer:4ffd32160efd92e956d39c5338a9b8fbafca58e03f791f6d8011f3e20e8ea6fa"
TRAINING_STEPS = 1000
TRAINING_BATCH_SIZE = 1
PHOTO_LIMIT_PER_REQUEST = 4
VIDEO_GENERATION_TIMEOUT = 600
MAX_TRAINING_PHOTOS = 10
MIN_TRAINING_PHOTOS = 5

# === ФУНКЦИИ ВАЛИДАЦИИ ===
def validate_lora_config():
    """Проверяет корректность конфигурации LoRA"""
    errors = []
    
    for lora_name, config in LORA_CONFIG.items():
        if not config.get("model"):
            errors.append(f"Отсутствует model для {lora_name}")
        if not isinstance(config.get("strength", 0), (int, float)):
            errors.append(f"Некорректная strength для {lora_name}")
        if config.get("strength", 0) > 1.0:
            errors.append(f"Strength > 1.0 для {lora_name}: {config.get('strength')}")
    
    if errors:
        print("⚠️ ОШИБКИ КОНФИГУРАЦИИ LORA:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("✅ Конфигурация LoRA корректна")
    
    return len(errors) == 0

def validate_styles_config():
    """Проверка согласованности стилей и промптов"""
    errors = []
    
    # Проверка мужских стилей
    for style_key in NEW_MALE_AVATAR_STYLES:
        if style_key not in new_male_avatar_prompts:
            errors.append(f"Отсутствует промпт для мужского стиля: {style_key}")
    
    # Проверка женских стилей
    for style_key in NEW_FEMALE_AVATAR_STYLES:
        if style_key not in new_female_avatar_prompts:
            errors.append(f"Отсутствует промпт для женского стиля: {style_key}")
    
    # Проверка стилей видеогенерации
    for style_key, style_name in VIDEO_GENERATION_STYLES.items():
        if style_key not in VIDEO_STYLE_PROMPTS:
            errors.append(f"Отсутствует промпт для стиля видеогенерации: {style_key}")
        # Проверка наличия эмодзи в названии стиля
        if not style_name.startswith(tuple(chr(i) for i in range(0x1F300, 0x1F6FF)) + tuple(chr(i) for i in range(0x1F900, 0x1FAFF))):
            errors.append(f"Отсутствует эмодзи в названии стиля видеогенерации: {style_name}")
    
    if errors:
        print("⚠️ ОШИБКИ КОНФИГУРАЦИИ СТИЛЕЙ:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("✅ Конфигурация стилей проверена успешно")
    
    return len(errors) == 0

def validate_models_config():
    """Проверка согласованности конфигурации моделей"""
    errors = []
    
    for model_key, model_data in IMAGE_GENERATION_MODELS.items():
        model_id = model_data['id']
        if model_id not in REPLICATE_COSTS:
            errors.append(f"Model {model_key} (ID: {model_id}) отсутствует в REPLICATE_COSTS")
    
    if errors:
        print("⚠️ ОШИБКИ КОНФИГУРАЦИИ МОДЕЛЕЙ:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("✅ Конфигурация моделей проверена успешно")
    
    return len(errors) == 0

def validate_max_quality_config():
    """Проверяет конфигурацию максимального качества"""
    errors = []
    
    for lora_name, config in LORA_CONFIG.items():
        strength = config.get("strength", 0)
        if strength > 1.0:
            errors.append(f"LoRA {lora_name} имеет силу {strength} > 1.0")
    
    for preset_name, params in GENERATION_QUALITY_PARAMS.items():
        if params.get("num_inference_steps", 0) > 50:
            errors.append(f"Preset {preset_name} имеет больше 50 шагов: {params['num_inference_steps']}")
        if params.get("output_quality", 0) != 100:
            errors.append(f"Preset {preset_name} не имеет максимального качества 100")
    
    if errors:
        print("⚠️ ОШИБКИ КОНФИГУРАЦИИ МАКСИМАЛЬНОГО КАЧЕСТВА:")
        for error in errors:
            print(f"  - {error}")
        return False
    
    print("✅ Конфигурация максимального качества проверена")
    return True

# === ДОПОЛНИТЕЛЬНЫЕ НАСТРОЙКИ КАЧЕСТВА ===
RESOLUTION_PRESETS = {
    "ultra_hd": (1440, 1440),
    "square_max": (1440, 1440),
    "portrait_max": (1080, 1440),
    "landscape_max": (1440, 1080),
}

CAMERA_PRESETS_MAX_QUALITY = {
    "portrait_ultra": {
        "camera": "Hasselblad H6D-400c MS",
        "lens": "HC 80mm f/2.8",
        "settings": "f/2.8, 1/250s, ISO 64",
        "lighting": "Profoto studio strobes with 120cm octabox",
        "resolution": "400MP medium format",
        "processing": "Phase One Capture One Pro"
    },
    "fashion_ultra": {
        "camera": "Canon EOS R5",
        "lens": "RF 85mm f/1.2L",
        "settings": "f/1.4, 1/200s, ISO 100",
        "lighting": "Natural window light + LED panels",
        "resolution": "45MP full-frame",
        "processing": "Professional retouching"
    },
    "lifestyle_ultra": {
        "camera": "Sony A7R V",
        "lens": "GM 50mm f/1.2",
        "settings": "f/1.8, 1/320s, ISO 100",
        "lighting": "Golden hour natural light",
        "resolution": "61MP full-frame",
        "processing": "Cinematic color grading"
    }
}

POST_PROCESSING_ULTRA = {
    "color_space": "ProPhoto RGB",
    "bit_depth": "16-bit",
    "resolution": "400 DPI",
    "sharpening": "Professional capture sharpening",
    "noise_reduction": "Advanced AI denoising",
    "color_grading": "Professional LUT application",
    "skin_retouching": "Frequency separation technique",
    "final_format": "Uncompressed PNG",
    "archival_quality": "Museum standard"
}

# === ФУНКЦИИ ДЛЯ МАКСИМАЛЬНОГО КАЧЕСТВА ===
def get_optimal_lora_config(prompt: str, generation_type: str) -> Dict[str, Any]:
    """Возвращает оптимальную конфигурацию LoRA для ультра-качества"""
    prompt_lower = prompt.lower()
    
    selected_loras = []
    
    # ВСЕГДА начинаем с максимального качества кожи
    selected_loras.append("skin_texture_master")
    
    # Профессиональная фотография - ВСЕГДА
    selected_loras.append("photo_realism_pro")
    
    # Анти-CGI - ОБЯЗАТЕЛЬНО
    selected_loras.append("anti_cgi")
    
    # Проверяем ключевые слова для лиц
    face_keywords = ["face", "portrait", "headshot", "closeup", "лицо", "портрет", "eyes", "глаза", "person"]
    if any(keyword in prompt_lower for keyword in face_keywords):
        selected_loras.append("face_perfection")
        selected_loras.append("portrait_master_pro")
    
    # Проверяем ключевые слова для моды
    fashion_keywords = ["fashion", "dress", "suit", "outfit", "clothes", "style", "мода", "одежда", "стиль", "luxury"]
    if any(keyword in prompt_lower for keyword in fashion_keywords):
        selected_loras.append("fashion")
    
    # Добавляем ультра-реализм если есть место
    if len(selected_loras) < MAX_LORA_COUNT:
        selected_loras.append("ultra_realism")
    
    # Добавляем цветокоррекцию если есть место
    if len(selected_loras) < MAX_LORA_COUNT:
        selected_loras.append("color_grading_pro")
    
    # Ограничиваем количество LoRA
    selected_loras = selected_loras[:MAX_LORA_COUNT - 1]  # -1 для аватара пользователя
    
    return {
        "loras": selected_loras,
        "quality_params": GENERATION_QUALITY_PARAMS["ultra_max_quality"],
        "negative_prompt": NEGATIVE_PROMPTS["ultra_realistic_max"]
    }

def get_max_quality_params(generation_type: str = "default") -> Dict[str, Any]:
    """Возвращает параметры максимального качества"""
    base_params = {
        "guidance_scale": 4.0,
        "num_inference_steps": 50,
        "scheduler": "DDIM",
        "output_quality": 100,
        "width": 1440,
        "height": 1440,
        "output_format": "png",
        "lora_scale": 1.0
    }
    
    if generation_type == "portrait":
        base_params["guidance_scale"] = 4.2
    elif generation_type == "fashion":
        base_params["guidance_scale"] = 4.5
    elif generation_type == "photorealistic":
        base_params["guidance_scale"] = 3.8
    elif generation_type == "ai_video_v2_1":
        base_params.update({
            "mode": "pro",
            "duration": 5,
            "aspect_ratio": "16:9",
            "output_format": "mp4"
        })
    
    return base_params

def get_ultra_negative_prompt(generation_type: str = "default") -> str:
    """Возвращает максимально детальный negative prompt"""
    if generation_type in ["portrait", "face", "headshot"]:
        return NEGATIVE_PROMPTS["ultra_realistic_max"]
    elif generation_type == "fashion":
        return NEGATIVE_PROMPTS["ultra_realistic_max"] + ", " + NEGATIVE_PROMPTS["fashion"]
    elif generation_type == "ai_video_v2_1":
        return (
            "blurry, pixelated, low lighting, noise, face deformations, incorrect face proportions, "
            "unnatural face expressions, face distortions, low image quality, poor detail, artifacts, "
            "unnatural movements, unrealistic, distortions, defects, generation errors, low frame rate, "
            "poor color reproduction, distorted textures, unnatural shadows, poor composition, "
            "unnatural proportions, distorted objects, unnatural camera movements, poor sound synchronization, "
            "unnatural transitions, low resolution, poor sharpness, distorted proportions, unnatural poses, "
            "distorted facial features, unnatural emotions, distorted eyes, distorted nose, distorted mouth, "
            "unnatural hair, distorted skin, unnatural hands, distorted fingers, unnatural feet, distorted paws, "
            "unnatural body proportions, distorted objects in the background"
        )
    else:
        return NEGATIVE_PROMPTS["ultra_realistic_max"]

# === СТИЛИ ВИДЕОГЕНЕРАЦИИ ===
VIDEO_GENERATION_STYLES = {
    "dynamic_action": "🏃‍♂️ Динамичное действие",
    "slow_motion": "🐢 Замедленное движение",
    "cinematic_pan": "🎥 Кинематографический панорамный вид",
    "facial_expression": "😊 Выразительная мимика",
    "object_movement": "⏳ Движение объекта",
    "dance_sequence": "💃 Танцевальная последовательность",
    "nature_flow": "🌊 Естественное течение",
    "urban_vibe": "🏙 Городская атмосфера",
    "fantasy_motion": "✨ Фантастическое движение",
    "retro_wave": "📼 Ретро-волна"
}

# === ПРОМПТЫ ДЛЯ ВИДЕОСТИЛЕЙ ===
VIDEO_STYLE_PROMPTS = {
    "dynamic_action": (
        "A dynamic action sequence of a person sprinting through a bustling urban street at dusk, wearing a sleek black jacket, with vibrant neon signs reflecting off wet asphalt, camera follows closely with smooth tracking shots, capturing rapid foot movements and intense expressions, ultra-realistic 8K resolution, cinematic lighting with pink and blue neon glow, shot with 35mm lens, shallow depth of field, vivid bokeh with urban textures, high-energy action style, rich color grading with deep blacks and neons, Blade Runner aesthetic"
    ),
    "slow_motion": (
        "A slow-motion sequence of a woman in a flowing red dress walking through a golden wheat field at sunset, her hair gently swaying in the breeze, camera captures intricate details of fabric movement and hair strands, ultra-realistic 8K resolution, cinematic golden hour lighting with warm oranges and pinks, shot with 50mm lens, shallow depth of field, dreamy bokeh with wheat textures, ethereal slow-motion style, rich color grading with golds and reds, poetic aesthetic"
    ),
    "cinematic_pan": (
        "A cinematic panning shot of a man standing on a cliff overlooking a vast mountain range, wearing a rugged hiking jacket, camera smoothly pans from his determined face to the expansive misty valleys below, ultra-realistic 8K resolution, cinematic lighting with soft pink and blue dawn glow, shot with 50mm lens, shallow depth of field, serene bokeh with mountain textures, epic landscape style, rich color grading with greens and purples, National Geographic aesthetic"
    ),
    "facial_expression": (
        "A close-up sequence focusing on a woman’s expressive face, transitioning from a contemplative gaze to a radiant smile, in a cozy café with warm lighting, camera zooms in slowly to capture subtle muscle movements and sparkling eyes, ultra-realistic 8K resolution, cinematic lighting with soft golden glow, shot with 85mm lens, shallow depth of field, intimate bokeh with café textures, emotive portrait style, rich color grading with creams and browns, cinematic aesthetic"
    ),
    "object_movement": (
        "A dynamic sequence of a vintage pocket watch swinging gently on a chain, held by a man in a tailored suit, set against a blurred luxurious study with wooden bookshelves, camera follows the watch’s hypnotic motion, ultra-realistic 8K resolution, cinematic lighting with warm amber glow, shot with 50mm lens, shallow depth of field, elegant bokeh with wood textures, vintage object-focused style, rich color grading with browns and golds, classic aesthetic"
    ),
    "dance_sequence": (
        "A vibrant dance sequence of a woman in a shimmering silver dress performing a contemporary routine on a grand stage, with swirling stage smoke and colorful spotlights, camera captures fluid body movements and dynamic spins, ultra-realistic 8K resolution, cinematic lighting with vibrant purple and blue hues, shot with 50mm lens, shallow depth of field, energetic bokeh with stage lights, dance photography style, bold color grading with neons and blacks, So You Think You Can Dance aesthetic"
    ),
    "nature_flow": (
        "A serene sequence of a man kayaking down a crystal-clear river surrounded by lush forest, camera follows smoothly capturing water ripples and paddle strokes, sunlight filtering through trees, ultra-realistic 8K resolution, cinematic lighting with soft green and golden hues, shot with 35mm lens, shallow depth of field, tranquil bokeh with water and forest textures, nature documentary style, rich color grading with greens and blues, Planet Earth aesthetic"
    ),
    "urban_vibe": (
        "A lively sequence of a woman skateboarding through a vibrant city alley with colorful graffiti walls, wearing a cropped jacket and high-top sneakers, camera follows with dynamic angles capturing wheel spins and urban energy, ultra-realistic 8K resolution, cinematic lighting with orange and blue streetlight glow, shot with 35mm lens, shallow depth of field, edgy bokeh with graffiti textures, street culture style, bold color grading with oranges and blacks, urban fashion aesthetic"
    ),
    "fantasy_motion": (
        "A mystical sequence of a woman in a flowing lavender gown casting a spell in an enchanted forest, glowing orbs of light swirling around her, camera captures fluid hand gestures and sparkling effects, ultra-realistic 8K resolution, cinematic lighting with ethereal purple and green glow, shot with 50mm lens, shallow depth of field, magical bokeh with forest textures, fantasy film style, rich color grading with purples and greens, Lord of the Rings aesthetic"
    ),
    "retro_wave": (
        "A retro-styled sequence of a man in a neon-colored jacket driving a vintage car along a coastal highway at night, neon signs and palm trees in the background, camera captures smooth car motion and glowing reflections, ultra-realistic 8K resolution, cinematic lighting with vibrant pink and teal neon glow, shot with 35mm lens, shallow depth of field, nostalgic bokeh with neon textures, retro synthwave style, bold color grading with pinks and blues, Miami Vice aesthetic"
    )
}

# === ФУНКЦИИ ВАЛИДАЦИИ ===
def validate_lora_config():
    """Проверяет корректность конфигурации LoRA"""
    errors = []
    
    for lora_name, config in LORA_CONFIG.items():
        if not config.get("model"):
            errors.append(f"Отсутствует model для {lora_name}")
        if not isinstance(config.get("strength", 0), (int, float)):
            errors.append(f"Некорректная strength для {lora_name}")
        if config.get("strength", 0) > 1.0:
            errors.append(f"Strength > 1.0 для {lora_name}: {config.get('strength')}")
    
    if errors:
        print("⚠️ ОШИБКИ КОНФИГУРАЦИИ LORA:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("✅ Конфигурация LoRA корректна")
    
    return len(errors) == 0

def validate_styles_config():
    """Проверка согласованности стилей и промптов"""
    errors = []
    
    # Проверка мужских стилей
    for style_key in NEW_MALE_AVATAR_STYLES:
        if style_key not in new_male_avatar_prompts:
            errors.append(f"Отсутствует промпт для мужского стиля: {style_key}")
    
    # Проверка женских стилей
    for style_key in NEW_FEMALE_AVATAR_STYLES:
        if style_key not in new_female_avatar_prompts:
            errors.append(f"Отсутствует промпт для женского стиля: {style_key}")
    
    # Проверка стилей видеогенерации
    for style_key in VIDEO_GENERATION_STYLES:
        if style_key not in VIDEO_STYLE_PROMPTS:
            errors.append(f"Отсутствует промпт для стиля видеогенерации: {style_key}")
    
    if errors:
        print("⚠️ ОШИБКИ КОНФИГУРАЦИИ СТИЛЕЙ:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("✅ Конфигурация стилей проверена успешно")
    
    return len(errors) == 0

# === ЭКСПОРТ КОНСТАНТ ===
__all__ = [
    'MULTI_LORA_MODEL', 'HF_LORA_MODELS', 'LORA_CONFIG', 'LORA_PRIORITIES',
    'LORA_STYLE_PRESETS', 'MAX_LORA_COUNT', 'USER_AVATAR_LORA_STRENGTH',
    'ASPECT_RATIOS', 'GENERATION_TYPE_TO_MODEL_KEY', 'CAMERA_SETUP_BASE',
    'LUXURY_DETAILS_BASE', 'REPLICATE_COSTS', 'GENERATION_QUALITY_PARAMS',
    'NEGATIVE_PROMPTS', 'CAMERA_SETUP_ENHANCED', 'IMAGE_GENERATION_MODELS',
    'GENERATION_STYLES', 'NEW_MALE_AVATAR_STYLES', 'NEW_FEMALE_AVATAR_STYLES',
    'VIDEO_GENERATION_STYLES', 'VIDEO_STYLE_PROMPTS',
    'style_prompts', 'new_male_avatar_prompts', 'new_female_avatar_prompts',
    'validate_lora_config', 'validate_styles_config', 'validate_models_config',
    'validate_max_quality_config', 'get_optimal_lora_config',
    'get_max_quality_params', 'get_ultra_negative_prompt',
    'RESOLUTION_PRESETS', 'CAMERA_PRESETS_MAX_QUALITY', 'POST_PROCESSING_ULTRA',
    'FLUX_TRAINER_MODEL', 'TRAINING_STEPS', 'TRAINING_BATCH_SIZE',
    'PHOTO_LIMIT_PER_REQUEST', 'VIDEO_GENERATION_TIMEOUT',
    'MAX_TRAINING_PHOTOS', 'MIN_TRAINING_PHOTOS'
]

print("🚀 КОНФИГУРАЦИЯ ГЕНЕРАЦИИ МАКСИМАЛЬНОГО КАЧЕСТВА ЗАГРУЖЕНА!")
print("📸 МАКСИМУМ для ультра-детализации")
print("🖼️ Фото без сжатия - МАКСИМАЛЬНОЕ разрешение")
print("🎯 Профессиональные ИИ модели с максимальными силами")
print("⚡ Анти-Блеск фильтры на максимуме")
print("🎨 Высокое качество съемки")
print("💎 ГОТОВ К СОЗДАНИЮ ШЕДЕВРОВ!")