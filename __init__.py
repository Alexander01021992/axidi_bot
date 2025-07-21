# generation/__init__.py
"""Модуль генерации изображений, видео и обучения моделей"""

from .utils import (
    TempFileManager,
    reset_generation_context,
    send_message_with_fallback,
    send_photo_with_retry,
    send_media_group_with_retry,
    run_replicate_async
)

from .images import (
    process_prompt,
    upload_image_to_replicate,
    generate_image
)

from .videos import (
    generate_video,
    handle_generate_video_callback,
    check_video_status,
    check_video_status_with_delay,
    check_pending_video_tasks
)

from .training import (
    start_training,
    send_training_progress,
    send_training_progress_with_delay,
    check_training_status,
    check_training_status_with_delay,
    check_pending_trainings
)

__all__ = [
    # Utils
    'TempFileManager',
    'reset_generation_context',
    'send_message_with_retry',
    'send_photo_with_retry',
    'send_media_group_with_retry',
    'run_replicate_async',
    
    # Images
    'process_prompt',
    'upload_image_to_replicate',
    'generate_image',
    
    # Videos
    'generate_video',
    'handle_generate_video',
    'check_video_status',
    'check_video_status_with_delay',
    'check_pending_video_tasks',
    
    # Training
    'start_training',
    'send_training_progress',
    'send_training_progress_with_delay',
    'check_training_status',
    'check_training_status_with_delay',
    'check_pending_trainings'
]