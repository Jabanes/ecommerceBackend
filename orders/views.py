from django.shortcuts import get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
from django.conf import settings
import json
import logging
import math

from .models import Order
from .services import PayPalService, ShopifyService, check_ali_express_stock, send_cancellation_notification, save_order_to_firestore, update_firestore_order_status
from django.forms.models import model_to_dict

logger = logging.getLogger(__name__)

@csrf_exempt
def create_paypal_order(request):
    """
    Handles the checkout initiation, including real-time price and inventory validation
    before creating a PayPal order.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Invalid request method")

    try:
        data = json.loads(request.body)
        required_fields = ['currency', 'user_id', 'line_items', 'shipping_address']
        if not all(field in data for field in required_fields):
            return HttpResponseBadRequest(f"Missing required fields. Required: {required_fields}")
        if not data['line_items']:
            return HttpResponseBadRequest("Cannot process an empty order.")

        logger.info(f"Initiating checkout for user {data['user_id']} with {len(data['line_items'])} items.")
        
        shopify_service = ShopifyService()
        validation_errors = []
        validated_items = []
        recalculated_amount = 0.0

        # --- Real-time Price and Inventory Validation ---
        for item in data['line_items']:
            variant_id = item.get('variant_id')
            quantity = item.get('quantity')
            frontend_price = item.get('price')

            if not all([variant_id, quantity, frontend_price]):
                validation_errors.append({'error': 'Invalid line item data. Each item must have variant_id, quantity, and price.'})
                continue # Move to the next item

            try:
                shopify_variant = shopify_service.get_variant_details(variant_id)
                if not shopify_variant:
                    logger.warning(f"Validation failed: Variant {variant_id} not found in Shopify.")
                    validation_errors.append({'variant_id': variant_id, 'error': f"Item '{item.get('title', 'N/A')}' is no longer available."})
                    continue

                # 1. Validate Inventory
                available_quantity = shopify_variant.get('inventory_quantity')
                logger.info(f"Validating inventory for variant {variant_id}. Requested: {quantity}, Available: {available_quantity}")
                if quantity > available_quantity:
                    logger.warning(f"Inventory check FAILED for variant {variant_id}. Requested: {quantity}, Available: {available_quantity}")
                    validation_errors.append({
                        'variant_id': variant_id,
                        'error': f"The item '{shopify_variant.get('title')}' is out of stock. Only {available_quantity} available."
                    })

                # 2. Validate Price
                shopify_price = float(shopify_variant.get('price'))
                logger.info(f"Validating price for variant {variant_id}. Frontend: {frontend_price}, Shopify: {shopify_price}")
                if not math.isclose(float(frontend_price), shopify_price, rel_tol=1e-5):
                    logger.warning(f"Price check FAILED for variant {variant_id}. Frontend: {frontend_price}, Shopify: {shopify_price}")
                    validation_errors.append({
                        'variant_id': variant_id,
                        'error': f"The price for '{shopify_variant.get('title')}' has changed from ${frontend_price} to ${shopify_price}."
                    })
                
                # If all checks for this item passed, add it to our validated list
                if not validation_errors:
                    validated_item = item.copy() # Start with original item data
                    validated_item['price'] = shopify_price # Update with the official price
                    validated_items.append(validated_item)
                    recalculated_amount += shopify_price * quantity

            except Exception as e:
                logger.error(f"A critical error occurred during validation for variant {variant_id}: {e}")
                return JsonResponse({'errors': [{'error': "An internal error occurred. Please try again."}]}, status=500)

        # --- End Validation ---

        if validation_errors:
            logger.warning(f"Checkout validation failed for user {data['user_id']} with errors: {validation_errors}")
            return JsonResponse({'errors': validation_errors}, status=400) # 400 Bad Request is appropriate here

        # --- Proceed with Validated Data ---
        logger.info(f"Checkout validation successful. Recalculated total: {recalculated_amount}")

        # Update order data with validated items and total
        validated_order_data = data.copy()
        validated_order_data['line_items'] = validated_items
        validated_order_data['amount'] = round(recalculated_amount, 2)

        local_order = Order.objects.create(
            user_id=data['user_id'],
            order_data=validated_order_data,
            status=Order.Status.PENDING
        )

        paypal_service = PayPalService()
        return_url = request.build_absolute_uri(reverse('paypal-return'))
        cancel_url = request.build_absolute_uri(reverse('paypal-cancel'))

        paypal_order = paypal_service.create_order(
            amount=validated_order_data['amount'],
            currency=data['currency'],
            return_url=return_url,
            cancel_url=cancel_url,
            shipping_address=data.get('shipping_address', {})
        )

        local_order.paypal_order_id = paypal_order['id']
        local_order.save()

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
        local_order.shopify_order_number = shopify_order['order']['order_number']
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


@csrf_exempt
def test_process_order(request, order_id):
    """
    This view orchestrates the E2E test flow for post-authorization processing.
    It is controlled by feature flags in settings.py.
    """
    if request.method != 'POST':
        return HttpResponseBadRequest("Invalid request method")

    logger.info(f"E2E TEST: Starting processing for Order ID: {order_id}")

    try:
        local_order = get_object_or_404(Order, id=order_id, status=Order.Status.AUTHORIZED)
    except Order.DoesNotExist:
        logger.error(f"E2E TEST: Order {order_id} not found or not in AUTHORIZED state.")
        return HttpResponseBadRequest("Order not found or not in 'authorized' state.")

    # Log initial data for debugging
    logger.info(f"E2E TEST: local_order data: {model_to_dict(local_order)}")
    logger.info(f"E2E TEST: paypal_authorization_id: {local_order.paypal_authorization_id}")
    logger.info(f"E2E TEST: shopify_order_id: {local_order.shopify_order_id}")
    
    paypal_service = PayPalService()
    shopify_service = ShopifyService()

    # --- 1. Stock Verification ---
    logger.info(f"E2E TEST: Performing stock check for order {order_id}.")
    if not check_ali_express_stock(local_order):
        logger.warning(f"E2E TEST: Stock check failed for order {order_id}. Initiating cancellation.")
        
        # In test mode, we still simulate voiding the PayPal auth
        if settings.ACCEPT_SANDBOX_PAYMENTS_FOR_TEST:
            try:
                logger.info(f"E2E TEST: Simulating PayPal VOID for auth {local_order.paypal_authorization_id}.")
                paypal_service.void_authorization(local_order.paypal_authorization_id)
                logger.info(f"E2E TEST: Successfully voided PayPal auth {local_order.paypal_authorization_id}.")
            except Exception as e:
                logger.error(f"E2E TEST: Error during simulated PayPal void for order {order_id}: {e}")
                # Continue with cancellation even if void fails
        
        try:
            logger.info(f"E2E TEST: Cancelling Shopify order {local_order.shopify_order_id}.")
            shopify_service.cancel_order(local_order.shopify_order_id, reason="other", note="Stock check failed.")
            logger.info(f"E2E TEST: Successfully cancelled Shopify order {local_order.shopify_order_id}.")
        except Exception as e:
            logger.error(f"E2E TEST: Critical error cancelling Shopify order {order_id}: {e}")
            # This is a critical failure, but we should still mark our local order as cancelled

        local_order.status = Order.Status.CANCELLED
        local_order.save()
        update_firestore_order_status(local_order.shopify_order_number, "CANCELLED", {"reason": "Stock Unavailable"})
        
        return JsonResponse({'status': 'error', 'message': 'Stock check failed. Order cancelled.'}, status=400)
    
    logger.info(f"E2E TEST: Stock check successful for order {order_id}.")

    # --- 2. Simulated Payment Capture (Conditional) ---
    payment_captured = False
    if settings.ACCEPT_SANDBOX_PAYMENTS_FOR_TEST and settings.AUTO_CAPTURE_PAYMENT_FOR_TEST:
        logger.info("E2E TEST: Running in test mode. Simulating successful PayPal payment capture.")
        # We don't call PayPal, just update our internal state
        local_order.status = Order.Status.PAID
        local_order.save()
        update_firestore_order_status(local_order.shopify_order_number, "PAID")
        payment_captured = True
        logger.info(f"E2E TEST: Order {order_id} status updated to PAID internally.")
    else:
        logger.info(f"E2E TEST: Attempting real PayPal capture for order {order_id}.")
        try:
            capture_data = paypal_service.capture_payment(local_order.paypal_authorization_id)
            local_order.status = Order.Status.CAPTURED
            local_order.save()
            update_firestore_order_status(local_order.shopify_order_number, "CAPTURED", {"paypal_capture_data": capture_data})
            payment_captured = True
            logger.info(f"E2E TEST: Real payment captured successfully for order {order_id}.")
        except Exception as e:
            logger.error(f"E2E TEST: Real PayPal capture failed for order {order_id}: {e}")
            # If real capture fails, we must void the auth and cancel the order
            # (Implementation of full cancellation flow would be here)
            return JsonResponse({'status': 'error', 'message': 'Payment capture failed.'}, status=500)

    # --- 3. Shopify Order Update to "Paid" (Conditional) ---
    if payment_captured and settings.AUTO_FULFILL_ORDER_FOR_TEST:
        logger.info("E2E TEST: Auto-fulfillment enabled. Updating Shopify order financial status to 'paid'.")
        try:
            amount = local_order.order_data['amount']
            currency = local_order.order_data['currency']
            
            # Use the new service method for test captures
            shopify_service.mark_order_as_paid_for_test(
                local_order.shopify_order_id,
                amount,
                currency,
                local_order.paypal_authorization_id
            )
            logger.info(f"E2E TEST: Successfully marked Shopify order {local_order.shopify_order_id} as 'paid'.")
            logger.info("E2E TEST: Shopify order updated to 'paid'. DSers should now detect the order as ready for manual placement on AliExpress.")
            update_firestore_order_status(local_order.shopify_order_number, "FULFILLMENT_READY")
        except Exception as e:
            logger.critical(f"E2E TEST: CRITICAL! Failed to update Shopify order {local_order.shopify_order_id} to 'paid': {e}")
            # Here you would trigger a refund/cancellation flow
            return JsonResponse({'status': 'error', 'message': 'Failed to update Shopify order status.'}, status=500)
    elif payment_captured:
        logger.info("E2E TEST: Automatic Shopify fulfillment skipped as per test flag (AUTO_FULFILL_ORDER_FOR_TEST=False).")

    return JsonResponse({'status': 'success', 'message': 'E2E test order processing completed successfully.'})
