import requests
import base64
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
import logging
from django.core.mail import send_mail
from .models import Order
import uuid
import datetime
import json


logger = logging.getLogger(__name__)

def check_ali_express_stock(order: Order) -> bool:
    """
    Checks product stock against Firestore before fulfilling an order.
    
    It iterates through line items from the order and checks if stock is available
    using the `is_stock_available` function.
    """
    logger.info(f"Checking stock for Order ID: {order.id}...")
    
    line_items = order.order_data.get('line_items', [])
    if not line_items:
        logger.warning(f"Order {order.id} has no line items to check. Failing stock check.")
        return False

    for item in line_items:
        variant_id = item.get('variant_id')
        quantity = item.get('quantity')
        
        if not variant_id or quantity is None:
            logger.error(f"Invalid line item in order {order.id}: {item}")
            return False

        if not is_stock_available(str(variant_id), quantity):
            logger.warning(f"Stock check failed for variant {variant_id} with quantity {quantity} for order {order.id}")
            return False
            
    logger.info(f"Stock check successful for Order ID: {order.id}.")
    return True


# --- PayPal Service ---

class PayPalService:
    """
    A service class for interacting with the PayPal REST API.
    """
    def __init__(self):
        self.client_id = settings.PAYPAL_CLIENT_ID
        self.client_secret = settings.PAYPAL_CLIENT_SECRET
        self.base_url = settings.PAYPAL_API_BASE

        # --- Add diagnostic logging ---
        logger.info("--- Initializing PayPalService ---")
        # To avoid logging the full secret, we'll just log its presence and length
        client_id_loaded = bool(self.client_id)
        secret_loaded = "Loaded" if self.client_secret else "NOT Loaded"
        logger.info(f"PAYPAL_CLIENT_ID loaded: {client_id_loaded}")
        logger.info(f"PAYPAL_CLIENT_SECRET status: {secret_loaded}")
        # --- End diagnostic logging ---

        if not all([self.client_id, self.client_secret, self.base_url]):
            raise ImproperlyConfigured("PayPal settings are not configured properly.")

    def get_access_token(self):
        """
        Get an OAuth2 access token from PayPal.
        """
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "client_credentials"}
        response = requests.post(f"{self.base_url}/v1/oauth2/token", headers=headers, data=data)
        response.raise_for_status()
        return response.json()["access_token"]

    def get_order_details(self, paypal_order_id):
        """
        Retrieves the full order details from PayPal, useful for getting the authorization ID.
        """
        access_token = self.get_access_token()
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        response = requests.get(f"{self.base_url}/v2/checkout/orders/{paypal_order_id}", headers=headers)
        response.raise_for_status()
        return response.json()

    def authorize_order(self, paypal_order_id):
        """
        Authorizes the payment for a previously created and approved order.
        """
        access_token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        # The payload for an authorize request is typically empty.
        payload = {}
        
        authorize_url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}/authorize"
        
        logger.info(f"Attempting to authorize PayPal order: {paypal_order_id}")
        
        try:
            response = requests.post(authorize_url, headers=headers, json=payload)
            response.raise_for_status()
            logger.info(f"Successfully authorized order {paypal_order_id}. Response: {response.text}")
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Failed to authorize PayPal order {paypal_order_id}: {e.response.status_code} - {e.response.text}")
            raise

    def create_order(self, amount, currency, return_url, cancel_url, shipping_address):
        """
        Create an order with intent 'AUTHORIZE'.
        """
        access_token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "intent": "AUTHORIZE",
            "purchase_units": [{
                "amount": {
                    "currency_code": currency,
                    "value": str(amount),
                },
                "shipping": {
                    "name": {
                        "full_name": f"{shipping_address.get('first_name', '')} {shipping_address.get('last_name', '')}"
                    },
                    "address": {
                        "address_line_1": shipping_address.get('address1', ''),
                        "admin_area_2": shipping_address.get('city', ''),
                        "admin_area_1": shipping_address.get('province', ''),
                        "postal_code": shipping_address.get('zip', ''),
                        "country_code": shipping_address.get('country', 'US') # Defaulting to US
                    }
                }
            }],
            "application_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "brand_name": "Your Store Name",
                "shipping_preference": "SET_PROVIDED_ADDRESS",
            }
        }
        
        logger.info(f"Sending payload to PayPal: {payload}")

        try:
            response = requests.post(f"{self.base_url}/v2/checkout/orders", headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"PayPal API Error: {e.response.status_code} - {e.response.text}")
            raise

    def capture_payment(self, authorization_id):
        """
        Capture the payment for a previously created authorization.
        """
        access_token = self.get_access_token()
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        capture_url = f"{self.base_url}/v2/payments/authorizations/{authorization_id}/capture"
        
        # You can add amount details if doing a partial capture
        capture_response = requests.post(capture_url, headers=headers, json={})
        capture_response.raise_for_status()
        return capture_response.json()

    def void_authorization(self, authorization_id):
        """
        Voids a previously created authorization, releasing the hold on funds.
        """
        access_token = self.get_access_token()
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        void_url = f"{self.base_url}/v2/payments/authorizations/{authorization_id}/void"
        
        response = requests.post(void_url, headers=headers)
        # A 204 No Content is a success for voids
        if response.status_code == 204:
            logger.info(f"Successfully voided PayPal Authorization ID: {authorization_id}")
            return True
        
        logger.error(f"Failed to void PayPal Authorization ID: {authorization_id}. Response: {response.text}")
        response.raise_for_status()


