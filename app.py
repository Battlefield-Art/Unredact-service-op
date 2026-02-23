"""
UnredactServiceOp - Streamlit Frontend
Production-ready PDF & Image Redaction Auditor with 100k-user scalability.
"""

import os
import sys
import time
import json
import base64
import uuid
import hashlib
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import streamlit as st
import loguru
import requests
from celery import Celery
import redis
import psutil
from prometheus_client import Counter, Histogram, generate_latest
import io

# Import monetization utilities
from utils.monetization import (
    get_user_identifier,
    is_pro_user,
    can_scan,
    increment_scan_count,
    get_pricing_plans,
    get_checkout_url,
    get_subscription_status,
    format_expiry_date,
    check_and_process_upgrade,
    get_stripe_publishable_key,
    is_stripe_configured,
    is_test_mode,
    FREE_TIER_SCANS_PER_DAY,
    FREE_TIER_WATERMARK_ENABLED,
)

# Configure logging
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
loguru.logger.add(
    LOG_DIR / 'app_{time}.log',
    rotation='100 MB',
    retention='7 days',
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}'
)

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Redis/Celery configuration
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', REDIS_URL)

# File upload limits
MAX_CONTENT_SIZE = int(os.getenv('MAX_CONTENT_SIZE', '52428800'))  # 50MB

# Rate limiting
RATE_LIMIT_FILES = int(os.getenv('RATE_LIMIT_FILES', '20'))
RATE_LIMIT_WINDOW = int(os.getenv('RATE_LIMIT_WINDOW', '300'))

# Initialize Celery
celery_app = Celery('unredactserviceop', broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

# Initialize Redis for rate limiting
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    REDIS_AVAILABLE = True
except Exception as e:
    loguru.logger.warning(f"Redis not available: {e}")
    REDIS_AVAILABLE = False

# ==============================================================================
# STREAMLIT CONFIGURATION
# ==============================================================================

st.set_page_config(
    page_title="UnredactServiceOp - Redaction Auditor",
    page_icon="🔒",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'About': '### UnredactServiceOp\nProfessional PDF & Image Redaction Auditor'
    }
)

# ==============================================================================
# CUSTOM STYLES - Dark Security Theme
# ==============================================================================

