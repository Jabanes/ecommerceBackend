import requests
import base64
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
import logging

logger = logging.getLogger(__name__)

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

    def capture_payment(self, paypal_order_id):
        """
        Capture the payment for a previously authorized order.
        """
        access_token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        # The authorization ID is part of the original order details after approval.
        # For simplicity here, we assume a direct capture endpoint on the order.
        # A more robust implementation would fetch the order, get the authorization ID, and then capture.
        capture_url = f"{self.base_url}/v2/checkout/orders/{paypal_order_id}/authorize"
        # First authorize
        auth_response = requests.post(capture_url, headers=headers, json={})
        auth_response.raise_for_status()
        
        authorization_id = auth_response.json()['purchase_units'][0]['payments']['authorizations'][0]['id']

        # Then capture
        capture_url = f"{self.base_url}/v2/payments/authorizations/{authorization_id}/capture"
        capture_response = requests.post(capture_url, headers=headers, json={})
        capture_response.raise_for_status()

        return capture_response.json()

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
        
        # This payload needs to be constructed from your frontend cart data.
        # The structure should match Shopify's Order API.
        # Example structure:
        payload = {
            "order": {
                "line_items": order_data.get('line_items', []), # e.g., [{"variant_id": 447654529, "quantity": 1}]
                "customer": order_data.get('customer', {}), # e.g., {"first_name": "Paul", "last_name": "Norman", "email": "paul.norman@example.com"}
                "shipping_address": order_data.get('shipping_address', {}),
                "financial_status": "authorized",
                "gateway": "PayPal",
                "transactions": [
                    {
                        "kind": "authorization",
                        "status": "success",
                        "gateway": "paypal",
                        "amount": order_data.get("amount"), # FIX: Use 'amount' which is sent from frontend
                        "test": True, # Set to False in production
                        "authorization": paypal_order_id
                    }
                ]
            }
        }
        
        logger.info(f"Sending payload to Shopify: {payload}")

        try:
            response = requests.post(url, headers=self._get_headers(), json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Shopify API Error: {e.response.status_code} - {e.response.text}")
            raise

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