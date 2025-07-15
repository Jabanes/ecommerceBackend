import os
import logging
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

def populate_categories():
    """
    Scans the 'products' collection, extracts unique categories and
    sub-categories, and populates the 'categories' collection.
    """
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Starting category population script...")

    load_dotenv()

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
        
        if not all(cred_dict.values()):
            raise ValueError("One or more Firebase environment variables are not set.")

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
            logging.info("Firebase Admin SDK initialized successfully from environment variables.")
        
        db = firestore.client()
    except Exception as e:
        logging.error(f"Failed to initialize Firebase Admin SDK: {e}")
        return

    try:
        products_ref = db.collection('products')
        products = products_ref.stream()

        categories_data = {}

        logging.info("Fetching products to build category tree...")
        product_count = 0
        for product in products:
            product_count += 1
            product_data = product.to_dict()
            
            main_category = product_data.get('category')
            sub_category = product_data.get('subCategory')

            if main_category and isinstance(main_category, str):
                main_category = main_category.strip()
                if main_category:
                    if main_category not in categories_data:
                        categories_data[main_category] = set()
                    
                    if sub_category and isinstance(sub_category, str):
                        sub_category = sub_category.strip()
                        if sub_category:
                            categories_data[main_category].add(sub_category)
        
        if product_count == 0:
            logging.warning("'products' collection is empty. No categories to populate.")
            return

        logging.info(f"Found {len(categories_data)} unique main categories from {product_count} products.")

        if not categories_data:
            logging.warning("No valid categories found to populate. Exiting.")
            return

        categories_collection_ref = db.collection('categories')
        logging.info("Populating 'categories' collection...")

        for category_name, sub_categories_set in categories_data.items():
            # Convert set to a sorted list for consistent ordering
            sub_categories_list = sorted(list(sub_categories_set))
            
            doc_ref = categories_collection_ref.document(category_name)
            
            category_doc = {
                'name': category_name,
                'subCategories': sub_categories_list
            }
            
            doc_ref.set(category_doc)
            logging.info(f"  - Wrote category: '{category_name}' with {len(sub_categories_list)} sub-categories.")

        logging.info("Successfully populated the 'categories' collection in Firestore.")

    except Exception as e:
        logging.error(f"An error occurred: {e}")

if __name__ == '__main__':
    populate_categories() 