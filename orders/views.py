from django.shortcuts import get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.conf import settings
import json
import logging

from .models import Order
from .services import PayPalService, ShopifyService, check_ali_express_stock, send_cancellation_notification, save_order_to_firestore
from django.forms.models import model_to_dict

logger = logging.getLogger(__name__)

@csrf_exempt
def create_paypal_order(request):
    if request.method != 'POST':
        return HttpResponseBadRequest("Invalid request method")

    try:
        data = json.loads(request.body)
        # Basic validation
        if 'amount' not in data or 'currency' not in data or 'user_id' not in data:
            return HttpResponseBadRequest("Missing required fields: amount, currency, user_id")

        # Create a local order record first
        local_order = Order.objects.create(
            user_id=data['user_id'],
            order_data=data, # Store all incoming data for later use
            status=Order.Status.PENDING
        )

        paypal_service = PayPalService()
        
        # Build full URLs for PayPal redirect
        return_url = request.build_absolute_uri(reverse('paypal-return'))
        cancel_url = request.build_absolute_uri(reverse('paypal-cancel'))

        paypal_order = paypal_service.create_order(
            amount=data['amount'],
            currency=data['currency'],
            return_url=return_url,
            cancel_url=cancel_url,
            shipping_address=data.get('shipping_address', {})
        )

        # Save the PayPal Order ID to our local order
        local_order.paypal_order_id = paypal_order['id']
        local_order.save()

        # Return the approval link to the frontend
        approval_link = next(link['href'] for link in paypal_order['links'] if link['rel'] == 'approve')
        return JsonResponse({'approval_url': approval_link})

    except Exception as e:
        logger.error(f"Error creating PayPal order: {e}")
        return JsonResponse({'error': str(e)}, status=500)


def paypal_return(request):
    """
    Handle the user returning from PayPal after approval.
    This step now includes fetching and storing the authorization ID.
    """
    try:
        paypal_order_id = request.GET.get('token')
        if not paypal_order_id:
            return HttpResponseBadRequest("Missing PayPal token")

        local_order = get_object_or_404(Order, paypal_order_id=paypal_order_id)
        
        # --- NEW: Authorize the order to get the Authorization ID ---
        paypal_service = PayPalService()
        # Instead of just getting details, we now authorize the order.
        authorization_details = paypal_service.authorize_order(paypal_order_id)
        
        # --- Add diagnostic logging ---
        logger.info(f"Full PayPal Authorization Details received: {json.dumps(authorization_details, indent=2)}")
        # --- End diagnostic logging ---
        
        # The authorization ID is needed to capture or void the payment later.
        purchase_unit = authorization_details.get('purchase_units', [{}])[0]
        authorization = purchase_unit.get('payments', {}).get('authorizations', [{}])[0]
        authorization_id = authorization.get('id')
        
        if not authorization_id:
            logger.error(f"Could not find authorization ID for order {paypal_order_id}. Purchase unit: {purchase_unit}")
            return redirect(settings.FRONTEND_FAILURE_URL)

        local_order.paypal_authorization_id = authorization_id
        # --- END NEW ---

        shopify_service = ShopifyService()
        shopify_order = shopify_service.create_order(
            order_data=local_order.order_data,
            paypal_order_id=paypal_order_id
        )

        local_order.shopify_order_id = shopify_order['order']['id']
        local_order.status = Order.Status.AUTHORIZED
        local_order.save()

        # --- NEW: Save comprehensive order details to Firestore ---
        comprehensive_order_details = {
            "local_order": model_to_dict(local_order),
            "paypal_authorization": authorization_details,
            "shopify_order": shopify_order
        }
        save_order_to_firestore(comprehensive_order_details)
        # --- END NEW ---

        frontend_success_url = f"{settings.FRONTEND_SUCCESS_URL}/{local_order.id}"
        return redirect(frontend_success_url)

    except Exception as e:
        logger.error(f"Error in PayPal return handler: {e}")
        return redirect(settings.FRONTEND_FAILURE_URL)


def paypal_cancel(request):
    """
    Handle the user cancelling the payment on PayPal.
    """
    try:
        paypal_order_id = request.GET.get('token')
        if paypal_order_id:
            local_order = get_object_or_404(Order, paypal_order_id=paypal_order_id)
            local_order.status = Order.Status.CANCELLED
            local_order.save()
    except Exception as e:
        logger.error(f"Error in PayPal cancel handler: {e}")

    # Redirect to a frontend cancellation page
    return redirect(settings.FRONTEND_CANCEL_URL)


