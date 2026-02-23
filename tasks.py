"""
UnredactServiceOp - Celery Tasks
All heavy processing tasks MUST be async Celery tasks for 100k-user scalability.
"""

import os
import sys
import json
import time
import uuid
import gc
import hashlib
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
from functools import wraps

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from celery import Task, group
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
import loguru

# Configure logging with unique trace IDs
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
loguru.logger.add(
    LOG_DIR / 'tasks_{time}.log',
    rotation='100 MB',
    retention='7 days',
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='{time:YYYY-MM-DD HH:mm:ss} | {level} | {extra[trace_id]} | {message}'
)

# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def generate_trace_id() -> str:
    """Generate unique trace ID for request tracking."""
    return f"{uuid.uuid4().hex[:12]}"


def log_task_execution(func):
    """Decorator for consistent task logging with trace IDs."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        trace_id = generate_trace_id()
        logger = loguru.logger.bind(trace_id=trace_id)
        logger.info(f"Starting {func.__name__}")
        start_time = time.time()
        
        try:
            result = func(*args, trace_id=trace_id, logger=logger, **kwargs)
            duration = time.time() - start_time
            logger.info(f"Completed {func.__name__} in {duration:.2f}s")
            gc.collect()  # Memory cleanup after every task
            return result
        except SoftTimeLimitExceeded:
            duration = time.time() - start_time
            logger.warning(f"Task {func.__name__} soft timeout after {duration:.2f}s")
            raise
        except TimeLimitExceeded:
            duration = time.time() - start_time
            logger.error(f"Task {func.__name__} hard timeout after {duration:.2f}s")
            raise
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Task {func.__name__} failed after {duration:.2f}s: {e}\n{traceback.format_exc()}")
            gc.collect()
            raise
    
    return wrapper


def get_temp_dir() -> Path:
    """Get or create temporary directory for file storage."""
    temp_dir = Path(__file__).parent / 'temp'
    temp_dir.mkdir(exist_ok=True)
    return temp_dir


def cleanup_temp_file(file_path: Path, max_age_hours: int = 24):
    """Clean up old temporary files."""
    try:
        if file_path.exists():
            file_age = datetime.now() - datetime.fromtimestamp(file_path.stat().st_mtime)
            if file_age > timedelta(hours=max_age_hours):
                file_path.unlink()
                return True
    except Exception as e:
        loguru.logger.warning(f"Failed to cleanup {file_path}: {e}")
    return False


# ==============================================================================
# CELERY APP IMPORT
# ==============================================================================

from celery_app import celery_app

# ==============================================================================
# BASE TASK CLASS - With retry logic and error handling
# ==============================================================================

class BaseTask(Task):
    """Base task class with retry logic and error handling."""
    
    # Retry configuration - 3 retries with exponential backoff
    autoretry_for = (Exception, SoftTimeLimitExceeded, TimeLimitExceeded)
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes max backoff
    retry_jitter = True
    max_retries = 3
    
    # Resource limits
    soft_time_limit = int(os.getenv('TASK_SOFT_TIME_LIMIT', '120'))
    time_limit = int(os.getenv('TASK_HARD_TIME_LIMIT', '180'))
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Handle task failure with friendly error message."""
        logger = loguru.logger.bind(trace_id=kwargs.get('trace_id', 'unknown'))
        logger.error(f"Task {task_id} failed: {exc}")
        gc.collect()
    
    def on_success(self, retval, task_id, args, kwargs):
        """Handle task success."""
        logger = loguru.logger.bind(trace_id=kwargs.get('trace_id', 'unknown'))
        logger.info(f"Task {task_id} succeeded")
        gc.collect()


# ==============================================================================
# UPLOAD PROCESSING TASK
# ==============================================================================

@celery_app.task(base=BaseTask, bind=True, name='tasks.process_upload', 
                queue='high_priority', track_started=True)