# --- Firestore Service ---
from dropship_backend.firebase_config import db

def is_stock_available(variant_id_str: str, quantity: int) -> bool:
    """
    Checks if a specific product variant has enough stock in Firestore.

    This function iterates through all products to find the matching variant.
    NOTE: This is inefficient and will be slow if there are many products.
    For production, consider optimizing by either fetching products directly if product IDs
    are available in the order, or by restructuring Firestore data (e.g., a separate
    variants collection).
    """
    try:
        variant_id_to_find = str(variant_id_str)
        logger.info(f"Checking stock for Variant ID: {variant_id_to_find}, Quantity: {quantity}")

        products_ref = db.collection('products')
        
        # This streams all documents. Inefficient but necessary with current data model.
        all_products = products_ref.stream()

        for product_doc in all_products:
            product_data = product_doc.to_dict()
            variants = product_data.get('variants', [])
            
            for variant in variants:
                variant_id = variant.get('id')
                if str(variant_id) == variant_id_to_find:
                    inventory = variant.get('inventory')
                    logger.info(f"Found Variant {variant_id_to_find} in Product {product_doc.id}. Available stock: {inventory}")
                    
                    if inventory is not None and inventory >= quantity:
                        logger.info(f"Stock is sufficient for Variant {variant_id_to_find}.")
                        return True
                    else:
                        logger.warning(f"Insufficient stock for Variant {variant_id_to_find}. Required: {quantity}, Available: {inventory}")
                        return False
        
        logger.warning(f"Variant with ID {variant_id_to_find} not found in any product.")
        return False
    except Exception as e:
        logger.error(f"Error during stock check for variant {variant_id_str}: {e}")
        logger.exception(e)  # Log stack trace
        return False

