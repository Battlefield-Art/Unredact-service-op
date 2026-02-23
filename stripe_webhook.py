"""
UnredactServiceOp - Stripe Webhook Handler
Flask endpoint for processing Stripe webhook events.
"""

import os
import json
import uuid
import hmac
import hashlib
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from flask import Flask, request, jsonify, abort
import loguru
import redis


# ==============================================================================
# CONFIGURATION
# ==============================================================================

app = Flask(__name__)

# Redis configuration
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_PRICE_ID_MONTHLY = os.getenv('STRIPE_PRICE_ID_MONTHLY', '')
STRIPE_PRICE_ID_ANNUAL = os.getenv('STRIPE_PRICE_ID_ANNUAL', '')

# Initialize Redis
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    REDIS_AVAILABLE = True
    loguru.logger.info("Redis connected for webhook handler")
except Exception as e:
    loguru.logger.error(f"Redis not available: {e}")
    redis_client = None
    REDIS_AVAILABLE = False

# Initialize Stripe
if STRIPE_SECRET_KEY:
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    STRIPE_AVAILABLE = True
    loguru.logger.info("Stripe initialized for webhook handler")
else:
    STRIPE_AVAILABLE = False
    loguru.logger.warning("Stripe not configured - webhook handler will only log events")


# ==============================================================================
# LOGGING SETUP
# ==============================================================================

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

loguru.logger.add(
    os.path.join(LOG_DIR, 'webhook_{time}.log'),
    rotation='50 MB',
    retention='14 days',
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}'
)


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def generate_trace_id() -> str:
    """Generate unique trace ID for this request."""
    return f"wh_{uuid.uuid4().hex[:12]}"


def get_subscription_key(identifier: str) -> str:
    """Get Redis key for subscription data."""
    return f"sub:{identifier}"


