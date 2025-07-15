import os
import requests
import json
from dotenv import load_dotenv
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore
import logging

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def fetch_new_products_from_shopify():
    """
    Fetches products from Shopify that do not already exist in the Firestore database,
    transforms them, and saves them to a local 'new_rawdata.json' file.
    """
    logging.info("Starting script to fetch new products...")
    load_dotenv()

    # --- Initialize Firebase Admin SDK ---
    try:
        # Construct credentials from environment variables
        cred_dict = {
            "type": "service_account",
            "project_id": os.getenv("FIREBASE_PROJECT_ID"),
            "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
            "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace('\\n', '\n'),
            "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
            "client_id": os.getenv("FIREBASE_CLIENT_ID"),
            "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
            "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
            "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_X509_CERT_URL"),
            "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_X509_CERT_URL"),
            "universe_domain": os.getenv("FIREBASE_UNIVERSE_DOMAIN")
        }

        # Check if all required keys are present
        if not all(cred_dict.values()):
            raise ValueError("One or more Firebase environment variables are not set.")

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            logging.info("Firebase Admin SDK initialized successfully from environment variables.")
        
        firestore_db = firestore.client()
    except Exception as e:
        logging.error(f"Failed to initialize Firebase Admin SDK: {e}")
        return

    # --- 1. Get existing product IDs from Firestore ---
    try:
        logging.info("Fetching existing product IDs from Firestore...")
        products_collection = firestore_db.collection('products')
        existing_ids = {doc.id for doc in products_collection.stream()}
        logging.info(f"Found {len(existing_ids)} existing products in Firestore.")
    except Exception as e:
        logging.error(f"Failed to fetch product IDs from Firestore: {e}")
        return

    # --- 2. Fetch products from Shopify ---
    store_url = os.getenv("SHOPIFY_STORE_URL")
    access_token = os.getenv("SHOPIFY_API_PASSWORD")
    output_filename = 'new_rawdata.json'
    
    if not all([store_url, access_token]):
        logging.error("Error: Ensure SHOPIFY_STORE_URL and SHOPIFY_ACCESS_TOKEN are set in the .env file.")
        return

    shopify_api_url = f"https://{store_url}/admin/api/2023-10/products.json"
    headers = { "X-Shopify-Access-Token": access_token, "Content-Type": "application/json" }

    try:
        logging.info(f"Fetching all products from Shopify store: {store_url}...")
        response = requests.get(shopify_api_url, headers=headers)
        response.raise_for_status()
        all_shopify_products = response.json().get('products', [])
        logging.info(f"Found {len(all_shopify_products)} total products in Shopify.")
    except Exception as e:
        logging.error(f"Error fetching from Shopify: {e}")
        return

    # --- 3. Filter out existing products ---
    new_products_from_api = [
        p for p in all_shopify_products if str(p['id']) not in existing_ids
    ]
    logging.info(f"Found {len(new_products_from_api)} new products to be added.")

    if not new_products_from_api:
        logging.info("No new products to fetch. Exiting.")
        return

    # --- 4. Transform new products data and save to JSON file ---
    try:
        logging.info(f"Transforming {len(new_products_from_api)} new products...")
        all_transformed_products = []
        for product_from_api in new_products_from_api:
            first_variant = product_from_api.get('variants', [{}])[0]
            tags_list = [tag.strip() for tag in product_from_api.get('tags', '').split(',') if tag.strip()]

            transformed_product = {
                '_id': str(product_from_api['id']),
                'id': str(product_from_api['id']),
                'title': product_from_api.get('title'),
                'description': product_from_api.get('body_html', ''),
                'vendor': product_from_api.get('vendor'),
                'productType': product_from_api.get('product_type'),
                'handle': product_from_api.get('handle'),
                'status': product_from_api.get('status', '').upper(),
                'tags': tags_list,
                'images': [{'url': img.get('src'), 'altText': img.get('alt')} for img in product_from_api.get('images', [])],
                'variants': [
                    {
                        'id': str(v.get('id')),
                        'title': v.get('title'),
                        'sku': v.get('sku'),
                        'price': float(v.get('price', 0)),
                        'compareAtPrice': float(v.get('compare_at_price')) if v.get('compare_at_price') else None,
                        'inventory': v.get('inventory_quantity', 0)
                    } for v in product_from_api.get('variants', [])
                ],
                'price': float(first_variant.get('price', 0)),
                'currency': 'ILS',
                'syncedAt': datetime.now(timezone.utc).isoformat()
            }
            all_transformed_products.append(transformed_product)

        # --- Write to JSON file ---
        output_filepath = os.path.join(os.path.dirname(__file__), output_filename)
        logging.info(f"Writing new product data to '{output_filepath}'...")
        with open(output_filepath, 'w', encoding='utf-8') as f:
            json.dump(all_transformed_products, f, ensure_ascii=False, indent=4)
        
        logging.info(f"Successfully saved new raw data to {output_filepath}")

    except Exception as e:
        logging.error(f"Error during data transformation or file writing: {e}")


if __name__ == "__main__":
    fetch_new_products_from_shopify() 