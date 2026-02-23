"""
UnredactServiceOp - Monetization & Subscription Management
Stripe integration, Redis-based subscription tracking, pricing tiers.
"""

import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
from functools import wraps

import redis
import loguru
import streamlit as st


# ==============================================================================
# CONFIGURATION
# ==============================================================================

REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICE_ID_MONTHLY = os.getenv('STRIPE_PRICE_ID_MONTHLY', '')
STRIPE_PRICE_ID_ANNUAL = os.getenv('STRIPE_PRICE_ID_ANNUAL', '')
STRIPE_MODE = os.getenv('STRIPE_MODE', 'test')  # 'test' or 'live'

# Free tier configuration
FREE_TIER_SCANS_PER_DAY = int(os.getenv('FREE_TIER_SCANS_PER_DAY', '5'))
FREE_TIER_WATERMARK_ENABLED = os.getenv('FREE_TIER_WATERMARK_ENABLED', 'true').lower() == 'true'

# Initialize Redis client
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    REDIS_AVAILABLE = True
except Exception as e:
    loguru.logger.warning(f"Redis not available for monetization: {e}")
    redis_client = None
    REDIS_AVAILABLE = False

# Initialize Stripe
if STRIPE_SECRET_KEY:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    STRIPE_AVAILABLE = True
else:
    STRIPE_AVAILABLE = False
    loguru.logger.warning("Stripe not configured - subscription features disabled")


# ==============================================================================
# PRICING PLANS
# ==============================================================================

def get_pricing_plans() -> List[Dict[str, Any]]:
    """Return available pricing plans."""
    return [
        {
            'id': 'free',
            'name': 'Free',
            'price': 0,
            'price_display': 'Free forever',
            'interval': 'forever',
            'price_id': None,
            'features': [
                f'{FREE_TIER_SCANS_PER_DAY} scans per day',
                'Basic redaction detection',
                'Watermarked reports',
                'Low priority processing',
            ],
            'limitations': [
                'Limited daily scans',
                'Watermarked reports',
                'Queue priority: Low',
            ]
        },
        {
            'id': 'pro_monthly',
            'name': 'Pro Monthly',
            'price': 3.00,
            'price_display': '$3.00/month',
            'interval': 'month',
            'price_id': STRIPE_PRICE_ID_MONTHLY,
            'features': [
                'Unlimited scans',
                'Full redaction detection',
                'No watermarks',
                'High priority processing',
                'Priority support',
            ],
            'cta': 'Upgrade to Pro',
            'popular': True,
        },
        {
            'id': 'pro_annual',
            'name': 'Pro Annual',
            'price': 29.00,
            'price_display': '$29.00/year',
            'interval': 'year',
            'price_id': STRIPE_PRICE_ID_ANNUAL,
            'savings': 19,  # 19% savings
            'features': [
                'Unlimited scans',
                'Full redaction detection',
                'No watermarks',
                'High priority processing',
                'Priority support',
                'Save 19%',
            ],
            'cta': 'Go Pro - Save 19%',
            'popular': False,
        }
    ]


def get_stripe_publishable_key() -> str:
    """Get Stripe publishable key for frontend."""
    return STRIPE_PUBLISHABLE_KEY


def is_stripe_configured() -> bool:
    """Check if Stripe is properly configured."""
    return STRIPE_AVAILABLE and bool(STRIPE_PUBLISHABLE_KEY)


def is_test_mode() -> bool:
    """Check if running in Stripe test mode."""
    return STRIPE_MODE == 'test'


# ==============================================================================
# USER IDENTIFICATION
# ==============================================================================

def get_user_identifier() -> str:
    """
    Get unique user identifier based on session or IP.
    Uses Streamlit session state as primary, falls back to IP.
    """
    # Try to get session-based identifier first
    if 'user_id' not in st.session_state:
        # Generate a new unique ID for this session
        st.session_state.user_id = f"session_{uuid.uuid4().hex[:16]}"
    
    # Also try to get IP-based identifier as fallback
    try:
        ip_address = (
            st.context.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
            st.context.headers.get('X-Real-IP', '') or
            '127.0.0.1'
        )
    except:
        ip_address = '127.0.0.1'
    
    # Use session ID as primary (more reliable for logged-in experience)
    # But store IP for reference
    return st.session_state.user_id