@log_task_execution
def process_upload(self, file_data: bytes, filename: str, file_type: str,
                   trace_id: str = None, logger=None) -> Dict[str, Any]:
    """
    Process uploaded file - validates, stores, and routes to appropriate analyzer.
    
    This is the entry point for all file processing. It:
    1. Validates file type and size via magic bytes
    2. Stores file in temp directory with unique ID
    3. Routes to PDF or image analyzer
    """
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    start_time = time.time()
    temp_dir = get_temp_dir()
    
    # Generate unique file ID
    file_id = uuid.uuid4().hex
    file_extension = Path(filename).suffix.lower()
    
    # Validate file type - Magic byte validation
    if file_type not in ['application/pdf', 'image/png', 'image/jpeg']:
        # Check magic bytes as fallback
        if file_extension == '.pdf' and file_data[:4] == b'%PDF':
            file_type = 'application/pdf'
        elif file_extension in ['.png', '.jpg', '.jpeg'] and file_data[:8]:
            if file_data[1:4] == b'PNG':
                file_type = 'image/png'
            elif file_data[:3] == b'\xff\xd8\xff':
                file_type = 'image/jpeg'
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
    
    # Validate file size (50MB max)
    max_size = int(os.getenv('MAX_CONTENT_SIZE', '52428800'))
    if len(file_data) > max_size:
        raise ValueError(f"File size exceeds maximum allowed ({max_size / 1024 / 1024}MB)")
    
    # Save file to temp directory
    file_path = temp_dir / f"{file_id}{file_extension}"
    file_path.write_bytes(file_data)
    
    logger.info(f"File saved: {file_path} ({len(file_data)} bytes)")
    
    # Route to appropriate analyzer based on file type
    result = {
        'file_id': file_id,
        'filename': filename,
        'file_type': file_type,
        'file_size': len(file_data),
        'file_path': str(file_path),
        'trace_id': trace_id,
        'processing_time': time.time() - start_time,
        'status': 'routing'
    }
    
    if file_type == 'application/pdf':
        # Route to PDF analysis
        pdf_analyzer = celery_app.tasks['tasks.analyze_pdf']
        pdf_task = pdf_analyzer.apply_async(
            args=[str(file_path), trace_id],
            kwargs={'file_id': file_id, 'filename': filename},
            task_id=f"{file_id}_pdf"
        )
        result['task_id'] = pdf_task.id
        result['status'] = 'processing'
        
    elif file_type in ['image/png', 'image/jpeg']:
        # Route to image analysis
        image_analyzer = celery_app.tasks['tasks.analyze_image']
        image_task = image_analyzer.apply_async(
            args=[str(file_path), trace_id],
            kwargs={'file_id': file_id, 'filename': filename},
            task_id=f"{file_id}_img"
        )
        result['task_id'] = image_task.id
        result['status'] = 'processing'
    
    logger.info(f"File routed to analyzer: {result.get('task_id')}")
    return result


# ==============================================================================
# PDF ANALYSIS TASKS
# ==============================================================================

@celery_app.task(base=BaseTask, bind=True, name='tasks.analyze_pdf',
                queue='pdf_processing', track_started=True)