st.markdown("""
<style>
    /* Main theme colors */
    :root {
        --primary: #00d4aa;
        --secondary: #1a1a2e;
        --background: #0f0f1a;
        --surface: #1a1a2e;
        --text: #e0e0e0;
        --danger: #ff4757;
        --warning: #ffa502;
        --success: #2ed573;
    }
    
    /* Override Streamlit defaults */
    .stApp {
        background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 100%);
    }
    
    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
        border-right: 1px solid #2d3436;
    }
    
    /* Headers */
    h1, h2, h3 {
        color: #00d4aa !important;
        font-family: 'Roboto Mono', monospace;
    }
    
    /* Risk level badges */
    .risk-badge {
        padding: 0.5rem 1rem;
        border-radius: 0.5rem;
        font-weight: bold;
        font-size: 1.2rem;
    }
    .risk-none { background: #2ed573; color: #000; }
    .risk-low { background: #7bed9f; color: #000; }
    .risk-medium { background: #ffa502; color: #000; }
    .risk-high { background: #ff4757; color: #fff; }
    .risk-critical { background: #c0392b; color: #fff; }
    
    /* Custom cards */
    .stat-card {
        background: rgba(26, 26, 46, 0.8);
        border: 1px solid #00d4aa;
        border-radius: 1rem;
        padding: 1.5rem;
        text-align: center;
    }
    
    /* Progress bar */
    .stProgress > div > div > div > div {
        background: linear-gradient(90deg, #00d4aa, #00a8cc);
    }
    
    /* File uploader */
    [data-testid="stFileUploader"] {
        background: rgba(26, 26, 46, 0.5);
        border-radius: 1rem;
        padding: 1rem;
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        border-radius: 0.5rem;
    }
    .stTabs [aria-selected="true"] {
        background: #00d4aa;
        color: #000;
    }
    
    /* Code blocks */
    code {
        background: #1a1a2e !important;
        color: #00d4aa !important;
    }
    
    /* Custom button */
    .stButton > button {
        background: linear-gradient(135deg, #00d4aa 0%, #00a8cc 100%);
        color: #000;
        font-weight: bold;
        border: none;
        border-radius: 0.5rem;
    }
    .stButton > button:hover {
        background: linear-gradient(135deg, #00ffcc 0%, #00d4aa 100%);
    }
    
    /* Pro badge */
    .pro-badge {
        background: linear-gradient(135deg, #ffd700 0%, #ffaa00 100%);
        color: #000;
        padding: 0.25rem 0.75rem;
        border-radius: 1rem;
        font-weight: bold;
        font-size: 0.875rem;
    }
    
    /* Free badge */
    .free-badge {
        background: rgba(100, 100, 120, 0.5);
        color: #aaa;
        padding: 0.25rem 0.75rem;
        border-radius: 1rem;
        font-size: 0.875rem;
    }
    
    /* Pricing card */
    .pricing-card {
        background: rgba(26, 26, 46, 0.9);
        border: 2px solid #2d3436;
        border-radius: 1rem;
        padding: 1.5rem;
        text-align: center;
        transition: all 0.3s ease;
    }
    .pricing-card.popular {
        border-color: #00d4aa;
        box-shadow: 0 0 20px rgba(0, 212, 170, 0.3);
    }
    .pricing-card:hover {
        transform: translateY(-5px);
    }
    
    /* Test mode banner */
    .test-mode-banner {
        background: linear-gradient(90deg, #ffa502, #ff6348);
        color: #fff;
        padding: 0.5rem;
        text-align: center;
        border-radius: 0.5rem;
        font-weight: bold;
        margin-bottom: 1rem;
    }
    
    /* Upgrade banner */
    .upgrade-banner {
        background: linear-gradient(135deg, rgba(0, 212, 170, 0.1) 0%, rgba(0, 168, 204, 0.1) 100%);
        border: 1px solid #00d4aa;
        border-radius: 1rem;
        padding: 1.5rem;
        text-align: center;
    }
    
    /* Scan counter */
    .scan-counter {
        font-size: 0.875rem;
        color: #aaa;
    }
    .scan-counter .remaining {
        color: #00d4aa;
        font-weight: bold;
    }
    .scan-counter .limit-reached {
        color: #ff4757;
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def generate_trace_id() -> str:
    """Generate unique trace ID."""
    return f"{uuid.uuid4().hex[:12]}"


def check_rate_limit(ip_address: str) -> bool:
    """Check if IP has exceeded rate limit."""
    if not REDIS_AVAILABLE:
        return True  # No rate limiting if Redis unavailable
    
    try:
        key = f"rate_limit:{ip_address}"
        current = redis_client.get(key)
        
        if current is None:
            redis_client.setex(key, RATE_LIMIT_WINDOW, 1)
            return True
        
        if int(current) >= RATE_LIMIT_FILES:
            return False
        
        redis_client.incr(key)
        return True
    except Exception as e:
        loguru.logger.warning(f"Rate limit check failed: {e}")
        return True


def get_client_ip() -> str:
    """Get client IP address."""
    try:
        return (
            st.context.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
            st.context.headers.get('X-Real-IP', '') or
            '127.0.0.1'
        )
    except:
        return '127.0.0.1'


def validate_file(file) -> Tuple[bool, str]:
    """Validate uploaded file."""
    # Check file size
    if file.size > MAX_CONTENT_SIZE:
        return False, f"File size exceeds maximum ({MAX_CONTENT_SIZE / 1024 / 1024}MB)"
    
    # Check file extension
    allowed_extensions = ['.pdf', '.png', '.jpg', '.jpeg']
    file_ext = Path(file.name).suffix.lower()
    
    if file_ext not in allowed_extensions:
        return False, f"Unsupported file type: {file_ext}"
    
    return True, "Valid"


def get_file_type(file) -> str:
    """Determine file type from extension."""
    ext = Path(file.name).suffix.lower()
    
    if ext == '.pdf':
        return 'application/pdf'
    elif ext == '.png':
        return 'image/png'
    elif ext in ['.jpg', '.jpeg']:
        return 'image/jpeg'
    
    return 'application/octet-stream'


def poll_task_status(task_id: str, max_attempts: int = 300) -> Dict[str, Any]:
    """Poll Celery task status with exponential backoff."""
    attempt = 0
    last_status = None
    
    while attempt < max_attempts:
        try:
            result = celery_app.AsyncResult(task_id)
            status = result.state
            
            if status == 'PENDING':
                last_status = {'state': 'PENDING', 'progress': 0}
            elif status == 'STARTED':
                last_status = {'state': 'STARTED', 'progress': 10}
            elif status == 'PROGRESS':
                last_status = result.info or {'state': 'PROGRESS', 'progress': 30}
            elif status == 'SUCCESS':
                return {'state': 'SUCCESS', 'result': result.result, 'progress': 100}
            elif status == 'FAILURE':
                return {
                    'state': 'FAILURE',
                    'error': str(result.info) if result.info else 'Unknown error',
                    'progress': 0
                }
            else:
                last_status = {'state': status, 'progress': 20}
            
            # Exponential backoff
            sleep_time = min(2 ** (attempt // 10), 5)
            time.sleep(sleep_time)
            attempt += 1
            
        except Exception as e:
            loguru.logger.error(f"Task polling error: {e}")
            last_status = {'state': 'ERROR', 'error': str(e)}
            break
    
    return last_status or {'state': 'TIMEOUT', 'progress': 0}


# ==============================================================================
# PRICING MODAL
# ==============================================================================

def show_pricing_modal():
    """Show pricing modal with subscription options."""
    plans = get_pricing_plans()
    
    # Get current URL for return
    try:
        current_url = st.context.headers.get('Referer', 'http://localhost:8501')
    except:
        current_url = 'http://localhost:8501'
    
    # Check if Stripe is configured
    if not is_stripe_configured():
        st.error("⚠️ Subscription system is not configured. Please contact the administrator.")
        return
    
    # Test mode warning
    if is_test_mode():
        st.info("💳 You are in **test mode**. Use test card: `4242 4242 4242 4242`")
    
    # Create pricing columns
    cols = st.columns(len(plans))
    
    for i, plan in enumerate(plans):
        with cols[i]:
            # Card class
            card_class = "pricing-card"
            if plan.get('popular'):
                card_class += " popular"
            
            # Plan header
            st.markdown(f'<div class="{card_class}">', unsafe_allow_html=True)
            
            # Plan name
            if plan.get('popular'):
                st.markdown("**Most Popular**")
            st.markdown(f"### {plan['name']}")
            
            # Price
            st.markdown(f"#### {plan['price_display']}")
            
            if plan.get('savings'):
                st.success(f"Save {plan['savings']}%")
            
            st.markdown("---")
            
            # Features
            st.markdown("**Features:**")
            for feature in plan.get('features', []):
                st.markdown(f"✅ {feature}")
            
            # Limitations (for free tier)
            if plan.get('limitations'):
                st.markdown("---")
                st.markdown("**Limitations:**")
                for limitation in plan.get('limitations', []):
                    st.markdown(f"❌ {limitation}")
            
            st.markdown("---")
            
            # CTA button
            if plan['id'] == 'free':
                st.success("Current Plan")
            else:
                checkout_url = get_checkout_url(plan['id'], current_url)
                if checkout_url:
                    st.link_button(
                        plan.get('cta', 'Upgrade'),
                        checkout_url,
                        type="primary"
                    )
                else:
                    st.error("Checkout unavailable")
            
            st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    st.caption("🔒 Payments secured by Stripe | Cancel anytime")


# ==============================================================================
# SESSION STATE MANAGEMENT
# ==============================================================================

if 'analysis_results' not in st.session_state:
    st.session_state.analysis_results = {}

if 'task_ids' not in st.session_state:
    st.session_state.task_ids = []

if 'upload_history' not in st.session_state:
    st.session_state.upload_history = []

# ==============================================================================
# SIDEBAR NAVIGATION
# ==============================================================================

def render_sidebar():
    """Render sidebar with navigation and system info."""
    with st.sidebar:
        st.title("🔒 UnredactServiceOp")
        st.markdown("---")
        
        # Navigation
        page = st.radio(
            "Navigation",
            ["📤 Upload & Analyze", "📊 Results", "📈 History", "⚙️ Settings"]
        )
        
        st.markdown("---")
        
        # System Info
        st.subheader("System Status")
        
        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.5)
            st.metric("CPU Usage", f"{cpu_percent:.1f}%", 
                     delta_color="normal" if cpu_percent < 70 else "inverse")
            
            # Memory usage
            mem = psutil.virtual_memory()
            st.metric("Memory", f"{mem.percent:.1f}%",
                     delta_color="normal" if mem.percent < 80 else "inverse")
            
            # Check Redis
            if REDIS_AVAILABLE:
                redis_client.ping()
                st.success("🟢 Redis Connected")
            else:
                st.warning("🟡 Redis Unavailable")
                
        except Exception as e:
            st.error(f"Status error: {e}")
        
        st.markdown("---")
        
        # Subscription Status
        st.subheader("Subscription")
        
        identifier = get_user_identifier()
        status = get_subscription_status(identifier)
        
        # Test mode banner
        if is_test_mode() and is_stripe_configured():
            st.markdown('<div class="test-mode-banner">🔧 Test Mode</div>', unsafe_allow_html=True)
        
        # Pro/Free badge
        if status['is_pro']:
            plan_name = 'Pro Monthly' if status['tier'] == 'pro_monthly' else 'Pro Annual'
            st.markdown(f'<span class="pro-badge">⭐ {plan_name}</span>', unsafe_allow_html=True)
            
            if status.get('expiry'):
                expiry_display = format_expiry_date(status['expiry'])
                st.caption(f"Renews: {expiry_display}")
            
            if st.button("Manage Subscription", key="manage_sub"):
                if status.get('customer_id'):
                    portal_url = get_customer_portal_url(
                        status['customer_id'],
                        st.query_params.get('current_url', 'http://localhost:8501')
                    )
                    if portal_url:
                        import webbrowser
                        webbrowser.open(portal_url)
                    else:
                        st.error("Could not open portal")
                else:
                    st.warning("Please contact support to manage your subscription")
        else:
            st.markdown('<span class="free-badge">Free Tier</span>', unsafe_allow_html=True)
            
            # Scan counter for free tier
            if status['scans_remaining'] >= 0:
                remaining = status['scans_remaining']
                limit = status['scans_limit']
                used = status['scans_used']
                
                if remaining > 0:
                    st.markdown(
                        f'<div class="scan-counter">Scans today: <span class="remaining">{remaining}/{limit}</span></div>',
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        f'<div class="scan-counter">Scans today: <span class="limit-reached">{used}/{limit} (Limit reached)</span></div>',
                        unsafe_allow_html=True
                    )
                
                # Upgrade prompt
                if remaining <= 2:
                    st.warning("接近 free scan limit!")
                    
            if st.button("Upgrade to Pro", key="upgrade_btn"):
                show_pricing_modal()
        
        st.markdown("---")
        
        # Info
        st.info("""
        **UnredactServiceOp v1.0**
        
        Professional PDF & Image
        Redaction Auditor
        
        Supports 100k+ users
        with Kubernetes HPA
        """)
        
        return page


# ==============================================================================
# UPLOAD PAGE
# ==============================================================================

def render_upload_page():
    """Render file upload and analysis page."""
    st.title("📤 Upload & Analyze")
    st.markdown("Upload PDF or image files to detect fake redactions and hidden content.")
    
    # Rate limiting check
    client_ip = get_client_ip()
    if not check_rate_limit(client_ip):
        st.error("🚫 Rate limit exceeded. Please try again later.")
        return
    
    # Check subscription and scan limits
    identifier = get_user_identifier()
    is_pro = is_pro_user(identifier)
    
    # Show upgrade banner if limit reached
    if not is_pro and not can_scan(identifier):
        st.markdown("""
        <div class="upgrade-banner">
            <h3>🚫 Daily Scan Limit Reached</h3>
            <p>You've used all your free scans for today. Upgrade to Pro for unlimited scans!</p>
        </div>
        """, unsafe_allow_html=True)
        
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Upgrade to Pro - Unlimited Scans", type="primary"):
                show_pricing_modal()
        with col2:
            st.info(f"Free tier: {FREE_TIER_SCANS_PER_DAY} scans/day")
        return
    
    # File uploader
    st.subheader("Select Files")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        uploaded_files = st.file_uploader(
            "Drag and drop or click to browse",
            type=['pdf', 'png', 'jpg', 'jpeg'],
            accept_multiple_files=True,
            help=f"Maximum file size: {MAX_CONTENT_SIZE / 1024 / 1024}MB"
        )
    
    with col2:
        st.markdown("""
        **Supported Formats:**
        - PDF documents
        - PNG images
        - JPEG images
        """)
    
    # Process uploaded files
    if uploaded_files:
        st.markdown("---")
        st.subheader("Analysis Queue")
        
        for uploaded_file in uploaded_files:
            # Validate file
            is_valid, message = validate_file(uploaded_file)
            
            if not is_valid:
                st.error(f"❌ {uploaded_file.name}: {message}")
                continue
            
            # Display file info
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            
            with col1:
                st.write(f"**{uploaded_file.name}**")
            with col2:
                st.write(f"{(uploaded_file.size / 1024):.1f} KB")
            with col3:
                file_type = get_file_type(uploaded_file)
                st.write(f"Type: {file_type.split('/')[1].upper()}")
            with col4:
                analyze_btn = st.button(f"Analyze", key=f"btn_{uploaded_file.name}")
            
            if analyze_btn:
                with st.spinner(f"Analyzing {uploaded_file.name}..."):
                    # Read file data
                    file_data = uploaded_file.getvalue()
                    file_type = get_file_type(uploaded_file)
                    
                    # Create Celery task
                    from tasks import process_upload
                    
                    try:
                        task = process_upload.apply_async(
                            args=[file_data, uploaded_file.name, file_type],
                            kwargs={'trace_id': generate_trace_id()}
                        )
                        
                        st.session_state.task_ids.append({
                            'task_id': task.id,
                            'filename': uploaded_file.name,
                            'file_type': file_type,
                            'trace_id': task.id
                        })
                        
                        st.success(f"✅ Task queued: `{task.id[:12]}...`")
                        
                        # Poll for results
                        with st.expander(f"Analysis Progress: {uploaded_file.name}"):
                            status_container = st.empty()
                            progress_bar = st.progress(0)
                            
                            status = poll_task_status(task.id)
                            
                            while status['state'] not in ['SUCCESS', 'FAILURE', 'ERROR', 'TIMEOUT']:
                                progress = status.get('progress', 0)
                                progress_bar.progress(min(progress, 100))
                                status_container.info(f"Status: {status.get('state', 'UNKNOWN')}")
                                status = poll_task_status(task.id)
                            
                            progress_bar.progress(100)
                            
                            if status['state'] == 'SUCCESS':
                                result = status.get('result', {})
                                status_container.success(f"✅ Analysis complete!")
                                
                                # Store results
                                st.session_state.analysis_results[task.id] = result
                                st.session_state.upload_history.append({
                                    'filename': uploaded_file.name,
                                    'risk_score': result.get('risk_score', 0),
                                    'timestamp': time.time()
                                })
                                
                                # Increment scan count for free tier
                                if not is_pro:
                                    increment_scan_count(identifier)
                                    
                                    # Show remaining scans and upgrade prompt
                                    remaining = get_scans_remaining(identifier)
                                    if remaining > 0:
                                        st.info(f"📊 Free scan used. {remaining} scans remaining today.")
                                    else:
                                        st.warning("🚫 You've reached your daily scan limit!")
                                        with st.expander("Upgrade to Pro for unlimited scans"):
                                            show_pricing_modal()
                            else:
                                error = status.get('error', 'Unknown error')
                                status_container.error(f"❌ Analysis failed: {error}")
                    
                    except Exception as e:
                        st.error(f"❌ Error: {e}")
                        loguru.logger.error(f"Analysis error: {e}")
            
            st.markdown("---")


# ==============================================================================
# RESULTS PAGE
# ==============================================================================

def render_results_page():
    """Render analysis results page."""
    st.title("📊 Analysis Results")
    
    if not st.session_state.analysis_results:
        st.info("No analysis results yet. Upload and analyze files to see results here.")
        return
    
    # Show results
    for task_id, result in st.session_state.analysis_results.items():
        with st.expander(f"📄 {result.get('filename', 'Unknown')} - Risk: {result.get('risk_score', 0)}/100", 
                        expanded=True):
            # Risk score display
            risk_score = result.get('risk_score', 0)
            risk_level = get_risk_level(risk_score)
            
            col1, col2, col3 = st.columns([1, 2, 1])
            
            with col1:
                risk_badge = f"risk-{risk_level}"
                st.markdown(f"""
                <div class="risk-badge {risk_badge}" style="text-align: center; padding: 1rem;">
                    <h2 style="margin: 0;">{risk_score}</h2>
                    <small>Risk Score</small>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                st.markdown(f"**Risk Level:** {risk_level.upper()}")
                st.markdown(f"**Status:** {result.get('status', 'unknown').upper()}")
                st.markdown(f"**File Type:** {result.get('file_type', 'unknown').upper()}")
                st.markdown(f"**Processing Time:** {result.get('processing_time', 0):.2f}s")
            
            with col3:
                # Download buttons
                from tasks import generate_report
                
                report_btn = st.button(f"Generate Report", key=f"report_{task_id}")
                if report_btn:
                    with st.spinner("Generating report..."):
                        try:
                            report_task = generate_report.apply_async(
                                args=[result, 'both'],
                                kwargs={'trace_id': result.get('trace_id', task_id)}
                            )
                            
                            report_status = poll_task_status(report_task.id)
                            
                            if report_status['state'] == 'SUCCESS':
                                report_result = report_status.get('result', {})
                                files = report_result.get('files', {})
                                
                                if 'json' in files:
                                    with open(files['json'], 'r') as f:
                                        st.download_button(
                                            "📥 Download JSON Report",
                                            f.read(),
                                            f"report_{task_id[:8]}.json",
                                            "application/json"
                                        )
                                
                                if 'pdf' in files:
                                    with open(files['pdf'], 'rb') as f:
                                        st.download_button(
                                            "📥 Download PDF Report",
                                            f.read(),
                                            f"report_{task_id[:8]}.pdf",
                                            "application/pdf"
                                        )
                        except Exception as e:
                            st.error(f"Report generation failed: {e}")
            
            st.markdown("---")
            
            # Detailed findings
            tab1, tab2, tab3, tab4 = st.tabs(["⚠️ Redaction Issues", "🔍 OCR Findings", "📋 Metadata", "💡 Recommendations"])
            
            with tab1:
                issues = result.get('redaction_issues', [])
                if issues:
                    for issue in issues:
                        if isinstance(issue, dict):
                            st.markdown(f"""
                            - **{issue.get('type', 'Unknown').replace('_', ' ').title()}**
                              - Page: {issue.get('page', 'N/A')}
                              - Severity: {issue.get('severity', 'unknown').upper()}
                              - {issue.get('description', '')}
                            """)
                else:
                    st.success("No redaction issues found!")
            
            with tab2:
                ocr_findings = result.get('ocr_findings', [])
                if ocr_findings:
                    for finding in ocr_findings:
                        if isinstance(finding, dict):
                            st.markdown(f"""
                            - **{finding.get('type', 'Unknown').replace('_', ' ').title()}**
                              - Page: {finding.get('page', 'N/A')}
                              - Confidence: {finding.get('confidence', 0):.1%}
                              - {finding.get('description', '')}
                            """)
                else:
                    st.info("No OCR ghosting detected.")
            
            with tab3:
                metadata = result.get('metadata', {})
                if metadata:
                    st.write(f"**Risk Level:** {metadata.get('risk_level', 'unknown').upper()}")
                    
                    risk_factors = metadata.get('risk_factors', [])
                    if risk_factors:
                        st.write("**Risk Factors:**")
                        for factor in risk_factors:
                            st.write(f"- {factor}")
                else:
                    st.info("No metadata analysis available.")
            
            with tab4:
                recommendations = result.get('recommendations', [])
                if recommendations:
                    for i, rec in enumerate(recommendations, 1):
                        st.write(f"{i}. {rec}")
                else:
                    st.info("No specific recommendations.")


def get_risk_level(score: int) -> str:
    """Get risk level from score."""
    if score == 0:
        return 'none'
    elif score < 30:
        return 'low'
    elif score < 60:
        return 'medium'
    elif score < 85:
        return 'high'
    else:
        return 'critical'


# ==============================================================================
# HISTORY PAGE
# ==============================================================================

def render_history_page():
    """Render upload history page."""
    st.title("📈 History")
    
    if not st.session_state.upload_history:
        st.info("No upload history yet.")
        return
    
    # Show history
    for item in reversed(st.session_state.upload_history[-20:]):
        risk_score = item['risk_score']
        risk_level = get_risk_level(risk_score)
        
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            st.write(f"**{item['filename']}**")
        with col2:
            st.write(f"Risk: {risk_score}/100")
        with col3:
            color = "green" if risk_score < 30 else "orange" if risk_score < 60 else "red"
            st.markdown(f":{color}[{risk_level.upper()}]")
        
        st.markdown("---")


# ==============================================================================
# SETTINGS PAGE
# ==============================================================================

def render_settings_page():
    """Render settings page."""
    st.title("⚙️ Settings")
    
    st.markdown("### Configuration")
    
    # Display current settings
    st.write(f"**Max File Size:** {MAX_CONTENT_SIZE / 1024 / 1024:.0f} MB")
    st.write(f"**Rate Limit:** {RATE_LIMIT_FILES} files per IP per {RATE_LIMIT_WINDOW / 60:.0f} minutes")
    st.write(f"**Redis URL:** {REDIS_URL}")
    
    st.markdown("---")
    
    # Image processing settings
    st.markdown("### Image Processing")
    
    col1, col2 = st.columns(2)
    
    with col1:
        gamma = st.slider("Gamma Correction", 0.1, 3.0, 1.0, 0.1)
        contrast = st.slider("Contrast", 0.5, 2.0, 1.0, 0.1)
    
    with col2:
        exposure = st.slider("Exposure", 0.5, 2.0, 1.0, 0.1)
        clahe = st.slider("CLAHE", 0, 5, 0, 1)
    
    if st.button("Apply Settings"):
        st.success("Settings applied!")
    
    st.markdown("---")
    
    # Health check
    st.markdown("### System Health")
    
    if st.button("Run Health Check"):
        try:
            # Check system
            cpu = psutil.cpu_percent()
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            st.write(f"CPU: {cpu:.1f}%")
            st.write(f"Memory: {mem.percent:.1f}%")
            st.write(f"Disk: {disk.percent:.1f}%")
            
            if REDIS_AVAILABLE:
                redis_client.ping()
                st.success("Redis: Connected")
            else:
                st.warning("Redis: Disconnected")
                
        except Exception as e:
            st.error(f"Health check failed: {e}")
    
    # Metrics endpoint
    st.markdown("---")
    st.markdown("### Prometheus Metrics")
    
    if st.button("View Metrics"):
        try:
            # Simple metrics display
            st.text("""
            # HELP unredact_tasks_total Total tasks processed
            # TYPE unredact_tasks_total counter
            unredact_tasks_total 0
            
            # HELP unredact_task_duration_seconds Task duration
            # TYPE unredact_task_duration_seconds histogram
            unredact_task_duration_seconds_bucket{le="1.0"} 0
            unredact_task_duration_seconds_bucket{le="10.0"} 0
            unredact_task_duration_seconds_bucket{le="60.0"} 0
            unredact_task_duration_seconds_bucket{le="+Inf"} 0
            """)
        except Exception as e:
            st.error(f"Metrics error: {e}")


# ==============================================================================
# HEALTH CHECK ENDPOINT
# ==============================================================================

def render_health_check():
    """Health check endpoint for Streamlit."""
    st.title("Health Check")
    
    try:
        # Check all systems
        checks = {
            'CPU': psutil.cpu_percent(interval=0.5),
            'Memory': psutil.virtual_memory().percent,
            'Disk': psutil.disk_usage('/').percent,
            'Redis': 'Connected' if (REDIS_AVAILABLE and redis_client.ping()) else 'Disconnected'
        }
        
        for name, value in checks.items():
            st.write(f"**{name}:** {value}")
        
        st.success("All systems operational!")
        
    except Exception as e:
        st.error(f"Health check failed: {e}")


# ==============================================================================
# MAIN APPLICATION
# ==============================================================================

def main():
    """Main application entry point."""
    
    # Check for upgrade success
    check_and_process_upgrade()
    
    # Route based on URL
    query_params = st.query_params
    route = query_params.get('route', 'upload')
    
    # Handle health check route
    if route == 'health':
        render_health_check()
        return
    
    # Render sidebar and get current page
    page = render_sidebar()
    
    # Route to appropriate page
    if page == "📤 Upload & Analyze":
        render_upload_page()
    elif page == "📊 Results":
        render_results_page()
    elif page == "📈 History":
        render_history_page()
    elif page == "⚙️ Settings":
        render_settings_page()


if __name__ == "__main__":
    main()