@csrf_exempt
def capture_payment(request, order_id):
    """
    An endpoint to trigger the capture of an authorized payment.
    This should be a protected endpoint (e.g., admin-only).
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Invalid request method")
        
    try:
        local_order = get_object_or_404(Order, id=order_id, status=Order.Status.AUTHORIZED)

        paypal_service = PayPalService()
        capture_data = paypal_service.capture_payment(local_order.paypal_order_id)
        
        # Assuming capture is successful, update Shopify
        shopify_service = ShopifyService()
        
        # Extract details needed for Shopify transaction
        amount = capture_data['amount']['value']
        currency = capture_data['amount']['currency_code']
        capture_id = capture_data['id']

        shopify_service.update_order_to_paid(
            shopify_order_id=local_order.shopify_order_id,
            amount=amount,
            currency=currency,
            paypal_capture_id=capture_id
        )

        local_order.status = Order.Status.CAPTURED
        local_order.save()

        return JsonResponse({'status': 'success', 'message': 'Payment captured and Shopify order updated.'})

    except Order.DoesNotExist:
        return HttpResponseBadRequest("Order not found or not in 'authorized' state.")
    except Exception as e:
        logger.error(f"Error capturing payment for order {order_id}: {e}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def paypal_webhook(request):
    """
    Listener for PayPal webhooks.
    A robust implementation should verify the webhook signature.
    """
    # For now, we just log the event.
    logger.info(f"Received PayPal webhook: {request.body.decode()}")
    # TODO: Implement webhook verification using `PayPalService.verify_webhook_signature()`
    # TODO: Process events like 'CHECKOUT.ORDER.APPROVED' as a more reliable
    #       way to trigger Shopify order creation instead of the return URL.
    return HttpResponse(status=200)


@csrf_exempt
def process_order(request, order_id):
    """
    This view orchestrates the post-authorization flow:
    1. Stock Check
    2. Payment Capture
    3. Shopify Order Update
    Includes robust error handling and cancellation logic.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Invalid request method")

    local_order = get_object_or_404(Order, id=order_id, status=Order.Status.AUTHORIZED)
    
    # Instantiate services
    paypal_service = PayPalService()
    shopify_service = ShopifyService()
    
    # Centralized cancellation logic
    def _cancel_order_flow(cancellation_reason, log_message):
        logger.error(f"CANCELLATION: {log_message}")
        try:
            # Void PayPal auth and cancel Shopify order
            if local_order.paypal_authorization_id:
                paypal_service.void_authorization(local_order.paypal_authorization_id)
            if local_order.shopify_order_id:
                shopify_service.cancel_order(local_order.shopify_order_id, reason="other")
            
            local_order.status = Order.Status.CANCELLED
            local_order.save()
            
            # Notify the customer
            customer_email = local_order.order_data.get('customer', {}).get('email')
            if customer_email:
                send_cancellation_notification(customer_email, str(local_order.id), cancellation_reason)
        except Exception as e:
            logger.critical(f"CRITICAL: Failed during cancellation process for order {local_order.id}: {e}")
        return JsonResponse({'status': 'error', 'message': cancellation_reason}, status=400)

    # 1. Stock Verification
    if not check_ali_express_stock(local_order):
        return _cancel_order_flow(
            cancellation_reason="An item in your order is out of stock.",
            log_message=f"Stock check failed for Order ID {local_order.id}."
        )

    # 2. Payment Capture
    try:
        logger.info(f"Attempting to capture payment for Order ID: {local_order.id}")
        capture_data = paypal_service.capture_payment(local_order.paypal_authorization_id)
        
        # 3. Shopify Order Update to "Paid"
        try:
            amount = capture_data['amount']['value']
            currency = capture_data['amount']['currency_code']
            capture_id = capture_data['id']
            
            shopify_service.update_order_to_paid(
                shopify_order_id=local_order.shopify_order_id,
                amount=amount,
                currency=currency,
                paypal_capture_id=capture_id
            )
            
            local_order.status = Order.Status.CAPTURED
            local_order.save()
            
            logger.info(f"Successfully processed and captured Order ID: {local_order.id}")
            return JsonResponse({'status': 'success', 'message': 'Payment captured and Shopify order updated.'})

        except Exception as shopify_error:
            # This is a critical state: payment was captured but Shopify update failed.
            return _cancel_order_flow(
                cancellation_reason="A critical error occurred while finalizing your order.",
                log_message=f"CRITICAL: Payment captured for order {local_order.id} but Shopify update failed: {shopify_error}"
            )
            
    except Exception as paypal_error:
        # If PayPal capture fails, cancel everything.
        return _cancel_order_flow(
            cancellation_reason="Your payment could not be processed.",
            log_message=f"PayPal capture failed for Order ID {local_order.id}: {paypal_error}"
        )