def get_user_ip() -> str:
    """Get user IP address."""
    try:
        return (
            st.context.headers.get('X-Forwarded-For', '').split(',')[0].strip() or
            st.context.headers.get('X-Real-IP', '') or
            '127.0.0.1'
        )
    except:
        return '127.0.0.1'


# ==============================================================================
# SUBSCRIPTION MANAGEMENT
# ==============================================================================

def _get_subscription_key(identifier: str) -> str:
    """Get Redis key for subscription data."""
    return f"sub:{identifier}"


def _get_scans_key(identifier: str) -> str:
    """Get Redis key for daily scan count."""
    today = datetime.now().strftime('%Y%m%d')
    return f"scans:{identifier}:{today}"


def get_subscription_data(identifier: str) -> Optional[Dict[str, Any]]:
    """Get subscription data from Redis."""
    if not REDIS_AVAILABLE:
        return None
    
    try:
        key = _get_subscription_key(identifier)
        data = redis_client.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        loguru.logger.error(f"Failed to get subscription data: {e}")
    
    return None


def is_pro_user(identifier: str) -> bool:
    """
    Check if user has an active Pro subscription.
    Returns True if user has active subscription, False otherwise.
    """
    if not REDIS_AVAILABLE:
        return False
    
    sub_data = get_subscription_data(identifier)
    if not sub_data:
        return False
    
    # Check if subscription is active and not expired
    tier = sub_data.get('tier', 'free')
    expiry = sub_data.get('expiry')
    
    if tier == 'free':
        return False
    
    # Check expiry
    if expiry:
        try:
            expiry_date = datetime.fromisoformat(expiry)
            if expiry_date < datetime.now():
                # Subscription expired
                return False
        except (ValueError, TypeError):
            pass
    
    return tier in ('pro_monthly', 'pro_annual')


def set_pro_subscription(
    identifier: str,
    tier: str,
    expiry: datetime,
    customer_id: Optional[str] = None,
    subscription_id: Optional[str] = None
) -> bool:
    """Store Pro subscription in Redis."""
    if not REDIS_AVAILABLE:
        loguru.logger.error("Cannot set subscription - Redis unavailable")
        return False
    
    try:
        key = _get_subscription_key(identifier)
        
        # Calculate TTL based on expiry
        now = datetime.now()
        if expiry > now:
            ttl_seconds = int((expiry - now).total_seconds())
        else:
            # Default to 30 days if expiry is in the past
            ttl_seconds = 30 * 24 * 60 * 60
        
        # Cap TTL at 30 days to ensure periodic refresh
        ttl_seconds = min(ttl_seconds, 30 * 24 * 60 * 60)
        
        data = {
            'tier': tier,
            'expiry': expiry.isoformat(),
            'customer_id': customer_id or '',
            'subscription_id': subscription_id or '',
            'activated_at': datetime.now().isoformat()
        }
        
        redis_client.setex(key, ttl_seconds, json.dumps(data))
        loguru.logger.info(f"Set subscription for {identifier[:12]}...: {tier}, expires {expiry.date()}")
        return True
        
    except Exception as e:
        loguru.logger.error(f"Failed to set subscription: {e}")
        return False


def remove_subscription(identifier: str) -> bool:
    """Remove subscription from Redis (downgrade to free)."""
    if not REDIS_AVAILABLE:
        return False
    
    try:
        key = _get_subscription_key(identifier)
        redis_client.delete(key)
        loguru.logger.info(f"Removed subscription for {identifier[:12]}...")
        return True
    except Exception as e:
        loguru.logger.error(f"Failed to remove subscription: {e}")
        return False


# ==============================================================================
# SCAN COUNT MANAGEMENT
# ==============================================================================

def get_scan_count(identifier: str) -> Tuple[int, int]:
    """
    Get current scan count and limit for today.
    Returns (current_count, limit).
    """
    if not REDIS_AVAILABLE:
        # If Redis unavailable, allow scans (fail-open)
        return 0, FREE_TIER_SCANS_PER_DAY
    
    try:
        key = _get_scans_key(identifier)
        current = redis_client.get(key)
        count = int(current) if current else 0
        return count, FREE_TIER_SCANS_PER_DAY
    except Exception as e:
        loguru.logger.error(f"Failed to get scan count: {e}")
        return 0, FREE_TIER_SCANS_PER_DAY