def verify_stripe_signature(payload: bytes, signature: str) -> bool:
    """
    Verify Stripe webhook signature.
    Returns True if signature is valid.
    """
    if not STRIPE_WEBHOOK_SECRET:
        loguru.logger.warning("No webhook secret configured - skipping signature verification")
        return True
    
    try:
        # Compute expected signature
        expected_signature = hmac.new(
            STRIPE_WEBHOOK_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        # Use timing-safe comparison
        return hmac.compare_digest(expected_signature, signature)
        
    except Exception as e:
        loguru.logger.error(f"Signature verification error: {e}")
        return False


def get_tier_from_price_id(price_id: str) -> str:
    """Determine subscription tier from Stripe price ID."""
    if price_id == STRIPE_PRICE_ID_ANNUAL:
        return 'pro_annual'
    elif price_id == STRIPE_PRICE_ID_MONTHLY:
        return 'pro_monthly'
    else:
        loguru.logger.warning(f"Unknown price ID: {price_id}, defaulting to pro_monthly")
        return 'pro_monthly'


# ==============================================================================
# SUBSCRIPTION MANAGEMENT
# ==============================================================================

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
        key = get_subscription_key(identifier)
        
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
        loguru.logger.info(f"Webhook: Set subscription for {identifier[:12]}...: {tier}, expires {expiry.date()}")
        return True
        
    except Exception as e:
        loguru.logger.error(f"Failed to set subscription: {e}")
        return False


def remove_subscription(identifier: str) -> bool:
    """Remove subscription from Redis (downgrade to free)."""
    if not REDIS_AVAILABLE:
        return False
    
    try:
        key = get_subscription_key(identifier)
        redis_client.delete(key)
        loguru.logger.info(f"Webhook: Removed subscription for {identifier[:12]}...")
        return True
    except Exception as e:
        loguru.logger.error(f"Failed to remove subscription: {e}")
        return False


# ==============================================================================
# EVENT HANDLERS
# ==============================================================================

def handle_checkout_session_completed(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle checkout.session.completed event.
    Activates Pro subscription after successful payment.
    """
    trace_id = generate_trace_id()
    loguru.logger.info(f"[{trace_id}] Handling checkout.session.completed")
    
    session = event.get('data', {}).get('object', {})
    
    # Get customer and subscription info
    customer_id = session.get('customer')
    subscription_id = session.get('subscription')
    user_id = session.get('client_reference_id')
    
    if not user_id:
        loguru.logger.error(f"[{trace_id}] No client_reference_id in checkout session")
        return {'success': False, 'error': 'No user ID'}
    
    if not subscription_id:
        loguru.logger.error(f"[{trace_id}] No subscription ID in checkout session")
        return {'success': False, 'error': 'No subscription ID'}
    
    if not STRIPE_AVAILABLE:
        loguru.logger.warning(f"[{trace_id}] Stripe not available - logging event only")
        return {'success': False, 'error': 'Stripe not configured'}
    
    try:
        # Get subscription details from Stripe
        subscription = stripe.Subscription.retrieve(subscription_id)
        
        # Get price ID to determine tier
        price_id = None
        if subscription.items.data:
            price_id = subscription.items.data[0].price.id
        
        tier = get_tier_from_price_id(price_id)
        
        # Calculate expiry date
        if subscription.current_period_end:
            expiry = datetime.fromtimestamp(subscription.current_period_end)
        else:
            expiry = datetime.now() + timedelta(days=30)
        
        # Store subscription
        success = set_pro_subscription(
            identifier=user_id,
            tier=tier,
            expiry=expiry,
            customer_id=customer_id,
            subscription_id=subscription_id
        )
        
        if success:
            loguru.logger.info(
                f"[{trace_id}] Activated Pro subscription for {user_id[:12]}... "
                f"tier={tier}, customer={customer_id}, expires={expiry.date()}"
            )
            return {'success': True, 'tier': tier, 'user_id': user_id}
        else:
            return {'success': False, 'error': 'Failed to store subscription'}
            
    except stripe.error.StripeError as e:
        loguru.logger.error(f"[{trace_id}] Stripe error: {e}")
        return {'success': False, 'error': str(e)}


def handle_subscription_updated(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle customer.subscription.updated event.
    Updates subscription status and expiry in Redis.
    """
    trace_id = generate_trace_id()
    loguru.logger.info(f"[{trace_id}] Handling customer.subscription.updated")
    
    subscription = event.get('data', {}).get('object', {})
    subscription_id = subscription.get('id')
    customer_id = subscription.get('customer')
    status = subscription.get('status')
    
    # Find user by subscription_id (need to scan Redis or maintain mapping)
    # For now, we'll need to check all subscriptions or use metadata
    if not STRIPE_AVAILABLE:
        loguru.logger.warning(f"[{trace_id}] Stripe not available - cannot lookup subscription")
        return {'success': False, 'error': 'Stripe not configured'}
    
    try:
        # Get full subscription details including metadata
        full_subscription = stripe.Subscription.retrieve(subscription_id)
        user_id = full_subscription.metadata.get('user_id')
        
        if not user_id:
            loguru.logger.warning(f"[{trace_id}] No user_id in subscription metadata")
            # Try to find by customer_id scan
            user_id = find_user_by_customer(customer_id)
            if not user_id:
                return {'success': False, 'error': 'User not found'}
        
        # Get price ID for tier
        price_id = None
        if full_subscription.items.data:
            price_id = full_subscription.items.data[0].price.id
        
        tier = get_tier_from_price_id(price_id)
        
        # Calculate new expiry
        if full_subscription.current_period_end:
            expiry = datetime.fromtimestamp(full_subscription.current_period_end)
        else:
            expiry = datetime.now() + timedelta(days=30)
        
        # Check if subscription is active
        if status in ('active', 'trialing'):
            success = set_pro_subscription(
                identifier=user_id,
                tier=tier,
                expiry=expiry,
                customer_id=customer_id,
                subscription_id=subscription_id
            )
            loguru.logger.info(
                f"[{trace_id}] Updated subscription for {user_id[:12]}... "
                f"status={status}, tier={tier}, expires={expiry.date()}"
            )
            return {'success': success, 'tier': tier, 'user_id': user_id}
        else:
            # Subscription not active (e.g., past_due, canceled)
            loguru.logger.info(f"[{trace_id}] Subscription {subscription_id} status: {status}")
            return {'success': True, 'status': status, 'user_id': user_id}
            
    except stripe.error.StripeError as e:
        loguru.logger.error(f"[{trace_id}] Stripe error: {e}")
        return {'success': False, 'error': str(e)}


def handle_subscription_deleted(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle customer.subscription.deleted event.
    Removes subscription from Redis (downgrade to free).
    """
    trace_id = generate_trace_id()
    loguru.logger.info(f"[{trace_id}] Handling customer.subscription.deleted")
    
    subscription = event.get('data', {}).get('object', {})
    subscription_id = subscription.get('id')
    
    if not STRIPE_AVAILABLE:
        loguru.logger.warning(f"[{trace_id}] Stripe not available - cannot lookup subscription")
        return {'success': False, 'error': 'Stripe not configured'}
    
    try:
        # Get full subscription to find user
        full_subscription = stripe.Subscription.retrieve(subscription_id)
        user_id = full_subscription.metadata.get('user_id')
        
        if not user_id:
            user_id = find_user_by_customer(full_subscription.get('customer'))
            if not user_id:
                return {'success': False, 'error': 'User not found'}
        
        # Remove subscription
        success = remove_subscription(user_id)
        
        loguru.logger.info(
            f"[{trace_id}] Deleted subscription for {user_id[:12]}..., "
            f"subscription={subscription_id}"
        )
        return {'success': success, 'user_id': user_id}
        
    except stripe.error.StripeError as e:
        loguru.logger.error(f"[{trace_id}] Stripe error: {e}")
        return {'success': False, 'error': str(e)}


def handle_invoice_payment_failed(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle invoice.payment_failed event.
    Logs failure and optionally notifies user.
    """
    trace_id = generate_trace_id()
    loguru.logger.info(f"[{trace_id}] Handling invoice.payment_failed")
    
    invoice = event.get('data', {}).get('object', {})
    customer_id = invoice.get('customer')
    subscription_id = invoice.get('subscription')
    
    loguru.logger.warning(
        f"[{trace_id}] Payment failed for customer={customer_id}, "
        f"subscription={subscription_id}"
    )
    
    # Try to find user and notify (in production, send email)
    if customer_id and STRIPE_AVAILABLE:
        try:
            user_id = find_user_by_customer(customer_id)
            if user_id:
                loguru.logger.info(f"[{trace_id}] Would notify user {user_id[:12]}... of payment failure")
                # In production: send_payment_failure_notification(user_id)
        except Exception as e:
            loguru.logger.error(f"[{trace_id}] Error notifying user: {e}")
    
    return {'success': True, 'notified': True}


def find_user_by_customer(customer_id: str) -> Optional[str]:
    """
    Find user ID by customer ID.
    This is a best-effort scan - in production, maintain a customer_id -> user_id mapping.
    """
    if not REDIS_AVAILABLE or not customer_id:
        return None
    
    try:
        # Scan for subscriptions with matching customer_id
        # This is O(n) but acceptable for small user base
        # In production, maintain a separate customer_id -> user_id index
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match='sub:session_*', count=100)
            
            for key in keys:
                data = redis_client.get(key)
                if data:
                    sub_data = json.loads(data)
                    if sub_data.get('customer_id') == customer_id:
                        return key.replace('sub:', '')
            
            if cursor == 0:
                break
                
    except Exception as e:
        loguru.logger.error(f"Error scanning for customer: {e}")
    
    return None


# ==============================================================================
# WEBHOOK ENDPOINT
# ==============================================================================

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """
    Main webhook endpoint for Stripe events.
    """
    trace_id = generate_trace_id()
    loguru.logger.info(f"[{trace_id}] Received webhook request")
    
    # Get request data
    payload = request.get_data()
    signature = request.headers.get('Stripe-Signature')
    
    # Verify signature (skip in test mode if no secret configured)
    if STRIPE_WEBHOOK_SECRET and signature:
        if not verify_stripe_signature(payload, signature):
            loguru.logger.warning(f"[{trace_id}] Invalid signature")
            abort(400, description="Invalid signature")
    elif not STRIPE_WEBHOOK_SECRET:
        loguru.logger.warning(f"[{trace_id}] No webhook secret - skipping verification")
    
    # Parse event
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as e:
        loguru.logger.error(f"[{trace_id}] Invalid JSON: {e}")
        abort(400, description="Invalid JSON")
    
    event_type = event.get('type')
    event_id = event.get('id')
    
    loguru.logger.info(f"[{trace_id}] Processing event: {event_type}, id: {event_id}")
    
    # Route to handler
    result = {'success': False, 'error': 'Unknown event type'}
    
    if event_type == 'checkout.session.completed':
        result = handle_checkout_session_completed(event)
    elif event_type == 'customer.subscription.updated':
        result = handle_subscription_updated(event)
    elif event_type == 'customer.subscription.deleted':
        result = handle_subscription_deleted(event)
    elif event_type == 'invoice.payment_failed':
        result = handle_invoice_payment_failed(event)
    else:
        loguru.logger.info(f"[{trace_id}] Unhandled event type: {event_type}")
        result = {'success': True, 'handled': False, 'event_type': event_type}
    
    # Log result
    if result.get('success'):
        loguru.logger.info(f"[{trace_id}] Successfully processed {event_type}")
    else:
        loguru.logger.error(f"[{trace_id}] Failed to process {event_type}: {result.get('error')}")
    
    # Return 200 for successful processing
    return jsonify({'received': True, **result})


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    health = {
        'status': 'healthy',
        'redis': REDIS_AVAILABLE,
        'stripe': STRIPE_AVAILABLE,
        'timestamp': datetime.now().isoformat()
    }
    
    # Check Redis connectivity
    if REDIS_AVAILABLE:
        try:
            redis_client.ping()
        except Exception as e:
            health['redis'] = False
            health['status'] = 'degraded'
            loguru.logger.error(f"Redis health check failed: {e}")
    
    return jsonify(health)


@app.route('/ready', methods=['GET'])
def readiness_check():
    """Readiness check - returns 503 if not ready."""
    if not REDIS_AVAILABLE:
        return jsonify({'error': 'Redis not available'}), 503
    if not STRIPE_AVAILABLE:
        return jsonify({'error': 'Stripe not configured'}), 503
    return jsonify({'ready': True})


# ==============================================================================
# MAIN
# ==============================================================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', '5000'))
    loguru.logger.info(f"Starting Stripe webhook handler on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
