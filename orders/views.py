from django.shortcuts import get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.conf import settings
import json
import logging

from .models import Order
from .services import PayPalService, ShopifyService

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
    """
    try:
        paypal_order_id = request.GET.get('token')
        if not paypal_order_id:
            return HttpResponseBadRequest("Missing PayPal token")

        local_order = get_object_or_404(Order, paypal_order_id=paypal_order_id)
        
        # Here you could re-confirm the order with PayPal API to ensure it's approved,
        # but for this flow, we proceed to create the Shopify order.

        shopify_service = ShopifyService()
        shopify_order = shopify_service.create_order(
            order_data=local_order.order_data,
            paypal_order_id=paypal_order_id
        )

        local_order.shopify_order_id = shopify_order['order']['id']
        local_order.status = Order.Status.AUTHORIZED
        local_order.save()

        # Redirect to a frontend success page
        frontend_success_url = f"{settings.FRONTEND_SUCCESS_URL}/{local_order.id}"
        return redirect(frontend_success_url)

    except Exception as e:
        logger.error(f"Error in PayPal return handler: {e}")
        # Redirect to a frontend failure page
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