def save_order_to_firestore(order_details: dict):
    """
    Saves a flattened and optimized order object to the 'orders' collection in Firestore.
    It extracts key information from the local order, PayPal, and Shopify data.
    """
    try:
        # --- Extract data from the comprehensive order details ---
        local_order_data = order_details.get('local_order', {}).get('order_data', {})
        shopify_order = order_details.get('shopify_order', {}).get('order', {})
        paypal_auth = order_details.get('paypal_authorization', {})

        if not all([local_order_data, shopify_order, paypal_auth]):
            logger.error("Missing critical data in order_details for Firestore.")
            return False

        # --- Safely extract nested data ---
        purchase_unit = paypal_auth.get('purchase_units', [{}])[0]
        paypal_authorization = purchase_unit.get('payments', {}).get('authorizations', [{}])[0]
        
        # --- Build the optimized Firestore document ---
        order_number = shopify_order.get('order_number')
        if not order_number:
            logger.error("Shopify order number is missing, cannot create Firestore document.")
            return False
            
        document_id = str(order_number)
        
        # Match Shopify line items to local line items to get price
        shopify_line_items = {str(item.get('variant_id')): item.get('price') for item in shopify_order.get('line_items', [])}
        
        optimized_line_items = []
        for item in local_order_data.get('line_items', []):
            variant_id = str(item.get('variant_id'))
            optimized_line_items.append({
                "quantity": item.get('quantity'),
                "variant_id": variant_id,
                "price_per_item": shopify_line_items.get(variant_id)
            })

        # --- Get customer email, prioritizing local data, then PayPal ---
        customer_email = local_order_data.get('customer', {}).get('email')
        if not customer_email:
            # The payer object is in the main authorization response
            customer_email = paypal_auth.get('payer', {}).get('email_address')
            logger.info(f"Customer email not found in local order; using PayPal email: {customer_email}")

        firestore_payload = {
            "user_id": local_order_data.get('user_id'),
            "shopify_order_id": shopify_order.get('id'),
            "shopify_order_number": order_number,
            "paypal_order_id": paypal_auth.get('id'),
            "paypal_authorization_id": paypal_authorization.get('id'),
            "amount": local_order_data.get('amount'),
            "currency": local_order_data.get('currency'),
            "customer_email": customer_email,
            "customer_first_name": local_order_data.get('shipping_address', {}).get('first_name'),
            "customer_last_name": local_order_data.get('shipping_address', {}).get('last_name'),
            "shipping_address": local_order_data.get('shipping_address'),
            "line_items": optimized_line_items,
            "current_backend_status": "AUTHORIZED",
            "created_at": datetime.datetime.now().isoformat(),
            "last_updated_at": datetime.datetime.now().isoformat(),
        }

        # --- Save to Firestore ---
        doc_ref = db.collection('orders').document(document_id)
        doc_ref.set(firestore_payload)

        logger.info(f"Successfully saved optimized order {document_id} to Firestore.")
        return True

    except Exception as e:
        logger.error(f"Failed to save optimized order to Firestore: {e}")
        logger.exception(e)
        return False

def update_firestore_order_status(shopify_order_number: str, new_status: str, additional_data: dict = None):
    """
    Updates the status of an order in Firestore and adds any additional metadata.
    """
    try:
        # Note: We are using the Shopify order NUMBER as the document ID.
        doc_ref = db.collection('orders').document(str(shopify_order_number))
        
        update_payload = {
            "current_backend_status": new_status,
            "last_updated_at": datetime.datetime.now().isoformat()
        }
        
        if additional_data:
            update_payload.update(additional_data)
            
        doc_ref.update(update_payload)
        logger.info(f"Successfully updated Firestore order {shopify_order_number} to status {new_status}.")
        return True
    except Exception as e:
        logger.error(f"Failed to update Firestore status for order {shopify_order_number}: {e}")
        return False


# --- Shopify Service ---

