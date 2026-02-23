"""
UnredactServiceOp - Application Constants
Centralized constants for magic numbers, thresholds, and configuration values.
"""

# File size limits
MAX_CONTENT_SIZE = 52428800  # 50MB in bytes
MAX_CONTENT_SIZE_MB = 50.0  # 50MB

# Rate limiting
RATE_LIMIT_FILES = 20  # Maximum files per IP window
RATE_LIMIT_WINDOW = 300  # Time window in seconds (5 minutes)
RATE_LIMIT_WINDOW_MINUTES = 5.0

# Image processing thresholds
MIN_GAMMA = 0.1
MAX_GAMMA = 3.0
MIN_CONTRAST = 0.5
MAX_CONTRAST = 2.0
MIN_EXPOSURE = 0.5
MAX_EXPOSURE = 2.0
MIN_CLAHE = 0
MAX_CLAHE = 5

# Image detection thresholds
BLACK_REGION_MIN_WIDTH = 50
BLACK_REGION_MIN_HEIGHT = 50
BLACK_REGION_MIN_AREA = 2500
BLACK_REGION_LARGE_AREA = 10000
BLACK_REGION_COLOR_THRESHOLD = 30
SOLID_COLOR_STD_THRESHOLD = 10
SOLID_REGION_MIN_PERIMETER = 100
LSB_VARIANCE_THRESHOLD = 0.01
JPEG_BLOCK_SIZE = 8
JPEG_BLOCK_VARIANCE_THRESHOLD = 50

# Memory limits
WORKER_MAX_MEMORY_PER_TASK = 1572864000  # 1.5GB in bytes
WORKER_MAX_MEMORY_PER_TASK_MB = 1536.0

# Task time limits
TASK_SOFT_TIME_LIMIT = 120  # 2 minutes
TASK_HARD_TIME_LIMIT = 180  # 3 minutes

# Celery retry configuration
CELERY_RETRY_BACKOFF_MAX = 600  # 10 minutes
CELERY_MAX_RETRIES = 3

# Cleanup settings
TEMP_FILE_CLEANUP_AGE_HOURS = 60
TEMP_FILE_CLEANUP_AGE_DEFAULT = 24

# Task routing queues
QUEUE_HIGH_PRIORITY = 'high_priority'
QUEUE_PDF_PROCESSING = 'pdf_processing'
QUEUE_IMAGE_PROCESSING = 'image_processing'
QUEUE_CLEANUP = 'cleanup'
QUEUE_DEFAULT = 'default'

# Worker settings
WORKER_PREFETCH_MULTIPLIER = 1
WORKER_MAX_TASKS_PER_CHILD = 50
WORKER_CONCURRENCY = 4

# Redis settings
REDIS_MAX_CONNECTIONS = 50
REDIS_SOCKET_TIMEOUT = 5
REDIS_SOCKET_CONNECT_TIMEOUT = 5
REDIS_VISIBILITY_TIMEOUT = 3600
RESULT_EXPIRES = 3600  # 1 hour

# Risk score thresholds
RISK_LEVEL_NONE = 0
RISK_LEVEL_LOW = 30
RISK_LEVEL_MEDIUM = 60
RISK_LEVEL_HIGH = 85
RISK_LEVEL_CRITICAL = 100

# Processing thresholds
PDF_PARALLEL_PROCESSING_MIN_PAGES = 5
PDF_PARALLEL_WORKERS = 4
POLLING_MAX_ATTEMPTS = 300
POLLING_MAX_RETRIES = 3
POLLING_EXPONENTIAL_BASE = 2
POLLING_MAX_SLEEP = 10

# File types
ALLOWED_EXTENSIONS = ['.pdf', '.png', '.jpg', '.jpeg']
ALLOWED_MIME_TYPES = ['application/pdf', 'image/png', 'image/jpeg']

# Magic byte signatures
MAGIC_BYTES = {
    'application/pdf': b'%PDF',
    'image/png': b'\x89PNG',
    'image/jpeg': b'\xff\xd8\xff',
}

# Metadata risk fields
HIGH_RISK_FIELDS = ['gps', 'latitude', 'longitude', 'location', 'coordinates']
MEDIUM_RISK_FIELDS = ['author', 'creator', 'producer', 'artist', 'copyright']
LOW_RISK_FIELDS = ['datetime', 'timestamp', 'date', 'software', 'tool']

# Log settings
LOG_DIR = 'logs'
LOG_ROTATION_SIZE = '100 MB'
LOG_RETENTION_DAYS = 7