@log_task_execution
def analyze_pdf(self, file_path: str, trace_id: str = None, 
                logger=None, **kwargs) -> Dict[str, Any]:
    """
    Analyze PDF for redaction issues.
    
    Performs:
    1. Metadata extraction with risk flagging
    2. Fake redaction detection (black rectangles, hidden text)
    3. OCR ghosting detection
    4. Multi-page splitting for parallel processing
    """
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    from utils.pdf_analyzer import PDFAnalyzer
    from utils.metadata_extractor import MetadataExtractor
    from utils.ocr_detector import OCRDetector
    
    start_time = time.time()
    file_path = Path(file_path)
    file_id = kwargs.get('file_id', file_path.stem)
    filename = kwargs.get('filename', file_path.name)
    
    logger.info(f"Starting PDF analysis: {file_path}")
    
    # Initialize analyzers
    pdf_analyzer = PDFAnalyzer(trace_id=trace_id)
    metadata_extractor = MetadataExtractor(trace_id=trace_id)
    ocr_detector = OCRDetector(trace_id=trace_id)
    
    results = {
        'file_id': file_id,
        'filename': filename,
        'file_path': str(file_path),
        'trace_id': trace_id,
        'file_type': 'pdf',
        'analysis_type': 'full',
        'status': 'processing',
        'start_time': datetime.now().isoformat(),
        'page_count': 0,
        'metadata': {},
        'redaction_issues': [],
        'ocr_findings': [],
        'risk_score': 0,
        'risk_factors': []
    }
    
    try:
        # Step 1: Get page count and check if we need parallel processing
        page_count = pdf_analyzer.get_page_count(str(file_path))
        results['page_count'] = page_count
        
        logger.info(f"PDF has {page_count} pages")
        
        # Step 2: Extract metadata
        try:
            metadata = metadata_extractor.extract_pdf_metadata(str(file_path))
            results['metadata'] = metadata
            logger.info(f"Extracted metadata: {len(metadata)} fields")
        except Exception as e:
            logger.warning(f"Metadata extraction failed: {e}")
            results['metadata'] = {'error': str(e)}
        
        # Step 3: For PDFs > 5 pages, split into parallel tasks
        if page_count > 5:
            logger.info(f"Splitting PDF into parallel subtasks ({page_count} pages)")
            page_groups = split_pages_into_groups(page_count, workers=4)
            
            # Create parallel subtasks
            subtasks = []
            for i, page_group in enumerate(page_groups):
                subtask = analyze_pdf_pages.apply_async(
                    args=[str(file_path), page_group, trace_id],
                    kwargs={'file_id': file_id},
                    task_id=f"{file_id}_pages_{i}"
                )
                subtasks.append(subtask)
            
            # Wait for all subtasks with progress tracking
            subtask_results = []
            for subtask in subtasks:
                try:
                    subtask_result = subtask.get(timeout=180, propagate=False)
                    if subtask_result:
                        subtask_results.append(subtask_result)
                except Exception as e:
                    logger.warning(f"Subtask failed: {e}")
            
            # Aggregate results from subtasks
            for sr in subtask_results:
                if sr:
                    results['redaction_issues'].extend(sr.get('redaction_issues', []))
                    results['ocr_findings'].extend(sr.get('ocr_findings', []))
            
            results['subtask_count'] = len(subtasks)
        
        # Step 4: Analyze all pages for redaction issues
        try:
            redaction_issues = pdf_analyzer.detect_fake_redactions(str(file_path))
            results['redaction_issues'] = redaction_issues
            logger.info(f"Found {len(redaction_issues)} redaction issues")
        except Exception as e:
            logger.warning(f"Redaction detection failed: {e}")
            results['redaction_issues'] = [{'error': str(e)}]
        
        # Step 5: OCR ghosting detection
        try:
            ocr_findings = ocr_detector.detect_ocr_ghosting(str(file_path))
            results['ocr_findings'] = ocr_findings
            logger.info(f"OCR ghosting: {len(ocr_findings)} findings")
        except Exception as e:
            logger.warning(f"OCR detection failed: {e}")
            results['ocr_findings'] = [{'error': str(e)}]
        
        # Step 6: Calculate risk score
        risk_score, risk_factors = calculate_risk_score(
            metadata=results.get('metadata', {}),
            redaction_issues=results.get('redaction_issues', []),
            ocr_findings=results.get('ocr_findings', []),
            page_count=page_count
        )
        results['risk_score'] = risk_score
        results['risk_factors'] = risk_factors
        
        # Step 7: Generate recommendations
        results['recommendations'] = generate_recommendations(
            risk_score=risk_score,
            risk_factors=risk_factors,
            file_type='pdf'
        )
        
        results['status'] = 'completed'
        results['processing_time'] = time.time() - start_time
        results['completed_at'] = datetime.now().isoformat()
        
        logger.info(f"PDF analysis completed. Risk score: {risk_score}/100")
        
        gc.collect()
        return results
        
    except Exception as e:
        logger.error(f"PDF analysis failed: {e}\n{traceback.format_exc()}")
        results['status'] = 'failed'
        results['error'] = str(e)
        results['trace_id'] = trace_id
        raise