def increment_scan_count(identifier: str) -> int:
    """
    Increment scan count for today.
    Returns the new count.
    """
    if not REDIS_AVAILABLE:
        return 0
    
    try:
        key = _get_scans_key(identifier)
        
        # Use pipeline for atomic operation
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, 24 * 60 * 60)  # 24 hour TTL
        results = pipe.execute()
        
        return results[0]
    except Exception as e:
        loguru.logger.error(f"Failed to increment scan count: {e}")
        return 0


def can_scan(identifier: str) -> bool:
    """
    Check if user can perform a scan (free tier limit check).
    Returns True if scan is allowed, False if limit reached.
    """
    # Pro users have unlimited scans
    if is_pro_user(identifier):
        return True
    
    # Check free tier limit
    current, limit = get_scan_count(identifier)
    return current < limit


def get_scans_remaining(identifier: str) -> int:
    """Get remaining scans for today (free tier)."""
    if is_pro_user(identifier):
        return -1  # Unlimited
    
    current, limit = get_scan_count(identifier)
    return max(0, limit - current)


# ==============================================================================
# STRIPE CHECKOUT
# ==============================================================================

def get_checkout_url(tier: str, return_url: str) -> Optional[str]:
    """
    Generate Stripe checkout URL for the specified tier.
    Returns None if Stripe not configured.
    """
    if not STRIPE_AVAILABLE:
        loguru.logger.warning("Stripe not configured - cannot generate checkout URL")
        return None
    
    # Get price ID based on tier
    if tier == 'pro_monthly':
        price_id = STRIPE_PRICE_ID_MONTHLY
    elif tier == 'pro_annual':
        price_id = STRIPE_PRICE_ID_ANNUAL
    else:
        loguru.logger.error(f"Invalid tier: {tier}")
        return None
    
    if not price_id:
        loguru.logger.error(f"No price ID configured for tier: {tier}")
        return None
    
    try:
        # Get user identifier for client_reference_id
        user_id = get_user_identifier()
        
        # Create checkout session
        session_params = {
            'mode': 'subscription',
            'payment_method_types': ['card'],
            'line_items': [{
                'price': price_id,
                'quantity': 1,
            }],
            'success_url': return_url + '?upgrade=success&session_id={CHECKOUT_SESSION_ID}',
            'cancel_url': return_url + '?upgrade=cancelled',
            'client_reference_id': user_id,
            'subscription_data': {
                'metadata': {
                    'user_id': user_id,
                }
            },
        }
        
        # Add test mode metadata if in test mode
        if is_test_mode():
            session_params['subscription_data']['metadata']['test_mode'] = 'true'
        
        session = stripe.checkout.Session.create(**session_params)
        loguru.logger.info(f"Created checkout session for {user_id[:12]}..., tier: {tier}")
        
        return session.url
        
    except stripe.error.StripeError as e:
        loguru.logger.error(f"Failed to create checkout session: {e}")
        return None


def get_customer_portal_url(customer_id: str, return_url: str) -> Optional[str]:
    """Generate Stripe customer portal URL for subscription management."""
    if not STRIPE_AVAILABLE or not customer_id:
        return None
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return session.url
    except stripe.error.StripeError as e:
        loguru.logger.error(f"Failed to create portal session: {e}")
        return None


# ==============================================================================
# SUBSCRIPTION STATUS UI HELPERS
# ==============================================================================