class ShopifyService:
    """
    A service class for interacting with the Shopify Admin API.
    """
    def __init__(self):
        self.store_name = settings.SHOPIFY_STORE_NAME
        self.api_key = settings.SHOPIFY_API_KEY
        self.password = settings.SHOPIFY_API_PASSWORD
        self.api_version = settings.SHOPIFY_API_VERSION

        # --- Add diagnostic logging ---
        logger.info("--- Initializing ShopifyService ---")
        logger.info(f"SHOPIFY_STORE_NAME loaded as: {self.store_name}")
        logger.info(f"SHOPIFY_API_PASSWORD loaded as: {self.password}")
        logger.info(f"SHOPIFY_API_VERSION loaded as: {self.api_version}")
        # --- End diagnostic logging ---

        if not all([self.store_name, self.password, self.api_version]):
            raise ImproperlyConfigured("Shopify settings are not configured properly.")
        
        self.base_url = f"https://{self.store_name}.myshopify.com/admin/api/{self.api_version}"

    def _get_headers(self):
        return {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": self.password,
        }

    def get_variant_details(self, variant_id):
        """
        Fetches the latest details for a specific product variant from Shopify.
        """
        url = f"{self.base_url}/variants/{variant_id}.json"
        logger.info(f"Fetching variant details from Shopify for variant_id: {variant_id}")
        try:
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            logger.debug(f"Successfully fetched Shopify variant {variant_id}. Data: {response.text}")
            return response.json().get('variant')
        except requests.exceptions.HTTPError as e:
            # Log specific Shopify error if available
            logger.error(f"Shopify API error fetching variant {variant_id}: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"An unexpected error occurred fetching variant {variant_id}: {e}")
            raise

    def create_order(self, order_data, paypal_order_id):
        """
        Creates an order in Shopify with financial_status 'authorized'.
        """
        url = f"{self.base_url}/orders.json"
        
        # --- Enrich Payload for Robustness ---
        customer_details = order_data.get('customer', {})
        shipping_address_details = order_data.get('shipping_address', {})

        # Ensure customer object has first and last name from shipping details
        if 'first_name' in shipping_address_details:
            customer_details['first_name'] = shipping_address_details['first_name']
        if 'last_name' in shipping_address_details:
            customer_details['last_name'] = shipping_address_details['last_name']
        # --- End Enrichment ---

        payload = {
            "order": {
                "line_items": order_data.get('line_items', []),
                "customer": customer_details,
                "shipping_address": shipping_address_details,
                "billing_address": shipping_address_details, # Explicitly set billing address
                "financial_status": "authorized",
                "gateway": "PayPal",
                "transactions": [
                    {
                        "kind": "authorization",
                        "status": "success",
                        "gateway": "paypal",
                        "amount": order_data.get("amount"),
                        "test": True, # Set to False in production
                        "authorization": paypal_order_id
                    }
                ]
            }
        }
        
        logger.info(f"Sending enriched payload to Shopify: {payload}")

        try:
            response = requests.post(url, headers=self._get_headers(), json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Shopify API Error: {e.response.status_code} - {e.response.text}")
            raise

    def cancel_order(self, shopify_order_id, reason="other"):
        """
        Cancels an order in Shopify.
        """
        url = f"{self.base_url}/orders/{shopify_order_id}/cancel.json"
        payload = {"reason": reason}
        
        logger.info(f"Cancelling Shopify Order ID: {shopify_order_id} with reason: {reason}")
        response = requests.post(url, headers=self._get_headers(), json=payload)
        response.raise_for_status()
        return response.json()

    def update_order_to_paid(self, shopify_order_id, amount, currency, paypal_capture_id):
        """
        Updates a Shopify order's financial status to 'paid' by creating a 'capture' transaction.
        """
        url = f"{self.base_url}/orders/{shopify_order_id}/transactions.json"
        
        payload = {
            "transaction": {
                "kind": "capture",
                "status": "success",
                "amount": amount,
                "currency": currency,
                "test": True,
                "authorization": paypal_capture_id, # The capture ID from PayPal
            }
        }
        
        response = requests.post(url, headers=self._get_headers(), json=payload)
        response.raise_for_status()

        # After a successful capture transaction, Shopify *may* automatically mark the order as paid.
        # If not, you might need another API call to update the order's financial_status directly.
        # For now, creating the transaction is the correct approach.
        return response.json()

    def mark_order_as_paid_for_test(self, shopify_order_id, amount, currency, authorization_id):
        """
        Marks a Shopify order as 'paid' by creating a test capture transaction.
        This is used for the E2E test flow to simulate a payment capture.
        """
        url = f"{self.base_url}/orders/{shopify_order_id}/transactions.json"
        
        payload = {
            "transaction": {
                "kind": "capture",
                "status": "success",
                "amount": amount,
                "currency": currency,
                "test": True,
                "authorization": authorization_id, # Using the auth ID for the test capture
            }
        }
        
        logger.info(f"TEST MODE: Marking Shopify order {shopify_order_id} as paid with auth {authorization_id}")
        response = requests.post(url, headers=self._get_headers(), json=payload)
        response.raise_for_status()

        # Shopify should automatically update the financial_status to 'paid'.
        logger.info(f"Successfully posted test capture transaction for Shopify order {shopify_order_id}.")
        return response.json()


# --- New Notification Service ---
def send_cancellation_notification(customer_email, order_id, reason):
    """
    Sends a simple email notification to the customer about order cancellation.
    """
    subject = f"Update on your order {order_id}"
    message = (
        f"Dear customer,\n\n"
        f"We're sorry to inform you that there has been an update regarding your order {order_id}.\n"
        f"Reason: {reason}\n\n"
        f"If you have any questions, please contact our support team.\n\n"
        f"Sincerely,\nThe Store Team"
    )
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [customer_email]

    try:
        send_mail(subject, message, from_email, recipient_list)
        logger.info(f"Cancellation email sent to {customer_email} for order {order_id}")
    except Exception as e:
        logger.error(f"Failed to send cancellation email for order {order_id}: {e}") 