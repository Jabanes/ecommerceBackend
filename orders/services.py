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

# --- New Placeholder Stock Checker ---
def check_ali_express_stock(order: Order) -> bool:
    """
    Placeholder function to simulate a stock check.
    In a real application, this would involve complex logic, an external API call,
    or a manual verification step.
    It iterates through line items stored in the order_data.
    """
    logger.info(f"Checking stock for Order ID: {order.id}...")
    # For now, we'll just simulate success. Change to False to test failure path.
    # for item in order.order_data.get('line_items', []):
    #     variant_id = item.get('variant_id')
    #     quantity = item.get('quantity')
    #     if not is_stock_available(variant_id, quantity):
    #          logger.warning(f"Stock check failed for variant {variant_id}")
    #          return False
    logger.info("Stock check successful (simulated).")
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

def save_order_to_firestore(order_details: dict):
    """
    Saves a comprehensive order object to the 'orders' collection in Firestore.
    Uses Shopify's order number (e.g., #1003) as the document ID for consistency.
    """
    try:
        # Extract Shopify order data
        shopify_order = order_details.get('shopify_order', {}).get('order', {})
        if not shopify_order:
            logger.error("No Shopify order data found in order_details")
            return False
            
        # Get the order number (e.g., 1003) NOT the order ID
        order_number = shopify_order.get('order_number')
        if not order_number:
            logger.error("No order number found in Shopify order data")
            logger.error(f"Shopify order data: {json.dumps(shopify_order, indent=2)}")
            return False
            
        document_id = str(order_number)
        logger.info(f"Using Shopify order number {document_id} as Firestore document ID")
        
        # Add metadata to help track document creation/updates
        order_details['metadata'] = {
            'created_at': datetime.datetime.now().isoformat(),
            'document_id': document_id,
            'shopify_order_number': order_number,
            'shopify_order_id': shopify_order.get('id'),  # Store the actual Shopify order ID as well
            'order_name': shopify_order.get('name')  # This is usually something like "#1003"
        }
        
        # Save to Firestore using Shopify's order number as document ID
        doc_ref = db.collection('orders').document(document_id)
        doc_ref.set(order_details)
        
        logger.info(f"Successfully saved order {document_id} to Firestore with metadata: {order_details['metadata']}")
        return True
    except Exception as e:
        logger.error(f"Failed to save order to Firestore: {e}")
        logger.exception(e)  # This will log the full stack trace
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