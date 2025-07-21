import replicate
import logging
import asyncio
from typing import Optional
from config import REPLICATE_API_TOKEN
from generation_config import IMAGE_GENERATION_MODELS

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log', encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class LlamaPromptAssistant:
    """Класс для работы с Llama 3 для генерации промптов"""
    
    def __init__(self):
        self.model_id = IMAGE_GENERATION_MODELS.get("meta-llama-3-8b-instruct", {}).get("id")
        if not self.model_id:
            logger.error("Llama 3 model ID не найден в конфигурации")
            raise ValueError("Llama 3 model configuration missing")
    
    async def generate_prompt(self, user_query: str, gender: str, max_length_chars: int = 1000, generation_type: str = 'with_avatar') -> str:
        """
        Генерирует промпт с помощью Llama 3.
        
        Args:
            user_query: Введённая пользователем идея.
            gender: Пол для генерации (man, woman, person).
            max_length_chars: Максимальная длина промпта в символах.
            generation_type: Тип генерации ('with_avatar', 'photo_to_photo', 'ai_video_v2_1').
        """
        if not REPLICATE_API_TOKEN:
            logger.error("REPLICATE_API_TOKEN не установлен. Невозможно использовать Llama 3.")
            return user_query

        # Проверка и нормализация параметра gender
        valid_genders = ['man', 'woman', 'person']
        normalized_gender = gender if gender in valid_genders else 'person'
        if gender not in valid_genders + ['neutral']:
            logger.warning(f"Некорректное значение gender: '{gender}', используется 'person'")
        elif gender == 'neutral':
            logger.debug(f"Пол 'neutral' заменен на 'person' для user_query: {user_query}")
            normalized_gender = 'person'

        # Расчет максимального количества токенов
        max_new_tokens_calculated = min(500, max(100, int(max_length_chars / 3.5)))

        # Системный промпт с улучшенными инструкциями
        system_prompt = self._create_system_prompt(normalized_gender, user_query, generation_type)
        
        # Пользовательский промпт
        full_prompt_for_llama = f'User idea: "{user_query}". Desired gender focus: {normalized_gender}.'

        logger.info(f"Запрос Llama 3 для: '{user_query}', пол: {normalized_gender}, тип: {generation_type}")
        logger.debug(f"Системный промпт: {system_prompt}")
        logger.debug(f"Пользовательский промпт: {full_prompt_for_llama}")
        logger.debug(f"max_new_tokens: {max_new_tokens_calculated}")

        try:
            # Выполняем запрос к Replicate API
            output = await self._run_replicate_model(
                system_prompt, 
                full_prompt_for_llama, 
                max_new_tokens_calculated
            )
            
            # Обрабатываем и очищаем результат
            final_prompt = self._process_output(output, max_length_chars)
            
            if final_prompt and final_prompt != user_query:
                logger.info(f"Llama 3 сгенерировал промпт: {final_prompt[:100]}...")
                return final_prompt
            else:
                logger.warning("Llama 3 не смог улучшить промпт, используется оригинальный")
                return user_query
                
        except Exception as e:
            logger.error(f"Ошибка при вызове Replicate API для Llama 3: {e}", exc_info=True)
            return user_query
    
    def _create_system_prompt(self, gender: str, user_query: str, generation_type: str) -> str:
        """Создает системный промпт для Llama 3 в зависимости от типа генерации."""
        if generation_type == 'ai_video_v2_1':
            return (
                "You are an expert prompt engineer specializing in ultra-realistic AI video generation. "
                f"Based on the user's idea: '{user_query}', craft a single, comprehensive prompt for a 5-second video "
                f"featuring a {gender}. "
                "\n\n"
                "REQUIREMENTS:\n"
                "1. Focus on dynamic movements: describe specific actions, gestures, or camera motion\n"
                "2. Include visual details: appearance, clothing, setting, lighting, mood\n"
                "3. Specify textures, materials, and surface qualities\n"
                "4. Describe the atmosphere and emotional tone\n"
                "5. Use cinematic terms: camera angles, tracking shots, zooms\n"
                "6. Be specific and descriptive, avoiding vague terms\n"
                "\n"
                "OUTPUT FORMAT:\n"
                "- Single paragraph, no line breaks\n"
                "- Direct prompt text only, no explanations or alternatives\n"
                "- Optimized for 8K ultra-realistic video\n"
                "- Professional videography style\n"
                "\n"
                "Generate the prompt now:"
            )
        return (
            "You are an expert prompt engineer specializing in ultra-realistic AI image generation. "
            f"A user wants to create a photorealistic image of a {gender}. "
            f"Based on their idea: '{user_query}', craft a single, comprehensive prompt that will produce "
            "an exceptional 8K ultra-realistic photograph. "
            "\n\n"
            "REQUIREMENTS:\n"
            "1. Focus on visual details: appearance, clothing, pose, expression, setting, lighting, mood\n"
            "2. Include photographic technical details: camera angle, depth of field, lighting setup\n"
            "3. Specify textures, materials, and surface qualities\n"
            "4. Describe the atmosphere and emotional tone\n"
            "5. Be specific and descriptive, avoiding vague terms\n"
            "\n"
            "OUTPUT FORMAT:\n"
            "- Single paragraph, no line breaks\n"
            "- Direct prompt text only, no explanations or alternatives\n"
            "- Optimized for photorealistic results\n"
            "- Professional photography style\n"
            "\n"
            "Generate the prompt now:"
        )
    
    async def _run_replicate_model(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """Выполняет запрос к модели Replicate."""
        loop = asyncio.get_event_loop()
        
        # Параметры для Llama 3
        input_params = {
            "top_k": 50,
            "top_p": 0.9,
            "prompt": user_prompt,
            "temperature": 0.75,
            "system_prompt": system_prompt,
            "max_new_tokens": max_tokens,
            "stop_sequences": "<|end_of_text|>,<|eot_id|>",
            "prompt_template": (
                "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
                "{system_prompt}<|eot_id|><|start_header_id|>user<|end_header_id|>\n\n"
                "{prompt}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
            )
        }
        
        # Выполняем в отдельном потоке для совместимости с asyncio
        output_stream = await loop.run_in_executor(
            None, 
            lambda: replicate.run(self.model_id, input=input_params)
        )
        
        # Собираем результат из потока
        generated_text = "".join([str(event) for event in output_stream])
        return generated_text
    
    def _process_output(self, output: str, max_length: int) -> str:
        """Обрабатывает и очищает вывод от Llama 3."""
        if not output:
            return ""
        
        # Очищаем от лишних пробелов
        output = output.strip()
        
        # Удаляем типичные вводные фразы
        intro_phrases = [
            "here's a prompt", "here is a prompt", "sure, here's", "okay, here is",
            "i'll create", "i'll craft", "let me create", "here's your prompt:",
            "prompt:", "here's the prompt", "the prompt is"
        ]
        
        lines = output.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line_lower = line.lower().strip()
            # Пропускаем строки с вводными фразами
            if any(phrase in line_lower for phrase in intro_phrases):
                continue
            # Добавляем непустые строки
            if line.strip():
                cleaned_lines.append(line.strip())
        
        # Объединяем в один промпт
        final_prompt = " ".join(cleaned_lines)
        
        # Если после очистки промпт пустой, используем оригинальный вывод
        if not final_prompt:
            final_prompt = output
        
        # Обрезаем если слишком длинный
        if len(final_prompt) > max_length * 1.2:
            final_prompt = final_prompt[:int(max_length * 1.2)]
            # Обрезаем по последнему пробелу
            last_space = final_prompt.rfind(' ')
            if last_space != -1:
                final_prompt = final_prompt[:last_space] + "..."
        
        return final_prompt

# Глобальный экземпляр ассистента
llama_assistant = None

async def get_llama_assistant() -> LlamaPromptAssistant:
    """Получает или создает экземпляр Llama ассистента."""
    global llama_assistant
    if llama_assistant is None:
        llama_assistant = LlamaPromptAssistant()
    return llama_assistant

async def generate_assisted_prompt(user_query: str, gender: str, max_length_chars: int = 1000, generation_type: str = 'with_avatar') -> str:
    """
    Генерирует промпт с помощью Llama ассистента.
    
    Args:
        user_query: Введённая пользователем идея.
        gender: Пол для генерации (man, woman, person).
        max_length_chars: Максимальная длина промпта в символах.
        generation_type: Тип генерации ('with_avatar', 'photo_to_photo', 'ai_video_v2_1').
    """
    try:
        assistant = await get_llama_assistant()
        return await assistant.generate_prompt(user_query, gender, max_length_chars, generation_type)
    except Exception as e:
        logger.error(f"Ошибка в generate_assisted_prompt: {e}", exc_info=True)
        return user_query

# Функция для тестирования
async def test_llama_assistant():
    """Тестовая функция для проверки работы Llama ассистента."""
    test_cases = [
        ("man in a futuristic city at night, high tech, neon lights", "man", "with_avatar"),
        ("woman in a magical forest, ethereal, glowing flowers", "woman", "with_avatar"),
        ("person meditating on a mountaintop, serene, sunrise", "person", "with_avatar"),
        ("деловой человек в офисе", "man", "with_avatar"),
        ("красивая девушка на пляже", "woman", "with_avatar"),
        ("человек в парке, осень, желтые листья", "neutral", "with_avatar"),
        ("man running through a city street, dynamic action", "man", "ai_video_v2_1"),
        ("woman dancing on a stage, vibrant lights", "woman", "ai_video_v2_1"),
        ("person kayaking in a river, nature flow", "person", "ai_video_v2_1")
    ]
    
    print("\n=== Тестирование Llama Assistant ===\n")
    
    for test_query, gender, gen_type in test_cases:
        print(f"Тест: '{test_query}', Пол: {gender}, Тип: {gen_type}")
        print("-" * 50)
        
        try:
            result = await generate_assisted_prompt(test_query, gender, generation_type=gen_type)
            print(f"Результат: {result}\n")
        except Exception as e:
            print(f"Ошибка: {e}\n")
        
        await asyncio.sleep(2)  # Небольшая пауза между запросами

# Запуск тестов при прямом вызове модуля
if __name__ == '__main__':
    asyncio.run(test_llama_assistant())
    
__all__ = ['LlamaPromptAssistant', 'get_llama_assistant', 'generate_assisted_prompt']