def get_subscription_status(identifier: str) -> Dict[str, Any]:
    """
    Get comprehensive subscription status for UI display.
    """
    sub_data = get_subscription_data(identifier)
    
    if not sub_data:
        return {
            'tier': 'free',
            'is_pro': False,
            'scans_remaining': get_scans_remaining(identifier),
            'scans_used': 0,
            'scans_limit': FREE_TIER_SCANS_PER_DAY,
        }
    
    tier = sub_data.get('tier', 'free')
    is_pro = tier in ('pro_monthly', 'pro_annual')
    expiry_str = sub_data.get('expiry')
    
    # Get scan info
    if is_pro:
        scans_remaining = -1  # Unlimited
        scans_used = 0
        scans_limit = -1
    else:
        current, limit = get_scan_count(identifier)
        scans_used = current
        scans_limit = limit
        scans_remaining = max(0, limit - current)
    
    # Parse expiry
    expiry_date = None
    if expiry_str:
        try:
            expiry_date = datetime.fromisoformat(expiry_str)
        except (ValueError, TypeError):
            pass
    
    return {
        'tier': tier,
        'is_pro': is_pro,
        'expiry': expiry_date,
        'customer_id': sub_data.get('customer_id', ''),
        'subscription_id': sub_data.get('subscription_id', ''),
        'scans_remaining': scans_remaining,
        'scans_used': scans_used,
        'scans_limit': scans_limit,
        'watermark_enabled': FREE_TIER_WATERMARK_ENABLED and not is_pro,
    }


def format_expiry_date(expiry: datetime) -> str:
    """Format expiry date for display."""
    if not expiry:
        return 'N/A'
    
    now = datetime.now()
    if expiry < now:
        return 'Expired'
    
    if expiry < now + timedelta(days=7):
        return f"Expires in {(expiry - now).days} days"
    
    return expiry.strftime('%b %d, %Y')


# ==============================================================================
# MIDDLEWARE DECORATOR
# ==============================================================================

def require_pro(f):
    """
    Decorator to restrict function to Pro users.
    Shows upgrade prompt if user is not Pro.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        identifier = get_user_identifier()
        
        if not is_pro_user(identifier):
            st.error("🚫 This feature requires Pro subscription.")
            st.info("Upgrade to Pro for unlimited access!")
            return None
        
        return f(*args, **kwargs)
    
    return wrapper


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def handle_upgrade_success(session_id: str) -> bool:
    """
    Handle successful upgrade from Stripe checkout.
    This is called after redirect from Stripe checkout.
    Returns True if activation was successful.
    """
    if not STRIPE_AVAILABLE:
        return False
    
    try:
        # Retrieve the checkout session
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        if checkout_session.payment_status != 'paid':
            loguru.logger.warning(f"Checkout session not paid: {session_id}")
            return False
        
        # Get subscription details
        subscription = stripe.Subscription.retrieve(checkout_session.subscription)
        
        # Get user ID from client_reference_id
        user_id = checkout_session.client_reference_id
        if not user_id:
            loguru.logger.error("No client_reference_id in checkout session")
            return False
        
        # Calculate expiry date
        if subscription.current_period_end:
            expiry = datetime.fromtimestamp(subscription.current_period_end)
        else:
            expiry = datetime.now() + timedelta(days=30)
        
        # Determine tier based on price
        price_id = None
        if subscription.items.data:
            price_id = subscription.items.data[0].price.id
        
        if price_id == STRIPE_PRICE_ID_ANNUAL:
            tier = 'pro_annual'
        elif price_id == STRIPE_PRICE_ID_MONTHLY:
            tier = 'pro_monthly'
        else:
            tier = 'pro_monthly'  # Default
        
        # Store subscription
        success = set_pro_subscription(
            identifier=user_id,
            tier=tier,
            expiry=expiry,
            customer_id=checkout_session.customer,
            subscription_id=subscription.id
        )
        
        if success:
            loguru.logger.info(f"Activated Pro subscription for {user_id[:12]}..., tier: {tier}")
        
        return success
        
    except stripe.error.StripeError as e:
        loguru.logger.error(f"Failed to handle upgrade success: {e}")
        return False


def check_and_process_upgrade() -> bool:
    """
    Check URL query params for upgrade success and process if needed.
    Returns True if upgrade was processed.
    """
    try:
        # Get query params
        query_params = st.query_params
        upgrade_status = query_params.get('upgrade')
        session_id = query_params.get('session_id')
        
        if upgrade_status == 'success' and session_id:
            # Process the upgrade
            success = handle_upgrade_success(session_id)
            
            # Clear query params
            st.query_params.clear()
            
            if success:
                st.success("🎉 Welcome to Pro! Your subscription is now active.")
                return True
            else:
                st.warning("There was an issue activating your subscription. Please contact support.")
        
        return False
        
    except Exception as e:
        loguru.logger.error(f"Error processing upgrade: {e}")
        return False