@celery_app.task(base=BaseTask, bind=True, name='tasks.analyze_pdf_pages',
                queue='pdf_processing', track_started=True)
@log_task_execution
def analyze_pdf_pages(self, file_path: str, page_range: List[int], 
                     trace_id: str = None, logger=None, **kwargs) -> Dict[str, Any]:
    """Analyze specific PDF pages for redaction issues (parallel subtask)."""
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    from utils.pdf_analyzer import PDFAnalyzer
    
    file_path = Path(file_path)
    file_id = kwargs.get('file_id', file_path.stem)
    
    logger.info(f"Analyzing PDF pages {page_range} for {file_path}")
    
    pdf_analyzer = PDFAnalyzer(trace_id=trace_id)
    
    results = {
        'file_id': file_id,
        'page_range': page_range,
        'redaction_issues': [],
        'ocr_findings': [],
        'status': 'processing'
    }
    
    try:
        # Analyze specific pages
        redaction_issues = pdf_analyzer.detect_fake_redactions(
            str(file_path), 
            page_numbers=page_range
        )
        results['redaction_issues'] = redaction_issues
        results['status'] = 'completed'
        
        logger.info(f"Page analysis completed: {len(redaction_issues)} issues found")
        
        gc.collect()
        return results
        
    except Exception as e:
        logger.error(f"Page analysis failed: {e}")
        results['status'] = 'failed'
        results['error'] = str(e)
        raise


