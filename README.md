# UnredactServiceOp - PDF & Image Redaction Auditor

A professional-grade, production-ready PDF and Image Redaction Auditor designed for 100,000-user scalability. UnredactServiceOp detects fake redactions, extracts hidden metadata, performs OCR ghosting analysis, and generates comprehensive audit reports.

## Features

### Core Functionality
- **Upload System**: Drag-and-drop + browse upload supporting PDF, PNG, JPG, JPEG (max 50MB with magic-byte validation)
- **Metadata Extraction**: Extracts EXIF and PDF metadata with risk flagging
- **Fake Redaction Detection**: Detects black rectangles and hidden text/objects at exact coordinates
- **Image Processing**: Real-time OpenCV adjustments (gamma, contrast, exposure, CLAHE) with side-by-side preview
- **OCR Ghosting Detection**: Advanced OCR analysis with before/after comparison
- **Audit Reports**: Professional downloadable PDF and JSON reports with risk scores and recommendations

### Architecture
- **Frontend**: Streamlit with modern dark security-themed UI
- **Backend**: Celery 5+ with Redis (broker + result backend + cache)
- **Scalability**: Stateless frontend + horizontally scalable workers
- **Multi-page Optimization**: PDFs > 5 pages automatically split into parallel Celery subtasks
- **Real-time Progress**: Task polling with progress bars, ETA, and live logs

### Reliability
- **Crash-proof**: Full try/except with structured logging and graceful degradation
- **Retries**: 3× automatic retries with exponential backoff and dead-letter queue
- **Input Validation**: File type via python-magic, size limits, no path traversal
- **Rate Limiting**: Redis-based (20 files per IP per 5 minutes)
- **Resource Protection**: Hard timeouts (120s), memory limits (), CPU monitoring
1.5 GB- **Cleanup**: Automatic temp file deletion after 60 minutes

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Tesseract OCR (for OCR features)
- Poppler utils (for PDF processing)

### Running with Docker Compose

```bash
cd unredactserviceop
docker compose up --build
```

The application will be available at http://localhost:8501

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env

# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Start Celery worker
celery -A celery_app worker --loglevel=info

# Run Streamlit
streamlit run app.py
```

## Kubernetes Deployment

Deploy to Kubernetes using the provided manifests:

```bash
kubectl apply -f ops.yml
```

The HPA is configured to scale workers based on 70% CPU utilization.

## API Endpoints

- `/` - Main Streamlit application
- `/health` - Health check endpoint
- `/api/upload` - File upload endpoint (internal)
- `/api/status/{task_id}` - Task status endpoint (internal)

## Supported File Types

| Type | Extensions | Max Size |
|------|------------|----------|
| PDF | .pdf | 50 MB |
| PNG | .png | 50 MB |
| JPEG | .jpg, .jpeg | 50 MB |

## Risk Scores

The system calculates a risk score (0-100) based on:
- **Metadata Risks**: Hidden author info, GPS coordinates, creation dates
- **Redaction Quality**: Black rectangles, hidden text, object detection
- **OCR Ghosting**: Text remnants after apparent redaction
- **Image Analysis**: Contrast issues, embedded content

## Monitoring

Prometheus metrics are exposed at `/metrics` (when enabled). Key metrics:
- `unredact_tasks_total` - Total tasks processed
- `unredact_tasks_failed` - Failed tasks
- `unredact_task_duration_seconds` - Task duration
- `unredact_active_workers` - Active worker count

## Project Structure

```
unredactserviceop/
├── app.py                  # Streamlit frontend with task polling
├── celery_app.py           # Celery configuration
├── tasks.py                # All Celery tasks
├── Dockerfile              # Multi-stage Dockerfile for web
├── Dockerfile.worker       # Multi-stage Dockerfile for worker
├── docker-compose.yml      # Web + worker + redis + beat
├── ops.yml                 # Kubernetes manifests
├── requirements.txt        # Python dependencies
├── .env.example           # Environment template
├── README.md              # This file
├── utils/
│   ├── __init__.py
│   ├── pdf_analyzer.py    # PDF analysis and redaction detection
│   ├── image_processor.py # OpenCV image processing
│   ├── metadata_extractor.py # Metadata extraction and risk flagging
│   ├── ocr_detector.py    # OCR ghosting detection
│   └── report_generator.py # PDF and JSON report generation
├── logs/                   # Application logs (gitignored)
└── temp/                   # Temporary files (gitignored)
```

## License

MIT License

## Support

For issues and support, please contact the development team with your trace ID (available in logs).
