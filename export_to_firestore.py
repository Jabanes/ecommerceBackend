import os
import logging
import json
from dotenv import load_dotenv
from tqdm import tqdm
import firebase_admin
from firebase_admin import credentials, firestore

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def export_json_to_firestore():
    """
    Reads refined product data from a local JSON file and writes it to a 
    'products' collection in Firestore.
    """
    print("Starting export from JSON to Firestore script...")
    
    # --- Load Environment Variables ---
    load_dotenv()
    
    json_file_path = 'refined_data.json'

    # --- Initialize Firebase Admin SDK ---
    try:
        cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if not cred_path:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            logging.info("Firebase Admin SDK initialized successfully.")
        
        firestore_db = firestore.client()
    except Exception as e:
        logging.error(f"Failed to initialize Firebase Admin SDK: {e}")
        return

    # --- Read data from JSON file ---
    try:
        logging.info(f"Reading data from '{json_file_path}'...")
        with open(json_file_path, 'r', encoding='utf-8') as f:
            refined_products = json.load(f)
        
        if not refined_products:
            logging.warning(f"No products found in '{json_file_path}'. Exiting.")
            return
            
        logging.info(f"Found {len(refined_products)} refined products to export.")

        # --- Get Firestore Collection Reference ---
        firestore_products_collection = firestore_db.collection('products')

        # --- Iterate and Write to Firestore ---
        for product in tqdm(refined_products, desc="Exporting to Firestore"):
            doc_id = product.get('productId')
            
            if not doc_id:
                logging.warning(f"Skipping product with missing 'productId'. Data: {product}")
                continue

            try:
                firestore_products_collection.document(doc_id).set(product)
                logging.info(f"Successfully wrote document {doc_id} to Firestore.")
            except Exception as e:
                logging.error(f"Failed to write document {doc_id} to Firestore: {e}")

        logging.info("Export to Firestore complete.")

    except FileNotFoundError:
        logging.error(f"Error: The file '{json_file_path}' was not found.")
    except json.JSONDecodeError:
        logging.error(f"Error: Could not decode JSON from the file '{json_file_path}'.")
    except Exception as e:
        logging.error(f"An error occurred during the export process: {e}")

if __name__ == "__main__":
    export_json_to_firestore() 