def split_pages_into_groups(page_count: int, workers: int = 4) -> List[List[int]]:
    """Split page range into groups for parallel processing."""
    pages = list(range(1, page_count + 1))
    chunk_size = max(1, (page_count + workers - 1) // workers)
    return [pages[i:i + chunk_size] for i in range(0, len(pages), chunk_size)]


# ==============================================================================
# IMAGE ANALYSIS TASKS
# ==============================================================================

@celery_app.task(base=BaseTask, bind=True, name='tasks.analyze_image',
                queue='image_processing', track_started=True)
@log_task_execution
def analyze_image(self, file_path: str, trace_id: str = None,
                  logger=None, **kwargs) -> Dict[str, Any]:
    """
    Analyze image for redaction issues.
    
    Performs:
    1. Metadata extraction (EXIF) with risk flagging
    2. Image analysis for fake redactions
    3. OCR ghosting detection for images
    """
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    from utils.image_processor import ImageProcessor
    from utils.metadata_extractor import MetadataExtractor
    from utils.ocr_detector import OCRDetector
    
    start_time = time.time()
    file_path = Path(file_path)
    file_id = kwargs.get('file_id', file_path.stem)
    filename = kwargs.get('filename', file_path.name)
    
    logger.info(f"Starting image analysis: {file_path}")
    
    # Initialize analyzers
    image_processor = ImageProcessor(trace_id=trace_id)
    metadata_extractor = MetadataExtractor(trace_id=trace_id)
    ocr_detector = OCRDetector(trace_id=trace_id)
    
    results = {
        'file_id': file_id,
        'filename': filename,
        'file_path': str(file_path),
        'trace_id': trace_id,
        'file_type': 'image',
        'analysis_type': 'full',
        'status': 'processing',
        'start_time': datetime.now().isoformat(),
        'metadata': {},
        'image_analysis': {},
        'ocr_findings': [],
        'risk_score': 0,
        'risk_factors': []
    }
    
    try:
        # Step 1: Extract EXIF metadata
        try:
            metadata = metadata_extractor.extract_image_metadata(str(file_path))
            results['metadata'] = metadata
            logger.info(f"Extracted EXIF metadata: {len(metadata)} fields")
        except Exception as e:
            logger.warning(f"Metadata extraction failed: {e}")
            results['metadata'] = {'error': str(e)}
        
        # Step 2: Image analysis
        try:
            image_analysis = image_processor.analyze_image(str(file_path))
            results['image_analysis'] = image_analysis
            logger.info(f"Image analysis completed")
        except Exception as e:
            logger.warning(f"Image analysis failed: {e}")
            results['image_analysis'] = {'error': str(e)}
        
        # Step 3: OCR ghosting detection
        try:
            ocr_findings = ocr_detector.detect_ocr_ghosting_image(str(file_path))
            results['ocr_findings'] = ocr_findings
            logger.info(f"OCR ghosting: {len(ocr_findings)} findings")
        except Exception as e:
            logger.warning(f"OCR detection failed: {e}")
            results['ocr_findings'] = [{'error': str(e)}]
        
        # Step 4: Calculate risk score
        risk_score, risk_factors = calculate_risk_score(
            metadata=results.get('metadata', {}),
            redaction_issues=results.get('image_analysis', {}).get('issues', []),
            ocr_findings=results.get('ocr_findings', []),
            page_count=1
        )
        results['risk_score'] = risk_score
        results['risk_factors'] = risk_factors
        
        # Step 5: Generate recommendations
        results['recommendations'] = generate_recommendations(
            risk_score=risk_score,
            risk_factors=risk_factors,
            file_type='image'
        )
        
        results['status'] = 'completed'
        results['processing_time'] = time.time() - start_time
        results['completed_at'] = datetime.now().isoformat()
        
        logger.info(f"Image analysis completed. Risk score: {risk_score}/100")
        
        gc.collect()
        return results
        
    except Exception as e:
        logger.error(f"Image analysis failed: {e}\n{traceback.format_exc()}")
        results['status'] = 'failed'
        results['error'] = str(e)
        results['trace_id'] = trace_id
        raise


@celery_app.task(base=BaseTask, bind=True, name='tasks.process_image_adjustments',
                queue='image_processing', track_started=True)
@log_task_execution
def process_image_adjustments(self, file_path: str, adjustments: Dict[str, float],
                              trace_id: str = None, logger=None) -> Dict[str, Any]:
    """
    Process image with real-time adjustments (gamma, contrast, exposure, CLAHE).
    Used for the live preview feature.
    """
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    from utils.image_processor import ImageProcessor
    
    file_path = Path(file_path)
    logger.info(f"Processing image adjustments for {file_path}")
    
    image_processor = ImageProcessor(trace_id=trace_id)
    
    try:
        # Apply adjustments
        result = image_processor.apply_adjustments(
            str(file_path),
            gamma=adjustments.get('gamma', 1.0),
            contrast=adjustments.get('contrast', 1.0),
            exposure=adjustments.get('exposure', 1.0),
            clahe=adjustments.get('clahe', 0)
        )
        
        # Encode result as base64
        import base64
        with open(result['output_path'], 'rb') as f:
            result['image_data'] = base64.b64encode(f.read()).decode('utf-8')
        
        logger.info(f"Image adjustments applied successfully")
        
        gc.collect()
        return result
        
    except Exception as e:
        logger.error(f"Image adjustment failed: {e}")
        raise


# ==============================================================================
# REPORT GENERATION TASKS
# ==============================================================================

@celery_app.task(base=BaseTask, bind=True, name='tasks.generate_report',
                queue='high_priority', track_started=True)
@log_task_execution
def generate_report(self, analysis_results: Dict[str, Any], 
                    report_format: str = 'both', trace_id: str = None,
                    logger=None) -> Dict[str, Any]:
    """
    Generate PDF and/or JSON audit report from analysis results.
    """
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    from utils.report_generator import ReportGenerator
    
    start_time = time.time()
    logger.info(f"Generating {report_format} report")
    
    report_generator = ReportGenerator(trace_id=trace_id)
    temp_dir = get_temp_dir()
    
    results = {
        'trace_id': trace_id,
        'report_format': report_format,
        'status': 'processing',
        'files': {}
    }
    
    try:
        # Generate JSON report
        if report_format in ['json', 'both']:
            json_path = temp_dir / f"report_{trace_id}.json"
            report_generator.generate_json_report(analysis_results, str(json_path))
            results['files']['json'] = str(json_path)
            logger.info(f"JSON report generated: {json_path}")
        
        # Generate PDF report
        if report_format in ['pdf', 'both']:
            pdf_path = temp_dir / f"report_{trace_id}.pdf"
            report_generator.generate_pdf_report(analysis_results, str(pdf_path))
            results['files']['pdf'] = str(pdf_path)
            logger.info(f"PDF report generated: {pdf_path}")
        
        results['status'] = 'completed'
        results['processing_time'] = time.time() - start_time
        
        logger.info(f"Report generation completed")
        
        gc.collect()
        return results
        
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        results['status'] = 'failed'
        results['error'] = str(e)
        raise


# ==============================================================================
# CLEANUP TASKS
# ==============================================================================

@celery_app.task(base=BaseTask, bind=True, name='tasks.cleanup_temp_files',
                queue='cleanup', track_started=True)
@log_task_execution
def cleanup_temp_files(self, trace_id: str = None, logger=None) -> Dict[str, Any]:
    """
    Periodic task to clean up old temporary files.
    Runs every 30 minutes via Celery Beat.
    """
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    max_age_hours = int(os.getenv('TEMP_FILE_CLEANUP_AGE_HOURS', '60'))
    temp_dir = get_temp_dir()
    
    logger.info(f"Cleaning up temp files older than {max_age_hours} hours")
    
    cleaned_count = 0
    cleaned_size = 0
    
    try:
        for file_path in temp_dir.glob('*'):
            if cleanup_temp_file(file_path, max_age_hours):
                cleaned_count += 1
                cleaned_size += file_path.stat().st_size
        
        logger.info(f"Cleaned up {cleaned_count} files ({cleaned_size / 1024 / 1024:.2f} MB)")
        
        gc.collect()
        return {
            'status': 'completed',
            'cleaned_count': cleaned_count,
            'cleaned_size_mb': cleaned_size / 1024 / 1024
        }
        
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")
        raise


@celery_app.task(base=BaseTask, bind=True, name='tasks.cleanup_old_results',
                queue='cleanup', track_started=True)
@log_task_execution
def cleanup_old_results(self, trace_id: str = None, logger=None) -> Dict[str, Any]:
    """
    Periodic task to clean up old Celery results.
    Runs every 6 hours via Celery Beat.
    """
    if logger is None:
        logger = loguru.logger.bind(trace_id=trace_id or generate_trace_id())
    
    from celery_app import celery_app
    
    logger.info("Cleaning up old Celery results")
    
    try:
        # Inspect and revoke old tasks
        inspector = celery_app.control.inspect()
        active = inspector.active()
        
        logger.info(f"Active tasks: {len(active) if active else 0}")
        
        gc.collect()
        return {
            'status': 'completed',
            'timestamp': datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Result cleanup failed: {e}")
        raise


# ==============================================================================
# RISK SCORE CALCULATION
# ==============================================================================

def calculate_risk_score(metadata: Dict, redaction_issues: List, 
                         ocr_findings: List, page_count: int) -> tuple:
    """
    Calculate overall risk score (0-100) based on all findings.
    
    Risk factors:
    - Metadata risks: Hidden author info, GPS, creation dates (0-25 points)
    - Redaction quality: Black rectangles, hidden text (0-40 points)
    - OCR ghosting: Text remnants after redaction (0-35 points)
    """
    risk_score = 0
    risk_factors = []
    
    # Metadata risk assessment (0-25)
    metadata_risk = 0
    if metadata:
        # Check for sensitive metadata
        sensitive_keys = ['author', 'creator', 'producer', 'gps', 'location', 
                         'coordinates', 'datetime_original', 'timestamp']
        for key in sensitive_keys:
            if key.lower() in str(metadata).lower():
                metadata_risk += 5
                risk_factors.append(f"Sensitive metadata found: {key}")
        
        metadata_risk = min(25, metadata_risk)
        risk_score += metadata_risk
    
    # Redaction quality assessment (0-40)
    redaction_risk = 0
    if redaction_issues:
        for issue in redaction_issues:
            if isinstance(issue, dict):
                issue_type = issue.get('type', '')
                if 'black_rectangle' in issue_type.lower() or 'hidden_text' in issue_type.lower():
                    redaction_risk += 10
                    risk_factors.append(f"Redaction issue: {issue_type}")
                if 'hidden_object' in issue_type.lower():
                    redaction_risk += 15
                    risk_factors.append(f"Hidden object detected: {issue_type}")
    
    redaction_risk = min(40, redaction_risk)
    risk_score += redaction_risk
    
    # OCR ghosting assessment (0-35)
    ocr_risk = 0
    if ocr_findings:
        for finding in ocr_findings:
            if isinstance(finding, dict):
                confidence = finding.get('confidence', 0)
                if confidence > 0.5:
                    ocr_risk += 15
                    risk_factors.append(f"OCR ghosting detected: {confidence:.1%} confidence")
    
    ocr_risk = min(35, ocr_risk)
    risk_score += ocr_risk
    
    # Page count adjustment
    if page_count > 10:
        risk_score = min(100, risk_score + 5)
        risk_factors.append(f"Large document ({page_count} pages) - manual review recommended")
    
    risk_score = min(100, risk_score)
    
    return risk_score, risk_factors


def generate_recommendations(risk_score: int, risk_factors: List[str], 
                             file_type: str) -> List[str]:
    """Generate actionable recommendations based on risk assessment."""
    recommendations = []
    
    if risk_score == 0:
        recommendations.append("No redaction issues detected. Document appears clean.")
    elif risk_score < 30:
        recommendations.append("Low risk - Document has minor issues. Review flagged items.")
    elif risk_score < 60:
        recommendations.append("Medium risk - Significant redaction issues found. Manual review required.")
    else:
        recommendations.append("HIGH RISK - Critical redaction issues detected. DO NOT share document.")
    
    # Specific recommendations
    if any('metadata' in f.lower() for f in risk_factors):
        recommendations.append(
            "Remove all metadata before publishing: Author, GPS, timestamps, and custom fields."
        )
    
    if any('black_rectangle' in f.lower() or 'hidden_text' in f.lower() for f in risk_factors):
        recommendations.append(
            "Re-redact sensitive content using proper removal tools (Adobe Acrobat, specialized software)."
        )
    
    if any('ocr' in f.lower() or 'ghosting' in f.lower() for f in risk_factors):
        recommendations.append(
            "Run OCR-aware redaction and verify with text extraction after redaction."
        )
    
    recommendations.append(
        "Always verify redaction by attempting to extract text from redacted regions."
    )
    
    return recommendations


# ==============================================================================
# HEALTH CHECK
# ==============================================================================

@celery_app.task(base=BaseTask, bind=True, name='tasks.health_check',
                queue='high_priority')
def health_check(self, trace_id: str = None) -> Dict[str, Any]:
    """System health check task."""
    import psutil
    
    return {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'cpu_percent': psutil.cpu_percent(interval=0.1),
        'memory_percent': psutil.virtual_memory().percent,
        'disk_percent': psutil.disk_usage('/').percent,
    }
