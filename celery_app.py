"""
UnredactServiceOp - Celery Application Configuration
Production-ready Celery setup with Redis broker, result backend, and task configuration.
"""

import os
from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

# ==============================================================================
# CONFIGURATION - All heavy tasks MUST be async Celery tasks
# ==============================================================================

# Redis configuration - single source of truth for all Redis connections
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', REDIS_URL)

# Initialize Celery app with unique name
celery_app = Celery('unredactserviceop')

# ==============================================================================
# CELERY CONFIGURATION - Optimized for 100k-user scalability
# ==============================================================================

celery_app.conf.update(
    # Broker settings - Redis with connection pooling
    broker_url=CELERY_BROKER_URL,
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=10,
    broker_pool_limit=50,  # Connection pool for high concurrency
    
    # Result backend - Redis with serialization
    result_backend=CELERY_RESULT_BACKEND,
    result_expires=3600,  # Results expire after 1 hour
    result_serializer='json',
    result_compression='gzip',
    
    # Task serialization
    task_serializer='json',
    accept_content=['json'],
    task_compression='gzip',
    
    # Time limits - Hard limits for resource protection
    task_soft_time_limit=int(os.getenv('TASK_SOFT_TIME_LIMIT', '120')),
    task_time_limit=int(os.getenv('TASK_HARD_TIME_LIMIT', '180')),
    
    # Worker configuration - Fair scheduling for 50+ concurrent jobs
    worker_prefetch_multiplier=1,  # Disable prefetch for fair scheduling
    worker_max_tasks_per_child=50,  # Restart worker after 50 tasks to prevent memory leaks
    worker_disable_rate_limits=True,
    
    # Task routing - Queue configuration
    task_routes={
        'tasks.process_upload': {'queue': 'high_priority'},
        'tasks.analyze_pdf': {'queue': 'pdf_processing'},
        'tasks.analyze_image': {'queue': 'image_processing'},
        'tasks.cleanup_temp_files': {'queue': 'cleanup'},
    },
    
    # Queue definitions with priority support
    task_queues=(
        Queue('high_priority', Exchange('high_priority'), routing_key='high_priority'),
        Queue('pdf_processing', Exchange('pdf_processing'), routing_key='pdf'),
        Queue('image_processing', Exchange('image_processing'), routing_key='image'),
        Queue('cleanup', Exchange('cleanup'), routing_key='cleanup'),
        Queue('default', Exchange('default'), routing_key='default'),
    ),
    
    # Default queue
    task_default_queue='default',
    task_default_exchange='default',
    task_default_routing_key='default',
    
    # Dead letter queue configuration
    task_acks_late=True,  # Acknowledge after task completion (not start)
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    
    # Beat schedule for periodic tasks
    beat_schedule={
        'cleanup-temp-files': {
            'task': 'tasks.cleanup_temp_files',
            'schedule': crontab(minute='*/30'),  # Run every 30 minutes
            'options': {'queue': 'cleanup'},
        },
        'cleanup-old-results': {
            'task': 'tasks.cleanup_old_results',
            'schedule': crontab(hour='*/6'),  # Run every 6 hours
        },
    },
    
    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
    task_track_started=True,
    
    # Performance tuning
    broker_transport_options={
        'visibility_timeout': 3600,
        'fanout_prefix': True,
        'fanout_patterns': True,
    },
    
    # Redis result backend settings
    redis_max_connections=50,
    redis_socket_timeout=5,
    redis_socket_connect_timeout=5,
)

# ==============================================================================
# CELERY BEAT SCHEDULER - Persistent schedule storage
# ==============================================================================

# Use django-celery-beat for database-backed schedule in production
# For simplicity, we use the default scheduler with file-based schedule

# ==============================================================================
# AUTODISCOVER TASKS - Auto-discover tasks from 'tasks' module
# ==============================================================================

celery_app.autodiscover_tasks(['tasks'], force=True)

# ==============================================================================
# HEALTH CHECK - Celery ping endpoint
# ==============================================================================

@celery_app.task(name='tasks.ping')
def ping():
    """Health check task for monitoring."""
    return {'status': 'ok', 'timestamp': __import__('time').time()}


# ==============================================================================
# SIGNAL HANDLING - Graceful shutdown and error handling
# ==============================================================================

@celery_app.signals.worker_init.connect
def on_worker_init(**kwargs):
    """Initialize worker resources."""
    import loguru
    loguru.logger.info("Celery worker initialized")


@celery_app.signals.worker_shutdown.connect
def on_worker_shutdown(**kwargs):
    """Cleanup worker resources."""
    import loguru
    import gc
    loguru.logger.info("Celery worker shutting down")
    gc.collect()


@celery_app.signals.task_prerun.connect
def on_task_prerun(task_id, task, *args, **kwargs):
    """Log task start."""
    import loguru
    loguru.logger.info(f"Task {task_id} ({task.name}) started")


@celery_app.signals.task_postrun.connect
def on_task_postrun(task_id, task, *args, **kwargs):
    """Log task completion and trigger garbage collection."""
    import loguru
    import gc
    loguru.logger.info(f"Task {task_id} ({task.name}) completed")
    gc.collect()


@celery_app.signals.task_failure.connect
def on_task_failure(task_id, exception, traceback, *args, **kwargs):
    """Log task failure with full traceback."""
    import loguru
    loguru.logger.error(f"Task {task_id} failed: {exception}\n{traceback}")
    gc.collect()
